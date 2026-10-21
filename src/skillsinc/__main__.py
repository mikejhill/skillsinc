"""Application entry point."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, TextIO

from skillsinc.cli import CommandLine
from skillsinc.commands import (
    InitCommand,
    PlanCommand,
    ProfilesCommand,
    ScheduleCommand,
)
from skillsinc.exceptions import AppError, ConfigurationError
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.schedule import Runner
from skillsinc.settings import InitSettings, PlanSettings, ScheduleSettings, Settings

logger = logging.getLogger(__name__)

EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_USAGE: Final = 2
EXIT_INTERRUPTED: Final = 130

# A --log-file larger than this is renamed to <name>.1 before a run appends
LOG_ROTATE_BYTES: Final = 1024 * 1024


class Application:
    """The ``skillsinc`` command-line application."""

    def __init__(
        self,
        environment: Environment | None = None,
        out: TextIO | None = None,
        runner: Runner | None = None,
    ) -> None:
        self._environment = environment or Environment.current()
        self._out = out or sys.stdout
        self._profiles = ProfileRegistry()
        self._runner = runner

    @classmethod
    def main(cls, argv: Sequence[str] | None = None) -> None:
        """Run the application and exit with its status code."""
        sys.exit(cls().run(argv))

    def run(self, argv: Sequence[str] | None = None) -> int:
        """Parse arguments, run one command, and return its exit code."""
        settings = CommandLine(self._environment).parse(argv)
        if isinstance(settings, PlanSettings) and settings.log_file is not None:
            return self._run_logged(settings, settings.log_file)
        self._configure_logging(settings.log_level)
        return self._guarded(settings)

    def _run_logged(self, settings: PlanSettings, path: Path) -> int:
        """Run with the report and diagnostics appended to ``path``."""
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > LOG_ROTATE_BYTES:
            path.replace(path.with_name(f"{path.name}.1"))
        out = self._out
        with path.open("a", encoding="utf-8") as log:
            started = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
            command = "sync" if settings.apply else "plan"
            log.write(f"--- {started} skillsinc {command}\n")
            self._out = log
            self._configure_logging(settings.log_level, log)
            try:
                return self._guarded(settings)
            finally:
                self._out = out
                self._configure_logging(settings.log_level)

    def _guarded(self, settings: Settings) -> int:
        """Run one command, turning expected errors into exit codes."""
        try:
            return self._dispatch(settings)
        except ConfigurationError as err:
            logger.error("%s", err)  # noqa: TRY400 — user-facing message, not a crash
            return EXIT_USAGE
        except AppError as err:
            logger.error("%s", err)  # noqa: TRY400 — user-facing message, not a crash
            return EXIT_FAILED
        except KeyboardInterrupt:
            logger.warning("Interrupted")
            return EXIT_INTERRUPTED

    def _dispatch(self, settings: Settings) -> int:
        """Run the handler for ``settings``."""
        if isinstance(settings, InitSettings):
            return InitCommand(self._profiles, self._environment).run(
                settings, self._out
            )
        if isinstance(settings, PlanSettings):
            return PlanCommand(self._profiles, self._environment).run(
                settings, self._out
            )
        if isinstance(settings, ScheduleSettings):
            return ScheduleCommand(self._environment, self._runner).run(
                settings, self._out
            )
        return ProfilesCommand(self._profiles).run(self._out)

    @staticmethod
    def _configure_logging(level: str, stream: TextIO | None = None) -> None:
        """Send diagnostics to ``stream``, else stderr."""
        logging.basicConfig(
            level=level,
            format="%(levelname)s: %(message)s",
            stream=stream or sys.stderr,
            force=True,
        )


if __name__ == "__main__":
    Application.main()
