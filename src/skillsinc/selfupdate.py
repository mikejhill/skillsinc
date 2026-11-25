"""Find newer SkillSinc releases on PyPI and plan how to install one.

The tray checks PyPI's JSON API for the newest release. Installing depends on
how SkillSinc was installed: ``uv tool``, ``pipx``, or pip in a virtual
environment can be upgraded; a working copy, a system Python, or a virtual
environment without pip is left to the user.

The upgrade runs from a helper after the tray exits, because a running
interpreter's files cannot be replaced on Windows. The helper is a short
standard-library program run by the base interpreter (outside the
environment being upgraded) with its commands as argument lists, so no shell
or batch file reinterprets a path. It waits for the tray's process to end,
appends the installer's output to ``update.log`` (a regular file, never
through a link), records the outcome in ``update-result.json``, and starts
the tray again. The tray reads that record at start: it reports a failed
upgrade once, and never retries that release automatically.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import re
import shutil
import sys
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Final

from skillsinc.exceptions import AppError
from skillsinc.schema import write_atomically

PYPI_URL: Final = "https://pypi.org/pypi/skillsinc/json"
RELEASES_URL: Final = "https://github.com/mikejhill/skillsinc/releases"
TIMEOUT_SECONDS: Final = 10
MAXIMUM_RESPONSE_BYTES: Final = 4 << 20
PACKAGE: Final = "skillsinc"
TRAY_PACKAGE: Final = "skillsinc[tray]"
LOG_FILE: Final = "update.log"
RESULT_FILE: Final = "update-result.json"
# How long the helper waits for the tray to exit before giving up
EXIT_WAIT_SECONDS: Final = 60
# Windows: no console window, outside the tray's process group
CREATE_NO_WINDOW: Final = 0x08000000
CREATE_NEW_PROCESS_GROUP: Final = 0x00000200
DETACHED_FLAGS: Final = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP

# Marker file in a tool's environment, the tool, its upgrade verb, and the
# variable that points the tool at the folder holding that environment
TOOL_MANAGERS: Final = (
    ("uv-receipt.toml", "uv", ("tool", "upgrade"), "UV_TOOL_DIR"),
    ("pipx_metadata.json", "pipx", ("upgrade",), "PIPX_HOME"),
)

# PEP 440, without epochs: release, pre, post, and dev segments
VERSION: Final = re.compile(
    r"v?(?P<release>\d+(?:\.\d+)*)"
    r"(?:[-_.]?(?P<pre>a|alpha|b|beta|c|rc|pre|preview)[-_.]?(?P<pre_n>\d*))?"
    r"(?:-(?P<post_implicit>\d+)|[-_.]?(?:post|rev|r)[-_.]?(?P<post_n>\d*))?"
    r"(?:[-_.]?dev[-_.]?(?P<dev_n>\d*))?"
    r"(?:\+[a-z0-9.]*)?",
    re.IGNORECASE,
)
PRE_RANKS: Final = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2}

type Fetch = Callable[[str], bytes]
type Which = Callable[[str], str | None]
type Segment = tuple[int, ...]
type VersionKey = tuple[Segment, Segment, Segment, Segment]


class UpdateError(AppError):
    """The newest release could not be found."""


def release_key(text: str) -> VersionKey | None:
    """Return a sort key for a PEP 440 version, or None if it is not one.

    ``1.2.0.dev1 < 1.2.0rc1 < 1.2.0 < 1.2.0.post1``; trailing zeros do not
    count, so ``1.2`` equals ``1.2.0``.
    """
    match = VERSION.fullmatch(text.strip())
    if match is None:
        return None
    release = [int(part) for part in match["release"].split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    pre_name, post, dev = match["pre"], _post(match), match["dev_n"]
    if pre_name is not None:
        rank = PRE_RANKS.get(pre_name.lower(), 2)  # pre and preview mean rc
        pre: tuple[int, ...] = (1, rank, int(match["pre_n"] or 0))
    elif dev is not None and post is None:
        pre = (0,)  # 1.0.dev1 sorts before 1.0a1
    else:
        pre = (2,)
    post_key = (1, post) if post is not None else (0,)
    dev_key = (0, int(dev or 0)) if dev is not None else (1,)
    return (tuple(release), pre, post_key, dev_key)


def _post(match: re.Match[str]) -> int | None:
    if match["post_implicit"] is not None:
        return int(match["post_implicit"])
    if match["post_n"] is not None:
        return int(match["post_n"] or 0)
    return None


def fetch_url(url: str) -> bytes:
    """Return the body of ``url`` (HTTPS GET, bounded size and time).

    Raises:
        OSError: If the request fails.
        http.client.HTTPException: If the server answers malformed HTTP.
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
        return is_newer(self.latest, self.current)


