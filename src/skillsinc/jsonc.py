"""Find object members by position in JSON with comments (JSONC), for in-place edits.

VS Code reads ``mcp.json`` as JSONC: ``//`` and ``/* */`` comments and trailing
commas are allowed. Rewriting such a file from parsed data would drop every
comment, so edits splice text at the positions recorded here instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

from skillsinc.models import JsonValue

WHITESPACE: Final = " \t\r\n"
VALUE_END: Final = ",]}" + WHITESPACE


@dataclass(frozen=True, slots=True)
class Member:
    """One ``"key": value`` pair, by offsets into the text."""

    key: str
    start: int
    value_start: int
    value_end: int
    comma: int | None


@dataclass(frozen=True, slots=True)
class ObjectSpan:
    """An object's braces and direct members, by offsets into the text."""

    open: int
    close: int
    members: tuple[Member, ...]

    def find(self, key: str) -> Member | None:
        """Return the member named ``key``, or None."""
        return next((member for member in self.members if member.key == key), None)


class Jsonc:
    """Blank comments, locate members, and splice edits that keep everything else."""

    @staticmethod
    def blank_comments(text: str) -> str:
        """Return ``text`` with comments replaced by spaces, keeping every offset.

        Raises:
            ValueError: If a string or block comment is not terminated.
        """
        out = list(text)
        index = 0
        while index < len(text):
            char = text[index]
            if char == '"':
                index = Jsonc._string_end(text, index)
                continue
            if text.startswith("//", index):
                end = text.find("\n", index)
                end = len(text) if end == -1 else end
            elif text.startswith("/*", index):
                end = text.find("*/", index + 2)
                if end == -1:
                    msg = "unterminated block comment"
                    raise ValueError(msg)
                end += 2
            else:
                index += 1
                continue
            for position in range(index, end):
                if out[position] not in "\r\n":
                    out[position] = " "
            index = end
        return "".join(out)

    @staticmethod
    def blank_trailing_commas(bare: str) -> str:
        """Return comment-free ``bare`` with trailing commas replaced by spaces."""
        out = list(bare)
        index = 0
        while index < len(bare):
            char = bare[index]
            if char == '"':
                index = Jsonc._string_end(bare, index)
                continue
            if char == ",":
                after = Jsonc._skip(bare, index + 1)
                if after < len(bare) and bare[after] in "]}":
                    out[index] = " "
            index += 1
        return "".join(out)

    @staticmethod
    def has_comments(text: str) -> bool:
        """Return True if ``text`` holds any comment."""
        return Jsonc.blank_comments(text) != text

    @staticmethod
    def object_at(bare: str, start: int) -> ObjectSpan:
        """Return the object whose ``{`` is the first character from ``start``.

        ``bare`` is the text with comments blanked; trailing commas are allowed.

        Raises:
            ValueError: If no well-formed object starts there.
        """
        index = Jsonc._skip(bare, start)
        if index >= len(bare) or bare[index] != "{":
            msg = "expected an object"
            raise ValueError(msg)
        opening = index
        members: list[Member] = []
        index = Jsonc._skip(bare, index + 1)
        while index < len(bare) and bare[index] != "}":
            if bare[index] != '"':
                msg = f"expected a key at offset {index}"
                raise ValueError(msg)
            key_end = Jsonc._string_end(bare, index)
            key = str(json.loads(bare[index:key_end]))
            colon = Jsonc._skip(bare, key_end)
            if colon >= len(bare) or bare[colon] != ":":
                msg = f"expected ':' at offset {colon}"
                raise ValueError(msg)
            value_start = Jsonc._skip(bare, colon + 1)
            value_end = Jsonc._value_end(bare, value_start)
            after = Jsonc._skip(bare, value_end)
            comma = after if after < len(bare) and bare[after] == "," else None
            members.append(Member(key, index, value_start, value_end, comma))
            index = Jsonc._skip(bare, after + 1) if comma is not None else after
        if index >= len(bare):
            msg = "unterminated object"
            raise ValueError(msg)
        return ObjectSpan(opening, index, tuple(members))

    @staticmethod
    def insert(text: str, span: ObjectSpan, key: str, value: JsonValue) -> str:
        """Return ``text`` with ``"key": value`` added as the last member of ``span``.

        The member goes after the last thing inside the braces (a comment
        included), indented like its siblings.
        """
        newline = Jsonc.newline(text)
        outer = Jsonc.indent(text, span.open, whole_line=True)
        step = Jsonc.step(text, span)
        inner = outer + step
        if span.members:
            inner = Jsonc.indent(text, span.members[-1].start) or inner
        rendered = json.dumps(value, indent=step, ensure_ascii=False)
        rendered = rendered.replace("\n", newline + inner)
        entry = f"{newline}{inner}{json.dumps(key, ensure_ascii=False)}: {rendered}"
        position = span.close
        while position > span.open + 1 and text[position - 1] in WHITESPACE:
            position -= 1
        if position == span.open + 1:
            entry += newline + outer
        edits: list[tuple[int, int, str]] = []
        if span.members:
            last = span.members[-1]
            if last.comma is not None:
                # The file uses trailing commas, so this member gets one too
                entry += ","
            elif last.value_end == position:
                entry = "," + entry
            else:
                edits.append((last.value_end, last.value_end, ","))
        edits.append((position, position, entry))
        return Jsonc.apply(text, edits)

    @staticmethod
    def replace(text: str, span: ObjectSpan, member: Member, value: JsonValue) -> str:
        """Return ``text`` with ``member``'s value replaced, indented in place."""
        newline = Jsonc.newline(text)
        indent = Jsonc.indent(text, member.start, whole_line=True)
        rendered = json.dumps(value, indent=Jsonc.step(text, span), ensure_ascii=False)
        rendered = rendered.replace("\n", newline + indent)
        return Jsonc.apply(text, [(member.value_start, member.value_end, rendered)])

    @staticmethod
    def delete(text: str, span: ObjectSpan, member: Member) -> str:
        """Return ``text`` without ``member``, and its line when it had one alone."""
        start = member.start
        end = member.comma + 1 if member.comma is not None else member.value_end
        edits: list[tuple[int, int, str]] = []
        position = span.members.index(member)
        if member.comma is None and position > 0:
            previous = span.members[position - 1].comma
            if previous is not None:
                if not text[previous + 1 : start].strip(WHITESPACE):
                    # Undo exactly what an insert after the last member added
                    return Jsonc.apply(text, [(previous, end, "")])
                edits.append((previous, previous + 1, ""))
        line_start = start
        while line_start > 0 and text[line_start - 1] in " \t":
            line_start -= 1
        line_end = end
        while line_end < len(text) and text[line_end] in " \t":
            line_end += 1
        alone = (line_start == 0 or text[line_start - 1] == "\n") and (
            line_end == len(text) or text[line_end] in "\r\n"
        )
        if alone:
            start = line_start
            end = line_end + (2 if text.startswith("\r\n", line_end) else 1)
        elif member.comma is not None:
            end = line_end
        inside = text[span.open + 1 : span.close]
        if len(span.members) == 1 and not Jsonc.has_comments(inside):
            # Nothing else is inside the braces, so leave a plain empty object
            return Jsonc.apply(text, [(span.open + 1, span.close, "")])
        edits.append((start, min(end, len(text)), ""))
        return Jsonc.apply(text, edits)

    @staticmethod
    def apply(text: str, edits: list[tuple[int, int, str]]) -> str:
        """Apply non-overlapping ``(start, end, replacement)`` edits."""
        for start, end, replacement in sorted(edits, key=lambda edit: -edit[0]):
            text = text[:start] + replacement + text[end:]
        return text

    @staticmethod
    def indent(text: str, index: int, *, whole_line: bool = False) -> str:
        """Return the indentation of the line holding ``index``.

        Unless ``whole_line``, it is empty when something precedes ``index``
        on that line.
        """
        line_start = text.rfind("\n", 0, index) + 1
        prefix = text[line_start:index]
        leading = prefix[: len(prefix) - len(prefix.lstrip(" \t"))]
        if whole_line or leading == prefix:
            return leading
        return ""

    @staticmethod
    def step(text: str, span: ObjectSpan) -> str:
        """Return one indentation level as the members use it (else two spaces)."""
        outer = Jsonc.indent(text, span.open, whole_line=True)
        for member in span.members:
            inner = Jsonc.indent(text, member.start)
            if inner.startswith(outer) and inner != outer:
                return inner[len(outer) :]
        return "  "

    @staticmethod
    def newline(text: str) -> str:
        """Return the file's line ending."""
        return "\r\n" if "\r\n" in text else "\n"

    # Internals

    @staticmethod
    def _skip(text: str, index: int) -> int:
        """Return the first offset at or after ``index`` that is not whitespace."""
        while index < len(text) and text[index] in WHITESPACE:
            index += 1
        return index

    @staticmethod
    def _string_end(text: str, index: int) -> int:
        """Return the offset just past the string starting at ``index``."""
        position = index + 1
        while position < len(text):
            if text[position] == "\\":
                position += 2
                continue
            if text[position] == '"':
                return position + 1
            position += 1
        msg = "unterminated string"
        raise ValueError(msg)

    @staticmethod
    def _value_end(text: str, index: int) -> int:
        """Return the offset just past the value starting at ``index``."""
        if index >= len(text):
            msg = "expected a value"
            raise ValueError(msg)
        char = text[index]
        if char == '"':
            return Jsonc._string_end(text, index)
        if char in "{[":
            return Jsonc._nested_end(text, index)
        position = index
        while position < len(text) and text[position] not in VALUE_END:
            position += 1
        if position == index:
            msg = f"expected a value at offset {index}"
            raise ValueError(msg)
        return position

    @staticmethod
    def _nested_end(text: str, index: int) -> int:
        """Return the offset just past the object or array starting at ``index``."""
        is_object = text[index] == "{"
        closing = "}" if is_object else "]"
        position = Jsonc._skip(text, index + 1)
        while position < len(text) and text[position] != closing:
            if is_object:
                position = Jsonc._skip(text, Jsonc._string_end(text, position))
                if position >= len(text) or text[position] != ":":
                    msg = f"expected ':' at offset {position}"
                    raise ValueError(msg)
                position = Jsonc._skip(text, position + 1)
            position = Jsonc._skip(text, Jsonc._value_end(text, position))
            if position < len(text) and text[position] == ",":
                position = Jsonc._skip(text, position + 1)
        if position >= len(text):
            msg = "unterminated object or array"
            raise ValueError(msg)
        return position + 1
