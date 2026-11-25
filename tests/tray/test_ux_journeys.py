"""User journeys through the tray: real menu actions, real sync processes.

Each journey drives the controller the way a person would (menu entries,
popup buttons, window controls) and checks only what they would observe: the
menu's text, the icon state, notifications, windows, and the history.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pytestqt.qtbot import QtBot
from tests.conftest import Sandbox
from tests.tray.conftest import Tray, add_profiles, make_tray

from skillsinc.history import Trigger
from skillsinc.tray.icons import TrayState
from skillsinc.tray.timing import HEARTBEAT
from skillsinc.usersettings import (
    NotificationOptions,
    ScheduleOptions,
    SettingsStore,
    StartupOptions,
    UserSettings,
)

pytestmark = pytest.mark.ux


class TestSyncFromTheMenu:
    """Sync now, and per profile, from the tray menu."""

    def test_sync_now_records_a_tray_run_and_updates_the_menu(self, tray: Tray) -> None:
        """Sync now runs a sync, then the menu says when and what it did."""
        assert tray.controller.tray.state.status == "No syncs yet"

        record = tray.wait_for_sync(tray.controller.tray.action("sync").trigger)

        assert record is not None
        assert record.ok, f"Got {record}"
        assert record.trigger is Trigger.TRAY
        status = tray.controller.tray.state.status
        assert status.startswith("Last sync"), status
        assert "create" in status, status
        assert tray.controller.tray.icon_state is TrayState.IDLE
        assert tray.notices == [], "Expected no notification by default"

    def test_profile_submenu_syncs_that_profile(
        self, qtbot: QtBot, configured: Sandbox
    ) -> None:
        """Picking a profile syncs only it; All profiles passes '*'."""
        add_profiles(configured.config_path, {"work": {}, "home": {"default": False}})
        tray = make_tray(qtbot, configured)
        try:
            assert tray.controller.tray.action("profile:work").text() == (
                "work (active here)"
            )
            home = tray.wait_for_sync(
                tray.controller.tray.action("profile:home").trigger
            )
            every = tray.wait_for_sync(tray.controller.tray.action("profile:*").trigger)

            assert home is not None
            assert home.profiles == ("home",)
            assert every is not None
            assert every.profiles == ("work", "home")
        finally:
            tray.controller.stop()

    def test_failure_turns_the_icon_red_and_notifies(
        self, qtbot: QtBot, sandbox: Sandbox
    ) -> None:
        """A failing sync marks the icon and raises a notification."""
        sandbox.config_path.parent.mkdir(parents=True)
        sandbox.config_path.write_text("{ broken", encoding="utf-8")
        tray = make_tray(qtbot, sandbox)
        try:
            assert (
                "Configuration error"
                in tray.controller.tray.action("profiles_error").text()
            )

            record = tray.wait_for_sync(lambda: tray.controller.sync_now(()))

            assert record is not None
            assert not record.ok
            assert tray.controller.tray.icon_state is TrayState.FAILED
            assert [notice.failure for notice in tray.notices] == [True]
            assert "failed" in tray.controller.tray.state.status
        finally:
            tray.controller.stop()

    def test_change_notifications_when_enabled(
        self, qtbot: QtBot, configured: Sandbox
    ) -> None:
        """With change notifications on, a sync that wrote files says so."""
        settings = UserSettings(
            schedule=ScheduleOptions(enabled=False),
            notifications=NotificationOptions(failures=True, changes=True),
        )
        tray = make_tray(qtbot, configured, settings)
        try:
            tray.wait_for_sync(lambda: tray.controller.sync_now(()))

            assert [notice.title for notice in tray.notices] == ["SkillSinc synced"]
        finally:
            tray.controller.stop()

    def test_cancel_entry_appears_while_syncing(self, tray: Tray) -> None:
        """While a sync runs the menu offers Cancel and says what runs."""

        def start_and_check() -> None:
            tray.controller.sync_now(())
            _assert_syncing(tray)

        tray.wait_for_sync(start_and_check)


def _assert_syncing(tray: Tray) -> None:
    state = tray.controller.tray.state
    assert state.syncing, "Expected the syncing state"
    assert state.status == "Syncing default profiles…", state.status
    assert "cancel" in tray.controller.tray.names()
    assert tray.controller.tray.icon_state is TrayState.SYNCING


class TestSchedule:
    """The tray's own schedule: start run, interval, pause, settings edits."""

    def test_start_run_then_next_sync_is_an_interval_later(
        self, qtbot: QtBot, configured: Sandbox
    ) -> None:
        """With run_at_start, the tray syncs at once and schedules the next."""
        settings = UserSettings(
            schedule=ScheduleOptions(interval=timedelta(minutes=10))
        )
        tray = make_tray(qtbot, configured, settings, start=False)
        try:
            record = tray.wait_for_sync(tray.controller.start)

            assert record is not None
            assert record.trigger is Trigger.TRAY_STARTUP
            assert tray.controller.tray.state.next_sync == "Next sync in 10 min"
            assert tray.controller.runner.pending == ()
        finally:
            tray.controller.stop()

    def test_due_sync_runs_on_the_schedule(
        self, qtbot: QtBot, configured: Sandbox
    ) -> None:
        """Once the interval has passed, the timer starts a scheduled sync."""
        settings = UserSettings(
            schedule=ScheduleOptions(interval=timedelta(minutes=1), run_at_start=False)
        )
        now = [configured_start()]
        tray = make_tray(qtbot, configured, settings, start=False)
        tray.controller._clock = lambda: now[0]
        tray.controller._started_at = now[0]
        tray.controller.start()
        try:
            assert tray.controller.tray.state.next_sync == "Next sync in 1 min"
            now[0] += timedelta(minutes=1)

            record = tray.wait_for_sync(tray.controller._on_timer)

            assert record is not None
            assert record.trigger is Trigger.TRAY_SCHEDULED
        finally:
            tray.controller.stop()

    def test_pause_and_resume(self, qtbot: QtBot, configured: Sandbox) -> None:
        """Pausing shows when syncs resume and greys the icon; Resume undoes it."""
        settings = UserSettings(schedule=ScheduleOptions(run_at_start=False))
        tray = make_tray(qtbot, configured, settings)
        try:
            self._pause_and_resume(tray)
        finally:
            tray.controller.stop()

    @staticmethod
    def _pause_and_resume(tray: Tray) -> None:
        tray.controller.tray.action("pause_1h").trigger()

        assert tray.controller.tray.state.next_sync.startswith(
            "Scheduled syncs paused until"
        )
        assert tray.controller.tray.icon_state is TrayState.PAUSED
        tray.controller.tray.action("resume").trigger()
        tray.controller.tray.action("pause_forever").trigger()
        assert tray.controller.tray.state.next_sync == "Scheduled syncs paused"
        tray.controller.tray.action("resume").trigger()
        assert tray.controller.tray.state.next_sync == "Next sync in 5 min"
        assert tray.controller.tray.icon_state is TrayState.IDLE

    def test_editing_settings_json_applies_at_once(self, tray: Tray) -> None:
        """Turning the schedule on in settings.json updates the menu."""
        store = SettingsStore.default(tray.sandbox.environment)

        store.save(
            UserSettings(
                schedule=ScheduleOptions(
                    interval=timedelta(hours=2), run_at_start=False
                )
            )
        )

        tray.qtbot.waitUntil(
            lambda: tray.controller.tray.state.next_sync == "Next sync in 2 h",
            timeout=5000,
        )

    def test_invalid_settings_keep_the_previous_ones_and_notify(
        self, tray: Tray
    ) -> None:
        """A broken edit is reported and the running settings stay."""
        store = SettingsStore.default(tray.sandbox.environment)
        before = tray.controller.settings

        store.path.write_text('{"schedule": {"interval": "soon"}}', encoding="utf-8")
        tray.controller.reload_settings()

        assert tray.controller.settings == before
        assert tray.notices
        assert tray.notices[-1].failure
        assert "previous settings" in tray.notices[-1].message

    def test_sign_in_start_follows_settings(self, tray: Tray) -> None:
        """launch_at_login registers the sign-in start, and removing it undoes it."""
        store = SettingsStore.default(tray.sandbox.environment)
        entry = (
            tray.sandbox.environment.config_home
            / "autostart"
            / "skillsinc-tray.desktop"
        )
        if not tray.sandbox.environment.platform.startswith("linux"):
            pytest.skip("autostart entries are Linux; Windows uses the Run key")

        store.save(UserSettings(startup=StartupOptions(launch_at_login=True)))
        tray.controller.reload_settings()
        assert entry.is_file()
        store.save(UserSettings(startup=StartupOptions(launch_at_login=False)))
        tray.controller.reload_settings()
        assert not entry.exists()


