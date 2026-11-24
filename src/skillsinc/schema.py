"""Keep the configuration's JSON Schema beside it, for editors.

The schema tells VS Code (and editors built on its JSON support) that the
file allows comments and trailing commas, so a ``config.json`` with comments
is not flagged, and it adds completion and validation for every key. A local
copy loads without network access or a trust prompt.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Final

from skillsinc.entries import JsonDocument
from skillsinc.jsonc import Jsonc

SCHEMA_FILE: Final = "config.schema.json"
REFERENCE: Final = f"./{SCHEMA_FILE}"
LOCAL_REFERENCES: Final = frozenset({SCHEMA_FILE, REFERENCE})
BOM: Final = "﻿"
# A schema copy larger than this is not SkillSinc's; never read it whole
MAXIMUM_SCHEMA_BYTES: Final = 1 << 20


class SchemaState(StrEnum):
    """What sits at ``config.schema.json`` beside a configuration."""

    MISSING = "missing"
    CURRENT = "current"
    OUTDATED = "outdated"
    """An older (or newer) SkillSinc schema; this version replaces it."""
    FOREIGN = "foreign"
    """Anything SkillSinc does not own: another file, a link, a directory."""


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
    def path(config: Path) -> Path:
        """Return where the schema copy for ``config`` lives."""
        return config.parent / SCHEMA_FILE

    @staticmethod
    def references_local(reference: str | None) -> bool:
        """Return True if ``$schema`` names the copy beside the configuration."""
        return reference is not None and reference.strip() in LOCAL_REFERENCES

    @classmethod
    def state(cls, config: Path) -> SchemaState:
        """Classify the schema copy without following links or opening specials."""
        target = cls.path(config)
        try:
            entry = target.lstat()
        except FileNotFoundError:
            return SchemaState.MISSING
        except OSError:
            return SchemaState.FOREIGN
        if not stat.S_ISREG(entry.st_mode) or entry.st_size > MAXIMUM_SCHEMA_BYTES:
            return SchemaState.FOREIGN
        try:
            current = target.read_bytes().decode("utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return SchemaState.FOREIGN
        if current == cls.text():
            return SchemaState.CURRENT
        return SchemaState.OUTDATED if cls._ours(current) else SchemaState.FOREIGN

    @classmethod
    def refresh(cls, config: Path) -> bool:
        """Write the bundled schema beside ``config``; return True if written.

        Only a missing entry or an outdated SkillSinc copy (a regular file) is
        written, atomically. Anything else is left alone.

        Raises:
            OSError: If the schema cannot be written.
        """
        if cls.state(config) not in {SchemaState.MISSING, SchemaState.OUTDATED}:
            return False
        _write_atomically(cls.path(config), cls.text())
        return True

    @classmethod
    def reference(cls, config: Path) -> str | None:
        """Return the file's ``$schema`` string, or None if absent or unreadable."""
        try:
            text = config.read_bytes().decode("utf-8").removeprefix(BOM)
            data = JsonDocument.load(_plain(text))
        except (OSError, ValueError):
            return None
        value = data.get("$schema")
        return value if isinstance(value, str) else None

    @staticmethod
    def with_reference(text: str) -> str | None:
        """Return JSONC ``text`` with ``$schema`` as its first key, else None.

        Only the inserted text differs: comments, line endings, indentation,
        and every other byte stay. None means the root object is empty or
        already has ``$schema``.

        Raises:
            ValueError: If ``text`` is not a JSONC object.
        """
        span = Jsonc.object_at(Jsonc.blank_comments(text), 0)
        if not span.members or span.find("$schema") is not None:
            return None
        first = span.members[0].start
        line_start = text.rfind("\n", 0, first) + 1
        prefix = text[line_start:first]
        member = f'"$schema": {json.dumps(REFERENCE)},'
        if line_start > span.open and not prefix.strip(" \t"):
            insertion = member + Jsonc.newline(text) + prefix
        else:
            insertion = member + " "
        updated = Jsonc.apply(text, [(first, first, insertion)])
        before = dict(JsonDocument.load(_plain(text)))
        after = dict(JsonDocument.load(_plain(updated)))
        if after != {"$schema": REFERENCE, **before}:  # pragma: no cover - guard
            msg = "inserting $schema would change other keys"
            raise ValueError(msg)
        return updated

    @classmethod
    def needs_reference(cls, config: Path) -> bool:
        """Return True if ``add_reference`` would edit ``config``."""
        try:
            text = config.read_bytes().decode("utf-8").removeprefix(BOM)
            return cls.with_reference(text) is not None
        except (OSError, ValueError):
            return False

    @classmethod
    def add_reference(cls, config: Path) -> bool:
        """Insert ``"$schema": "./config.schema.json"`` as the file's first key.

        The file is replaced atomically, keeping its permissions and any
        byte-order mark; a symbolic link is followed, so the link stays.

        Returns:
            True if the key was added.

        Raises:
            OSError: If the file cannot be read or replaced.
            ValueError: If the file is not a JSONC object or UTF-8.
        """
        target = config.resolve()
        raw = target.read_bytes().decode("utf-8")
        bom = BOM if raw.startswith(BOM) else ""
        updated = cls.with_reference(raw.removeprefix(BOM))
        if updated is None:
            return False
        _write_atomically(target, bom + updated, mode_from=target)
        return True

    @classmethod
    def _ours(cls, text: str) -> bool:
        """Return True if ``text`` is a SkillSinc schema, of any version."""
        try:
            data = json.loads(text)
        except ValueError:
            return False
        return isinstance(data, dict) and bool(data.get("$id") == cls.identifier())


def _plain(text: str) -> bytes:
    """Return JSONC ``text`` as plain JSON bytes."""
    return Jsonc.blank_trailing_commas(Jsonc.blank_comments(text)).encode()


def _write_atomically(target: Path, text: str, mode_from: Path | None = None) -> None:
    """Write ``text`` to a sibling temporary file, then replace ``target``.

    Raises:
        OSError: If the file cannot be written or replaced.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    staging = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        if mode_from is not None:
            staging.chmod(stat.S_IMODE(mode_from.stat().st_mode))
        else:
            staging.chmod(0o644 & ~_umask())
        staging.replace(target)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise


def _umask() -> int:
    """Return the process umask without changing it for long."""
    mask = os.umask(0)
    os.umask(mask)
    return mask
