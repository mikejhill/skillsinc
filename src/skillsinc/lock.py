"""Single-writer lock so overlapping runs (manual and scheduled) never interleave."""

from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path
from types import TracebackType
from typing import Final, Self

from skillsinc.exceptions import LockError

STALE_AFTER_SECONDS: Final = 3600
# Windows: OpenProcess access right and the exit code of a running process
PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000
STILL_ACTIVE: Final = 259
ERROR_INVALID_PARAMETER: Final = 87


def process_exists(pid: int) -> bool:
    """Return False only when no process ``pid`` exists; True when unsure."""
    if pid <= 0:
        return True
    if sys.platform == "win32":  # pragma: no cover - Windows only
        return _windows_process_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _windows_process_exists(pid: int) -> bool:  # pragma: no cover - Windows only
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # ty: ignore[unresolved-attribute]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)  # noqa: FBT003 - Win32 BOOL
    if not handle:
        error = int(ctypes.get_last_error())  # ty: ignore[unresolved-attribute]
        return error != ERROR_INVALID_PARAMETER
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


class StateLock:
    """Exclusive lock file in the state directory; stale locks are reclaimed.

    A lock is stale when its holder process no longer exists (a sync killed by
    Cancel or a crash cannot release it) or when it is older than the limit.
    """

    def __init__(self, path: Path, *, stale_after: float = STALE_AFTER_SECONDS) -> None:
        self._path = path
        self._stale_after = stale_after
        self._held = False

    def __enter__(self) -> Self:
        """Acquire the lock.

        Raises:
            LockError: If another live run holds it.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._is_stale():
            self._path.unlink(missing_ok=True)
        try:
            descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as err:
            msg = f"another SkillSinc run is in progress (lock file {self._path})"
            raise LockError(msg) from err
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()}\n")
        self._held = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the lock."""
        if self._held:
            self._path.unlink(missing_ok=True)
            self._held = False

    def _is_stale(self) -> bool:
        """Return True if the holder is gone or the lock is older than the limit."""
        try:
            age = time.time() - self._path.stat().st_mtime
        except FileNotFoundError:
            return False
        try:
            content = self._path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            content = ""
        if age > self._stale_after:
            return True
        holder = int(content) if content.isdigit() else 0
        return holder != os.getpid() and not process_exists(holder)
