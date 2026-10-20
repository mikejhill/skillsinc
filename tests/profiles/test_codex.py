"""Test Codex instruction blocks and advisory path-scoping losses."""

from __future__ import annotations

import pytest
from tests.conftest import Builder

from skillsinc.models import Frontmatter, Kind
from skillsinc.paths import Environment
from skillsinc.profiles.codex import CodexProfile


class TestCodexProfile:
    """Codex accepts instruction bodies as managed AGENTS.md sections."""

    @pytest.mark.parametrize(
        ("header", "text", "loss_count"),
        [
            ({"applyTo": "**"}, "Body.", 0),
            ({}, "Body.", 1),
            (
                {"applyTo": ["*.py", "*.js"], "extra": True},
                "Applies when working with files matching `*.py`, `*.js`.\n\nBody.",
                2,
            ),
        ],
    )
    def test_instruction_scope_and_loss_reporting(
        self, header: Frontmatter, text: str, loss_count: int
    ) -> None:
        """Use advisory text for scoped instructions and report the semantic loss."""
        item = Builder.item(Kind.INSTRUCTION, frontmatter=header)

        result = CodexProfile().render(item)

        assert (result.blocks[0].path, result.blocks[0].text, len(result.losses)) == (
            "AGENTS.md",
            text,
            loss_count,
        ), f"Expected AGENTS section {text!r} with {loss_count} losses, got {result!r}"

    @pytest.mark.parametrize("kind", [Kind.SKILL, Kind.AGENT, Kind.PROMPT])
    def test_other_kinds_are_refused(self, kind: Kind) -> None:
        """Codex has no destination for these kinds under its home."""
        result = CodexProfile().render(Builder.item(kind))

        assert result.unsupported is not None, f"Expected kind refusal, got {result!r}"
        assert f"no location for {kind}" in result.unsupported, (
            f"Expected kind refusal, got {result!r}"
        )

    def test_codex_home_override_is_honored(self, environment: Environment) -> None:
        """Use an absolute CODEX_HOME instead of the default injected home."""
        custom = environment.home / "custom"
        overridden = Environment(
            environment.home, {"CODEX_HOME": str(custom)}, environment.platform
        )

        result = CodexProfile().default_path(overridden)

        assert result == custom, f"Expected {custom}, got {result}"
