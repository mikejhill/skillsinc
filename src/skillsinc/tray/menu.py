"""The tray icon and its context menu."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from skillsinc.tray.catalog import Catalog
from skillsinc.tray.icons import TrayState, tray_icon

APP_NAME: Final = "SkillSinc"
ALL_PROFILES: Final = "*"
PAUSES: Final = (
    ("pause_1h", "For 1 hour", timedelta(hours=1)),
    ("pause_4h", "For 4 hours", timedelta(hours=4)),
    ("pause_day", "For 24 hours", timedelta(days=1)),
)
CHECK_UPDATES: Final = "Check for updates"


@dataclass(frozen=True, slots=True)
class TrayCommands:
    """Callbacks the tray menu triggers."""

    sync: Callable[[tuple[str, ...]], object]
    """Sync now: empty selects the scheduled profiles, ``("*",)`` all of them."""
    cancel: Callable[[], None]
    pause: Callable[[timedelta | None], None]
    """Pause scheduled syncs for a while, or until resumed (None)."""
    resume: Callable[[], None]
    history: Callable[[], None]
    popup: Callable[[], None]
    settings: Callable[[], None]
    open_config: Callable[[], None]
    open_state: Callable[[], None]
    about: Callable[[], None]
    exit: Callable[[], None]
    update: Callable[[], None] | None = None
    """None: no update entry."""


@dataclass(frozen=True, slots=True)
class MenuState:
    """Everything the menu shows besides its fixed entries."""

    status: str = "No syncs yet"
    next_sync: str = ""
    syncing: bool = False
    paused: bool = False
    catalog: Catalog = field(default_factory=Catalog)
    update_label: str = CHECK_UPDATES
    update_busy: bool = False


class TrayIcon(QSystemTrayIcon):
    """The tray icon: click for recent syncs, right-click for the menu."""

    def __init__(self, commands: TrayCommands, parent: QObject | None = None) -> None:
        super().__init__(tray_icon(TrayState.IDLE), parent)
        self._commands = commands
        self._menu = QMenu()
        self._menu.setToolTipsVisible(True)
        self._actions: dict[str, QAction] = {}
        self._state = MenuState()
        self._icon_state = TrayState.IDLE
        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)
        self.setToolTip(APP_NAME)
        self.update_menu(self._state)

    @property
    def menu(self) -> QMenu:
        """Return the context menu."""
        return self._menu

    @property
    def state(self) -> MenuState:
        """Return what the menu shows now."""
        return self._state

    @property
    def icon_state(self) -> TrayState:
        """Return the state the icon shows."""
        return self._icon_state

    def names(self) -> list[str]:
        """Return the names of every action, in menu order (submenus included)."""
        return list(self._actions)

    def action(self, name: str) -> QAction:
        """Return the named action."""
        return self._actions[name]

    def set_icon_state(self, state: TrayState) -> None:
        """Show ``state`` on the icon."""
        if state is not self._icon_state:
            self._icon_state = state
            self.setIcon(tray_icon(state))

    def update_menu(self, state: MenuState) -> None:
        """Rebuild the menu for ``state``."""
        self._state = state
        self._menu.clear()
        self._actions.clear()
        c = self._commands
        self._info("status", state.status)
        if state.next_sync:
            self._info("next", state.next_sync)
        self._menu.addSeparator()
        self._add("sync", "Sync now", lambda: c.sync(())).setToolTip(
            "Sync the profiles your settings schedule (default: the active ones)"
        )
        self._profiles_menu(state.catalog)
        if state.syncing:
            self._add("cancel", "Cancel sync", c.cancel)
        if state.paused:
            self._add("resume", "Resume scheduled syncs", c.resume)
        else:
            pause = self._menu.addMenu("Pause scheduled syncs")
            for name, label, duration in PAUSES:
                self._add(name, label, lambda d=duration: c.pause(d), pause)
            self._add("pause_forever", "Until I resume", lambda: c.pause(None), pause)
        self._menu.addSeparator()
        self._add("history", "Sync history…", c.history)
        self._add("settings", "Settings…", c.settings)
        self._add("open_config", "Open configuration file", c.open_config)
        self._add("open_state", "Open history and logs folder", c.open_state)
        self._menu.addSeparator()
        if c.update is not None:
            label = "Checking for updates…" if state.update_busy else state.update_label
            self._add("update", label, c.update).setEnabled(not state.update_busy)
        self._add("about", f"About {APP_NAME}", c.about)
        self._add("exit", "Exit", c.exit)
        tooltip = f"{APP_NAME}\n{state.status}"
        self.setToolTip(f"{tooltip}\n{state.next_sync}" if state.next_sync else tooltip)

    def _profiles_menu(self, catalog: Catalog) -> None:
        submenu = self._menu.addMenu("Sync profile")
        self._actions["profiles_menu"] = submenu.menuAction()
        if catalog.error:
            self._info(
                "profiles_error", f"Configuration error: {catalog.error}", submenu
            )
            return
        if not catalog.defined:
            self._info("profiles_none", "No profiles in the configuration", submenu)
            return
        self._add(
            "profile:*",
            "All profiles",
            lambda: self._commands.sync((ALL_PROFILES,)),
            submenu,
        )
        submenu.addSeparator()
        for entry in catalog.profiles:
            label = f"{entry.name} (active here)" if entry.active else entry.name
            action = self._add(
                f"profile:{entry.name}",
                label,
                lambda name=entry.name: self._commands.sync((name,)),
                submenu,
            )
            action.setToolTip(
                f"{entry.description}\n{entry.reason}"
                if entry.description
                else entry.reason
            )

    def _add(
        self,
        name: str,
        text: str,
        callback: Callable[[], object],
        menu: QMenu | None = None,
    ) -> QAction:
        action = (menu or self._menu).addAction(text)
        action.triggered.connect(lambda _=False: callback())
        self._actions[name] = action
        return action

    def _info(self, name: str, text: str, menu: QMenu | None = None) -> QAction:
        action = (menu or self._menu).addAction(text)
        action.setEnabled(False)
        self._actions[name] = action
        return action

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Show recent syncs on a click."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._commands.popup()
