"""Typed settings for each command, produced by the command-line parser."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Literal

from skillsinc.history import Trigger


@dataclass(frozen=True, slots=True)
class InitSettings:
    """Settings for ``skillsinc init``."""

    config_path: Path
    source: Path | None
    force: bool
    log_level: str
    with_guidance: bool = False
    project: bool = False


@dataclass(frozen=True, slots=True)
class PlanSettings:
    """Settings for ``skillsinc plan`` and ``skillsinc sync``."""

    config_path: Path
    apply: bool
    destinations: tuple[str, ...]
    adopt: bool
    json_output: bool
    verbose: bool
    log_level: str
    log_file: Path | None = None
    profiles: tuple[str, ...] = ()
    """Sync profiles named by ``--profile``; empty uses the machine's defaults."""
    trigger: Trigger = Trigger.MANUAL
    """What started a sync, as the history records it."""


@dataclass(frozen=True, slots=True)
class HistorySettings:
    """Settings for ``skillsinc history``."""

    config_path: Path
    json_output: bool
    limit: int
    log_level: str


@dataclass(frozen=True, slots=True)
class HarnessesSettings:
    """Settings for ``skillsinc harnesses``."""

    log_level: str


@dataclass(frozen=True, slots=True)
class ProfilesSettings:
    """Settings for ``skillsinc profiles``."""

    config_path: Path
    json_output: bool
    log_level: str
    profiles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScheduleSettings:
    """Settings for ``skillsinc schedule install|remove|status``."""

    action: Literal["install", "remove", "status"]
    scheduler: str | None
    interval: timedelta
    config_path: Path | None
    dry_run: bool
    log_level: str


@dataclass(frozen=True, slots=True)
class StatusSettings:
    """Settings for ``skillsinc status``."""

    config_path: Path
    json_output: bool
    log_level: str


type Settings = (
    HarnessesSettings
    | HistorySettings
    | InitSettings
    | PlanSettings
    | ProfilesSettings
    | ScheduleSettings
    | StatusSettings
)
