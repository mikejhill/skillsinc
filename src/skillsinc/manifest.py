"""Ownership manifests: what SkillSinc wrote to each destination, and its digest."""

from __future__ import annotations

import json
import os
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from typing import TextIO

from skillsinc.exceptions import ConfigurationError

MANIFEST_VERSION: Final = 2
READABLE_VERSIONS: Final = frozenset({1, MANIFEST_VERSION})


@dataclass(frozen=True, slots=True)
class OwnedBlock:
    """A managed block SkillSinc owns inside a shared file."""

    digest: str
    created_file: bool


@dataclass(slots=True)
class OwnedEntries:
    """Keyed entries SkillSinc owns in a shared config file (mutated while applying)."""

    created_file: bool
    keys: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Manifest:
    """Files and blocks SkillSinc owns in one destination (mutated while applying)."""

    destination: str
    root: str
    files: dict[str, str] = field(default_factory=dict)
    blocks: dict[str, OwnedBlock] = field(default_factory=dict)
    entries: dict[str, OwnedEntries] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize to stable, human-readable JSON."""
        data = {
            "version": MANIFEST_VERSION,
            "destination": self.destination,
            "root": self.root,
            "files": dict(sorted(self.files.items())),
            "blocks": {
                path: {"digest": block.digest, "created_file": block.created_file}
                for path, block in sorted(self.blocks.items())
            },
            "entries": {
                path: {
                    "created_file": owned.created_file,
                    "keys": dict(sorted(owned.keys.items())),
                }
                for path, owned in sorted(self.entries.items())
            },
        }
        return json.dumps(data, indent=2) + "\n"


@dataclass(frozen=True, slots=True)
class Ownership:
    """One applied change to what SkillSinc owns; a None digest releases it."""

    path: str
    digest: str | None
    block: bool = False
    entry: str = ""
    created_file: bool = False

    def apply(self, manifest: Manifest) -> None:
        """Record this change in ``manifest``."""
        if self.entry:
            owned = manifest.entries.setdefault(
                self.path, OwnedEntries(self.created_file)
            )
            if self.digest is None:
                owned.keys.pop(self.entry, None)
            else:
                owned.keys[self.entry] = self.digest
            if not owned.keys:
                manifest.entries.pop(self.path)
            return
        if self.block:
            if self.digest is None:
                manifest.blocks.pop(self.path, None)
            else:
                manifest.blocks[self.path] = OwnedBlock(self.digest, self.created_file)
            return
        if self.digest is None:
            manifest.files.pop(self.path, None)
        else:
            manifest.files[self.path] = self.digest

    @staticmethod
    def parse(data: object) -> Ownership:
        """Return the change a journal line recorded.

        Raises:
            ValueError: If ``data`` is not a recorded change.
        """
        if not isinstance(data, dict):
            msg = "expected an object"
            raise ValueError(msg)  # noqa: TRY004 — callers handle ValueError only
        digest = data["digest"]
        return Ownership(
            str(data["path"]),
            None if digest is None else str(digest),
            bool(data["block"]),
            str(data["entry"]),
            bool(data["created_file"]),
        )


class Journal:
    """Changes applied since the manifest was last saved, one JSON line each.

    Each line is one applied step. Appending a line costs the same however
    large the manifest is, so a run
    that writes thousands of files stays linear while an interrupted run still
    loses track of nothing it wrote. Saving the manifest folds the journal in.
    """

    def __init__(self, manifest: Manifest, stream: TextIO) -> None:
        self._manifest = manifest
        self._stream = stream

    def record(self, step: Sequence[Ownership]) -> None:
        """Apply one step's changes to the manifest and append them as one line.

        A step is everything one write did, such as every entry of a shared
        file, so an interruption keeps all of it or none of it.
        """
        line = json.dumps([asdict(ownership) for ownership in step]) + "\n"
        for ownership in step:
            ownership.apply(self._manifest)
        self._stream.write(line)
        self._stream.flush()


class ManifestStore:
    """Load and atomically save manifests under the state directory."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def path(self, destination: str) -> Path:
        """Return the manifest file for ``destination``."""
        return self._directory / "manifests" / f"{destination}.json"

    def journal_path(self, destination: str) -> Path:
        """Return the journal of changes not yet saved into the manifest."""
        return self._directory / "manifests" / f"{destination}.journal"

    def load(self, destination: str, root: Path) -> Manifest:
        """Return the manifest for ``destination``, empty if none exists.

        Changes a run journaled but never saved, because it was interrupted,
        are replayed on top.

        Raises:
            ConfigurationError: If the manifest or journal is corrupt, or the
                manifest belongs to another path.
        """
        path = self.path(destination)
        manifest = Manifest(destination, str(root))
        saved = path.is_file()
        if saved:
            try:
                data: object = json.loads(path.read_text(encoding="utf-8"))
                manifest = self._parse(destination, data)
            except (OSError, ValueError, KeyError, TypeError) as err:
                msg = f"ownership manifest is unreadable: {path} ({err})"
                raise ConfigurationError(msg) from err
        self._replay(manifest, saved=saved)
        if manifest.root != str(root):
            moved = path if saved else self.journal_path(destination)
            msg = (
                f"destination '{destination}' moved from {manifest.root} to {root}."
                f" SkillSinc still tracks files in the old location; delete {moved}"
                " to release them, then sync again"
            )
            raise ConfigurationError(msg)
        return manifest

    def save(self, manifest: Manifest) -> None:
        """Atomically replace the manifest file, then drop the folded-in journal."""
        path = self.path(manifest.destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_suffix(".json.tmp")
        staging.write_text(manifest.to_json(), encoding="utf-8")
        staging.replace(path)
        self.journal_path(manifest.destination).unlink(missing_ok=True)

    @contextmanager
    def journal(self, manifest: Manifest) -> Generator[Journal]:
        """Open the journal for appending changes to ``manifest`` as they apply.

        A line cut short by an interruption is dropped first, so new lines
        never run into it.
        """
        path = self.journal_path(manifest.destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and not path.read_bytes().endswith(b"\n"):
            os.truncate(path, path.read_bytes().rfind(b"\n") + 1)
        if not path.is_file() or not path.stat().st_size:
            path.write_text(
                json.dumps({"root": manifest.root}) + "\n", encoding="utf-8"
            )
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            yield Journal(manifest, stream)

    # Internals

    def _replay(self, manifest: Manifest, *, saved: bool) -> None:
        """Apply journaled changes to ``manifest``; a cut-short last line is ignored.

        The first line names the destination root, which must match a saved
        manifest's.

        Raises:
            ConfigurationError: If a complete line is not what was recorded.
        """
        path = self.journal_path(manifest.destination)
        if not path.is_file():
            return
        try:
            lines = path.read_text(encoding="utf-8").split("\n")[:-1]
            if not lines:
                return
            header: object = json.loads(lines[0])
            if not isinstance(header, dict) or not isinstance(header.get("root"), str):
                msg = "the first line does not name the destination root"
                raise ValueError(msg)  # noqa: TRY004, TRY301 — reported below
            if saved and header["root"] != manifest.root:
                msg = f"it is for {header['root']}, the manifest for {manifest.root}"
                raise ValueError(msg)  # noqa: TRY301 — reported below
            manifest.root = header["root"]
            for line in lines[1:]:
                step: object = json.loads(line)
                if not isinstance(step, list):
                    msg = "expected a list of changes"
                    raise ValueError(msg)  # noqa: TRY004, TRY301 — reported below
                for data in step:
                    Ownership.parse(data).apply(manifest)
        except (OSError, ValueError, KeyError) as err:
            msg = f"ownership journal is unreadable: {path} ({err})"
            raise ConfigurationError(msg) from err

    @staticmethod
    def _parse(destination: str, data: object) -> Manifest:
        """Convert decoded JSON into a ``Manifest``."""
        if not isinstance(data, dict) or data.get("version") not in READABLE_VERSIONS:
            msg = "unsupported manifest version"
            raise ValueError(msg)
        files = data["files"]
        blocks = data["blocks"]
        entries = data.get("entries", {})
        if not all(isinstance(value, dict) for value in (files, blocks, entries)):
            msg = "files, blocks, and entries must be objects"
            raise TypeError(msg)
        return Manifest(
            destination=destination,
            root=str(data["root"]),
            files={str(path): str(digest) for path, digest in files.items()},
            blocks={
                str(path): OwnedBlock(str(entry["digest"]), bool(entry["created_file"]))
                for path, entry in blocks.items()
            },
            entries={
                str(path): OwnedEntries(
                    bool(entry["created_file"]),
                    {str(key): str(digest) for key, digest in entry["keys"].items()},
                )
                for path, entry in entries.items()
            },
        )
