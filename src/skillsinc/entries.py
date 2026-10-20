"""Read, edit, and write keyed entries inside shared JSON and TOML config files."""

from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from collections.abc import MutableMapping
from decimal import Decimal
from typing import cast, override

import tomlkit
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import InlineTable, Item, Table

from skillsinc.exceptions import ConflictError
from skillsinc.models import Digest, JsonValue


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
        """Return the editor for ``path``'s format: TOML for ``.toml``, else JSON.

        Raises:
            ConflictError: If the file cannot be parsed safely.
        """
        if path.endswith(".toml"):
            return TomlEntryFile(raw)
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
        values = self._container(container, create=False)
        values.pop(key, None)
        if prune and not values:
            self._document.pop(container, None)
            if not self._document:
                self.raw = None
                return None
        self.raw = JsonDocument.dump(self._document)
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
        if not isinstance(value, dict):
            msg = f"'{container}.{key}' must be a table"
            raise ConflictError(msg)
        values = self._container(container)
        expected = self._document.unwrap()
        expected.setdefault(container, {})[key] = value
        if values is None:
            if self._document:
                self._document.add(tomlkit.nl())
            values = tomlkit.table(is_super_table=True)
            self._document[container] = values
        values[key] = self._item(value, inline=isinstance(values, InlineTable))
        return self._dump(expected)

    @override
    def remove(self, container: str, key: str, *, prune: bool) -> bytes | None:
        """Remove ``container.key`` and return the new bytes, or None if now empty.

        An emptied container is always dropped, since TOML cannot tell an empty
        table from a missing one; only ``prune`` reports an emptied file as None.

        Raises:
            ConflictError: If the edit does not read back as intended.
        """
        values = self._container(container)
        if values is None or key not in values:
            return self.raw
        expected = self._document.unwrap()
        del expected[container][key]
        del values[key]
        # An empty table header means nothing in TOML, so drop it either way
        if not values:
            del expected[container]
            del self._document[container]
            if prune and not expected:
                self.raw = None
                return None
        return self._dump(expected)

    # Internals

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
