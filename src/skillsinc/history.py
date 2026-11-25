"""A record of every sync: when, why, which profiles, and how it went.

Each ``skillsinc sync`` appends one JSON line to ``history.jsonl`` in its
state directory, whatever started it: a terminal, the operating system's
scheduler, or the tray. The tray's history window and ``skillsinc history``
read it back, newest first.

Appends happen under the state lock, so runs never interleave lines; a line
that cannot be parsed (a crash mid-write, a hand edit) is skipped on read.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from skillsinc.models import JsonValue
from skillsinc.plan import Action, Plan
from skillsinc.schema import write_atomically

logger = logging.getLogger(__name__)

HISTORY_FILE: Final = "history.jsonl"
RECORD_VERSION: Final = 1
# Keep one record's details bounded, however large the sync
MAXIMUM_CHANGES: Final = 200
MAXIMUM_ERRORS: Final = 50
# Trim the file once it holds this many times the records it should keep
TRIM_FACTOR: Final = 2
# No record line is shorter than this many bytes
SMALLEST_RECORD: Final = 160


class Trigger(StrEnum):
    """What started a sync."""

    MANUAL = "manual"
    """``skillsinc sync`` typed in a terminal."""
    SCHEDULED = "scheduled"
    """The operating system's scheduler (``skillsinc schedule install``)."""
    TRAY = "tray"
    """Sync now, from the tray."""
    TRAY_SCHEDULED = "tray-scheduled"
    """The tray's own schedule."""
    TRAY_STARTUP = "tray-startup"
    """The sync the tray runs as it starts."""


@dataclass(frozen=True, slots=True)
class ChangeRecord:
    """One applied change, as the history shows it."""

    destination: str
    action: str
    path: str


