"""Single-writer lock so overlapping runs (manual and scheduled) never interleave."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import TracebackType
from typing import Final, Self

from skillsinc.exceptions import LockError

STALE_AFTER_SECONDS: Final = 3600


class StateLock:
    """Exclusive lock file in the state directory; stale locks are reclaimed."""

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
        """Return True if a lock file exists but is older than the stale limit."""
        try:
            age = time.time() - self._path.stat().st_mtime
        except FileNotFoundError:
            return False
        return age > self._stale_after
