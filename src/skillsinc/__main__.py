"""Application entry point."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from typing import Final, TextIO

from skillsinc.cli import CommandLine
from skillsinc.commands import InitCommand, PlanCommand, ProfilesCommand
from skillsinc.exceptions import AppError, ConfigurationError
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.settings import InitSettings, PlanSettings, Settings

logger = logging.getLogger(__name__)

EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_USAGE: Final = 2
EXIT_INTERRUPTED: Final = 130


class Application:
    """The ``skillsinc`` command-line application."""

    def __init__(
        self, environment: Environment | None = None, out: TextIO | None = None
    ) -> None:
        self._environment = environment or Environment.current()
        self._out = out or sys.stdout
        self._profiles = ProfileRegistry()

    @classmethod
    def main(cls, argv: Sequence[str] | None = None) -> None:
        """Run the application and exit with its status code."""
        sys.exit(cls().run(argv))

    def run(self, argv: Sequence[str] | None = None) -> int:
        """Parse arguments, run one command, and return its exit code."""
        settings = CommandLine(self._environment).parse(argv)
        self._configure_logging(settings.log_level)
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
        return ProfilesCommand(self._profiles).run(self._out)

    @staticmethod
    def _configure_logging(level: str) -> None:
        """Send diagnostics to stderr."""
        logging.basicConfig(
            level=level,
            format="%(levelname)s: %(message)s",
            stream=sys.stderr,
            force=True,
        )


if __name__ == "__main__":
    Application.main()