def is_newer(candidate: str, current: str) -> bool:
    """Return True if ``candidate`` is a later version than ``current``."""
    after, before = release_key(candidate), release_key(current)
    return after is not None and (before is None or after > before)


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
        except (OSError, ValueError, http.client.HTTPException) as err:
            msg = f"could not check for updates: {err}"
            raise UpdateError(msg) from err
        info = data.get("info") if isinstance(data, dict) else None
        latest = info.get("version") if isinstance(info, dict) else None
        if not isinstance(latest, str) or release_key(latest) is None:
            msg = "could not check for updates: PyPI sent no version"
            raise UpdateError(msg)
        return Release(latest, current)


def _has_pip() -> bool:
    """Return True if pip can run in this interpreter's environment."""
    return importlib.util.find_spec("pip") is not None


@dataclass(frozen=True, slots=True)
class Installation:
    """How this copy of SkillSinc was installed, and how to upgrade it."""

    kind: str
    """``uv``, ``pipx``, ``pip``, or ``manual``."""
    command: tuple[str, ...] = ()
    """The upgrade command; empty when SkillSinc cannot upgrade itself."""
    reason: str = ""
    """Why not, for ``manual``."""
    environment: tuple[tuple[str, str], ...] = ()
    """Variables that point the command at this installation."""

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
        has_pip: Callable[[], bool] = _has_pip,
    ) -> Installation:
        """Return how the running interpreter's environment was installed."""
        prefix = prefix or Path(sys.prefix)
        executable = executable or Path(sys.executable)
        base_prefix = base_prefix or Path(sys.base_prefix)
        if cls._editable():
            return cls("manual", reason="SkillSinc runs from a working copy")
        for marker, tool, verb, variable in TOOL_MANAGERS:
            if (prefix / marker).is_file():
                return cls._tool(prefix, tool, verb, variable, which)
        if prefix == base_prefix:
            return cls(
                "manual",
                reason="SkillSinc is installed in a system Python; upgrade it with"
                " the tool that installed it",
            )
        if not has_pip():
            return cls(
                "manual",
                reason="this environment has no pip; upgrade it with the tool that"
                " installed SkillSinc, such as uv pip install --upgrade"
                f' "{TRAY_PACKAGE}"',
            )
        return cls(
            "pip",
            (str(executable), "-m", "pip", "install", "--upgrade", TRAY_PACKAGE),
        )

    @classmethod
    def _tool(
        cls,
        prefix: Path,
        tool: str,
        verb: tuple[str, ...],
        variable: str,
        which: Which,
    ) -> Installation:
        """Return the tool's upgrade, aimed at the folder holding ``prefix``."""
        manual = f"run {tool} {' '.join(verb)} {PACKAGE}"
        # uv: <UV_TOOL_DIR>/skillsinc; pipx: <PIPX_HOME>/venvs/skillsinc
        home = prefix.parent if tool == "uv" else prefix.parent.parent
        misplaced = tool == "pipx" and prefix.parent.name != "venvs"
        if prefix.name != PACKAGE or misplaced:
            reason = f"{prefix} is not where {tool} keeps SkillSinc; {manual}"
            return cls("manual", reason=reason)
        found = which(tool)
        if found is None:
            return cls("manual", reason=f"{tool} is not on PATH; {manual}")
        return cls(tool, (found, *verb, PACKAGE), environment=((variable, str(home)),))

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


def base_interpreter(executable: str | None = None, base: str | None = None) -> str:
    """Return a Python outside this environment, so the upgrade can replace it."""
    candidate = base if base is not None else getattr(sys, "_base_executable", "")
    if candidate and Path(candidate).is_file():
        return str(candidate)
    return executable or sys.executable


