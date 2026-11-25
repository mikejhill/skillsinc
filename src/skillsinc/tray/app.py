"""Start the tray, or hand a request to the one already running."""

from __future__ import annotations

import logging
import logging.handlers
import os
import stat
import sys
from collections.abc import Sequence
from io import TextIOWrapper
from typing import Final, TextIO, override

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from skillsinc.history import BINARY, NO_FOLLOW, NON_BLOCKING
from skillsinc.paths import Environment
from skillsinc.tray.controller import TrayController
from skillsinc.tray.icons import TrayState, tray_icon
from skillsinc.tray.instance import SingleInstance
from skillsinc.tray.startup import LoginItem, tray_command

logger = logging.getLogger(__name__)

LOG_FILE: Final = "tray.log"
LOCK_FILE: Final = "tray.lock"
# How long a launch waits for another, starting tray to accept its request
STARTING_WAIT_MS: Final = 5000
LOG_BYTES: Final = 1024 * 1024
EXIT_OK: Final = 0
EXIT_UNAVAILABLE: Final = 1
DEFAULT_LOG_LEVEL: Final = "INFO"
SIGN_IN_VARIABLE: Final = "SKILLSINC_TRAY_SIGN_IN"


class SafeLogHandler(logging.handlers.RotatingFileHandler):
    """A rotating log that opens only a regular file, never through a link."""

    @override
    def _open(self) -> TextIOWrapper:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | NO_FOLLOW | NON_BLOCKING
        descriptor = os.open(self.baseFilename, flags | BINARY, 0o600)
        try:
            self._check(descriptor)
        except OSError:
            os.close(descriptor)
            raise
        return open(  # noqa: PTH123 - wraps the checked descriptor
            descriptor, "a", encoding=self.encoding, errors=self.errors
        )

    def _check(self, descriptor: int) -> None:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            msg = f"{self.baseFilename} is not a regular file"
            raise OSError(msg)
        if NON_BLOCKING:
            os.set_blocking(descriptor, True)


def log_to_file(environment: Environment, level: str) -> bool:
    """Send the tray's diagnostics to ``tray.log`` in the state directory.

    A windowless tray has no console, so the log file is where problems show.
    A link or special file at that path is left alone and nothing is logged.
    """
    folder = environment.state_directory
    folder.mkdir(parents=True, exist_ok=True)
    try:
        handler: logging.Handler = SafeLogHandler(
            folder / LOG_FILE, maxBytes=LOG_BYTES, backupCount=1, encoding="utf-8"
        )
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.basicConfig(level=level, handlers=[handler], force=True)
    return not isinstance(handler, logging.NullHandler)


def login_item(environment: Environment) -> LoginItem | None:
    """Return the sign-in entry, unless ``SKILLSINC_TRAY_SIGN_IN=off``."""
    if environment.variables.get(SIGN_IN_VARIABLE, "").lower() == "off":
        return None
    return LoginItem(environment, tray_command())


def _application() -> None:
    """Create or configure the Qt application for a resident tray."""
    application = QApplication.instance() or QApplication(sys.argv[:1])
    if isinstance(application, QApplication):
        application.setQuitOnLastWindowClosed(False)
        application.setApplicationName("SkillSinc")
        application.setWindowIcon(tray_icon(TrayState.IDLE))


def run(
    request: Sequence[str],
    environment: Environment,
    out: TextIO | None,
    *,
    instance_name: str | None = None,
    log_level: str = DEFAULT_LOG_LEVEL,
) -> int:
    """Run the tray with ``request`` (empty: start, or show the popup).

    ``out`` is None under ``pythonw``, which has no console.
    """

    def say(text: str) -> None:
        if out is not None:
            out.write(text)

    _application()
    instance = SingleInstance(instance_name or SingleInstance.name_for(environment))
    if instance.forward(list(request)):
        say("Sent to the running SkillSinc tray.\n")
        return EXIT_OK
    if list(request[:1]) == ["quit"]:
        say("SkillSinc tray is not running.\n")
        return EXIT_OK
    if not QSystemTrayIcon.isSystemTrayAvailable():
        logger.warning("No system tray is available; the tray icon may not appear")
    folder = environment.state_directory
    folder.mkdir(parents=True, exist_ok=True)
    if not instance.listen(folder / LOCK_FILE):
        if instance.forward_when_ready(list(request), STARTING_WAIT_MS):
            say("Sent to the running SkillSinc tray.\n")
            return EXIT_OK
        say("Could not start the SkillSinc tray: another instance is starting.\n")
        return EXIT_UNAVAILABLE
    if not log_to_file(environment, log_level):
        say(f"Not logging: {folder / LOG_FILE} is not a regular file.\n")
    controller = TrayController(environment, login_item=login_item(environment))
    instance.message_received.connect(controller.handle)
    controller.start()
    if request:
        controller.handle(list(request))
    logger.info("SkillSinc tray started")
    try:
        return QApplication.exec()
    finally:
        instance.close()
