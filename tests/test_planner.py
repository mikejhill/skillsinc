"""Test route selection, render collisions, and source/manifest failures."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from tests.conftest import Builder, Sandbox

from skillsinc.applier import Applier
from skillsinc.config import Config, DestinationConfig, RouteConfig
from skillsinc.exceptions import ConfigurationError
from skillsinc.manifest import Manifest, ManifestStore
from skillsinc.plan import Plan
from skillsinc.planner import Planner
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.sources import SourceReader


class TestPlanner:
    """Only complete sources and unambiguous renderings may produce changes."""

    @staticmethod
    def planner(sandbox: Sandbox) -> Planner:
        """Build a planner using isolated state and real local collaborators."""
        return Planner(
            ProfileRegistry(),
            SourceReader(),
            ManifestStore(sandbox.environment.state_directory),
            sandbox.environment,
        )

    def test_only_limits_enabled_destinations(self, sandbox: Sandbox) -> None:
        """Explicit destination filtering omits other enabled targets."""
        config = sandbox.configure()
        destinations = dict(config.destinations)
        destinations["other"] = DestinationConfig(
            "other", "generic", sandbox.environment.home / "other"
        )
        destinations["disabled"] = DestinationConfig(
            "disabled", "generic", sandbox.environment.home / "disabled", enabled=False
        )

        result = self.planner(sandbox).plan(
            replace(config, destinations=destinations), only=("target",)
        )

        assert [entry.destination.name for entry in result.destinations] == [
            "target"
        ], f"Expected only target, got {result!r}"

    def test_disabled_destinations_are_omitted(self, sandbox: Sandbox) -> None:
        """Disabled targets are not planned even when explicitly requested."""
        config = sandbox.configure()
        destinations = {"target": replace(config.destinations["target"], enabled=False)}

        result = self.planner(sandbox).plan(
            replace(config, destinations=destinations), only=("target",)
        )

        assert result.destinations == (), (
            f"Expected empty plan without source reads, got {result!r}"
        )
        assert result.source_errors == {}, (
            f"Expected empty plan without source reads, got {result!r}"
        )

    def test_unknown_only_destination_is_usage_error(self, sandbox: Sandbox) -> None:
        """Reject misspelled destination filters before any source read."""
        config = sandbox.configure()

        with pytest.raises(ConfigurationError, match=r"unknown destination.*missing"):
            self.planner(sandbox).plan(config, only=("missing",))

    @pytest.mark.parametrize("different", [False, True])
    def test_multiple_sources_deduplicate_or_block_colliding_outputs(
        self, sandbox: Sandbox, tmp_path: Path, *, different: bool
    ) -> None:
        """Identical outputs deduplicate while conflicting content blocks the target."""
        second = tmp_path / "second"
        package = second / "skills/sample"
        package.mkdir(parents=True)
        content = (
            b"---\ndescription: Other\n---\nDifferent"
            if different
            else (sandbox.library / "skills/sample/SKILL.md").read_bytes()
        )
        (package / "SKILL.md").write_bytes(content)
        config = sandbox.configure()
        sources = dict(config.sources)
        sources["second"] = Builder.source(second, name="second")
        config = replace(
            config,
            sources=sources,
            routes=(RouteConfig(("library", "second"), ("target",)),),
        )

        result = self.planner(sandbox).plan(config)

        target = result.destinations[0]
        assert target.blocked is different, (
            f"Expected collision blocking {different}, got {target!r}"
        )
        if different:
            assert target.changes == (), (
                f"Expected collision explanation and no changes, got {target!r}"
            )
            assert "produce different content" in target.errors[0], (
                f"Expected collision explanation and no changes, got {target!r}"
            )
        else:
            assert len(target.changes) == 7, (
                f"Expected deduplicated seven outputs, got {target.changes!r}"
            )

    def test_each_harness_gets_its_own_diff(self, sandbox: Sandbox) -> None:
        """A blocked harness never blocks another, and each keeps its own layout."""
        home = sandbox.environment.home
        Builder.write_config(
            sandbox.config_path,
            sandbox.library,
            (("claude", "claude", home / "claude"), ("codex", "codex", home / "codex")),
            allow_lossy=True,
        )
        (home / "codex").mkdir()
        (home / "codex/AGENTS.md").write_text(
            "<!-- skillsinc:begin -->\n", encoding="utf-8"
        )

        result = self.planner(sandbox).plan(sandbox.load())

        claude, codex = result.destinations
        assert claude.changes, f"Expected claude changes, got {claude!r}"
        assert not claude.blocked, f"Expected claude unblocked, got {claude!r}"
        assert not any(change.block for change in claude.changes), (
            f"Expected only whole files for claude, got {claude.changes!r}"
        )
        assert (codex.blocked, codex.changes) == (True, ()), (
            f"Expected damaged codex block to block only codex, got {codex!r}"
        )

    def test_any_matching_route_can_accept_losses(self, sandbox: Sandbox) -> None:
        """Duplicate routes union their loss permission without duplicating files."""
        config = sandbox.configure("claude")
        config = replace(
            config, routes=(*config.routes, replace(config.routes[0], allow_lossy=True))
        )

        result = self.planner(sandbox).plan(config)

        target = result.destinations[0]
        assert "agents/reviewer.md" in [change.path for change in target.changes], (
            f"Expected accepted lossy conversions once, got {target!r}"
        )
        assert len(target.notes) == 3, (
            f"Expected accepted lossy conversions once, got {target!r}"
        )

    def test_corrupt_manifest_blocks_destination(self, sandbox: Sandbox) -> None:
        """Block changes when existing ownership records cannot be read."""
        config = sandbox.configure()
        store = ManifestStore(sandbox.environment.state_directory)
        path = store.path("target")
        path.parent.mkdir(parents=True)
        path.write_text("invalid", encoding="utf-8")

        result = self.planner(sandbox).plan(config)

        target = result.destinations[0]
        assert target.blocked, f"Expected manifest failure blocking, got {target!r}"
        assert target.changes == (), (
            f"Expected manifest failure blocking, got {target!r}"
        )
        assert "manifest is unreadable" in target.errors[0], (
            f"Expected manifest failure blocking, got {target!r}"
        )

    def test_no_routes_release_previously_owned_files(self, sandbox: Sandbox) -> None:
        """Release owned files from enabled destinations after routes disappear."""
        config = sandbox.configure()
        sandbox.root.mkdir()
        store = ManifestStore(sandbox.environment.state_directory)
        store.save(Manifest("target", str(sandbox.root), {"gone": "digest"}))

        result = self.planner(sandbox).plan(
            Config(config.path, config.sources, config.destinations, ())
        )

        assert result.destinations[0].changes[0].action.value == "forget", (
            f"Expected absent owned file released, got {result!r}"
        )

    @pytest.mark.parametrize("different", [False, True])
    def test_mcp_servers_from_two_sources_deduplicate_or_block(
        self, sandbox: Sandbox, tmp_path: Path, *, different: bool
    ) -> None:
        """Two sources defining one server must agree or block the destination."""
        server = '{"command": "a"}'
        (sandbox.library / "mcp").mkdir()
        (sandbox.library / "mcp/tool.json").write_text(server, encoding="utf-8")
        second = tmp_path / "second"
        (second / "mcp").mkdir(parents=True)
        (second / "mcp/tool.json").write_text(
            '{"command": "b"}' if different else server, encoding="utf-8"
        )
        config = sandbox.configure("copilot")
        sources = dict(config.sources)
        sources["second"] = Builder.source(second, name="second")
        config = replace(
            config,
            sources=sources,
            routes=(
                RouteConfig(("library", "second"), ("target",), include=("mcp/*",)),
            ),
        )

        target = self.planner(sandbox).plan(config).destinations[0]

        assert target.blocked is different, (
            f"Expected blocking {different}, got {target!r}"
        )
        if different:
            assert "entry mcpServers/tool" in target.errors[0], (
                f"Expected entry clash explanation, got {target.errors!r}"
            )
        else:
            assert [change.entry for change in target.changes] == ["mcpServers/tool"], (
                f"Expected one deduplicated entry, got {target.changes!r}"
            )

    def test_hook_files_sharing_an_event_merge_into_one_entry(
        self, sandbox: Sandbox, tmp_path: Path
    ) -> None:
        """Two hook files for one Claude event combine; identical groups dedupe."""
        hooks = sandbox.library / "hooks"
        hooks.mkdir()
        (hooks / "a.json").write_text(
            '{"version": 1, "hooks": {"Stop": [{"bash": "a.sh"}]}}',
            encoding="utf-8",
        )
        (hooks / "b.json").write_text(
            '{"version": 1, "hooks": {"Stop": [{"bash": "b.sh"}]}}',
            encoding="utf-8",
        )
        second = tmp_path / "second"
        shutil.copytree(hooks, second / "hooks")
        config = sandbox.configure("claude")
        sources = dict(config.sources)
        sources["second"] = Builder.source(second, name="second")
        config = replace(
            config,
            sources=sources,
            routes=(
                RouteConfig(("library", "second"), ("target",), include=("hooks/*",)),
            ),
        )

        target = self.planner(sandbox).plan(config).destinations[0]

        assert [change.entry for change in target.changes] == ["hooks/Stop"], (
            f"Expected one merged entry, got {target!r}"
        )
        written = json.loads(target.changes[0].content or b"{}")
        commands = [group["hooks"][0]["command"] for group in written["hooks"]["Stop"]]
        assert commands == ["a.sh", "b.sh"], (
            f"Expected both hooks once each, got {commands!r}"
        )


class TestDuplicateLoads:
    """A harness that reads two destinations must not load one item twice."""

    @staticmethod
    def plan(
        sandbox: Sandbox,
        profiles: tuple[str, ...],
        *,
        custom: tuple[str, ...] = (),
        routes: tuple[RouteConfig, ...] = (),
        only: tuple[str, ...] = (),
    ) -> Plan:
        """Route the sample library to ``profiles`` and return the plan.

        Destinations named in ``custom`` get a path outside the harness's folder.
        """
        config = sandbox.configure()
        registry = ProfileRegistry()
        destinations = {}
        for name in profiles:
            default = registry.get(name).default_path(sandbox.environment)
            assert default is not None
            path = sandbox.environment.home / "x" / name if name in custom else default
            destinations[name] = DestinationConfig(name, name, path)
        routes = routes or (
            RouteConfig(sources=("library",), destinations=profiles, allow_lossy=True),
        )
        config = replace(config, destinations=destinations, routes=routes)
        return TestPlanner.planner(sandbox).plan(config, only=only)

    @staticmethod
    def skipped(plan: Plan, destination: str) -> dict[str, str]:
        """Return ``{item: reason}`` for the destination's skips."""
        found = next(d for d in plan.destinations if d.destination.name == destination)
        return {skip.item: skip.reason for skip in found.skipped}

    def test_copilot_leaves_skills_to_agents(self, sandbox: Sandbox) -> None:
        """Copilot CLI reads ~/.agents/skills, so ~/.copilot gets no copy."""
        plan = self.plan(sandbox, ("copilot", "agents"))

        assert self.skipped(plan, "copilot")["library:skills/sample"] == (
            "Copilot CLI already loads it from destination agents"
        ), f"Expected skill covered by agents, got {plan!r}"
        assert plan.warnings == (), f"Expected no warning, got {plan.warnings!r}"

    def test_one_destination_run_needs_the_cover_in_place(
        self, sandbox: Sandbox
    ) -> None:
        """``-d copilot`` keeps the skill until ~/.agents already holds it."""
        before = self.plan(sandbox, ("copilot", "agents"), only=("copilot",))
        Applier(ManifestStore(sandbox.environment.state_directory)).apply(
            self.plan(sandbox, ("copilot", "agents"), only=("agents",))
        )
        after = self.plan(sandbox, ("copilot", "agents"), only=("copilot",))

        assert "library:skills/sample" not in self.skipped(before, "copilot"), (
            f"Expected the skill kept while agents lacks it, got {before!r}"
        )
        assert "library:skills/sample" in self.skipped(after, "copilot"), (
            f"Expected the skill covered once agents has it, got {after!r}"
        )

    def test_blocked_cover_does_not_skip(self, sandbox: Sandbox) -> None:
        """A blocked agents destination delivers nothing, so copilot keeps it."""
        agents = sandbox.environment.home / ".agents" / "skills" / "sample"
        agents.mkdir(parents=True)
        (agents / "SKILL.md").write_text("Unmanaged.\n", encoding="utf-8")

        plan = self.plan(sandbox, ("copilot", "agents"))

        agents_plan = next(
            d for d in plan.destinations if d.destination.name == "agents"
        )
        assert agents_plan.blocked, f"Expected agents blocked, got {agents_plan!r}"
        assert "library:skills/sample" not in self.skipped(plan, "copilot"), (
            f"Expected the skill kept in copilot, got {plan!r}"
        )

    def test_failed_cover_stops_dependent_apply(self, sandbox: Sandbox) -> None:
        """If the covering destination fails, its dependents are not applied."""
        plan = self.plan(sandbox, ("copilot", "agents"))
        copilot = next(d for d in plan.destinations if d.destination.name == "copilot")
        assert copilot.depends_on == ("agents",), f"Got {copilot!r}"
        blocker = sandbox.environment.home / ".agents" / "skills" / "sample"
        blocker.mkdir(parents=True)
        (blocker / "SKILL.md").write_text("Raced in.\n", encoding="utf-8")

        results = Applier(ManifestStore(sandbox.environment.state_directory)).apply(
            plan
        )

        by_name = {result.name: result for result in results}
        assert by_name["agents"].error is not None, f"Got {results!r}"
        assert by_name["copilot"].applied == 0, f"Got {results!r}"
        assert "not applied: agents failed" in str(by_name["copilot"].error)

    def test_vscode_keeps_instructions_copilot_cannot_take(
        self, sandbox: Sandbox
    ) -> None:
        """Copilot skips an unscoped instruction (lossy); VS Code keeps it."""
        route = RouteConfig(sources=("library",), destinations=("copilot", "vscode"))
        plan = self.plan(sandbox, ("copilot", "vscode"), routes=(route,))

        vscode = self.skipped(plan, "vscode")
        assert "library:instructions/scoped" in vscode, f"Got {vscode!r}"
        assert "library:instructions/global" not in vscode, (
            f"Expected the unscoped instruction written to VS Code, got {vscode!r}"
        )
        assert plan.warnings == (), f"Expected no warning, got {plan.warnings!r}"

    def test_custom_path_destinations_neither_cover_nor_warn(
        self, sandbox: Sandbox
    ) -> None:
        """A destination outside the harness's folder is invisible to the harness."""
        plan = self.plan(sandbox, ("copilot", "agents"), custom=("copilot",))

        assert "library:skills/sample" not in self.skipped(plan, "copilot"), (
            f"Expected the skill written to the custom copilot path, got {plan!r}"
        )
        assert plan.warnings == (), f"Expected no warning, got {plan.warnings!r}"

    def test_unavoidable_duplicate_is_warned(self, sandbox: Sandbox) -> None:
        """VS Code reads both ~/.claude/skills and ~/.agents/skills."""
        plan = self.plan(sandbox, ("claude", "vscode", "agents"))

        assert plan.warnings == (
            "VS Code loads 1 item(s) from both claude and agents:"
            " skills/sample; exclude them from one route",
        ), f"Expected one VS Code warning, got {plan.warnings!r}"

    def test_unrouted_cover_does_not_skip(self, sandbox: Sandbox) -> None:
        """An agents destination that is not sent the skill covers nothing."""
        routes = (
            RouteConfig(("library",), ("copilot",)),
            RouteConfig(("library",), ("agents",), include=("instructions/*",)),
        )
        plan = self.plan(sandbox, ("copilot", "agents"), routes=routes)

        assert "library:skills/sample" not in self.skipped(plan, "copilot"), (
            f"Expected the skill kept in copilot, got {plan!r}"
        )

    def test_custom_path_other_is_not_read(self, sandbox: Sandbox) -> None:
        """VS Code does not read an agents destination outside ~/.agents."""
        plan = self.plan(sandbox, ("claude", "vscode", "agents"), custom=("agents",))

        assert plan.warnings == (), f"Expected no warning, got {plan.warnings!r}"

    def test_long_warning_lists_five_items(self) -> None:
        """Warnings name five items and count the rest."""
        labels = [f"skills/s{index}" for index in range(7)]

        result = Planner._sample(labels)

        assert result == (
            "skills/s0, skills/s1, skills/s2, skills/s3, skills/s4 and 2 more"
        ), f"Got {result!r}"

    def test_identical_skill_from_another_source_is_still_pending(
        self, sandbox: Sandbox, tmp_path: Path
    ) -> None:
        """A cover's file deduplicated across sources is pending for every source."""
        second = tmp_path / "second"
        shutil.copytree(sandbox.library / "skills", second / "skills")
        config = sandbox.configure()
        registry = ProfileRegistry()
        destinations = {}
        for name in ("copilot", "agents"):
            default = registry.get(name).default_path(sandbox.environment)
            assert default is not None
            destinations[name] = DestinationConfig(name, name, default)
        config = replace(
            config,
            sources={**config.sources, "second": Builder.source(second, name="second")},
            destinations=destinations,
            routes=(
                RouteConfig(("library", "second"), ("agents",)),
                RouteConfig(("second",), ("copilot",)),
            ),
        )

        plan = TestPlanner.planner(sandbox).plan(config, only=("copilot",))

        assert "second:skills/sample" not in self.skipped(plan, "copilot"), (
            f"Expected the skill kept until agents has it, got {plan!r}"
        )

    def test_cover_only_source_failure_does_not_fail_the_run(
        self, sandbox: Sandbox, tmp_path: Path
    ) -> None:
        """A broken library read only to check coverage is not this run's error."""
        config = sandbox.configure()
        registry = ProfileRegistry()
        destinations = {}
        for name in ("copilot", "agents"):
            default = registry.get(name).default_path(sandbox.environment)
            assert default is not None
            destinations[name] = DestinationConfig(name, name, default)
        missing = Builder.source(tmp_path / "missing", name="other")
        config = replace(
            config,
            sources={**config.sources, "other": missing},
            destinations=destinations,
            routes=(
                RouteConfig(("library",), ("copilot",)),
                RouteConfig(("other",), ("agents",)),
            ),
        )

        plan = TestPlanner.planner(sandbox).plan(config, only=("copilot",))

        assert plan.ok, f"Expected a clean copilot run, got {plan!r}"
        assert "library:skills/sample" not in self.skipped(plan, "copilot"), (
            f"Expected copilot to keep its skill, got {plan!r}"
        )
