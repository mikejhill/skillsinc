"""The sync runner: one child at a time, queued, deduplicated, bounded."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess
from pytestqt.qtbot import QtBot

from skillsinc.history import Trigger
from skillsinc.tray.runner import (
    MAXIMUM_OUTPUT,
    SyncOutcome,
    SyncRequest,
    SyncRunner,
    _Running,
)

# A stand-in for `python -m skillsinc`: logs its arguments, waits, then exits
FAKE = """\
import os, sys, time
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
print("output from the child")
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
sys.exit(int(os.environ.get("FAKE_CODE", "0")))
"""


@pytest.fixture
def fake(tmp_path: Path) -> Path:
    """Write the fake program; return its log path."""
    (tmp_path / "fake.py").write_text(FAKE, encoding="utf-8")
    return tmp_path / "calls.log"


def _runner(
    tmp_path: Path,
    log: Path,
    *,
    sleep: float = 0,
    code: int = 0,
    timeout_ms: int = 30_000,
) -> SyncRunner:
    variables = dict(os.environ)
    variables.update(FAKE_LOG=str(log), FAKE_SLEEP=str(sleep), FAKE_CODE=str(code))
    return SyncRunner(
        tmp_path / "config.json",
        program=[sys.executable, str(tmp_path / "fake.py")],
        environment=variables,
        timeout_ms=timeout_ms,
    )


def _finish(qtbot: QtBot, runner: SyncRunner, request: SyncRequest) -> SyncOutcome:
    with qtbot.waitSignal(runner.finished, timeout=20_000) as blocker:
        assert runner.request(request)
    outcome = blocker.args[0]
    assert isinstance(outcome, SyncOutcome)
    return outcome


class TestRunner:
    """Each request becomes one ``sync`` child with the right arguments."""

    def test_arguments_name_trigger_config_and_profiles(
        self, qtbot: QtBot, tmp_path: Path, fake: Path
    ) -> None:
        """The child gets ``sync --trigger --config`` and one --profile each."""
        runner = _runner(tmp_path, fake)

        outcome = _finish(qtbot, runner, SyncRequest(Trigger.TRAY, ("work", "home")))

        config = tmp_path / "config.json"
        assert outcome.ok
        assert "output from the child" in outcome.output
        assert fake.read_text(encoding="utf-8").split() == [
            "sync",
            "--trigger",
            "tray",
            "--config",
            str(config),
            "--profile",
            "work",
            "--profile",
            "home",
        ]

    def test_failure_exit_code(self, qtbot: QtBot, tmp_path: Path, fake: Path) -> None:
        """A non-zero exit is a failed outcome."""
        outcome = _finish(
            qtbot, _runner(tmp_path, fake, code=1), SyncRequest(Trigger.TRAY)
        )

        assert (outcome.ok, outcome.exit_code) == (False, 1)

    def test_requests_queue_and_duplicates_are_dropped(
        self, qtbot: QtBot, tmp_path: Path, fake: Path
    ) -> None:
        """A second request waits; a repeat of a running or queued one is dropped."""
        runner = _runner(tmp_path, fake, sleep=0.5)
        first = SyncRequest(Trigger.TRAY)
        second = SyncRequest(Trigger.TRAY, ("work",))
        outcomes: list[SyncOutcome] = []
        runner.finished.connect(outcomes.append)

        assert runner.request(first)
        assert runner.busy
        assert runner.current == first
        assert runner.request(second)
        assert not runner.request(first), "Expected the running request dropped"
        assert not runner.request(second), "Expected the queued request dropped"
        assert not runner.request(SyncRequest(Trigger.TRAY_SCHEDULED)), (
            "Expected a scheduled run dropped while another runs"
        )
        assert runner.pending == (second,)
        qtbot.waitUntil(lambda: len(outcomes) == 2, timeout=20_000)

        assert [outcome.request for outcome in outcomes] == [first, second]
        assert not runner.busy

    def test_cancel_stops_the_child_and_clears_the_queue(
        self, qtbot: QtBot, tmp_path: Path, fake: Path
    ) -> None:
        """Cancel kills the running child and drops what waits."""
        runner = _runner(tmp_path, fake, sleep=30)
        with qtbot.waitSignal(runner.finished, timeout=20_000) as blocker:
            runner.request(SyncRequest(Trigger.TRAY))
            runner.request(SyncRequest(Trigger.TRAY, ("x",)))
            qtbot.waitUntil(fake.exists, timeout=10_000)
            runner.cancel()

        assert not blocker.args[0].ok
        assert runner.pending == ()

    def test_time_limit_stops_a_hung_child(
        self, qtbot: QtBot, tmp_path: Path, fake: Path
    ) -> None:
        """A child past the time limit is killed and reported failed."""
        runner = _runner(tmp_path, fake, sleep=30, timeout_ms=500)

        outcome = _finish(qtbot, runner, SyncRequest(Trigger.TRAY_SCHEDULED))

        assert not outcome.ok

    def test_program_that_cannot_start(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A missing interpreter is a failed outcome with a reason."""
        runner = SyncRunner(tmp_path / "c.json", program=[str(tmp_path / "missing")])

        outcome = _finish(qtbot, runner, SyncRequest(Trigger.TRAY))

        assert not outcome.ok
        assert "could not start" in outcome.error
        assert runner.wait()

    def test_default_program_runs_this_package(self, tmp_path: Path) -> None:
        """Without a program the runner uses this Python with -m skillsinc."""
        runner = SyncRunner(tmp_path / "c.json")

        argv = runner.arguments(SyncRequest(Trigger.TRAY))

        assert argv[1:4] == ["-P", "-m", "skillsinc"]
        runner.set_config(tmp_path / "other.json")
        assert runner.arguments(SyncRequest(Trigger.TRAY))[-1].endswith("other.json")

    @pytest.mark.parametrize(
        ("profiles", "label"),
        [((), "default profiles"), (("*",), "all profiles"), (("a", "b"), "a, b")],
    )
    def test_labels(self, profiles: tuple[str, ...], label: str) -> None:
        """Requests describe themselves for menus."""
        assert SyncRequest(Trigger.TRAY, profiles).label() == label


