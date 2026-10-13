"""Command-line parsing into typed command settings."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Final

from skillsinc.paths import Environment
from skillsinc.settings import InitSettings, PlanSettings, ProfilesSettings, Settings

LOG_LEVELS: Final = ("DEBUG", "INFO", "WARNING", "ERROR")


class CommandLine:
    """Parser for the ``skillsinc`` command."""

    def __init__(self, environment: Environment) -> None:
        self._environment = environment
        self._parser = self._build_parser()

    def parse(self, argv: Sequence[str] | None = None) -> Settings:
        """Parse ``argv`` into the settings for one command."""
        args = self._parser.parse_args(argv)
        log_level = str(args.log_level)
        command = str(args.command)
        if command == "profiles":
            return ProfilesSettings(log_level=log_level)

        config_path = Path(args.config) if args.config else None
        config_path = config_path or self._environment.default_config_file
        if command == "init":
            source = Path(args.source) if args.source else None
            if source is not None and not source.expanduser().is_dir():
                self._parser.error(f"--source is not a directory: {source}")
            return InitSettings(config_path, source, bool(args.force), log_level)
        return PlanSettings(
            config_path=config_path,
            apply=command == "sync",
            destinations=tuple(args.destination or ()),
            adopt=bool(args.adopt),
            json_output=bool(args.json),
            verbose=bool(args.verbose),
            log_level=log_level,
        )

    def _build_parser(self) -> argparse.ArgumentParser:
        """Define the commands and their options."""
        parser = argparse.ArgumentParser(
            prog="skillsinc",
            description=(
                "Sync one library of agent skills, instructions, agents, and prompts"
                " into every AI harness you use."
            ),
        )
        parser.add_argument(
            "--version", action="version", version=f"%(prog)s {version('skillsinc')}"
        )
        common = argparse.ArgumentParser(add_help=False)
        common.add_argument(
            "--log-level",
            default="WARNING",
            choices=LOG_LEVELS,
            help="diagnostic verbosity on stderr (default: %(default)s)",
        )
        configured = argparse.ArgumentParser(add_help=False, parents=[common])
        configured.add_argument(
            "--config",
            metavar="PATH",
            help=f"config file (default: {self._environment.default_config_file})",
        )

        commands = parser.add_subparsers(
            dest="command", required=True, metavar="COMMAND"
        )
        init = commands.add_parser(
            "init", parents=[configured], help="write a starter configuration"
        )
        init.add_argument("--source", metavar="DIR", help="your customization library")
        init.add_argument(
            "--force", action="store_true", help="overwrite an existing file"
        )

        for name, summary in (
            ("plan", "show what a sync would change, without writing"),
            ("sync", "apply the plan to every enabled destination"),
        ):
            command = commands.add_parser(name, parents=[configured], help=summary)
            command.add_argument(
                "-d",
                "--destination",
                action="append",
                metavar="NAME",
                help="limit to this destination (repeatable)",
            )
            command.add_argument(
                "--adopt",
                action="store_true",
                help="take ownership of existing files listed as unmanaged",
            )
            command.add_argument(
                "--json", action="store_true", help="print a JSON report"
            )
            command.add_argument(
                "-v",
                "--verbose",
                action="store_true",
                help="also list unsupported items",
            )

        commands.add_parser(
            "profiles",
            parents=[common],
            help="list harness profiles and what they support",
        )
        return parser
