"""Command-line parsing into typed command settings."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Final

from skillsinc.paths import Environment
from skillsinc.schedule import (
    DEFAULT_INTERVAL,
    Scheduler,
    format_interval,
    parse_interval,
)
from skillsinc.settings import (
    InitSettings,
    PlanSettings,
    ProfilesSettings,
    ScheduleSettings,
    Settings,
)

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
        if command == "schedule":
            return ScheduleSettings(
                action=args.action,
                scheduler=args.scheduler,
                interval=getattr(args, "interval", DEFAULT_INTERVAL),
                config_path=self._environment.expand(args.config, Path.cwd())
                if getattr(args, "config", None)
                else None,
                dry_run=bool(getattr(args, "dry_run", False)),
                log_level=log_level,
            )

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
            log_file=Path(args.log_file) if args.log_file else None,
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
            command.add_argument(
                "--log-file",
                metavar="PATH",
                help="append the report and diagnostics to PATH instead of"
                " printing them",
            )

        commands.add_parser(
            "profiles",
            parents=[common],
            help="list harness profiles and what they support",
        )
        self._add_schedule(commands, configured, common)
        return parser

    @staticmethod
    def _add_schedule(
        commands: argparse._SubParsersAction[argparse.ArgumentParser],
        configured: argparse.ArgumentParser,
        common: argparse.ArgumentParser,
    ) -> None:
        """Define ``schedule install|remove|status``."""
        schedule = commands.add_parser(
            "schedule",
            help="run sync on a schedule with the operating system's scheduler",
        )
        which = argparse.ArgumentParser(add_help=False)
        which.add_argument(
            "--scheduler",
            choices=[scheduler.value for scheduler in Scheduler],
            help="scheduler to use (default: Task Scheduler on Windows, launchd on"
            " macOS, a systemd user timer on Linux, else cron)",
        )
        actions = schedule.add_subparsers(
            dest="action", required=True, metavar="ACTION"
        )
        install = actions.add_parser(
            "install",
            parents=[configured, which],
            help="register sync, or replace an earlier registration",
        )
        install.add_argument(
            "--interval",
            type=CommandLine._interval,
            default=DEFAULT_INTERVAL,
            metavar="DURATION",
            help="time between runs, such as 5m, 1h, or 00:05:00 (default:"
            f" {format_interval(DEFAULT_INTERVAL)})",
        )
        install.add_argument(
            "--dry-run",
            action="store_true",
            help="print the definition without registering it",
        )
        actions.add_parser(
            "remove", parents=[common, which], help="unregister the scheduled sync"
        )
        actions.add_parser(
            "status",
            parents=[common, which],
            help="show the scheduled sync and its last result",
        )

    @staticmethod
    def _interval(text: str) -> timedelta:
        """Parse ``--interval`` for argparse."""
        try:
            return parse_interval(text)
        except ValueError as err:
            raise argparse.ArgumentTypeError(str(err)) from err
