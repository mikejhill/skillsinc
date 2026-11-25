"""Checking for a newer release and installing it from the tray."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.conftest import Sandbox
from tests.tray.conftest import UV, Launches, Tray, make_tray, make_updater

from skillsinc.selfupdate import RESULT_FILE, Installation, UpdateError, UpdateResult
from skillsinc.tray import updater as updater_module
from skillsinc.tray.notice import Notice
from skillsinc.tray.updater import Updater
from skillsinc.upgrades import current_version
from skillsinc.usersettings import (
    ScheduleOptions,
    SettingsStore,
    UpdateOptions,
    UserSettings,
)

NEWER = "999.0.0"
CHECK_TIMEOUT_MS = 10_000
AUTOMATIC = UserSettings(
    schedule=ScheduleOptions(enabled=False, run_at_start=False),
    updates=UpdateOptions(check=True, install=True),
)


def _checked(
    qtbot: QtBot, updater: Updater, *, manual: bool, install: bool = False
) -> list[Notice]:
    """Run one check to completion and return the notices it sent."""
    notices: list[Notice] = []
    updater.notified.connect(notices.append)
    updater.check(manual=manual, install=install)
    qtbot.waitUntil(lambda: not updater.checking, timeout=CHECK_TIMEOUT_MS)
    return notices


def _record(
    folder: Path,
    version: str,
    code: int | None,
    note: str = "",
    *,
    reported: bool = False,
) -> None:
    """Write what the helper records after an upgrade."""
    folder.mkdir(parents=True, exist_ok=True)
    data = {"version": version, "returncode": code, "note": note, "reported": reported}
    (folder / RESULT_FILE).write_bytes(json.dumps(data).encode())


def _notices(updater: Updater) -> list[Notice]:
    notices: list[Notice] = []
    updater.notified.connect(notices.append)
    return notices


class TestCheck:
    """A check reports what PyPI has."""

    def test_up_to_date(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A manual check with nothing newer says so."""
        updater = make_updater(tmp_path)

        notices = _checked(qtbot, updater, manual=True)

        assert [notice.message for notice in notices] == [
            f"SkillSinc {current_version()} is up to date."
        ]
        assert updater.available == ""
        assert updater.summary().endswith("(up to date)")

    def test_automatic_up_to_date_is_quiet(self, qtbot: QtBot, tmp_path: Path) -> None:
        """An automatic check with nothing newer sends no notice."""
        assert _checked(qtbot, make_updater(tmp_path), manual=False) == []

    def test_newer_release(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A newer release is announced and becomes available."""
        updater = make_updater(tmp_path, NEWER)

        notices = _checked(qtbot, updater, manual=False)

        assert len(notices) == 1
        assert f"SkillSinc {NEWER} is available" in notices[0].message
        assert updater.release is not None
        assert updater.available == NEWER
        assert updater.summary() == f"{NEWER} (update available)"

    @pytest.mark.parametrize(("manual", "count"), [(True, 1), (False, 0)])
    @pytest.mark.parametrize("error", [OSError("offline"), UpdateError("bad feed")])
    def test_failure(
        self,
        qtbot: QtBot,
        tmp_path: Path,
        error: Exception,
        *,
        manual: bool,
        count: int,
    ) -> None:
        """A failed check is reported only when the user asked for it."""
        updater = make_updater(tmp_path, error)

        notices = _checked(qtbot, updater, manual=manual)

        assert len(notices) == count
        assert all(notice.failure for notice in notices)
        assert updater.summary() == "not checked yet"

    def test_unexpected_error_ends_the_check(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """Any exception in the worker becomes a failed check, not a hang."""
        updater = make_updater(tmp_path, RuntimeError("boom"))

        notices = _checked(qtbot, updater, manual=True)

        assert [notice.failure for notice in notices] == [True]
        assert "boom" in notices[0].message

    def test_failed_check_keeps_the_last_release(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """A failed check neither forgets a release found nor installs it."""
        updater = make_updater(tmp_path, NEWER)
        _checked(qtbot, updater, manual=False)
        updater._feed = make_updater(tmp_path, OSError("offline"))._feed
        requested: list[bool] = []
        updater.install_requested.connect(lambda: requested.append(True))

        _checked(qtbot, updater, manual=False, install=True)

        assert updater.available == NEWER
        assert requested == []

    def test_second_check_while_checking_is_ignored(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """Asking again while a check runs starts nothing new."""
        updater = make_updater(tmp_path)
        changes: list[bool] = []
        updater.changed.connect(lambda: changes.append(updater.checking))

        updater.check(manual=False)
        updater.check(manual=False)
        qtbot.waitUntil(lambda: not updater.checking, timeout=CHECK_TIMEOUT_MS)

        assert changes == [True, False]

    def test_install_requested_after_automatic_install_check(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """With automatic installs on, a newer release asks for an install."""
        updater = make_updater(tmp_path, NEWER)

        with qtbot.waitSignal(updater.install_requested, timeout=CHECK_TIMEOUT_MS):
            notices = _checked(qtbot, updater, manual=False, install=True)

        assert notices == []

    def test_failed_release_is_not_installed_again(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """A release whose upgrade failed is offered, not installed automatically."""
        _record(tmp_path, NEWER, 1)
        updater = make_updater(tmp_path, NEWER)
        requested: list[bool] = []
        updater.install_requested.connect(lambda: requested.append(True))

        notices = _checked(qtbot, updater, manual=False, install=True)

        assert requested == []
        assert "Choose Install" in notices[0].message
        assert updater.failed_before(NEWER)
        assert not updater.failed_before("998.0.0")


class TestInstall:
    """Installing starts the helper, or says why not."""

    def test_nothing_to_install(self, tmp_path: Path) -> None:
        """Without a newer release found, nothing starts."""
        launches = Launches()

        assert not make_updater(tmp_path, launches=launches).install()
        assert launches.started == []

    def test_starts_the_helper(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A supported installation starts the helper with the upgrade plan."""
        launches = Launches()
        updater = make_updater(tmp_path, NEWER, launches=launches)
        _checked(qtbot, updater, manual=False)

        assert updater.install()

        [(_program, arguments)] = launches.started
        assert arguments[:2] == ("-I", "-c")
        spec = json.loads(arguments[-1])
        assert spec["upgrade"] == list(UV.command)
        assert spec["environment"] == dict(UV.environment)
        assert spec["version"] == NEWER
        assert spec["relaunch"] == ["/py/python", "-m", "skillsinc", "tray"]
        assert spec["result"] == str(tmp_path / RESULT_FILE)

    @pytest.mark.parametrize(
        ("installation", "launches", "expected"),
        [
            (Installation("manual", reason="a working copy"), True, "yourself"),
            (UV, False, "Could not start"),
        ],
    )
    def test_failures_are_notified(
        self,
        qtbot: QtBot,
        tmp_path: Path,
        installation: Installation,
        *,
        launches: bool,
        expected: str,
    ) -> None:
        """Manual installs and failed launches say why."""
        updater = make_updater(
            tmp_path,
            NEWER,
            installation=installation,
            launches=Launches(succeeds=launches),
        )
        _checked(qtbot, updater, manual=False)
        notices = _notices(updater)

        assert not updater.install()

        assert len(notices) == 1
        assert expected in notices[0].message
        assert notices[0].failure

    @pytest.mark.parametrize(
        ("platform", "option"),
        [
            ("linux", {"start_new_session": True}),
            ("win32", {"creationflags": 0x08000200}),
        ],
    )
    def test_start_detached(
        self,
        monkeypatch: pytest.MonkeyPatch,
        platform: str,
        option: dict[str, object],
    ) -> None:
        """The default launcher starts a windowless process that outlives the tray."""
        seen: list[tuple[list[str], dict[str, object]]] = []

        def popen(command: list[str], **options: object) -> None:
            seen.append((command, options))

        monkeypatch.setattr(updater_module.subprocess, "Popen", popen)

        assert updater_module.start_detached("/bin/sh", ("x",), platform)
        [(command, options)] = seen
        assert command == ["/bin/sh", "x"]
        assert option.items() <= options.items()

    def test_start_detached_failure(self, tmp_path: Path) -> None:
        """A program that cannot start is a False, not an exception."""
        assert not updater_module.start_detached(str(tmp_path / "missing"), ())


class TestLastAttempt:
    """At start the tray reports what the helper recorded, once."""

    def test_nothing_recorded(self, tmp_path: Path) -> None:
        """Without a record there is nothing to say."""
        updater = make_updater(tmp_path)
        notices = _notices(updater)

        updater.report_last_attempt()

        assert notices == []

    def test_success_is_announced_once(self, tmp_path: Path) -> None:
        """An upgrade to the running version is announced and forgotten."""
        _record(tmp_path, current_version(), 0)
        updater = make_updater(tmp_path)
        notices = _notices(updater)

        updater.report_last_attempt()
        updater.report_last_attempt()

        assert [n.message for n in notices] == [
            f"SkillSinc was updated to {current_version()}."
        ]
        assert UpdateResult.read(tmp_path) is None

    def test_superseded_failure_is_forgotten(self, tmp_path: Path) -> None:
        """A failure for a version no newer than this one is cleared quietly."""
        _record(tmp_path, "0.0.1", 1)
        updater = make_updater(tmp_path)
        notices = _notices(updater)

        updater.report_last_attempt()

        assert notices == []
        assert UpdateResult.read(tmp_path) is None

    @pytest.mark.parametrize(
        ("code", "note", "why"),
        [
            (1, "", "the installer exited with 1"),
            (None, "", "the installer did not run"),
            (None, "the tray did not exit", "the tray did not exit"),
            (0, "", "the installer finished but"),
        ],
    )
    def test_failure_is_reported_once(
        self, tmp_path: Path, code: int | None, note: str, why: str
    ) -> None:
        """A failed upgrade says why, once, and the record stays to block retries."""
        _record(tmp_path, NEWER, code, note)
        updater = make_updater(tmp_path)
        notices = _notices(updater)

        updater.report_last_attempt()
        updater.report_last_attempt()

        assert len(notices) == 1
        assert notices[0].failure
        assert why in notices[0].message
        assert "not be retried automatically" in notices[0].message
        result = UpdateResult.read(tmp_path)
        assert result is not None
        assert result.reported
        assert updater.failed_before(NEWER)


class TestController:
    """The tray's menu, timers, and About window use the updater."""

    def _found(
        self,
        qtbot: QtBot,
        sandbox: Sandbox,
        folder: Path,
        settings: UserSettings | None = None,
        installation: Installation = UV,
    ) -> tuple[Tray, Launches]:
        """Return a tray whose updater has already found a newer release."""
        launches = Launches()
        updater = make_updater(
            folder, NEWER, installation=installation, launches=launches
        )
        _checked(qtbot, updater, manual=False)
        return make_tray(qtbot, sandbox, settings, updater=updater), launches

    def test_menu_checks_then_installs(
        self, qtbot: QtBot, configured: Sandbox, tmp_path: Path
    ) -> None:
        """Check finds a release; Install starts the helper and exits."""
        launches = Launches()
        tray = make_tray(
            qtbot,
            configured,
            updater=make_updater(tmp_path, NEWER, launches=launches),
        )
        try:
            controller = tray.controller
            controller.check_updates()
            qtbot.waitUntil(lambda: bool(controller.updater.available), timeout=5000)
            assert launches.started == []

            controller.install_update()

            assert len(launches.started) == 1
            assert tray.quit_calls == [True]
        finally:
            tray.controller.stop()

    def test_install_waits_for_queued_syncs(
        self, qtbot: QtBot, configured: Sandbox, tmp_path: Path
    ) -> None:
        """An install asked for during a sync starts once no sync is left."""
        tray, launches = self._found(qtbot, configured, tmp_path)
        try:

            def sync_then_install() -> None:
                tray.controller.sync_now(())
                tray.controller.install_update()
                tray.controller.install_update()
                assert launches.started == []

            tray.wait_for_sync(sync_then_install)

            waits = [n for n in tray.notices if "after the running sync" in n.message]
            assert len(waits) == 1
            assert len(launches.started) == 1
            assert tray.quit_calls == [True]
        finally:
            tray.controller.stop()

    def test_automatic_install_dropped_when_consent_is_withdrawn(
        self, qtbot: QtBot, configured: Sandbox, tmp_path: Path
    ) -> None:
        """Turning automatic installs off while one waits cancels it."""
        tray, launches = self._found(qtbot, configured, tmp_path, AUTOMATIC)
        try:
            store = SettingsStore.default(configured.environment)

            def sync_then_withdraw() -> None:
                tray.controller.sync_now(())
                tray.controller.install_update(manual=False)
                store.save(
                    UserSettings(
                        schedule=AUTOMATIC.schedule,
                        updates=UpdateOptions(check=True, install=False),
                    )
                )
                tray.controller.reload_settings()

            tray.wait_for_sync(sync_then_withdraw)

            assert launches.started == []
            assert tray.quit_calls == []
        finally:
            tray.controller.stop()

    def test_install_waits_for_settings(
        self, qtbot: QtBot, configured: Sandbox, tmp_path: Path
    ) -> None:
        """An install waits while Settings is open, then runs when it closes."""
        tray, launches = self._found(qtbot, configured, tmp_path)
        try:
            tray.controller.show_settings()
            window = tray.controller.settings_window
            assert window is not None

            tray.controller.install_update()

            assert launches.started == []
            assert any("close Settings" in n.message for n in tray.notices)

            window.reject()

            assert len(launches.started) == 1
            assert tray.quit_calls == [True]
        finally:
            tray.controller.stop()

    def test_failed_install_keeps_the_tray(
        self, qtbot: QtBot, configured: Sandbox, tmp_path: Path
    ) -> None:
        """A manual installation is reported and the tray keeps running."""
        manual = Installation("manual", reason="a working copy")
        tray, _launches = self._found(qtbot, configured, tmp_path, installation=manual)
        try:
            tray.controller.install_update()

            assert tray.quit_calls == []
            assert any("yourself" in notice.message for notice in tray.notices)
        finally:
            tray.controller.stop()

    @pytest.mark.parametrize("check", [True, False])
    def test_automatic_check_follows_settings(
        self, qtbot: QtBot, configured: Sandbox, tmp_path: Path, *, check: bool
    ) -> None:
        """The timed check runs only when updates.check is on."""
        settings = UserSettings(
            schedule=ScheduleOptions(enabled=False, run_at_start=False),
            updates=UpdateOptions(check=check),
        )
        tray = make_tray(
            qtbot, configured, settings, updater=make_updater(tmp_path, NEWER)
        )
        try:
            tray.controller._automatic_update_check()
            qtbot.waitUntil(lambda: not tray.controller.updater.checking, timeout=5000)

            assert bool(tray.controller.updater.available) is check
        finally:
            tray.controller.stop()

    def test_automatic_install(
        self, qtbot: QtBot, configured: Sandbox, tmp_path: Path
    ) -> None:
        """With automatic installs on, the timed check installs and exits."""
        launches = Launches()
        tray = make_tray(
            qtbot,
            configured,
            AUTOMATIC,
            updater=make_updater(tmp_path, NEWER, launches=launches),
        )
        try:
            tray.controller._automatic_update_check()
            qtbot.waitUntil(lambda: bool(launches.started), timeout=5000)

            assert tray.quit_calls == [True]
        finally:
            tray.controller.stop()

    def test_start_reports_the_last_attempt(
        self, qtbot: QtBot, configured: Sandbox, tmp_path: Path
    ) -> None:
        """A failed upgrade recorded before the tray started is reported."""
        _record(tmp_path, NEWER, 2)
        tray = make_tray(qtbot, configured, updater=make_updater(tmp_path))
        try:
            assert any(n.failure and "did not work" in n.message for n in tray.notices)
        finally:
            tray.controller.stop()

    def test_about_shows_the_latest_release(self, tray: Tray, qtbot: QtBot) -> None:
        """About reports the last check's result."""
        tray.controller.updater.check(manual=False)
        qtbot.waitUntil(lambda: not tray.controller.updater.checking, timeout=5000)

        tray.controller.show_about()

        dialog = tray.controller.about_dialog
        assert dialog is not None
        rows = dict(dialog.facts.rows())
        assert rows["Latest release"].endswith("(up to date)")


def test_update_error_is_an_app_error() -> None:
    """Update failures share the application's error type."""
    assert str(UpdateError("could not check")) == "could not check"
