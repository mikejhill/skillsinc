"""Test scheduler definitions, registration calls, and the schedule command."""

from __future__ import annotations

import io
import plistlib
import shlex
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import override

import pytest
from hypothesis import given
from hypothesis import strategies as st

from skillsinc.commands import ScheduleCommand
from skillsinc.paths import Environment
from skillsinc.schedule import (
    Completed,
    Cron,
    Job,
    Launchd,
    Runner,
    ScheduleError,
    Scheduler,
    SystemdTimer,
    TaskScheduler,
    backend,
    command_line,
    detect,
    format_interval,
    interpreter,
    parse_interval,
)
from skillsinc.settings import ScheduleSettings

NAMESPACE = {"t": TaskScheduler.NAMESPACE}
JOB = Job(
    ("/opt/py/bin/python", "-P", "-m", "skillsinc", "sync"),
    timedelta(minutes=5),
    {"XDG_STATE_HOME": "/srv/state"},
)


class FakeRunner(Runner):
    """Record scheduler commands and answer them from canned results."""

    def __init__(
        self,
        results: dict[tuple[str, ...], Completed] | None = None,
        programs: frozenset[str] = frozenset(),
    ) -> None:
        self.results = results or {}
        self.programs = programs
        self.calls: list[tuple[str, ...]] = []
        self.inputs: list[str | None] = []
        self.files: list[str] = []

    @override
    def run(self, argv: Sequence[str], stdin: str | None = None) -> Completed:
        """Return the result whose key is the longest prefix of ``argv``."""
        call = tuple(argv)
        self.calls.append(call)
        self.inputs.append(stdin)
        if "/XML" in call:
            # The definition file only exists during the call
            path = Path(call[call.index("/XML") + 1])
            self.files.append(path.read_text(encoding="utf-16"))
        matches = [key for key in self.results if call[: len(key)] == key]
        return self.results[max(matches, key=len)] if matches else Completed(0)

    @override
    def available(self, program: str) -> bool:
        """Return True for the programs this fake pretends are installed."""
        return program in self.programs


def settings(action: str = "install", **overrides: object) -> ScheduleSettings:
    """Build schedule settings with explicit overrides."""
    base = ScheduleSettings(
        action="install",
        scheduler="cron",
        interval=timedelta(minutes=5),
        config_path=None,
        dry_run=False,
        log_level="WARNING",
    )
    return replace(base, action=action, **overrides)


