"""Test ownership serialization, storage, and corrupt manifest rejection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skillsinc.exceptions import ConfigurationError
from skillsinc.manifest import Manifest, ManifestStore, OwnedBlock


class TestManifestStore:
    """Stable ownership records prevent accidental cross-root reconciliation."""

    def test_missing_manifest_is_empty(self, tmp_path: Path) -> None:
        """A first sync starts with empty ownership for the requested root."""
        result = ManifestStore(tmp_path / "state").load("target", tmp_path)

        assert result == Manifest("target", str(tmp_path)), (
            f"Expected empty manifest, got {result!r}"
        )

    def test_save_round_trip_is_stable_and_atomic(self, tmp_path: Path) -> None:
        """Persist whole-file and block ownership without leaving staging files."""
        store = ManifestStore(tmp_path / "state")
        manifest = Manifest(
            "target",
            str(tmp_path),
            {"z": "two", "a": "one"},
            {"AGENTS.md": OwnedBlock(digest="block", created_file=True)},
        )

        store.save(manifest)
        result = store.load("target", tmp_path)

        assert result == manifest, f"Expected ownership {manifest!r}, got {result!r}"
        assert list(json.loads(manifest.to_json())["files"]) == ["a", "z"], (
            f"Expected sorted file keys, got {manifest.to_json()}"
        )
        assert list(store.path("target").parent.glob("*.tmp")) == [], (
            "Expected no staging files after save"
        )

    @pytest.mark.parametrize(
        "text",
        [
            "{",
            "[]",
            '{"version":2}',
            '{"version":1}',
            '{"version":1,"root":"x","files":[],"blocks":{}}',
            '{"version":1,"root":"x","files":{},"blocks":{"AGENTS.md":{}}}',
        ],
    )
    def test_corrupt_manifest_fails_closed(self, tmp_path: Path, text: str) -> None:
        """Malformed and unsupported ownership records raise configuration errors."""
        store = ManifestStore(tmp_path)
        path = store.path("target")
        path.parent.mkdir(parents=True)
        path.write_text(text, encoding="utf-8")

        with pytest.raises(
            ConfigurationError, match="ownership manifest is unreadable"
        ):
            store.load("target", tmp_path)

    def test_moved_root_requires_explicit_release(self, tmp_path: Path) -> None:
        """Refuse to reuse an old destination manifest at another root."""
        store = ManifestStore(tmp_path)
        store.save(Manifest("target", str(tmp_path / "old")))

        with pytest.raises(ConfigurationError, match="moved from"):
            store.load("target", tmp_path / "new")
