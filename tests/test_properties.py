"""Property-based checks of the codecs that rewrite users' files in place.

Hypothesis generates many inputs per property; each property states an
invariant that must hold for every one, not just the hand-picked examples in
the module tests.
"""

from __future__ import annotations

import json
import re
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from skillsinc.blocks import BEGIN, END, ManagedBlock
from skillsinc.frontmatter import FrontmatterCodec
from skillsinc.jsonc import Jsonc
from skillsinc.models import FrontmatterValue, JsonValue

COMMENT: Final = "/* kept */ "

text = st.text(
    st.characters(codec="utf-8", exclude_categories=("Cs",), exclude_characters="\r"),
    max_size=80,
)
keys = st.text(st.characters(codec="utf-8", categories=("L", "N")), min_size=1)
scalars: st.SearchStrategy[FrontmatterValue] = st.one_of(
    st.none(), st.booleans(), st.integers(), text
)
frontmatter_values: st.SearchStrategy[FrontmatterValue] = st.recursive(
    scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(keys, children, max_size=3),
    ),
    max_leaves=8,
)
json_values: st.SearchStrategy[JsonValue] = st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(), text),
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(keys, children, max_size=3),
    ),
    max_leaves=8,
)
json_objects = st.dictionaries(keys, json_values, max_size=5)
markdown = text.filter(lambda value: BEGIN not in value and END not in value)


def _commented(value: dict[str, JsonValue]) -> str:
    """Return ``value`` as indented JSON with a block comment opening every line."""
    lines = json.dumps(value, indent=2, ensure_ascii=False).split("\n")
    return "\n".join(COMMENT + line for line in lines)


def _parse(jsonc: str) -> object:
    """Parse JSONC the way VS Code does: comments and trailing commas allowed."""
    return json.loads(Jsonc.blank_trailing_commas(Jsonc.blank_comments(jsonc)))


class TestFrontmatterRoundTrip:
    """Rendered Markdown parses back to the same header and body."""

    @given(st.dictionaries(keys, frontmatter_values, min_size=1, max_size=5), text)
    def test_render_then_parse_is_identity(
        self, header: dict[str, FrontmatterValue], body: str
    ) -> None:
        """Every header and body survive a render and parse unchanged."""
        codec = FrontmatterCodec()

        document = codec.parse(codec.render(header, body))

        actual = (document.frontmatter, document.body)
        assert actual == (header, body), f"Expected {(header, body)!r}, got {actual!r}"

    @given(text.filter(lambda body: not re.match(r"\A(﻿)?---", body)))
    def test_body_without_header_is_kept_verbatim(self, body: str) -> None:
        """A file with no header parses to an empty header and its exact text."""
        codec = FrontmatterCodec()

        document = codec.parse(codec.render({}, body))

        assert (document.frontmatter, document.body) == ({}, body), (
            f"Expected ({{}}, {body!r}), got {(document.frontmatter, document.body)!r}"
        )


class TestManagedBlockSplice:
    """The managed block is found, replaced, and removed without touching the rest."""

    @given(markdown, st.lists(st.tuples(keys, markdown), min_size=1, max_size=3))
    def test_spliced_block_is_extracted_unchanged(
        self, content: str, sections: list[tuple[str, str]]
    ) -> None:
        """After a splice, ``extract`` returns exactly the block written."""
        block = ManagedBlock.build(sections)

        spliced = ManagedBlock().splice(content, block)

        assert ManagedBlock().extract(spliced) == block, (
            f"Expected {block!r} extracted from {spliced!r}"
        )

    @given(markdown, st.lists(st.tuples(keys, markdown), min_size=1, max_size=3))
    def test_splicing_the_same_block_twice_changes_nothing(
        self, content: str, sections: list[tuple[str, str]]
    ) -> None:
        """A second sync with an unchanged library rewrites nothing."""
        block = ManagedBlock.build(sections)
        once = ManagedBlock().splice(content, block)

        twice = ManagedBlock().splice(once, block)

        assert twice == once, f"Expected idempotent splice, got {once!r} -> {twice!r}"

    @given(markdown, st.lists(st.tuples(keys, markdown), min_size=1, max_size=3))
    def test_removing_the_block_keeps_the_user_text(
        self, content: str, sections: list[tuple[str, str]]
    ) -> None:
        """Adding then removing the block leaves the user's text, trimmed at ends."""
        added = ManagedBlock().splice(content, ManagedBlock.build(sections))

        removed = ManagedBlock().splice(added, None)

        assert removed.strip() == content.strip(), (
            f"Expected {content.strip()!r} kept, got {removed.strip()!r}"
        )


class TestJsoncEdits:
    """Member edits change the parsed data as intended and keep every comment."""

    @given(json_objects)
    def test_blanking_comments_keeps_offsets(self, value: dict[str, JsonValue]) -> None:
        """Blanked text has the same length and parses to the original data."""
        source = _commented(value)

        bare = Jsonc.blank_comments(source)

        assert (len(bare), json.loads(bare)) == (len(source), value), (
            f"Expected {len(source)} characters parsing to {value!r}, got {bare!r}"
        )

    @given(json_objects, keys, json_values)
    def test_insert_or_replace_sets_the_key_and_keeps_comments(
        self, value: dict[str, JsonValue], key: str, new: JsonValue
    ) -> None:
        """Setting a key yields the expected data; no comment is lost."""
        source = _commented(value)
        span = Jsonc.object_at(Jsonc.blank_comments(source), 0)
        member = span.find(key)

        if member is None:
            edited = Jsonc.insert(source, span, key, new)
        else:
            edited = Jsonc.replace(source, span, member, new)

        assert _parse(edited) == value | {key: new}, (
            f"Expected {value | {key: new}!r}, got {edited!r}"
        )
        assert edited.count(COMMENT.strip()) == source.count(COMMENT.strip()), (
            f"Expected every comment kept, got {edited!r}"
        )

    @given(json_objects.filter(bool), st.data())
    def test_delete_removes_only_that_key(
        self, value: dict[str, JsonValue], data: st.DataObject
    ) -> None:
        """Deleting a member yields the data without it."""
        key = data.draw(st.sampled_from(sorted(value)))
        source = json.dumps(value, indent=2, ensure_ascii=False)
        span = Jsonc.object_at(source, 0)
        member = span.find(key)
        assert member is not None, f"Expected {key!r} in {source!r}"

        edited = Jsonc.delete(source, span, member)

        expected = {name: item for name, item in value.items() if name != key}
        assert json.loads(edited) == expected, f"Expected {expected!r}, got {edited!r}"