class TestInterval:
    """Intervals parse from short and clock forms within the scheduler limits."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("5m", timedelta(minutes=5)),
            ("90s", timedelta(seconds=90)),
            ("1h30m", timedelta(hours=1, minutes=30)),
            ("1d", timedelta(days=1)),
            ("2H", timedelta(hours=2)),
            ("00:05:00", timedelta(minutes=5)),
            ("1:30", timedelta(hours=1, minutes=30)),
        ],
    )
    def test_valid_forms(self, text: str, expected: timedelta) -> None:
        """Each accepted form yields the same duration a person means."""
        result = parse_interval(text)

        assert result == expected, f"Expected {expected} for {text!r}, got {result}"

    @pytest.mark.parametrize("text", ["", "5", "5x", "m", "1:5", "00:00:30", "32d"])
    def test_invalid_forms_are_refused(self, text: str) -> None:
        """Bare numbers, unknown units, and out-of-range values are errors."""
        with pytest.raises(ValueError, match="interval"):
            parse_interval(text)

    @given(st.integers(min_value=60, max_value=31 * 86400))
    def test_format_round_trips(self, seconds: int) -> None:
        """Formatting an interval and parsing it back returns the same value."""
        interval = timedelta(seconds=seconds)

        result = parse_interval(format_interval(interval))

        assert result == interval, f"Expected {interval}, got {result}"

    def test_zero_formats_as_seconds(self) -> None:
        """An empty interval still prints a unit."""
        assert format_interval(timedelta()) == "0s", "Expected 0s"


class TestTaskScheduler:
    """Windows tasks are registered from XML and inspected with PowerShell."""

    @pytest.fixture
    def scheduler(self, environment: Environment) -> TaskScheduler:
        r"""Return a backend for user CORP\mike at a fixed time."""
        windows = replace(
            environment,
            variables={"USERNAME": "mike", "USERDOMAIN": "CORP"},
            platform="win32",
        )
        return TaskScheduler(
            FakeRunner(), windows, clock=lambda: datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
        )

    def test_definition_repeats_and_runs_command(
        self, scheduler: TaskScheduler
    ) -> None:
        """The XML repeats every interval, runs once at a time, and quotes args."""
        job = replace(JOB, argv=("C:\\py\\pythonw.exe", "sync", "--config", "C:\\a b"))

        root = ET.fromstring(scheduler.definition(job).split("\n", 1)[1])  # noqa: S314 - our own XML

        found = {
            path: root.findtext(path, "", NAMESPACE)
            for path in (
                ".//t:TimeTrigger/t:Repetition/t:Interval",
                ".//t:TimeTrigger/t:StartBoundary",
                ".//t:LogonTrigger/t:UserId",
                ".//t:Principal/t:LogonType",
                ".//t:MultipleInstancesPolicy",
                ".//t:Exec/t:Command",
                ".//t:Exec/t:Arguments",
            )
        }
        assert list(found.values()) == [
            "PT5M",
            "2026-01-02T03:04:00+00:00",
            "CORP\\mike",
            "InteractiveToken",
            "IgnoreNew",
            "C:\\py\\pythonw.exe",
            'sync --config "C:\\a b"',
        ], f"Expected the task definition, got {found}"

    @pytest.mark.parametrize(
        ("interval", "expected"),
        [
            (timedelta(days=1, hours=2), "P1DT2H"),
            (timedelta(days=2), "P2D"),
            (timedelta(minutes=1, seconds=30), "PT1M30S"),
        ],
    )
    def test_durations(
        self, scheduler: TaskScheduler, interval: timedelta, expected: str
    ) -> None:
        """Intervals become ISO 8601 durations."""
        text = scheduler.definition(replace(JOB, interval=interval))

        assert f"<Interval>{expected}</Interval>" in text, (
            f"Expected {expected} in {text}"
        )

    def test_user_without_domain(self, environment: Environment) -> None:
        """A machine without USERDOMAIN names the bare user."""
        local = replace(environment, variables={"USERNAME": "mike"})

        text = TaskScheduler(FakeRunner(), local).definition(JOB)

        assert "<UserId>mike</UserId>" in text, f"Expected bare user in {text}"

    def test_missing_user_is_an_error(self, environment: Environment) -> None:
        """Without USERNAME the task cannot name its user."""
        with pytest.raises(ScheduleError, match="USERNAME"):
            TaskScheduler(FakeRunner(), environment).definition(JOB)

    def test_install_registers_utf16_xml_with_force(
        self, scheduler: TaskScheduler
    ) -> None:
        """Install hands schtasks a UTF-16 file and replaces an existing task."""
        scheduler.install(JOB)

        runner = scheduler._runner
        assert isinstance(runner, FakeRunner), "Expected the fake runner"
        call = runner.calls[0]
        assert (call[:4], call[-1]) == (
            ("schtasks.exe", "/Create", "/TN", "SkillSinc sync"),
            "/F",
        ), f"Expected a forced create, got {call}"
        assert runner.files[0].startswith('<?xml version="1.0" encoding="UTF-16"?>'), (
            f"Expected the XML definition, got {runner.files[0][:60]!r}"
        )

    def test_install_failure_reports_schtasks_message(
        self, environment: Environment
    ) -> None:
        """A refused registration raises with schtasks' own error."""
        windows = replace(environment, variables={"USERNAME": "mike"})
        runner = FakeRunner({("schtasks.exe",): Completed(1, "", "Access is denied.")})

        with pytest.raises(ScheduleError, match=r"exit 1\): Access is denied"):
            TaskScheduler(runner, windows).install(JOB)

    def test_remove_deletes_existing_task(self, environment: Environment) -> None:
        """Remove deletes the task only after finding it."""
        runner = FakeRunner()

        removed = TaskScheduler(runner, environment).remove()

        assert (removed, [call[1] for call in runner.calls]) == (
            True,
            ["/Query", "/Delete"],
        ), f"Expected query then delete, got {removed}, {runner.calls}"

    def test_remove_missing_task(self, environment: Environment) -> None:
        """Removing a missing task changes nothing."""
        runner = FakeRunner({("schtasks.exe", "/Query"): Completed(1)})

        removed = TaskScheduler(runner, environment).remove()

        assert (removed, len(runner.calls)) == (False, 1), (
            f"Expected no delete, got {removed}, {runner.calls}"
        )

    def test_status_reads_powershell_fields(self, environment: Environment) -> None:
        """Status shows the interval, command, and a translated last result."""
        output = (
            "Every=PT5M\nRuns=C:\\py\\pythonw.exe -m skillsinc sync\n"
            "Last run=2026-01-02T03:04:00\nLast result=267011\nNext run=x\n"
        )
        runner = FakeRunner({("powershell.exe",): Completed(0, output)})

        status = TaskScheduler(runner, environment).status()

        assert status.installed, "Expected an installed task"
        assert dict(status.details) == {
            "Every": "PT5M",
            "Runs": "C:\\py\\pythonw.exe -m skillsinc sync",
            "Last run": "2026-01-02T03:04:00",
            "Last result": "not run yet",
            "Next run": "x",
        }, f"Expected parsed details, got {status.details}"

    def test_status_missing_task(self, environment: Environment) -> None:
        """Exit 3 from the status script means no task."""
        runner = FakeRunner({("powershell.exe",): Completed(3)})

        status = TaskScheduler(runner, environment).status()

        assert status.installed is False, f"Expected no task, got {status}"

    def test_status_failure_raises(self, environment: Environment) -> None:
        """Any other PowerShell failure is reported."""
        runner = FakeRunner({("powershell.exe",): Completed(1, "", "boom")})

        with pytest.raises(ScheduleError, match="Reading the task"):
            TaskScheduler(runner, environment).status()


