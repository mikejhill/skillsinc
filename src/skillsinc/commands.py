"""Command handlers: one class per ``skillsinc`` subcommand."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from skillsinc.applier import Applier
from skillsinc.config import ConfigLoader
from skillsinc.exceptions import AppError, ConfigurationError
from skillsinc.history import SyncHistory, SyncRecord, SyncRecorder
from skillsinc.lock import StateLock
from skillsinc.manifest import ManifestStore
from skillsinc.paths import Environment, absolute
from skillsinc.planner import Planner
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.remote import RemoteCache
from skillsinc.report import Reporter, SourceStatus
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
from skillsinc.schema import ConfigSchema
from skillsinc.selection import PROFILES_VARIABLE as SELECTION_VARIABLE
from skillsinc.selection import Origin, ProfileSelector, Selection
from skillsinc.settings import (
    HistorySettings,
    InitSettings,
    PlanSettings,
    ProfilesSettings,
    ScheduleSettings,
    StatusSettings,
)
from skillsinc.sources import SourceReader
from skillsinc.starter import StarterConfig
from skillsinc.upgrades import ConfigMaintenance, current_version
from skillsinc.usersettings import (
    DEFAULT_HISTORY_KEEP,
    SettingsFileError,
    SettingsStore,
)

logger = logging.getLogger(__name__)

TEMPORARY_PYTHON = (
    "{} is in a temporary uv environment (uvx or uv run --with) that uv may"
    " delete; install SkillSinc with `uv tool install skillsinc` and run"
    " `skillsinc schedule install` from it"
)


def ignore_state(state: Path) -> None:
    """Keep a project's state directory out of version control.

    Manifests record what this checkout's syncs wrote, so they never belong
    in the repository; a ``.gitignore`` inside the directory ignores it whole.
    """
    state.mkdir(parents=True, exist_ok=True)
    ignore = state / ".gitignore"
    if not ignore.exists():
        ignore.write_text(
            "# SkillSinc state for this checkout; never commit it\n*\n",
            encoding="utf-8",
        )


class InitCommand:
    """Write a starter configuration."""

    def __init__(self, profiles: ProfileRegistry, environment: Environment) -> None:
        self._starter = StarterConfig(profiles, environment)
        self._environment = environment

    def run(self, settings: InitSettings, out: TextIO) -> int:
        """Write the file and tell the user what to do next."""
        self._starter.write(
            settings.config_path,
            settings.source,
            force=settings.force,
            with_guidance=settings.with_guidance,
            project=self._environment.working_directory if settings.project else None,
        )
        if settings.with_guidance:
            out.write(
                "Installed SkillSinc guidance in the source library. Edit it there;"
                " sync distributes it like other library content.\n"
                "Keep the library versioned in your own Git repository;"
                " init does not initialize Git or contact a remote.\n"
            )
        try:
            ConfigSchema.refresh(settings.config_path)
        except OSError as err:
            out.write(
                f"warning: could not write {ConfigSchema.path(settings.config_path)}:"
                f" {err}; `skillsinc sync` tries again\n"
            )
        out.write(
            f"Wrote {settings.config_path}\n"
            "Review it, run `skillsinc plan` to preview, then `skillsinc sync`.\n"
        )
        if settings.project:
            out.write(
                "Inside this project, plan and sync use this file; pass --user for"
                " your user configuration.\n"
                "Commit the file; SkillSinc keeps .config/skillsinc/state out of"
                " Git.\n"
            )
        else:
            out.write(
                "To keep destinations in sync, run `skillsinc schedule install`.\n"
            )
        return 0


class PlanCommand:
    """Preview (``plan``) or apply (``sync``) the configured routes."""

    def __init__(
        self,
        profiles: ProfileRegistry,
        environment: Environment,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC).astimezone(),
    ) -> None:
        self._profiles = profiles
        self._environment = environment
        self._clock = clock

    def run(self, settings: PlanSettings, out: TextIO) -> int:
        """Plan, apply under the state lock when syncing, and report.

        A sync is recorded in the history whether it succeeds or fails.
        """
        if not settings.apply:
            return self._run(settings, out, None)
        recorder = self._recorder(settings)
        try:
            return self._run(settings, out, recorder)
        except AppError as err:
            recorder.failed(str(err))
            raise

    def _recorder(self, settings: PlanSettings) -> SyncRecorder:
        """Return the recorder for one sync, keeping as many runs as settings say."""
        try:
            keep = SettingsStore.default(self._environment).load().history.keep
        except SettingsFileError as err:
            logger.warning("%s; keeping %d history records", err, DEFAULT_HISTORY_KEEP)
            keep = DEFAULT_HISTORY_KEEP
        return SyncRecorder(
            state=self._environment.state_directory,
            keep=keep,
            trigger=settings.trigger,
            config=settings.config_path,
            requested=settings.profiles,
            version=current_version(),
            clock=self._clock,
        )

    def _run(
        self, settings: PlanSettings, out: TextIO, recorder: SyncRecorder | None
    ) -> int:
        """Plan, and when ``recorder`` is given apply and record the run."""
        config = ConfigLoader(self._profiles, self._environment).load(
            settings.config_path
        )
        config, selection = ProfileSelector(self._environment).select(
            config, settings.profiles
        )
        self._check_destinations(settings, selection)
        profiles = self._profiles.scoped(config.project)
        state = config.state_directory(self._environment)
        store = ManifestStore(state, config.project)
        reader = SourceReader(remotes=RemoteCache(self._environment))
        planner = Planner(profiles, reader, store, self._environment)
        reporter = Reporter(verbose=settings.verbose)

        maintenance = ConfigMaintenance(state)
        if recorder is None:
            plan = planner.plan(
                config, only=settings.destinations, adopt=settings.adopt
            )
            pending = maintenance.plan(config.path)
            out.write(
                reporter.plan_json(
                    plan,
                    configuration=pending.changes,
                    configuration_warnings=pending.warnings,
                    selection=selection,
                )
                if settings.json_output
                else reporter.plan_text(
                    plan,
                    applied=False,
                    configuration=pending.changes,
                    configuration_warnings=pending.warnings,
                    selection=selection,
                )
            )
            return 0 if plan.ok else 1

        if config.project is not None:
            ignore_state(state)
        recorder.locate(state)
        lock = StateLock(state / "sync.lock")
        with lock:
            plan = planner.plan(
                config, only=settings.destinations, adopt=settings.adopt
            )
            results = Applier(store).apply(plan)
            maintained = maintenance.apply(config.path)
            recorder.applied(
                plan,
                profiles=selection.active
                if selection.origin is not Origin.NONE
                else None,
                errors=[
                    f"{result.name}: {result.error}"
                    for result in results
                    if result.error is not None
                ],
                warnings=maintained.warnings,
            )
        if settings.json_output:
            out.write(
                reporter.plan_json(
                    plan,
                    results,
                    configuration=maintained.applied,
                    configuration_warnings=maintained.warnings,
                    selection=selection,
                )
            )
        else:
            out.write(
                reporter.plan_text(
                    plan,
                    applied=True,
                    configuration=maintained.applied,
                    configuration_warnings=maintained.warnings,
                    selection=selection,
                )
            )
            out.write(reporter.results_text(results))
        failed = any(result.error is not None for result in results)
        return 0 if plan.ok and not failed else 1

    @staticmethod
    def _check_destinations(settings: PlanSettings, selection: Selection) -> None:
        """Refuse ``--destination`` names that only inactive profiles reach.

        Raises:
            ConfigurationError: If a named destination is left out by profiles.
        """
        left_out = [
            name
            for name in settings.destinations
            if name in selection.inactive_destinations
        ]
        if left_out:
            active = ", ".join(selection.active) or "none"
            msg = (
                f"destination(s) {', '.join(left_out)} receive nothing from the"
                f" active profiles ({active}); select a profile that routes to"
                " them with --profile"
            )
            raise ConfigurationError(msg)


class StatusCommand:
    """Report each source's health without contacting any remote."""

    def __init__(self, profiles: ProfileRegistry, environment: Environment) -> None:
        self._profiles = profiles
        self._environment = environment

    def run(self, settings: StatusSettings, out: TextIO) -> int:
        """Print source states; exit 1 when any source needs attention."""
        config = ConfigLoader(self._profiles, self._environment).load(
            settings.config_path
        )
        cache = RemoteCache(self._environment)
        states = [SourceStatus.of(source, cache) for source in config.sources.values()]
        reporter = Reporter()
        out.write(
            reporter.status_json(states)
            if settings.json_output
            else reporter.status_text(states)
        )
        return 0 if all(state.healthy for state in states) else 1


