"""Test argument parsing, usage failures, and application boundary exits."""

from __future__ import annotations

import io

import pytest
from tests.conftest import Sandbox

from skillsinc.__main__ import Application
from skillsinc.cli import CommandLine
from skillsinc.paths import Environment
from skillsinc.settings import InitSettings, PlanSettings, ProfilesSettings


class TestCommandLine:
    """CLI options become typed settings before reaching command handlers."""

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

    @pytest.mark.parametrize(
        "argv",
        [
            [],
            ["unknown"],
            ["sync", "--log-level", "INVALID"],
            ["init", "--source", "missing-directory"],
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