class TestLaunchd:
    """macOS agents are written to LaunchAgents and loaded into gui/<uid>."""

    @staticmethod
    def runner(**results: Completed) -> FakeRunner:
        """Return a fake that reports uid 501 plus ``results`` by launchctl verb."""
        canned: dict[tuple[str, ...], Completed] = {("id", "-u"): Completed(0, "501\n")}
        canned |= {("launchctl", verb): result for verb, result in results.items()}
        return FakeRunner(canned)

    def test_definition_sets_interval_and_variables(
        self, environment: Environment
    ) -> None:
        """The plist runs the argv every interval with the given variables."""
        agent = plistlib.loads(
            Launchd(self.runner(), environment).definition(JOB).encode()
        )

        result = (
            agent["ProgramArguments"],
            agent["StartInterval"],
            agent["EnvironmentVariables"],
            agent["RunAtLoad"],
        )
        assert result == (list(JOB.argv), 300, dict(JOB.variables), True), (
            f"Expected the agent definition, got {result}"
        )

    def test_definition_without_variables(self, environment: Environment) -> None:
        """No variables means no EnvironmentVariables key."""
        text = Launchd(self.runner(), environment).definition(
            replace(JOB, variables={})
        )

        assert "EnvironmentVariables" not in text, f"Expected no variables in {text}"

    def test_install_writes_then_reloads(self, environment: Environment) -> None:
        """Install writes the plist, unloads any old copy, then bootstraps."""
        runner = self.runner(bootout=Completed(5))
        launchd = Launchd(runner, environment)

        launchd.install(JOB)

        path = Path(launchd.location)
        assert path.is_file(), f"Expected {path} to be written"
        assert [call for call in runner.calls if call[0] == "launchctl"] == [
            ("launchctl", "bootout", f"gui/501/{Launchd.LABEL}"),
            ("launchctl", "bootstrap", "gui/501", str(path)),
        ], f"Expected bootout then bootstrap, got {runner.calls}"

    def test_install_failure_raises(self, environment: Environment) -> None:
        """A refused bootstrap is reported."""
        runner = self.runner(bootstrap=Completed(5, "", "Input/output error"))

        with pytest.raises(ScheduleError, match="Loading the agent"):
            Launchd(runner, environment).install(JOB)

    def test_unknown_uid_raises(self, environment: Environment) -> None:
        """Without a uid there is no domain to load into."""
        runner = FakeRunner({("id",): Completed(1, "", "no id")})

        with pytest.raises(ScheduleError, match="user id"):
            Launchd(runner, environment).install(JOB)

    def test_remove_unloads_and_deletes(self, environment: Environment) -> None:
        """Remove unloads the agent and deletes its file."""
        launchd = Launchd(self.runner(), environment)
        launchd.install(JOB)

        removed = launchd.remove()

        assert (removed, Path(launchd.location).exists()) == (True, False), (
            f"Expected the agent removed, got {removed}"
        )

    def test_remove_missing_agent(self, environment: Environment) -> None:
        """Nothing loaded and no file means nothing removed."""
        removed = Launchd(self.runner(bootout=Completed(3)), environment).remove()

        assert removed is False, "Expected nothing to remove"

    def test_status_shows_last_exit_code(self, environment: Environment) -> None:
        """Status reads the interval from the plist and the exit from launchctl."""
        printed = Completed(0, "\tstate = not running\n\tlast exit code = 0\n")
        launchd = Launchd(self.runner(print=printed), environment)
        launchd.install(JOB)

        status = launchd.status()

        assert dict(status.details) == {
            "Every": "5m",
            "Runs": "/opt/py/bin/python -P -m skillsinc sync",
            "Last result": "0",
        }, f"Expected agent details, got {status.details}"

    def test_status_before_first_run(self, environment: Environment) -> None:
        """A loaded agent without an exit code has not run yet."""
        launchd = Launchd(
            self.runner(print=Completed(0, "state = waiting")), environment
        )
        launchd.install(JOB)

        status = launchd.status()

        assert ("Last result", "not run yet") in status.details, (
            f"Expected not run yet, got {status.details}"
        )

    def test_status_of_unloaded_agent(self, environment: Environment) -> None:
        """An agent file that launchd does not know asks for a reinstall."""
        launchd = Launchd(self.runner(print=Completed(113)), environment)
        launchd.install(JOB)

        status = launchd.status()

        assert dict(status.details)["Loaded"].startswith("no"), (
            f"Expected an unloaded agent, got {status.details}"
        )

    def test_status_without_agent(self, environment: Environment) -> None:
        """No plist means not installed."""
        status = Launchd(self.runner(), environment).status()

        assert status.installed is False, f"Expected no agent, got {status}"


