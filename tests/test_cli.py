"""Test argument parsing, usage failures, and application boundary exits."""

from __future__ import annotations

import io
import logging
import sys
from datetime import timedelta

import pytest
from tests.conftest import Sandbox

from skillsinc.__main__ import Application
from skillsinc.cli import CommandLine
from skillsinc.paths import Environment
from skillsinc.settings import (
    HarnessesSettings,
    InitSettings,
    PlanSettings,
    ProfilesSettings,
    ScheduleSettings,
    TraySettings,
)


class TestCommandLine:
    """CLI options become typed settings before reaching command handlers."""

    def test_guidance_is_explicitly_opted_in(self, environment: Environment) -> None:
        """The new init option is typed and does not alter its default."""
        default = CommandLine(environment).parse(["init"])
        opted_in = CommandLine(environment).parse(["init", "--with-guidance"])

        assert isinstance(default, InitSettings), f"Expected init settings: {default!r}"
        assert not default.with_guidance, (
            f"Expected manual empty-library default, got {default!r}"
        )
        assert isinstance(opted_in, InitSettings), (
            f"Expected init settings: {opted_in!r}"
        )
        assert opted_in.with_guidance, (
            f"Expected explicit guidance option, got {opted_in!r}"
        )

    def test_sync_options_are_typed(self, environment: Environment) -> None:
        """Parse repeatable destinations and boolean output/adoption flags."""
        result = CommandLine(environment).parse(
            [
                "sync",
                "-d",
                "one",
                "-d",
                "two",
                "--adopt",
                "--json",
                "-v",
                "--log-level",
                "DEBUG",
            ]
        )

        assert result == PlanSettings(
            config_path=environment.default_config_file,
            apply=True,
            destinations=("one", "two"),
            adopt=True,
            json_output=True,
            verbose=True,
            log_level="DEBUG",
        ), f"Expected complete sync settings, got {result!r}"

    def test_init_settings_are_typed(self, environment: Environment) -> None:
        """Parse init without a source using the injected config path."""
        result = CommandLine(environment).parse(["init", "--force"])

        assert result == InitSettings(
            config_path=environment.default_config_file,
            source=None,
            force=True,
            log_level="WARNING",
        ), f"Expected init settings, got {result!r}"

    def test_harnesses_settings_are_typed(self, environment: Environment) -> None:
        """The harness table command needs no configuration file."""
        result = CommandLine(environment).parse(["harnesses"])

        assert result == HarnessesSettings("WARNING"), (
            f"Expected harness settings, got {result!r}"
        )

    def test_profiles_settings_are_typed(self, environment: Environment) -> None:
        """``profiles`` reads the configuration and accepts --profile and --json."""
        result = CommandLine(environment).parse(
            ["profiles", "--json", "-p", "work", "--profile", "home,x"]
        )

        assert result == ProfilesSettings(
            environment.config_file(),
            json_output=True,
            log_level="WARNING",
            profiles=("work", "home,x"),
        ), f"Expected profile settings, got {result!r}"

    @pytest.mark.parametrize(
        ("argv", "request_"),
        [
            ([], ()),
            (["--sync"], ("sync",)),
            (["--sync", "work", "*"], ("sync", "work", "*")),
            (["--show", "about"], ("show", "about")),
            (["--quit"], ("quit",)),
        ],
    )
    def test_tray_requests_are_typed(
        self, environment: Environment, argv: list[str], request_: tuple[str, ...]
    ) -> None:
        """``tray`` options become the request a running tray receives."""
        result = CommandLine(environment).parse(["tray", *argv])

        assert result == TraySettings(request_, "INFO"), f"Got {result!r}"

    def test_sync_accepts_profiles(self, environment: Environment) -> None:
        """``sync --profile`` is repeatable and kept verbatim for the selector."""
        result = CommandLine(environment).parse(["sync", "-p", "a", "-p", "b,c"])

        assert isinstance(result, PlanSettings), f"Expected plan settings: {result!r}"
        assert result.profiles == ("a", "b,c"), f"Got {result.profiles!r}"

    def test_schedule_install_settings_are_typed(
        self, environment: Environment
    ) -> None:
        """Parse the interval, scheduler, absolute config path, and dry run."""
        result = CommandLine(environment).parse(
            [
                "schedule",
                "install",
                "--interval",
                "00:05:00",
                "--scheduler",
                "cron",
                "--config",
                "~/custom.json",
                "--dry-run",
            ]
        )

        assert result == ScheduleSettings(
            action="install",
            scheduler="cron",
            interval=timedelta(minutes=5),
            config_path=environment.home / "custom.json",
            dry_run=True,
            log_level="WARNING",
        ), f"Expected install settings, got {result!r}"

    def test_schedule_status_defaults(self, environment: Environment) -> None:
        """Status needs no interval or config and detects the scheduler."""
        result = CommandLine(environment).parse(["schedule", "status"])

        assert result == ScheduleSettings(
            action="status",
            scheduler=None,
            interval=timedelta(hours=1),
            config_path=None,
            dry_run=False,
            log_level="WARNING",
        ), f"Expected status settings, got {result!r}"

    @pytest.mark.parametrize(
        "argv",
        [
            [],
            ["unknown"],
            ["sync", "--log-level", "INVALID"],
            ["init", "--source", "missing-directory"],
            ["schedule"],
            ["schedule", "install", "--interval", "5"],
            ["schedule", "install", "--interval", "30s"],
            ["schedule", "status", "--scheduler", "at"],
        ],
    )
    def test_invalid_arguments_exit_two(
        self, environment: Environment, argv: list[str]
    ) -> None:
        """Reject invalid commands, diagnostic levels, and nonexistent sources."""
        with pytest.raises(SystemExit) as caught:
            CommandLine(environment).parse(argv)

        assert caught.value.code == 2, (
            f"Expected parser exit 2, got {caught.value.code!r}"
        )

    @pytest.mark.parametrize("argv", [["--help"], ["--version"]])
    def test_help_and_version_exit_zero(
        self, environment: Environment, argv: list[str]
    ) -> None:
        """Informational parser commands exit successfully."""
        with pytest.raises(SystemExit) as caught:
            CommandLine(environment).parse(argv)

        assert caught.value.code == 0, (
            f"Expected informational exit 0, got {caught.value.code!r}"
        )


