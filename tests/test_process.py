"""Test that child processes start without a console window on Windows."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable

import pytest

from skillsinc.process import NO_WINDOW
from skillsinc.remote import GitRunner
from skillsinc.schedule import Runner

Options = dict[str, object]


def _capture(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Options]:
    """Replace ``subprocess.run`` with a recorder of its keyword arguments."""
    calls: list[Options] = []

    def run(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        calls.append(options)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_flag_hides_windows_only_on_windows() -> None:
    """Windows gets ``CREATE_NO_WINDOW``; other platforms pass no flags."""
    expected = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    wanted = expected if sys.platform == "win32" else 0

    assert wanted == NO_WINDOW, f"Expected flags {wanted}, got {NO_WINDOW}"


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
def test_every_runner_starts_children_without_a_window(
    monkeypatch: pytest.MonkeyPatch, start: Callable[[], object]
) -> None:
    """Git, ``gh``, and scheduler commands all pass the no-window flag."""
    calls = _capture(monkeypatch)

    start()

    flags = [call.get("creationflags") for call in calls]

    assert flags == [NO_WINDOW], f"Expected one call with {NO_WINDOW}, got {flags}"


def test_runner_still_runs_real_programs() -> None:
    """The flag does not stop output capture on any platform."""
    ran = GitRunner().run(
        [sys.executable, "-c", "print('hello')"], dict(os.environ), 30
    )

    result = (ran.code, ran.stdout.strip())

    assert result == (0, "hello"), f"Expected (0, 'hello'), got {result}"
