"""Test strict JSON parsing and entry edits inside shared configuration files."""

from __future__ import annotations

import json
import tomllib

import pytest
import tomlkit

from skillsinc.entries import (
    EntryFile,
    JsoncEntryFile,
    JsonDocument,
    JsonEntryFile,
    TomlEntryFile,
)
from skillsinc.exceptions import ConflictError
from skillsinc.jsonc import Jsonc
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
        unchanged = document.remove("mcp_servers", "absent", prune=True)
        assert unchanged == result, (
            f"Expected removing a missing entry to change nothing, got {unchanged!r}"
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

    def test_pruning_keeps_comments_the_user_added(self) -> None:
        """A created file is kept, comments intact, when only comments remain."""
        raw = b'# my notes\n\n[mcp_servers.ours]\ncommand = "b"\n'
        document = TomlEntryFile(raw)

        result = document.remove("mcp_servers", "ours", prune=True)

        assert result is not None, "Expected the commented file kept"
        assert b"# my notes" in result, f"Expected the comment kept, got {result!r}"

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
            (
                b'[mcp_servers.a]\ncommand = "x"\n[other]\nq = 1\n'
                b'[mcp_servers.a.env]\nK = "v"\n'
            ),
        ],
    )
    def test_unusual_layouts_are_edited_faithfully(self, raw: bytes) -> None:
        """Dotted keys and split sub-tables still give the intended data."""
        document = TomlEntryFile(raw)

        result = document.set("mcp_servers", "a", {"command": "y"})

        assert tomllib.loads(result.decode())["mcp_servers"] == {
            "a": {"command": "y"}
        }, f"Expected the entry replaced, got {result!r}"


USER_JSONC = b"""{
  // My servers
  "servers": {
    "mine": {"type": "stdio", "command": "x"}, // keep me
  },
  /* inputs */
  "inputs": [],
}
"""
SERVER: JsonValue = {"type": "stdio", "command": "gh", "args": ["mcp"]}


class TestJsoncEntryFile:
    """JSONC entry edits splice text, keeping comments, commas, and layout."""

    def test_open_picks_jsonc_for_vscode_mcp_json(self) -> None:
        """Only VS Code's ``mcp.json`` is read with comments allowed."""
        result = (
            type(EntryFile.open("../mcp.json", None)),
            type(EntryFile.open(".claude.json", None)),
        )

        assert result == (JsoncEntryFile, JsonEntryFile), f"Got {result!r}"

    @pytest.mark.parametrize(
        "raw",
        [
            USER_JSONC,
            b"{}",
            b'{"servers": {}}',
            b'{\n    "inputs": [] // only inputs\n}\n',
            b'{\n\t"servers": {\n\t\t"a": {\n\t\t\t"command": "a"\n\t\t}\n\t}\n}\n',
            b'{\r\n  "servers": {\r\n    "a": {"command": "a"}\r\n  }\r\n}\r\n',
            b'\xef\xbb\xbf{"x": "// not /* a comment", "servers": {"a": {}}}',
        ],
    )
    def test_set_update_and_remove_keep_everything_else(self, raw: bytes) -> None:
        """Add, change, then remove one server; the user's bytes come back exactly."""
        document = JsoncEntryFile(raw)

        added = document.set("servers", "gh", SERVER)
        changed: JsonValue = {"command": "gh2"}
        updated = document.set("servers", "gh", changed)
        restored = document.remove("servers", "gh", prune=False)

        for result, value in ((added, SERVER), (updated, changed)):
            parsed = JsoncEntryFile(result)
            assert parsed.digest("servers", "gh") == JsonDocument.digest(value), (
                f"Expected the server set, got {result!r}"
            )
        assert JsoncEntryFile(restored or b"{}").digest("servers", "gh") is None
        if raw not in {b"{}", b'{\n    "inputs": [] // only inputs\n}\n'}:
            assert restored == raw, f"Expected original bytes, got {restored!r}"

    def test_added_entry_matches_sibling_layout(self) -> None:
        """A new server follows its siblings' indentation and the file's commas."""
        result = JsoncEntryFile(USER_JSONC).set("servers", "gh", {"command": "gh"})

        assert result == USER_JSONC.replace(
            b"// keep me\n",
            b'// keep me\n    "gh": {\n      "command": "gh"\n    },\n',
        ), f"Expected an indented member with a trailing comma, got {result!r}"

    def test_new_container_is_added_after_comments(self) -> None:
        """A missing container goes last, after a trailing comment."""
        raw = b'{\n  "inputs": [] // only inputs\n}\n'

        result = JsoncEntryFile(raw).set("servers", "gh", {"command": "gh"})

        assert result == (
            b'{\n  "inputs": [], // only inputs\n  "servers": {\n'
            b'    "gh": {\n      "command": "gh"\n    }\n  }\n}\n'
        ), f"Got {result!r}"

    def test_pruning_removes_a_created_file_without_comments(self) -> None:
        """The last server in a file SkillSinc created deletes the file."""
        document = JsoncEntryFile(None)
        document.set("servers", "gh", SERVER)

        result = document.remove("servers", "gh", prune=True)

        assert result is None, f"Expected the file to be deleted, got {result!r}"

    def test_pruning_keeps_comments_the_user_added(self) -> None:
        """A comment added to a created file keeps the file."""
        raw = b'{\n  // mine\n  "servers": {"gh": {"command": "gh"}}\n}\n'

        result = JsoncEntryFile(raw).remove("servers", "gh", prune=True)

        assert result == b"{\n  // mine\n}\n", f"Got {result!r}"

    def test_pruning_keeps_comments_inside_the_container(self) -> None:
        """A comment inside ``servers`` keeps the container and the file."""
        raw = b'{\n  "servers": {\n    // mine\n    "gh": {"command": "gh"}\n  }\n}\n'

        result = JsoncEntryFile(raw).remove("servers", "gh", prune=True)

        assert result == b'{\n  "servers": {\n    // mine\n  }\n}\n', f"Got {result!r}"

    def test_removing_a_missing_entry_changes_nothing(self) -> None:
        """Absent entries leave the bytes alone."""
        result = JsoncEntryFile(USER_JSONC).remove("servers", "gh", prune=True)

        assert result == USER_JSONC, f"Got {result!r}"

    @pytest.mark.parametrize(
        "raw",
        [
            b"{/* open",
            b'{"a": 1, "a": 2}',
            b"[]",
            b'{"servers": 3}',
            b'{"a": "open',
            b"\xff",
        ],
    )
    def test_unusable_file_is_a_conflict(self, raw: bytes) -> None:
        """Broken JSONC and non-object containers block instead of being rewritten."""
        with pytest.raises(ConflictError):
            JsoncEntryFile(raw).digest("servers", "gh")

    def test_edit_that_reads_back_differently_is_a_conflict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spliced text that does not parse back to the intended data blocks."""
        document = JsoncEntryFile(USER_JSONC)
        monkeypatch.setattr(Jsonc, "insert", staticmethod(lambda *_: "{}"))

        with pytest.raises(ConflictError, match="Fix it by hand"):
            document.set("servers", "gh", SERVER)

    def test_edit_that_does_not_parse_back_is_a_conflict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spliced text that is not JSONC blocks."""
        document = JsoncEntryFile(USER_JSONC)
        monkeypatch.setattr(Jsonc, "delete", staticmethod(lambda *_: "{"))

        with pytest.raises(ConflictError, match="Fix it by hand"):
            document.remove("servers", "mine", prune=False)


