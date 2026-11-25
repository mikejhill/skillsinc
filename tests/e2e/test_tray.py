"""Run ``skillsinc tray`` as its own process: start, forward requests, quit."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from tests.e2e.conftest import TIMEOUT_SECONDS, Cli, Harnesses

pytest.importorskip("PySide6", reason="the tray extra is not installed")

POLL_SECONDS = 0.2


def _runs(cli: Cli) -> list[dict[str, Any]]:
    result = cli.run("history", "--json")
    assert result.status == 0, f"Got {result!r}"
    return result.json()["runs"]


def _wait_for_run(
    cli: Cli, trigger: str, tray: subprocess.Popen[str]
) -> dict[str, Any]:
    """Wait for a finished run started by ``trigger``; fail if the tray exits."""
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        found = [run for run in _runs(cli) if run["trigger"] == trigger]
        if found:
            return found[0]
        assert tray.poll() is None, f"The tray exited early: {tray.communicate()}"
        time.sleep(POLL_SECONDS)
    pytest.fail(f"No {trigger} run within {TIMEOUT_SECONDS}s: {_runs(cli)}")


class TestTrayProcess:
    """The tray syncs on start, takes requests from later launches, and quits."""

    def test_start_sync_request_and_quit(self, cli: Cli, library: Path) -> None:
        """A second launch hands its request to the running tray."""
        Harnesses.install(cli.home)
        assert cli.run("init", "--source", str(library)).status == 0
        # Never touch the developer's real sign-in entry (HKCU is not per home)
        environment = cli.environment | {
            "QT_QPA_PLATFORM": "offscreen",
            "SKILLSINC_TRAY_SIGN_IN": "off",
        }
        cli.environment = environment
        tray = subprocess.Popen(  # noqa: S603 - argv is built by the test
            [*cli.command, "tray"],
            cwd=cli.home,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
        )
        try:
            started = _wait_for_run(cli, "tray-startup", tray)
            forwarded = cli.run("tray", "--sync")
            requested = _wait_for_run(cli, "tray", tray)
            quit_result = cli.run("tray", "--quit")
            status = tray.wait(timeout=TIMEOUT_SECONDS)
        finally:
            if tray.poll() is None:
                tray.kill()
            tray.communicate()

        assert started["ok"], f"Got {started}"
        assert started["counts"].get("create", 0) > 0, f"Got {started}"
        assert forwarded.stdout == "Sent to the running SkillSinc tray.\n", forwarded
        assert requested["ok"], f"Got {requested}"
        assert quit_result.status == 0, f"Got {quit_result!r}"
        assert status == 0, f"The tray exited with {status}"
        assert (cli.state_directory / "tray.log").is_file()

    def test_quit_when_not_running(self, cli: Cli) -> None:
        """Quitting with no tray running says so and succeeds."""
        cli.environment = cli.environment | {"QT_QPA_PLATFORM": "offscreen"}

        result = cli.run("tray", "--quit")

        assert (result.status, result.stdout) == (
            0,
            "SkillSinc tray is not running.\n",
        ), f"Got {result!r}"
