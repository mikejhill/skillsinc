"""Read, edit, and write keyed entries inside a shared JSON configuration file."""

from __future__ import annotations

import json

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
            raw.decode("utf-8-sig"), object_pairs_hook=JsonDocument._pairs
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
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return Digest.of(canonical.encode())

    # Internals

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


class EntryFile:
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

    def digest(self, container: str, key: str) -> str | None:
        """Return the digest of the value at ``container.key``, or None if absent.

        Raises:
            ConflictError: If ``container`` exists but is not an object.
        """
        values = self._container(container, create=False)
        if key not in values:
            return None
        return JsonDocument.digest(values[key])

    def set(self, container: str, key: str, value: JsonValue) -> bytes:
        """Set ``container.key`` and return the new file bytes."""
        self._container(container, create=True)[key] = value
        self.raw = JsonDocument.dump(self._document)
        return self.raw

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
        values = self._document.get(container)
        if values is None:
            values = {}
            if create:
                self._document[container] = values
        if not isinstance(values, dict):
            msg = f"'{container}' is not a JSON object; fix it by hand"
            raise ConflictError(msg)
        return values