def configured_start() -> datetime:
    """Return the wall-clock time now: the sync child stamps real times."""
    return datetime.now(UTC)


class TestWindows:
    """Popup, history window, settings file, About, and folders."""

    def test_popup_lists_recent_syncs_and_its_buttons_work(self, tray: Tray) -> None:
        """A click shows the newest runs; Sync now and History… act."""
        tray.wait_for_sync(lambda: tray.controller.sync_now(()))

        tray.controller.tray.activated.emit(
            tray.controller.tray.ActivationReason.Trigger
        )

        popup = tray.controller.popup
        assert popup.isVisible()
        assert popup.list.count() == 1
        assert "Sync now" in popup.list.item(0).text()
        tray.wait_for_sync(popup.sync_button.click)
        assert not popup.isVisible(), "Expected the popup to close on a button"
        tray.controller.show_popup()
        popup.history_button.click()
        assert tray.controller.history_window.isVisible()

    def test_history_window_shows_rows_and_details(self, tray: Tray) -> None:
        """Each run is a row; selecting one shows its changes."""
        tray.wait_for_sync(lambda: tray.controller.sync_now(()))
        tray.wait_for_sync(lambda: tray.controller.sync_now(()))

        tray.controller.show_history()

        window = tray.controller.history_window
        assert window.model.rowCount() == 2
        assert window.model.data(window.model.index(0, 5)) == "no changes"
        window.table.selectRow(1)
        text = window.details.toPlainText()
        assert "Trigger: Sync now" in text
        assert "Changes:" in text
        assert window.empty.isHidden()

    def test_empty_history_window_explains_itself(self, tray: Tray) -> None:
        """Before any sync the window says how to start one."""
        tray.controller.show_history()

        assert tray.controller.history_window.empty.isVisible()

    def test_settings_entry_creates_and_opens_settings_json(self, tray: Tray) -> None:
        """Settings… opens settings.json, writing it first if missing."""
        store = SettingsStore.default(tray.sandbox.environment)
        store.path.unlink()

        tray.controller.tray.action("settings").trigger()

        assert tray.opened == [store.path]
        assert store.exists()

    def test_open_config_and_state_folder(self, tray: Tray) -> None:
        """The open entries hand the right paths to the desktop."""
        tray.controller.tray.action("open_config").trigger()
        tray.controller.tray.action("open_state").trigger()

        assert tray.opened == [
            tray.sandbox.config_path,
            tray.sandbox.environment.state_directory,
        ]

    def test_about_shows_the_version_and_paths(self, tray: Tray) -> None:
        """About names the release and where things live."""
        tray.controller.handle(["show", "about"])

        dialog = tray.controller.about_dialog
        assert dialog is not None
        assert dialog.isVisible()
        assert dialog.facts.version in dialog.version_label.text()
        assert str(tray.sandbox.config_path) in dialog.details.text()
        dialog.close()

    def test_forwarded_requests(self, tray: Tray) -> None:
        """Requests from other launches open windows, sync, or quit."""
        tray.controller.handle(["show", "history"])
        assert tray.controller.history_window.isVisible()
        tray.controller.handle([])
        assert tray.controller.popup.isVisible()
        tray.controller.handle(["show", "settings"])
        tray.controller.handle(["bogus"])
        tray.wait_for_sync(lambda: tray.controller.handle(["sync"]))
        tray.controller.handle(["quit"])

        assert tray.quit_calls == [True]
        assert tray.history()[0].trigger is Trigger.TRAY

    def test_exit_entry_quits(self, tray: Tray) -> None:
        """Exit stops the tray and quits."""
        tray.controller.tray.action("exit").trigger()

        assert tray.quit_calls == [True]


def test_heartbeat_is_a_minute() -> None:
    """The schedule rechecks at least once a minute (sleep and clock changes)."""
    assert timedelta(seconds=60) == HEARTBEAT
