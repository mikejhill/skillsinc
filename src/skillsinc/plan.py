"""The plan: every change a sync would make, decided before anything is written."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from skillsinc.config import DestinationConfig


class Action(StrEnum):
    """What applying a change does to the destination and the manifest."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ADOPT = "adopt"
    TRACK = "track"
    FORGET = "forget"


@dataclass(frozen=True, slots=True)
class Change:
    """One preconditioned change to one destination file.

    ``block`` marks the managed Markdown block; ``entry`` names the
    ``container/key`` a change sets or removes inside a shared JSON file.
    """

    action: Action
    path: str
    expected: str | None
    content: bytes | None = None
    owned_digest: str | None = None
    block: bool = False
    entry: str = ""
    created_file: bool = False
    executable: bool = False
    note: str = ""

    @property
    def writes(self) -> bool:
        """Return True if applying this change modifies the filesystem."""
        return self.action in {
            Action.CREATE,
            Action.UPDATE,
            Action.ADOPT,
            Action.DELETE,
        }


@dataclass(frozen=True, slots=True)
class Skip:
    """A routed item the destination does not receive."""

    item: str
    reason: str
    lossy: bool = False


@dataclass(frozen=True, slots=True)
class DestinationPlan:
    """Changes, skips, and blocking errors for one destination."""

    destination: DestinationConfig
    changes: tuple[Change, ...] = ()
    skipped: tuple[Skip, ...] = ()
    notes: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    delivered: tuple[str, ...] = ()
    """Labels (``source:kind/name``) of the items this destination receives."""

    @property
    def blocked(self) -> bool:
        """Return True if errors prevent applying any change here."""
        return bool(self.errors)

    def counts(self) -> Counter[str]:
        """Return the number of changes per action."""
        return Counter(change.action.value for change in self.changes)


@dataclass(frozen=True, slots=True)
class Plan:
    """The complete plan across destinations."""

    destinations: tuple[DestinationPlan, ...]
    source_errors: Mapping[str, str]
    warnings: tuple[str, ...] = ()
    """Problems that do not block a sync, such as a harness loading an item twice."""

    @property
    def ok(self) -> bool:
        """Return True if no source failed and no destination is blocked."""
        return not self.source_errors and not any(
            destination.blocked for destination in self.destinations
        )

    @property
    def has_changes(self) -> bool:
        """Return True if any unblocked destination has changes."""
        return any(
            destination.changes and not destination.blocked
            for destination in self.destinations
        )
