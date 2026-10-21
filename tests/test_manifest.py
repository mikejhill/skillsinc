"""Test ownership serialization, storage, and corrupt manifest rejection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skillsinc.exceptions import ConfigurationError
from skillsinc.manifest import (
    Manifest,
    ManifestStore,
    OwnedBlock,
    OwnedEntries,
    Ownership,
)


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
            '{"version":3,"root":"x","files":{},"blocks":{}}',
            '{"version":2,"root":"x","files":{},"blocks":{},"entries":[]}',
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


class TestManifestEntries:
    """Entry ownership round-trips and older manifests upgrade in place."""

    def test_entries_round_trip(self, tmp_path: Path) -> None:
        """Persist per-key entry digests and whether SkillSinc created the file."""
        store = ManifestStore(tmp_path / "state")
        manifest = Manifest(
            "target",
            str(tmp_path),
            entries={
                "mcp-config.json": OwnedEntries(
                    created_file=True, keys={"mcpServers/a": "digest"}
                )
            },
        )

        store.save(manifest)

        assert store.load("target", tmp_path) == manifest, (
            f"Expected entry ownership preserved, got {manifest!r}"
        )

    def test_version_one_manifest_loads_without_entries(self, tmp_path: Path) -> None:
        """Manifests written before entries existed still load."""
        store = ManifestStore(tmp_path)
        path = store.path("target")
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {"version": 1, "root": str(tmp_path), "files": {"a": "d"}, "blocks": {}}
            ),
            encoding="utf-8",
        )

        result = store.load("target", tmp_path)

        assert result == Manifest("target", str(tmp_path), {"a": "d"}), (
            f"Expected upgraded manifest, got {result!r}"
        )


class TestJournal:
    """Changes journaled by an interrupted run are replayed, never lost or misread."""

    @staticmethod
    def interrupted(store: ManifestStore, root: Path, *ownerships: Ownership) -> None:
        """Journal ``ownerships`` as a run that never saved its manifest would."""
        with store.journal(Manifest("target", str(root))) as journal:
            for ownership in ownerships:
                journal.record((ownership,))

    def test_journal_replays_onto_the_saved_manifest(self, tmp_path: Path) -> None:
        """Every kind of ownership change is replayed in order."""
        store = ManifestStore(tmp_path / "state")
        store.save(Manifest("target", str(tmp_path), {"old": "d0", "kept": "k"}))
        self.interrupted(
            store,
            tmp_path,
            Ownership("new", "d1"),
            Ownership("old", None),
            Ownership("AGENTS.md", "b", block=True, created_file=True),
            Ownership("mcp.json", "e", entry="servers/a", created_file=True),
            Ownership("mcp.json", "e", entry="servers/b", created_file=True),
            Ownership("mcp.json", None, entry="servers/b"),
        )

        result = store.load("target", tmp_path)

        assert result == Manifest(
            "target",
            str(tmp_path),
            {"kept": "k", "new": "d1"},
            {"AGENTS.md": OwnedBlock("b", created_file=True)},
            {"mcp.json": OwnedEntries(created_file=True, keys={"servers/a": "e"})},
        ), f"Got {result!r}"

    def test_releasing_the_last_block_or_entry_drops_its_record(
        self, tmp_path: Path
    ) -> None:
        """A released block or last entry leaves no empty record behind."""
        store = ManifestStore(tmp_path / "state")
        store.save(
            Manifest(
                "target",
                str(tmp_path),
                blocks={"AGENTS.md": OwnedBlock("b", created_file=False)},
                entries={
                    "mcp.json": OwnedEntries(
                        created_file=False, keys={"servers/a": "e"}
                    )
                },
            )
        )
        self.interrupted(
            store,
            tmp_path,
            Ownership("AGENTS.md", None, block=True),
            Ownership("mcp.json", None, entry="servers/a"),
        )

        result = store.load("target", tmp_path)

        assert (result.blocks, result.entries) == ({}, {}), f"Got {result!r}"

    def test_line_cut_short_is_ignored_then_dropped(self, tmp_path: Path) -> None:
        """A half-written last line is skipped and never merged with the next."""
        store = ManifestStore(tmp_path / "state")
        self.interrupted(store, tmp_path, Ownership("a", "d"))
        with store.journal_path("target").open("a", encoding="utf-8") as stream:
            stream.write('[{"path": "b", "dig')

        before = store.load("target", tmp_path).files
        self.interrupted(store, tmp_path, Ownership("c", "d"))
        after = store.load("target", tmp_path).files

        assert (before, after) == ({"a": "d"}, {"a": "d", "c": "d"}), (
            f"Got {before!r} then {after!r}"
        )

    def test_shared_file_write_is_recovered_whole(self, tmp_path: Path) -> None:
        """Every entry one write put on disk is recorded together, or not at all."""
        store = ManifestStore(tmp_path / "state")
        step = tuple(
            Ownership("mcp.json", "e", entry=f"servers/{key}", created_file=True)
            for key in ("a", "b")
        )
        with store.journal(Manifest("target", str(tmp_path))) as journal:
            journal.record(step)
        whole = store.load("target", tmp_path).entries
        path = store.journal_path("target")
        raw = path.read_bytes()
        path.write_bytes(raw[: raw.rindex(b"servers/b")])

        torn = store.load("target", tmp_path).entries

        assert whole == {
            "mcp.json": OwnedEntries(
                created_file=True, keys={"servers/a": "e", "servers/b": "e"}
            )
        }, f"Expected both entries recovered, got {whole!r}"
        assert torn == {}, (
            f"Expected a cut-short step recovered not at all, got {torn!r}"
        )

    def test_header_cut_short_is_rewritten(self, tmp_path: Path) -> None:
        """A journal interrupted inside its first line starts over cleanly."""
        store = ManifestStore(tmp_path / "state")
        path = store.journal_path("target")
        path.parent.mkdir(parents=True)
        path.write_text('{"ro', encoding="utf-8")

        empty = store.load("target", tmp_path)
        self.interrupted(store, tmp_path, Ownership("a", "d"))

        assert (empty.files, store.load("target", tmp_path).files) == ({}, {"a": "d"})

    @pytest.mark.parametrize(
        "text",
        [
            '{"root": "ROOT"}\nnot json\n[{"path": "a"}]\n',
            '{"root": "ROOT"}\n[{"path": "a"}]\n',
            '{"root": "ROOT"}\n{"path": "a"}\n',
            '{"root": "ROOT"}\n[[]]\n',
            '{"path": "a", "digest": "d"}\n',
            '{"root": "elsewhere"}\n',
        ],
    )
    def test_corrupt_journal_fails_closed(self, tmp_path: Path, text: str) -> None:
        """Complete lines that are not recorded changes raise configuration errors."""
        store = ManifestStore(tmp_path / "state")
        store.save(Manifest("target", str(tmp_path)))
        path = store.journal_path("target")
        path.write_text(
            text.replace("ROOT", json.dumps(str(tmp_path))[1:-1]), encoding="utf-8"
        )

        with pytest.raises(ConfigurationError, match="journal is unreadable"):
            store.load("target", tmp_path)

    def test_journal_alone_names_its_root(self, tmp_path: Path) -> None:
        """A first run cut short still refuses a destination that has moved."""
        store = ManifestStore(tmp_path / "state")
        self.interrupted(store, tmp_path / "old", Ownership("a", "d"))

        with pytest.raises(ConfigurationError, match=r"moved from .*\.journal"):
            store.load("target", tmp_path / "new")