class ProfilesCommand:
    """List the configuration's sync profiles and which apply on this machine."""

    def __init__(self, profiles: ProfileRegistry, environment: Environment) -> None:
        self._profiles = profiles
        self._environment = environment

    def run(self, settings: ProfilesSettings, out: TextIO) -> int:
        """Print each profile, whether it is active, and why."""
        config = ConfigLoader(self._profiles, self._environment).load(
            settings.config_path
        )
        _, selection = ProfileSelector(self._environment).select(
            config, settings.profiles
        )
        out.write(
            Reporter.selection_json(selection)
            if settings.json_output
            else Reporter.selection_text(selection)
        )
        return 0


class HistoryCommand:
    """List recent syncs of one configuration, newest first."""

    def __init__(self, environment: Environment) -> None:
        self._environment = environment

    def run(self, settings: HistorySettings, out: TextIO) -> int:
        """Print the newest ``settings.limit`` records."""
        records = self.records(settings.config_path, settings.limit)
        out.write(
            Reporter.history_json(records)
            if settings.json_output
            else Reporter.history_text(records)
        )
        return 0

    def records(self, config: Path, limit: int) -> list[SyncRecord]:
        """Return the newest records of syncs of ``config``.

        A project's runs are in its own state directory; failures that came
        before its configuration loaded are in the user's.
        """
        target = absolute(config)
        directories = [self._environment.state_directory]
        project = self._environment.state_directory
        try:
            loaded = ConfigLoader(ProfileRegistry(), self._environment).load(config)
            project = loaded.state_directory(self._environment)
        except AppError:
            logger.debug("Reading history without a loadable configuration")
        if project not in directories:
            directories.append(project)
        found = [
            record
            for directory in directories
            for record in SyncHistory(directory, keep=DEFAULT_HISTORY_KEEP).records()
            if absolute(Path(record.config)) == target
        ]
        found.sort(key=lambda record: record.started, reverse=True)
        return found[:limit]


