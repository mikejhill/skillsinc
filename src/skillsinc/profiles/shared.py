"""Harness-neutral profiles: the shared ``~/.agents`` skills home and plain copies."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, override

from skillsinc.models import Customization, Kind, Rendering, Support
from skillsinc.paths import Environment
from skillsinc.profiles.base import Profile


class AgentsProfile(Profile):
    """Shared Agent Skills home (``~/.agents``) read by Codex, Copilot, and others."""

    name: ClassVar[str] = "agents"
    summary: ClassVar[str] = "Shared Agent Skills home: skills/"
    support: ClassVar[Mapping[Kind, Support]] = {
        Kind.SKILL: Support.EXACT,
        Kind.INSTRUCTION: Support.UNSUPPORTED,
        Kind.AGENT: Support.UNSUPPORTED,
        Kind.PROMPT: Support.UNSUPPORTED,
    }

    @override
    def default_path(self, environment: Environment) -> Path:
        """Return ``~/.agents``."""
        return environment.home / ".agents"

    @override
    def render(self, item: Customization) -> Rendering:
        """Copy skills; refuse every other kind."""
        if item.kind is Kind.SKILL:
            return self.copy_skill(item)
        return self.refuse_kind(item)


class GenericProfile(Profile):
    """Any directory: copies the library layout verbatim, with no conversion."""

    name: ClassVar[str] = "generic"
    summary: ClassVar[str] = "Verbatim copy of the library layout (path required)"
    support: ClassVar[Mapping[Kind, Support]] = dict.fromkeys(Kind, Support.EXACT)

    @override
    def default_path(self, environment: Environment) -> None:
        """Return None: a generic destination always needs an explicit path."""
        return

    @override
    def render(self, item: Customization) -> Rendering:
        """Copy ``item`` to ``<kind>/`` under its original file name."""
        if item.kind is Kind.SKILL:
            return self.copy_skill(item)
        return self.copy_file(item, f"{item.kind}/{item.file_name}")
