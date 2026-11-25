"""Start the tray when the user signs in.

Windows: a value under the user's ``Run`` key. Linux: an XDG autostart entry.
macOS is not supported yet; ``supported`` says so and nothing is written.

Only SkillSinc's own entries are replaced or removed: a ``Run`` value or a
desktop file at the same name that runs something else, or a desktop file that
is a link or not a regular file, is a conflict and is left alone.
"""

from __future__ import annotations

import re
import stat
import subprocess
import sys
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Final, Protocol

from skillsinc.paths import Environment
from skillsinc.schema import write_atomically

VALUE_NAME: Final = "SkillSinc"
RUN_KEY: Final = r"Software\Microsoft\Windows\CurrentVersion\Run"
DESKTOP_FILE: Final = "skillsinc-tray.desktop"
NOT_WINDOWS: Final = "the Run key exists only on Windows"
MARKER: Final = "X-SkillSinc-Tray=true"
# A Run value that starts some SkillSinc tray, from any installation
OWNED_COMMAND: Final = re.compile(r"-m\s+skillsinc\s+tray\b|skillsinc-tray", re.I)


class EntryState(Enum):
    """What is registered at the sign-in entry's name."""

    ABSENT = "absent"
    CURRENT = "current"
    """SkillSinc's entry, running this installation's command."""
    OWNED = "owned"
    """SkillSinc's entry, running another command (an older installation)."""
    CONFLICT = "conflict"
    """Something else: never replaced or removed."""


class RunKey(Protocol):
    """String values under one registry key."""

    def read(self, name: str) -> str | None:
        """Return the value, or None when absent."""

    def write(self, name: str, value: str) -> None:
        """Set the value."""

    def delete(self, name: str) -> None:
        """Remove the value if present."""


class WindowsRunKey:  # pragma: no cover - needs the Windows registry
    r"""``HKCU\...\Run`` through ``winreg``."""

    def read(self, name: str) -> str | None:
        """Return the value, or None when absent."""
        if sys.platform != "win32":
            raise OSError(NOT_WINDOWS)
        import winreg  # noqa: PLC0415 - Windows only

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _kind = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        return str(value)

    def write(self, name: str, value: str) -> None:
        """Set the value."""
        if sys.platform != "win32":
            raise OSError(NOT_WINDOWS)
        import winreg  # noqa: PLC0415 - Windows only

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        """Remove the value if present."""
        if sys.platform != "win32":
            raise OSError(NOT_WINDOWS)
        import winreg  # noqa: PLC0415 - Windows only

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            return


class LoginItem:
    """Register or remove the command that starts the tray at sign-in."""

    def __init__(
        self,
        environment: Environment,
        command: Sequence[str],
        run_key: RunKey | None = None,
    ) -> None:
        self._environment = environment
        self._command = list(command)
        self._platform = environment.platform
        self._run_key = run_key

    @property
    def supported(self) -> bool:
        """Return True if this platform can start the tray at sign-in."""
        return self._platform == "win32" or self._platform.startswith("linux")

    @property
    def command_line(self) -> str:
        """Return the registered command as one line."""
        if self._platform == "win32":
            return subprocess.list2cmdline(self._command)
        return " ".join(_quote(part) for part in self._command)

    def state(self) -> EntryState:
        """Return what is registered at the entry's name now."""
        if self._platform == "win32":
            return self._run_value_state()
        if self._platform.startswith("linux"):
            return self._desktop_state()
        return EntryState.ABSENT

    def _run_value_state(self) -> EntryState:
        value = self._key().read(VALUE_NAME)
        if value is None:
            return EntryState.ABSENT
        if value == self.command_line:
            return EntryState.CURRENT
        if OWNED_COMMAND.search(value):
            return EntryState.OWNED
        return EntryState.CONFLICT

    def _desktop_state(self) -> EntryState:
        path = self._desktop_file()
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return EntryState.ABSENT
        try:
            text = path.read_text(encoding="utf-8") if stat.S_ISREG(mode) else ""
        except (OSError, UnicodeDecodeError):
            text = ""
        if text == self._entry():
            return EntryState.CURRENT
        return EntryState.OWNED if MARKER in text.splitlines() else EntryState.CONFLICT

    def enabled(self) -> bool:
        """Return True if the tray starts at sign-in with this command."""
        return self.state() is EntryState.CURRENT

    def apply(self, *, enabled: bool) -> bool:
        """Start the tray at sign-in or stop doing so; False if unsupported.

        Raises:
            OSError: If another program's entry holds the name, or on a write
                failure.
        """
        if not self.supported:
            return False
        state = self.state()
        if state is EntryState.CONFLICT:
            if not enabled:
                return True
            where = (
                rf"HKCU\{RUN_KEY}\{VALUE_NAME}"
                if self._platform == "win32"
                else str(self._desktop_file())
            )
            msg = f"{where} belongs to another program; leaving it alone"
            raise OSError(msg)
        if self._platform == "win32":
            if enabled:
                self._key().write(VALUE_NAME, self.command_line)
            elif state is not EntryState.ABSENT:
                self._key().delete(VALUE_NAME)
            return True
        path = self._desktop_file()
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomically(path, self._entry())
        else:
            path.unlink(missing_ok=True)
        return True

    def _key(self) -> RunKey:
        if self._run_key is None:  # pragma: no cover - needs the Windows registry
            self._run_key = WindowsRunKey()
        return self._run_key

    def _desktop_file(self) -> Path:
        return self._environment.config_home / "autostart" / DESKTOP_FILE

    def _entry(self) -> str:
        return (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=SkillSinc\n"
            "Comment=Sync agent skills on a schedule\n"
            f"Exec={self.command_line}\n"
            "X-GNOME-Autostart-enabled=true\n"
            f"{MARKER}\n"
        )


def tray_command(
    executable: Path | None = None, platform: str = sys.platform
) -> list[str]:
    """Return the command that starts the tray with no console window."""
    from skillsinc.schedule import interpreter  # noqa: PLC0415 - avoids a cycle

    python = interpreter(executable or Path(sys.executable), platform)
    return [str(python), "-P", "-m", "skillsinc", "tray"]


def _quote(part: str) -> str:
    """Quote ``part`` for a desktop entry's Exec line."""
    if part and all(c.isalnum() or c in "-_./=:" for c in part):
        return part
    escaped = part.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
