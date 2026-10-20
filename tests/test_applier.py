"""Test preconditions, atomic writes, ownership recording, and partial failure."""

from __future__ import annotations

from pathlib import Path

import pytest

from skillsinc.applier import Applier
from skillsinc.config import DestinationConfig
from skillsinc.manifest import Manifest, ManifestStore, OwnedBlock, OwnedEntries
from skillsinc.models import Digest
from skillsinc.plan import Action, Change, DestinationPlan, Plan


class TestApplier:
    """Apply only planned content and record every successful ownership change."""

    @staticmethod
    def plan(
        root: Path, changes: tuple[Change, ...], *, errors: tuple[str, ...] = ()
    ) -> Plan:
        """Build a one-destination application plan."""
        return Plan(
            (
                DestinationPlan(
                    DestinationConfig("target", "generic", root),
                    changes=changes,
                    errors=errors,
                ),
            ),
            {},
        )

    @pytest.mark.parametrize("action", [Action.CREATE, Action.UPDATE, Action.ADOPT])
    def test_writes_and_records_owned_content(
        self, tmp_path: Path, action: Action
    ) -> None:
        """Writing actions atomically persist content and its ownership digest."""
        root = tmp_path / "destination"
        store = ManifestStore(tmp_path / "state")
        root.mkdir()
        expected = None
        if action is not Action.CREATE:
            (root / "file").write_bytes(b"old")
            expected = Digest.of(b"old")
        change = Change(
            action,
            "file",
            expected,
            content=b"new",
            owned_digest=Digest.of(b"new"),
            executable=True,
        )

        results = Applier(store).apply(self.plan(root, (change,)))

        assert (results[0].applied, results[0].error, (root / "file").read_bytes()) == (
            1,
            None,
            b"new",
        ), f"Expected one successful write, got {results!r}"
        assert store.load("target", root).files == {"file": Digest.of(b"new")}, (
            f"Expected file ownership, got {store.load('target', root)!r}"
        )
        assert list(root.glob(".*skillsinc-tmp")) == [], (
            "Expected no write staging artifacts"
        )

    def test_changed_precondition_stops_after_successful_prefix(
        self, tmp_path: Path
    ) -> None:
        """Preserve earlier successes and leave a concurrently edited file untouched."""
        root = tmp_path / "root"
        root.mkdir()
        (root / "second").write_bytes(b"edited")
        store = ManifestStore(tmp_path / "state")
        changes = (
            Change(
                Action.CREATE, "first", None, content=b"created", owned_digest="digest"
            ),
            Change(
                Action.UPDATE,
                "second",
                Digest.of(b"old"),
                content=b"replacement",
                owned_digest="new",
            ),
        )

        results = Applier(store).apply(self.plan(root, changes))

        assert results[0].applied == 1, (
            f"Expected stopped prefix of one, got {results!r}"
        )
        assert results[0].error is not None, (
            f"Expected stopped prefix of one, got {results!r}"
        )
        assert "changed since" in results[0].error, (
            f"Expected stopped prefix of one, got {results!r}"
        )
        assert ((root / "second").read_bytes(), store.load("target", root).files) == (
            b"edited",
            {"first": "digest"},
        ), f"Expected recorded prefix, got {store.load('target', root)!r}"

    @pytest.mark.parametrize("action", [Action.TRACK, Action.FORGET])
    def test_metadata_actions_do_not_write_files(
        self, tmp_path: Path, action: Action
    ) -> None:
        """Tracking and releasing ownership leave the current file bytes intact."""
        store = ManifestStore(tmp_path / "state")
        (tmp_path / "file").write_bytes(b"user")
        store.save(Manifest("target", str(tmp_path), {"file": "old"}))
        digest = "new" if action is Action.TRACK else None
        change = Change(action, "file", Digest.of(b"user"), owned_digest=digest)

        results = Applier(store).apply(self.plan(tmp_path, (change,)))

        expected = {"file": "new"} if action is Action.TRACK else {}
        assert (
            results[0].applied,
            (tmp_path / "file").read_bytes(),
            store.load("target", tmp_path).files,
        ) == (1, b"user", expected), f"Expected metadata-only {action}, got {results!r}"

    def test_delete_prunes_empty_parents_but_preserves_root(
        self, tmp_path: Path
    ) -> None:
        """Delete owned files and empty parents while retaining the root."""
        root = tmp_path / "root"
        file = root / "nested/sub/file"
        file.parent.mkdir(parents=True)
        file.write_bytes(b"owned")
        store = ManifestStore(tmp_path / "state")
        store.save(
            Manifest("target", str(root), {"nested/sub/file": Digest.of(b"owned")})
        )

        result = Applier(store).apply(
            self.plan(
                root, (Change(Action.DELETE, "nested/sub/file", Digest.of(b"owned")),)
            )
        )

        assert result[0].error is None, f"Expected empty preserved root, got {result!r}"
        assert root.is_dir(), f"Expected empty preserved root, got {result!r}"
        assert list(root.iterdir()) == [], (
            f"Expected empty preserved root, got {result!r}"
        )

    def test_delete_preserves_nonempty_parents(self, tmp_path: Path) -> None:
        """Pruning stops at a directory containing unmanaged user content."""
        directory = tmp_path / "nested"
        directory.mkdir()
        (directory / "owned").write_bytes(b"owned")
        (directory / "user").write_bytes(b"user")

        result = Applier(ManifestStore(tmp_path / "state")).apply(
            self.plan(
                tmp_path, (Change(Action.DELETE, "nested/owned", Digest.of(b"owned")),)
            )
        )

        assert result[0].error is None, (
            f"Expected user sibling preserved, got {result!r}"
        )
        assert (directory / "user").read_bytes() == b"user", (
            f"Expected user sibling preserved, got {result!r}"
        )

    @pytest.mark.parametrize("digest", [None, "block-digest"])
    def test_block_ownership_is_recorded_or_released(
        self, tmp_path: Path, digest: str | None
    ) -> None:
        """Block changes update only the block ownership map."""
        store = ManifestStore(tmp_path / "state")
        store.save(
            Manifest(
                "target",
                str(tmp_path),
                blocks={"AGENTS.md": OwnedBlock(digest="old", created_file=False)},
            )
        )
        change = Change(
            Action.CREATE,
            "AGENTS.md",
            None,
            content=b"block",
            owned_digest=digest,
            block=True,
            created_file=True,
        )

        Applier(store).apply(self.plan(tmp_path, (change,)))

        expected = (
            {"AGENTS.md": OwnedBlock(digest=digest, created_file=True)}
            if digest is not None
            else {}
        )
        actual = store.load("target", tmp_path).blocks
        assert actual == expected, (
            f"Expected block ownership {expected!r}, got {actual!r}"
        )

    def test_blocked_destination_is_not_applied(self, tmp_path: Path) -> None:
        """Destination errors prevent every planned write to that destination."""
        change = Change(Action.CREATE, "file", None, content=b"data")

        result = Applier(ManifestStore(tmp_path / "state")).apply(
            self.plan(tmp_path, (change,), errors=("blocked",))
        )

        assert result == (), f"Expected no application, got {result!r}"
        assert not (tmp_path / "file").exists(), (
            f"Expected no application, got {result!r}"
        )

    @pytest.mark.parametrize(
        ("content", "remaining"),
        [(b'{"servers": {}}\n', True), (None, False)],
    )
    def test_entry_delete_rewrites_or_removes_the_file(
        self, tmp_path: Path, content: bytes | None, *, remaining: bool
    ) -> None:
        """Removing an entry writes the remaining JSON, or deletes an emptied file."""
        store = ManifestStore(tmp_path / "state")
        root = tmp_path / "destination"
        root.mkdir()
        before = b'{"servers": {"ours": {}}}'
        (root / "mcp.json").write_bytes(before)
        store.save(
            Manifest(
                "target",
                str(root),
                entries={
                    "mcp.json": OwnedEntries(
                        created_file=False, keys={"servers/ours": "d"}
                    )
                },
            )
        )
        change = Change(
            Action.DELETE,
            "mcp.json",
            Digest.of(before),
            content=content,
            entry="servers/ours",
        )

        Applier(store).apply(self.plan(root, (change,)))

        assert (root / "mcp.json").exists() is remaining, (
            f"Expected file kept {remaining}, got {list(root.iterdir())!r}"
        )
        assert store.load("target", root).entries == {}, (
            f"Expected entry released, got {store.load('target', root)!r}"
        )

    def test_entry_ownership_is_recorded_per_key(self, tmp_path: Path) -> None:
        """Entry changes add keys to the file's entry record."""
        store = ManifestStore(tmp_path / "state")
        change = Change(
            Action.CREATE,
            "mcp.json",
            None,
            content=b"{}\n",
            owned_digest="d",
            entry="servers/ours",
            created_file=True,
        )

        Applier(store).apply(self.plan(tmp_path, (change,)))

        actual = store.load("target", tmp_path).entries
        assert actual == {
            "mcp.json": OwnedEntries(created_file=True, keys={"servers/ours": "d"})
        }, f"Expected recorded entry, got {actual!r}"
