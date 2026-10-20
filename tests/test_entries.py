"""Test strict JSON parsing and entry edits inside shared configuration files."""

from __future__ import annotations

import json
import tomllib

import pytest
import tomlkit

from skillsinc.entries import EntryFile, JsonDocument, JsonEntryFile, TomlEntryFile
from skillsinc.exceptions import ConflictError
from skillsinc.models import JsonValue


class TestJsonDocument:
    """Shared JSON files are parsed strictly and digested independently of layout."""

    @pytest.mark.parametrize(
        "raw",
        [
            b"[]",
            b'{"a": 1, "a": 2}',
            b'{"a": 1 // comment\n}',
            b"\xff",
            b'{"a": 1e400}',
            b'{"a": 9007199254740993.0}',
            b'{"a": NaN}',
        ],
    )
    def test_ambiguous_or_unsupported_json_is_refused(self, raw: bytes) -> None:
        """Reject arrays, duplicate keys, comments, bad bytes, and lossy numbers."""
        with pytest.raises(ValueError):  # noqa: PT011 — each case raises its own message
            JsonDocument.load(raw)

    def test_exact_decimal_numbers_are_kept(self) -> None:
        """Numbers that survive a rewrite unchanged are accepted."""
        result = JsonDocument.load(b'{"a": 1.5, "b": 1e5, "c": 12345678901234567890}')

        assert result == {"a": 1.5, "b": 100000.0, "c": 12345678901234567890}, (
            f"Expected exact numbers, got {result!r}"
        )

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
        document = JsonEntryFile(raw)

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
        document = JsonEntryFile(b'{"mcpServers": {"ours": {"command": "b"}}}')

        result = document.remove("mcpServers", "ours", prune=prune)

        actual = result[:1] if result is not None else None
        assert actual == expected, f"Expected {expected!r}, got {result!r}"

    def test_digest_of_missing_entry_is_none(self) -> None:
        """A missing container or key reads as absent."""
        result = JsonEntryFile(None).digest("mcpServers", "ours")

        assert result is None, f"Expected absent entry, got {result!r}"

    @pytest.mark.parametrize(
        "raw", [b"not json", b'{"mcpServers": []}', b'{"mcpServers": null}']
    )
    def test_unusable_file_is_a_conflict(self, raw: bytes) -> None:
        """Invalid files and non-object containers block instead of being rewritten."""
        with pytest.raises(ConflictError):
            JsonEntryFile(raw).digest("mcpServers", "ours")


USER_TOML = b"""# Codex settings
model = "o3"  # pinned

[mcp_servers.mine]
command = "mine"

[profiles.fast]
model = "mini"
"""


