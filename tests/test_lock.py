"""Test exclusive locking, stale reclamation, and exception cleanup."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from skillsinc.exceptions import LockError
from skillsinc.lock import StateLock


class TestStateLock:
    """Only one writer may hold the temporary state lock."""

    def test_lock_exists_only_inside_context(self, tmp_path: Path) -> None:
        """Acquisition writes the owner PID and release removes the lock."""
        path = tmp_path / "state/sync.lock"

        with StateLock(path):
            content = path.read_text(encoding="utf-8")

        assert content == f"{os.getpid()}\n", f"Expected current PID, got {content!r}"
        assert path.exists() is False, f"Expected released lock, found {path}"

    def test_second_writer_is_refused(self, tmp_path: Path) -> None:
        """A held lock rejects a second writer without removing the first lock."""
        path = tmp_path / "sync.lock"

        with (
            StateLock(path),
            pytest.raises(LockError, match="another SkillSinc run"),
            StateLock(path),
        ):
            pytest.fail("Expected the second writer to be rejected")

    def test_stale_lock_is_reclaimed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A controlled old timestamp permits a replacement writer."""
        path = tmp_path / "sync.lock"
        path.write_text("old", encoding="utf-8")
        os.utime(path, (100, 100))
        monkeypatch.setattr("skillsinc.lock.time.time", lambda: 200)

        with StateLock(path, stale_after=99):
            content = path.read_text(encoding="utf-8")

        assert content == f"{os.getpid()}\n", (
            f"Expected replacement PID, got {content!r}"
        )

    def test_exception_releases_lock(self, tmp_path: Path) -> None:
        """Exceptions from the writer still release the lock."""
        path = tmp_path / "sync.lock"

        with pytest.raises(ValueError, match="invalid literal"), StateLock(path):
            int("writer failed")

        assert path.exists() is False, f"Expected cleanup after exception, found {path}"
