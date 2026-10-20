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
VSCODE_SUFFIXES: Final = {
    Kind.INSTRUCTION: ".instructions.md",
    Kind.AGENT: ".agent.md",
    Kind.PROMPT: ".prompt.md",
}
VSCODE_REFUSALS: Final = {
    Kind.SKILL: "VS Code reads skills from copilot or agents destinations",
    Kind.MCP: (
        "VS Code keeps user MCP servers in User/mcp.json, outside the"
        " prompts folder, which SkillSinc does not edit yet"
    ),
    Kind.RESOURCE: "route resources to a claude or copilot destination",
    Kind.HOOK: (
        "VS Code reads user hooks from Copilot CLI and Claude Code"
        " locations; route hooks to a copilot or claude destination"
    ),
}


class CopilotProfile(Profile):
    """Copilot CLI user directory (``~/.copilot``), also read by VS Code for skills."""

    name: ClassVar[str] = "copilot"
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
                return self.copy_skill(item)
            case Kind.INSTRUCTION:
                return self._instruction(item)
            case Kind.AGENT | Kind.RESOURCE | Kind.HOOK:
                path = COPY_PATHS[item.kind].format(name=item.name)
                return self.copy_file(item, path)
            case Kind.PROMPT:
                return self.refuse_kind(item, "route prompts to a vscode destination")
            case Kind.MCP:
                server = dict(item.document.frontmatter)
                entry = EntryOutput("mcp-config.json", "mcpServers", item.name, server)
                return Rendering(entries=(entry,))
        assert_never(item.kind)

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
    summary: ClassVar[str] = (
        "VS Code user prompts folder: *.instructions/agent/prompt.md"
    )
    support: ClassVar[Mapping[Kind, Support]] = {
        Kind.SKILL: Support.UNSUPPORTED,
        Kind.INSTRUCTION: Support.EXACT,
        Kind.AGENT: Support.EXACT,
        Kind.PROMPT: Support.EXACT,
        Kind.MCP: Support.UNSUPPORTED,
        Kind.HOOK: Support.UNSUPPORTED,
        Kind.RESOURCE: Support.UNSUPPORTED,
    }

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
        suffix = VSCODE_SUFFIXES.get(item.kind)
        if suffix is None:
            return self.refuse_kind(item, VSCODE_REFUSALS[item.kind])
        return self.copy_file(item, f"{item.name}{suffix}")
