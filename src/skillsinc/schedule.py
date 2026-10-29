"""Register ``skillsinc sync`` with the operating system's scheduler.

Each backend writes one definition SkillSinc owns by name (a Task Scheduler
task, a launchd agent, a pair of systemd user units, or one marked crontab
line), so installing again replaces it and removing deletes only it.
"""

from __future__ import annotations

import plistlib
import re
import shlex
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol, assert_never

from skillsinc.exceptions import AppError
from skillsinc.paths import Environment

MINIMUM_INTERVAL: Final = timedelta(minutes=1)
MAXIMUM_INTERVAL: Final = timedelta(days=31)  # Task Scheduler's repetition limit
DEFAULT_INTERVAL: Final = timedelta(hours=1)

# Variables that change which configuration, manifests, or harness folders a
# run uses; schedulers start jobs without the shell's environment.
VARIABLES: Final = (
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "CLAUDE_CONFIG_DIR",
    "COPILOT_HOME",
    "CODEX_HOME",
)

_UNITS: Final = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_SUFFIXED: Final = re.compile(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")
_CLOCK: Final = re.compile(r"(\d+):([0-5]\d)(?::([0-5]\d))?")


class ScheduleError(AppError):
    """The scheduler refused a change or cannot be used here."""


class Scheduler(StrEnum):
    """Supported operating-system schedulers."""

    TASK_SCHEDULER = "task-scheduler"
    LAUNCHD = "launchd"
    SYSTEMD = "systemd"
    CRON = "cron"


def parse_interval(text: str) -> timedelta:
    """Parse ``90s``, ``5m``, ``1h30m``, ``1d``, or ``H:MM[:SS]``.

    Raises:
        ValueError: If the text is not an interval from one minute to 31 days.
    """
    raw = text.strip().lower()
    clock = _CLOCK.fullmatch(raw)
    suffixed = _SUFFIXED.fullmatch(raw)
    if clock:
        hours, minutes, seconds = (int(part or 0) for part in clock.groups())
        interval = timedelta(hours=hours, minutes=minutes, seconds=seconds)
    elif raw and suffixed:
        parts = zip(suffixed.groups(), _UNITS.values(), strict=True)
        interval = timedelta(seconds=sum(int(n or 0) * unit for n, unit in parts))
    else:
        msg = f"invalid interval {text!r}; use for example 5m, 1h, 1h30m, or 00:05:00"
        raise ValueError(msg)
    if not MINIMUM_INTERVAL <= interval <= MAXIMUM_INTERVAL:
        msg = f"interval {text!r} must be from 1m to 31d"
        raise ValueError(msg)
    return interval


def format_interval(interval: timedelta) -> str:
    """Return ``interval`` in the short form ``parse_interval`` reads."""
    remaining = int(interval.total_seconds())
    text = ""
    for suffix, unit in _UNITS.items():
        count, remaining = divmod(remaining, unit)
        if count:
            text += f"{count}{suffix}"
    return text or "0s"


@dataclass(frozen=True, slots=True)
class Job:
    """The scheduled command: what runs, how often, and with which variables."""

    argv: tuple[str, ...]
    interval: timedelta
    variables: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Status:
    """What the scheduler holds for SkillSinc."""

    installed: bool
    details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Completed:
    """A finished scheduler command."""

    code: int
    stdout: str = ""
    stderr: str = ""


class Runner:
    """Run scheduler commands; tests substitute a recording fake."""

    def run(self, argv: Sequence[str], stdin: str | None = None) -> Completed:
        """Run ``argv`` to completion; a missing program exits 127."""
        try:
            completed = subprocess.run(  # noqa: S603 - argv is built by a backend
                list(argv),
                input=stdin,
                capture_output=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except FileNotFoundError as err:
            return Completed(127, "", str(err))
        return Completed(completed.returncode, completed.stdout, completed.stderr)

    def available(self, program: str) -> bool:
        """Return True if ``program`` is on ``PATH``."""
        return shutil.which(program) is not None


class Backend(Protocol):
    """One operating-system scheduler."""

    title: str
    location: str
    writes_log: bool

    def definition(self, job: Job) -> str:
        """Return the text that ``install`` registers."""

    def install(self, job: Job) -> None:
        """Register ``job``, replacing an earlier registration."""

    def remove(self) -> bool:
        """Unregister the job; return False if none was registered."""

    def status(self) -> Status:
        """Describe the registered job."""


def _check(completed: Completed, action: str) -> Completed:
    """Return ``completed``, or raise with the scheduler's own message."""
    if completed.code != 0:
        output = (completed.stderr or completed.stdout).strip()
        msg = f"{action} failed (exit {completed.code}): {output or 'no output'}"
        raise ScheduleError(msg)
    return completed


def disposable(executable: Path) -> bool:
    """Return True if ``executable`` lives in a uv cache environment.

    ``uvx`` and ``uv run --with`` run from such environments, which uv may
    delete at any time, so a scheduled job must not point into one.
    """
    parts = {part.lower() for part in executable.parts}
    return bool(parts & {"archive-v0", "builds-v0", "environments-v2"})


def command_line(argv: Sequence[str], platform: str) -> str:
    """Return ``argv`` as one command line in ``platform``'s quoting."""
    if platform == "win32":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


class TaskScheduler:
    """Windows Task Scheduler: a per-user task run at logon and every interval."""

    title = "Windows Task Scheduler"
    writes_log = True
    TASK: Final = "SkillSinc sync"
    NAMESPACE: Final = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    _NOT_RUN: Final = {"267011": "not run yet", "267009": "running"}
    _MISSING: Final = 3
    _STATUS_SCRIPT: Final = """\
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$task = Get-ScheduledTask -TaskName '@TASK@' -ErrorAction SilentlyContinue
if (-not $task) { exit 3 }
$info = $task | Get-ScheduledTaskInfo
$repeat = $task.Triggers | Where-Object { $_.Repetition.Interval } |
    Select-Object -First 1
"Every=$($repeat.Repetition.Interval)"
"Runs=$($task.Actions[0].Execute) $($task.Actions[0].Arguments)"
'Last run={0:s}' -f $info.LastRunTime
"Last result=$($info.LastTaskResult)"
'Next run={0:s}' -f $info.NextRunTime
"""

    def __init__(
        self,
        runner: Runner,
        environment: Environment,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC).astimezone(),
    ) -> None:
        self._runner = runner
        self._environment = environment
        self._clock = clock
        self.location = f'task "{self.TASK}"'

    def definition(self, job: Job) -> str:
        """Return the task XML: logon and repeating triggers, one instance."""
        user = self._user()
        task = ET.Element("Task", version="1.2", xmlns=self.NAMESPACE)
        info = ET.SubElement(task, "RegistrationInfo")
        ET.SubElement(info, "Description").text = (
            f"Runs skillsinc sync every {format_interval(job.interval)}."
            " Created by `skillsinc schedule install`."
        )
        triggers = ET.SubElement(task, "Triggers")
        logon = ET.SubElement(triggers, "LogonTrigger")
        ET.SubElement(logon, "Enabled").text = "true"
        ET.SubElement(logon, "UserId").text = user
        timer = ET.SubElement(triggers, "TimeTrigger")
        repetition = ET.SubElement(timer, "Repetition")
        ET.SubElement(repetition, "Interval").text = self._duration(job.interval)
        ET.SubElement(repetition, "StopAtDurationEnd").text = "false"
        ET.SubElement(timer, "StartBoundary").text = self._clock().isoformat(
            timespec="seconds"
        )
        ET.SubElement(timer, "Enabled").text = "true"
        principal = ET.SubElement(
            ET.SubElement(task, "Principals"), "Principal", id="Author"
        )
        ET.SubElement(principal, "UserId").text = user
        ET.SubElement(principal, "LogonType").text = "InteractiveToken"
        ET.SubElement(principal, "RunLevel").text = "LeastPrivilege"
        settings = ET.SubElement(task, "Settings")
        for name, value in (
            ("MultipleInstancesPolicy", "IgnoreNew"),
            ("DisallowStartIfOnBatteries", "false"),
            ("StopIfGoingOnBatteries", "false"),
            ("StartWhenAvailable", "true"),
            ("ExecutionTimeLimit", "PT30M"),
            ("Enabled", "true"),
        ):
            ET.SubElement(settings, name).text = value
        execute = ET.SubElement(
            ET.SubElement(task, "Actions", Context="Author"), "Exec"
        )
        ET.SubElement(execute, "Command").text = job.argv[0]
        ET.SubElement(execute, "Arguments").text = subprocess.list2cmdline(job.argv[1:])
        ET.indent(task)
        text = ET.tostring(task, encoding="unicode")
        return f'<?xml version="1.0" encoding="UTF-16"?>\n{text}\n'

    def install(self, job: Job) -> None:
        """Create or replace the task from its XML definition."""
        text = self.definition(job)
        with tempfile.TemporaryDirectory(prefix="skillsinc-") as directory:
            path = Path(directory) / "task.xml"
            path.write_text(text, encoding="utf-16")
            _check(
                self._schtasks("/Create", "/TN", self.TASK, "/XML", str(path), "/F"),
                "Registering the task",
            )

    def remove(self) -> bool:
        """Delete the task if it exists."""
        if self._schtasks("/Query", "/TN", self.TASK).code != 0:
            return False
        _check(self._schtasks("/Delete", "/TN", self.TASK, "/F"), "Deleting the task")
        return True

    def status(self) -> Status:
        """Read the task's interval, command, and last result."""
        shown = self._runner.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                self._STATUS_SCRIPT.replace("@TASK@", self.TASK),
            ]
        )
        if shown.code == self._MISSING:
            return Status(installed=False)
        _check(shown, "Reading the task")
        values = dict(
            line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line
        )
        details = tuple(
            (label, self._NOT_RUN.get(values[label], values[label]))
            for label in ("Every", "Runs", "Last run", "Last result", "Next run")
            if label in values
        )
        return Status(installed=True, details=details)

    def _schtasks(self, *argv: str) -> Completed:
        """Run ``schtasks.exe`` with ``argv``."""
        return self._runner.run(["schtasks.exe", *argv])

    def _user(self) -> str:
        r"""Return ``DOMAIN\user`` for the logon trigger and principal."""
        user = self._environment.variables.get("USERNAME", "")
        if not user:
            msg = "USERNAME is not set; cannot name the task's user"
            raise ScheduleError(msg)
        domain = self._environment.variables.get("USERDOMAIN", "")
        return f"{domain}\\{user}" if domain else user

    @staticmethod
    def _duration(interval: timedelta) -> str:
        """Return ``interval`` as an ISO 8601 duration such as ``PT1H30M``."""
        days, remaining = divmod(int(interval.total_seconds()), 86400)
        hours, remaining = divmod(remaining, 3600)
        minutes, seconds = divmod(remaining, 60)
        time = "".join(
            f"{count}{unit}"
            for count, unit in ((hours, "H"), (minutes, "M"), (seconds, "S"))
            if count
        )
        return "P" + (f"{days}D" if days else "") + (f"T{time}" if time else "")


