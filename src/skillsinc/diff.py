"""Compare a destination's desired content with its files and ownership records."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from skillsinc.blocks import ManagedBlock
from skillsinc.exceptions import ConflictError
from skillsinc.manifest import Manifest, OwnedBlock
from skillsinc.models import Digest, FileOutput
from skillsinc.plan import Action, Change

UNMANAGED = "exists but is not managed by SkillSinc; review it, then rerun with --adopt"


class DestinationDiff:
    """Produce preconditioned changes for one destination directory."""

    def __init__(self, root: Path, manifest: Manifest, *, adopt: bool) -> None:
        self._root = root
        self._manifest = manifest
        self._adopt = adopt
        self._blocks = ManagedBlock()
        self._changes: list[Change] = []
        self._errors: list[str] = []

    def compare(
        self,
        files: Mapping[str, FileOutput],
        blocks: Mapping[str, Sequence[tuple[str, str]]],
    ) -> tuple[tuple[Change, ...], tuple[str, ...]]:
        """Return ``(changes, errors)``; any error blocks the whole destination."""
        for path in sorted(files):
            self._guarded(path, lambda p=path: self._desired_file(files[p]))
        for path in sorted(set(self._manifest.files) - set(files)):
            self._guarded(path, lambda p=path: self._removed_file(p))
        for path in sorted(set(blocks) | set(self._manifest.blocks)):
            sections = sorted(blocks.get(path, ()))
            self._guarded(path, lambda p=path, s=sections: self._block(p, s))
        return tuple(self._changes), tuple(self._errors)

    # Files

    def _desired_file(self, output: FileOutput) -> None:
        """Plan a file that should exist with ``output``'s content."""
        path = output.path
        current = self._read(path)
        owned = self._manifest.files.get(path)
        wanted = Digest.of(output.content)
        if current is None:
            note = "restores a deleted managed file" if owned is not None else ""
            self._add(Action.CREATE, output, None, note)
            return

        found = Digest.of(current)
        if owned is None:
            if not self._adopt:
                same = " (identical content)" if found == wanted else ""
                self._errors.append(f"{path}: {UNMANAGED}{same}")
                return
            action = Action.TRACK if found == wanted else Action.ADOPT
            self._add(action, output, found, "")
        elif found != wanted:
            note = "overwrites local edits" if found != owned else ""
            self._add(Action.UPDATE, output, found, note)
        elif owned != wanted:
            self._add(Action.TRACK, output, found, "")

    def _removed_file(self, path: str) -> None:
        """Plan an owned file that is no longer desired."""
        current = self._read(path)
        if current is None:
            self._changes.append(Change(Action.FORGET, path, None, note="already gone"))
            return
        found = Digest.of(current)
        if found == self._manifest.files[path]:
            self._changes.append(Change(Action.DELETE, path, found))
            return
        note = "edited since SkillSinc wrote it, so it is left in place and released"
        self._changes.append(Change(Action.FORGET, path, found, note=note))

    # Blocks

    def _block(self, path: str, sections: Sequence[tuple[str, str]]) -> None:
        """Plan the managed block inside ``path``."""
        raw = self._read(path)
        text = raw.decode("utf-8") if raw is not None else None
        existing = self._blocks.extract(text) if text is not None else None
        owned = self._manifest.blocks.get(path)
        expected = Digest.of(raw) if raw is not None else None

        if not sections:
            self._removed_block(path, text, existing, expected)
            return
        if existing is not None and owned is None and not self._adopt:
            self._errors.append(f"{path}: managed block {UNMANAGED}")
            return

        block = ManagedBlock.build(sections)
        digest = Digest.of(block.encode())
        created = owned.created_file if owned else raw is None
        updated = self._blocks.splice(text, block)
        if updated == text:
            if owned != OwnedBlock(digest, created):
                self._block_change(
                    Action.TRACK, path, expected, None, digest, created=created
                )
            return
        drifted = (
            owned is not None
            and existing is not None
            and Digest.of(existing.encode()) != owned.digest
        )
        action = Action.CREATE if raw is None else Action.UPDATE
        note = "overwrites local edits to the block" if drifted else ""
        self._block_change(
            action, path, expected, updated, digest, created=created, note=note
        )

    def _removed_block(
        self, path: str, text: str | None, existing: str | None, expected: str | None
    ) -> None:
        """Plan removal of an owned block that no longer has sections."""
        owned = self._manifest.blocks[path]
        if text is None or existing is None:
            self._changes.append(
                Change(Action.FORGET, path, expected, block=True, note="already gone")
            )
            return
        remaining = self._blocks.splice(text, None)
        if not remaining.strip() and owned.created_file:
            self._changes.append(Change(Action.DELETE, path, expected, block=True))
            return
        self._block_change(
            Action.UPDATE, path, expected, remaining, None, created=False
        )

    def _block_change(  # noqa: PLR0913 — every field is part of the change record
        self,
        action: Action,
        path: str,
        expected: str | None,
        content: str | None,
        digest: str | None,
        *,
        created: bool,
        note: str = "",
    ) -> None:
        """Record a block change."""
        self._changes.append(
            Change(
                action,
                path,
                expected,
                content=content.encode() if content is not None else None,
                owned_digest=digest,
                block=True,
                created_file=created,
                note=note,
            )
        )

    # Internals

    def _add(
        self, action: Action, output: FileOutput, expected: str | None, note: str
    ) -> None:
        """Record a whole-file change."""
        self._changes.append(
            Change(
                action,
                output.path,
                expected,
                content=output.content,
                owned_digest=Digest.of(output.content),
                executable=output.executable,
                note=note,
            )
        )

    def _guarded(self, path: str, step: Callable[[], None]) -> None:
        """Run ``step`` for ``path``, converting conflicts into blocking errors."""
        try:
            self._check_path(path)
            step()
        except ConflictError as err:
            self._errors.append(f"{path}: {err}")
        except (OSError, UnicodeDecodeError) as err:
            self._errors.append(f"{path}: cannot read ({err})")

    def _check_path(self, path: str) -> None:
        """Refuse paths that escape the root or pass through links or junctions."""
        target = self._root / path
        relative = Path(path)
        if relative.anchor or ".." in relative.parts:
            msg = "path escapes the destination directory"
            raise ConflictError(msg)
        current = self._root
        for part in relative.parts:
            current /= part
            if current.is_symlink() or current.is_junction():
                msg = f"refusing to write through a link or junction at {current}"
                raise ConflictError(msg)
        if target.is_dir():
            msg = "a directory exists where a file is expected"
            raise ConflictError(msg)

    def _read(self, path: str) -> bytes | None:
        """Return the current bytes at ``path``, or None if absent."""
        target = self._root / path
        if not target.is_file():
            return None
        return target.read_bytes()
