"""Starting the tray: forward to a running one, refuse, or run until quit."""

from __future__ import annotations

import io
import logging
import sys
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QSystemTrayIcon
from pytestqt.qtbot import QtBot
from tests.conftest import Sandbox

from skillsinc.__main__ import Application
from skillsinc.tray import app, launch
from skillsinc.tray.instance import SingleInstance
from skillsinc.usersettings import ScheduleOptions, SettingsStore, UserSettings


def _name() -> str:
    return f"skillsinc-test-{uuid.uuid4().hex}"


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """Put back the root logger the tray points at its log file."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


def test_launcher_runs_the_tray_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """``skillsinc-tray --sync`` is ``skillsinc tray --sync``."""
    calls: list[list[str]] = []
    monkeypatch.setattr(sys, "argv", ["skillsinc-tray", "--sync"])
    monkeypatch.setattr(Application, "main", calls.append)

    launch.main()

    assert calls == [["tray", "--sync"]]


class TestRun:
    """``app.run`` decides between forwarding, refusing, and running."""

    def test_request_goes_to_the_running_tray(
        self, qtbot: QtBot, configured: Sandbox
    ) -> None:
        """With a tray running, the request is sent and the launch ends."""
        name = _name()
        running = SingleInstance(name)
        assert running.listen()
        out = io.StringIO()
        try:
            with qtbot.waitSignal(running.message_received, timeout=5000) as blocker:
                status = app.run(
                    ["sync", "work"], configured.environment, out, instance_name=name
                )
        finally:
            running.close()

        assert status == app.EXIT_OK
        assert blocker.args == [["sync", "work"]]
        assert out.getvalue() == "Sent to the running SkillSinc tray.\n"

    def test_quit_with_nothing_running(self, qtbot: QtBot, configured: Sandbox) -> None:
        """Quitting when no tray runs says so."""
        del qtbot
        out = io.StringIO()

        status = app.run(["quit"], configured.environment, out, instance_name=_name())

        assert status == app.EXIT_OK
        assert out.getvalue() == "SkillSinc tray is not running.\n"

    def test_unusable_name_refuses_to_start(
        self,
        qtbot: QtBot,
        configured: Sandbox,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the single-instance socket cannot be taken, the launch fails."""
        del qtbot
        monkeypatch.setattr(app, "STARTING_WAIT_MS", 0)
        out = io.StringIO()
        name = str(tmp_path / "missing" / "socket")

        status = app.run([], configured.environment, out, instance_name=name)

        assert status == app.EXIT_UNAVAILABLE
        assert "Could not start the SkillSinc tray" in out.getvalue()

    @pytest.mark.usefixtures("restore_logging")
    def test_unsafe_log_is_reported_at_start(
        self, qtbot: QtBot, configured: Sandbox, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A tray that cannot log says so on its console and still runs."""
        del qtbot
        monkeypatch.setattr(app, "log_to_file", lambda *_args: False)
        SettingsStore.default(configured.environment).save(
            UserSettings(schedule=ScheduleOptions(enabled=False, run_at_start=False))
        )
        name = _name()
        QTimer.singleShot(200, lambda: SingleInstance(name).forward(["quit"]))
        out = io.StringIO()

        status = app.run([], configured.environment, out, instance_name=name)

        assert status == app.EXIT_OK
        assert "Not logging" in out.getvalue()

    @pytest.mark.usefixtures("restore_logging")
    def test_runs_until_another_launch_quits_it(
        self,
        qtbot: QtBot,
        configured: Sandbox,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The tray starts, handles its own request, and quits on request."""
        del qtbot
        monkeypatch.setattr(
            QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True)
        )
        SettingsStore.default(configured.environment).save(
            UserSettings(schedule=ScheduleOptions(enabled=False, run_at_start=False))
        )
        name = _name()
        QTimer.singleShot(200, lambda: SingleInstance(name).forward(["quit"]))

        status = app.run(
            ["show", "history"],
            configured.environment,
            io.StringIO(),
            instance_name=name,
        )

        assert status == app.EXIT_OK
        log = configured.environment.state_directory / app.LOG_FILE
        assert "SkillSinc tray started" in log.read_text(encoding="utf-8")


class TestLaunchRaces:
    """Two launches at once, consoles that are missing, and unsafe logs."""

    def test_launch_during_another_start_hands_over(
        self, qtbot: QtBot, configured: Sandbox, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A launch that loses the start lock sends its request once ready."""
        del qtbot
        folder = configured.environment.state_directory
        folder.mkdir(parents=True, exist_ok=True)
        holder = QLockFile(str(folder / app.LOCK_FILE))
        assert holder.tryLock(0)
        answers = iter([False, False, True])
        monkeypatch.setattr(SingleInstance, "forward", lambda *_args: next(answers))
        out = io.StringIO()
        try:
            status = app.run(
                ["sync"], configured.environment, out, instance_name=_name()
            )
        finally:
            holder.unlock()

        assert status == app.EXIT_OK
        assert out.getvalue() == "Sent to the running SkillSinc tray.\n"

    def test_no_console_writes_nothing(self, qtbot: QtBot, configured: Sandbox) -> None:
        """Under pythonw there is no stdout; status lines are dropped."""
        del qtbot

        status = app.run(["quit"], configured.environment, None, instance_name=_name())

        assert status == app.EXIT_OK

    @pytest.mark.usefixtures("restore_logging")
    @pytest.mark.skipif(sys.platform == "win32", reason="links need privileges")
    def test_linked_log_is_left_alone(
        self, configured: Sandbox, tmp_path: Path
    ) -> None:
        """A link at tray.log is never followed."""
        folder = configured.environment.state_directory
        folder.mkdir(parents=True, exist_ok=True)
        target = tmp_path / "victim.txt"
        target.write_text("keep", encoding="utf-8")
        (folder / app.LOG_FILE).symlink_to(target)

        assert not app.log_to_file(configured.environment, "INFO")
        logging.getLogger("skillsinc").warning("not written")
        assert target.read_text(encoding="utf-8") == "keep"

    @pytest.mark.usefixtures("restore_logging")
    @pytest.mark.skipif(sys.platform == "win32", reason="FIFOs are POSIX")
    def test_fifo_log_is_refused_without_blocking(self, configured: Sandbox) -> None:
        """A FIFO at tray.log is refused at once."""
        import os  # noqa: PLC0415

        folder = configured.environment.state_directory
        folder.mkdir(parents=True, exist_ok=True)
        fifo = folder / app.LOG_FILE
        os.mkfifo(fifo)

        assert not app.log_to_file(configured.environment, "INFO")
        reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        try:
            assert not app.log_to_file(configured.environment, "INFO")
        finally:
            os.close(reader)

    def test_sign_in_can_be_switched_off(self, configured: Sandbox) -> None:
        """SKILLSINC_TRAY_SIGN_IN=off keeps the tray away from sign-in entries."""
        environment = replace(
            configured.environment,
            variables={**configured.environment.variables, app.SIGN_IN_VARIABLE: "off"},
        )

        assert app.login_item(environment) is None
        assert app.login_item(configured.environment) is not None
