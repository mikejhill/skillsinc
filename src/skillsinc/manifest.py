"""Ownership manifests: what SkillSinc wrote to each destination, and its digest."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from skillsinc.exceptions import ConfigurationError

MANIFEST_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class OwnedBlock:
    """A managed block SkillSinc owns inside a shared file."""

    digest: str
    created_file: bool


@dataclass(slots=True)
class Manifest:
    """Files and blocks SkillSinc owns in one destination (mutated while applying)."""

    destination: str
    root: str
    files: dict[str, str] = field(default_factory=dict)
    blocks: dict[str, OwnedBlock] = field(default_factory=dict)

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
        }
        return json.dumps(data, indent=2) + "\n"


class ManifestStore:
    """Load and atomically save manifests under the state directory."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def path(self, destination: str) -> Path:
        """Return the manifest file for ``destination``."""
        return self._directory / "manifests" / f"{destination}.json"

    def load(self, destination: str, root: Path) -> Manifest:
        """Return the manifest for ``destination``, empty if none exists.

        Raises:
            ConfigurationError: If the manifest is corrupt or belongs to another path.
        """
        path = self.path(destination)
        if not path.is_file():
            return Manifest(destination, str(root))
        try:
            data: object = json.loads(path.read_text(encoding="utf-8"))
            manifest = self._parse(destination, data)
        except (OSError, ValueError, KeyError, TypeError) as err:
            msg = f"ownership manifest is unreadable: {path} ({err})"
            raise ConfigurationError(msg) from err
        if manifest.root != str(root):
            msg = (
                f"destination '{destination}' moved from {manifest.root} to {root}."
                f" SkillSinc still tracks files in the old location; delete {path}"
                " to release them, then sync again"
            )
            raise ConfigurationError(msg)
        return manifest

    def save(self, manifest: Manifest) -> None:
        """Atomically replace the manifest file."""
        path = self.path(manifest.destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_suffix(".json.tmp")
        staging.write_text(manifest.to_json(), encoding="utf-8")
        staging.replace(path)

    # Internals

    @staticmethod
    def _parse(destination: str, data: object) -> Manifest:
        """Convert decoded JSON into a ``Manifest``."""
        if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
            msg = "unsupported manifest version"
            raise ValueError(msg)
        files = data["files"]
        blocks = data["blocks"]
        if not isinstance(files, dict) or not isinstance(blocks, dict):
            msg = "files and blocks must be objects"
            raise TypeError(msg)
        return Manifest(
            destination=destination,
            root=str(data["root"]),
            files={str(path): str(digest) for path, digest in files.items()},
            blocks={
                str(path): OwnedBlock(str(entry["digest"]), bool(entry["created_file"]))
                for path, entry in blocks.items()
            },
        )
