"""Test managed block preservation, newline handling, and marker validation."""

from __future__ import annotations

import pytest

from skillsinc.blocks import BEGIN, END, ManagedBlock
from skillsinc.exceptions import ConflictError


class TestManagedBlock:
    """One managed region preserves all surrounding user content."""

    @pytest.mark.parametrize(
        "prefix", ["User", "User\n", "User\n\n", "User\r\n", "", "   "]
    )
    def test_appends_extractable_block(self, prefix: str) -> None:
        """Append a normalized block using the existing newline convention."""
        codec = ManagedBlock()
        block = codec.build((("library:instructions/example", " Body \n"),))

        result = codec.splice(prefix, block)

        assert codec.extract(result) == block, (
            f"Expected appended block {block!r}, got {result!r}"
        )
        assert "\n\nBody\n\n" in block, f"Expected trimmed section text, got {block!r}"

    @pytest.mark.parametrize("host", ["User\n", "User\r\n", ""])
    def test_crlf_section_round_trips(self, host: str) -> None:
        """Keep a CRLF section stable so the next plan finds no change."""
        codec = ManagedBlock()
        block = codec.build((("library:instructions/example", "# T\r\n\r\nBody\r\n"),))

        result = codec.splice(host, block)

        assert "\r\r" not in result, f"Expected no doubled CR, got {result!r}"
        assert codec.extract(result) == block, (
            f"Expected extract to return the built block {block!r}, got {result!r}"
        )
        assert codec.splice(result, block) == result, (
            f"Expected a second splice to be a no-op, got {result!r}"
        )

    def test_replaces_block_in_place(self) -> None:
        """Keep prefix and suffix byte-for-byte when updating the managed region."""
        codec = ManagedBlock()
        old = codec.build((("old", "Old"),))
        new = codec.build((("new", "New"),))
        original = f"Before\n\n{old}\n\nAfter\n"

        result = codec.splice(original, new)

        assert result == f"Before\n\n{new}\n\nAfter\n", (
            f"Expected in-place replacement, got {result!r}"
        )

    @pytest.mark.parametrize(
        ("before", "after", "expected"),
        [
            ("Before\n\n", "\n\nAfter\n", "Before\n\nAfter\n"),
            ("Before\n", "", "Before\n"),
            ("", "\nAfter", "After\n"),
            ("", "", ""),
        ],
    )
    def test_removes_only_managed_block(
        self, before: str, after: str, expected: str
    ) -> None:
        """Remove the region and normalize only neighboring blank lines."""
        original = before + ManagedBlock.build(()) + after

        result = ManagedBlock().splice(original, None)

        assert result == expected, (
            f"Expected remaining content {expected!r}, got {result!r}"
        )

    def test_removal_without_markers_is_noop(self) -> None:
        """Unmanaged files remain untouched when no block is requested."""
        result = ManagedBlock().splice("User\r\n", None)

        assert result == "User\r\n", f"Expected unchanged user content, got {result!r}"

    @pytest.mark.parametrize(
        "content", [BEGIN, END, END + BEGIN, BEGIN + BEGIN + END, BEGIN + END + END]
    )
    def test_damaged_markers_raise_conflict(self, content: str) -> None:
        """Reject missing, reordered, and duplicated markers."""
        with pytest.raises(ConflictError, match="markers are damaged"):
            ManagedBlock().splice(content, "Replacement")