class TestSystemdTimer:
    """Linux user units are written under the XDG config home and enabled."""

    def test_units_quote_command_and_variables(self, environment: Environment) -> None:
        r"""Arguments and variables are quoted with %, $, quotes, and \ escaped."""
        job = replace(
            JOB,
            argv=("/opt/py", "sync", "--config", '/a b/100%$x"\\.json'),
            variables={"CODEX_HOME": "/c d"},
        )

        text = SystemdTimer(FakeRunner(), environment).definition(job)

        assert (
            'ExecStart="/opt/py" "sync" "--config" "/a b/100%%$$x\\"\\\\.json"' in text
        ), f"Expected an escaped ExecStart in {text}"
        assert 'Environment="CODEX_HOME=/c d"' in text, f"Expected variables in {text}"
        assert "OnUnitActiveSec=300s" in text, f"Expected the interval in {text}"

    def test_install_writes_units_and_restarts_timer(
        self, environment: Environment
    ) -> None:
        """Install writes both units, reloads, enables, and restarts the timer."""
        runner = FakeRunner()
        timer = SystemdTimer(runner, environment)

        timer.install(JOB)

        directory = environment.config_home / "systemd" / "user"
        written = sorted(path.name for path in directory.iterdir())
        assert written == ["skillsinc.service", "skillsinc.timer"], (
            f"Expected both units, got {written}"
        )
        assert [call[2:] for call in runner.calls] == [
            ("daemon-reload",),
            ("enable", "skillsinc.timer"),
            ("restart", "skillsinc.timer"),
        ], f"Expected reload, enable, restart; got {runner.calls}"

    def test_install_failure_raises(self, environment: Environment) -> None:
        """A failed systemctl step is reported."""
        runner = FakeRunner({("systemctl", "--user", "enable"): Completed(1)})

        with pytest.raises(ScheduleError, match="Enabling the timer"):
            SystemdTimer(runner, environment).install(JOB)

    def test_remove_deletes_units(self, environment: Environment) -> None:
        """Remove disables the timer and deletes both units."""
        timer = SystemdTimer(FakeRunner(), environment)
        timer.install(JOB)

        removed = timer.remove()

        directory = environment.config_home / "systemd" / "user"
        assert (removed, list(directory.iterdir())) == (True, []), (
            f"Expected no units left, got {removed}, {list(directory.iterdir())}"
        )

    def test_remove_missing_timer(self, environment: Environment) -> None:
        """Without a timer file nothing runs."""
        runner = FakeRunner()

        removed = SystemdTimer(runner, environment).remove()

        assert (removed, runner.calls) == (False, []), (
            f"Expected nothing to remove, got {removed}, {runner.calls}"
        )

    def test_status_with_last_run(self, environment: Environment) -> None:
        """Status reads the units and the service's last result."""
        runner = FakeRunner(
            {
                ("systemctl", "--user", "is-active"): Completed(0, "active\n"),
                ("systemctl", "--user", "show"): Completed(
                    0, "Result=success\nExecMainExitTimestamp=Wed 2026-01-07\n"
                ),
            }
        )
        timer = SystemdTimer(runner, environment)
        timer.install(JOB)

        status = timer.status()

        details = dict(status.details)
        assert (details["Every"], details["Timer"], details["Last result"]) == (
            "300s",
            "active",
            "success",
        ), f"Expected timer details, got {status.details}"

    def test_status_before_first_run(self, environment: Environment) -> None:
        """A timer that never fired shows no last run, even without a service file."""
        runner = FakeRunner({("systemctl", "--user", "show"): Completed(0, "")})
        timer = SystemdTimer(runner, environment)
        timer.install(JOB)
        (environment.config_home / "systemd/user/skillsinc.service").unlink()

        status = timer.status()

        assert "Last run" not in dict(status.details), (
            f"Expected no last run, got {status.details}"
        )

    def test_status_without_timer(self, environment: Environment) -> None:
        """No timer file means not installed."""
        status = SystemdTimer(FakeRunner(), environment).status()

        assert status.installed is False, f"Expected no timer, got {status}"


