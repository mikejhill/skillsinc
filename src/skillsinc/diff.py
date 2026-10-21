"""Compare a destination's desired content with its files and ownership records."""

from __future__ import annotations

import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from skillsinc.blocks import ManagedBlock
from skillsinc.entries import EntryFile, JsonDocument
from skillsinc.exceptions import ConflictError
from skillsinc.manifest import Manifest, OwnedBlock
from skillsinc.models import Digest, EntryOutput, FileOutput
from skillsinc.plan import Action, Change

UNMANAGED = "exists but is not managed by SkillSinc; review it, then rerun with --adopt"
EDITED = "edited since SkillSinc wrote it, so it is left in place and released"
BOTH = (
    "wanted in more than one form (whole file, managed block, config entries);"
    " route it one way"
)
EXECUTABLE = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


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
        entries: Mapping[str, Sequence[EntryOutput]] | None = None,
    ) -> tuple[tuple[Change, ...], tuple[str, ...]]:
        """Return ``(changes, errors)``; any error blocks the whole destination."""
        wanted = entries or {}
        overlap = sorted(
            (set(files) & set(blocks))
            | (set(files) & set(wanted))
            | (set(blocks) & set(wanted))
        )
        if overlap:
            return (), tuple(f"{path}: {BOTH}" for path in overlap)
        for path in sorted(files):
            self._guarded(path, lambda p=path: self._desired_file(files[p]))
        for path in sorted(set(self._manifest.files) - set(files)):
            self._guarded(path, lambda p=path: self._removed_file(p))
        for path in sorted(set(blocks) | set(self._manifest.blocks)):
            sections = sorted(blocks.get(path, ()))
            self._guarded(path, lambda p=path, s=sections: self._block(p, s))
        for path in sorted(set(wanted) | set(self._manifest.entries)):
            outputs = {output.entry: output for output in wanted.get(path, ())}
            self._guarded(path, lambda p=path, o=outputs: self._entries(p, o))
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
        matches = found == wanted and not self._mode_differs(output)
        if owned is None:
            if not self._adopt:
                same = " (identical content)" if found == wanted else ""
                self._errors.append(f"{path}: {UNMANAGED}{same}")
                return
            action = Action.TRACK if matches else Action.ADOPT
            self._add(action, output, found, "")
        elif not matches:
            note = "overwrites local edits" if found not in {owned, wanted} else ""
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
        self._changes.append(Change(Action.FORGET, path, found, note=EDITED))

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
        created = raw is None or (owned is not None and owned.created_file)
        updated = self._blocks.splice(text, block)
        if updated == text:
            if owned != OwnedBlock(digest, created):
                self._block_change(
                    Action.TRACK, path, expected, None, digest, created=created
                )
            return
        action = Action.CREATE if raw is None else Action.UPDATE
        note = ""
        if owned is None:
            action = Action.ADOPT if existing is not None else action
        elif existing is None:
            note = "restores a deleted managed block"
        elif Digest.of(existing.encode()) != owned.digest:
            note = "overwrites local edits to the block"
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
        if Digest.of(existing.encode()) != owned.digest:
            self._changes.append(
                Change(Action.FORGET, path, expected, block=True, note=EDITED)
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

    # Entries

    def _entries(self, path: str, outputs: Mapping[str, EntryOutput]) -> None:
        """Plan keyed entries inside a shared JSON or TOML file, one change per entry.

        Each change carries the digest of the file before it and the bytes
        after it, so the applier applies them in order and stops at any drift.
        Removals come last so an emptied file is never deleted and recreated.
        """
        document = EntryFile.open(path, self._read(path))
        owned = self._manifest.entries.get(path)
        created = owned.created_file if owned else document.raw is None
        recorded = owned.keys if owned else {}
        for entry in sorted(outputs):
            change = self._desired_entry(
                document, outputs[entry], recorded.get(entry), created=created
            )
            if change is not None:
                self._changes.append(change)
        for entry in sorted(set(recorded) - set(outputs)):
            self._changes.append(
                self._removed_entry(document, path, entry, created=created)
            )

    def _desired_entry(
        self,
        document: EntryFile,
        output: EntryOutput,
        owned: str | None,
        *,
        created: bool,
    ) -> Change | None:
        """Plan one entry that should hold ``output.value``."""
        expected = Digest.of(document.raw) if document.raw is not None else None
        found = document.digest(output.container, output.key)
        wanted = JsonDocument.digest(output.value)
        if found is None:
            action = Action.CREATE
            note = "restores a deleted managed entry" if owned is not None else ""
        elif owned is None:
            if not self._adopt:
                same = " (identical content)" if found == wanted else ""
                self._errors.append(
                    f"{output.path}: entry {output.entry} {UNMANAGED}{same}"
                )
                return None
            action = Action.TRACK if found == wanted else Action.ADOPT
            note = ""
        elif found != wanted:
            action = Action.UPDATE
            note = "overwrites local edits" if found != owned else ""
        elif owned != wanted:
            action, note = Action.TRACK, ""
        else:
            return None
        content = (
            None
            if action is Action.TRACK
            else document.set(output.container, output.key, output.value)
        )
        return Change(
            action,
            output.path,
            expected,
            content=content,
            owned_digest=wanted,
            entry=output.entry,
            created_file=created,
            note=note,
        )

    def _removed_entry(
        self, document: EntryFile, path: str, entry: str, *, created: bool
    ) -> Change:
        """Plan an owned entry that is no longer desired."""
        container, _, key = entry.partition("/")
        expected = Digest.of(document.raw) if document.raw is not None else None
        found = document.digest(container, key)
        if found is None:
            return Change(
                Action.FORGET, path, expected, entry=entry, note="already gone"
            )
        if found != self._manifest.entries[path].keys[entry]:
            note = (
                "edited since SkillSinc wrote it, so it is left in place and released"
            )
            return Change(Action.FORGET, path, expected, entry=entry, note=note)
        content = document.remove(container, key, prune=created)
        return Change(
            Action.DELETE,
            path,
            expected,
            content=content,
            entry=entry,
            created_file=created,
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

    def _mode_differs(self, output: FileOutput) -> bool:
        """Return True if the file's executable flag differs from ``output``'s."""
        if sys.platform == "win32":
            return False
        mode = (self._root / output.path).stat().st_mode
        return bool(mode & EXECUTABLE) != output.executable

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
