"""Checking for a newer release and installing it from the tray."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.conftest import Sandbox
from tests.tray.conftest import UV, Launches, Tray, make_tray, make_updater

from skillsinc.selfupdate import Installation, UpdateError
from skillsinc.tray import updater as updater_module
from skillsinc.tray.menu import CHECK_UPDATES
from skillsinc.tray.notice import Notice
from skillsinc.tray.updater import Updater
from skillsinc.upgrades import current_version
from skillsinc.usersettings import ScheduleOptions, UpdateOptions, UserSettings

NEWER = "999.0.0"
CHECK_TIMEOUT_MS = 10_000


def _checked(
    qtbot: QtBot, updater: Updater, *, manual: bool, install: bool = False
) -> list[Notice]:
    """Run one check to completion and return the notices it sent."""
    notices: list[Notice] = []
    updater.notified.connect(notices.append)
    updater.check(manual=manual, install=install)
    qtbot.waitUntil(lambda: not updater.checking, timeout=CHECK_TIMEOUT_MS)
    return notices


class TestCheck:
    """A check reports what PyPI has and changes the menu entry."""

    def test_up_to_date(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A manual check with nothing newer says so; the entry stays a check."""
        updater = make_updater(tmp_path)

        notices = _checked(qtbot, updater, manual=True)

        assert [notice.message for notice in notices] == [
            f"SkillSinc {current_version()} is up to date."
        ]
        assert (updater.available, updater.label()) == (False, CHECK_UPDATES)
        assert updater.summary().endswith("(up to date)")

    def test_automatic_up_to_date_is_quiet(self, qtbot: QtBot, tmp_path: Path) -> None:
        """An automatic check with nothing newer sends no notice."""
        assert _checked(qtbot, make_updater(tmp_path), manual=False) == []

    def test_newer_release(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A newer release is announced and becomes the menu entry."""
        updater = make_updater(tmp_path, NEWER)

        notices = _checked(qtbot, updater, manual=False)

        assert len(notices) == 1
        assert f"SkillSinc {NEWER} is available" in notices[0].message
        assert updater.release is not None
        assert updater.label() == f"Install SkillSinc {NEWER}"
        assert updater.summary() == f"{NEWER} (update available)"

    @pytest.mark.parametrize(("manual", "count"), [(True, 1), (False, 0)])
    def test_failure(
        self, qtbot: QtBot, tmp_path: Path, *, manual: bool, count: int
    ) -> None:
        """A failed check is reported only when the user asked for it."""
        updater = make_updater(tmp_path, OSError("offline"))

        notices = _checked(qtbot, updater, manual=manual)

        assert len(notices) == count
        assert all(notice.failure for notice in notices)
        assert updater.summary() == "not checked yet"

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


class TestInstall:
    """Installing writes the script and starts it, or says why not."""

    def test_starts_the_script(self, tmp_path: Path) -> None:
        """A supported installation starts the upgrade script."""
        launches = Launches()
        updater = make_updater(tmp_path, launches=launches)

        assert updater.install()

        [(program, arguments)] = launches.started
        assert program in {"/bin/sh", "cmd.exe"}
        script = Path(arguments[-1])
        assert " ".join(UV.command) in script.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        ("installation", "launches", "folder_is_file", "expected"),
        [
            (Installation("manual", reason="a working copy"), True, False, "yourself"),
            (UV, True, True, "Could not update"),
            (UV, False, False, "Could not start"),
        ],
    )
    def test_failures_are_notified(
        self,
        tmp_path: Path,
        installation: Installation,
        *,
        launches: bool,
        folder_is_file: bool,
        expected: str,
    ) -> None:
        """Manual installs, unwritable folders and failed launches say why."""
        folder = tmp_path / "state"
        if folder_is_file:
            folder.write_text("", encoding="utf-8")
        updater = make_updater(
            folder, installation=installation, launches=Launches(succeeds=launches)
        )
        notices: list[Notice] = []
        updater.notified.connect(notices.append)

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


class TestController:
    """The tray's menu, timers, and About window use the updater."""

    def test_menu_checks_then_installs(
        self, qtbot: QtBot, configured: Sandbox, tmp_path: Path
    ) -> None:
        """The entry checks first; once a release is found it installs and exits."""
        launches = Launches()
        tray = make_tray(
            qtbot,
            configured,
            updater=make_updater(tmp_path, NEWER, launches=launches),
        )
        try:
            controller = tray.controller
            controller.update_command()
            qtbot.waitUntil(lambda: controller.updater.available, timeout=5000)
            assert launches.started == []

            controller.update_command()

            assert len(launches.started) == 1
            assert tray.quit_calls == [True]
        finally:
            tray.controller.stop()

    def test_install_waits_for_the_running_sync(
        self, qtbot: QtBot, configured: Sandbox, tmp_path: Path
    ) -> None:
        """An install asked for during a sync starts when the sync ends."""
        launches = Launches()
        tray = make_tray(
            qtbot, configured, updater=make_updater(tmp_path, launches=launches)
        )
        try:

            def sync_then_install() -> None:
                tray.controller.sync_now(())
                tray.controller.install_update()
                assert launches.started == []

            tray.wait_for_sync(sync_then_install)

            assert any("after this sync" in n.message for n in tray.notices)
            assert len(launches.started) == 1
            assert tray.quit_calls == [True]
        finally:
            tray.controller.stop()

    def test_failed_install_keeps_the_tray(
        self, qtbot: QtBot, configured: Sandbox, tmp_path: Path
    ) -> None:
        """A manual installation is reported and the tray keeps running."""
        manual = Installation("manual", reason="a working copy")
        tray = make_tray(
            qtbot, configured, updater=make_updater(tmp_path, installation=manual)
        )
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

            assert tray.controller.updater.available is check
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