class TestCron:
    """cron keeps one marked line and leaves the rest of the crontab alone."""

    OTHER = "0 3 * * * backup"

    @pytest.mark.parametrize(
        ("interval", "expected"),
        [
            (timedelta(minutes=1), "* * * * *"),
            (timedelta(minutes=5), "*/5 * * * *"),
            (timedelta(hours=1), "0 */1 * * *"),
            (timedelta(hours=6), "0 */6 * * *"),
            (timedelta(days=1), "0 0 * * *"),
        ],
    )
    def test_expressions(self, interval: timedelta, expected: str) -> None:
        """Intervals that divide an hour or a day become cron schedules."""
        line = Cron(FakeRunner()).definition(replace(JOB, interval=interval))

        assert line.startswith(f"{expected} "), f"Expected {expected!r}, got {line!r}"

    @pytest.mark.parametrize(
        "interval",
        [
            timedelta(minutes=7),
            timedelta(seconds=90),
            timedelta(hours=5),
            timedelta(days=2),
        ],
    )
    def test_unexpressible_intervals(self, interval: timedelta) -> None:
        """Intervals cron cannot repeat evenly are refused."""
        with pytest.raises(ScheduleError, match="cron cannot run"):
            Cron(FakeRunner()).definition(replace(JOB, interval=interval))

    def test_definition_prefixes_variables(self) -> None:
        """Variables precede the command, shell-quoted."""
        job = replace(JOB, variables={"XDG_STATE_HOME": "/a b"})

        line = Cron(FakeRunner()).definition(job)

        assert "*/5 * * * * 'XDG_STATE_HOME=/a b' /opt/py/bin/python" in line, (
            f"Expected quoted variables, got {line!r}"
        )

    def test_install_replaces_own_line_only(self) -> None:
        """Install keeps other lines and swaps the previous SkillSinc line."""
        existing = f"{self.OTHER}\n*/9 * * * * old {Cron.MARK}\n"
        runner = FakeRunner({("crontab", "-l"): Completed(0, existing)})

        Cron(runner).install(JOB)

        written = runner.inputs[-1] or ""
        assert written.splitlines()[0] == self.OTHER, f"Expected kept line: {written}"
        assert written.count(Cron.MARK) == 1, f"Expected one line: {written}"
        assert "*/5 * * * *" in written, f"Expected the new schedule: {written}"

    def test_install_into_empty_crontab(self) -> None:
        """A user without a crontab gets one with just SkillSinc's line."""
        runner = FakeRunner(
            {("crontab", "-l"): Completed(1, "", "no crontab for mike")}
        )

        Cron(runner).install(JOB)

        assert (runner.inputs[-1] or "").count("\n") == 1, (
            f"Expected one line, got {runner.inputs[-1]!r}"
        )

    def test_unreadable_crontab_raises(self) -> None:
        """Other crontab failures stop the change."""
        runner = FakeRunner({("crontab", "-l"): Completed(1, "", "permission denied")})

        with pytest.raises(ScheduleError, match="Reading the crontab"):
            Cron(runner).install(JOB)

    def test_remove_keeps_other_lines(self) -> None:
        """Remove writes back everything but SkillSinc's line."""
        existing = f"{self.OTHER}\n*/5 * * * * x {Cron.MARK}\n"
        runner = FakeRunner({("crontab", "-l"): Completed(0, existing)})

        removed = Cron(runner).remove()

        assert (removed, runner.inputs[-1]) == (True, f"{self.OTHER}\n"), (
            f"Expected only the other line, got {removed}, {runner.inputs[-1]!r}"
        )

    def test_remove_without_line(self) -> None:
        """Without SkillSinc's line the crontab is not rewritten."""
        runner = FakeRunner({("crontab", "-l"): Completed(0, f"{self.OTHER}\n")})

        removed = Cron(runner).remove()

        assert (removed, len(runner.calls)) == (False, 1), (
            f"Expected no write, got {removed}, {runner.calls}"
        )

    def test_status_shows_line(self) -> None:
        """Status splits the schedule from the command."""
        line = Cron(FakeRunner()).definition(replace(JOB, variables={}))
        runner = FakeRunner({("crontab", "-l"): Completed(0, line)})

        status = Cron(runner).status()

        assert status.details == (
            ("When", "*/5 * * * *"),
            ("Runs", "/opt/py/bin/python -P -m skillsinc sync"),
        ), f"Expected the line's parts, got {status.details}"

    def test_status_without_line(self) -> None:
        """No marked line means not installed."""
        status = Cron(FakeRunner({("crontab", "-l"): Completed(0, "")})).status()

        assert status.installed is False, f"Expected no line, got {status}"