class TestJsonc:
    """The position scanner refuses text it cannot locate members in."""

    @pytest.mark.parametrize(
        "bare",
        [
            "",
            "[]",
            "{1: 2}",
            '{"a" 1}',
            '{"a": }',
            '{"a": 1',
            '{"a": {"b" 1}}',
            '{"a": [1, 2}',
            '{"a": [1, 2',
            '{"a',
            '{"a": ',
        ],
    )
    def test_malformed_objects_are_refused(self, bare: str) -> None:
        """No member positions are reported for malformed text."""
        with pytest.raises(ValueError, match=r"expected|unterminated"):
            Jsonc.object_at(bare, 0)

    def test_members_record_keys_values_and_commas(self) -> None:
        """Offsets point at each key, value, and separating comma."""
        span = Jsonc.object_at('{"a": [1, {"b": 2}], "c": "x",}', 0)

        assert [(m.key, m.value_start, m.value_end, m.comma) for m in span.members] == [
            ("a", 6, 19, 19),
            ("c", 26, 29, 29),
        ], f"Got {span!r}"

    @pytest.mark.parametrize(
        ("text", "key", "expected"),
        [
            ('{\n  "a": 1,\n  "b": 2,\n  "c": 3\n}', "b", '{\n  "a": 1,\n  "c": 3\n}'),
            ('{\n  "a": 1, // x\n  "b": 2\n}', "b", '{\n  "a": 1 // x\n}'),
            ('{"a": 1, "b": 2}', "a", '{"b": 2}'),
        ],
    )
    def test_delete_removes_one_member_and_its_separator(
        self, text: str, key: str, expected: str
    ) -> None:
        """Deleting keeps the remaining members, comments, and valid commas."""
        span = Jsonc.object_at(Jsonc.blank_comments(text), 0)
        member = span.find(key)
        assert member is not None, f"Expected {key!r} in {text!r}"

        result = Jsonc.delete(text, span, member)

        assert result == expected, f"Got {result!r}"

    def test_comments_are_blanked_without_moving_text(self) -> None:
        """Comment characters become spaces; newlines and strings stay put."""
        text = '{"a\\"b": 1 /* x\ny */ // z\n}'

        result = Jsonc.blank_comments(text)

        assert result == '{"a\\"b": 1     \n         \n}', f"Got {result!r}"
