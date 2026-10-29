"""Resource disabling through an installed CLI against synthetic user homes."""

from __future__ import annotations

import json
from pathlib import Path

from tests.e2e.conftest import Cli


class ResourceConfig:
    """Write process-test settings with explicit temporary source and destination."""

    @staticmethod
    def write(cli: Cli, library: Path, *, disabled: bool) -> Path:
        """Configure two matching routes so disabling must dominate both."""
        root = cli.home / "destination"
        route = {
            "sources": ["library"],
            "destinations": ["target"],
            "include": ["skills/*"],
        }
        config = {
            "version": 1,
            "sources": {
                "library": {
                    "path": library.as_posix(),
                    "disabled": ["skills/review"] if disabled else [],
                }
            },
            "destinations": {"target": {"profile": "generic", "path": root.as_posix()}},
            "routes": [route, route],
        }
        cli.config_file.parent.mkdir(parents=True, exist_ok=True)
        cli.config_file.write_text(json.dumps(config), encoding="utf-8")
        return root


class TestResourceDisableProcess:
    """Preview, retirement, restoration and edited-file safety match CLI promises."""

    def test_disable_preview_cleanup_and_reenable(
        self, cli: Cli, library: Path
    ) -> None:
        """The installed package honors resource disabling without source changes."""
        root = ResourceConfig.write(cli, library, disabled=False)
        assert cli.run("sync").status == 0, "Expected initial skill sync"
        target = root / "skills/review/SKILL.md"
        original = target.read_bytes()
        source = library / "skills/review/SKILL.md"
        unmanaged = root / "user-only.txt"
        unmanaged.write_bytes(b"User-owned content")
        state = (cli.state_directory / "manifests/target.json").read_bytes()
        ResourceConfig.write(cli, library, disabled=True)

        preview = cli.run("plan", "--json")

        assert preview.status == 0, f"Expected valid disable preview: {preview!r}"
        destination = preview.json()["destinations"][0]
        assert any(change["action"] == "delete" for change in destination["changes"]), (
            f"Expected deletion preview: {preview.stdout}"
        )
        assert (
            destination["skipped"][0]["reason"]
            == "disabled by sources.library.disabled"
        ), f"Expected disabled reason: {preview.stdout}"
        assert target.read_bytes() == original, "Expected preview not to write target"
        assert (cli.state_directory / "manifests/target.json").read_bytes() == state, (
            "Expected preview not to write ownership state"
        )
        applied = cli.run("sync")
        assert applied.status == 0, f"Expected resource retirement: {applied!r}"
        assert not target.exists(), "Expected managed skill removed"
        assert source.read_bytes() == original, "Expected authored source preserved"
        assert unmanaged.read_bytes() == b"User-owned content", (
            "Expected unowned output preserved"
        )
        ResourceConfig.write(cli, library, disabled=False)
        restored = cli.run("sync")
        assert restored.status == 0, f"Expected resource restoration: {restored!r}"
        assert target.read_bytes() == original, "Expected authored skill restored"
        assert not cli.run("plan", "--json").json()["destinations"][0]["changes"], (
            "Expected restored convergence"
        )

    def test_disable_preserves_edited_file_and_reenable_requires_adoption(
        self, cli: Cli, library: Path
    ) -> None:
        """An edited skill is released, and reenable cannot silently reclaim it."""
        root = ResourceConfig.write(cli, library, disabled=False)
        assert cli.run("sync").status == 0, "Expected initial skill sync"
        target = root / "skills/review/SKILL.md"
        target.write_bytes(b"Locally edited skill")
        ResourceConfig.write(cli, library, disabled=True)

        retired = cli.run("sync")

        assert retired.status == 0, f"Expected safe edited-file retirement: {retired!r}"
        assert "forget" in retired.stdout, f"Expected ownership release: {retired!r}"
        assert target.read_bytes() == b"Locally edited skill", (
            "Expected local edits preserved"
        )
        ResourceConfig.write(cli, library, disabled=False)
        restored = cli.run("sync")
        assert restored.status == 1, (
            f"Expected explicit adoption requirement: {restored!r}"
        )
        assert "not managed" in restored.stdout, (
            f"Expected conflict diagnostic: {restored!r}"
        )
        assert target.read_bytes() == b"Locally edited skill", (
            "Expected released edits preserved"
        )
