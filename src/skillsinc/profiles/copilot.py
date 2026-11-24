"""GitHub Copilot profiles: Copilot CLI home and the VS Code user prompts folder."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Final, assert_never, override

from skillsinc.jsonc import Jsonc
from skillsinc.models import Customization, EntryOutput, Kind, Rendering, Support
from skillsinc.paths import Environment, absolute
from skillsinc.profiles.base import Profile

logger = logging.getLogger(__name__)

COPY_PATHS: Final = {
    Kind.AGENT: "agents/{name}.agent.md",
    Kind.RESOURCE: "resources/{name}.md",
    Kind.HOOK: "hooks/{name}.json",
}
SKILL_LOCATIONS: Final = "chat.agentSkillsLocations"
# The skills folders VS Code reads from other profiles' default directories
SKILL_FOLDERS: Final = {"claude": ".claude", "agents": ".agents", "copilot": ".copilot"}
PROJECT_VSCODE_REFUSAL: Final = (
    "in a project VS Code reads it from .github; route it to a copilot destination"
)
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
    project_folder: ClassVar[str] = ".github"
    # In a repository Copilot also reads .claude/skills and .agents/skills
    project_also_reads: ClassVar[Mapping[str, frozenset[Kind]] | None] = {
        "agents": frozenset({Kind.SKILL}),
        "claude": frozenset({Kind.SKILL}),
    }
    project_covered_by: ClassVar[Mapping[str, frozenset[Kind]] | None] = (
        project_also_reads
    )
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
    def user_path(self, environment: Environment) -> Path:
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
            case Kind.PROMPT if self.project is not None:
                # .github/prompts is the repository's prompt folder for VS Code
                rendering = self.prompt(item, f"prompts/{item.name}.prompt.md")
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
        path = "mcp-config.json" if self.project is None else "mcp.json"
        entry = EntryOutput(path, "mcpServers", item.name, server)
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
    project_folder: ClassVar[str] = ".vscode"
    # In a workspace VS Code reads .github instructions, agents, and prompts
    project_also_reads: ClassVar[Mapping[str, frozenset[Kind]] | None] = {
        "copilot": frozenset({Kind.SKILL, Kind.INSTRUCTION, Kind.AGENT, Kind.PROMPT}),
        "agents": frozenset({Kind.SKILL}),
        "claude": frozenset({Kind.SKILL}),
    }
    project_covered_by: ClassVar[Mapping[str, frozenset[Kind]] | None] = dict[
        str, frozenset[Kind]
    ]()

    @override
    def reads_with(self, environment: Environment) -> Mapping[str, frozenset[Kind]]:
        """Drop skill folders ``chat.agentSkillsLocations`` sets to ``false``.

        The folders are the other profiles' default directories at this
        scope: under the home directory, or under the project. User settings
        apply first, then a project's ``.vscode/settings.json``.
        Unreadable settings leave VS Code's defaults in place.
        """
        reads = self.reads()
        locations = self._locations(self.user_path(environment).parent, environment)
        if self.project is not None:
            locations.update(self._locations(self.project / ".vscode", environment))
        base = self.project if self.project is not None else environment.home
        off = {
            profile
            for profile, folder in SKILL_FOLDERS.items()
            if locations.get(self._key(base / folder / "skills", environment)) is False
        }
        return {
            profile: kinds - {Kind.SKILL} if profile in off else kinds
            for profile, kinds in reads.items()
        }

    def _locations(self, folder: Path, environment: Environment) -> dict[str, bool]:
        """Return ``chat.agentSkillsLocations`` from ``folder/settings.json``."""
        path = folder / "settings.json"
        try:
            text = path.read_text(encoding="utf-8-sig")
            bare = Jsonc.blank_trailing_commas(Jsonc.blank_comments(text))
            settings: object = json.loads(bare)
        except (OSError, ValueError) as err:
            if not isinstance(err, FileNotFoundError):
                logger.debug("Ignoring unreadable VS Code settings %s: %s", path, err)
            return {}
        value = settings.get(SKILL_LOCATIONS) if isinstance(settings, dict) else None
        if not isinstance(value, dict):
            return {}
        base = self.project if self.project is not None else environment.home
        return {
            self._key(environment.expand(str(key), base), environment): enabled
            for key, enabled in value.items()
            if isinstance(enabled, bool)
        }

    @staticmethod
    def _key(path: Path, environment: Environment) -> str:
        """Return ``path`` normalized for comparison on this platform."""
        text = str(absolute(path)).rstrip("/\\")
        return text.lower() if environment.platform == "win32" else text

    @override
    def user_path(self, environment: Environment) -> Path:
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
        """Copy ``item`` verbatim with the suffix VS Code expects.

        In a project the destination is ``.vscode``, which holds only
        ``mcp.json``; VS Code reads the rest from ``.github``.
        """
        if self.project is not None:
            return self._workspace(item)
        return self._prompts_folder(item)

    def _prompts_folder(self, item: Customization) -> Rendering:
        """Write ``item`` into the user ``prompts`` folder."""
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

    def _workspace(self, item: Customization) -> Rendering:
        """Write a server into a project's ``.vscode/mcp.json``; refuse the rest."""
        if item.kind is Kind.MCP:
            return self.server_entry(item, "mcp.json", "servers")
        return self.refuse_kind(item, PROJECT_VSCODE_REFUSAL)
