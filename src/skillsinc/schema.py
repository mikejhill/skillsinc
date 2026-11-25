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
import sys
import tempfile
from collections.abc import Callable
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import ClassVar, Final

from skillsinc.entries import JsonDocument
from skillsinc.jsonc import Jsonc

SCHEMA_FILE: Final = "config.schema.json"
REFERENCE: Final = f"./{SCHEMA_FILE}"
SETTINGS_SCHEMA_FILE: Final = "settings.schema.json"
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
    """The bundled schema and the copy kept beside a configuration file.

    Subclasses name another bundled schema with ``FILE``; ``settings.json``
    keeps ``settings.schema.json`` beside it the same way.
    """

    FILE: ClassVar[str] = SCHEMA_FILE

    @classmethod
    def text(cls) -> str:
        """Return the schema bundled with this version of SkillSinc."""
        return files("skillsinc").joinpath(cls.FILE).read_text(encoding="utf-8")

    @classmethod
    def identifier(cls) -> str:
        """Return the schema's ``$id``, which marks a file as SkillSinc's schema."""
        return str(json.loads(cls.text())["$id"])

    @classmethod
    def path(cls, config: Path) -> Path:
        """Return where the schema copy for ``config`` lives."""
        return config.parent / cls.FILE

    @classmethod
    def local_reference(cls) -> str:
        """Return the ``$schema`` value that names the copy beside the file."""
        return f"./{cls.FILE}"

    @classmethod
    def references_local(cls, reference: str | None) -> bool:
        """Return True if ``$schema`` names the copy beside the configuration."""
        return reference is not None and reference.strip() in {
            cls.FILE,
            cls.local_reference(),
        }

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
        write_atomically(cls.path(config), cls.text())
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

    @classmethod
    def with_reference(cls, text: str) -> str | None:
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
        reference = cls.local_reference()
        member = f'"$schema": {json.dumps(reference)},'
        if line_start > span.open and not prefix.strip(" \t"):
            insertion = member + Jsonc.newline(text) + prefix
        else:
            insertion = member + " "
        updated = Jsonc.apply(text, [(first, first, insertion)])
        before = dict(JsonDocument.load(_plain(text)))
        after = dict(JsonDocument.load(_plain(updated)))
        if after != {"$schema": reference, **before}:  # pragma: no cover - guard
            msg = "inserting $schema would change other keys"
            raise ValueError(msg)
        return updated

    @classmethod
    def needs_reference(cls, config: Path) -> bool:
        """Return True if ``add_reference`` would edit ``config``.

        Raises:
            OSError: If the file cannot be read.
            ValueError: If the file is not UTF-8 or not a JSONC object.
        """
        text = config.read_bytes().decode("utf-8").removeprefix(BOM)
        return cls.with_reference(text) is not None

    @classmethod
    def add_reference(cls, config: Path) -> bool:
        """Insert ``"$schema": "./config.schema.json"`` as the file's first key.

        The file is replaced atomically, keeping its permissions (and, on
        Windows, its ACL) and any byte-order mark; a symbolic link is
        followed, so the link stays. Just before the replacement the file is
        checked again, so an edit saved meanwhile is never discarded.

        Returns:
            True if the key was added.

        Raises:
            OSError: If the file cannot be read or replaced.
            ValueError: If the file is not a JSONC object or UTF-8, or it
                changed while the edit was prepared.
        """
        target = config.resolve()
        before = target.stat()
        data = target.read_bytes()
        raw = data.decode("utf-8")
        bom = BOM if raw.startswith(BOM) else ""
        updated = cls.with_reference(raw.removeprefix(BOM))
        if updated is None:
            return False

        def unchanged() -> None:
            """Refuse if the file changed since it was read."""
            now = target.stat()
            same = (now.st_size, now.st_mtime_ns, now.st_ino) == (
                before.st_size,
                before.st_mtime_ns,
                before.st_ino,
            )
            if not same or target.read_bytes() != data:
                msg = f"{config} changed while $schema was being added; left as is"
                raise ValueError(msg)

        write_atomically(target, bom + updated, mode_from=before, check=unchanged)
        return True

    @classmethod
    def _ours(cls, text: str) -> bool:
        """Return True if ``text`` is a SkillSinc schema, of any version."""
        try:
            data = json.loads(text)
        except ValueError:
            return False
        return isinstance(data, dict) and bool(data.get("$id") == cls.identifier())


class SettingsSchema(ConfigSchema):
    """The bundled ``settings.json`` schema and the copy kept beside it."""

    FILE: ClassVar[str] = SETTINGS_SCHEMA_FILE


def _plain(text: str) -> bytes:
    """Return JSONC ``text`` as plain JSON bytes."""
    return Jsonc.blank_trailing_commas(Jsonc.blank_comments(text)).encode()


def write_atomically(
    target: Path,
    text: str,
    *,
    mode_from: os.stat_result | None = None,
    check: Callable[[], None] | None = None,
) -> None:
    """Write ``text`` to an exclusive sibling temporary file, then replace ``target``.

    ``mode_from`` keeps an existing file's permission bits; on Windows the
    replacement keeps the target's ACL and attributes too. ``check`` runs just
    before the replacement and may raise to abandon it.

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
            staging.chmod(stat.S_IMODE(mode_from.st_mode))
        else:
            staging.chmod(0o644 & ~_umask())
        if check is not None:
            check()
        if mode_from is not None and sys.platform == "win32":  # pragma: no cover
            _replace_keeping_security(staging, target)
        else:
            staging.replace(target)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise


def _replace_keeping_security(staging: Path, target: Path) -> None:  # pragma: no cover
    """Replace ``target`` with ``staging`` via ReplaceFileW, keeping its ACL.

    Raises:
        OSError: If Windows refuses the replacement.
    """
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415 - Windows only

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel32.ReplaceFileW(str(target), str(staging), None, 0, None, None):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        staging.replace(target)


def _umask() -> int:
    """Return the process umask without changing it for long."""
    mask = os.umask(0)
    os.umask(mask)
    return mask
