"""Check PyPI for a newer SkillSinc and hand the upgrade to a helper.

Checks run on a worker thread so the menu never waits on the network. An
install starts the helper detached; the tray then exits so the helper can
replace its files, and the helper starts the tray again. At start the tray
reads what the helper recorded: a success is announced, and a failure is
reported once and never retried automatically for that release.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Final

from PySide6.QtCore import QObject, Signal

from skillsinc.selfupdate import (
    DETACHED_FLAGS,
    LOG_FILE,
    RELEASES_URL,
    Installation,
    Release,
    ReleaseFeed,
    UpdateError,
    UpdateHelper,
    UpdateResult,
    is_newer,
)
from skillsinc.tray.notice import Notice
from skillsinc.upgrades import current_version

logger = logging.getLogger(__name__)

TITLE: Final = "SkillSinc update"

type Launcher = Callable[[str, Sequence[str]], bool]


def start_detached(
    program: str, arguments: Sequence[str], platform: str = sys.platform
) -> bool:
    """Start ``program`` to outlive the tray, with no window; True if it started."""
    options: dict[str, Any] = (
        {"creationflags": DETACHED_FLAGS}
        if platform == "win32"
        else {"start_new_session": True}
    )
    try:
        subprocess.Popen(  # noqa: S603 - our own helper, no shell
            [program, *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            **options,
        )
    except OSError:
        logger.exception("Could not start %s", program)
        return False
    return True


class Updater(QObject):
    """The tray's update state: last check, newest release, and installing."""

    changed = Signal()
    notified = Signal(object)  # Notice
    install_requested = Signal()  # an automatic check found a release to install
    _checked = Signal(object)  # (Release | UpdateError, manual, install)

    def __init__(  # noqa: PLR0913 - collaborators are injected for tests
        self,
        folder: Path,
        relaunch: Sequence[str],
        *,
        feed: ReleaseFeed | None = None,
        installation: Callable[[], Installation] = Installation.detect,
        launcher: Launcher = start_detached,
        current: Callable[[], str] = current_version,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._folder = folder
        self._relaunch = tuple(relaunch)
        self._feed = feed or ReleaseFeed()
        self._installation = installation
        self._launcher = launcher
        self._current = current
        self._release: Release | None = None
        self._checking = False
        self._checked.connect(self._on_checked)

    @property
    def release(self) -> Release | None:
        """Return the result of the last successful check."""
        return self._release

    @property
    def checking(self) -> bool:
        """Return True while a check runs."""
        return self._checking

    @property
    def available(self) -> str:
        """Return the newer release found, or "" if there is none."""
        if self._release is not None and self._release.newer:
            return self._release.latest
        return ""

    def summary(self) -> str:
        """Return the About window's line on releases."""
        if self._release is None:
            return "not checked yet"
        if self._release.newer:
            return f"{self._release.latest} (update available)"
        return f"{self._release.latest} (up to date)"

    def report_last_attempt(self) -> None:
        """Announce what the last upgrade did, once."""
        result = UpdateResult.read(self._folder)
        if result is None:
            return
        current = self._current()
        if result.succeeded and not is_newer(result.version, current):
            self.notified.emit(Notice(TITLE, f"SkillSinc was updated to {current}."))
            UpdateResult.clear(self._folder)
            return
        if not is_newer(result.version, current):
            UpdateResult.clear(self._folder)  # a later install superseded it
            return
        if result.reported:
            return
        if result.note:
            why = result.note
        elif result.succeeded:
            why = f"the installer finished but {current} is still installed"
        elif result.returncode is not None:
            why = f"the installer exited with {result.returncode}"
        else:
            why = "the installer did not run"
        self.notified.emit(
            Notice(
                TITLE,
                f"Updating to SkillSinc {result.version} did not work: {why}. See"
                f" {self._folder / LOG_FILE}. It will not be retried automatically.",
                failure=True,
            )
        )
        UpdateResult.mark_reported(self._folder)

    def failed_before(self, version: str) -> bool:
        """Return True if an upgrade to ``version`` already failed here."""
        result = UpdateResult.read(self._folder)
        return (
            result is not None
            and result.version == version
            and is_newer(version, self._current())
        )

    def check(self, *, manual: bool, install: bool = False) -> None:
        """Look for a newer release; ``install`` asks to install one found."""
        if self._checking:
            return
        self._checking = True
        self.changed.emit()

        def work() -> None:
            try:
                result: Release | UpdateError = self._feed.latest(self._current())
            except Exception as err:  # noqa: BLE001 - any failure ends the check
                result = (
                    err
                    if isinstance(err, UpdateError)
                    else UpdateError(f"could not check for updates: {err}")
                )
            self._checked.emit((result, manual, install))

        threading.Thread(target=work, name="skillsinc-update", daemon=True).start()

    def install(self) -> bool:
        """Start the upgrade helper; True if it started and the tray must exit."""
        version = self.available
        if not version:
            return False
        installation = self._installation()
        if not installation.can_upgrade:
            self.notified.emit(
                Notice(
                    TITLE,
                    f"Update SkillSinc yourself: {installation.reason}."
                    f" Releases: {RELEASES_URL}",
                    failure=True,
                )
            )
            return False
        helper = UpdateHelper.plan(installation, self._relaunch, self._folder, version)
        if not self._launcher(helper.program, helper.arguments):
            self.notified.emit(
                Notice(TITLE, "Could not start the update helper", failure=True)
            )
            return False
        logger.info("Updating to %s with %s", version, " ".join(installation.command))
        return True

    def _on_checked(self, payload: tuple[Release | UpdateError, bool, bool]) -> None:
        result, manual, install = payload
        self._checking = False
        if isinstance(result, UpdateError):
            logger.warning("%s", result)
            if manual:
                self.notified.emit(Notice(TITLE, str(result), failure=True))
            self.changed.emit()
            return
        self._release = result
        automatic = install and result.newer and not self.failed_before(result.latest)
        if result.newer and not automatic:
            self.notified.emit(
                Notice(
                    TITLE,
                    f"SkillSinc {result.latest} is available (you have"
                    f" {result.current}). Choose Install in the menu.",
                )
            )
        elif manual and not result.newer:
            self.notified.emit(
                Notice(TITLE, f"SkillSinc {result.current} is up to date.")
            )
        self.changed.emit()
        if automatic:
            self.install_requested.emit()
