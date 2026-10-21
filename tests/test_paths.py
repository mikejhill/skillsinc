"""Test process environment capture without consulting the real user home."""

from __future__ import annotations

from pathlib import Path

import pytest

from skillsinc.paths import Environment, destination_file


class TestEnvironment:
    """Current environment capture copies only injected process-boundary data."""

    def test_current_captures_a_snapshot_of_fake_process_inputs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Snapshot fake home, variables, and platform independently of later edits."""
        variables = {"XDG_STATE_HOME": str(tmp_path / "state")}
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr("skillsinc.paths.os.environ", variables)
        monkeypatch.setattr("skillsinc.paths.sys.platform", "test-platform")

        result = Environment.current()
        variables["XDG_STATE_HOME"] = "changed"

        assert result == Environment(
            tmp_path, {"XDG_STATE_HOME": str(tmp_path / "state")}, "test-platform"
        ), f"Expected independent fake environment snapshot, got {result!r}"

    @pytest.mark.parametrize(
        ("present", "expected"),
        [
            ((), "config.json"),
            (("config.jsonc",), "config.jsonc"),
            (("config.json", "config.jsonc"), "config.json"),
        ],
    )
    def test_default_config_file_prefers_json_and_falls_back_to_jsonc(
        self, environment: Environment, present: tuple[str, ...], expected: str
    ) -> None:
        """Default to ``config.json``; keep reading an early ``config.jsonc``."""
        directory = environment.config_home / "skillsinc"
        directory.mkdir(parents=True)
        for name in present:
            (directory / name).write_text("{}", encoding="utf-8")

        result = environment.default_config_file

        assert result == directory / expected, f"Expected {expected}, got {result}"


class TestDestinationFile:
    """Only a declared sibling's leading ``..`` is taken by name."""

    def test_sibling_is_beside_the_configured_root(self, tmp_path: Path) -> None:
        """A leading ``..`` names the configured root's parent, not a link's."""
        root = tmp_path / "home" / ".claude"

        result = destination_file(root, "../.claude.json")

        assert result == tmp_path / "home" / ".claude.json", f"Got {result}"

    def test_root_is_never_normalized(self, tmp_path: Path) -> None:
        """A ``..`` inside the configured root keeps its operating-system meaning."""
        root = tmp_path / "link" / ".." / "claude"

        result = destination_file(root, "rules/a.md")

        assert result == root / "rules" / "a.md", f"Got {result}"