class Launchd:
    """macOS launchd: a per-user agent loaded at login and every interval."""

    title = "launchd"
    writes_log = True
    LABEL: Final = "io.github.mikejhill.skillsinc"
    _EXIT: Final = re.compile(r"last exit code = (.+)")

    def __init__(self, runner: Runner, environment: Environment) -> None:
        self._runner = runner
        self._path = (
            environment.home / "Library" / "LaunchAgents" / f"{self.LABEL}.plist"
        )
        self.location = str(self._path)

    def definition(self, job: Job) -> str:
        """Return the agent property list."""
        agent: dict[str, object] = {
            "Label": self.LABEL,
            "ProgramArguments": list(job.argv),
            "StartInterval": int(job.interval.total_seconds()),
            "RunAtLoad": True,
            "ProcessType": "Background",
        }
        if job.variables:
            agent["EnvironmentVariables"] = dict(job.variables)
        return plistlib.dumps(agent).decode("utf-8")

    def install(self, job: Job) -> None:
        """Write the agent and (re)load it into the login session."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(self.definition(job), encoding="utf-8")
        domain = self._domain()
        self._runner.run(["launchctl", "bootout", f"{domain}/{self.LABEL}"])
        _check(
            self._runner.run(["launchctl", "bootstrap", domain, str(self._path)]),
            "Loading the agent",
        )

    def remove(self) -> bool:
        """Unload and delete the agent if it exists."""
        unloaded = (
            self._runner.run(
                ["launchctl", "bootout", f"{self._domain()}/{self.LABEL}"]
            ).code
            == 0
        )
        existed = self._path.is_file()
        self._path.unlink(missing_ok=True)
        return unloaded or existed

    def status(self) -> Status:
        """Read the agent's interval and command, and launchd's last exit code."""
        if not self._path.is_file():
            return Status(installed=False)
        agent = plistlib.loads(self._path.read_bytes())
        seconds = int(agent.get("StartInterval", 0))
        details = [
            ("Every", format_interval(timedelta(seconds=seconds))),
            ("Runs", shlex.join(str(arg) for arg in agent.get("ProgramArguments", []))),
        ]
        printed = self._runner.run(
            ["launchctl", "print", f"{self._domain()}/{self.LABEL}"]
        )
        if printed.code != 0:
            details.append(("Loaded", "no; run `skillsinc schedule install` again"))
        else:
            found = self._EXIT.search(printed.stdout)
            details.append(("Last result", found.group(1) if found else "not run yet"))
        return Status(installed=True, details=tuple(details))

    def _domain(self) -> str:
        """Return the login session's launchd domain, ``gui/<uid>``."""
        uid = _check(self._runner.run(["id", "-u"]), "Reading the user id")
        return f"gui/{uid.stdout.strip()}"


