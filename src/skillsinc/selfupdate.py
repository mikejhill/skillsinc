"""Find newer SkillSinc releases on PyPI and plan how to install one.

The tray checks PyPI's JSON API for the newest release. Installing depends on
how SkillSinc was installed: ``uv tool``, ``pipx``, or pip in a virtual
environment can be upgraded; a working copy or a system Python is left to the
user. The upgrade runs from a small script after the tray exits, because a
running interpreter's files cannot be replaced on Windows, and the script
starts the tray again afterwards.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Final

from skillsinc.exceptions import AppError
from skillsinc.schema import write_atomically
from skillsinc.upgrades import version_key

PYPI_URL: Final = "https://pypi.org/pypi/skillsinc/json"
RELEASES_URL: Final = "https://github.com/mikejhill/skillsinc/releases"
TIMEOUT_SECONDS: Final = 10
MAXIMUM_RESPONSE_BYTES: Final = 4 << 20
PACKAGE: Final = "skillsinc"
TRAY_PACKAGE: Final = "skillsinc[tray]"
SCRIPT_STEM: Final = "update"
LOG_FILE: Final = "update.log"

# Marker file in a tool's environment, the tool, and its upgrade verb
TOOL_MANAGERS: Final = (
    ("uv-receipt.toml", "uv", ("tool", "upgrade")),
    ("pipx_metadata.json", "pipx", ("upgrade",)),
)

type Fetch = Callable[[str], bytes]
type Which = Callable[[str], str | None]


class UpdateError(AppError):
    """The newest release could not be found."""


def fetch_url(url: str) -> bytes:
    """Return the body of ``url`` (HTTPS GET, bounded size and time).

    Raises:
        OSError: If the request fails.
    """
    request = urllib.request.Request(  # noqa: S310 - a fixed https URL
        url, headers={"Accept": "application/json", "User-Agent": "skillsinc"}
    )
    opened = urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS)  # noqa: S310
    with opened as response:
        return bytes(response.read(MAXIMUM_RESPONSE_BYTES))


@dataclass(frozen=True, slots=True)
class Release:
    """The newest release and whether it is newer than the running one."""

    latest: str
    current: str

    @property
    def newer(self) -> bool:
        """Return True if ``latest`` is a later release than ``current``."""
        return version_key(self.latest) > version_key(self.current)


class ReleaseFeed:
    """PyPI's record of SkillSinc releases."""

    def __init__(self, fetch: Fetch = fetch_url, url: str = PYPI_URL) -> None:
        self._fetch = fetch
        self._url = url

    def latest(self, current: str) -> Release:
        """Return the newest release, compared with ``current``.

        Raises:
            UpdateError: If PyPI cannot be reached or answers unexpectedly.
        """
        try:
            data: object = json.loads(self._fetch(self._url))
        except (OSError, ValueError) as err:
            msg = f"could not check for updates: {err}"
            raise UpdateError(msg) from err
        info = data.get("info") if isinstance(data, dict) else None
        latest = info.get("version") if isinstance(info, dict) else None
        if not isinstance(latest, str) or not version_key(latest):
            msg = "could not check for updates: PyPI sent no version"
            raise UpdateError(msg)
        return Release(latest, current)


@dataclass(frozen=True, slots=True)
class Installation:
    """How this copy of SkillSinc was installed, and how to upgrade it."""

    kind: str
    """``uv``, ``pipx``, ``pip``, or ``manual``."""
    command: tuple[str, ...] = ()
    """The upgrade command; empty when SkillSinc cannot upgrade itself."""
    reason: str = ""
    """Why not, for ``manual``."""

    @property
    def can_upgrade(self) -> bool:
        """Return True if SkillSinc can run the upgrade itself."""
        return bool(self.command)

    @classmethod
    def detect(
        cls,
        prefix: Path | None = None,
        executable: Path | None = None,
        base_prefix: Path | None = None,
        which: Which = shutil.which,
    ) -> Installation:
        """Return how the running interpreter's environment was installed."""
        prefix = prefix or Path(sys.prefix)
        executable = executable or Path(sys.executable)
        base_prefix = base_prefix or Path(sys.base_prefix)
        for marker, tool, verb in TOOL_MANAGERS:
            if (prefix / marker).is_file():
                found = which(tool)
                if found is None:
                    reason = f"{tool} is not on PATH; run {tool} {' '.join(verb)}"
                    return cls("manual", reason=reason)
                return cls(tool, (found, *verb, PACKAGE))
        if cls._editable():
            return cls("manual", reason="SkillSinc runs from a working copy")
        if prefix == base_prefix:
            return cls(
                "manual",
                reason="SkillSinc is installed in a system Python; upgrade it with"
                " the tool that installed it",
            )
        return cls(
            "pip",
            (str(executable), "-m", "pip", "install", "--upgrade", TRAY_PACKAGE),
        )

    @staticmethod
    def _editable() -> bool:
        """Return True if the installed distribution is an editable checkout."""
        try:
            text = distribution(PACKAGE).read_text("direct_url.json")
        except PackageNotFoundError:  # pragma: no cover - a bare checkout
            return True
        if text is None or not text:
            return False
        try:
            data: object = json.loads(text)
        except ValueError:
            return False
        options = data.get("dir_info") if isinstance(data, dict) else None
        return isinstance(options, dict) and options.get("editable") is True


@dataclass(frozen=True, slots=True)
class UpdateScript:
    """A script that waits for the tray to exit, upgrades, and restarts it."""

    path: Path
    text: str
    program: str
    arguments: tuple[str, ...]

    @classmethod
    def write(
        cls,
        folder: Path,
        upgrade: Sequence[str],
        relaunch: Sequence[str],
        platform: str = sys.platform,
    ) -> UpdateScript:
        """Write the script into ``folder`` and return how to start it.

        Raises:
            OSError: If the script cannot be written.
        """
        log = folder / LOG_FILE
        if platform == "win32":
            path = folder / f"{SCRIPT_STEM}.cmd"
            text = (
                "@echo off\r\n"
                "rem Written by the SkillSinc tray: upgrade after it exits\r\n"
                "ping -n 4 127.0.0.1 >nul\r\n"
                f'{subprocess.list2cmdline(upgrade)} >>"{log}" 2>&1\r\n'
                f'start "" {subprocess.list2cmdline(relaunch)}\r\n'
            )
            program, arguments = "cmd.exe", ("/c", str(path))
        else:
            path = folder / f"{SCRIPT_STEM}.sh"
            text = (
                "#!/bin/sh\n"
                "# Written by the SkillSinc tray: upgrade after it exits\n"
                "sleep 3\n"
                f"{shlex.join(upgrade)} >>{shlex.quote(str(log))} 2>&1\n"
                f"nohup {shlex.join(relaunch)} >/dev/null 2>&1 &\n"
            )
            program, arguments = "/bin/sh", (str(path),)
        folder.mkdir(parents=True, exist_ok=True)
        write_atomically(path, text)
        return cls(path, text, program, arguments)
