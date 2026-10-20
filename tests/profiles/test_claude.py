"""Test Claude conversion fidelity and explicit loss reporting."""

from __future__ import annotations

import pytest
from tests.conftest import Builder

from skillsinc.frontmatter import FrontmatterCodec
from skillsinc.models import Frontmatter, Kind
from skillsinc.paths import Environment
from skillsinc.profiles.claude import ClaudeProfile


class TestClaudeProfile:
    """Rules, agents, and commands preserve supported fields only."""

    @pytest.mark.parametrize(
        ("header", "expected", "losses"),
        [
            ({"applyTo": "**/*.py, **/*.js"}, {"paths": ["**/*.py", "**/*.js"]}, ()),
            ({"applyTo": "**"}, {}, ()),
            ({}, {}, ("has no applyTo",)),
            ({"applyTo": "*", "unknown": 1}, {}, ("drops frontmatter: unknown",)),
        ],
    )
    def test_rule_globs_and_losses(
        self, header: Frontmatter, expected: Frontmatter, losses: tuple[str, ...]
    ) -> None:
        """Translate applyTo into paths and report omitted semantics."""
        item = Builder.item(Kind.INSTRUCTION, frontmatter=header)

        result = ClaudeProfile().render(item)

        document = FrontmatterCodec().parse(result.files[0].content)
        assert (result.files[0].path, document.frontmatter, document.body) == (
            "rules/sample.md",
            expected,
            "Body.\n",
        ), f"Expected rule conversion {expected!r}, got {result!r}"
        assert len(result.losses) == len(losses), (
            f"Expected loss prefixes {losses!r}, got {result.losses!r}"
        )
        assert all(
            (
                actual.startswith(prefix)
                for actual, prefix in zip(result.losses, losses, strict=True)
            )
        ), f"Expected loss prefixes {losses!r}, got {result.losses!r}"

    def test_agent_drops_foreign_tools_and_model(self) -> None:
        """Avoid treating Copilot tool and model values as Claude-native values."""
        item = Builder.item(
            Kind.AGENT,
            frontmatter={
                "description": "Review",
                "tools": ["read"],
                "model": "foreign",
            },
        )

        result = ClaudeProfile().render(item)

        header = FrontmatterCodec().parse(result.files[0].content).frontmatter
        assert header == {"name": "sample", "description": "Review"}, (
            f"Expected only Claude name and description, got {header!r}"
        )
        assert result.losses == ("drops frontmatter: tools, model",), (
            f"Expected foreign field losses, got {result.losses!r}"
        )

    @pytest.mark.parametrize("description", [None, ""])
    def test_agent_without_description_is_refused(
        self, description: str | None
    ) -> None:
        """Require a description for discoverable Claude subagents."""
        item = Builder.item(Kind.AGENT, frontmatter={"description": description})

        result = ClaudeProfile().render(item)

        assert result.unsupported == "Claude subagents require a description", (
            f"Expected description refusal, got {result!r}"
        )

    def test_command_preserves_supported_fields_and_reports_variables(self) -> None:
        """Carry command metadata and flag variables that Claude cannot expand."""
        item = Builder.item(
            Kind.PROMPT,
            frontmatter={
                "name": "ignored",
                "description": "Ask",
                "argument-hint": "question",
                "tools": ["read"],
            },
            body="${input:x} ${file} ${input:x}",
        )

        result = ClaudeProfile().render(item)

        header = FrontmatterCodec().parse(result.files[0].content).frontmatter
        assert header == {"description": "Ask", "argument-hint": "question"}, (
            f"Expected command metadata, got {header!r}"
        )
        assert result.losses == (
            "drops frontmatter: tools",
            "uses VS Code variables other harnesses ignore: ${file}, ${input:x}",
        ), f"Expected field and variable losses, got {result.losses!r}"

    def test_default_path_uses_fake_home(self, environment: Environment) -> None:
        """Default Claude paths stay within the injected home."""
        result = ClaudeProfile().default_path(environment)

        assert result == environment.home / ".claude", (
            f"Expected fake Claude root, got {result}"
        )

    @pytest.mark.parametrize(
        ("kind", "path"),
        [(Kind.AGENT, "agents/sample.md"), (Kind.PROMPT, "commands/sample.md")],
    )
    def test_plugin_components_are_copied_verbatim(self, kind: Kind, path: str) -> None:
        """Plugin agents and commands are already in Claude Code's format."""
        header: Frontmatter = {"description": "Run", "tools": "Read", "model": "x"}
        item = Builder.item(kind, frontmatter=header, plugin="kit")

        result = ClaudeProfile().render(item)

        assert [(output.path, output.content) for output in result.files] == [
            (path, item.document.raw)
        ], f"Expected exact copy at {path}, got {result!r}"
        assert result.losses == (), f"Expected no losses, got {result.losses!r}"