class TestTomlEntryFile:
    """TOML entry edits keep comments, layout, and every other table."""

    def test_open_picks_the_format_from_the_suffix(self) -> None:
        """``.toml`` paths get the TOML editor; everything else stays JSON."""
        result = (
            type(EntryFile.open("config.toml", None)),
            type(EntryFile.open("mcp-config.json", None)),
        )

        assert result == (TomlEntryFile, JsonEntryFile), f"Got {result!r}"

    def test_set_keeps_comments_and_other_tables(self) -> None:
        """Adding a server leaves the user's text byte-for-byte in place."""
        document = TomlEntryFile(USER_TOML)

        result = document.set(
            "mcp_servers", "ours", {"command": "npx", "env": {"TOKEN": "x"}}
        )

        assert result.startswith(USER_TOML.split(b"[profiles")[0]), (
            f"Expected the user's text kept, got {result!r}"
        )
        assert b"[profiles.fast]" in result, f"Expected tables kept, got {result!r}"
        assert tomllib.loads(result.decode())["mcp_servers"] == {
            "mine": {"command": "mine"},
            "ours": {"command": "npx", "env": {"TOKEN": "x"}},
        }, f"Expected one added server, got {result!r}"

    def test_set_then_remove_restores_the_file(self) -> None:
        """Removing the entry it added gives back the original bytes."""
        document = TomlEntryFile(USER_TOML)
        document.set("mcp_servers", "ours", {"command": "npx"})

        result = document.remove("mcp_servers", "ours", prune=False)

        assert result == USER_TOML, f"Expected original text, got {result!r}"

    def test_set_replaces_an_entry_in_place(self) -> None:
        """Updating an entry keeps its position among the user's tables."""
        document = TomlEntryFile(USER_TOML)

        result = document.set("mcp_servers", "mine", {"command": "new"})

        assert result == USER_TOML.replace(b'"mine"', b'"new"'), (
            f"Expected one changed value, got {result!r}"
        )

    def test_new_file_gets_a_dotted_table_header(self) -> None:
        """A created container is written as ``[mcp_servers.<key>]`` only."""
        document = TomlEntryFile(None)

        result = document.set("mcp_servers", "ours", {"url": "https://x"})

        assert result == b'[mcp_servers.ours]\nurl = "https://x"\n', (
            f"Expected a single table, got {result!r}"
        )

    def test_new_container_follows_existing_settings_after_a_blank_line(
        self,
    ) -> None:
        """A first server is appended below the user's settings, which stay as-is."""
        document = TomlEntryFile(b'model = "o3"\n')

        result = document.set("mcp_servers", "ours", {"command": "x"})

        assert result == b'model = "o3"\n\n[mcp_servers.ours]\ncommand = "x"\n', (
            f"Expected an appended table, got {result!r}"
        )
        assert document.remove("mcp_servers", "absent", prune=True) == result, (
            "Expected removing a missing entry to change nothing"
        )

    def test_inline_container_gets_inline_entries(self) -> None:
        """An inline ``mcp_servers = {...}`` stays valid when an entry is added."""
        document = TomlEntryFile(b'mcp_servers = { a = { command = "x" } }\n')

        result = document.set("mcp_servers", "b", {"command": "y", "args": ["1"]})

        assert tomllib.loads(result.decode())["mcp_servers"] == {
            "a": {"command": "x"},
            "b": {"command": "y", "args": ["1"]},
        }, f"Expected both inline entries, got {result!r}"

    @pytest.mark.parametrize(("prune", "expected"), [(True, None), (False, b"")])
    def test_remove_last_entry_prunes_only_when_requested(
        self, *, prune: bool, expected: bytes | None
    ) -> None:
        """An emptied file SkillSinc created is reported for deletion."""
        document = TomlEntryFile(b'[mcp_servers.ours]\ncommand = "b"\n')

        result = document.remove("mcp_servers", "ours", prune=prune)

        assert result == expected, f"Expected {expected!r}, got {result!r}"

    def test_pruning_keeps_other_settings(self) -> None:
        """Dropping the emptied container leaves the rest of the file."""
        document = TomlEntryFile(b'model = "o3"\n\n[mcp_servers.ours]\ncommand = "b"\n')

        result = document.remove("mcp_servers", "ours", prune=True)

        assert result is not None, "Expected the file kept"
        assert tomllib.loads(result.decode()) == {"model": "o3"}, f"Got {result!r}"

    def test_digest_matches_json_digest_and_tolerates_dates(self) -> None:
        """Equal values digest the same in TOML and JSON; dates do not crash."""
        document = TomlEntryFile(
            b'[mcp_servers.a]\ncommand = "x"\nargs = ["1"]\n'
            b"[mcp_servers.b]\nwhen = 2026-10-20\n"
        )

        result = document.digest("mcp_servers", "a")

        assert result == JsonDocument.digest({"args": ["1"], "command": "x"}), (
            f"Expected format-independent digest, got {result}"
        )
        assert document.digest("mcp_servers", "b") is not None, "Expected a digest"
        assert document.digest("mcp_servers", "c") is None, "Expected absent"
        assert TomlEntryFile(None).digest("mcp_servers", "a") is None, "Expected absent"

    @pytest.mark.parametrize(
        "raw",
        [b"a = 1\na = 2\n", b"[x]\n[x]\n", b"a = \n", b"\xff", b"mcp_servers = 3\n"],
    )
    def test_unusable_file_is_a_conflict(self, raw: bytes) -> None:
        """Invalid TOML and non-table containers block instead of being rewritten."""
        with pytest.raises(ConflictError):
            TomlEntryFile(raw).digest("mcp_servers", "ours")

    @pytest.mark.parametrize("value", [{"command": None}, ["not", "a", "table"]])
    def test_values_toml_cannot_hold_are_a_conflict(self, value: JsonValue) -> None:
        """Nulls and non-table entries are refused rather than written badly."""
        with pytest.raises(ConflictError):
            TomlEntryFile(None).set("mcp_servers", "ours", value)

    def test_edit_that_reads_back_differently_is_a_conflict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Output that does not parse back to the intended data blocks the write."""
        document = TomlEntryFile(USER_TOML)
        monkeypatch.setattr(tomlkit, "dumps", lambda _: 'model = "other"\n')

        with pytest.raises(ConflictError, match="Fix it by hand"):
            document.set("mcp_servers", "ours", {"command": "y"})

    def test_edit_that_does_not_parse_back_is_a_conflict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Output that is not valid TOML blocks the write."""
        document = TomlEntryFile(USER_TOML)
        monkeypatch.setattr(tomlkit, "dumps", lambda _: "a = ")

        with pytest.raises(ConflictError, match="Fix it by hand"):
            document.remove("mcp_servers", "mine", prune=False)

    @pytest.mark.parametrize(
        "raw",
        [
            b'mcp_servers.a.command = "x"\n',
            b'[mcp_servers.a]\ncommand = "x"\n[other]\nq = 1\n'
            b'[mcp_servers.a.env]\nK = "v"\n',
        ],
    )
    def test_unusual_layouts_are_edited_faithfully(self, raw: bytes) -> None:
        """Dotted keys and split sub-tables still give the intended data."""
        document = TomlEntryFile(raw)

        result = document.set("mcp_servers", "a", {"command": "y"})

        assert tomllib.loads(result.decode())["mcp_servers"] == {
            "a": {"command": "y"}
        }, f"Expected the entry replaced, got {result!r}"
