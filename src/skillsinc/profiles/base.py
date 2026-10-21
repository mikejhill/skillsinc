"""Common behaviour for harness profiles."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Final

from skillsinc.frontmatter import FrontmatterCodec
from skillsinc.models import (
    Customization,
    EntryOutput,
    FileOutput,
    Frontmatter,
    FrontmatterValue,
    JsonValue,
    Kind,
    LossPolicy,
    Rendering,
    Support,
)
from skillsinc.paths import Environment

VSCODE_VARIABLE: Final = re.compile(r"\$\{[A-Za-z]+(?::[^}]*)?\}")
GLOBAL_GLOBS: Final = frozenset({"**", "**/*", "*"})
CLAUDE_ARGUMENTS: Final = re.compile(r"\$(?:ARGUMENTS\b|\d)")
CLAUDE_SHELL: Final = re.compile(r"!`[^`]*`")
PLUGIN_AGENT_POLICY: Final = LossPolicy(carried=frozenset({"name", "description"}))
PLUGIN_COMMAND_POLICY: Final = LossPolicy(
    carried=frozenset({"description", "argument-hint"})
)
SERVER_FIELDS: Final = frozenset({"type", "command", "args", "env", "url", "headers"})
LOCAL_TYPES: Final = frozenset({"stdio", "local"})
REMOTE_TYPES: Final = frozenset({"http", "sse"})


class Profile(ABC):
    """How one harness discovers customizations under a destination directory."""

    name: ClassVar[str]
    summary: ClassVar[str]
    support: ClassVar[Mapping[Kind, Support]]
    outside: ClassVar[frozenset[str]] = frozenset[str]()
    """Files beside the root (``../<name>``) this profile may write entries into."""
    harness: ClassVar[str] = ""
    """The application that reads this destination, or empty for shared folders."""
    also_reads: ClassVar[Mapping[str, frozenset[Kind]]] = dict[str, frozenset[Kind]]()
    """Kinds the harness also loads from other profiles' default directories."""

    def __init__(self) -> None:
        self._codec = FrontmatterCodec()

    @abstractmethod
    def default_path(self, environment: Environment) -> Path | None:
        """Return the harness's user-level directory, or None if it has none."""

    @abstractmethod
    def render(self, item: Customization) -> Rendering:
        """Translate ``item`` into this harness's native files."""

    def locate(self, path: str, root: Path, environment: Environment) -> str:
        """Return where an entry file named ``path`` lives relative to ``root``."""
        del root, environment
        return path

    # Shared conversions

    @staticmethod
    def copy_skill(item: Customization, directory: str = "skills") -> Rendering:
        """Copy a skill package verbatim into ``directory/<name>/``."""
        root = f"{directory}/{item.name}"
        files = [FileOutput(f"{root}/SKILL.md", item.document.raw)]
        files.extend(
            FileOutput(f"{root}/{asset.path}", asset.content, asset.executable)
            for asset in item.assets
        )
        return Rendering(files=tuple(files))

    @staticmethod
    def copy_file(item: Customization, path: str) -> Rendering:
        """Copy a single-file customization verbatim to ``path``."""
        return Rendering(files=(FileOutput(path, item.document.raw),))

    def rewrite(
        self,
        item: Customization,
        path: str,
        frontmatter: dict[str, FrontmatterValue],
        losses: list[str],
    ) -> Rendering:
        """Build a one-file rendering with rewritten frontmatter."""
        content = self._codec.render(frontmatter, item.document.body)
        return Rendering(files=(FileOutput(path, content),), losses=tuple(losses))

    def agent(self, item: Customization, path: str) -> Rendering:
        """Copy a library agent; convert a Claude Code plugin agent to ``path``.

        A plugin agent keeps only its name and description.
        """
        if item.plugin is None:
            return self.copy_file(item, path)
        source = item.document.frontmatter
        frontmatter: dict[str, FrontmatterValue] = {
            key: source[key] for key in ("name", "description") if key in source
        }
        losses = PLUGIN_AGENT_POLICY.losses(source)
        return self.rewrite(item, path, frontmatter, losses)

    def prompt(self, item: Customization, path: str) -> Rendering:
        """Copy a library prompt; convert a Claude Code plugin command to ``path``."""
        if item.plugin is None:
            return self.copy_file(item, path)
        source = item.document.frontmatter
        frontmatter: dict[str, FrontmatterValue] = {
            key: source[key]
            for key in ("description", "argument-hint")
            if key in source
        }
        losses = PLUGIN_COMMAND_POLICY.losses(source)
        found = sorted(set(CLAUDE_ARGUMENTS.findall(item.document.body)))
        if found:
            losses.append(f"uses Claude Code argument placeholders: {', '.join(found)}")
        if CLAUDE_SHELL.search(item.document.body):
            losses.append(
                "runs shell commands with !`...`, which only Claude Code executes"
            )
        return self.rewrite(item, path, frontmatter, losses)

    @staticmethod
    def apply_to(frontmatter: Frontmatter) -> list[str] | None:
        """Return the ``applyTo`` globs, or None when the item applies everywhere."""
        value = frontmatter.get("applyTo")
        if value is None:
            return None
        raw = value if isinstance(value, list) else str(value).split(",")
        globs = [str(glob).strip() for glob in raw if str(glob).strip()]
        if not globs or set(globs) <= GLOBAL_GLOBS:
            return None
        return globs

    @staticmethod
    def variable_losses(body: str) -> list[str]:
        """Report VS Code ``${...}`` variables, which other harnesses do not expand."""
        found = sorted(set(VSCODE_VARIABLE.findall(body)))
        if not found:
            return []
        return [f"uses VS Code variables other harnesses ignore: {', '.join(found)}"]

    @staticmethod
    def server_entry(item: Customization, path: str, container: str) -> Rendering:
        """Convert an MCP server into an entry with an explicit transport ``type``.

        Fields outside the common ``mcpServers`` shape are dropped and reported;
        a ``url`` server without a ``type`` is written as ``http`` and reported,
        because it could equally be ``sse``.
        """
        source = item.document.frontmatter
        transport = source.get("type")
        if transport is not None and not isinstance(transport, str):
            return Rendering.refuse("type must be a string")
        losses: list[str] = []
        if "command" in source:
            if transport is not None and transport not in LOCAL_TYPES:
                return Rendering.refuse(
                    f"type '{transport}' does not fit a server with a command"
                )
            transport = "stdio"
        elif transport is None:
            transport = "http"
            losses.append("has no type, so it is written as an http server")
        elif transport not in REMOTE_TYPES:
            return Rendering.refuse(
                f"type '{transport}' is not a remote transport (http or sse)"
            )
        server: dict[str, JsonValue] = {"type": transport}
        server.update(
            (key, value)
            for key, value in source.items()
            if key in SERVER_FIELDS and key != "type"
        )
        dropped = [key for key in source if key not in SERVER_FIELDS]
        if dropped:
            losses.append(f"drops server fields: {', '.join(dropped)}")
        entry = EntryOutput(path, container, item.name, server)
        return Rendering(entries=(entry,), losses=tuple(losses))

    @classmethod
    def refuse_kind(cls, item: Customization, hint: str = "") -> Rendering:
        """Return the standard refusal for a kind this profile cannot hold."""
        reason = f"the {cls.name} profile has no location for {item.kind}"
        return Rendering.refuse(f"{reason}; {hint}" if hint else reason)
