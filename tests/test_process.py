"""Test that child processes open no console window from a windowless run."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable

import pytest

from skillsinc.process import ChildWindows
from skillsinc.remote import GitRunner
from skillsinc.schedule import Runner

Options = dict[str, object]


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[Options]:
    """Replace ``subprocess.run`` with a recorder of its keyword arguments."""
    calls: list[Options] = []

    def run(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        calls.append(options)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


@pytest.mark.parametrize(
    ("platform", "console", "expected"),
    [
        pytest.param("win32", None, ChildWindows.CREATE_NO_WINDOW, id="pythonw"),
        pytest.param("win32", "conhost", 0, id="windows-terminal"),
        pytest.param("linux", None, 0, id="linux"),
        pytest.param("darwin", "tty", 0, id="macos"),
    ],
)
def test_only_a_windowless_windows_run_hides_children(
    platform: str, console: str | None, expected: int
) -> None:
    """A terminal keeps its console, so Ctrl+C still reaches Git's helpers."""
    flags = ChildWindows(platform, lambda: console is not None).flags()

    assert flags == expected, f"Expected {expected:#x}, got {flags:#x}"


def test_flag_matches_the_windows_constant() -> None:
    """The constant is the value Windows defines for ``CREATE_NO_WINDOW``."""
    wanted = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    assert wanted == ChildWindows.CREATE_NO_WINDOW, (
        f"Expected {wanted:#x}, got {ChildWindows.CREATE_NO_WINDOW:#x}"
    )


@pytest.mark.parametrize(
    "start",
    [
        pytest.param(
            lambda: GitRunner().run(["git", "--version"], dict(os.environ), 5),
            id="git-runner",
        ),
        pytest.param(lambda: Runner().run(["schtasks.exe", "/Query"]), id="scheduler"),
    ],
)
def test_every_runner_passes_the_chosen_flags(
    monkeypatch: pytest.MonkeyPatch, start: Callable[[], object]
) -> None:
    """Git, ``gh``, and scheduler commands all start with the chosen flags."""
    monkeypatch.setattr(ChildWindows, "flags", lambda _self: 0x1234)
    calls = _capture(monkeypatch)

    start()

    flags = [call.get("creationflags") for call in calls]
    assert flags == [0x1234], f"Expected one call with 0x1234, got {flags}"


def test_runner_still_runs_real_programs() -> None:
    """The flags do not stop output capture on any platform."""
    ran = GitRunner().run(
        [sys.executable, "-c", "print('hello')"], dict(os.environ), 30
    )
    result = (ran.code, ran.stdout.strip())

    assert result == (0, "hello"), f"Expected (0, 'hello'), got {result}"
