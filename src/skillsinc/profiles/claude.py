"""Claude Code profile: skills, rules, subagents, slash commands, and imports."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import ClassVar, Final, override

from skillsinc.models import (
    BlockOutput,
    Customization,
    EntryOutput,
    FileOutput,
    FrontmatterValue,
    Kind,
    LossPolicy,
    Rendering,
    Support,
)
from skillsinc.paths import Environment
from skillsinc.profiles.base import Profile
from skillsinc.profiles.hooks import ClaudeHooks

CLAUDE_JSON: Final = ".claude.json"
PROJECT_MCP: Final = "../.mcp.json"
INSTRUCTION_POLICY: Final = LossPolicy(
    carried=frozenset({"applyTo"}), ignored=frozenset({"name", "description"})
)
# Subagent fields no other harness's agent format defines, copied as written
AGENT_NATIVE: Final = frozenset(
    {
        "permissionMode",
        "maxTurns",
        "autoCompactWindow",
        "skills",
        "memory",
        "background",
        "omitClaudeMd",
        "effort",
        "isolation",
        "color",
        "initialPrompt",
    }
)
AGENT_MODELS: Final = frozenset({"sonnet", "opus", "haiku", "fable", "inherit"})
AGENT_MODEL_ID: Final = re.compile(r"^claude-[a-z0-9][a-z0-9.-]*(?:\[1m\])?$")
# Claude Code tools are PascalCase (``Read``, ``Bash(git *)``) or ``mcp__server__tool``
AGENT_TOOL: Final = re.compile(r"^(?:mcp__\S+|[A-Z][A-Za-z0-9]*(?:\(.*\))?)$")
AGENT_NAME_LIMIT: Final = 256
PROMPT_POLICY: Final = LossPolicy(
    carried=frozenset({"description", "argument-hint"}), ignored=frozenset({"name"})
)


class ClaudeProfile(Profile):
    """Claude Code user directory (``~/.claude``)."""

    name: ClassVar[str] = "claude"
    harness: ClassVar[str] = "Claude Code"
    summary: ClassVar[str] = (
        "Claude Code: skills/, rules/, agents/, commands/, resources/ (CLAUDE.md),"
        " settings.json hooks, .claude.json"
    )
    support: ClassVar[Mapping[Kind, Support]] = {
        Kind.SKILL: Support.EXACT,
        Kind.INSTRUCTION: Support.CONVERTED,
        Kind.AGENT: Support.CONVERTED,
        Kind.PROMPT: Support.CONVERTED,
        Kind.MCP: Support.CONVERTED,
        Kind.HOOK: Support.CONVERTED,
        Kind.RESOURCE: Support.EXACT,
    }
    outside: ClassVar[frozenset[str]] = frozenset({"../.claude.json", PROJECT_MCP})
    project_folder: ClassVar[str] = ".claude"

    @override
    def user_path(self, environment: Environment) -> Path:
        """Return ``$CLAUDE_CONFIG_DIR`` or ``~/.claude``."""
        return environment.directory("CLAUDE_CONFIG_DIR", environment.home / ".claude")

    @override
    def render(self, item: Customization) -> Rendering:
        """Translate ``item`` into Claude Code's native layout."""
        if item.kind is Kind.MCP:
            return self.server_entry(item, CLAUDE_JSON, "mcpServers")
        converters: dict[Kind, Callable[[Customization], Rendering]] = {
            Kind.SKILL: self.copy_skill,
            Kind.INSTRUCTION: self._rule,
            Kind.AGENT: self._agent,
            Kind.PROMPT: self._command,
            Kind.RESOURCE: self._resource,
            Kind.HOOK: self._hooks,
        }
        return converters[item.kind](item)

    @override
    def locate(self, path: str, root: Path, environment: Environment) -> str:
        """Place ``.claude.json`` where Claude Code reads it for this root.

        Claude Code keeps it in ``$CLAUDE_CONFIG_DIR`` when that is set, and
        beside the default ``~/.claude`` directory (``~/.claude.json``) otherwise.
        A project's servers go in ``.mcp.json`` at the project root.
        """
        if path != CLAUDE_JSON:
            return path
        if self.project is not None:
            return PROJECT_MCP
        configured = environment.directory("CLAUDE_CONFIG_DIR", Path())
        if configured.is_absolute() or root != environment.home / ".claude":
            return path
        return f"../{CLAUDE_JSON}"

    # Conversions

    def _rule(self, item: Customization) -> Rendering:
        """Convert an instructions file into ``rules/<name>.md`` with ``paths``."""
        source = item.document.frontmatter
        losses = INSTRUCTION_POLICY.losses(source)
        globs = self.apply_to(source)
        if "applyTo" not in source:
            losses.append(
                "has no applyTo, so Claude loads it in every session"
                " rather than on demand"
            )
        frontmatter: dict[str, FrontmatterValue] = {}
        if globs is not None:
            frontmatter["paths"] = list[FrontmatterValue](globs)
        return self.rewrite(item, f"rules/{item.name}.md", frontmatter, losses)

    def _agent(self, item: Customization) -> Rendering:
        """Convert an agent into a ``agents/<name>.md`` subagent.

        The declared ``name`` is kept when Claude Code accepts it, since
        subagents are identified by it rather than by file name. ``tools``,
        ``disallowedTools``, and ``model`` are carried only when their values
        are Claude Code's own (VS Code agents use other tool and model names);
        fields only Claude Code defines are copied. Plugin agents are already
        Claude Code subagents, so they are copied.
        """
        source = item.document.frontmatter
        declared = source.get("name")
        name = declared if self._agent_name(declared) else item.name
        identity = f"Claude Code subagent name {name!r}"
        if item.plugin is not None:
            copied = self.copy_file(item, f"agents/{item.name}.md")
            return replace(copied, identity=identity)
        description = source.get("description")
        if description is None or description == "":
            return Rendering.refuse("Claude subagents require a description")
        frontmatter: dict[str, FrontmatterValue] = {
            "name": name,
            "description": description,
        }
        frontmatter.update(
            (key, value)
            for key, value in source.items()
            if key in AGENT_NATIVE or self._claude_value(key, value)
        )
        dropped = [key for key in source if key not in {"name", *frontmatter}]
        losses = [f"drops frontmatter: {', '.join(dropped)}"] if dropped else []
        rendering = self.rewrite(item, f"agents/{item.name}.md", frontmatter, losses)
        return replace(rendering, identity=identity)

    @staticmethod
    def _agent_name(name: FrontmatterValue) -> bool:
        """Return True if Claude Code loads a subagent with this ``name``."""
        return (
            isinstance(name, str)
            and bool(name.strip())
            and len(name) <= AGENT_NAME_LIMIT
            and ":" not in name
            and not name.startswith("-")
        )

    @staticmethod
    def _claude_value(key: str, value: FrontmatterValue) -> bool:
        """Return True if ``value`` is a Claude Code ``model`` or tool list."""
        if key == "model":
            return isinstance(value, str) and (
                value in AGENT_MODELS or bool(AGENT_MODEL_ID.match(value))
            )
        if key not in {"tools", "disallowedTools"}:
            return False
        if isinstance(value, str):
            tools: list[FrontmatterValue] = [part.strip() for part in value.split(",")]
        elif isinstance(value, list):
            tools = value
        else:
            return False
        return bool(tools) and all(
            isinstance(tool, str) and AGENT_TOOL.match(tool) for tool in tools
        )

    def _command(self, item: Customization) -> Rendering:
        """Convert a prompt file into a ``commands/<name>.md`` slash command.

        Plugin commands are already Claude Code commands, so they are copied.
        """
        if item.plugin is not None:
            return self.copy_file(item, f"commands/{item.name}.md")
        source = item.document.frontmatter
        frontmatter: dict[str, FrontmatterValue] = {
            key: source[key]
            for key in ("description", "argument-hint")
            if key in source
        }
        losses = PROMPT_POLICY.losses(source)
        losses.extend(self.variable_losses(item.document.body))
        return self.rewrite(item, f"commands/{item.name}.md", frontmatter, losses)

    @staticmethod
    def _hooks(item: Customization) -> Rendering:
        """Convert a Copilot hook file into ``settings.json`` hook groups per event."""
        if item.document.frontmatter.get("disableAllHooks") is True:
            return Rendering.refuse("sets disableAllHooks, so none of its hooks run")
        hooks = item.document.frontmatter.get("hooks")
        converted = ClaudeHooks.convert(hooks if isinstance(hooks, dict) else {})
        if not converted.events:
            return Rendering.refuse("has no hooks Claude Code can run")
        entries = tuple(
            EntryOutput("settings.json", "hooks", event, groups, merge=True)
            for event, groups in sorted(converted.events.items())
        )
        return Rendering(entries=entries, losses=tuple(converted.losses))

    @staticmethod
    def _resource(item: Customization) -> Rendering:
        """Copy a resource to ``resources/`` and import it from ``CLAUDE.md``.

        Claude Code resolves ``@`` imports relative to the importing file, so the
        managed block in ``CLAUDE.md`` loads the copy in every session.
        """
        path = f"resources/{item.name}.md"
        return Rendering(
            files=(FileOutput(path, item.document.raw),),
            blocks=(BlockOutput("CLAUDE.md", f"@{path}"),),
        )
