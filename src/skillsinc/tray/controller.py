"""The tray application: schedule, menu, windows, and notifications in one place."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Protocol

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from skillsinc.config import ConfigLoader
from skillsinc.history import SyncRecord, Trigger
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.tray.about import AboutDialog, AboutFacts
from skillsinc.tray.catalog import Catalog, CatalogReader
from skillsinc.tray.history_view import HistoryPopup, HistoryWindow
from skillsinc.tray.icons import TrayState
from skillsinc.tray.menu import MenuState, TrayCommands, TrayIcon
from skillsinc.tray.runner import SyncOutcome, SyncRequest, SyncRunner
from skillsinc.tray.settings_window import SettingsWindow
from skillsinc.tray.startup import LoginItem
from skillsinc.tray.timing import ScheduleState, describe_delta
from skillsinc.upgrades import current_version
from skillsinc.usersettings import SettingsFileError, SettingsStore, UserSettings

logger = logging.getLogger(__name__)

POPUP_LIMIT: Final = 8
# Re-read settings.json this long after the last change event
SETTINGS_DEBOUNCE_MS: Final = 300
# Refresh the "3 min ago" labels this often while the menu is closed
LABEL_REFRESH_MS: Final = 30_000
# On exit, wait this long for a running sync to finish before stopping it
EXIT_WAIT_MS: Final = 30_000


class Opener(Protocol):
    """Open a file or folder with the desktop's default application."""

    def __call__(self, path: Path) -> bool:
        """Return True if the desktop accepted the request."""


def open_with_desktop(path: Path) -> bool:
    """Open ``path`` with the default application."""
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


@dataclass(frozen=True, slots=True)
class Notice:
    """A tray notification."""

    title: str
    message: str
    failure: bool = False


