"""Apply a plan: atomic per-file writes, precondition checks, and manifest updates."""

from __future__ import annotations

import logging
import stat
from dataclasses import dataclass
from pathlib import Path

from skillsinc.exceptions import ConflictError
from skillsinc.manifest import Manifest, ManifestStore, OwnedBlock, OwnedEntries
from skillsinc.models import Digest
from skillsinc.plan import Action, Change, DestinationPlan, Plan

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DestinationResult:
    """What happened when applying one destination's plan."""

    name: str
    applied: int
    error: str | None = None


class Applier:
    """Execute planned changes, recording ownership after every successful step."""

    def __init__(self, store: ManifestStore) -> None:
        self._store = store

    def apply(self, plan: Plan) -> tuple[DestinationResult, ...]:
        """Apply every unblocked destination; one failure never affects another."""
        return tuple(
            self._apply_destination(destination)
            for destination in plan.destinations
            if not destination.blocked and destination.changes
        )

    # Steps

    def _apply_destination(self, plan: DestinationPlan) -> DestinationResult:
        """Apply one destination, stopping at the first failed precondition."""
        destination = plan.destination
        manifest = self._store.load(destination.name, destination.path)
        applied = 0
        for change in plan.changes:
            try:
                self._apply_change(destination.path, change)
            except (ConflictError, OSError) as err:
                logger.error("%s: %s: %s", destination.name, change.path, err)  # noqa: TRY400 — expected failure, no traceback
                self._store.save(manifest)
                return DestinationResult(
                    destination.name, applied, f"{change.path}: {err}"
                )
            self._record(manifest, change)
            self._store.save(manifest)
            applied += 1
        return DestinationResult(destination.name, applied)

    def _apply_change(self, root: Path, change: Change) -> None:
        """Verify the file is as planned, then perform the change."""
        target = root / change.path
        self._verify(target, change.expected)
        match change.action:
            case Action.DELETE if change.content is None:
                target.unlink()
                self._prune(target.parent, root)
            case Action.CREATE | Action.UPDATE | Action.ADOPT | Action.DELETE:
                self._write(target, change)
            case Action.TRACK | Action.FORGET:
                pass

    # Internals

    @staticmethod
    def _verify(target: Path, expected: str | None) -> None:
        """Raise if the file changed since planning (another process edited it)."""
        current = Digest.of(target.read_bytes()) if target.is_file() else None
        if current != expected:
            msg = "changed since the plan was made; run the sync again"
            raise ConflictError(msg)

    @staticmethod
    def _write(target: Path, change: Change) -> None:
        """Write ``change.content`` atomically via a sibling temporary file."""
        content = change.content or b""
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f".{target.name}.skillsinc-tmp")
        staging.write_bytes(content)
        if change.executable:
            mode = staging.stat().st_mode
            staging.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        staging.replace(target)

    @staticmethod
    def _prune(directory: Path, root: Path) -> None:
        """Remove directories emptied by a deletion, stopping at the root."""
        while directory != root and directory.is_relative_to(root):
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent

    @staticmethod
    def _record(manifest: Manifest, change: Change) -> None:
        """Update ownership to reflect an applied change."""
        if change.entry:
            owned = manifest.entries.setdefault(
                change.path, OwnedEntries(change.created_file)
            )
            if change.owned_digest is None:
                owned.keys.pop(change.entry, None)
            else:
                owned.keys[change.entry] = change.owned_digest
            if not owned.keys:
                manifest.entries.pop(change.path)
            return
        if change.block:
            if change.owned_digest is None:
                manifest.blocks.pop(change.path, None)
            else:
                manifest.blocks[change.path] = OwnedBlock(
                    change.owned_digest, change.created_file
                )
            return
        if change.owned_digest is None:
            manifest.files.pop(change.path, None)
        else:
            manifest.files[change.path] = change.owned_digest
