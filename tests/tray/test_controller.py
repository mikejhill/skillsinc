"""The tray controller's edges: failures, settings changes, and odd states."""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import override

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QSystemTrayIcon
from pytestqt.qtbot import QtBot
from tests.conftest import Sandbox
from tests.tray.conftest import Tray, child_environment, make_tray

from skillsinc.history import SyncHistory, SyncRecord, Trigger
from skillsinc.tray import controller as controller_module
from skillsinc.tray.controller import open_with_desktop
from skillsinc.tray.icons import TrayState
from skillsinc.tray.runner import SyncRequest, SyncRunner
from skillsinc.tray.startup import LoginItem
from skillsinc.usersettings import (
    NotificationOptions,
    ScheduleOptions,
    SettingsStore,
    StartupOptions,
    UserSettings,
)

pytestmark = pytest.mark.ux

OFF = ScheduleOptions(enabled=False, run_at_start=False)


def _program_runner(sandbox: Sandbox, code: str) -> SyncRunner:
    """Return a runner whose "sync" is a Python one-liner."""
    return SyncRunner(
        sandbox.config_path,
        program=[sys.executable, "-c", code],
        environment=child_environment(sandbox.environment),
    )


class FailingLoginItem(LoginItem):
    """A sign-in entry the system refuses to change."""

    @override
    def apply(self, *, enabled: bool) -> bool:
        """Refuse."""
        msg = f"access denied (enabled={enabled})"
        raise OSError(msg)


def test_open_with_desktop_asks_qt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Files open through the desktop's default application."""
    asked: list[str] = []

    def open_url(url: object) -> bool:
        asked.append(str(url.toLocalFile()))  # ty: ignore[unresolved-attribute]
        return True

    monkeypatch.setattr(controller_module.QDesktopServices, "openUrl", open_url)

    assert open_with_desktop(tmp_path)
    assert asked == [str(tmp_path)]


