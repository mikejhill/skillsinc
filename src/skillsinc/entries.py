"""Read, edit, and write keyed entries inside shared JSON, JSONC, and TOML files."""

from __future__ import annotations

import codecs
import copy
import json
import math
from abc import ABC, abstractmethod
from collections.abc import Mapping, MutableMapping, Sequence
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any, Final, cast, override

import tomlkit
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import InlineTable, Item, Table

from skillsinc.exceptions import ConflictError
from skillsinc.jsonc import Jsonc, ObjectSpan
from skillsinc.models import Digest, JsonValue

JSONC_FILES: Final = frozenset({"mcp.json"})

type Setting = tuple[str, str, JsonValue]
"""``(container, key, value)`` for one entry to set."""
type Removal = tuple[str, str]
"""``(container, key)`` for one entry to remove."""


class JsonDocument:
    """Strict JSON objects: no duplicate keys, no comments, stable output."""

    @staticmethod
    def load(raw: bytes) -> dict[str, JsonValue]:
        """Parse ``raw`` as a JSON object.

        Raises:
            ValueError: If the bytes are not UTF-8 JSON, repeat a key, or hold
                something other than an object at the top level.
        """
        loaded: object = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=JsonDocument._pairs,
            parse_float=JsonDocument._float,
            parse_constant=JsonDocument._constant,
        )
        if not isinstance(loaded, dict):
            msg = "expected a JSON object at the top level"
            raise ValueError(msg)  # noqa: TRY004 — callers handle ValueError only
        return {str(key): value for key, value in loaded.items()}

    @staticmethod
    def dump(document: dict[str, JsonValue]) -> bytes:
        """Return ``document`` as indented UTF-8 JSON with a trailing newline."""
        return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode()

    @staticmethod
    def digest(value: JsonValue) -> str:
        """Return a digest of ``value`` that ignores key order and whitespace."""
        canonical = json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=JsonDocument._other
        )
        return Digest.of(canonical.encode())

    # Internals

    @staticmethod
    def _other(value: object) -> str:
        """Stand in for a value JSON cannot hold, such as a TOML date a user wrote."""
        return f"<{type(value).__name__} {value}>"

    @staticmethod
    def _float(token: str) -> float:
        """Parse a decimal number, refusing ones a rewrite would change."""
        value = float(token)
        if not math.isfinite(value) or Decimal(repr(value)) != Decimal(token):
            msg = f"number {token} cannot be rewritten without changing its value"
            raise ValueError(msg)
        return value

    @staticmethod
    def _constant(token: str) -> float:
        """Refuse ``NaN`` and ``Infinity``, which are not valid JSON."""
        msg = f"'{token}' is not valid JSON"
        raise ValueError(msg)

    @staticmethod
    def _pairs(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
        """Build an object, refusing duplicate keys the parser would silently drop."""
        result: dict[str, JsonValue] = {}
        for key, value in pairs:
            if key in result:
                msg = f"duplicate key '{key}'"
                raise ValueError(msg)
            result[key] = value
        return result


class EntryFile(ABC):
    """One shared configuration file being edited entry by entry while planning."""

    raw: bytes | None

    @staticmethod
    def open(path: str, raw: bytes | None) -> EntryFile:
        """Return the editor for ``path``'s format.

        TOML for ``.toml``, JSONC for files their harness reads with comments
        (VS Code's ``mcp.json``), and strict JSON otherwise.

        Raises:
            ConflictError: If the file cannot be parsed safely.
        """
        if path.endswith(".toml"):
            return TomlEntryFile(raw)
        if PurePosixPath(path).name in JSONC_FILES:
            return JsoncEntryFile(raw)
        return JsonEntryFile(raw)

    @abstractmethod
    def digest(self, container: str, key: str) -> str | None:
        """Return the digest of the value at ``container.key``, or None if absent.

        Raises:
            ConflictError: If ``container`` exists but is not a table or object.
        """

    @abstractmethod
    def set(self, container: str, key: str, value: JsonValue) -> bytes:
        """Set ``container.key`` and return the new file bytes."""

    @abstractmethod
    def remove(self, container: str, key: str, *, prune: bool) -> bytes | None:
        """Remove ``container.key`` and return the new bytes, or None if now empty.

        With ``prune``, an emptied container is dropped and an emptied file is
        reported as None so the caller can delete it.
        """

    @abstractmethod
    def edit(
        self, sets: Sequence[Setting], removals: Sequence[Removal], *, prune: bool
    ) -> bytes | None:
        """Apply ``sets``, then ``removals``, and return the final bytes.

        The result is what calling ``set`` and ``remove`` in that order would
        give, but the file is rendered and checked once instead of after every
        entry, so planning many entries in one file stays linear.
        """


class JsonEntryFile(EntryFile):
    """One shared JSON file being edited entry by entry while planning."""

    def __init__(self, raw: bytes | None) -> None:
        """Parse the current file (None when it does not exist).

        Raises:
            ConflictError: If the file is not a plain JSON object.
        """
        self.raw = raw
        try:
            self._document = JsonDocument.load(raw) if raw is not None else {}
        except (UnicodeDecodeError, ValueError) as err:
            msg = (
                f"is not a plain JSON object ({err}); SkillSinc will not rewrite it."
                " Fix it by hand"
            )
            raise ConflictError(msg) from err

    @override
    def digest(self, container: str, key: str) -> str | None:
        """Return the digest of the value at ``container.key``, or None if absent.

        Raises:
            ConflictError: If ``container`` exists but is not an object.
        """
        values = self._container(container, create=False)
        if key not in values:
            return None
        return JsonDocument.digest(values[key])

    @override
    def set(self, container: str, key: str, value: JsonValue) -> bytes:
        """Set ``container.key`` and return the new file bytes."""
        self._container(container, create=True)[key] = value
        self.raw = JsonDocument.dump(self._document)
        return self.raw

    @override
    def remove(self, container: str, key: str, *, prune: bool) -> bytes | None:
        """Remove ``container.key`` and return the new bytes, or None if now empty.

        With ``prune``, an emptied container is dropped and an emptied file is
        reported as None so the caller can delete it.
        """
        return self.edit((), ((container, key),), prune=prune)

    @override
    def edit(
        self, sets: Sequence[Setting], removals: Sequence[Removal], *, prune: bool
    ) -> bytes | None:
        """Apply ``sets``, then ``removals``, and return the final bytes."""
        for container, key, value in sets:
            self._container(container, create=True)[key] = value
        emptied = False
        for container, key in removals:
            values = self._container(container, create=False)
            values.pop(key, None)
            if prune and not values:
                self._document.pop(container, None)
                emptied = emptied or not self._document
        self.raw = None if emptied else JsonDocument.dump(self._document)
        return self.raw

    # Internals

    def _container(self, container: str, *, create: bool) -> dict[str, JsonValue]:
        """Return the container object, creating it on request."""
        if container not in self._document:
            values: dict[str, JsonValue] = {}
            if create:
                self._document[container] = values
            return values
        values = self._document[container]
        if not isinstance(values, dict):
            msg = f"'{container}' is not a JSON object; fix it by hand"
            raise ConflictError(msg)
        return values


class JsoncEntryFile(EntryFile):
    """One shared JSONC file edited in place, keeping comments and layout.

    Comments and trailing commas are allowed. Each edit splices only the
    affected member's text and is checked by parsing the result back, so a
    layout the splicer cannot edit faithfully blocks instead of corrupting.
    """

    def __init__(self, raw: bytes | None) -> None:
        """Parse the current file (None when it does not exist).

        Raises:
            ConflictError: If the file is not a JSONC object.
        """
        self.raw = raw
        self._bom = raw is not None and raw.startswith(codecs.BOM_UTF8)
        try:
            self._text = raw.decode("utf-8-sig") if raw is not None else ""
            self._document = self._load(self._text) if raw is not None else {}
        except (UnicodeDecodeError, ValueError) as err:
            msg = (
                f"is not a JSON object, even allowing comments ({err});"
                " SkillSinc will not rewrite it. Fix it by hand"
            )
            raise ConflictError(msg) from err

    @override
    def digest(self, container: str, key: str) -> str | None:
        """Return the digest of the value at ``container.key``, or None if absent.

        Raises:
            ConflictError: If ``container`` exists but is not an object.
        """
        values = self._container(container)
        if key not in values:
            return None
        return JsonDocument.digest(values[key])

    @override
    def set(self, container: str, key: str, value: JsonValue) -> bytes:
        """Set ``container.key`` in place and return the new file bytes.

        Raises:
            ConflictError: If the edit does not read back as intended.
        """
        return self._set_all({container: {key: value}})

    @override
    def remove(self, container: str, key: str, *, prune: bool) -> bytes | None:
        """Remove ``container.key`` and return the new bytes, or None if now empty.

        With ``prune``, an emptied container without comments is dropped, and
        an emptied file without comments is reported as None so the caller can
        delete it.

        Raises:
            ConflictError: If the edit does not read back as intended.
        """
        values = self._container(container)
        if key not in values:
            return self.raw
        expected = copy.deepcopy(self._document)
        expected[container] = {name: v for name, v in values.items() if name != key}
        top = self._top()
        holder = top.find(container)
        span = Jsonc.object_at(self._bare(), holder.value_start) if holder else None
        member = span.find(key) if span else None
        if holder is None or span is None or member is None:
            # Unreachable: the parsed document and the scanned text agree
            return self.raw
        inside = self._text[span.open + 1 : span.close]
        if prune and len(span.members) == 1 and not Jsonc.has_comments(inside):
            del expected[container]
            text = Jsonc.delete(self._text, top, holder)
            if not expected and not Jsonc.has_comments(text):
                self._text, self._document, self.raw = "", {}, None
                return None
        else:
            text = Jsonc.delete(self._text, span, member)
        return self._commit(text, expected)

    @override
    def edit(
        self, sets: Sequence[Setting], removals: Sequence[Removal], *, prune: bool
    ) -> bytes | None:
        """Apply ``sets``, then ``removals``, and return the final bytes.

        Sets are spliced together, a container at a time; removals, which are
        rare in bulk, go one by one.

        Raises:
            ConflictError: If an edit does not read back as intended.
        """
        grouped: dict[str, dict[str, JsonValue]] = {}
        for container, key, value in sets:
            grouped.setdefault(container, {})[key] = value
        if grouped:
            self._set_all(grouped)
        for container, key in removals:
            self.remove(container, key, prune=prune)
        return self.raw

    # Internals

    def _set_all(self, grouped: Mapping[str, Mapping[str, JsonValue]]) -> bytes:
        """Set every ``container.key`` in ``grouped`` and return the new bytes.

        Raises:
            ConflictError: If the edit does not read back as intended.
        """
        expected = copy.deepcopy(self._document)
        for container, values in grouped.items():
            expected[container] = {**self._container(container), **values}
        if self.raw is None:
            return self._commit(JsonDocument.dump(expected).decode(), expected)
        text = self._text
        for container, values in grouped.items():
            top = Jsonc.object_at(Jsonc.blank_comments(text), 0)
            holder = top.find(container)
            if holder is None:
                text = Jsonc.insert(text, top, {container: dict(values)})
                continue
            span = Jsonc.object_at(Jsonc.blank_comments(text), holder.value_start)
            members = {member.key: member for member in span.members}
            replaced = {
                members[key]: value for key, value in values.items() if key in members
            }
            if replaced:
                text = Jsonc.replace(text, span, replaced)
            added = {key: value for key, value in values.items() if key not in members}
            if added:
                bare = Jsonc.blank_comments(text)
                span = Jsonc.object_at(bare, span.open)
                text = Jsonc.insert(text, span, added)
        return self._commit(text, expected)

    @staticmethod
    def _load(text: str) -> dict[str, JsonValue]:
        """Parse JSONC text strictly once comments and trailing commas are blanked."""
        plain = Jsonc.blank_trailing_commas(Jsonc.blank_comments(text))
        return JsonDocument.load(plain.encode())

    def _bare(self) -> str:
        """Return the current text with comments blanked."""
        return Jsonc.blank_comments(self._text)

    def _top(self) -> ObjectSpan:
        """Return the top-level object."""
        return Jsonc.object_at(self._bare(), 0)

    def _container(self, container: str) -> dict[str, JsonValue]:
        """Return the container object (empty if absent)."""
        if container not in self._document:
            return {}
        values = self._document[container]
        if not isinstance(values, dict):
            msg = f"'{container}' is not a JSON object; fix it by hand"
            raise ConflictError(msg)
        return cast("dict[str, JsonValue]", values)

    def _commit(self, text: str, expected: dict[str, JsonValue]) -> bytes:
        """Keep ``text`` if it reads back as ``expected``; otherwise refuse."""
        try:
            actual: object = self._load(text)
        except ValueError as err:
            actual = err
        if actual != expected:
            msg = (
                "could not be edited without changing other settings;"
                " SkillSinc will not rewrite it. Fix it by hand"
            )
            raise ConflictError(msg)
        self._text = text
        self._document = expected
        self.raw = (codecs.BOM_UTF8 if self._bom else b"") + text.encode()
        return self.raw


class TomlEntryFile(EntryFile):
    """One shared TOML file edited entry by entry, keeping comments and layout.

    Every entry is a table under one top-level container table, such as
    ``[mcp_servers.<key>]``. Each edit is checked by parsing the new text back,
    so a layout ``tomlkit`` cannot edit faithfully blocks instead of corrupting.
    """

    def __init__(self, raw: bytes | None) -> None:
        """Parse the current file (None when it does not exist).

        Raises:
            ConflictError: If the file is not valid UTF-8 TOML.
        """
        self.raw = raw
        try:
            text = raw.decode("utf-8-sig") if raw is not None else ""
            self._document = tomlkit.parse(text)
        except (UnicodeDecodeError, TOMLKitError) as err:
            msg = (
                f"is not valid TOML ({err}); SkillSinc will not rewrite it."
                " Fix it by hand"
            )
            raise ConflictError(msg) from err

    @override
    def digest(self, container: str, key: str) -> str | None:
        """Return the digest of the value at ``container.key``, or None if absent.

        Raises:
            ConflictError: If ``container`` exists but is not a table.
        """
        values = self._container(container)
        if values is None or key not in values:
            return None
        return JsonDocument.digest(self._plain(values[key]))

    @override
    def set(self, container: str, key: str, value: JsonValue) -> bytes:
        """Set ``container.key`` and return the new file bytes.

        Raises:
            ConflictError: If ``value`` is not a table or the edit does not
                read back as intended.
        """
        expected = self._document.unwrap()
        self._put(expected, container, key, value)
        return self._dump(expected)

    @override
    def remove(self, container: str, key: str, *, prune: bool) -> bytes | None:
        """Remove ``container.key`` and return the new bytes, or None if now empty.

        An emptied container is always dropped, since TOML cannot tell an empty
        table from a missing one; only ``prune`` reports an emptied file as None.

        Raises:
            ConflictError: If the edit does not read back as intended.
        """
        return self.edit((), ((container, key),), prune=prune)

    @override
    def edit(
        self, sets: Sequence[Setting], removals: Sequence[Removal], *, prune: bool
    ) -> bytes | None:
        """Apply ``sets``, then ``removals``, and return the final bytes.

        Raises:
            ConflictError: If a value is not a table or the result does not
                read back as intended.
        """
        expected = self._document.unwrap()
        changed = False
        for container, key, value in sets:
            self._put(expected, container, key, value)
            changed = True
        emptied = False
        for container, key in removals:
            values = self._container(container)
            if values is None or key not in values:
                continue
            changed = True
            del expected[container][key]
            del values[key]
            # An empty table header means nothing in TOML, so drop it either way
            if not values:
                del expected[container]
                del self._document[container]
                # Comments the user added keep a file SkillSinc created
                emptied = emptied or (
                    prune and not expected and not tomlkit.dumps(self._document).strip()
                )
        if emptied:
            self.raw = None
            return None
        return self._dump(expected) if changed else self.raw

    # Internals

    def _put(
        self, expected: dict[str, Any], container: str, key: str, value: JsonValue
    ) -> None:
        """Set ``container.key`` in the document and in ``expected``.

        Raises:
            ConflictError: If ``value`` is not a table.
        """
        if not isinstance(value, dict):
            msg = f"'{container}.{key}' must be a table"
            raise ConflictError(msg)
        values = self._container(container)
        expected.setdefault(container, {})[key] = value
        if values is None:
            if self._document:
                self._document.add(tomlkit.nl())
            values = tomlkit.table(is_super_table=True)
            self._document[container] = values
        values[key] = self._item(value, inline=isinstance(values, InlineTable))

    def _container(self, container: str) -> MutableMapping[str, object] | None:
        """Return the container table, or None if the file has none."""
        if container not in self._document:
            return None
        values = self._document[container]
        if not isinstance(values, MutableMapping):
            msg = f"'{container}' is not a TOML table; fix it by hand"
            raise ConflictError(msg)
        return cast("MutableMapping[str, object]", values)

    def _dump(self, expected: dict[str, object]) -> bytes:
        """Serialize the document, refusing output that reads back differently.

        Blank lines ``tomlkit`` leaves at the end of the file are trimmed, so
        removing an entry it appended gives back the user's original text.
        """
        text = tomlkit.dumps(self._document)
        if text.strip():
            text = text.rstrip("\n") + "\n"
        try:
            actual = tomlkit.parse(text).unwrap()
        except TOMLKitError as err:
            actual = err
        if actual != expected:
            msg = (
                "could not be edited without changing other settings;"
                " SkillSinc will not rewrite it. Fix it by hand"
            )
            raise ConflictError(msg)
        self.raw = text.encode()
        return self.raw

    @staticmethod
    def _item(value: JsonValue, *, inline: bool) -> Item:
        """Convert a plain value, writing nested tables inline under the entry."""
        if isinstance(value, dict):
            table = tomlkit.inline_table() if inline else tomlkit.table()
            for key, nested in value.items():
                table[key] = TomlEntryFile._item(nested, inline=True)
            if isinstance(table, Table):
                table.add(tomlkit.nl())
            return table
        if isinstance(value, list):
            array = tomlkit.array()
            array.extend(TomlEntryFile._item(nested, inline=True) for nested in value)
            return array
        if value is None:
            msg = "TOML has no null value"
            raise ConflictError(msg)
        return tomlkit.item(value)

    @staticmethod
    def _plain(value: object) -> JsonValue:
        """Return a parsed TOML value as plain Python data."""
        unwrap = getattr(value, "unwrap", None)
        return unwrap() if callable(unwrap) else value  # ty: ignore[invalid-return-type]