class SystemdTimer:
    """Linux systemd: a user timer starting a oneshot ``sync`` service."""

    title = "systemd user timer"
    writes_log = False  # output goes to the journal
    SERVICE: Final = "skillsinc.service"
    TIMER: Final = "skillsinc.timer"

    def __init__(self, runner: Runner, environment: Environment) -> None:
        self._runner = runner
        self._directory = environment.config_home / "systemd" / "user"
        self.location = str(self._directory / self.TIMER)

    def definition(self, job: Job) -> str:
        """Return both unit files, each headed by its file name."""
        return (
            "".join(
                f"# {name}\n{text}\n" for name, text in self._units(job).items()
            ).rstrip("\n")
            + "\n"
        )

    def install(self, job: Job) -> None:
        """Write the units, then enable and restart the timer."""
        self._directory.mkdir(parents=True, exist_ok=True)
        for name, text in self._units(job).items():
            (self._directory / name).write_text(text, encoding="utf-8")
        for argv, action in (
            (("daemon-reload",), "Reloading systemd"),
            (("enable", self.TIMER), "Enabling the timer"),
            (("restart", self.TIMER), "Starting the timer"),
        ):
            _check(self._systemctl(*argv), action)

    def remove(self) -> bool:
        """Stop and delete both units if the timer exists."""
        timer = self._directory / self.TIMER
        if not timer.is_file():
            return False
        self._systemctl("disable", "--now", self.TIMER)
        timer.unlink()
        (self._directory / self.SERVICE).unlink(missing_ok=True)
        self._systemctl("daemon-reload")
        return True

    def status(self) -> Status:
        """Read the timer's interval and command, and the last run's result."""
        timer = self._directory / self.TIMER
        if not timer.is_file():
            return Status(installed=False)
        service = self._directory / self.SERVICE
        units = timer.read_text(encoding="utf-8")
        if service.is_file():
            units += service.read_text(encoding="utf-8")
        values = dict(re.findall(r"^(\w+)=(.*)$", units, flags=re.MULTILINE))
        state = self._systemctl("is-active", self.TIMER).stdout.strip() or "unknown"
        details = [
            ("Every", values.get("OnUnitActiveSec", "")),
            ("Runs", values.get("ExecStart", "")),
            ("Timer", state),
            ("Log", f"journalctl --user -u {self.SERVICE}"),
        ]
        shown = self._systemctl(
            "show", self.SERVICE, "--property=Result,ExecMainExitTimestamp"
        )
        last = dict(re.findall(r"^(\w+)=(.*)$", shown.stdout, flags=re.MULTILINE))
        if last.get("ExecMainExitTimestamp"):
            details += [
                ("Last run", last["ExecMainExitTimestamp"]),
                ("Last result", last.get("Result", "")),
            ]
        return Status(installed=True, details=tuple(details))

    def _units(self, job: Job) -> dict[str, str]:
        """Return the service and timer unit text by file name."""
        environment = "".join(
            f"Environment={self._quote(f'{key}={value}', variables=False)}\n"
            for key, value in sorted(job.variables.items())
        )
        command = " ".join(self._quote(arg) for arg in job.argv)
        seconds = int(job.interval.total_seconds())
        return {
            self.SERVICE: (
                "# Written by `skillsinc schedule install`; started by"
                f" {self.TIMER}.\n"
                "[Unit]\nDescription=Sync agent customizations with SkillSinc\n\n"
                f"[Service]\nType=oneshot\nExecStart={command}\n{environment}"
            ),
            self.TIMER: (
                "# Written by `skillsinc schedule install`.\n"
                "[Unit]\nDescription=Run SkillSinc sync every"
                f" {format_interval(job.interval)}\n\n"
                "[Timer]\nOnActiveSec=1min\n"
                f"OnUnitActiveSec={seconds}s\n\n"
                "[Install]\nWantedBy=timers.target\n"
            ),
        }

    def _systemctl(self, *argv: str) -> Completed:
        """Run ``systemctl --user`` with ``argv``."""
        return self._runner.run(["systemctl", "--user", *argv])

    @staticmethod
    def _quote(text: str, *, variables: bool = True) -> str:
        """Quote one word for a unit file, escaping specifiers.

        ``$`` is doubled only where systemd expands variables (``ExecStart``),
        not in ``Environment=`` assignments.
        """
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
        return '"' + (escaped.replace("$", "$$") if variables else escaped) + '"'


