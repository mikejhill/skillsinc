"""Keep the configuration's JSON Schema beside it, for editors.

The schema tells VS Code (and editors built on its JSON support) that the
file allows comments and trailing commas, so a ``config.json`` with comments
is not flagged, and it adds completion and validation for every key. A local
copy loads without network access or a trust prompt.
"""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Final

SCHEMA_FILE: Final = "config.schema.json"
LOCAL_REFERENCES: Final = frozenset({SCHEMA_FILE, f"./{SCHEMA_FILE}"})


class ConfigSchema:
    """The bundled schema and the copy kept beside a configuration file."""

    @staticmethod
    def text() -> str:
        """Return the schema bundled with this version of SkillSinc."""
        return files("skillsinc").joinpath(SCHEMA_FILE).read_text(encoding="utf-8")

    @classmethod
    def identifier(cls) -> str:
        """Return the schema's ``$id``, which marks a file as SkillSinc's schema."""
        return str(json.loads(cls.text())["$id"])

    @staticmethod
    def references_local(reference: str | None) -> bool:
        """Return True if ``$schema`` names the copy beside the configuration."""
        return reference is not None and reference.strip() in LOCAL_REFERENCES

    @classmethod
    def refresh(cls, config: Path) -> Path | None:
        """Write the bundled schema beside ``config``; return its path if written.

        A missing copy is written and an outdated SkillSinc copy replaced. A
        file of the same name that is not SkillSinc's schema is left alone.

        Raises:
            OSError: If the schema cannot be written.
        """
        target = config.parent / SCHEMA_FILE
        text = cls.text()
        if target.is_file():
            try:
                current = target.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                return None
            if current == text or not cls._ours(current):
                return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    @classmethod
    def _ours(cls, text: str) -> bool:
        """Return True if ``text`` is a SkillSinc schema, of any version."""
        try:
            data = json.loads(text)
        except ValueError:
            return False
        return isinstance(data, dict) and bool(data.get("$id") == cls.identifier())
