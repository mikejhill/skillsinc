"""GitHub Copilot profiles: Copilot CLI home and the VS Code user prompts folder."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Final, assert_never, override

from skillsinc.models import Customization, EntryOutput, Kind, Rendering, Support
from skillsinc.paths import Environment
from skillsinc.profiles.base import Profile

COPY_PATHS: Final = {
    Kind.AGENT: "agents/{name}.agent.md",
    Kind.RESOURCE: "resources/{name}.md",
    Kind.HOOK: "hooks/{name}.json",
}
VSCODE_REFUSALS: Final = {
    Kind.SKILL: "VS Code reads skills from copilot or agents destinations",
    Kind.RESOURCE: "route resources to a claude or copilot destination",
    Kind.HOOK: (
        "VS Code reads user hooks from Copilot CLI and Claude Code"
        " locations; route hooks to a copilot or claude destination"
    ),
}


class CopilotProfile(Profile):
    """Copilot CLI user directory (``~/.copilot``), also read by VS Code for skills."""

    name: ClassVar[str] = "copilot"
    harness: ClassVar[str] = "Copilot CLI"
    also_reads: ClassVar[Mapping[str, frozenset[Kind]]] = {
        "agents": frozenset({Kind.SKILL}),
    }
    covered_by: ClassVar[Mapping[str, frozenset[Kind]]] = also_reads
    summary: ClassVar[str] = (
        "Copilot CLI: skills/, instructions/, agents/, resources/, hooks/,"
        " mcp-config.json"
    )
    support: ClassVar[Mapping[Kind, Support]] = {
        Kind.SKILL: Support.EXACT,
        Kind.INSTRUCTION: Support.EXACT,
        Kind.AGENT: Support.EXACT,
        Kind.PROMPT: Support.UNSUPPORTED,
        Kind.MCP: Support.EXACT,
        Kind.HOOK: Support.EXACT,
        Kind.RESOURCE: Support.EXACT,
    }

    @override
    def default_path(self, environment: Environment) -> Path:
        """Return ``$COPILOT_HOME`` or ``~/.copilot``."""
        return environment.directory("COPILOT_HOME", environment.home / ".copilot")

    @override
    def render(self, item: Customization) -> Rendering:
        """Copy ``item`` into Copilot CLI's native layout."""
        match item.kind:
            case Kind.SKILL:
                rendering = self.copy_skill(item)
            case Kind.INSTRUCTION:
                rendering = self._instruction(item)
            case Kind.AGENT | Kind.RESOURCE:
                rendering = self.agent(
                    item, COPY_PATHS[item.kind].format(name=item.name)
                )
            case Kind.PROMPT:
                rendering = self.refuse_kind(
                    item, "route prompts to a vscode destination"
                )
            case Kind.MCP:
                rendering = self._server(item)
            case Kind.HOOK:
                rendering = self.copy_file(
                    item, COPY_PATHS[item.kind].format(name=item.name)
                )
            case _:
                return assert_never(item.kind)
        return rendering

    def _server(self, item: Customization) -> Rendering:
        """Copy a server's fields as written, then apply ``profiles.copilot``."""
        parts = self.server_parts(item)
        if isinstance(parts, Rendering):
            return parts
        server = {**parts.fields, **parts.overrides}
        entry = EntryOutput("mcp-config.json", "mcpServers", item.name, server)
        return Rendering(entries=(entry,), losses=tuple(parts.losses))

    def _instruction(self, item: Customization) -> Rendering:
        """Copy an instructions file, noting that Copilot CLI needs ``applyTo``."""
        rendering = self.copy_file(item, f"instructions/{item.name}.instructions.md")
        if "applyTo" in item.document.frontmatter:
            return rendering
        loss = "has no applyTo, which Copilot CLI needs to apply the file"
        return Rendering(files=rendering.files, losses=(loss,))


class VSCodeProfile(Profile):
    """VS Code user prompts folder, holding prompt, instruction, and agent files."""

    name: ClassVar[str] = "vscode"
    harness: ClassVar[str] = "VS Code"
    also_reads: ClassVar[Mapping[str, frozenset[Kind]]] = {
        "copilot": frozenset({Kind.SKILL, Kind.INSTRUCTION, Kind.AGENT}),
        "agents": frozenset({Kind.SKILL}),
        "claude": frozenset({Kind.SKILL}),
    }
    covered_by: ClassVar[Mapping[str, frozenset[Kind]]] = {
        "copilot": frozenset({Kind.INSTRUCTION, Kind.AGENT}),
    }
    summary: ClassVar[str] = (
        "VS Code user prompts folder: *.instructions/agent/prompt.md, ../mcp.json"
    )
    support: ClassVar[Mapping[Kind, Support]] = {
        Kind.SKILL: Support.UNSUPPORTED,
        Kind.INSTRUCTION: Support.EXACT,
        Kind.AGENT: Support.EXACT,
        Kind.PROMPT: Support.EXACT,
        Kind.MCP: Support.CONVERTED,
        Kind.HOOK: Support.UNSUPPORTED,
        Kind.RESOURCE: Support.UNSUPPORTED,
    }
    outside: ClassVar[frozenset[str]] = frozenset({"../mcp.json"})

    @override
    def default_path(self, environment: Environment) -> Path:
        """Return the stable VS Code ``User/prompts`` folder for this platform."""
        if environment.platform == "win32":
            fallback = environment.home / "AppData" / "Roaming"
            base = environment.directory("APPDATA", fallback)
        elif environment.platform == "darwin":
            base = environment.home / "Library" / "Application Support"
        else:
            base = environment.config_home
        return base / "Code" / "User" / "prompts"

    @override
    def render(self, item: Customization) -> Rendering:
        """Copy ``item`` verbatim with the suffix VS Code expects."""
        match item.kind:
            case Kind.INSTRUCTION:
                return self.copy_file(item, f"{item.name}.instructions.md")
            case Kind.AGENT:
                return self.agent(item, f"{item.name}.agent.md")
            case Kind.PROMPT:
                return self.prompt(item, f"{item.name}.prompt.md")
            case Kind.MCP:
                return self.server_entry(item, "../mcp.json", "servers")
            case Kind.SKILL | Kind.HOOK | Kind.RESOURCE:
                return self.refuse_kind(item, VSCODE_REFUSALS[item.kind])
        return assert_never(item.kind)