class TestSelection:
    """The platform picks the scheduler; Linux prefers a systemd user session."""

    @pytest.mark.parametrize(
        ("platform", "programs", "session", "expected"),
        [
            ("win32", frozenset(), 0, Scheduler.TASK_SCHEDULER),
            ("darwin", frozenset(), 0, Scheduler.LAUNCHD),
            ("linux", frozenset({"systemctl", "crontab"}), 0, Scheduler.SYSTEMD),
            ("linux", frozenset({"systemctl", "crontab"}), 1, Scheduler.CRON),
            ("linux", frozenset({"crontab"}), 0, Scheduler.CRON),
        ],
    )
    def test_detect(
        self, platform: str, programs: frozenset[str], session: int, expected: str
    ) -> None:
        """Each platform maps to its native scheduler."""
        runner = FakeRunner({("systemctl",): Completed(session)}, programs)

        assert detect(platform, runner) == expected, f"Expected {expected}"

    def test_detect_without_scheduler(self) -> None:
        """A Linux system with neither systemd nor cron needs --scheduler."""
        with pytest.raises(ScheduleError, match="--scheduler"):
            detect("linux", FakeRunner())

    @pytest.mark.parametrize("scheduler", list(Scheduler))
    def test_backend_titles(
        self, scheduler: Scheduler, environment: Environment
    ) -> None:
        """Every scheduler has a backend with a title and a location."""
        chosen = backend(scheduler, FakeRunner(), environment)

        assert chosen.title, f"Expected a title for {scheduler}"
        assert chosen.location, f"Expected a location for {scheduler}"

    def test_windowless_interpreter(self, tmp_path: Path) -> None:
        """Windows runs use pythonw.exe beside python.exe when it exists."""
        python = tmp_path / "python.exe"
        (tmp_path / "pythonw.exe").touch()

        result = interpreter(python, "win32")

        assert result == tmp_path / "pythonw.exe", f"Expected pythonw, got {result}"

    @pytest.mark.parametrize(
        ("name", "platform"), [("python.exe", "win32"), ("python3", "linux")]
    )
    def test_interpreter_unchanged(
        self, tmp_path: Path, name: str, platform: str
    ) -> None:
        """Without pythonw.exe, or off Windows, the interpreter stays the same."""
        python = tmp_path / name

        assert interpreter(python, platform) == python, "Expected no change"

    def test_command_line_quoting(self) -> None:
        """Display quoting follows the platform."""
        argv = ["C:\\a b\\py.exe", "sync"]

        result = (command_line(argv, "win32"), command_line(argv, "linux"))

        assert result == ('"C:\\a b\\py.exe" sync', "'C:\\a b\\py.exe' sync"), (
            f"Expected platform quoting, got {result}"
        )


