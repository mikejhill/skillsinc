"""Read a library directory into customizations, failing loudly on ambiguity."""

from __future__ import annotations

import logging
import os
import re
import stat
from collections.abc import Callable
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Final

from skillsinc.config import SourceConfig
from skillsinc.entries import JsonDocument
from skillsinc.exceptions import FrontmatterError, SourceError
from skillsinc.frontmatter import FrontmatterCodec
from skillsinc.models import Asset, Customization, Document, Frontmatter, Kind

logger = logging.getLogger(__name__)

NAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SUFFIXES: Final = {
    Kind.INSTRUCTION: ".instructions.md",
    Kind.AGENT: ".agent.md",
    Kind.PROMPT: ".prompt.md",
}
JSON_LABELS: Final = {Kind.MCP: "MCP server definition", Kind.HOOK: "hook file"}


class SourceReader:
    """Read one library directory completely, or raise ``SourceError``."""

    def __init__(self, codec: FrontmatterCodec | None = None) -> None:
        self._codec = codec or FrontmatterCodec()

    def read(self, source: SourceConfig) -> tuple[Customization, ...]:
        """Return every customization in ``source``, sorted by selector.

        A missing kind directory means "none of that kind". A missing root or
        any unreadable item fails the whole source, because a partial read
        would look like deletions.

        Raises:
            SourceError: If the library cannot be read completely and unambiguously.
        """
        if not source.path.is_dir():
            msg = f"source '{source.name}': directory not found: {source.path}"
            raise SourceError(msg)

        items: list[Customization] = []
        try:
            for kind in Kind:
                directory = source.path / source.layout[kind]
                if not directory.is_dir():
                    continue
                if kind is Kind.SKILL:
                    items.extend(self._skills(source, directory))
                elif kind is Kind.MCP:
                    items.extend(
                        self._json_items(source, kind, directory, self._check_server)
                    )
                elif kind is Kind.HOOK:
                    items.extend(
                        self._json_items(source, kind, directory, self._check_hooks)
                    )
                elif kind is Kind.RESOURCE:
                    items.extend(self._resources(source, directory))
                else:
                    items.extend(self._documents(source, kind, directory))
        except (OSError, FrontmatterError) as err:
            msg = f"source '{source.name}': {err}"
            raise SourceError(msg) from err
        logger.debug("Read %d items from source %s", len(items), source.name)
        return tuple(sorted(items, key=lambda item: item.selector))

    # Kinds

    def _skills(self, source: SourceConfig, directory: Path) -> list[Customization]:
        """Read every ``<name>/SKILL.md`` package under ``directory``."""
        items: list[Customization] = []
        for package in sorted(directory.iterdir()):
            if not package.is_dir() or self._excluded(source, package.name):
                continue
            name = self._name(package.name, package)
            entry = package / "SKILL.md"
            if not entry.is_file():
                msg = f"skill directory has no SKILL.md: {package}"
                raise SourceError(msg)
            document = self._parse(entry)
            self._check_skill(name, document.frontmatter, entry)
            assets = tuple(self._assets(source, package))
            items.append(
                Customization(
                    Kind.SKILL, name, source.name, document, "SKILL.md", assets
                )
            )
        return items

    def _documents(
        self, source: SourceConfig, kind: Kind, directory: Path
    ) -> list[Customization]:
        """Read single-file customizations (``<name><suffix>`` or ``<name>.md``)."""
        items: dict[str, Customization] = {}
        for path in sorted(directory.iterdir()):
            if not path.is_file() or self._excluded(source, path.name):
                continue
            stem = self._stem(path.name, SUFFIXES[kind])
            if stem is None:
                continue
            name = self._name(stem, path)
            if name in items:
                msg = f"two {kind} files define '{name}' in {directory}"
                raise SourceError(msg)
            document = self._parse(path)
            items[name] = Customization(kind, name, source.name, document, path.name)
        return list(items.values())

    def _json_items(
        self,
        source: SourceConfig,
        kind: Kind,
        directory: Path,
        check: Callable[[Frontmatter, Path], None],
    ) -> list[Customization]:
        """Read JSON definitions (``<name>.json``, one item per file)."""
        items: list[Customization] = []
        for path in sorted(directory.iterdir()):
            if (
                not path.is_file()
                or self._excluded(source, path.name)
                or path.suffix != ".json"
            ):
                continue
            name = self._name(path.stem, path)
            raw = path.read_bytes()
            try:
                value = JsonDocument.load(raw)
            except (UnicodeDecodeError, ValueError) as err:
                msg = f"{path}: invalid {JSON_LABELS[kind]} ({err})"
                raise SourceError(msg) from err
            check(value, path)
            document = Document(frontmatter=value, body="", raw=raw)
            items.append(Customization(kind, name, source.name, document, path.name))
        return items

    def _resources(self, source: SourceConfig, directory: Path) -> list[Customization]:
        """Read reference files (``<name>.md``), kept byte for byte."""
        items: list[Customization] = []
        for path in sorted(directory.iterdir()):
            if (
                not path.is_file()
                or self._excluded(source, path.name)
                or path.suffix != ".md"
            ):
                continue
            name = self._name(path.stem, path)
            document = Document(frontmatter={}, body="", raw=path.read_bytes())
            items.append(
                Customization(Kind.RESOURCE, name, source.name, document, path.name)
            )
        return items

    # Internals

    def _parse(self, path: Path) -> Document:
        """Parse a Markdown file."""
        try:
            return self._codec.parse(path.read_bytes())
        except FrontmatterError as err:
            msg = f"{path}: {err}"
            raise FrontmatterError(msg) from err

    def _assets(self, source: SourceConfig, package: Path) -> list[Asset]:
        """Collect a skill's supporting files, skipping excluded names."""
        assets: list[Asset] = []
        for root, directories, files in os.walk(package):
            directories[:] = sorted(
                name for name in directories if not self._excluded(source, name)
            )
            for file_name in sorted(files):
                path = Path(root) / file_name
                relative = path.relative_to(package).as_posix()
                if relative == "SKILL.md" or self._excluded(source, file_name):
                    continue
                mode = path.stat().st_mode
                executable = bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
                assets.append(Asset(relative, path.read_bytes(), executable))
        return assets

    @staticmethod
    def _check_skill(name: str, frontmatter: Frontmatter, entry: Path) -> None:
        """Enforce the Agent Skills rules harnesses rely on for discovery."""
        declared = frontmatter.get("name")
        if declared is not None and declared != name:
            msg = (
                f"{entry}: frontmatter name '{declared}' differs from"
                f" directory name '{name}'"
            )
            raise SourceError(msg)
        description = frontmatter.get("description")
        if not isinstance(description, str) or not description.strip():
            msg = f"{entry}: skills need a description so harnesses can discover them"
            raise SourceError(msg)

    @staticmethod
    def _check_server(server: Frontmatter, path: Path) -> None:
        """Require exactly one launch method: a ``command`` or a ``url``."""
        methods = [
            key
            for key in ("command", "url")
            if isinstance(server.get(key), str) and str(server[key]).strip()
        ]
        if len(methods) != 1:
            msg = f"{path}: an MCP server needs either a 'command' or a 'url' string"
            raise SourceError(msg)

    @staticmethod
    def _check_hooks(hooks: Frontmatter, path: Path) -> None:
        """Require a Copilot CLI hook file: version 1 and event arrays of objects."""
        version = hooks.get("version")
        events = hooks.get("hooks")
        if isinstance(version, bool) or version != 1 or not isinstance(events, dict):
            msg = f'{path}: a hook file needs "version": 1 and a "hooks" object'
            raise SourceError(msg)
        for event, entries in events.items():
            if not isinstance(entries, list) or not all(
                isinstance(entry, dict) for entry in entries
            ):
                msg = f"{path}: hook event '{event}' must be an array of objects"
                raise SourceError(msg)

    @staticmethod
    def _excluded(source: SourceConfig, name: str) -> bool:
        """Return True if ``name`` matches one of the source's exclude patterns."""
        return any(fnmatchcase(name, pattern) for pattern in source.exclude)

    @staticmethod
    def _stem(file_name: str, suffix: str) -> str | None:
        """Return the item name for a file, or None if it is not Markdown."""
        if file_name.endswith(suffix):
            return file_name.removesuffix(suffix)
        if file_name.endswith(".md"):
            return file_name.removesuffix(".md")
        return None

    @staticmethod
    def _name(name: str, path: Path) -> str:
        """Validate an item name so it is safe to use as a path segment."""
        if not NAME_PATTERN.match(name):
            msg = f"invalid customization name '{name}': {path}"
            raise SourceError(msg)
        return name
