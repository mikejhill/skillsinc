"""Test strict JSON parsing and entry edits inside shared configuration files."""

from __future__ import annotations

import json

import pytest

from skillsinc.entries import EntryFile, JsonDocument
from skillsinc.exceptions import ConflictError


class TestJsonDocument:
    """Shared JSON files are parsed strictly and digested independently of layout."""

    @pytest.mark.parametrize(
        "raw",
        [b"[]", b'{"a": 1, "a": 2}', b'{"a": 1 // comment\n}', b"\xff"],
    )
    def test_ambiguous_or_unsupported_json_is_refused(self, raw: bytes) -> None:
        """Reject arrays, duplicate keys, comments, and non-UTF-8 bytes."""
        with pytest.raises(ValueError):  # noqa: PT011 — each case raises its own message
            JsonDocument.load(raw)

    def test_digest_ignores_key_order(self) -> None:
        """Equal values reordered by another tool keep the same digest."""
        first = JsonDocument.digest({"command": "x", "args": ["a"]})
        second = JsonDocument.digest({"args": ["a"], "command": "x"})

        assert first == second, f"Expected order-free digest, got {first} != {second}"


class TestEntryFile:
    """Entry edits change only their own key and keep everything else."""

    def test_set_preserves_unrelated_keys_and_order(self) -> None:
        """Adding an entry keeps the user's other servers and top-level settings."""
        raw = b'{"theme": "dark", "mcpServers": {"mine": {"command": "a"}}}'
        document = EntryFile(raw)

        result = json.loads(document.set("mcpServers", "ours", {"command": "b"}))

        assert result == {
            "theme": "dark",
            "mcpServers": {"mine": {"command": "a"}, "ours": {"command": "b"}},
        }, f"Expected one added entry, got {result!r}"
        assert list(result) == ["theme", "mcpServers"], (
            f"Expected key order preserved, got {list(result)!r}"
        )

    @pytest.mark.parametrize(("prune", "expected"), [(True, None), (False, b"{")])
    def test_remove_last_entry_prunes_only_when_requested(
        self, *, prune: bool, expected: bytes | None
    ) -> None:
        """An emptied file SkillSinc created is reported for deletion."""
        document = EntryFile(b'{"mcpServers": {"ours": {"command": "b"}}}')

        result = document.remove("mcpServers", "ours", prune=prune)

        actual = result[:1] if result is not None else None
        assert actual == expected, f"Expected {expected!r}, got {result!r}"

    def test_digest_of_missing_entry_is_none(self) -> None:
        """A missing container or key reads as absent."""
        result = EntryFile(None).digest("mcpServers", "ours")

        assert result is None, f"Expected absent entry, got {result!r}"

    @pytest.mark.parametrize("raw", [b"not json", b'{"mcpServers": []}'])
    def test_unusable_file_is_a_conflict(self, raw: bytes) -> None:
        """Invalid files and non-object containers block instead of being rewritten."""
        with pytest.raises(ConflictError):
            EntryFile(raw).digest("mcpServers", "ours")