class TestSettingsEdges:
    """Broken, missing, and changed settings."""

    def test_broken_settings_at_start_are_reported(
        self, qtbot: QtBot, configured: Sandbox
    ) -> None:
        """Starting over an invalid settings.json notifies and uses defaults."""
        store = SettingsStore.default(configured.environment)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text('{"schedule": {"enabled": "yes"}}', encoding="utf-8")
        tray = make_tray(qtbot, configured, save=False, start=False)
        try:
            tray.controller.start()

            assert tray.notices
            assert tray.notices[0].failure
            assert "Using defaults" in tray.notices[0].message
        finally:
            tray.controller.stop()

    def test_settings_that_cannot_be_created_are_reported(
        self, qtbot: QtBot, configured: Sandbox
    ) -> None:
        """Opening settings when settings.json cannot be written says why."""
        tray = make_tray(qtbot, configured, save=False)
        try:
            folder = SettingsStore.default(configured.environment).path.parent
            folder.mkdir(parents=True, exist_ok=True)
            blocked = folder / "settings.json"
            blocked.mkdir()

            tray.controller.show_settings()

            assert tray.opened == []
            assert tray.notices[-1].failure
        finally:
            tray.controller.stop()

    def test_a_new_config_path_is_followed(self, tray: Tray, tmp_path: Path) -> None:
        """Pointing settings at another configuration syncs that one."""
        other = tmp_path / "other" / "config.json"
        other.parent.mkdir()
        other.write_text(
            tray.sandbox.config_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        store = SettingsStore.default(tray.sandbox.environment)
        store.save(UserSettings(config=other, schedule=OFF))

        tray.controller.reload_settings()
        tray.controller.open_config()

        assert tray.opened == [other]
        assert tray.controller.runner.arguments(SyncRequest(Trigger.TRAY))[-1] == str(
            other
        )

    def test_watch_without_a_settings_file(self, tray: Tray) -> None:
        """With settings.json gone, defaults apply and its folder stays watched."""
        SettingsStore.default(tray.sandbox.environment).path.unlink()

        tray.controller.reload_settings()

        assert tray.controller.settings == UserSettings()

    def test_unsupported_sign_in_is_left_alone(
        self, qtbot: QtBot, configured: Sandbox
    ) -> None:
        """Where start at sign-in is unsupported, the setting changes nothing."""
        mac = replace(configured.environment, platform="darwin")
        item = LoginItem(mac, ["skillsinc-tray"])
        settings = UserSettings(
            schedule=OFF, startup=StartupOptions(launch_at_login=True)
        )
        tray = make_tray(qtbot, configured, settings, login_item=item)
        try:
            assert not item.enabled()
            assert tray.notices == []
        finally:
            tray.controller.stop()

    def test_refused_sign_in_change_is_reported(
        self, qtbot: QtBot, configured: Sandbox
    ) -> None:
        """A sign-in entry that cannot be written raises a failure notice."""
        linux = replace(configured.environment, platform="linux")
        item = FailingLoginItem(linux, ["skillsinc-tray"])
        settings = UserSettings(
            schedule=OFF, startup=StartupOptions(launch_at_login=True)
        )
        tray = make_tray(qtbot, configured, settings, login_item=item)
        try:
            assert [notice.failure for notice in tray.notices] == [True]
            assert "access denied" in tray.notices[0].message
        finally:
            tray.controller.stop()


class TestSyncEdges:
    """Syncs that leave no record, stop early, or overlap the menu."""

    def test_sync_without_a_record_reports_its_output(
        self, qtbot: QtBot, configured: Sandbox
    ) -> None:
        """A failed child that wrote no record is reported with its output."""
        SyncHistory(configured.environment.state_directory, keep=10).append(
            SyncRecord(
                id="old",
                started=datetime.now(UTC) - timedelta(days=1),
                finished=datetime.now(UTC) - timedelta(days=1),
                trigger=Trigger.MANUAL,
                ok=True,
                config=str(configured.config_path),
            )
        )
        runner = _program_runner(configured, "print('boom'); raise SystemExit(3)")
        tray = make_tray(qtbot, configured, runner=runner)
        try:
            record = tray.wait_for_sync(lambda: tray.controller.sync_now(()))

            assert record is None
            assert tray.notices[-1].message == "boom"
            assert tray.controller.tray.icon_state is TrayState.FAILED
        finally:
            tray.controller.stop()

    def test_failure_notices_can_be_turned_off(
        self, qtbot: QtBot, configured: Sandbox
    ) -> None:
        """With failure notifications off, a failed sync stays quiet."""
        settings = UserSettings(
            schedule=OFF, notifications=NotificationOptions(failures=False)
        )
        runner = _program_runner(configured, "raise SystemExit(1)")
        tray = make_tray(qtbot, configured, settings, runner=runner)
        try:
            tray.wait_for_sync(lambda: tray.controller.sync_now(()))

            assert tray.notices == []
        finally:
            tray.controller.stop()

    def test_cancel_and_label_refresh_while_syncing(
        self, qtbot: QtBot, configured: Sandbox
    ) -> None:
        """A refresh during a sync keeps the syncing icon; Cancel stops it."""
        runner = _program_runner(configured, "import time; time.sleep(30)")
        tray = make_tray(qtbot, configured, runner=runner)
        try:

            def start_refresh_cancel() -> None:
                tray.controller.sync_now(())
                tray.controller._refresh_labels()
                assert tray.controller.tray.icon_state is TrayState.SYNCING
                tray.controller.tray.action("cancel").trigger()

            tray.wait_for_sync(start_refresh_cancel)

            assert not tray.controller.runner.busy
        finally:
            tray.controller.stop()

    def test_stop_ends_a_sync_that_does_not_finish(
        self, qtbot: QtBot, configured: Sandbox, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Quitting does not wait forever for a running sync."""
        monkeypatch.setattr(controller_module, "EXIT_WAIT_MS", 0)
        runner = _program_runner(configured, "import time; time.sleep(30)")
        tray = make_tray(qtbot, configured, runner=runner)
        with qtbot.waitSignal(tray.controller.synced, timeout=10_000):
            tray.controller.sync_now(())
            qtbot.waitUntil(lambda: tray.controller.runner.busy)
            tray.controller.stop()

        assert not tray.controller.runner.busy

    def test_timer_before_due_only_reschedules(
        self, qtbot: QtBot, configured: Sandbox
    ) -> None:
        """A timer that fires early starts nothing."""
        settings = UserSettings(schedule=ScheduleOptions(run_at_start=False))
        tray = make_tray(qtbot, configured, settings)
        try:
            tray.controller._on_timer()

            assert not tray.controller.runner.busy
        finally:
            tray.controller.stop()


class TestLabels:
    """Labels and notices in states the journeys do not reach."""

    def test_an_expired_pause_clears_itself(
        self, qtbot: QtBot, configured: Sandbox
    ) -> None:
        """Once a timed pause ends, the menu offers Pause again."""
        settings = UserSettings(schedule=ScheduleOptions(run_at_start=False))
        now = [datetime.now(UTC)]
        tray = make_tray(qtbot, configured, settings, start=False)
        tray.controller._clock = lambda: now[0]
        tray.controller.start()
        try:
            tray.controller.pause(timedelta(hours=1))
            now[0] += timedelta(hours=2)

            tray.controller._refresh_labels()

            assert not tray.controller.tray.state.paused
            assert "pause_1h" in tray.controller.tray.names()
        finally:
            tray.controller.stop()

    def test_open_menu_is_not_rebuilt(self, tray: Tray) -> None:
        """The menu is never rebuilt under the pointer."""
        before = tray.controller.tray.state
        tray.controller.tray.menu.popup(QPoint(0, 0))
        try:
            tray.qtbot.waitUntil(tray.controller.tray.menu.isVisible)
            tray.controller.pause(None)

            assert tray.controller.tray.state is before
        finally:
            tray.controller.tray.menu.hide()

    def test_a_file_that_will_not_open_is_reported(
        self, qtbot: QtBot, configured: Sandbox
    ) -> None:
        """If the desktop cannot open a file, a notice says which."""
        tray = make_tray(qtbot, configured, opens=False)
        try:
            tray.controller.open_state()

            assert tray.notices[-1].failure
            assert "Could not open" in tray.notices[-1].message
        finally:
            tray.controller.stop()

    def test_notices_without_message_support(
        self, tray: Tray, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without balloon support a notice is still emitted."""
        monkeypatch.setattr(
            QSystemTrayIcon, "supportsMessages", staticmethod(lambda: False)
        )

        tray.controller.open_state()
        tray.controller._notify(controller_module.Notice("t", "m"))

        assert tray.notices[-1].message == "m"
