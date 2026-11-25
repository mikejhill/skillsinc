"""Starting the tray: forward to a running one, refuse, or run until quit."""

from __future__ import annotations

import io
import logging
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
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
        self, qtbot: QtBot, configured: Sandbox, tmp_path: Path
    ) -> None:
        """If the single-instance socket cannot be taken, the launch fails."""
        del qtbot
        out = io.StringIO()
        name = str(tmp_path / "missing" / "socket")

        status = app.run([], configured.environment, out, instance_name=name)

        assert status == app.EXIT_UNAVAILABLE
        assert "Could not start the SkillSinc tray" in out.getvalue()

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
