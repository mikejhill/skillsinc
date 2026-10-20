"""OpenAI Codex profile: global instructions in a managed block of ``AGENTS.md``."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Final, override

from skillsinc.models import (
    BlockOutput,
    Customization,
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


class CodexProfile(Profile):
    """Codex home directory (``~/.codex``)."""

    name: ClassVar[str] = "codex"
    summary: ClassVar[str] = "Codex: instructions merged into AGENTS.md"
    support: ClassVar[Mapping[Kind, Support]] = {
        Kind.SKILL: Support.UNSUPPORTED,
        Kind.INSTRUCTION: Support.CONVERTED,
        Kind.AGENT: Support.UNSUPPORTED,
        Kind.PROMPT: Support.UNSUPPORTED,
        Kind.MCP: Support.UNSUPPORTED,
    }

    @override
    def default_path(self, environment: Environment) -> Path:
        """Return ``$CODEX_HOME`` or ``~/.codex``."""
        return environment.directory("CODEX_HOME", environment.home / ".codex")

    @override
    def render(self, item: Customization) -> Rendering:
        """Merge instructions into ``AGENTS.md``; refuse every other kind."""
        if item.kind is Kind.SKILL:
            return self.refuse_kind(
                item, "Codex reads user skills from an agents destination (~/.agents)"
            )
        if item.kind is Kind.MCP:
            return self.refuse_kind(
                item,
                "Codex reads MCP servers from config.toml, which SkillSinc does"
                " not edit yet",
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