class HarnessesCommand:
    """List built-in harness profiles."""

    def __init__(self, profiles: ProfileRegistry) -> None:
        self._profiles = profiles

    def run(self, out: TextIO) -> int:
        """Print the harness profile table."""
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
        uncarried = self._uncarried_conditions(settings, job)
        if settings.dry_run:
            out.write(f"Would register with {chosen.title} ({chosen.location}):\n")
            out.write(chosen.definition(job))
            if uncarried:
                out.write(f"Warning: {uncarried}\n")
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
        if uncarried:
            out.write(f"Warning: {uncarried}\n")
        out.write(
            "Check it with `skillsinc schedule status`;"
            " remove it with `skillsinc schedule remove`.\n"
        )
        return 0

    def _uncarried_conditions(self, settings: ScheduleSettings, job: Job) -> str:
        """Explain profile ``when.env`` variables the scheduled job may not see.

        A job carries only the fixed variables, so a profile selected by
        another shell variable can be active interactively and inactive when
        scheduled, and the scheduled run would then remove its items from
        shared destinations. Values are never copied into job files.
        """
        if SELECTION_VARIABLE in job.variables:
            return ""
        path = settings.config_path or self._environment.default_config_file
        try:
            config = ConfigLoader(ProfileRegistry(), self._environment).load(path)
        except AppError:
            return ""
        names = sorted(
            {
                name
                for profile in config.profiles.values()
                if profile.default and profile.when is not None
                for name in profile.when.variables
                if name not in job.variables
            }
        )
        if not names:
            return ""
        return (
            f"profile conditions read {', '.join(names)}, which scheduled runs may"
            f" not see; set {SELECTION_VARIABLE} to the profiles this machine"
            " applies (saved with setx on Windows) and run install again"
        )

    def _job(
        self, chosen: Backend, settings: ScheduleSettings
    ) -> tuple[Job, Path | None]:
        """Return the job to register and the log it appends to, if any."""
        platform = self._environment.platform
        # -P keeps the job's working directory off the import path
        python = str(interpreter(self._executable, platform))
        argv = [python, "-P", "-m", "skillsinc", "sync", "--trigger", "scheduled"]
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
