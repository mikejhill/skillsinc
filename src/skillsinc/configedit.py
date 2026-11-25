"""Edit ``config.json`` section by section, keeping comments elsewhere.

The settings window edits sources, destinations, profiles, and routes as plain
JSON values. Saving splices only what changed into the original text: a
changed source replaces that source's value, a removed one deletes its
member, and changed routes replace the ``routes`` array. Comments outside the
edited values stay. A candidate is validated by the real loader before it is
written, and a file edited on disk since it was read is never overwritten.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Final

from skillsinc.config import Config, ConfigurationError
from skillsinc.entries import JsonDocument
from skillsinc.jsonc import Jsonc
from skillsinc.models import JsonValue
from skillsinc.schema import BOM, write_atomically

# The keyed sections, in the order the window shows them
SECTIONS: Final = ("sources", "destinations", "profiles")
ROUTES: Final = "routes"

type Table = dict[str, JsonValue]
type Validator = Callable[[Path, str], Config]


class ConfigEditError(ConfigurationError):
    """The configuration cannot be read, validated, or saved."""


class ConfigDocument:
    """One ``config.json``: its original text and the values being edited."""

    def __init__(self, path: Path, text: str) -> None:
        """Parse ``text``, the content of ``path``.

        Raises:
            ConfigEditError: If ``text`` is not a JSONC object.
        """
        self._path = path
        self._bom = BOM if text.startswith(BOM) else ""
        self._original = text.removeprefix(BOM)
        try:
            plain = Jsonc.blank_trailing_commas(Jsonc.blank_comments(self._original))
            data = JsonDocument.load(plain.encode())
        except ValueError as err:
            msg = f"{path}: invalid JSONC: {err}"
            raise ConfigEditError(msg) from err
        self._loaded: dict[str, JsonValue] = data
        self._data: dict[str, JsonValue] = copy.deepcopy(data)

    @classmethod
    def open(cls, path: Path) -> ConfigDocument:
        """Read ``path``.

        Raises:
            ConfigEditError: If it cannot be read or is not a JSONC object.
        """
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as err:
            msg = f"cannot read {path} ({err})"
            raise ConfigEditError(msg) from err
        return cls(path, text)

    @property
    def path(self) -> Path:
        """Return the configuration file."""
        return self._path

    @property
    def changed(self) -> bool:
        """Return True if any value differs from the file as read."""
        return self._data != self._loaded

    def section(self, name: str) -> dict[str, Table]:
        """Return a copy of a keyed section (``sources``, ``destinations``, …)."""
        raw = self._data.get(name)
        if not isinstance(raw, dict):
            return {}
        return {
            key: copy.deepcopy(value) if isinstance(value, dict) else {}
            for key, value in raw.items()
        }

    def put(self, name: str, key: str, table: Table, *, old_key: str = "") -> None:
        """Add or replace ``key`` in section ``name``; rename from ``old_key``.

        A rename also renames the entry in every route that names it.
        """
        current = self._data.get(name)
        entries: dict[str, JsonValue] = (
            dict(current) if isinstance(current, dict) else {}
        )
        if old_key and old_key != key and old_key in entries:
            entries = {
                (key if existing == old_key else existing): value
                for existing, value in entries.items()
            }
            self._rename_references(name, old_key, key)
        entries[key] = copy.deepcopy(table)
        self._data[name] = entries

    def remove(self, name: str, key: str) -> None:
        """Remove ``key`` from section ``name``."""
        current = self._data.get(name)
        if isinstance(current, dict) and key in current:
            entries = dict(current)
            del entries[key]
            self._data[name] = entries

    def routes(self) -> list[Table]:
        """Return a copy of the routes."""
        raw = self._data.get(ROUTES)
        if not isinstance(raw, list):
            return []
        return [copy.deepcopy(item) if isinstance(item, dict) else {} for item in raw]

    def set_routes(self, routes: list[Table]) -> None:
        """Replace every route."""
        self._data[ROUTES] = [copy.deepcopy(route) for route in routes]

    def render(self) -> str:
        """Return the original text with every change spliced in.

        Raises:
            ConfigEditError: If the original text cannot be edited in place.
        """
        text = self._original
        try:
            for name in SECTIONS:
                text = self._render_section(text, name)
            if self._data.get(ROUTES) != self._loaded.get(ROUTES):
                text = Jsonc.set_path(text, [ROUTES], self._data.get(ROUTES, []))
        except ValueError as err:
            msg = f"{self._path}: cannot edit in place ({err})"
            raise ConfigEditError(msg) from err
        return text

    def validate(self, loader: Validator) -> Config:
        """Return the configuration the edited text loads as.

        Raises:
            ConfigurationError: If it is invalid.
        """
        return loader(self._path, self.render())

    def save(self, loader: Validator) -> bool:
        """Validate and write the edits; False if nothing changed.

        Raises:
            ConfigurationError: If the edited configuration is invalid.
            ConfigEditError: If the file changed on disk since it was read.
            OSError: If it cannot be written.
        """
        if not self.changed:
            return False
        rendered = self.render()
        loader(self._path, rendered)
        original = (self._bom + self._original).encode("utf-8")
        before = self._path.stat()

        def unchanged() -> None:
            if self._path.read_bytes() != original:
                msg = f"{self._path} changed since it was read; reopen settings"
                raise ConfigEditError(msg)

        unchanged()
        write_atomically(
            self._path, self._bom + rendered, mode_from=before, check=unchanged
        )
        self._original = rendered
        self._loaded = copy.deepcopy(self._data)
        return True

    def _render_section(self, text: str, name: str) -> str:
        before = self._loaded.get(name)
        after = self._data.get(name)
        if before == after:
            return text
        if not isinstance(after, dict) or not isinstance(before, dict):
            return Jsonc.set_path(text, [name], after if after is not None else {})
        for key in before:
            if key not in after:
                text = Jsonc.delete_path(text, [name, key])
        for key, value in after.items():
            if before.get(key) != value:
                text = Jsonc.set_path(text, [name, key], value)
        return text

    def _rename_references(self, name: str, old: str, new: str) -> None:
        """Rename ``old`` to ``new`` in the route field named like the section."""
        routes = self._data.get(ROUTES)
        if not isinstance(routes, list):
            return
        for route in routes:
            names = route.get(name) if isinstance(route, dict) else None
            if isinstance(route, dict) and isinstance(names, list):
                route[name] = [new if item == old else item for item in names]


def describe(value: JsonValue) -> str:
    """Return ``value`` as compact JSON for a table cell."""
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
