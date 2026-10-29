"""Command handlers: one class per ``skillsinc`` subcommand."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TextIO

from skillsinc.applier import Applier
from skillsinc.config import ConfigLoader
from skillsinc.lock import StateLock
from skillsinc.manifest import ManifestStore
from skillsinc.paths import Environment
from skillsinc.planner import Planner
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.report import Reporter
from skillsinc.schedule import (
    VARIABLES,
    Backend,
    Job,
    Runner,
    ScheduleError,
    Scheduler,
    backend,
    command_line,
    detect,
    disposable,
    format_interval,
    interpreter,
)
from skillsinc.settings import InitSettings, PlanSettings, ScheduleSettings
from skillsinc.sources import SourceReader
from skillsinc.starter import StarterConfig

logger = logging.getLogger(__name__)

TEMPORARY_PYTHON = (
    "{} is in a temporary uv environment (uvx or uv run --with) that uv may"
    " delete; install SkillSinc with `uv tool install skillsinc` and run"
    " `skillsinc schedule install` from it"
)


class InitCommand:
    """Write a starter configuration."""

    def __init__(self, profiles: ProfileRegistry, environment: Environment) -> None:
        self._starter = StarterConfig(profiles, environment)

    def run(self, settings: InitSettings, out: TextIO) -> int:
        """Write the file and tell the user what to do next."""
        self._starter.write(
            settings.config_path,
            settings.source,
            force=settings.force,
            with_guidance=settings.with_guidance,
        )
        if settings.with_guidance:
            out.write(
                "Installed SkillSinc guidance in the source library. Edit it there;"
                " sync distributes it like other library content.\n"
                "Keep the library versioned in your own Git repository;"
                " init does not initialize Git or contact a remote.\n"
            )
        out.write(
            f"Wrote {settings.config_path}\n"
            "Review it, run `skillsinc plan` to preview, then `skillsinc sync`.\n"
            "To keep destinations in sync, run `skillsinc schedule install`.\n"
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
        planner = Planner(self._profiles, SourceReader(), store, self._environment)
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


class ScheduleCommand:
    """Register, remove, or inspect the scheduled ``sync``."""

    def __init__(
        self,
        environment: Environment,
        runner: Runner | None = None,
        executable: Path | None = None,
    ) -> None:
        self._environment = environment
        self._runner = runner or Runner()
        self._executable = executable or Path(sys.executable)

    def run(self, settings: ScheduleSettings, out: TextIO) -> int:
        """Run the requested action and report it."""
        scheduler = (
            Scheduler(settings.scheduler)
            if settings.scheduler is not None
            else detect(self._environment.platform, self._runner)
        )
        chosen = backend(scheduler, self._runner, self._environment)
        if settings.action == "install":
            return self._install(chosen, settings, out)
        if settings.action == "remove":
            removed = chosen.remove()
            verb = "Removed" if removed else "Nothing to remove; no"
            out.write(f"{verb} {chosen.title} {chosen.location}.\n")
            return 0
        status = chosen.status()
        out.write(f"Scheduler: {chosen.title} ({chosen.location})\n")
        out.write(f"Installed: {'yes' if status.installed else 'no'}\n")
        for label, value in status.details:
            out.write(f"{label}: {value}\n")
        if not status.installed:
            out.write("Register it with `skillsinc schedule install`.\n")
        return 0 if status.installed else 1

    def _install(self, chosen: Backend, settings: ScheduleSettings, out: TextIO) -> int:
        """Register the job, or print its definition for ``--dry-run``."""
        job, log = self._job(chosen, settings)
        temporary = disposable(Path(job.argv[0]))
        if settings.dry_run:
            out.write(f"Would register with {chosen.title} ({chosen.location}):\n")
            out.write(chosen.definition(job))
            if temporary:
                out.write(f"Warning: {TEMPORARY_PYTHON.format(job.argv[0])}\n")
            return 0
        if temporary:
            raise ScheduleError(TEMPORARY_PYTHON.format(job.argv[0]))
        if log is not None:
            log.parent.mkdir(parents=True, exist_ok=True)
        chosen.install(job)
        platform = self._environment.platform
        out.write(
            f"Registered {chosen.title} {chosen.location}:"
            f" sync every {format_interval(job.interval)}.\n"
            f"Runs: {command_line(job.argv, platform)}\n"
        )
        if log is not None:
            out.write(f"Log: {log}\n")
        if platform == "win32" and job.variables:
            names = ", ".join(sorted(job.variables))
            out.write(
                f"Note: tasks read saved user variables only; set {names} with"
                " setx so scheduled runs see the same values.\n"
            )
        out.write(
            "Check it with `skillsinc schedule status`;"
            " remove it with `skillsinc schedule remove`.\n"
        )
        return 0

    def _job(
        self, chosen: Backend, settings: ScheduleSettings
    ) -> tuple[Job, Path | None]:
        """Return the job to register and the log it appends to, if any."""
        platform = self._environment.platform
        # -P keeps the job's working directory off the import path
        python = str(interpreter(self._executable, platform))
        argv = [python, "-P", "-m", "skillsinc", "sync"]
        log = None
        if chosen.writes_log:
            log = self._environment.state_directory / "sync.log"
            argv += ["--log-file", str(log)]
        if settings.config_path is not None:
            argv += ["--config", str(settings.config_path)]
        variables = {
            name: self._environment.variables[name]
            for name in VARIABLES
            if self._environment.variables.get(name, "")
        }
        return Job(tuple(argv), settings.interval, variables), log
