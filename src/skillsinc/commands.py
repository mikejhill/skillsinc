"""Command handlers: one class per ``skillsinc`` subcommand."""

from __future__ import annotations

import logging
from typing import TextIO

from skillsinc.applier import Applier
from skillsinc.config import ConfigLoader
from skillsinc.lock import StateLock
from skillsinc.manifest import ManifestStore
from skillsinc.paths import Environment
from skillsinc.planner import Planner
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.report import Reporter
from skillsinc.settings import InitSettings, PlanSettings
from skillsinc.sources import SourceReader
from skillsinc.starter import StarterConfig

logger = logging.getLogger(__name__)


class InitCommand:
    """Write a starter configuration."""

    def __init__(self, profiles: ProfileRegistry, environment: Environment) -> None:
        self._starter = StarterConfig(profiles, environment)

    def run(self, settings: InitSettings, out: TextIO) -> int:
        """Write the file and tell the user what to do next."""
        self._starter.write(settings.config_path, settings.source, force=settings.force)
        out.write(
            f"Wrote {settings.config_path}\n"
            "Review it, run `skillsinc plan` to preview, then `skillsinc sync`.\n"
        )
        return 0


class PlanCommand:
    """Preview (``plan``) or apply (``sync``) the configured routes."""

    def __init__(self, profiles: ProfileRegistry, environment: Environment) -> None:
        self._profiles = profiles
        self._environment = environment

    def run(self, settings: PlanSettings, out: TextIO) -> int:
        """Plan, apply under the state lock when syncing, and report."""
        config = ConfigLoader(self._profiles, self._environment).load(
            settings.config_path
        )
        store = ManifestStore(self._environment.state_directory)
        planner = Planner(self._profiles, SourceReader(), store)
        reporter = Reporter(verbose=settings.verbose)

        if not settings.apply:
            plan = planner.plan(
                config, only=settings.destinations, adopt=settings.adopt
            )
            out.write(
                reporter.plan_json(plan)
                if settings.json_output
                else reporter.plan_text(plan, applied=False)
            )
            return 0 if plan.ok else 1

        lock = StateLock(self._environment.state_directory / "sync.lock")
        with lock:
            plan = planner.plan(
                config, only=settings.destinations, adopt=settings.adopt
            )
            results = Applier(store).apply(plan)
        if settings.json_output:
            out.write(reporter.plan_json(plan, results))
        else:
            out.write(reporter.plan_text(plan, applied=True))
            out.write(reporter.results_text(results))
        failed = any(result.error is not None for result in results)
        return 0 if plan.ok and not failed else 1


class ProfilesCommand:
    """List built-in profiles."""

    def __init__(self, profiles: ProfileRegistry) -> None:
        self._profiles = profiles

    def run(self, out: TextIO) -> int:
        """Print the profile table."""
        out.write(Reporter.profiles_text(self._profiles.profiles))
        return 0
