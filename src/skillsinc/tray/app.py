"""Start the tray, or hand a request to the one already running."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from collections.abc import Sequence
from typing import Final, TextIO

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from skillsinc.paths import Environment
from skillsinc.tray.controller import TrayController
from skillsinc.tray.icons import TrayState, tray_icon
from skillsinc.tray.instance import SingleInstance
from skillsinc.tray.startup import LoginItem, tray_command

logger = logging.getLogger(__name__)

LOG_FILE: Final = "tray.log"
LOG_BYTES: Final = 1024 * 1024
EXIT_OK: Final = 0
EXIT_UNAVAILABLE: Final = 1


def log_to_file(environment: Environment, level: str) -> None:
    """Send the tray's diagnostics to ``tray.log`` in the state directory.

    A windowless tray has no console, so the log file is where problems show.
    """
    folder = environment.state_directory
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        folder / LOG_FILE, maxBytes=LOG_BYTES, backupCount=1, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.basicConfig(level=level, handlers=[handler], force=True)


def run(
    request: Sequence[str],
    environment: Environment,
    out: TextIO,
    *,
    instance_name: str | None = None,
) -> int:
    """Run the tray with ``request`` (empty: start, or show the popup)."""
    application = QApplication.instance() or QApplication(sys.argv[:1])
    if isinstance(application, QApplication):
        application.setQuitOnLastWindowClosed(False)
        application.setApplicationName("SkillSinc")
        application.setWindowIcon(tray_icon(TrayState.IDLE))
    instance = SingleInstance(instance_name or SingleInstance.name_for(environment))
    if instance.forward(list(request)):
        out.write("Sent to the running SkillSinc tray.\n")
        return EXIT_OK
    if list(request[:1]) == ["quit"]:
        out.write("SkillSinc tray is not running.\n")
        return EXIT_OK
    if not QSystemTrayIcon.isSystemTrayAvailable():
        logger.warning("No system tray is available; the tray icon may not appear")
    if not instance.listen():
        out.write("Could not start the SkillSinc tray: another instance is starting.\n")
        return EXIT_UNAVAILABLE
    log_to_file(environment, "INFO")
    controller = TrayController(
        environment, login_item=LoginItem(environment, tray_command())
    )
    instance.message_received.connect(controller.handle)
    controller.start()
    if request:
        controller.handle(list(request))
    logger.info("SkillSinc tray started")
    try:
        return application.exec()
    finally:
        instance.close()
