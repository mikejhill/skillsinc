"""The tray icon's menu: entries for each state, and what each one does."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QSystemTrayIcon
from pytestqt.qtbot import QtBot

from skillsinc.tray.catalog import Catalog, ProfileEntry
from skillsinc.tray.icons import BADGES, TrayState, badged, brand_pixmap, tray_icon
from skillsinc.tray.menu import MenuState, TrayCommands, TrayIcon


class Calls:
    """Records each command the menu triggers."""

    def __init__(self) -> None:
        self.log: list[tuple[str, object]] = []

    def commands(self, *, update: bool = False) -> TrayCommands:
        """Return commands that log their name and argument."""

        def record(name: str) -> Callable[..., None]:
            def call(*args: object) -> None:
                self.log.append((name, args[0] if args else None))

            return call

        return TrayCommands(
            sync=record("sync"),
            cancel=record("cancel"),
            pause=record("pause"),
            resume=record("resume"),
            history=record("history"),
            popup=record("popup"),
            settings=record("settings"),
            open_config=record("open_config"),
            open_state=record("open_state"),
            about=record("about"),
            exit=record("exit"),
            update=record("update") if update else None,
        )


CATALOG = Catalog(
    profiles=(
        ProfileEntry("work", "Work machines", active=True, reason="default"),
        ProfileEntry("home", "", active=False, reason="host does not match"),
    ),
    defined=True,
)


@pytest.fixture
def calls() -> Calls:
    """A fresh command log."""
    return Calls()


def _icon(qtbot: QtBot, calls: Calls, *, update: bool = False) -> TrayIcon:
    del qtbot  # the QApplication fixture
    return TrayIcon(calls.commands(update=update))


class TestEntries:
    """Which entries appear, in order, for each state."""

    def test_idle_menu(self, qtbot: QtBot, calls: Calls) -> None:
        """The idle menu offers sync, profiles, pause, windows, about, exit."""
        icon = _icon(qtbot, calls)
        icon.update_menu(
            MenuState(status="Last sync 1 min ago", next_sync="Next in 4 min")
        )

        assert icon.names() == [
            "status",
            "next",
            "sync",
            "profiles_menu",
            "profiles_none",
            "pause_1h",
            "pause_4h",
            "pause_day",
            "pause_forever",
            "history",
            "settings",
            "open_config",
            "open_state",
            "about",
            "exit",
        ]
        assert not icon.action("status").isEnabled(), "Expected an information line"
        assert icon.action("about").text() == "About SkillSinc"
        assert "Next in 4 min" in icon.toolTip()

    def test_profiles_are_listed_with_all_profiles(
        self, qtbot: QtBot, calls: Calls
    ) -> None:
        """Each profile, marked when active here, after an All profiles entry."""
        icon = _icon(qtbot, calls)
        icon.update_menu(MenuState(catalog=CATALOG))

        assert icon.action("profile:*").text() == "All profiles"
        assert icon.action("profile:work").text() == "work (active here)"
        assert icon.action("profile:home").text() == "home"
        assert "host does not match" in icon.action("profile:home").toolTip()

    def test_configuration_error_is_shown_in_the_submenu(
        self, qtbot: QtBot, calls: Calls
    ) -> None:
        """A broken configuration says so instead of listing profiles."""
        icon = _icon(qtbot, calls)
        icon.update_menu(MenuState(catalog=Catalog(error="bad JSON")))

        assert icon.action("profiles_error").text() == "Configuration error: bad JSON"

    def test_syncing_and_paused_states(self, qtbot: QtBot, calls: Calls) -> None:
        """While syncing there is Cancel; while paused there is Resume."""
        icon = _icon(qtbot, calls)
        icon.update_menu(MenuState(syncing=True, paused=True))

        assert "cancel" in icon.names()
        assert "resume" in icon.names()
        assert "pause_1h" not in icon.names()

    def test_update_entry_and_busy_state(self, qtbot: QtBot, calls: Calls) -> None:
        """The update entry appears when wired, disabled while checking."""
        icon = _icon(qtbot, calls, update=True)
        icon.update_menu(MenuState(update_busy=True))

        assert icon.action("update").text() == "Checking for updates…"
        assert not icon.action("update").isEnabled()


class TestCommands:
    """Each entry triggers its command."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("sync", ("sync", ())),
            ("profile:*", ("sync", ("*",))),
            ("profile:home", ("sync", ("home",))),
            ("pause_1h", ("pause", timedelta(hours=1))),
            ("pause_forever", ("pause", None)),
            ("history", ("history", None)),
            ("settings", ("settings", None)),
            ("open_config", ("open_config", None)),
            ("open_state", ("open_state", None)),
            ("about", ("about", None)),
            ("exit", ("exit", None)),
        ],
    )
    def test_entry_triggers_command(
        self, qtbot: QtBot, calls: Calls, name: str, expected: tuple[str, object]
    ) -> None:
        """Triggering an entry calls the matching command."""
        icon = _icon(qtbot, calls)
        icon.update_menu(MenuState(catalog=CATALOG))

        icon.action(name).trigger()

        assert calls.log == [expected]

    def test_cancel_resume_and_update(self, qtbot: QtBot, calls: Calls) -> None:
        """The conditional entries call their commands too."""
        icon = _icon(qtbot, calls, update=True)
        icon.update_menu(MenuState(syncing=True, paused=True))

        for name in ("cancel", "resume", "update"):
            icon.action(name).trigger()

        assert [name for name, _ in calls.log] == ["cancel", "resume", "update"]

    def test_click_opens_the_popup_and_other_activations_do_not(
        self, qtbot: QtBot, calls: Calls
    ) -> None:
        """A left click shows recent syncs; a context click leaves it to the menu."""
        icon = _icon(qtbot, calls)

        icon.activated.emit(QSystemTrayIcon.ActivationReason.Context)
        icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)

        assert calls.log == [("popup", None)]


class TestIcons:
    """Each state has a distinct icon."""

    def test_every_state_has_an_icon(self, qtbot: QtBot) -> None:
        """No state renders as an empty icon."""
        del qtbot
        for state in TrayState:
            assert not tray_icon(state).isNull(), f"Expected an icon for {state}"

    def test_badge_marks_the_lower_right_corner(self, qtbot: QtBot) -> None:
        """A badge recolors the corner and leaves the top left alone."""
        del qtbot
        plain = brand_pixmap(32).toImage()
        marked = badged(brand_pixmap(32), BADGES["failed"]).toImage()

        corner = marked.pixelColor(26, 26)
        assert corner.red() > 200, f"Got {corner.name()}"
        assert corner.green() < 100, f"Got {corner.name()}"
        assert marked.pixelColor(2, 2) == plain.pixelColor(2, 2)

    def test_pixmaps_are_scaled_to_the_requested_size(self, qtbot: QtBot) -> None:
        """A size between exports is scaled from the nearest larger one."""
        del qtbot
        image: QImage = brand_pixmap(40).toImage()

        assert (image.width(), image.height()) == (40, 40)

    def test_icon_state_changes(self, qtbot: QtBot, calls: Calls) -> None:
        """set_icon_state switches the shown state."""
        icon = _icon(qtbot, calls)

        icon.set_icon_state(TrayState.FAILED)
        icon.set_icon_state(TrayState.FAILED)

        assert icon.icon_state is TrayState.FAILED