class TestRunner:
    """The real runner captures output and reports missing programs."""

    def test_runs_program(self) -> None:
        """Output and input pass through the subprocess."""
        code = "import sys; sys.stdout.write(sys.stdin.read().upper())"

        result = Runner().run([sys.executable, "-c", code], stdin="ok")

        assert result == Completed(0, "OK", ""), f"Expected OK, got {result}"

    def test_missing_program_exits_127(self) -> None:
        """A program that does not exist behaves like a shell's 127."""
        result = Runner().run(["skillsinc-no-such-program"])

        assert result.code == 127, f"Expected 127, got {result}"

    def test_available(self) -> None:
        """PATH lookup finds real programs only."""
        runner = Runner()
        result = (runner.available(sys.executable), runner.available("skillsinc-nope"))

        assert result == (True, False), f"Expected (True, False), got {result}"


class TestScheduleCommand:
    """The command builds the job and reports each action."""

    @staticmethod
    def command(
        environment: Environment, runner: FakeRunner | None = None
    ) -> ScheduleCommand:
        """Return a command with a fixed interpreter."""
        return ScheduleCommand(
            environment, runner or FakeRunner(), Path("/opt/py/bin/python")
        )

    def test_install_reports_command_and_log(self, environment: Environment) -> None:
        """Install registers sync with the log file and prints how to check it."""
        runner = FakeRunner({("crontab", "-l"): Completed(0, "")})
        out = io.StringIO()

        status = self.command(environment, runner).run(settings(), out)

        log = environment.state_directory / "sync.log"
        assert status == 0, f"Expected success, got {status}"
        assert log.parent.is_dir(), f"Expected {log.parent} created"
        assert f"--log-file {shlex.quote(str(log))}" in (runner.inputs[-1] or ""), (
            f"Expected the log in the crontab, got {runner.inputs[-1]!r}"
        )
        assert "Registered cron crontab: sync every 5m." in out.getvalue(), (
            f"Expected a summary, got {out.getvalue()}"
        )
        assert "XDG_STATE_HOME=" in (runner.inputs[-1] or ""), (
            "Expected the state root carried into the job"
        )

    def test_install_passes_explicit_config(self, environment: Environment) -> None:
        """An explicit --config is passed to every scheduled run."""
        out = io.StringIO()
        config = environment.home / "custom.json"

        self.command(environment).run(
            settings(scheduler="systemd", config_path=config, dry_run=True), out
        )

        assert '"--config"' in out.getvalue(), f"Expected --config: {out.getvalue()}"
        assert "custom.json" in out.getvalue(), f"Expected the path: {out.getvalue()}"

    def test_systemd_install_logs_to_the_journal(
        self, environment: Environment
    ) -> None:
        """Systemd runs keep their output in the journal, so no log file is named."""
        out = io.StringIO()

        self.command(environment).run(settings(scheduler="systemd"), out)

        assert "--log-file" not in out.getvalue(), f"Expected no log: {out.getvalue()}"
        assert "\nLog:" not in out.getvalue(), f"Expected no log: {out.getvalue()}"

    def test_dry_run_registers_nothing(self, environment: Environment) -> None:
        """--dry-run prints the definition and runs no scheduler command."""
        runner = FakeRunner()
        out = io.StringIO()

        status = self.command(environment, runner).run(settings(dry_run=True), out)

        assert (status, runner.calls) == (0, []), (
            f"Expected no scheduler calls, got {status}, {runner.calls}"
        )
        assert out.getvalue().startswith("Would register with cron"), (
            f"Expected a preview, got {out.getvalue()}"
        )

    def test_windows_note_about_saved_variables(self, environment: Environment) -> None:
        """Task Scheduler cannot carry variables, so install says how to save them."""
        windows = replace(
            environment,
            variables={**environment.variables, "USERNAME": "mike"},
            platform="win32",
        )
        out = io.StringIO()

        self.command(windows).run(settings(scheduler="task-scheduler"), out)

        assert "set XDG_CONFIG_HOME, XDG_STATE_HOME with setx" in out.getvalue(), (
            f"Expected the setx note, got {out.getvalue()}"
        )

    def test_install_without_variables_has_no_note(
        self, environment: Environment
    ) -> None:
        """No relevant variables means no note."""
        windows = replace(environment, variables={"USERNAME": "m"}, platform="win32")
        out = io.StringIO()

        self.command(windows).run(settings(scheduler="task-scheduler"), out)

        assert "setx" not in out.getvalue(), f"Expected no note: {out.getvalue()}"

    @pytest.mark.parametrize(
        ("existing", "expected"),
        [
            (f"* * * * * x {Cron.MARK}\n", "Removed cron crontab."),
            ("", "Nothing to remove; no cron crontab."),
        ],
    )
    def test_remove(
        self, environment: Environment, existing: str, expected: str
    ) -> None:
        """Remove succeeds whether or not a job was registered."""
        runner = FakeRunner({("crontab", "-l"): Completed(0, existing)})
        out = io.StringIO()

        status = self.command(environment, runner).run(settings("remove"), out)

        assert (status, out.getvalue().strip()) == (0, expected), (
            f"Expected {expected!r}, got {status}, {out.getvalue()!r}"
        )

    @pytest.mark.parametrize(
        ("existing", "code", "expected"),
        [
            (f"*/5 * * * * x {Cron.MARK}\n", 0, "Installed: yes\nWhen: */5 * * * *"),
            ("", 1, "Installed: no\nRegister it"),
        ],
    )
    def test_status(
        self, environment: Environment, existing: str, code: int, expected: str
    ) -> None:
        """Status exits 0 when installed and 1 when not."""
        runner = FakeRunner({("crontab", "-l"): Completed(0, existing)})
        out = io.StringIO()

        status = self.command(environment, runner).run(settings("status"), out)

        assert status == code, f"Expected exit {code}, got {status}"
        assert expected in out.getvalue(), f"Expected {expected!r} in {out.getvalue()}"

    def test_detects_scheduler_when_not_given(self, environment: Environment) -> None:
        """Without --scheduler the platform's scheduler is used."""
        darwin = replace(environment, platform="darwin")
        out = io.StringIO()

        self.command(darwin).run(settings(scheduler=None, dry_run=True), out)

        assert out.getvalue().startswith("Would register with launchd"), (
            f"Expected launchd, got {out.getvalue()}"
        )

    def test_default_interpreter_is_this_python(self, environment: Environment) -> None:
        """Without an override the job runs the current interpreter with -P."""
        out = io.StringIO()

        ScheduleCommand(environment, FakeRunner()).run(settings(dry_run=True), out)

        python = interpreter(Path(sys.executable), environment.platform)
        expected = shlex.join([str(python), "-P", "-m", "skillsinc", "sync"])
        assert expected in out.getvalue(), f"Expected {expected} in {out.getvalue()}"
