"""Test ownership decisions, drift repair, managed blocks, and unsafe paths."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import ClassVar

import pytest

from skillsinc.blocks import ManagedBlock
from skillsinc.diff import DestinationDiff
from skillsinc.entries import JsonDocument
from skillsinc.manifest import Manifest, OwnedBlock, OwnedEntries
from skillsinc.models import Digest, EntryOutput, FileOutput, JsonValue
from skillsinc.plan import Action, Change


class TestDestinationDiff:
    """Desired content is reconciled without claiming unmanaged user files."""

    def test_absolute_path_is_refused(self, tmp_path: Path) -> None:
        """Reject absolute output paths even when they point inside the root."""
        path = str(tmp_path / "file")

        changes, errors = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=False
        ).compare({path: FileOutput(path, b"data")}, {})

        assert changes == (), f"Expected absolute path refused, got {changes!r}"
        assert len(errors) == 1, f"Expected one path conflict, got {errors!r}"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows rooted-path semantics")
    def test_windows_rooted_path_is_refused(self, tmp_path: Path) -> None:
        """Refuse Windows rooted paths that discard the configured root directory."""
        path = "/escape"

        changes, errors = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=False
        ).compare({path: FileOutput(path, b"data")}, {})

        assert changes == (), f"Expected rooted path refused, got {changes!r}"
        assert len(errors) == 1, f"Expected one rooted path error, got {errors!r}"

    @pytest.mark.parametrize(
        ("current", "owned", "adopt", "action", "note"),
        [
            (None, None, False, Action.CREATE, ""),
            (None, b"old", False, Action.CREATE, "restores a deleted managed file"),
            (b"old", b"old", False, Action.UPDATE, ""),
            (b"edited", b"old", False, Action.UPDATE, "overwrites local edits"),
            (b"new", b"old", False, Action.TRACK, ""),
            (b"new", None, True, Action.TRACK, ""),
            (b"old", None, True, Action.ADOPT, ""),
        ],
    )
    def test_desired_file_transition(  # noqa: PLR0913 - transition matrix
        self,
        tmp_path: Path,
        current: bytes | None,
        owned: bytes | None,
        action: Action,
        note: str,
        *,
        adopt: bool,
    ) -> None:
        """Choose the exact action and precondition for each ownership transition."""
        path = tmp_path / "file.md"
        if current is not None:
            path.write_bytes(current)
        manifest = Manifest(
            "target",
            str(tmp_path),
            {"file.md": Digest.of(owned)} if owned is not None else {},
        )
        output = FileOutput("file.md", b"new", executable=True)

        changes, errors = DestinationDiff(tmp_path, manifest, adopt=adopt).compare(
            {"file.md": output}, {}
        )

        expected = Digest.of(current) if current is not None else None
        assert errors == (), f"Expected no conflicts, got {errors!r}"
        assert (
            changes[0].action,
            changes[0].expected,
            changes[0].owned_digest,
            changes[0].note,
            changes[0].executable,
        ) == (action, expected, Digest.of(b"new"), note, True), (
            f"Expected {action} with {note!r}, got {changes!r}"
        )

    def test_matching_owned_file_is_unchanged(self, tmp_path: Path) -> None:
        """A managed file whose bytes and ownership match needs no change."""
        (tmp_path / "file").write_bytes(b"same")
        manifest = Manifest("target", str(tmp_path), {"file": Digest.of(b"same")})

        result = DestinationDiff(tmp_path, manifest, adopt=False).compare(
            {"file": FileOutput("file", b"same")}, {}
        )

        assert result == ((), ()), f"Expected no changes or errors, got {result!r}"

    @pytest.mark.parametrize("current", [b"same", b"different"])
    def test_unmanaged_file_blocks_even_identical_content(
        self, tmp_path: Path, current: bytes
    ) -> None:
        """Matching bytes never grant ownership without explicit adoption."""
        (tmp_path / "file").write_bytes(current)

        changes, errors = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=False
        ).compare({"file": FileOutput("file", b"same")}, {})

        assert changes == (), f"Expected unmanaged conflict, got {(changes, errors)!r}"
        assert len(errors) == 1, (
            f"Expected unmanaged conflict, got {(changes, errors)!r}"
        )
        assert "not managed" in errors[0], (
            f"Expected unmanaged conflict, got {(changes, errors)!r}"
        )

    @pytest.mark.parametrize(
        ("current", "action", "note"),
        [
            (None, Action.FORGET, "already gone"),
            (b"owned", Action.DELETE, ""),
            (b"edited", Action.FORGET, "edited since"),
        ],
    )
    def test_removed_file_is_deleted_or_released(
        self, tmp_path: Path, current: bytes | None, action: Action, note: str
    ) -> None:
        """Delete unchanged owned files and release edited or absent ones."""
        if current is not None:
            (tmp_path / "file").write_bytes(current)
        manifest = Manifest("target", str(tmp_path), {"file": Digest.of(b"owned")})

        changes, errors = DestinationDiff(tmp_path, manifest, adopt=False).compare(
            {}, {}
        )

        assert errors == (), (
            f"Expected {action} with note {note!r}, got {(changes, errors)!r}"
        )
        assert changes[0].action is action, (
            f"Expected {action} with note {note!r}, got {(changes, errors)!r}"
        )
        assert changes[0].note.startswith(note), (
            f"Expected {action} with note {note!r}, got {(changes, errors)!r}"
        )

    @pytest.mark.parametrize("path", ["../escape", "directory"])
    def test_unsafe_or_directory_paths_block(self, tmp_path: Path, path: str) -> None:
        """Reject escaping paths and directories at expected file locations."""
        (tmp_path / "directory").mkdir()

        changes, errors = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=False
        ).compare({path: FileOutput(path, b"data")}, {})

        assert changes == (), (
            f"Expected one blocked unsafe path, got {(changes, errors)!r}"
        )
        assert len(errors) == 1, (
            f"Expected one blocked unsafe path, got {(changes, errors)!r}"
        )

    @pytest.mark.parametrize("adopt", [False, True])
    def test_unowned_existing_block_requires_adoption(
        self, tmp_path: Path, *, adopt: bool
    ) -> None:
        """An existing managed region needs explicit ownership adoption."""
        block = ManagedBlock.build((("section", "Body"),))
        (tmp_path / "AGENTS.md").write_text(block + "\n", encoding="utf-8")

        changes, errors = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=adopt
        ).compare({}, {"AGENTS.md": (("section", "Body"),)})

        if adopt:
            assert errors == (), (
                f"Expected block ownership tracking, got {(changes, errors)!r}"
            )
            assert changes[0].action is Action.TRACK, (
                f"Expected block ownership tracking, got {(changes, errors)!r}"
            )
        else:
            assert changes == (), (
                f"Expected unmanaged block conflict, got {(changes, errors)!r}"
            )
            assert "not managed" in errors[0], (
                f"Expected unmanaged block conflict, got {(changes, errors)!r}"
            )

    def test_owned_matching_block_is_unchanged(self, tmp_path: Path) -> None:
        """A block matching its ownership digest is already current."""
        block = ManagedBlock.build((("section", "Body"),))
        (tmp_path / "AGENTS.md").write_bytes((block + "\n").encode())
        manifest = Manifest(
            "target",
            str(tmp_path),
            blocks={
                "AGENTS.md": OwnedBlock(
                    digest=Digest.of(block.encode()), created_file=True
                )
            },
        )

        result = DestinationDiff(tmp_path, manifest, adopt=False).compare(
            {}, {"AGENTS.md": (("section", "Body"),)}
        )

        assert result == ((), ()), f"Expected unchanged block, got {result!r}"

    def test_edited_block_is_repaired_with_note(self, tmp_path: Path) -> None:
        """Repair local edits inside the block while preserving outside content."""
        block = ManagedBlock.build((("section", "Original"),))
        (tmp_path / "AGENTS.md").write_bytes(
            ("User\n" + block.replace("Original", "Edited") + "\n").encode()
        )
        manifest = Manifest(
            "target",
            str(tmp_path),
            blocks={
                "AGENTS.md": OwnedBlock(
                    digest=Digest.of(block.encode()), created_file=False
                )
            },
        )

        changes, errors = DestinationDiff(tmp_path, manifest, adopt=False).compare(
            {}, {"AGENTS.md": (("section", "Original"),)}
        )

        assert errors == (), f"Expected block drift warning, got {(changes, errors)!r}"
        assert changes[0].note == "overwrites local edits to the block", (
            f"Expected block drift warning, got {(changes, errors)!r}"
        )
        assert changes[0].content == ("User\n" + block + "\n").encode(), (
            f"Expected repaired block with user prefix, got {changes[0].content!r}"
        )

    @pytest.mark.parametrize(
        ("content", "created", "action"),
        [
            (None, True, Action.FORGET),
            ("User\n", False, Action.FORGET),
            ("block", True, Action.DELETE),
            ("block", False, Action.UPDATE),
            ("User\nblock", True, Action.UPDATE),
        ],
    )
    def test_removed_block_retains_user_owned_file(
        self, tmp_path: Path, content: str | None, action: Action, *, created: bool
    ) -> None:
        """Only delete an empty shared file when SkillSinc originally created it."""
        block = ManagedBlock.build((("section", "Body"),))
        if content is not None:
            (tmp_path / "AGENTS.md").write_text(
                content.replace("block", block), encoding="utf-8"
            )
        manifest = Manifest(
            "target",
            str(tmp_path),
            blocks={"AGENTS.md": OwnedBlock(Digest.of(block.encode()), created)},
        )

        changes, errors = DestinationDiff(tmp_path, manifest, adopt=False).compare(
            {}, {}
        )

        assert errors == (), (
            f"Expected {action} for removed block, got {(changes, errors)!r}"
        )
        assert changes[0].action is action, (
            f"Expected {action} for removed block, got {(changes, errors)!r}"
        )
        assert changes[0].block, (
            f"Expected {action} for removed block, got {(changes, errors)!r}"
        )

    def test_non_utf8_shared_file_blocks(self, tmp_path: Path) -> None:
        """Fail safely when a shared Markdown file is not valid UTF-8."""
        (tmp_path / "AGENTS.md").write_bytes(b"\xff")

        changes, errors = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=False
        ).compare({}, {"AGENTS.md": (("section", "Body"),)})

        assert changes == (), f"Expected decoding conflict, got {(changes, errors)!r}"
        assert "cannot read" in errors[0], (
            f"Expected decoding conflict, got {(changes, errors)!r}"
        )


class TestEntryDiff:
    """JSON entries follow the ownership table one key at a time."""

    SERVER: ClassVar[dict[str, JsonValue]] = {"command": "server"}

    @staticmethod
    def compare(
        root: Path,
        current: dict[str, JsonValue] | None,
        owned: dict[str, str] | None,
        outputs: tuple[EntryOutput, ...],
        *,
        adopt: bool = False,
    ) -> tuple[tuple[Change, ...], tuple[str, ...]]:
        """Write ``current`` as ``mcp.json`` and diff ``outputs`` against it."""
        if current is not None:
            (root / "mcp.json").write_text(json.dumps(current), encoding="utf-8")
        manifest = Manifest("target", str(root))
        if owned is not None:
            manifest.entries["mcp.json"] = OwnedEntries(created_file=False, keys=owned)
        return DestinationDiff(root, manifest, adopt=adopt).compare(
            {}, {}, {"mcp.json": outputs} if outputs else {}
        )

    def output(self, value: dict[str, JsonValue] | None = None) -> EntryOutput:
        """Return the desired ``servers/ours`` entry."""
        return EntryOutput("mcp.json", "servers", "ours", value or self.SERVER)

    @pytest.mark.parametrize(
        ("current", "owned", "adopt", "action", "note"),
        [
            (None, None, False, Action.CREATE, ""),
            (
                {"other": 1},
                "old",
                False,
                Action.CREATE,
                "restores a deleted managed entry",
            ),
            ({"servers": {"ours": "old"}}, "old", False, Action.UPDATE, ""),
            (
                {"servers": {"ours": "edited"}},
                "old",
                False,
                Action.UPDATE,
                "overwrites local edits",
            ),
            (
                {"servers": {"ours": {"command": "server"}}},
                "old",
                False,
                Action.TRACK,
                "",
            ),
            (
                {"servers": {"ours": {"command": "server"}}},
                None,
                True,
                Action.TRACK,
                "",
            ),
            ({"servers": {"ours": "old"}}, None, True, Action.ADOPT, ""),
        ],
    )
    def test_desired_entry_transition(  # noqa: PLR0913 - transition matrix
        self,
        tmp_path: Path,
        current: dict[str, JsonValue] | None,
        owned: str | None,
        action: Action,
        note: str,
        *,
        adopt: bool,
    ) -> None:
        """Choose the action, note, and precondition for each entry transition."""
        recorded = (
            {"servers/ours": JsonDocument.digest(owned)} if owned is not None else None
        )

        changes, errors = self.compare(
            tmp_path, current, recorded, (self.output(),), adopt=adopt
        )

        raw = (tmp_path / "mcp.json").read_bytes() if current is not None else None
        expected = Digest.of(raw) if raw is not None else None
        assert errors == (), f"Expected no errors, got {errors!r}"
        assert [
            (c.action, c.entry, c.expected, c.note, c.owned_digest) for c in changes
        ] == [
            (action, "servers/ours", expected, note, JsonDocument.digest(self.SERVER))
        ], f"Expected one {action} change, got {changes!r}"

    def test_written_file_keeps_unrelated_settings(self, tmp_path: Path) -> None:
        """The planned bytes add the entry and keep every other key."""
        changes, _ = self.compare(
            tmp_path, {"theme": "dark", "servers": {"mine": 1}}, None, (self.output(),)
        )

        written = json.loads(changes[0].content or b"")
        assert written == {
            "theme": "dark",
            "servers": {"mine": 1, "ours": self.SERVER},
        }, f"Expected user keys preserved, got {written!r}"

    def test_unmanaged_entry_blocks_without_adopt(self, tmp_path: Path) -> None:
        """An existing entry SkillSinc did not write blocks the destination."""
        changes, errors = self.compare(
            tmp_path, {"servers": {"ours": self.SERVER}}, None, (self.output(),)
        )

        assert changes == (), f"Expected no changes, got {changes!r}"
        assert errors == (
            "mcp.json: entry servers/ours exists but is not managed by SkillSinc;"
            " review it, then rerun with --adopt (identical content)",
        ), f"Expected one unmanaged-entry error, got {errors!r}"

    def test_up_to_date_entry_has_no_change(self, tmp_path: Path) -> None:
        """An owned entry matching the library needs nothing."""
        owned = {"servers/ours": JsonDocument.digest(self.SERVER)}

        changes, errors = self.compare(
            tmp_path, {"servers": {"ours": self.SERVER}}, owned, (self.output(),)
        )

        assert (changes, errors) == ((), ()), f"Expected no work, got {changes!r}"

    @pytest.mark.parametrize(
        ("current", "action", "note"),
        [
            ({"servers": {"ours": {"command": "server"}}}, Action.DELETE, ""),
            (
                {"servers": {"ours": "edited"}},
                Action.FORGET,
                "edited since SkillSinc wrote it, so it is left in place and released",
            ),
            ({"servers": {}}, Action.FORGET, "already gone"),
            (None, Action.FORGET, "already gone"),
        ],
    )
    def test_removed_entry_transition(
        self,
        tmp_path: Path,
        current: dict[str, JsonValue] | None,
        action: Action,
        note: str,
    ) -> None:
        """Delete unchanged owned entries and release edited or missing ones."""
        owned = {"servers/ours": JsonDocument.digest(self.SERVER)}

        changes, errors = self.compare(tmp_path, current, owned, ())

        assert errors == (), f"Expected no errors, got {errors!r}"
        assert [(c.action, c.note, c.owned_digest) for c in changes] == [
            (action, note, None)
        ], f"Expected {action}, got {changes!r}"

    def test_removing_last_entry_of_created_file_deletes_it(
        self, tmp_path: Path
    ) -> None:
        """A file SkillSinc created is deleted once its last entry goes."""
        (tmp_path / "mcp.json").write_text(
            json.dumps({"servers": {"ours": self.SERVER}}), encoding="utf-8"
        )
        manifest = Manifest("target", str(tmp_path))
        manifest.entries["mcp.json"] = OwnedEntries(
            created_file=True, keys={"servers/ours": JsonDocument.digest(self.SERVER)}
        )

        changes, _ = DestinationDiff(tmp_path, manifest, adopt=False).compare({}, {})

        assert [(c.action, c.content) for c in changes] == [(Action.DELETE, None)], (
            f"Expected whole-file delete, got {changes!r}"
        )

    def test_sequential_changes_chain_preconditions(self, tmp_path: Path) -> None:
        """Each change expects the bytes the previous change wrote."""
        outputs = (
            EntryOutput("mcp.json", "servers", "a", self.SERVER),
            EntryOutput("mcp.json", "servers", "b", self.SERVER),
        )

        changes, _ = self.compare(tmp_path, None, None, outputs)

        first, second = changes
        assert first.expected is None, f"Expected missing file first, got {first!r}"
        assert second.expected == Digest.of(first.content or b""), (
            f"Expected chained precondition, got {second!r}"
        )

    def test_invalid_json_blocks_destination(self, tmp_path: Path) -> None:
        """A file that is not plain JSON is never rewritten."""
        (tmp_path / "mcp.json").write_text("{ // comment\n}", encoding="utf-8")

        changes, errors = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=False
        ).compare({}, {}, {"mcp.json": (self.output(),)})

        assert changes == (), f"Expected no changes, got {changes!r}"
        assert "is not a plain JSON object" in errors[0], (
            f"Expected JSON refusal, got {errors!r}"
        )
