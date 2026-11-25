"""Start child processes without opening console windows on Windows.

Scheduled runs use ``pythonw.exe``, which has no console. Windows gives a
console program such as ``git.exe`` started from a windowless parent a new,
visible console window, so such a parent starts children with
``CREATE_NO_WINDOW``: they share one hidden console, as do their own children
(``git-remote-https``, credential helpers, ``ssh``).

A run in a terminal keeps the default, so the children stay in the terminal's
console and Ctrl+C reaches them and their helpers too.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from typing import Final


class ChildWindows:
    """Choose ``creationflags`` that open no console window for a child."""

    CREATE_NO_WINDOW: Final = 0x08000000

    def __init__(
        self,
        platform: str = sys.platform,
        attached: Callable[[], bool] | None = None,
    ) -> None:
        self._platform = platform
        self._attached = attached or self._console_attached

    def flags(self) -> int:
        """Return ``CREATE_NO_WINDOW`` on Windows without a console, else 0."""
        if self._platform != "win32" or self._attached():
            return 0
        return self.CREATE_NO_WINDOW

    @staticmethod
    def _console_attached() -> bool:  # pragma: no cover - needs Windows
        """Return True unless this is a Windows process with no console."""
        if sys.platform != "win32":
            return True
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