class Cron:
    """cron: one crontab line marked as SkillSinc's."""

    title = "cron"
    writes_log = True
    MARK: Final = "# skillsinc schedule"
    location = "crontab"

    def __init__(self, runner: Runner) -> None:
        self._runner = runner

    def definition(self, job: Job) -> str:
        """Return the crontab line."""
        variables = " ".join(
            f"{key}={shlex.quote(value)}"
            for key, value in sorted(job.variables.items())
        )
        command = f"{variables} {shlex.join(job.argv)}".strip()
        # cron ends the command at an unescaped %, even inside shell quotes
        command = command.replace("%", "\\%")
        return f"{self._expression(job.interval)} {command} {self.MARK}\n"

    def install(self, job: Job) -> None:
        """Replace SkillSinc's line, keeping every other line."""
        lines = [*self._others(), self.definition(job).rstrip("\n")]
        self._write(lines)

    def remove(self) -> bool:
        """Drop SkillSinc's line if present."""
        lines = self._lines()
        others = self._others(lines)
        if len(others) == len(lines):
            return False
        self._write(others)
        return True

    def status(self) -> Status:
        """Show SkillSinc's crontab line."""
        line = next((line for line in self._lines() if line.endswith(self.MARK)), "")
        if not line:
            return Status(installed=False)
        schedule, command = " ".join(line.split()[:5]), line.split(maxsplit=5)[5]
        command = command.removesuffix(self.MARK).strip()
        return Status(installed=True, details=(("When", schedule), ("Runs", command)))

    def _lines(self) -> list[str]:
        """Return the current crontab, or nothing if the user has none."""
        listed = self._runner.run(["crontab", "-l"])
        if listed.code != 0 and "no crontab" in listed.stderr.lower():
            return []
        return _check(listed, "Reading the crontab").stdout.splitlines()

    def _others(self, lines: list[str] | None = None) -> list[str]:
        """Return every crontab line except SkillSinc's."""
        lines = self._lines() if lines is None else lines
        return [line for line in lines if not line.endswith(self.MARK)]

    def _write(self, lines: list[str]) -> None:
        """Install ``lines`` as the user's crontab."""
        text = "".join(f"{line}\n" for line in lines)
        _check(self._runner.run(["crontab", "-"], stdin=text), "Writing the crontab")

    @staticmethod
    def _expression(interval: timedelta) -> str:
        """Return a cron schedule for an interval that divides an hour or a day.

        Raises:
            ScheduleError: If cron cannot express the interval.
        """
        seconds = int(interval.total_seconds())
        minutes, hours = seconds // 60, seconds // 3600
        if seconds % 60 == 0 and minutes < 60 and 60 % minutes == 0:  # noqa: PLR2004
            return "* * * * *" if minutes == 1 else f"*/{minutes} * * * *"
        if seconds % 3600 == 0 and hours <= 24 and 24 % hours == 0:  # noqa: PLR2004
            return "0 0 * * *" if hours == 24 else f"0 */{hours} * * *"  # noqa: PLR2004
        msg = (
            f"cron cannot run every {format_interval(interval)}; use whole minutes"
            " that divide an hour (such as 5m) or whole hours that divide a day"
        )
        raise ScheduleError(msg)


