"""Claude Code profile: skills, rules, subagents, slash commands, and imports."""

from __future__ import annotations

from collections.abc import Callable, Mapping
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
INSTRUCTION_POLICY: Final = LossPolicy(
    carried=frozenset({"applyTo"}), ignored=frozenset({"name", "description"})
)
AGENT_POLICY: Final = LossPolicy(carried=frozenset({"name", "description"}))
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
    outside: ClassVar[frozenset[str]] = frozenset({"../.claude.json"})

    @override
    def default_path(self, environment: Environment) -> Path:
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
        """
        if path != CLAUDE_JSON:
            return path
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

        Plugin agents are already Claude Code subagents, so they are copied.
        """
        if item.plugin is not None:
            return self.copy_file(item, f"agents/{item.name}.md")
        source = item.document.frontmatter
        description = source.get("description")
        if description is None or description == "":
            return Rendering.refuse("Claude subagents require a description")
        frontmatter: dict[str, FrontmatterValue] = {
            "name": item.name,
            "description": description,
        }
        losses = AGENT_POLICY.losses(source)
        return self.rewrite(item, f"agents/{item.name}.md", frontmatter, losses)

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
