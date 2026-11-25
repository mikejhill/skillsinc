"""Test exclusive locking, stale reclamation, and exception cleanup."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from skillsinc.exceptions import LockError
from skillsinc.lock import StateLock, process_exists


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

    def test_lock_of_a_dead_process_is_reclaimed(self, tmp_path: Path) -> None:
        """A sync killed while holding the lock does not block the next one."""
        path = tmp_path / "sync.lock"
        child = subprocess.run(
            [sys.executable, "-c", "import os; print(os.getpid())"],
            capture_output=True,
            check=True,
            encoding="utf-8",
        )
        path.write_text(child.stdout, encoding="utf-8")

        with StateLock(path):
            content = path.read_text(encoding="utf-8")

        assert content == f"{os.getpid()}\n"

    @pytest.mark.parametrize("content", ["", "not a pid", "0"])
    def test_lock_without_a_holder_is_kept(self, tmp_path: Path, content: str) -> None:
        """A lock whose holder is unknown (being written) is respected."""
        path = tmp_path / "sync.lock"
        path.write_text(content, encoding="utf-8")

        with pytest.raises(LockError), StateLock(path):
            pytest.fail("Expected the lock to be held")

    def test_unreadable_lock_is_kept(self, tmp_path: Path) -> None:
        """A lock that cannot be read as text counts as held."""
        path = tmp_path / "sync.lock"
        path.write_bytes(b"\xff\xfe")

        with pytest.raises(LockError), StateLock(path):
            pytest.fail("Expected the lock to be held")

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
    def test_process_exists(self) -> None:
        """Only a missing process is reported missing."""
        assert process_exists(os.getpid())
        assert process_exists(1)
        assert process_exists(-5)

    def test_process_of_another_user_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A process we may not signal still exists."""

        def refuse(pid: int, signal: int) -> None:
            raise PermissionError(pid, signal)

        monkeypatch.setattr("skillsinc.lock.os.kill", refuse)
        monkeypatch.setattr("skillsinc.lock.sys.platform", "linux")

        assert process_exists(12345)

    def test_exception_releases_lock(self, tmp_path: Path) -> None:
        """Exceptions from the writer still release the lock."""
        path = tmp_path / "sync.lock"

        with pytest.raises(ValueError, match="invalid literal"), StateLock(path):
            int("writer failed")

        assert path.exists() is False, f"Expected cleanup after exception, found {path}"
