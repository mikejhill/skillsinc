"""Check PyPI for a newer SkillSinc and hand the upgrade to a script.

Checks run on a worker thread so the menu never waits on the network. An
install writes the upgrade script and starts it detached; the tray then exits
so the script can replace its files, and the script starts the tray again.
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
    RELEASES_URL,
    Installation,
    Release,
    ReleaseFeed,
    UpdateError,
    UpdateScript,
)
from skillsinc.tray.menu import CHECK_UPDATES
from skillsinc.tray.notice import Notice
from skillsinc.upgrades import current_version

logger = logging.getLogger(__name__)

TITLE: Final = "SkillSinc update"
# Windows process creation flags: no console window, and Ctrl+C in the tray's
# group does not reach the script
CREATE_NO_WINDOW: Final = 0x08000000
CREATE_NEW_PROCESS_GROUP: Final = 0x00000200

type Launcher = Callable[[str, Sequence[str]], bool]


def start_detached(
    program: str, arguments: Sequence[str], platform: str = sys.platform
) -> bool:
    """Start ``program`` to outlive the tray, with no window; True if it started."""
    options: dict[str, Any] = (
        {"creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP}
        if platform == "win32"
        else {"start_new_session": True}
    )
    try:
        subprocess.Popen(  # noqa: S603 - our own script, no shell
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
    _checked = Signal(object)  # (Release | UpdateError, manual)

    def __init__(  # noqa: PLR0913 - collaborators are injected for tests
        self,
        folder: Path,
        relaunch: Sequence[str],
        *,
        feed: ReleaseFeed | None = None,
        installation: Callable[[], Installation] = Installation.detect,
        launcher: Launcher = start_detached,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._folder = folder
        self._relaunch = tuple(relaunch)
        self._feed = feed or ReleaseFeed()
        self._installation = installation
        self._launcher = launcher
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
    def available(self) -> bool:
        """Return True if a newer release was found."""
        return self._release is not None and self._release.newer

    def label(self) -> str:
        """Return the menu entry's text."""
        if self.available and self._release is not None:
            return f"Install SkillSinc {self._release.latest}"
        return CHECK_UPDATES

    def summary(self) -> str:
        """Return the About window's line on releases."""
        if self._release is None:
            return "not checked yet"
        if self._release.newer:
            return f"{self._release.latest} (update available)"
        return f"{self._release.latest} (up to date)"

    def check(self, *, manual: bool, install: bool = False) -> None:
        """Look for a newer release; ``install`` installs one found."""
        if self._checking:
            return
        self._checking = True
        self.changed.emit()

        def work() -> None:
            try:
                result: Release | UpdateError = self._feed.latest(current_version())
            except UpdateError as err:
                result = err
            self._checked.emit((result, manual, install))

        threading.Thread(target=work, name="skillsinc-update", daemon=True).start()

    def install(self) -> bool:
        """Start the upgrade script; True if it started and the tray must exit."""
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
        try:
            script = UpdateScript.write(
                self._folder, installation.command, self._relaunch
            )
        except OSError as err:
            self.notified.emit(Notice(TITLE, f"Could not update: {err}", failure=True))
            return False
        if not self._launcher(script.program, script.arguments):
            self.notified.emit(
                Notice(TITLE, f"Could not start {script.path}", failure=True)
            )
            return False
        logger.info("Updating with %s", " ".join(installation.command))
        return True

    def _on_checked(self, payload: tuple[Release | UpdateError, bool, bool]) -> None:
        result, manual, install = payload
        self._checking = False
        if isinstance(result, UpdateError):
            logger.warning("%s", result)
            if manual:
                self.notified.emit(Notice(TITLE, str(result), failure=True))
        else:
            self._release = result
            if result.newer and not install:
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
        if install and self.available:
            self.install_requested.emit()
