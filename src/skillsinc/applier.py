"""Apply a plan: atomic per-file writes, precondition checks, and manifest updates."""

from __future__ import annotations

import logging
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from skillsinc.exceptions import ConflictError
from skillsinc.manifest import ManifestStore, Ownership
from skillsinc.models import Digest
from skillsinc.paths import destination_file
from skillsinc.plan import Action, Change, DestinationPlan, Plan

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DestinationResult:
    """What happened when applying one destination's plan."""

    name: str
    applied: int
    error: str | None = None


class Applier:
    """Execute planned changes, journaling ownership after every successful step."""

    def __init__(self, store: ManifestStore) -> None:
        self._store = store

    def apply(self, plan: Plan) -> tuple[DestinationResult, ...]:
        """Apply every unblocked destination, covering destinations first.

        One failure never affects another, except that a destination relying on
        a failed one to deliver items it skips is not applied at all.
        """
        results: dict[str, DestinationResult] = {}
        for destination in self._ordered(plan.destinations):
            if destination.blocked or not destination.changes:
                continue
            name = destination.destination.name
            failed = [
                other
                for other in destination.depends_on
                if other in results and results[other].error is not None
            ]
            missing = self._missing(destination.requires)
            if failed:
                results[name] = DestinationResult(
                    name,
                    0,
                    f"not applied: {', '.join(failed)} failed, and it skips"
                    " items that destination was to deliver",
                )
            elif missing is not None:
                results[name] = DestinationResult(
                    name,
                    0,
                    f"not applied: {missing} is missing or changed, and it skips"
                    " the item that file delivers",
                )
            else:
                results[name] = self._apply_destination(destination)
        return tuple(
            results[destination.destination.name]
            for destination in plan.destinations
            if destination.destination.name in results
        )

    @staticmethod
    def _missing(requires: Sequence[tuple[Path, str]]) -> Path | None:
        """Return the first required file that is absent or has other content."""
        for path, digest in requires:
            try:
                present = path.is_file() and Digest.of(path.read_bytes()) == digest
            except OSError:
                present = False  # unreadable counts as missing
            if not present:
                return path
        return None

    @staticmethod
    def _ordered(
        destinations: Sequence[DestinationPlan],
    ) -> list[DestinationPlan]:
        """Return destinations with each one after those it depends on."""
        names = {destination.destination.name for destination in destinations}
        done: set[str] = set()
        ordered: list[DestinationPlan] = []
        remaining = list(destinations)
        while remaining:
            ready = [
                destination
                for destination in remaining
                if all(
                    other in done or other not in names
                    for other in destination.depends_on
                )
            ] or remaining[:1]
            for destination in ready:
                ordered.append(destination)
                done.add(destination.destination.name)
                remaining.remove(destination)
        return ordered

    # Steps

    def _apply_destination(self, plan: DestinationPlan) -> DestinationResult:
        """Apply one destination, stopping at the first failed precondition.

        Ownership is journaled after every step and the manifest saved once
        at the end, so an interrupted run still knows everything it wrote.
        """
        destination = plan.destination
        manifest = self._store.load(destination.name, destination.path)
        applied = 0
        try:
            with self._store.journal(manifest) as journal:
                for step in self._steps(plan.changes):
                    try:
                        self._apply_step(destination.path, step)
                    except (ConflictError, OSError) as err:
                        path = step[0].path
                        logger.error("%s: %s: %s", destination.name, path, err)  # noqa: TRY400 — expected failure, no traceback
                        return DestinationResult(
                            destination.name, applied, f"{path}: {err}"
                        )
                    journal.record(
                        tuple(
                            Ownership(
                                change.path,
                                change.owned_digest,
                                change.block,
                                change.entry,
                                change.created_file,
                            )
                            for change in step
                        )
                    )
                    applied += len(step)
        finally:
            self._store.save(manifest)
        return DestinationResult(destination.name, applied)

    @staticmethod
    def _steps(changes: Sequence[Change]) -> list[tuple[Change, ...]]:
        """Group the changes applied as one step.

        Entry changes to one shared file follow each other, expect the same
        bytes, and carry the same result, so the file is written once.
        """
        steps: list[list[Change]] = []
        for change in changes:
            previous = steps[-1][-1] if steps else None
            if (
                previous is not None
                and change.entry
                and previous.entry
                and (previous.path, previous.expected) == (change.path, change.expected)
            ):
                steps[-1].append(change)
            else:
                steps.append([change])
        return [tuple(step) for step in steps]

    def _apply_step(self, root: Path, step: Sequence[Change]) -> None:
        """Verify the file is as planned, then perform the step's write, if any."""
        target = destination_file(root, step[0].path)
        self._verify(target, step[0].expected)
        writes = [change for change in step if change.writes]
        if not writes:
            return
        change = writes[-1]
        if change.action is Action.DELETE and change.content is None:
            target.unlink()
            self._prune(target.parent, root)
        else:
            self._write(target, change)

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
        """Write ``change.content`` atomically via a sibling temporary file.

        A shared file keeps its permissions, so a private configuration file
        holding credentials stays private.
        """
        content = change.content or b""
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f".{target.name}.skillsinc-tmp")
        staging.write_bytes(content)
        if (change.entry or change.block) and target.is_file():
            staging.chmod(stat.S_IMODE(target.stat().st_mode))
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
