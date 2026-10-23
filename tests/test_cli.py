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
    InitSettings,
    PlanSettings,
    ProfilesSettings,
    ScheduleSettings,
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

    def test_profiles_settings_are_typed(self, environment: Environment) -> None:
        """The profile table command needs no configuration file."""
        result = CommandLine(environment).parse(["profiles"])

        assert result == ProfilesSettings("WARNING"), (
            f"Expected profile settings, got {result!r}"
        )

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

    def test_profiles_lists_builtin_harnesses(self, sandbox: Sandbox) -> None:
        """Expose the supported profiles without reading a configuration."""
        status, output = sandbox.run("profiles")

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

    def test_keyboard_interrupt_returns_130(
        self, environment: Environment, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An interruption at the output boundary returns the shell interrupt code."""
        out = io.StringIO()
        monkeypatch.setattr(out, "write", self.interrupt)

        result = Application(environment, out).run(["profiles"])

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
            Application.main(["profiles"])

        assert caught.value.code == 0, (
            f"Expected main exit 0, got {caught.value.code!r}"
        )