class TrayController(QObject):
    """Owns the tray icon, the schedule, the sync runner, and every window."""

    notified = Signal(object)  # Notice
    synced = Signal(object)  # SyncRecord | None, after each sync ends

    def __init__(  # noqa: PLR0913 - collaborators are injected for tests
        self,
        environment: Environment,
        *,
        store: SettingsStore | None = None,
        runner: SyncRunner | None = None,
        clock: Callable[[], datetime] | None = None,
        opener: Opener = open_with_desktop,
        login_item: LoginItem | None = None,
        quit_application: Callable[[], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._environment = environment
        self._store = store or SettingsStore.default(environment)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._opener = opener
        self._login_item = login_item
        self._quit = quit_application or QApplication.quit
        self._reader = CatalogReader(environment)
        self._settings, self._settings_error = self._read_settings()
        # Until settings.json reads cleanly, nothing automatic happens: no
        # scheduled syncs and no sign-in changes from default values
        self._held = bool(self._settings_error)
        self._before_id = ""
        self._config = self._settings.config_file(environment)
        self._runner = runner or SyncRunner(self._config, parent=self)
        self._runner.set_config(self._config)
        self._runner.started.connect(self._on_started)
        self._runner.finished.connect(self._on_finished)
        self._started_at = self._clock()
        self._paused_until: datetime | None = None
        self._paused = False
        self._last_attempt: datetime | None = None
        self._last_record: SyncRecord | None = None
        self._catalog = Catalog()
        self._last_outcome: SyncOutcome | None = None
        self.tray = TrayIcon(self._commands(), self)
        self.popup = HistoryPopup(
            sync_now=lambda: self.sync_now(()),
            open_history=self.show_history,
            open_settings=self.show_settings,
        )
        self.history_window = HistoryWindow(
            load=lambda limit: self._reader.history(self._config, limit),
            sync_now=lambda: self.sync_now(()),
            open_folder=self.open_state,
        )
        self.about_dialog: AboutDialog | None = None
        self.settings_window: SettingsWindow | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer)
        self._labels = QTimer(self)
        self._labels.setInterval(LABEL_REFRESH_MS)
        self._labels.timeout.connect(self._refresh_labels)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(SETTINGS_DEBOUNCE_MS)
        self._debounce.timeout.connect(self.reload_settings)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(lambda _path: self._debounce.start())
        self._watcher.directoryChanged.connect(lambda _path: self._debounce.start())

    # Public state, for the window code and tests

    @property
    def settings(self) -> UserSettings:
        """Return the settings in effect."""
        return self._settings

    @property
    def runner(self) -> SyncRunner:
        """Return the sync runner."""
        return self._runner

    def schedule_state(self) -> ScheduleState:
        """Return what decides the next scheduled sync."""
        last = self._last_record.started if self._last_record is not None else None
        if self._last_attempt is not None and (
            last is None or self._last_attempt > last
        ):
            last = self._last_attempt
        schedule = self._settings.schedule
        return ScheduleState(
            enabled=schedule.enabled and not self._held,
            interval=schedule.interval,
            last_started=last,
            started_at=self._started_at,
            run_at_start=schedule.run_at_start,
            paused_until=self._paused_until,
            paused=self._paused,
        )

    # Lifecycle

    def start(self) -> None:
        """Show the tray, apply settings, and start the schedule."""
        self._watch()
        self._refresh_records()
        self._catalog = self._reader.profiles(self._config)
        self._apply_startup()
        self.tray.show()
        self._labels.start()
        self._refresh_labels()
        if self._settings_error:
            self._notify(
                Notice("SkillSinc settings", self._settings_error, failure=True)
            )
        self._reschedule()

    def stop(self) -> None:
        """Stop the schedule; let a running sync finish briefly, then end it."""
        self._timer.stop()
        self._labels.stop()
        if self._runner.busy and not self._runner.wait(EXIT_WAIT_MS):
            self._runner.cancel()
        self.tray.hide()

    def handle(self, arguments: Sequence[str]) -> None:
        """Act on a request forwarded by another ``skillsinc tray`` launch."""
        if not arguments:
            self.show_popup()
            return
        command, *rest = arguments
        if command == "sync":
            self.sync_now(tuple(rest))
        elif command == "show":
            target = rest[0] if rest else "popup"
            {
                "history": self.show_history,
                "settings": self.show_settings,
                "about": self.show_about,
            }.get(target, self.show_popup)()
        elif command == "quit":
            self.exit()
        else:
            logger.warning("Ignoring an unknown tray request %r", command)

    # Commands

    def sync_now(self, profiles: tuple[str, ...]) -> bool:
        """Sync now: ``profiles`` selects, empty uses the scheduled set."""
        if self._held:
            self._notify(
                Notice(
                    "SkillSinc settings",
                    "Fix settings.json before syncing: " + self._settings_error,
                    failure=True,
                )
            )
            return False
        selected = profiles or tuple(self._settings.schedule.profiles or ())
        return self._runner.request(SyncRequest(Trigger.TRAY, selected))

    def cancel(self) -> None:
        """Stop the running sync."""
        self._runner.cancel()

    def pause(self, duration: timedelta | None) -> None:
        """Pause scheduled syncs for ``duration``, or until resumed."""
        if duration is None:
            self._paused = True
        else:
            self._paused_until = self._clock() + duration
        self._refresh_labels()
        self._reschedule()

    def resume(self) -> None:
        """Resume scheduled syncs."""
        self._paused = False
        self._paused_until = None
        self._refresh_labels()
        self._reschedule()

    def show_popup(self) -> None:
        """Show the newest syncs beside the tray icon."""
        self.popup.populate(
            self._status_text(), self._reader.history(self._config, POPUP_LIMIT)
        )
        geometry = self.tray.geometry()
        self.popup.show_near(geometry.center() if geometry.isValid() else None)

    def show_history(self) -> None:
        """Open the full history window."""
        self.history_window.present()

    def show_settings(self) -> None:
        """Open the settings window, read afresh from both files."""
        if self.settings_window is None:
            self.settings_window = SettingsWindow(
                self._store,
                default_config=lambda settings: settings.config_file(self._environment),
                validate=ConfigLoader(ProfileRegistry(), self._environment).load,
                on_saved=self._on_settings_saved,
            )
        self.settings_window.present()

    def show_about(self) -> None:
        """Show the About window."""
        facts = AboutFacts(
            version=current_version(),
            config=self._config,
            settings=self._store.path,
            state=self._environment.state_directory,
        )
        self.about_dialog = AboutDialog(facts)
        self.about_dialog.show()
        self.about_dialog.raise_()
        self.about_dialog.activateWindow()

    def open_config(self) -> None:
        """Open the configuration in the default editor."""
        self._open(self._config)

    def open_state(self) -> None:
        """Open the folder holding the history and logs."""
        folder = self._environment.state_directory
        folder.mkdir(parents=True, exist_ok=True)
        self._open(folder)

    def exit(self) -> None:
        """Stop and quit the application."""
        self.stop()
        self._quit()

    def reload_settings(self) -> None:
        """Re-read settings.json after it changed on disk."""
        settings, error = self._read_settings(self._settings)
        self._watch()
        if error:
            self._settings_error = error
            self._notify(Notice("SkillSinc settings", error, failure=True))
            return
        held, self._held, self._settings_error = self._held, False, ""
        if settings == self._settings and not held:
            return
        self._settings = settings
        config = settings.config_file(self._environment)
        if config != self._config:
            self._config = config
            self._runner.set_config(config)
            self._refresh_records()
        self._catalog = self._reader.profiles(self._config)
        self._apply_startup()
        self._refresh_labels()
        self._reschedule()

    # Internals

    def _on_settings_saved(self) -> None:
        """Apply what the settings window saved without waiting for the watcher."""
        self.reload_settings()
        self._catalog = self._reader.profiles(self._config)
        self._refresh_labels()

    def _commands(self) -> TrayCommands:
        return TrayCommands(
            sync=self.sync_now,
            cancel=self.cancel,
            pause=self.pause,
            resume=self.resume,
            history=self.show_history,
            popup=self.show_popup,
            settings=self.show_settings,
            open_config=self.open_config,
            open_state=self.open_state,
            about=self.show_about,
            exit=self.exit,
        )

    def _read_settings(
        self, previous: UserSettings | None = None
    ) -> tuple[UserSettings, str]:
        try:
            return self._store.load(), ""
        except SettingsFileError as err:
            logger.warning("%s", err)
            if previous is not None:
                return previous, f"{err}. Using the previous settings."
            return UserSettings(), f"{err}. Automatic syncs wait until it is fixed."

    def _watch(self) -> None:
        """Watch settings.json, or the nearest folder above it that exists."""
        folder = self._store.path.parent
        while not folder.is_dir() and folder.parent != folder:
            folder = folder.parent
        watched = [str(folder)]
        if self._store.exists():
            watched.append(str(self._store.path))
        current = set(self._watcher.files()) | set(self._watcher.directories())
        missing = [path for path in watched if path not in current]
        if missing:
            self._watcher.addPaths(missing)

    def _apply_startup(self) -> None:
        item = self._login_item
        if item is None or not item.supported or self._held:
            return
        wanted = self._settings.startup.launch_at_login
        try:
            if item.enabled() != wanted:
                item.apply(enabled=wanted)
        except OSError as err:
            self._notify(
                Notice(
                    "SkillSinc", f"Could not change sign-in start: {err}", failure=True
                )
            )

    def _refresh_records(self) -> None:
        newest = self._reader.history(self._config, 1)
        self._last_record = newest[0] if newest else None

    def _on_timer(self) -> None:
        # A terminal or OS-scheduled sync may have run since the last look
        self._refresh_records()
        state = self.schedule_state()
        now = self._clock()
        if state.is_due(now) and not self._runner.busy:
            first = (
                self._last_record is None
                or self._last_record.started < self._started_at
            )
            trigger = (
                Trigger.TRAY_STARTUP
                if first and self._settings.schedule.run_at_start
                else Trigger.TRAY_SCHEDULED
            )
            self._last_attempt = now
            profiles = tuple(self._settings.schedule.profiles or ())
            self._runner.request(SyncRequest(trigger, profiles))
        self._reschedule()

    def _reschedule(self) -> None:
        """Arm the timer; a running sync rearms it when it ends."""
        wait = self.schedule_state().wait(self._clock())
        if wait is None or self._runner.busy:
            self._timer.stop()
            return
        self._timer.start(max(0, int(wait.total_seconds() * 1000)))

    def _on_started(self, request: SyncRequest) -> None:
        self._refresh_records()
        self._before_id = self._last_record.id if self._last_record else ""
        self._timer.stop()
        self.tray.set_icon_state(TrayState.SYNCING)
        self._refresh_labels(syncing=request)

    def _on_finished(self, outcome: SyncOutcome) -> None:
        self._last_outcome = outcome
        self._refresh_records()
        self._catalog = self._reader.profiles(self._config)
        record = self._last_record
        if record is not None and not self._covers(record, outcome):
            record = None
        if not outcome.ok or (record is not None and not record.ok):
            if self._settings.notifications.failures:
                reason = (
                    record.summary()
                    if record is not None
                    else outcome.error or outcome.output.strip()[-300:] or "it failed"
                )
                self._notify(Notice("SkillSinc sync failed", reason, failure=True))
        elif (
            record is not None
            and record.changed
            and self._settings.notifications.changes
        ):
            self._notify(Notice("SkillSinc synced", record.summary()))
        self._refresh_labels()
        self._reschedule()
        if self.history_window.isVisible():
            self.history_window.refresh()
        self.synced.emit(record)

    def _covers(self, record: SyncRecord, outcome: SyncOutcome) -> bool:
        """Return True if ``record`` is the run ``outcome`` reports.

        The run's record is the newest one, new since the run started.
        """
        return record.trigger is outcome.request.trigger and (
            record.id != self._before_id
        )

    def _icon_state(self) -> TrayState:
        if self._runner.busy:
            return TrayState.SYNCING
        record = self._last_record
        if (self._last_outcome is not None and not self._last_outcome.ok) or (
            record is not None and not record.ok
        ):
            return TrayState.FAILED
        if self._paused or (
            self._paused_until is not None and self._paused_until > self._clock()
        ):
            return TrayState.PAUSED
        return TrayState.IDLE

    def _status_text(self, syncing: SyncRequest | None = None) -> str:
        running = syncing or self._runner.current
        if running is not None:
            return f"Syncing {running.label()}…"
        record = self._last_record
        if record is None:
            return "No syncs yet"
        ago = describe_delta(self._clock() - record.finished)
        if record.ok:
            return f"Last sync {ago} ago: {record.summary()}"
        return f"Last sync failed {ago} ago: {record.summary()}"

    def _next_text(self) -> str:
        schedule = self._settings.schedule
        if self._held:
            return "Scheduled syncs wait for a valid settings.json"
        if not schedule.enabled:
            return "Scheduled syncs are off"
        if self._paused:
            return "Scheduled syncs paused"
        due = self.schedule_state().next_due()
        now = self._clock()
        if self._paused_until is not None and self._paused_until > now:
            local = self._paused_until.astimezone().strftime("%H:%M")
            return f"Scheduled syncs paused until {local}"
        if due is None or due <= now:
            return "Next sync: now"
        return f"Next sync in {describe_delta(due - now)}"

    def _refresh_labels(self, syncing: SyncRequest | None = None) -> None:
        if self.tray.menu.isVisible():
            return
        if self._paused_until is not None and self._paused_until <= self._clock():
            self._paused_until = None
        self.tray.set_icon_state(TrayState.SYNCING if syncing else self._icon_state())
        self.tray.update_menu(
            MenuState(
                status=self._status_text(syncing),
                next_sync=self._next_text(),
                syncing=syncing is not None or self._runner.busy,
                paused=self._paused
                or (
                    self._paused_until is not None
                    and self._paused_until > self._clock()
                ),
                catalog=self._catalog,
            )
        )

    def _open(self, path: Path) -> None:
        if not self._opener(path):
            self._notify(Notice("SkillSinc", f"Could not open {path}", failure=True))

    def _notify(self, notice: Notice) -> None:
        self.notified.emit(notice)
        icon = (
            QSystemTrayIcon.MessageIcon.Warning
            if notice.failure
            else QSystemTrayIcon.MessageIcon.Information
        )
        if QSystemTrayIcon.supportsMessages():
            self.tray.showMessage(notice.title, notice.message, icon)
