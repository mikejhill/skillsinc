"""Test route selection, render collisions, and source/manifest failures."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from tests.conftest import Builder, Sandbox

from skillsinc.config import Config, DestinationConfig, RouteConfig
from skillsinc.exceptions import ConfigurationError
from skillsinc.manifest import Manifest, ManifestStore
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