class TestApplication:
    """Application translates process-boundary failures to documented status codes."""

    def test_log_file_receives_report_and_diagnostics(self, sandbox: Sandbox) -> None:
        """--log-file appends a header, the report, and errors; stdout stays empty."""
        log = sandbox.environment.state_directory / "sync.log"

        first = sandbox.run("plan", "--log-file", str(log))
        sandbox.configure()
        second = sandbox.run("sync", "--log-file", str(log))

        text = log.read_text(encoding="utf-8")
        assert (first, second) == ((2, ""), (0, "")), (
            f"Expected nothing on stdout, got {first}, {second}"
        )
        assert text.count("--- ") == 2, f"Expected two run headers in {text}"
        assert "skillsinc plan\nERROR:" in text, f"Expected the plan error in {text}"
        assert "skillsinc sync\n" in text, f"Expected the sync header in {text}"
        handler = logging.getLogger().handlers[0]
        assert isinstance(handler, logging.StreamHandler), "Expected a stream handler"
        assert handler.stream is sys.stderr, "Expected logging restored to stderr"

    def test_large_log_file_is_rotated(self, sandbox: Sandbox) -> None:
        """A log over 1 MiB moves to <name>.1 before the run appends."""
        log = sandbox.environment.state_directory / "sync.log"
        log.parent.mkdir(parents=True)
        log.write_text("x" * (1024 * 1024 + 1), encoding="utf-8")

        sandbox.run("plan", "--log-file", str(log))

        sizes = (log.stat().st_size, log.with_name("sync.log.1").stat().st_size)
        assert sizes[0] < 1024 < sizes[1], f"Expected a rotated log, got {sizes}"

    def test_schedule_dry_run_dispatches(self, sandbox: Sandbox) -> None:
        """The schedule command is reachable through the application."""
        status, output = sandbox.run(
            "schedule", "install", "--scheduler", "cron", "--dry-run"
        )

        assert status == 0, f"Expected success, got {status}: {output}"
        assert "0 */1 * * *" in output, f"Expected an hourly line, got {output}"

    def test_harnesses_lists_builtin_profiles(self, sandbox: Sandbox) -> None:
        """Expose the supported harness profiles without reading a configuration."""
        status, output = sandbox.run("harnesses")

        assert status == 0, f"Expected all built-ins, got {status}: {output}"
        assert all(
            name in output
            for name in (
                "claude",
                "copilot",
                "vscode",
                "codex",
                "agents",
                "generic",
            )
        ), f"Expected all built-ins, got {status}: {output}"

    def test_tray_dispatches_to_the_tray_app(
        self, environment: Environment, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``skillsinc tray`` hands its request to the tray application."""
        pytest.importorskip("PySide6")
        from skillsinc.tray import app  # noqa: PLC0415 - optional dependency

        calls: list[tuple[str, ...]] = []

        def run(request: tuple[str, ...], *_args: object, log_level: str) -> int:
            calls.append((*request, log_level))
            return 0

        monkeypatch.setattr(app, "run", run)

        out = io.StringIO()
        assert Application(environment, out).run(["tray", "--quit"]) == 0
        assert Application(environment, out).run(["tray", "--log-level", "ERROR"]) == 0
        assert calls == [("quit", "INFO"), ("ERROR",)]

    def test_tray_without_the_extra_explains_the_install(
        self,
        environment: Environment,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Without PySide6 the tray says how to install it."""
        import skillsinc.tray  # noqa: PLC0415

        monkeypatch.setitem(sys.modules, "skillsinc.tray.app", None)
        monkeypatch.delattr(skillsinc.tray, "app", raising=False)

        status = Application(environment, io.StringIO()).run(["tray"])

        assert status == 1
        assert "skillsinc[tray]" in capsys.readouterr().err

    def test_keyboard_interrupt_returns_130(
        self, environment: Environment, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An interruption at the output boundary returns the shell interrupt code."""
        out = io.StringIO()
        monkeypatch.setattr(out, "write", self.interrupt)

        result = Application(environment, out).run(["harnesses"])

        assert result == 130, f"Expected interrupted exit 130, got {result}"

    @staticmethod
    def interrupt(text: str) -> int:
        """Simulate an interrupted output boundary."""
        raise KeyboardInterrupt(text)

    def test_main_exits_with_application_status(
        self, environment: Environment, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The installed entry point exits with the command result."""
        monkeypatch.setattr(Environment, "current", lambda: environment)

        with pytest.raises(SystemExit) as caught:
            Application.main(["harnesses"])

        assert caught.value.code == 0, (
            f"Expected main exit 0, got {caught.value.code!r}"
        )
