"""Test process environment capture without consulting the real user home."""

from __future__ import annotations

from pathlib import Path

import pytest

from skillsinc.paths import Environment


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
