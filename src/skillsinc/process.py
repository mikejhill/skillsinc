"""Start child processes without opening console windows on Windows.

Scheduled runs use ``pythonw.exe``, which has no console. Windows gives a
console program such as ``git.exe`` started from a windowless parent a new,
visible console window, so every child process SkillSinc starts must pass
``creationflags=NO_WINDOW``. The flag gives the child a hidden console that
its own children (``git-remote-https``, credential helpers, ``ssh``) share.
Output is captured either way, so the flag changes nothing in a terminal.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Final

NO_WINDOW: Final = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
"""``CREATE_NO_WINDOW`` on Windows; elsewhere 0, which changes nothing."""
