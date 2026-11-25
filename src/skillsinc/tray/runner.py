"""Run ``skillsinc sync`` in a child process, one at a time.

The tray never syncs in its own process: a child keeps the tray responsive,
contains a crash, honours the same state lock as every other run, and always
runs the installed release, even after an update. The child records itself in
the history; the tray reads that record when it exits.

On Windows the child is ``pythonw.exe``, so no console window appears, and
its own children (git, gh) start hidden as decision 0012 describes.
"""

from __future__ import annotations

import logging
import sys
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from skillsinc.history import Trigger
from skillsinc.schedule import interpreter

logger = logging.getLogger(__name__)

# A sync that has not finished after this long is stopped
TIMEOUT_MS: Final = 30 * 60 * 1000
# Output kept per run for the history window's details
MAXIMUM_OUTPUT: Final = 64 * 1024


@dataclass(frozen=True, slots=True)
class SyncRequest:
    """One sync to run: what started it and which profiles it selects."""

    trigger: Trigger
    profiles: tuple[str, ...] = ()
    """Profiles passed as ``--profile``; empty uses the machine's defaults."""

    def label(self) -> str:
        """Return a short description for menus and notifications."""
        if not self.profiles:
            return "default profiles"
        if self.profiles == ("*",):
            return "all profiles"
        return ", ".join(self.profiles)


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """How one child process ended."""

    request: SyncRequest
    exit_code: int
    output: str = ""
    crashed: bool = False
    error: str = ""
    """Why the process could not run, when it never started."""

    @property
    def ok(self) -> bool:
        """Return True if the sync succeeded."""
        return self.exit_code == 0 and not self.crashed and not self.error


@dataclass(slots=True)
class _Running:
    request: SyncRequest
    process: QProcess
    config: Path | None = None
    output: bytearray = field(default_factory=bytearray)


class SyncRunner(QObject):
    """Queue sync requests and run each as a ``skillsinc sync`` child process."""

    started = Signal(object)  # SyncRequest
    finished = Signal(object)  # SyncOutcome

    def __init__(
        self,
        config: Path,
        *,
        program: Sequence[str] | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_ms: int = TIMEOUT_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._program = list(program) if program is not None else self._default()
        self._environment = environment
        self._timeout_ms = timeout_ms
        # Each request keeps the configuration current when it was made
        self._queue: deque[tuple[SyncRequest, Path]] = deque()
        self._running: _Running | None = None
        self._done: QProcess | None = None
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._on_timeout)

    @staticmethod
    def _default() -> list[str]:
        """Return the windowless Python running this package as a module."""
        python = interpreter(Path(sys.executable), sys.platform)
        return [str(python), "-P", "-m", "skillsinc"]

    @property
    def busy(self) -> bool:
        """Return True while a sync runs."""
        return self._running is not None

    @property
    def current(self) -> SyncRequest | None:
        """Return the running request, if any."""
        return self._running.request if self._running is not None else None

    @property
    def pending(self) -> tuple[SyncRequest, ...]:
        """Return the queued requests, oldest first."""
        return tuple(request for request, _config in self._queue)

    def set_config(self, config: Path) -> None:
        """Sync ``config`` from the next run on."""
        self._config = config

    def arguments(self, request: SyncRequest, config: Path | None = None) -> list[str]:
        """Return the full command line for ``request`` on ``config``."""
        argv: list[str] = [
            *self._program,
            "sync",
            "--trigger",
            request.trigger.value,
            "--config",
            str(config or self._config),
        ]
        for profile in request.profiles:
            argv += ["--profile", profile]
        return argv

    def request(self, request: SyncRequest) -> bool:
        """Run ``request`` now, or after the running sync; False if already queued.

        A scheduled request is dropped while any sync runs or waits, since
        that sync serves it.
        """
        scheduled = request.trigger in {Trigger.TRAY_SCHEDULED, Trigger.TRAY_STARTUP}
        if scheduled and (self._running is not None or self._queue):
            return False
        if (request, self._config) in self._queue or (
            self._running is not None
            and (self._running.request, self._running.config) == (request, self._config)
        ):
            return False
        self._queue.append((request, self._config))
        if self._running is None:
            self._start_next()
        return True

    def cancel(self) -> None:
        """Drop queued requests and stop the running sync."""
        self._queue.clear()
        if self._running is not None:
            self._running.process.kill()

    def wait(self, msecs: int = 30_000) -> bool:
        """Block until the running sync ends (tests and shutdown)."""
        running = self._running
        if running is None:
            return True
        return running.process.waitForFinished(msecs)

    def _start_next(self) -> None:
        self._done = None
        if not self._queue or self._running is not None:
            return
        request, config = self._queue.popleft()
        process = QProcess()
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        if self._environment is not None:
            environment = QProcessEnvironment()
            for name, value in self._environment.items():
                environment.insert(name, value)
            process.setProcessEnvironment(environment)
        running = _Running(request, process, config)
        self._running = running
        process.readyReadStandardOutput.connect(lambda: self._read(running))
        process.finished.connect(
            lambda code, status: self._on_finished(running, code, status)
        )
        process.errorOccurred.connect(lambda error: self._on_error(running, error))
        argv = self.arguments(request, config)
        logger.info("Starting %s", " ".join(argv))
        self.started.emit(request)
        self._deadline.start(self._timeout_ms)
        process.start(argv[0], argv[1:])

    def _read(self, running: _Running) -> None:
        running.output += bytes(running.process.readAllStandardOutput().data())
        if len(running.output) > MAXIMUM_OUTPUT:
            del running.output[: len(running.output) - MAXIMUM_OUTPUT]

    def _on_finished(
        self, running: _Running, code: int, status: QProcess.ExitStatus
    ) -> None:
        self._read(running)
        crashed = status == QProcess.ExitStatus.CrashExit
        self._finish(
            running,
            SyncOutcome(
                running.request,
                code if not crashed else -1,
                running.output.decode("utf-8", errors="replace"),
                crashed=crashed,
            ),
        )

    def _on_error(self, running: _Running, error: QProcess.ProcessError) -> None:
        if error != QProcess.ProcessError.FailedToStart:
            return  # finished() follows and reports it
        message = f"could not start {self._program[0]}: {running.process.errorString()}"
        self._finish(running, SyncOutcome(running.request, -1, error=message))

    def _finish(self, running: _Running, outcome: SyncOutcome) -> None:
        if self._running is not running:
            return
        self._deadline.stop()
        self._running = None
        # Python owns the process; it is released by the next start, never
        # inside its own signal, so no deferred deletion can outlive the runner
        self._done = running.process
        logger.info("Sync %s ended with %s", outcome.request, outcome.exit_code)
        self.finished.emit(outcome)
        QTimer.singleShot(0, self, self._start_next)

    def _on_timeout(self) -> None:
        if self._running is not None:
            logger.warning("Stopping a sync that ran past the time limit")
            self._running.process.kill()
