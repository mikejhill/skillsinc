"""Test ownership decisions, drift repair, managed blocks, and unsafe paths."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from skillsinc.blocks import ManagedBlock
from skillsinc.diff import DestinationDiff
from skillsinc.manifest import Manifest, OwnedBlock
from skillsinc.models import Digest, FileOutput
from skillsinc.plan import Action


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
            path.chmod(0o755)
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


class TestDestinationDiffEdgeCases:
    """Added, removed, and changed resources across files and blocks."""

    @staticmethod
    def owned_block(
        sections: tuple[tuple[str, str], ...], *, created: bool
    ) -> OwnedBlock:
        """Return the ownership record for a block built from ``sections``."""
        return OwnedBlock(Digest.of(ManagedBlock.build(sections).encode()), created)

    def test_added_changed_and_removed_files_are_planned_together(
        self, tmp_path: Path
    ) -> None:
        """Plan every kind of file change in one pass, in sorted path order."""
        (tmp_path / "changed").write_bytes(b"old")
        (tmp_path / "removed").write_bytes(b"gone")
        (tmp_path / "kept").write_bytes(b"kept")
        manifest = Manifest(
            "target",
            str(tmp_path),
            {
                "changed": Digest.of(b"old"),
                "removed": Digest.of(b"gone"),
                "kept": Digest.of(b"kept"),
            },
        )
        files = {
            "added": FileOutput("added", b"new"),
            "changed": FileOutput("changed", b"new"),
            "kept": FileOutput("kept", b"kept"),
        }

        changes, errors = DestinationDiff(tmp_path, manifest, adopt=False).compare(
            files, {}
        )

        actual = [(change.action, change.path) for change in changes]
        expected = [
            (Action.CREATE, "added"),
            (Action.UPDATE, "changed"),
            (Action.DELETE, "removed"),
        ]
        assert errors == (), f"Expected no conflicts, got {errors!r}"
        assert actual == expected, f"Expected {expected!r}, got {actual!r}"

    def test_every_conflict_is_reported(self, tmp_path: Path) -> None:
        """One conflict does not hide later ones or stop other paths being checked."""
        (tmp_path / "a").write_bytes(b"user")
        (tmp_path / "b").write_bytes(b"user")

        changes, errors = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=False
        ).compare(
            {
                "a": FileOutput("a", b"x"),
                "b": FileOutput("b", b"x"),
                "c": FileOutput("c", b"x"),
            },
            {},
        )

        assert [error.split(":")[0] for error in errors] == ["a", "b"], (
            f"Expected conflicts for a and b, got {errors!r}"
        )
        assert [change.path for change in changes] == ["c"], (
            f"Expected c still planned, got {changes!r}"
        )

    def test_nested_new_file_is_created(self, tmp_path: Path) -> None:
        """A desired file under missing directories is a plain create."""
        path = "skills/sample/SKILL.md"

        changes, errors = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=False
        ).compare({path: FileOutput(path, b"body")}, {})

        assert errors == (), f"Expected no conflicts, got {errors!r}"
        assert (changes[0].action, changes[0].expected) == (Action.CREATE, None), (
            f"Expected create with no precondition, got {changes!r}"
        )

    def test_unmanaged_identical_file_is_labelled(self, tmp_path: Path) -> None:
        """The conflict for an identical unmanaged file says the content matches."""
        (tmp_path / "file").write_bytes(b"same")

        _, errors = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=False
        ).compare({"file": FileOutput("file", b"same")}, {})

        assert errors[0].endswith("(identical content)"), (
            f"Expected identical-content hint, got {errors!r}"
        )

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
    @pytest.mark.parametrize(("mode", "executable"), [(0o644, True), (0o755, False)])
    def test_executable_bit_change_is_updated(
        self, tmp_path: Path, mode: int, *, executable: bool
    ) -> None:
        """A changed executable flag is synced even when the bytes are identical."""
        target = tmp_path / "run.sh"
        target.write_bytes(b"script")
        target.chmod(mode)
        manifest = Manifest("target", str(tmp_path), {"run.sh": Digest.of(b"script")})

        changes, errors = DestinationDiff(tmp_path, manifest, adopt=False).compare(
            {"run.sh": FileOutput("run.sh", b"script", executable=executable)}, {}
        )

        assert errors == (), f"Expected no conflicts, got {errors!r}"
        assert [(c.action, c.executable) for c in changes] == [
            (Action.UPDATE, executable)
        ], f"Expected executable update, got {changes!r}"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
    def test_adopting_file_with_other_mode_rewrites_it(self, tmp_path: Path) -> None:
        """Adoption only tracks a file whose bytes and executable flag both match."""
        target = tmp_path / "run.sh"
        target.write_bytes(b"script")
        target.chmod(0o644)

        changes, _ = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=True
        ).compare({"run.sh": FileOutput("run.sh", b"script", executable=True)}, {})

        assert changes[0].action is Action.ADOPT, (
            f"Expected adopt to set the mode, got {changes!r}"
        )

    def test_mode_is_ignored_on_windows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Windows has no executable bit, so only the bytes decide."""
        (tmp_path / "run.sh").write_bytes(b"script")
        manifest = Manifest("target", str(tmp_path), {"run.sh": Digest.of(b"script")})
        monkeypatch.setattr(sys, "platform", "win32")

        result = DestinationDiff(tmp_path, manifest, adopt=False).compare(
            {"run.sh": FileOutput("run.sh", b"script", executable=True)}, {}
        )

        assert result == ((), ()), f"Expected no change on Windows, got {result!r}"

    def test_symlinked_parent_is_refused(self, tmp_path: Path) -> None:
        """Never plan a write through a link inside the destination."""
        outside = tmp_path / "outside"
        outside.mkdir()
        root = tmp_path / "root"
        root.mkdir()
        try:
            (root / "skills").symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks are not available")

        changes, errors = DestinationDiff(
            root, Manifest("target", str(root)), adopt=False
        ).compare({"skills/a": FileOutput("skills/a", b"x")}, {})

        assert changes == (), f"Expected no changes, got {changes!r}"
        assert "link" in errors[0], f"Expected link refusal, got {errors!r}"

    def test_path_wanted_as_file_and_block_blocks(self, tmp_path: Path) -> None:
        """A path cannot be both a whole managed file and a managed block."""
        changes, errors = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=False
        ).compare(
            {"AGENTS.md": FileOutput("AGENTS.md", b"whole")},
            {"AGENTS.md": (("section", "Body"),)},
        )

        assert len(errors) == 1, f"Expected one overlap error, got {errors!r}"
        assert "both" in errors[0], f"Expected overlap error, got {errors!r}"
        assert changes == (), f"Expected nothing planned, got {changes!r}"

    def test_new_block_creates_file(self, tmp_path: Path) -> None:
        """A block for a missing file creates it and records the creation."""
        sections = (("section", "Body"),)

        changes, errors = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=False
        ).compare({}, {"AGENTS.md": sections})

        block = ManagedBlock.build(sections)
        assert errors == (), f"Expected no conflicts, got {errors!r}"
        assert (
            changes[0].action,
            changes[0].content,
            changes[0].owned_digest,
            changes[0].created_file,
        ) == (
            Action.CREATE,
            (block + "\n").encode(),
            Digest.of(block.encode()),
            True,
        ), f"Expected block file creation, got {changes!r}"

    def test_new_block_appends_to_user_file(self, tmp_path: Path) -> None:
        """A block for a user's file without markers appends and keeps their text."""
        (tmp_path / "AGENTS.md").write_text("User\n", encoding="utf-8", newline="\n")
        sections = (("section", "Body"),)

        changes, _ = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=False
        ).compare({}, {"AGENTS.md": sections})

        block = ManagedBlock.build(sections)
        assert (
            changes[0].action,
            changes[0].content,
            changes[0].created_file,
        ) == (Action.UPDATE, f"User\n\n{block}\n".encode(), False), (
            f"Expected appended block in user file, got {changes!r}"
        )

    def test_changed_sections_update_block(self, tmp_path: Path) -> None:
        """New section text replaces the block without a drift note."""
        old = (("section", "Old"),)
        new = (("section", "New"), ("another", "Added"))
        (tmp_path / "AGENTS.md").write_text(
            "User\n\n" + ManagedBlock.build(old) + "\n", encoding="utf-8", newline="\n"
        )
        manifest = Manifest(
            "target",
            str(tmp_path),
            blocks={"AGENTS.md": self.owned_block(old, created=False)},
        )

        changes, _ = DestinationDiff(tmp_path, manifest, adopt=False).compare(
            {}, {"AGENTS.md": new}
        )

        block = ManagedBlock.build(sorted(new))
        assert (changes[0].action, changes[0].note, changes[0].content) == (
            Action.UPDATE,
            "",
            f"User\n\n{block}\n".encode(),
        ), f"Expected sorted section update, got {changes!r}"

    def test_crlf_block_is_unchanged(self, tmp_path: Path) -> None:
        """A current block in a CRLF file is not rewritten."""
        sections = (("section", "Body"),)
        block = ManagedBlock.build(sections)
        (tmp_path / "AGENTS.md").write_bytes(
            ("User\r\n\r\n" + block.replace("\n", "\r\n") + "\r\n").encode()
        )
        manifest = Manifest(
            "target",
            str(tmp_path),
            blocks={"AGENTS.md": self.owned_block(sections, created=False)},
        )

        result = DestinationDiff(tmp_path, manifest, adopt=False).compare(
            {}, {"AGENTS.md": sections}
        )

        assert result == ((), ()), f"Expected unchanged CRLF block, got {result!r}"

    def test_stale_block_ownership_is_tracked(self, tmp_path: Path) -> None:
        """A block already on disk but recorded with an old digest is re-tracked."""
        sections = (("section", "Body"),)
        (tmp_path / "AGENTS.md").write_text(
            ManagedBlock.build(sections) + "\n", encoding="utf-8", newline="\n"
        )
        manifest = Manifest(
            "target",
            str(tmp_path),
            blocks={"AGENTS.md": OwnedBlock(Digest.of(b"stale"), created_file=True)},
        )

        changes, _ = DestinationDiff(tmp_path, manifest, adopt=False).compare(
            {}, {"AGENTS.md": sections}
        )

        assert [(c.action, c.content, c.created_file) for c in changes] == [
            (Action.TRACK, None, True)
        ], f"Expected ownership refresh, got {changes!r}"

    def test_damaged_markers_block(self, tmp_path: Path) -> None:
        """Duplicated markers are never guessed at."""
        block = ManagedBlock.build((("section", "Body"),))
        (tmp_path / "AGENTS.md").write_text(
            block + "\n" + block, encoding="utf-8", newline="\n"
        )

        changes, errors = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=True
        ).compare({}, {"AGENTS.md": (("section", "Body"),)})

        assert changes == (), f"Expected no changes, got {changes!r}"
        assert "markers are damaged" in errors[0], (
            f"Expected marker error, got {errors!r}"
        )

    def test_adopting_different_block_is_adopt(self, tmp_path: Path) -> None:
        """Taking over an unowned block with other content is reported as adopt."""
        (tmp_path / "AGENTS.md").write_text(
            ManagedBlock.build((("section", "Theirs"),)) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        changes, _ = DestinationDiff(
            tmp_path, Manifest("target", str(tmp_path)), adopt=True
        ).compare({}, {"AGENTS.md": (("section", "Ours"),)})

        assert (changes[0].action, changes[0].created_file) == (Action.ADOPT, False), (
            f"Expected block adoption, got {changes!r}"
        )

    def test_deleted_block_is_restored_with_note(self, tmp_path: Path) -> None:
        """A block removed by hand from an existing file is restored and noted."""
        sections = (("section", "Body"),)
        (tmp_path / "AGENTS.md").write_text("User\n", encoding="utf-8", newline="\n")
        manifest = Manifest(
            "target",
            str(tmp_path),
            blocks={"AGENTS.md": self.owned_block(sections, created=False)},
        )

        changes, _ = DestinationDiff(tmp_path, manifest, adopt=False).compare(
            {}, {"AGENTS.md": sections}
        )

        assert (changes[0].action, changes[0].note) == (
            Action.UPDATE,
            "restores a deleted managed block",
        ), f"Expected restore note, got {changes!r}"

    def test_recreated_block_file_is_marked_created(self, tmp_path: Path) -> None:
        """Recreating a deleted shared file records that SkillSinc created it."""
        sections = (("section", "Body"),)
        manifest = Manifest(
            "target",
            str(tmp_path),
            blocks={"AGENTS.md": self.owned_block(sections, created=False)},
        )

        changes, _ = DestinationDiff(tmp_path, manifest, adopt=False).compare(
            {}, {"AGENTS.md": sections}
        )

        assert (changes[0].action, changes[0].created_file) == (Action.CREATE, True), (
            f"Expected recreated file owned for later deletion, got {changes!r}"
        )

    @pytest.mark.parametrize("created", [False, True])
    def test_edited_removed_block_is_released(
        self, tmp_path: Path, *, created: bool
    ) -> None:
        """An edited block that is no longer wanted is left in place and released."""
        sections = (("section", "Body"),)
        edited = ManagedBlock.build((("section", "Edited"),))
        (tmp_path / "AGENTS.md").write_text(
            edited + "\n", encoding="utf-8", newline="\n"
        )
        manifest = Manifest(
            "target",
            str(tmp_path),
            blocks={"AGENTS.md": self.owned_block(sections, created=created)},
        )

        changes, _ = DestinationDiff(tmp_path, manifest, adopt=False).compare({}, {})

        assert [(c.action, c.block, c.owned_digest) for c in changes] == [
            (Action.FORGET, True, None)
        ], f"Expected released block, got {changes!r}"
        assert changes[0].note.startswith("edited since"), (
            f"Expected release note, got {changes!r}"
        )

    def test_diffs_are_independent_per_destination(self, tmp_path: Path) -> None:
        """The same desired state yields each destination's own changes."""
        fresh = tmp_path / "fresh"
        synced = tmp_path / "synced"
        for root in (fresh, synced):
            root.mkdir()
        (synced / "file").write_bytes(b"data")
        desired = {"file": FileOutput("file", b"data")}

        fresh_result = DestinationDiff(
            fresh, Manifest("fresh", str(fresh)), adopt=False
        ).compare(desired, {})
        synced_result = DestinationDiff(
            synced,
            Manifest("synced", str(synced), {"file": Digest.of(b"data")}),
            adopt=False,
        ).compare(desired, {})

        assert [c.action for c in fresh_result[0]] == [Action.CREATE], (
            f"Expected create in fresh destination, got {fresh_result!r}"
        )
        assert synced_result == ((), ()), (
            f"Expected no changes in synced destination, got {synced_result!r}"
        )
