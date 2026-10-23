"""Typed settings for each command, produced by the command-line parser."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class InitSettings:
    """Settings for ``skillsinc init``."""

    config_path: Path
    source: Path | None
    force: bool
    log_level: str
    with_guidance: bool = False


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


@dataclass(frozen=True, slots=True)
class ProfilesSettings:
    """Settings for ``skillsinc profiles``."""

    log_level: str


@dataclass(frozen=True, slots=True)
class ScheduleSettings:
    """Settings for ``skillsinc schedule install|remove|status``."""

    action: Literal["install", "remove", "status"]
    scheduler: str | None
    interval: timedelta
    config_path: Path | None
    dry_run: bool
    log_level: str


type Settings = InitSettings | PlanSettings | ProfilesSettings | ScheduleSettings
