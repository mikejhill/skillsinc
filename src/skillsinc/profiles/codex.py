"""OpenAI Codex profile: ``AGENTS.md`` instructions and ``config.toml`` MCP servers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Final, override

from skillsinc.models import (
    BlockOutput,
    Customization,
    EntryOutput,
    JsonValue,
    Kind,
    LossPolicy,
    Rendering,
    Support,
)
from skillsinc.paths import Environment
from skillsinc.profiles.base import Profile

INSTRUCTION_POLICY: Final = LossPolicy(
    carried=frozenset({"applyTo"}), ignored=frozenset({"name", "description"})
)

# Library server fields Codex reads, per transport, and their Codex names
STDIO_FIELDS: Final = {"command": "command", "args": "args", "env": "env", "cwd": "cwd"}
HTTP_FIELDS: Final = {"url": "url", "headers": "http_headers"}
STDIO_TYPES: Final = frozenset({"stdio", "local"})
HTTP_TYPES: Final = frozenset({"http", "streamable-http", "streamable_http"})
SERVER_VARIABLE: Final = "${"


class CodexProfile(Profile):
    """Codex home directory (``~/.codex``)."""

    name: ClassVar[str] = "codex"
    summary: ClassVar[str] = (
        "Codex: instructions merged into AGENTS.md, config.toml mcp_servers"
    )
    support: ClassVar[Mapping[Kind, Support]] = {
        Kind.SKILL: Support.UNSUPPORTED,
        Kind.INSTRUCTION: Support.CONVERTED,
        Kind.AGENT: Support.UNSUPPORTED,
        Kind.PROMPT: Support.UNSUPPORTED,
        Kind.MCP: Support.CONVERTED,
        Kind.RESOURCE: Support.UNSUPPORTED,
    }

    @override
    def default_path(self, environment: Environment) -> Path:
        """Return ``$CODEX_HOME`` or ``~/.codex``."""
        return environment.directory("CODEX_HOME", environment.home / ".codex")

    @override
    def render(self, item: Customization) -> Rendering:
        """Merge instructions into ``AGENTS.md`` and servers into ``config.toml``."""
        if item.kind is Kind.SKILL:
            return self.refuse_kind(
                item, "Codex reads user skills from an agents destination (~/.agents)"
            )
        if item.kind is Kind.MCP:
            return self.server(item)
        if item.kind is Kind.RESOURCE:
            return self.refuse_kind(
                item, "route resources to a claude or copilot destination"
            )
        if item.kind is not Kind.INSTRUCTION:
            return self.refuse_kind(item)

        # Path scoping becomes advisory text
        frontmatter = item.document.frontmatter
        losses = INSTRUCTION_POLICY.losses(frontmatter)
        body = item.document.body.strip()
        globs = self.apply_to(frontmatter)
        if "applyTo" not in frontmatter:
            losses.append(
                "has no applyTo, so Codex loads it in every session"
                " rather than on demand"
            )
        if globs is not None:
            scope = ", ".join(f"`{glob}`" for glob in globs)
            body = f"Applies when working with files matching {scope}.\n\n{body}"
            losses.append("applyTo becomes advisory text; Codex loads it every session")
        return Rendering(blocks=(BlockOutput("AGENTS.md", body),), losses=tuple(losses))

    # MCP servers

    @classmethod
    def server(cls, item: Customization) -> Rendering:
        """Convert an MCP server into a ``[mcp_servers.<name>]`` table.

        Codex rejects unknown fields and fields of the other transport, so only
        fields it reads are written and every other field is reported as lost.
        """
        server = dict(item.document.frontmatter)
        transport = server.get("type")
        if transport is not None and not isinstance(transport, str):
            return Rendering.refuse("'type' must be a string for Codex")
        if transport == "sse":
            return Rendering.refuse(
                "Codex supports stdio and streamable HTTP MCP servers, not SSE"
            )
        fields = STDIO_FIELDS if "command" in server else HTTP_FIELDS
        types = STDIO_TYPES if "command" in server else HTTP_TYPES
        if transport is not None and transport not in types:
            return Rendering.refuse(f"Codex has no MCP server type '{transport}'")
        table: dict[str, JsonValue] = {}
        for name, target in fields.items():
            if name not in server:
                continue
            problem = cls._invalid(name, server[name])
            if problem is not None:
                return Rendering.refuse(problem)
            table[target] = server[name]
        tools = server.get("tools")
        tools_carried = cls._tools(tools, table)
        losses = cls._server_losses(server, fields, tools_carried=tools_carried)
        entry = EntryOutput("config.toml", "mcp_servers", item.name, table)
        return Rendering(entries=(entry,), losses=tuple(losses))

    @staticmethod
    def _invalid(name: str, value: JsonValue) -> str | None:
        """Return why a carried field has a type Codex would reject, if it does."""
        if name in {"command", "cwd", "url"}:
            valid = isinstance(value, str)
            shape = "a string"
        elif name == "args":
            valid = isinstance(value, list) and all(isinstance(v, str) for v in value)
            shape = "a list of strings"
        else:
            valid = isinstance(value, dict) and all(
                isinstance(v, str) for v in value.values()
            )
            shape = "an object of strings"
        return None if valid else f"'{name}' must be {shape} for Codex"

    @staticmethod
    def _tools(tools: JsonValue, table: dict[str, JsonValue]) -> bool:
        """Carry a ``tools`` allow-list as ``enabled_tools``; return True if handled.

        ``["*"]`` (all tools) is Codex's default, so it needs no field.
        """
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            return False
        if "*" not in tools:
            table["enabled_tools"] = tools
        return True

    @staticmethod
    def _server_losses(
        server: dict[str, JsonValue], fields: Mapping[str, str], *, tools_carried: bool
    ) -> list[str]:
        """Report dropped fields and ``${...}`` expansion Codex does not perform."""
        known = set(fields) | {"type"} | ({"tools"} if tools_carried else set())
        losses: list[str] = []
        dropped = [name for name in server if name not in known]
        if dropped:
            losses.append(f"drops fields Codex does not read: {', '.join(dropped)}")
        expanded = [
            name
            for name in fields
            if name in server and SERVER_VARIABLE in json.dumps(server[name])
        ]
        if expanded:
            losses.append(
                f"Codex does not expand ${{...}} variables in {', '.join(expanded)}"
            )
        return losses