class TestRunnerEdges:
    """Late signals and idle calls change nothing; output stays bounded."""

    def test_idle_calls_do_nothing(
        self, qtbot: QtBot, tmp_path: Path, fake: Path
    ) -> None:
        """Cancel, a deadline, or a stale finish with nothing running is a no-op."""
        del qtbot
        runner = _runner(tmp_path, fake)
        stale = _Running(SyncRequest(Trigger.TRAY), QProcess())

        runner.cancel()
        runner._on_timeout()
        runner._finish(stale, SyncOutcome(stale.request, 0))

        assert not runner.busy

    def test_output_keeps_only_the_tail(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A chatty child cannot grow the tray's memory without bound."""
        runner = SyncRunner(
            tmp_path / "config.json",
            program=[
                sys.executable,
                "-c",
                f"print('x' * {MAXIMUM_OUTPUT * 2}, end=''); print('END')",
            ],
        )

        outcome = _finish(qtbot, runner, SyncRequest(Trigger.TRAY))

        assert len(outcome.output.encode()) <= MAXIMUM_OUTPUT
        assert outcome.output.endswith("END\n") or outcome.output.endswith("END\r\n")


def test_queued_request_keeps_its_configuration(
    qtbot: QtBot, tmp_path: Path, fake: Path
) -> None:
    """Changing the configuration does not redirect a sync already queued."""
    runner = _runner(tmp_path, fake, sleep=0.5)
    first, second = SyncRequest(Trigger.TRAY), SyncRequest(Trigger.TRAY, ("home",))
    with qtbot.waitSignals([runner.finished, runner.finished], timeout=20_000):
        assert runner.request(first)
        assert runner.request(second)
        runner.set_config(tmp_path / "other.json")
        assert runner.request(second), "Expected the new configuration queued too"

    lines = fake.read_text(encoding="utf-8").splitlines()
    assert str(tmp_path / "config.json") in lines[1]
