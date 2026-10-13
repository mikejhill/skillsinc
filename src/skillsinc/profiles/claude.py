"""Claude Code profile: skills, rules, subagents, and slash commands."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Final, override

from skillsinc.models import (
    Customization,
    FileOutput,
    FrontmatterValue,
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
AGENT_POLICY: Final = LossPolicy(carried=frozenset({"name", "description"}))
PROMPT_POLICY: Final = LossPolicy(
    carried=frozenset({"description", "argument-hint"}), ignored=frozenset({"name"})
)


class ClaudeProfile(Profile):
    """Claude Code user directory (``~/.claude``)."""

    name: ClassVar[str] = "claude"
    summary: ClassVar[str] = "Claude Code: skills/, rules/, agents/, commands/"
    support: ClassVar[Mapping[Kind, Support]] = {
        Kind.SKILL: Support.EXACT,
        Kind.INSTRUCTION: Support.CONVERTED,
        Kind.AGENT: Support.CONVERTED,
        Kind.PROMPT: Support.CONVERTED,
    }

    @override
    def default_path(self, environment: Environment) -> Path:
        """Return ``$CLAUDE_CONFIG_DIR`` or ``~/.claude``."""
        return environment.directory("CLAUDE_CONFIG_DIR", environment.home / ".claude")

    @override
    def render(self, item: Customization) -> Rendering:
        """Translate ``item`` into Claude Code's native layout."""
        match item.kind:
            case Kind.SKILL:
                return self.copy_skill(item)
            case Kind.INSTRUCTION:
                return self._rule(item)
            case Kind.AGENT:
                return self._agent(item)
            case Kind.PROMPT:
                return self._command(item)

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
        return self._single(item, f"rules/{item.name}.md", frontmatter, losses)

    def _agent(self, item: Customization) -> Rendering:
        """Convert an agent into a ``agents/<name>.md`` subagent."""
        source = item.document.frontmatter
        description = source.get("description")
        if description is None or description == "":
            return Rendering.refuse("Claude subagents require a description")
        frontmatter: dict[str, FrontmatterValue] = {
            "name": item.name,
            "description": description,
        }
        losses = AGENT_POLICY.losses(source)
        return self._single(item, f"agents/{item.name}.md", frontmatter, losses)

    def _command(self, item: Customization) -> Rendering:
        """Convert a prompt file into a ``commands/<name>.md`` slash command."""
        source = item.document.frontmatter
        frontmatter: dict[str, FrontmatterValue] = {
            key: source[key]
            for key in ("description", "argument-hint")
            if key in source
        }
        losses = PROMPT_POLICY.losses(source)
        losses.extend(self.variable_losses(item.document.body))
        return self._single(item, f"commands/{item.name}.md", frontmatter, losses)

    def _single(
        self,
        item: Customization,
        path: str,
        frontmatter: dict[str, FrontmatterValue],
        losses: list[str],
    ) -> Rendering:
        """Build a one-file rendering with rewritten frontmatter."""
        content = self._codec.render(frontmatter, item.document.body)
        return Rendering(files=(FileOutput(path, content),), losses=tuple(losses))
