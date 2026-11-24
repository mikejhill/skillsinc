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
from skillsinc.schema import REFERENCE, ConfigSchema, SchemaState

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


@dataclass(frozen=True, slots=True)
class ConfigChange:
    """A change to the configuration file or the schema beside it."""

    action: Action
    path: Path

    def describe(self) -> str:
        """Return the change as one report line."""
        match self.action:
            case Action.ADD_SCHEMA_REFERENCE:
                return f'add "$schema": "{REFERENCE}" to {self.path} (once)'
            case Action.CREATE_SCHEMA:
                return f"create {self.path}"
            case Action.UPDATE_SCHEMA:
                return f"update {self.path}"


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    """What ``apply`` changed and what it could not."""

    applied: tuple[ConfigChange, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)


class ConfigMaintenance:
    """Plan and apply configuration upgrades and the editor schema."""

    def __init__(self, state: Path, installed: str | None = None) -> None:
        self._record = state / RECORD_FILE
        self._installed = installed or current_version()

    def applied_version(self, config: Path) -> str | None:
        """Return the version whose upgrades ``config`` last received, if any."""
        return self._configs().get(self._key(config))

    def plan(self, config: Path) -> tuple[ConfigChange, ...]:
        """Return the pending changes, without writing anything."""
        changes: list[ConfigChange] = []
        reference = ConfigSchema.reference(config)
        if self._pending(config, SCHEMA_REFERENCE_SINCE) and (
            reference is None and ConfigSchema.needs_reference(config)
        ):
            changes.append(ConfigChange(Action.ADD_SCHEMA_REFERENCE, config))
            reference = REFERENCE
        if ConfigSchema.references_local(reference):
            state = ConfigSchema.state(config)
            schema = ConfigSchema.path(config)
            if state is SchemaState.MISSING:
                changes.append(ConfigChange(Action.CREATE_SCHEMA, schema))
            elif state is SchemaState.OUTDATED:
                changes.append(ConfigChange(Action.UPDATE_SCHEMA, schema))
        return tuple(changes)

    def apply(self, config: Path) -> MaintenanceResult:
        """Re-plan, apply each change, then record this version for ``config``.

        Failures are warnings. A failed upgrade leaves the record unchanged,
        so the next sync tries it again.
        """
        applied: list[ConfigChange] = []
        warnings: list[str] = []
        upgraded = True
        for change in self.plan(config):
            try:
                if change.action is Action.ADD_SCHEMA_REFERENCE:
                    done = ConfigSchema.add_reference(config)
                else:
                    done = ConfigSchema.refresh(config)
            except (OSError, ValueError) as err:
                warnings.append(f"could not {change.describe()}: {err}")
                if change.action is Action.ADD_SCHEMA_REFERENCE:
                    # The schema changes planned after it assumed the reference
                    upgraded = False
                    break
                continue
            if done:
                applied.append(change)
        if upgraded and self._pending(config, self._installed):
            try:
                self._store(config)
            except OSError as err:
                warnings.append(f"could not record upgrades in {self._record}: {err}")
        return MaintenanceResult(tuple(applied), tuple(warnings))

    # Internals

    def _pending(self, config: Path, since: str) -> bool:
        """Return True if ``config`` has not received upgrades from ``since``."""
        applied = self.applied_version(config)
        return applied is None or version_key(applied) < version_key(since)

    @staticmethod
    def _key(config: Path) -> str:
        """Return the record key for ``config``: its absolute path."""
        return absolute(config).as_posix()

    def _configs(self) -> dict[str, str]:
        """Return the recorded versions; an unreadable record counts as empty."""
        try:
            data = json.loads(self._record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        configs = data.get("configs") if isinstance(data, dict) else None
        if not isinstance(configs, dict):
            return {}
        return {
            str(key): value for key, value in configs.items() if isinstance(value, str)
        }

    def _store(self, config: Path) -> None:
        """Store this version for ``config``, atomically."""
        configs = self._configs()
        # Upgrades through the newest one are applied, even from a build
        # numbered below it (a development checkout)
        newest = max(self._installed, SCHEMA_REFERENCE_SINCE, key=version_key)
        configs[self._key(config)] = newest
        data = {"version": RECORD_VERSION, "configs": dict(sorted(configs.items()))}
        self._record.parent.mkdir(parents=True, exist_ok=True)
        staging = self._record.with_suffix(".json.tmp")
        staging.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        staging.replace(self._record)