@dataclass(frozen=True, slots=True)
class SyncRecord:
    """One sync run."""

    id: str
    started: datetime
    finished: datetime
    trigger: Trigger
    ok: bool
    config: str
    profiles: tuple[str, ...] | None = None
    """The profiles the run selected; None when it ran the default set."""
    version: str = ""
    """The SkillSinc release that ran it."""
    counts: Mapping[str, int] = field(default_factory=dict)
    """Applied changes per action (``create``, ``update``, ...)."""
    destinations: int = 0
    errors: tuple[str, ...] = ()
    warnings: int = 0
    changes: tuple[ChangeRecord, ...] = ()
    """Up to ``MAXIMUM_CHANGES`` of the applied changes."""
    truncated: bool = False
    """True when ``changes`` holds only the first of more."""

    @staticmethod
    def new_id() -> str:
        """Return a short random identifier for a record."""
        return secrets.token_hex(6)

    @property
    def duration(self) -> float:
        """Return the run's length in seconds."""
        return max(0.0, (self.finished - self.started).total_seconds())

    @property
    def changed(self) -> int:
        """Return the number of changes the run applied."""
        return sum(self.counts.values())

    def summary(self) -> str:
        """Return a one-line description: what changed, or why it failed."""
        if not self.ok:
            first = self.errors[0] if self.errors else "failed"
            more = f" (+{len(self.errors) - 1} more)" if len(self.errors) > 1 else ""
            return f"{first}{more}"
        parts = [f"{count} {action}" for action, count in self.counts.items() if count]
        return ", ".join(parts) if parts else "no changes"

    @classmethod
    def of_plan(  # noqa: PLR0913 - one record gathers every fact about a run
        cls,
        plan: Plan,
        *,
        started: datetime,
        finished: datetime,
        trigger: Trigger,
        config: Path,
        profiles: Sequence[str] | None,
        version: str,
        errors: Iterable[str] = (),
        warnings: Iterable[str] = (),
    ) -> SyncRecord:
        """Return the record of a sync that applied ``plan``."""
        counts = dict.fromkeys((action.value for action in Action), 0)
        changes: list[ChangeRecord] = []
        total = 0
        for destination in plan.destinations:
            if destination.blocked:
                continue
            for change in destination.changes:
                counts[change.action.value] += 1
                total += 1
                if len(changes) < MAXIMUM_CHANGES:
                    changes.append(
                        ChangeRecord(
                            destination.destination.name,
                            change.action.value,
                            change.path,
                        )
                    )
        found = [
            *(f"{name}: {error}" for name, error in plan.source_errors.items()),
            *(
                f"{destination.destination.name}: {error}"
                for destination in plan.destinations
                for error in destination.errors
            ),
            *errors,
        ]
        return cls(
            id=cls.new_id(),
            started=started,
            finished=finished,
            trigger=trigger,
            ok=not found,
            config=str(config),
            profiles=tuple(profiles) if profiles is not None else None,
            version=version,
            counts={action: count for action, count in counts.items() if count},
            destinations=len(plan.destinations),
            errors=tuple(found[:MAXIMUM_ERRORS]),
            warnings=len([*plan.warnings, *plan.source_warnings, *warnings]),
            changes=tuple(changes),
            truncated=total > len(changes),
        )

    @classmethod
    def of_failure(  # noqa: PLR0913 - one record gathers every fact about a run
        cls,
        error: str,
        *,
        started: datetime,
        finished: datetime,
        trigger: Trigger,
        config: Path,
        profiles: Sequence[str] | None,
        version: str,
    ) -> SyncRecord:
        """Return the record of a sync that stopped before applying anything."""
        return cls(
            id=cls.new_id(),
            started=started,
            finished=finished,
            trigger=trigger,
            ok=False,
            config=str(config),
            profiles=tuple(profiles) if profiles is not None else None,
            version=version,
            errors=(error,),
        )

    def to_json(self) -> dict[str, JsonValue]:
        """Return the record as one history line holds it."""
        return {
            "v": RECORD_VERSION,
            "id": self.id,
            "started": self.started.isoformat(timespec="milliseconds"),
            "finished": self.finished.isoformat(timespec="milliseconds"),
            "trigger": self.trigger.value,
            "ok": self.ok,
            "config": self.config,
            "profiles": list(self.profiles) if self.profiles is not None else None,
            "version": self.version,
            "counts": dict(self.counts),
            "destinations": self.destinations,
            "errors": list(self.errors),
            "warnings": self.warnings,
            "changes": [
                {"destination": c.destination, "action": c.action, "path": c.path}
                for c in self.changes
            ],
            "truncated": self.truncated,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> SyncRecord:
        """Return the record in one parsed history line.

        Raises:
            ValueError: If a required field is missing or malformed.
        """
        try:
            profiles = data.get("profiles")
            counts = data.get("counts") or {}
            changes = data.get("changes") or []
            if not isinstance(counts, dict) or not isinstance(changes, list):
                raise TypeError  # noqa: TRY301 - same handling as a bad field
            return cls(
                id=str(data["id"]),
                started=datetime.fromisoformat(str(data["started"])),
                finished=datetime.fromisoformat(str(data["finished"])),
                trigger=Trigger(str(data["trigger"])),
                ok=bool(data["ok"]),
                config=str(data.get("config", "")),
                profiles=tuple(str(p) for p in profiles)
                if isinstance(profiles, list)
                else None,
                version=str(data.get("version", "")),
                counts={str(k): int(v) for k, v in counts.items()},
                destinations=int(str(data.get("destinations", 0))),
                errors=tuple(str(e) for e in _list(data.get("errors"))),
                warnings=int(str(data.get("warnings", 0))),
                changes=tuple(
                    ChangeRecord(
                        str(c["destination"]), str(c["action"]), str(c["path"])
                    )
                    for c in changes
                    if isinstance(c, dict)
                ),
                truncated=bool(data.get("truncated", False)),
            )
        except (KeyError, TypeError, ValueError) as err:
            msg = f"malformed history record ({err!r})"
            raise ValueError(msg) from err


def _list(value: object) -> list[object]:
    """Return ``value``'s items if it is a list, else an empty list."""
    return list(value) if isinstance(value, list) else []


class SyncHistory:
    """The append-only ``history.jsonl`` in one state directory."""

    def __init__(self, state: Path, *, keep: int) -> None:
        self._path = state / HISTORY_FILE
        self._keep = keep

    @property
    def path(self) -> Path:
        """Return the history file."""
        return self._path

    def append(self, record: SyncRecord) -> None:
        """Add ``record``; trim the oldest once the file is far over ``keep``.

        A failure to write is logged, never raised: history must not fail a sync.
        """
        line = json.dumps(record.to_json(), ensure_ascii=False, separators=(",", ":"))
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644
            )
            with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as file:
                file.write(line + "\n")
            self._trim()
        except OSError as err:
            logger.warning("Could not record the sync in %s: %s", self._path, err)

    def records(self, limit: int | None = None) -> list[SyncRecord]:
        """Return up to ``limit`` records, newest first, skipping unreadable lines."""
        found: list[SyncRecord] = []
        for line in reversed(self._lines()):
            if limit is not None and len(found) >= limit:
                break
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    found.append(SyncRecord.from_json(data))
            except ValueError:
                logger.debug("Skipping an unreadable history line in %s", self._path)
        return found

    def last(self) -> SyncRecord | None:
        """Return the newest record, or None."""
        newest = self.records(limit=1)
        return newest[0] if newest else None

    def clear(self) -> None:
        """Delete every record."""
        self._path.unlink(missing_ok=True)

    def _lines(self) -> list[str]:
        try:
            text = self._path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return []
        return [line for line in text.splitlines() if line.strip()]

    def _trim(self) -> None:
        # Skip reading the file while it is too small to hold that many records
        if self._path.stat().st_size <= self._keep * TRIM_FACTOR * SMALLEST_RECORD:
            return
        lines = self._lines()
        if len(lines) <= self._keep * TRIM_FACTOR:
            return
        write_atomically(self._path, "\n".join(lines[-self._keep :]) + "\n")


class SyncRecorder:
    """Record one ``skillsinc sync`` run, however it ends."""

    def __init__(  # noqa: PLR0913 - the facts known before the run starts
        self,
        *,
        state: Path,
        keep: int,
        trigger: Trigger,
        config: Path,
        requested: Sequence[str],
        version: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._state = state
        self._keep = keep
        self._trigger = trigger
        self._config = config
        self._requested = tuple(requested) or None
        self._version = version
        self._clock = clock
        self._started = clock()
        self._recorded = False

    def locate(self, state: Path) -> None:
        """Record into ``state``: the configuration's own state directory."""
        self._state = state

    def applied(
        self,
        plan: Plan,
        *,
        profiles: Sequence[str] | None,
        errors: Iterable[str] = (),
        warnings: Iterable[str] = (),
    ) -> SyncRecord:
        """Record a run that applied ``plan``; return the record."""
        record = SyncRecord.of_plan(
            plan,
            started=self._started,
            finished=self._clock(),
            trigger=self._trigger,
            config=self._config,
            profiles=profiles,
            version=self._version,
            errors=errors,
            warnings=warnings,
        )
        self._append(record)
        return record

    def failed(self, error: str) -> SyncRecord | None:
        """Record a run that stopped with ``error``, unless already recorded."""
        if self._recorded:
            return None
        record = SyncRecord.of_failure(
            error,
            started=self._started,
            finished=self._clock(),
            trigger=self._trigger,
            config=self._config,
            profiles=self._requested,
            version=self._version,
        )
        self._append(record)
        return record

    def _append(self, record: SyncRecord) -> None:
        SyncHistory(self._state, keep=self._keep).append(record)
        self._recorded = True