# The helper runs with -I on the base interpreter: standard library only
HELPER: Final = r"""
import json, os, stat, subprocess, sys, time

spec = json.loads(sys.argv[1])
windows = os.name == "nt"


def alive(pid):
    if windows:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)
        ]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def record(code, note):
    path = spec["result"]
    staging = "%s.%d.tmp" % (path, os.getpid())
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    data = {"version": spec["version"], "returncode": code, "note": note,
            "at": time.time(), "reported": False}
    try:
        descriptor = os.open(staging, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(staging, path)
    except OSError:
        try:
            os.unlink(staging)
        except OSError:
            pass


def open_log():
    flags = (os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    try:
        descriptor = os.open(spec["log"], flags, 0o600)
    except OSError:
        return None
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        return None
    if getattr(os, "O_NONBLOCK", 0):
        os.set_blocking(descriptor, True)
    return descriptor


deadline = time.monotonic() + spec["wait"]
while alive(spec["pid"]):
    if time.monotonic() > deadline:
        record(None, "the tray did not exit")
        sys.exit(1)
    time.sleep(0.2)
log = open_log()
output = log if log is not None else subprocess.DEVNULL
environment = dict(os.environ)
environment.update(spec["environment"])
hidden = {"creationflags": spec["no_window"]} if windows else {}
try:
    code = subprocess.run(
        spec["upgrade"], stdin=subprocess.DEVNULL, stdout=output, stderr=output,
        env=environment, check=False, **hidden
    ).returncode
    note = ""
except OSError as err:
    code, note = None, str(err)
if log is not None:
    os.close(log)
record(code, note)
if windows:
    detached = {"creationflags": spec["detached"]}
else:
    detached = {"start_new_session": True}
try:
    subprocess.Popen(spec["relaunch"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **detached)
except OSError:
    sys.exit(1)
"""


@dataclass(frozen=True, slots=True)
class UpdateHelper:
    """The command that upgrades this installation after the tray exits."""

    program: str
    arguments: tuple[str, ...]

    @classmethod
    def plan(  # noqa: PLR0913 - everything the helper needs, injected
        cls,
        installation: Installation,
        relaunch: Sequence[str],
        folder: Path,
        version: str,
        *,
        pid: int | None = None,
        python: str | None = None,
    ) -> UpdateHelper:
        """Return the helper for ``installation``, upgrading to ``version``."""
        spec = {
            "pid": os.getpid() if pid is None else pid,
            "wait": EXIT_WAIT_SECONDS,
            "upgrade": list(installation.command),
            "environment": dict(installation.environment),
            "relaunch": list(relaunch),
            "log": str(folder / LOG_FILE),
            "result": str(folder / RESULT_FILE),
            "version": version,
            "no_window": CREATE_NO_WINDOW,
            "detached": DETACHED_FLAGS,
        }
        program = python or base_interpreter()
        return cls(program, ("-I", "-c", HELPER, json.dumps(spec)))


@dataclass(frozen=True, slots=True)
class UpdateResult:
    """What the helper recorded about the last upgrade it ran."""

    version: str
    returncode: int | None
    note: str
    reported: bool

    @property
    def succeeded(self) -> bool:
        """Return True if the installer exited cleanly."""
        return self.returncode == 0

    @classmethod
    def read(cls, folder: Path) -> UpdateResult | None:
        """Return the recorded result, or None if there is none or it is unreadable."""
        path = folder / RESULT_FILE
        try:
            if path.is_symlink() or not path.is_file():
                return None
            data: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("version"), str):
            return None
        code = data.get("returncode")
        return cls(
            str(data["version"]),
            code if isinstance(code, int) and not isinstance(code, bool) else None,
            str(data.get("note") or ""),
            data.get("reported") is True,
        )

    @staticmethod
    def mark_reported(folder: Path) -> None:
        """Record that the failure was reported, keeping the rest."""
        path = folder / RESULT_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["reported"] = True
            write_atomically(path, json.dumps(data))
        except (OSError, ValueError, TypeError):
            return

    @staticmethod
    def clear(folder: Path) -> None:
        """Forget the recorded result."""
        (folder / RESULT_FILE).unlink(missing_ok=True)
