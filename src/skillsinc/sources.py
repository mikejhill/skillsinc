"""Read a library directory into customizations, failing loudly on ambiguity."""

from __future__ import annotations

import logging
import os
import re
import stat
from collections.abc import Callable
from dataclasses import replace
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Final

from skillsinc.config import SourceConfig
from skillsinc.entries import JsonDocument
from skillsinc.exceptions import FrontmatterError, SourceError
from skillsinc.frontmatter import FrontmatterCodec
from skillsinc.models import (
    Asset,
    Customization,
    Document,
    Frontmatter,
    JsonValue,
    Kind,
)

logger = logging.getLogger(__name__)

NAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SUFFIXES: Final = {
    Kind.INSTRUCTION: ".instructions.md",
    Kind.AGENT: ".agent.md",
    Kind.PROMPT: ".prompt.md",
}
JSON_LABELS: Final = {Kind.MCP: "MCP server definition", Kind.HOOK: "hook file"}

# Claude Code plugin layout (decision 0003)
PLUGIN_MANIFEST: Final = ".claude-plugin"
PLUGIN_SERVERS: Final = ".mcp.json"
PLUGIN_PARTS: Final = {
    Kind.SKILL: "skills",
    Kind.AGENT: "agents",
    Kind.PROMPT: "commands",
}
PLUGIN_EXPANDED: Final = frozenset(
    {PLUGIN_MANIFEST, PLUGIN_SERVERS, *PLUGIN_PARTS.values()}
)
PLUGIN_RELOCATING: Final = ("skills", "agents", "commands", "mcpServers")
PLUGIN_METADATA: Final = frozenset(
    {
        "$schema",
        "name",
        "displayName",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "metadata",
        "icon",
        "documentationUrl",
        "supportUrl",
        "privacyPolicyUrl",
        "termsOfServiceUrl",
        "defaultEnabled",
    }
)
PLUGIN_HIDDEN: Final = frozenset({".lsp.json"})
PLUGIN_VARIABLE: Final = re.compile(
    r"\$(?:\{CLAUDE_PLUGIN_\w+\}|CLAUDE_PLUGIN_\w+|\{user_config\.[^}]*\})"
)


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
            plugins = source.path / source.plugins
            if plugins.is_dir():
                items.extend(self._plugins(source, plugins))
        except (OSError, FrontmatterError) as err:
            msg = f"source '{source.name}': {err}"
            raise SourceError(msg) from err
        self._check_unique(source, items)
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

    def _plugins(self, source: SourceConfig, directory: Path) -> list[Customization]:
        """Expand each ``<plugin>/`` into skills, agents, prompts, and MCP servers."""
        items: list[Customization] = []
        for root in sorted(directory.iterdir()):
            if not root.is_dir() or self._excluded(source, root.name):
                continue
            plugin = self._name(root.name, root)
            unsynced = self._manifest(plugin, root)
            unsynced.extend(self._unsynced(source, root))
            expanded: list[Customization] = []
            for kind, part in PLUGIN_PARTS.items():
                folder = root / part
                if not folder.is_dir():
                    continue
                if kind is Kind.SKILL:
                    expanded.extend(self._skills(source, folder))
                else:
                    expanded.extend(self._documents(source, kind, folder))
                    unsynced.extend(
                        f"{part}/{path.name}/"
                        for path in sorted(folder.iterdir(), key=lambda path: path.name)
                        if path.is_dir() and not self._excluded(source, path.name)
                    )
            servers = root / PLUGIN_SERVERS
            if servers.is_file():
                expanded.extend(self._plugin_servers(source, servers))
            if not expanded:
                logger.warning("plugin '%s' has nothing SkillSinc can sync", plugin)
            losses: tuple[str, ...] = ()
            if unsynced:
                parts = ", ".join(unsynced)
                losses = (f"plugin '{plugin}' also has {parts}, which is not synced",)
            items.extend(
                replace(
                    item,
                    plugin=plugin,
                    losses=losses,
                    refusal=self._plugin_refusal(item),
                )
                for item in expanded
            )
        return items

    def _plugin_servers(self, source: SourceConfig, path: Path) -> list[Customization]:
        """Read a plugin's ``.mcp.json``: an ``mcpServers`` map, or the bare map."""
        try:
            loaded = JsonDocument.load(path.read_bytes())
        except (UnicodeDecodeError, ValueError) as err:
            msg = f"{path}: invalid MCP server file ({err})"
            raise SourceError(msg) from err
        servers: JsonValue = loaded.get("mcpServers", loaded)
        if not isinstance(servers, dict):
            msg = f"{path}: 'mcpServers' must be a JSON object"
            raise SourceError(msg)
        items: list[Customization] = []
        for key, server in sorted(servers.items()):
            name = self._name(key, path)
            if not isinstance(server, dict):
                msg = f"{path}: MCP server '{name}' must be a JSON object"
                raise SourceError(msg)
            self._check_server(server, path)
            raw = JsonDocument.dump(server)
            document = Document(frontmatter=server, body="", raw=raw)
            items.append(
                Customization(Kind.MCP, name, source.name, document, f"{name}.json")
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
        problem = None
        if "enabled" in server and not isinstance(server["enabled"], bool):
            problem = "'enabled' must be true or false"
        for key in ("startupTimeoutSec", "toolTimeoutSec"):
            value = server.get(key)
            if key in server and (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or value <= 0
            ):
                problem = f"'{key}' must be a positive number of seconds"
        profiles = server.get("profiles")
        if "profiles" in server and not (
            isinstance(profiles, dict)
            and all(isinstance(value, dict) for value in profiles.values())
        ):
            problem = "'profiles' must map profile names to objects"
        if problem is not None:
            msg = f"{path}: {problem}"
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
    def _manifest(plugin: str, root: Path) -> list[str]:
        """Check the optional manifest; return the keys SkillSinc does not sync.

        Raises:
            SourceError: If the manifest is invalid, names another plugin, or
                moves components out of their standard folders.
        """
        path = root / PLUGIN_MANIFEST / "plugin.json"
        if not path.is_file():
            return []
        try:
            manifest = JsonDocument.load(path.read_bytes())
        except (UnicodeDecodeError, ValueError) as err:
            msg = f"{path}: invalid plugin manifest ({err})"
            raise SourceError(msg) from err
        declared = manifest.get("name")
        if declared is not None and declared != plugin:
            msg = (
                f"{path}: manifest name '{declared}' differs from"
                f" directory name '{plugin}'"
            )
            raise SourceError(msg)
        relocated = [key for key in PLUGIN_RELOCATING if key in manifest]
        if relocated:
            msg = (
                f"{path}: SkillSinc reads only the standard plugin layout;"
                f" move {', '.join(relocated)} into their default folders"
            )
            raise SourceError(msg)
        return [f"manifest key {key}" for key in manifest if key not in PLUGIN_METADATA]

    def _unsynced(self, source: SourceConfig, root: Path) -> list[str]:
        """Name the plugin's top-level parts that no kind carries."""
        found: list[str] = []
        for path in sorted(root.iterdir(), key=lambda path: path.name):
            name = path.name
            if name in PLUGIN_EXPANDED:
                continue
            if self._excluded(source, name) and name not in PLUGIN_HIDDEN:
                continue
            if path.is_dir():
                found.append(f"{name}/")
            elif name == "SKILL.md" or not (
                name.endswith(".md") or name.startswith("LICENSE")
            ):
                found.append(name)
        return found

    @staticmethod
    def _plugin_refusal(item: Customization) -> str | None:
        """Refuse items whose files rely on variables only an installed plugin resolves.

        Skill assets are scanned too: a script copied out of the plugin would
        find nothing at ``${CLAUDE_PLUGIN_ROOT}``.
        """
        contents = [item.document.raw, *(asset.content for asset in item.assets)]
        found = sorted(
            {
                match.group(0)
                for content in contents
                for match in PLUGIN_VARIABLE.finditer(
                    content.decode("utf-8", errors="replace")
                )
            }
        )
        if not found:
            return None
        return f"refers to {', '.join(found)}, which only an installed plugin resolves"

    @staticmethod
    def _check_unique(source: SourceConfig, items: list[Customization]) -> None:
        """Fail when two items, from the library or its plugins, share an identity."""
        seen: dict[str, Customization] = {}
        for item in items:
            previous = seen.setdefault(item.selector, item)
            if previous is item:
                continue
            origins = sorted(
                f"plugin '{found.plugin}'"
                if found.plugin is not None
                else "the library"
                for found in (previous, item)
            )
            msg = (
                f"source '{source.name}': {item.selector} is defined by"
                f" {' and '.join(origins)}"
            )
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
