"""Test Codex instruction blocks, MCP server tables, and their losses."""

from __future__ import annotations

import pytest
from tests.conftest import Builder

from skillsinc.models import EntryOutput, Frontmatter, JsonValue, Kind
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


class TestCodexServers:
    """MCP servers become ``[mcp_servers.<name>]`` tables with only Codex fields."""

    @pytest.mark.parametrize(
        ("server", "table"),
        [
            (
                {
                    "type": "local",
                    "command": "npx",
                    "args": ["-y", "pkg"],
                    "env": {"K": "v"},
                    "cwd": "/w",
                },
                {
                    "command": "npx",
                    "args": ["-y", "pkg"],
                    "env": {"K": "v"},
                    "cwd": "/w",
                },
            ),
            (
                {"type": "http", "url": "https://x", "headers": {"A": "b"}},
                {"url": "https://x", "http_headers": {"A": "b"}},
            ),
            ({"command": "gh", "tools": ["*"]}, {"command": "gh"}),
            (
                {"command": "gh", "tools": ["read", "list"]},
                {"command": "gh", "enabled_tools": ["read", "list"]},
            ),
        ],
    )
    def test_servers_convert_exactly(
        self, server: dict[str, JsonValue], table: dict[str, JsonValue]
    ) -> None:
        """Carried fields keep their values under Codex's names, with no losses."""
        item = Builder.item(Kind.MCP, name="github", frontmatter=server)

        result = CodexProfile().render(item)

        assert result.entries == (
            EntryOutput("config.toml", "mcp_servers", "github", table),
        ), f"Expected Codex table {table!r}, got {result!r}"
        assert result.losses == (), f"Expected no losses, got {result.losses!r}"

    @pytest.mark.parametrize(
        ("server", "loss"),
        [
            (
                {"command": "gh", "timeout": 5},
                "drops fields Codex does not read: timeout",
            ),
            ({"command": "gh", "headers": {"A": "b"}}, "does not read: headers"),
            ({"url": "https://x", "env": {"K": "v"}}, "does not read: env"),
            ({"command": "gh", "tools": "all"}, "does not read: tools"),
            ({"command": "gh", "env": {"T": "${TOKEN}"}}, "${...} variables in env"),
        ],
    )
    def test_unread_fields_and_variables_are_losses(
        self, server: dict[str, JsonValue], loss: str
    ) -> None:
        """Fields Codex would reject are dropped and reported, never written."""
        item = Builder.item(Kind.MCP, frontmatter=server)

        result = CodexProfile().render(item)

        assert any(loss in found for found in result.losses), (
            f"Expected loss {loss!r}, got {result.losses!r}"
        )
        written = result.entries[0].value
        assert isinstance(written, dict), f"Expected a table, got {written!r}"
        assert set(written) <= {
            "command",
            "args",
            "env",
            "cwd",
            "url",
            "http_headers",
        }, f"Expected only Codex fields, got {written!r}"

    @pytest.mark.parametrize(
        ("server", "reason"),
        [
            ({"type": "sse", "url": "https://x"}, "not SSE"),
            ({"type": "http", "command": "gh"}, "no MCP server type 'http'"),
            ({"type": "ws", "url": "https://x"}, "no MCP server type 'ws'"),
            ({"command": "gh", "type": []}, "'type' must be a string"),
            ({"command": 3}, "'command' must be a string"),
            ({"command": "gh", "args": ["a", 1]}, "'args' must be a list of strings"),
            (
                {"url": "https://x", "headers": {"A": None}},
                "'headers' must be an object",
            ),
        ],
    )
    def test_servers_codex_would_reject_are_refused(
        self, server: dict[str, JsonValue], reason: str
    ) -> None:
        """Transports and field types Codex cannot load are refused with a reason."""
        item = Builder.item(Kind.MCP, frontmatter=server)

        result = CodexProfile().render(item)

        assert result.unsupported is not None, f"Expected refusal, got {result!r}"
        assert reason in result.unsupported, f"Expected {reason!r}, got {result!r}"