def detect(platform: str, runner: Runner) -> Scheduler:
    """Choose the scheduler for ``platform``.

    Raises:
        ScheduleError: If no supported scheduler is available.
    """
    if platform == "win32":
        return Scheduler.TASK_SCHEDULER
    if platform == "darwin":
        return Scheduler.LAUNCHD
    if (
        runner.available("systemctl")
        and runner.run(["systemctl", "--user", "show-environment"]).code == 0
    ):
        return Scheduler.SYSTEMD
    if runner.available("crontab"):
        return Scheduler.CRON
    msg = "found neither a systemd user session nor crontab; pass --scheduler"
    raise ScheduleError(msg)


def backend(scheduler: Scheduler, runner: Runner, environment: Environment) -> Backend:
    """Return the backend for ``scheduler``."""
    match scheduler:
        case Scheduler.TASK_SCHEDULER:
            return TaskScheduler(runner, environment)
        case Scheduler.LAUNCHD:
            return Launchd(runner, environment)
        case Scheduler.SYSTEMD:
            return SystemdTimer(runner, environment)
        case Scheduler.CRON:
            return Cron(runner)
        case _:
            return assert_never(scheduler)


def interpreter(executable: Path, platform: str) -> Path:
    """Return the Python that scheduled runs use.

    On Windows this is ``pythonw.exe`` beside ``executable`` when present, so
    each run opens no console window.
    """
    if platform == "win32" and executable.name.lower() == "python.exe":
        windowless = executable.with_name("pythonw.exe")
        if windowless.is_file():
            return windowless
    return executable
