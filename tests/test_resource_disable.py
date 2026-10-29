"""Resource disabling journeys preserve source data and existing ownership rules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import pytest
from tests.conftest import Sandbox

from skillsinc.manifest import ManifestStore

pytestmark = pytest.mark.integration

OUTPUTS: Final = {
    "skills/sample": "skills/sample/SKILL.md",
    "instructions/scoped": "instructions/scoped.instructions.md",
    "agents/reviewer": "agents/reviewer.agent.md",
    "prompts/ask": "prompts/ask.prompt.md",
    "mcp/server": "mcp/server.json",
    "resources/reference": "resources/reference.md",
    "hooks/session": "hooks/session.json",
}


class DisableCase:
    """Build explicit synthetic routes without touching real harness folders."""

    @staticmethod
    def complete_library(sandbox: Sandbox) -> None:
        """Extend the standard fixture with the remaining three resource kinds."""
        files = {
            "mcp/server.json": '{"command": "synthetic-mcp", "args": []}\n',
            "resources/reference.md": "Synthetic reference.\n",
            "hooks/session.json": (
                '{"version": 1, "hooks": {"sessionStart": '
                '[{"bash": "synthetic-hook"}]}}\n'
            ),
        }
        for relative, content in files.items():
            path = sandbox.library / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    @staticmethod
    def configure(  # noqa: PLR0913 - explicit journey configuration overrides
        sandbox: Sandbox,
        disabled: tuple[str, ...] = (),
        *,
        profile: str = "generic",
        include: tuple[str, ...] = ("*/*",),
        exclude: tuple[str, ...] = (),
        enabled: bool = True,
        duplicate_routes: bool = False,
    ) -> None:
        """Write one source and an explicit temporary destination."""
        route = {
            "sources": ["library"],
            "destinations": ["target"],
            "include": list(include),
            "exclude": list(exclude),
            "allow_lossy": True,
        }
        config = {
            "version": 1,
            "sources": {
                "library": {
                    "path": sandbox.library.as_posix(),
                    "disabled": list(disabled),
                }
            },
            "destinations": {
                "target": {
                    "profile": profile,
                    "path": sandbox.root.as_posix(),
                    "enabled": enabled,
                }
            },
            "routes": [route, route] if duplicate_routes else [route],
        }
        sandbox.config_path.parent.mkdir(parents=True, exist_ok=True)
        sandbox.config_path.write_text(json.dumps(config), encoding="utf-8")

    @staticmethod
    def contents(root: Path) -> dict[str, bytes]:
        """Snapshot file bytes without depending on timestamps or permissions."""
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }


class TestDisableResources:
    """Every customization kind can be retired without deleting its source."""

    @pytest.mark.parametrize("selector", OUTPUTS)
    def test_preview_disable_and_reenable_every_kind(
        self, sandbox: Sandbox, selector: str
    ) -> None:
        """Preview writes nothing; sync retires managed output and undo restores it."""
        DisableCase.complete_library(sandbox)
        DisableCase.configure(sandbox)
        assert sandbox.run("sync")[0] == 0, "Expected initial seven-kind sync"
        source_before = DisableCase.contents(sandbox.library)
        unmanaged = sandbox.root / "user-only.txt"
        unmanaged.write_bytes(b"Unmanaged content")
        destination_before = DisableCase.contents(sandbox.root)
        state_before = DisableCase.contents(sandbox.environment.state_directory)
        DisableCase.configure(sandbox, (selector,))

        status, output = sandbox.run("plan", "--json")
        planned = json.loads(output)["destinations"][0]
        assert status == 0, f"Expected valid disable preview: {output}"
        assert any(
            skip["item"] == f"library:{selector}"
            and skip["reason"] == "disabled by sources.library.disabled"
            for skip in planned["skipped"]
        ), f"Expected explicit disabled reason: {output}"
        assert any(
            change["action"] == "delete" and change["path"] == OUTPUTS[selector]
            for change in planned["changes"]
        ), f"Expected managed deletion in preview: {output}"
        assert (
            "disabled by sources.library.disabled"
            in sandbox.run("plan", "--verbose")[1]
        ), "Expected verbose preview to explain disabling"
        assert DisableCase.contents(sandbox.root) == destination_before, (
            "Expected previews not to write destination files"
        )
        assert (
            DisableCase.contents(sandbox.environment.state_directory) == state_before
        ), "Expected previews not to write ownership state"

        status, output = sandbox.run("sync")
        assert status == 0, f"Expected safe disable sync: {output}"
        assert not (sandbox.root / OUTPUTS[selector]).exists(), (
            "Expected managed output removed"
        )
        assert unmanaged.read_bytes() == b"Unmanaged content", (
            "Expected unowned file preserved"
        )
        assert DisableCase.contents(sandbox.library) == source_before, (
            "Expected source bytes preserved"
        )
        DisableCase.configure(sandbox)
        assert sandbox.run("sync")[0] == 0, (
            "Expected removing selector to reenable resource"
        )
        assert DisableCase.contents(sandbox.root) == destination_before, (
            "Expected original outputs restored without touching unowned content"
        )
        assert "up to date" in sandbox.run("plan")[1], "Expected reenabled convergence"

    def test_duplicate_routes_cannot_reenable_disabled_resource(
        self, sandbox: Sandbox
    ) -> None:
        """Source-level disabling dominates all routes, including duplicate matches."""
        DisableCase.configure(sandbox, ("skills/sample",), duplicate_routes=True)

        status, output = sandbox.run("sync", "--json")

        assert status == 0, f"Expected a valid disabled route: {output}"
        assert not (sandbox.root / OUTPUTS["skills/sample"]).exists(), (
            "Expected duplicate routes not to reenable a disabled skill"
        )
        skips = json.loads(output)["destinations"][0]["skipped"]
        assert sum(skip["item"] == "library:skills/sample" for skip in skips) == 1, (
            f"Expected one disabled item after route deduplication: {output}"
        )

    @pytest.mark.parametrize(
        "pattern", ["plugins/Kit/skills/Wid*", "plugins/*/skills/Widget"]
    )
    def test_plugin_selector_disables_expanded_skill(
        self, sandbox: Sandbox, pattern: str
    ) -> None:
        """Plugin-scoped selectors match expanded resources without altering source."""
        entry = sandbox.library / "plugins/Kit/skills/Widget/SKILL.md"
        entry.parent.mkdir(parents=True)
        entry.write_bytes(
            b"---\ndescription: Synthetic plugin skill\n---\nPlugin body.\n"
        )
        DisableCase.configure(sandbox, (pattern,))

        status, output = sandbox.run("sync", "--verbose")

        assert status == 0, f"Expected plugin disable success: {output}"
        assert not (sandbox.root / "skills/Widget/SKILL.md").exists(), (
            "Expected plugin skill disabled"
        )
        assert (sandbox.root / OUTPUTS["skills/sample"]).is_file(), (
            "Expected ordinary skill enabled"
        )
        assert entry.read_bytes().endswith(b"Plugin body.\n"), (
            "Expected plugin source intact"
        )

    def test_disabling_is_scoped_to_one_source(
        self, sandbox: Sandbox, tmp_path: Path
    ) -> None:
        """The same selector in another source still reaches its destination."""
        other = tmp_path / "other-library"
        entry = other / "skills/sample/SKILL.md"
        entry.parent.mkdir(parents=True)
        entry.write_bytes(b"---\ndescription: Other source skill\n---\nOther body.\n")
        config = {
            "version": 1,
            "sources": {
                "library": {
                    "path": sandbox.library.as_posix(),
                    "disabled": ["skills/sample"],
                },
                "other": {"path": other.as_posix()},
            },
            "destinations": {
                "target": {"profile": "generic", "path": sandbox.root.as_posix()}
            },
            "routes": [
                {
                    "sources": ["library", "other"],
                    "destinations": ["target"],
                    "include": ["skills/*"],
                }
            ],
        }
        sandbox.config_path.parent.mkdir(parents=True, exist_ok=True)
        sandbox.config_path.write_text(json.dumps(config), encoding="utf-8")

        status, output = sandbox.run("sync")

        assert status == 0, f"Expected the other source still enabled: {output}"
        assert (
            sandbox.root / "skills/sample/SKILL.md"
        ).read_bytes() == entry.read_bytes(), (
            "Expected output from the enabled source with the same selector"
        )


class TestDisableSafety:
    """Disabling follows ownership-aware cleanup and existing fail-closed rules."""

    @pytest.mark.parametrize("profile", ["codex", "claude"])
    @pytest.mark.parametrize("excluded", [False, True])
    def test_partial_shared_unit_disable_blocks_instead_of_overwriting_edits(
        self, sandbox: Sandbox, profile: str, *, excluded: bool
    ) -> None:
        """Disabling one instruction or hook contributor protects its shared unit."""
        DisableCase.complete_library(sandbox)
        other_hook = sandbox.library / "hooks/other.json"
        other_hook.write_text(
            '{"version":1,"hooks":{"sessionStart":[{"bash":"synthetic-other"}]}}',
            encoding="utf-8",
        )
        include = ("instructions/*",) if profile == "codex" else ("hooks/*",)
        disabled = (
            ("instructions/scoped",) if profile == "codex" else ("hooks/session",)
        )
        DisableCase.configure(sandbox, profile=profile, include=include)
        assert sandbox.run("sync")[0] == 0, "Expected shared-unit setup"
        target = sandbox.root / ("AGENTS.md" if profile == "codex" else "settings.json")
        if profile == "codex":
            current = target.read_text(encoding="utf-8").replace(
                "<!-- skillsinc:end -->",
                "Local shared-block edit.\n<!-- skillsinc:end -->",
            )
            target.write_text(current, encoding="utf-8")
        else:
            settings = json.loads(target.read_text(encoding="utf-8"))
            settings["hooks"]["SessionStart"].append(
                {
                    "hooks": [{"type": "command", "command": "local-only"}],
                }
            )
            target.write_text(json.dumps(settings), encoding="utf-8")
        before = DisableCase.contents(sandbox.root)
        state_before = DisableCase.contents(sandbox.environment.state_directory)
        source_before = DisableCase.contents(sandbox.library)
        DisableCase.configure(
            sandbox,
            disabled,
            profile=profile,
            include=include,
            exclude=disabled if excluded else (),
        )

        status, output = sandbox.run("sync")

        assert status == 1, (
            f"Expected disabling blocked before overwriting edits: {output}"
        )
        assert "while disabling resources" in output, (
            f"Expected preservation reason: {output}"
        )
        assert DisableCase.contents(sandbox.root) == before, (
            "Expected shared edits preserved"
        )
        assert (
            DisableCase.contents(sandbox.environment.state_directory) == state_before
        ), "Expected ownership state preserved for the blocked destination"
        assert DisableCase.contents(sandbox.library) == source_before, (
            "Expected source bytes unchanged"
        )
        DisableCase.configure(sandbox, profile=profile, include=include)
        status, output = sandbox.run("plan")
        assert status == 0, (
            f"Expected ordinary repair policy without disabling: {output}"
        )
        assert "overwrites local edits" in output, (
            "Expected the normal overwrite warning"
        )

    @pytest.mark.parametrize(
        "selector", ["resources/reference", "resources/not-present"]
    )
    def test_unrelated_drift_blocks_disable_transaction(
        self, sandbox: Sandbox, selector: str
    ) -> None:
        """Conservative protection also prevents overwriting other destination drift."""
        DisableCase.complete_library(sandbox)
        DisableCase.configure(sandbox)
        assert sandbox.run("sync")[0] == 0, "Expected initial generic sync"
        edited = sandbox.root / "skills/sample/SKILL.md"
        edited.write_bytes(b"Unrelated local edit")
        before = DisableCase.contents(sandbox.root)
        DisableCase.configure(sandbox, (selector,))

        status, output = sandbox.run("sync")

        assert status == 1, f"Expected unsafe disable transaction blocked: {output}"
        assert "while disabling resources" in output, "Expected edit protection reason"
        assert DisableCase.contents(sandbox.root) == before, (
            "Expected neither unrelated edits nor pending removals applied"
        )

    @pytest.mark.parametrize("profile", ["generic", "codex", "copilot"])
    def test_edited_managed_content_is_released_and_reenable_blocks(
        self, sandbox: Sandbox, profile: str
    ) -> None:
        """Edited whole files, instruction blocks and MCP entries survive disabling."""
        DisableCase.complete_library(sandbox)
        includes = {
            "generic": ("resources/*",),
            "codex": ("instructions/*",),
            "copilot": ("mcp/*",),
        }
        paths = {
            "generic": "resources/reference.md",
            "codex": "AGENTS.md",
            "copilot": "mcp-config.json",
        }
        DisableCase.configure(sandbox, profile=profile, include=includes[profile])
        assert sandbox.run("sync")[0] == 0, "Expected initial managed content"
        target = sandbox.root / paths[profile]
        if profile == "codex":
            text = target.read_text(encoding="utf-8").replace(
                "<!-- skillsinc:end -->", "Local block edit.\n<!-- skillsinc:end -->"
            )
            target.write_text("Outside user text.\n" + text, encoding="utf-8")
        elif profile == "copilot":
            target.write_text(
                '{"mcpServers":{"server":{"command":"local-edit"},'
                '"unmanaged":{"command":"user-server"}},"userSetting":true}',
                encoding="utf-8",
            )
        else:
            target.write_bytes(b"Locally edited managed file.\n")
        edited = target.read_bytes()
        source_before = DisableCase.contents(sandbox.library)
        DisableCase.configure(
            sandbox, includes[profile], profile=profile, include=includes[profile]
        )

        status, output = sandbox.run("sync")

        assert status == 0, f"Expected ownership-aware disabling: {output}"
        assert "forget" in output, f"Expected edited ownership released: {output}"
        assert target.read_bytes() == edited, "Expected exact edited content preserved"
        manifest = ManifestStore(sandbox.environment.state_directory).load(
            "target", sandbox.root
        )
        assert (manifest.files, manifest.blocks, manifest.entries) == ({}, {}, {}), (
            "Expected released content no longer owned"
        )
        DisableCase.configure(sandbox, profile=profile, include=includes[profile])
        status, output = sandbox.run("sync")
        assert status == 1, f"Expected unmanaged conflict on reenable: {output}"
        assert "not managed" in output, (
            f"Expected actionable adoption diagnostic: {output}"
        )
        assert target.read_bytes() == edited, (
            "Expected reenable not to overwrite released edits"
        )
        assert DisableCase.contents(sandbox.library) == source_before, (
            "Expected sources unchanged"
        )

    @pytest.mark.parametrize("failure", ["destination-disabled", "source-missing"])
    def test_disabled_destination_or_failed_source_prevents_cleanup(
        self, sandbox: Sandbox, failure: str
    ) -> None:
        """Source selectors never bypass destination suspension or source failures."""
        DisableCase.configure(sandbox)
        assert sandbox.run("sync")[0] == 0, "Expected initial sync"
        before = DisableCase.contents(sandbox.root)
        state_before = DisableCase.contents(sandbox.environment.state_directory)
        DisableCase.configure(
            sandbox, ("skills/*",), enabled=failure != "destination-disabled"
        )
        if failure == "source-missing":
            sandbox.library.rename(sandbox.library.with_name("temporarily-unavailable"))

        status, output = sandbox.run("sync")

        expected = 1 if failure == "source-missing" else 0
        assert status == expected, f"Expected exit {expected}: {output}"
        assert DisableCase.contents(sandbox.root) == before, "Expected output preserved"
        assert (
            DisableCase.contents(sandbox.environment.state_directory) == state_before
        ), "Expected ownership state unchanged"
