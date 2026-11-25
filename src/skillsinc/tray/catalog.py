"""What the tray offers to sync: the configuration's profiles and its history."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from skillsinc.commands import HistoryCommand
from skillsinc.config import ConfigLoader
from skillsinc.exceptions import AppError
from skillsinc.history import SyncRecord
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.selection import Origin, ProfileSelector


@dataclass(frozen=True, slots=True)
class ProfileEntry:
    """One sync profile, as the tray's menu shows it."""

    name: str
    description: str
    active: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Catalog:
    """The configuration's profiles, or why they could not be read."""

    profiles: tuple[ProfileEntry, ...] = ()
    error: str = ""
    defined: bool = False
    """True when the configuration defines profiles at all."""

    @property
    def active(self) -> tuple[str, ...]:
        """Return the profiles active on this machine."""
        return tuple(entry.name for entry in self.profiles if entry.active)


class CatalogReader:
    """Read the configuration's profiles and history without running a sync."""

    def __init__(self, environment: Environment) -> None:
        self._environment = environment

    def profiles(self, config: Path) -> Catalog:
        """Return the profiles of ``config`` and whether each is active here."""
        try:
            loaded = ConfigLoader(ProfileRegistry(), self._environment).load(config)
            _, selection = ProfileSelector(self._environment).select(loaded)
        except AppError as err:
            return Catalog(error=str(err))
        if selection.origin is Origin.NONE:
            return Catalog()
        return Catalog(
            profiles=tuple(
                ProfileEntry(
                    state.profile.name,
                    state.profile.description,
                    state.active,
                    state.reason,
                )
                for state in selection.states
            ),
            defined=True,
        )

    def history(self, config: Path, limit: int) -> list[SyncRecord]:
        """Return the newest ``limit`` syncs of ``config``, newest first."""
        return HistoryCommand(self._environment).records(config, limit)
