"""Planned edits SkillSinc makes beside, or once to, a configuration file.

Two kinds of change are planned by ``plan`` and applied by ``sync``:

- **One-time upgrades** to the configuration itself. ``upgrades.json`` in the
  configuration's state directory records, per configuration file, the
  SkillSinc version whose upgrades were last applied. An upgrade runs only
  while that record is older than the version that introduced it, so an edit
  the user later undoes is never made again.
- **The editor schema** ``config.schema.json``, kept current whenever the
  configuration names it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Final

from skillsinc.paths import absolute
from skillsinc.schema import REFERENCE, ConfigSchema, SchemaState, write_atomically

RECORD_FILE: Final = "upgrades.json"
RECORD_VERSION: Final = 1
SCHEMA_REFERENCE_SINCE: Final = "0.8.0"


def current_version() -> str:
    """Return the installed SkillSinc version."""
    try:
        return version("skillsinc")
    except PackageNotFoundError:  # pragma: no cover - running from a bare checkout
        return "0"


def version_key(text: str) -> tuple[int, ...]:
    """Return the numeric release parts of ``text``: ``0.8.0rc1`` is ``(0, 8, 0)``."""
    release = text.split("+", maxsplit=1)[0]
    return tuple(int(part) for part in re.findall(r"\d+", release)[:3])


class Action(StrEnum):
    """One planned configuration change."""

    ADD_SCHEMA_REFERENCE = "add-schema-reference"
    CREATE_SCHEMA = "create-schema"
    UPDATE_SCHEMA = "update-schema"


DESCRIPTIONS: Final = {
    Action.ADD_SCHEMA_REFERENCE: 'add "$schema": "' + REFERENCE + '" to {path} (once)',
    Action.CREATE_SCHEMA: "create {path}",
    Action.UPDATE_SCHEMA: "update {path}",
}


@dataclass(frozen=True, slots=True)
class ConfigChange:
    """A change to the configuration file or the schema beside it."""

    action: Action
    path: Path

    def describe(self) -> str:
        """Return the change as one report line."""
        return DESCRIPTIONS[self.action].format(path=self.path)


@dataclass(frozen=True, slots=True)
class MaintenancePlan:
    """Pending changes, and why a pending upgrade cannot run yet."""

    changes: tuple[ConfigChange, ...] = ()
    warnings: tuple[str, ...] = ()
    complete: bool = True
    """False while an upgrade is still owed; the record is then not advanced."""


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    """What ``apply`` changed and what it could not."""

    applied: tuple[ConfigChange, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)


class RecordError(ValueError):
    """``upgrades.json`` exists but cannot be read or is not a valid record."""


class ConfigMaintenance:
    """Plan and apply configuration upgrades and the editor schema."""

    def __init__(self, state: Path, installed: str | None = None) -> None:
        self._record = state / RECORD_FILE
        self._installed = installed or current_version()

    def applied_version(self, config: Path) -> str | None:
        """Return the version whose upgrades ``config`` last received, if any.

        Raises:
            RecordError: If the record exists but is unreadable or invalid.
        """
        configs = self._configs()
        key = self._find(configs, config)
        return None if key is None else configs[key]

    def plan(self, config: Path) -> MaintenancePlan:
        """Return the pending changes, without writing anything."""
        changes: list[ConfigChange] = []
        warnings: list[str] = []
        complete = True
        reference = ConfigSchema.reference(config)
        try:
            pending = self._pending(config, SCHEMA_REFERENCE_SINCE)
        except RecordError as err:
            warnings.append(f"{err}; one-time configuration upgrades skipped")
            pending, complete = False, False
        if pending and reference is None:
            add, problem = self._plan_reference(config)
            if problem is not None:
                warnings.append(problem)
                complete = False
            if add:
                changes.append(ConfigChange(Action.ADD_SCHEMA_REFERENCE, config))
                reference = REFERENCE
        if ConfigSchema.references_local(reference):
            state = ConfigSchema.state(config)
            schema = ConfigSchema.path(config)
            if state is SchemaState.MISSING:
                changes.append(ConfigChange(Action.CREATE_SCHEMA, schema))
            elif state is SchemaState.OUTDATED:
                changes.append(ConfigChange(Action.UPDATE_SCHEMA, schema))
        return MaintenancePlan(tuple(changes), tuple(warnings), complete=complete)

    def apply(self, config: Path) -> MaintenanceResult:
        """Re-plan, apply each change, then record this version for ``config``.

        Failures are warnings. An upgrade that fails or cannot run yet leaves
        the record unchanged, so the next sync tries it again.
        """
        plan = self.plan(config)
        applied: list[ConfigChange] = []
        warnings = list(plan.warnings)
        complete = plan.complete
        for change in plan.changes:
            try:
                if change.action is Action.ADD_SCHEMA_REFERENCE:
                    done = ConfigSchema.add_reference(config)
                else:
                    done = ConfigSchema.refresh(config)
            except (OSError, ValueError) as err:
                warnings.append(f"could not {change.describe()}: {err}")
                if change.action is Action.ADD_SCHEMA_REFERENCE:
                    # The schema changes planned after it assumed the reference
                    complete = False
                    break
                continue
            if done:
                applied.append(change)
        if complete:
            try:
                self._store(config)
            except (OSError, RecordError) as err:
                warnings.append(f"could not record upgrades in {self._record}: {err}")
        return MaintenanceResult(tuple(applied), tuple(warnings))

    # Internals

    @staticmethod
    def _plan_reference(config: Path) -> tuple[bool, str | None]:
        """Return whether to add ``$schema``, and why it must wait, if it must."""
        try:
            needed = ConfigSchema.needs_reference(config)
        except (OSError, ValueError) as err:
            return False, f"could not inspect {config} for $schema: {err}"
        if not needed:
            return False, None
        if ConfigSchema.state(config) is SchemaState.FOREIGN:
            schema = ConfigSchema.path(config)
            return False, (
                f"{schema} is not SkillSinc's schema; $schema was not added to"
                f" {config}. Move it aside and sync again"
            )
        return True, None

    def _pending(self, config: Path, since: str) -> bool:
        """Return True if ``config`` has not received upgrades from ``since``.

        Raises:
            RecordError: If the record exists but is unreadable or invalid.
        """
        applied = self.applied_version(config)
        return applied is None or version_key(applied) < version_key(since)

    @staticmethod
    def _key(config: Path) -> str:
        """Return the record key for ``config``: its absolute path."""
        return absolute(config).as_posix()

    @classmethod
    def _find(cls, configs: dict[str, str], config: Path) -> str | None:
        """Return the stored key naming ``config``, matching by identity too.

        On a case-insensitive file system two spellings open one file, so a
        key that is not an exact match but names the same file counts.
        """
        key = cls._key(config)
        if key in configs:
            return key
        for stored in configs:
            try:
                if Path(stored).samefile(config):
                    return stored
            except OSError:
                continue
        return None

    def _configs(self) -> dict[str, str]:
        """Return the recorded versions; a missing record is empty.

        Raises:
            RecordError: If the record exists but is unreadable or invalid.
        """
        try:
            text = self._record.read_bytes().decode("utf-8")
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError) as err:
            msg = f"cannot read {self._record}: {err}"
            raise RecordError(msg) from err
        try:
            data = json.loads(text)
        except ValueError as err:
            msg = f"{self._record} is not valid JSON: {err}"
            raise RecordError(msg) from err
        configs = data.get("configs") if isinstance(data, dict) else None
        valid = (
            isinstance(data, dict)
            and data.get("version") == RECORD_VERSION
            and isinstance(configs, dict)
            and all(isinstance(value, str) for value in configs.values())
        )
        if not valid:
            msg = f"{self._record} is not a SkillSinc upgrade record"
            raise RecordError(msg)
        return dict(configs)

    def _store(self, config: Path) -> None:
        """Store the newest applied version for ``config``, atomically.

        Raises:
            OSError: If the record cannot be written.
            RecordError: If the existing record is invalid.
        """
        configs = self._configs()
        key = self._find(configs, config) or self._key(config)
        # Upgrades through the newest one are applied, even from a build
        # numbered below it (a development checkout)
        newest = max(self._installed, SCHEMA_REFERENCE_SINCE, key=version_key)
        current = configs.get(key)
        if current is not None and version_key(current) >= version_key(newest):
            return
        configs[key] = newest
        data = {"version": RECORD_VERSION, "configs": dict(sorted(configs.items()))}
        write_atomically(self._record, json.dumps(data, indent=2) + "\n")
