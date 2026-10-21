"""Test Codex instruction blocks, TOML agents, MCP server tables, and their losses."""

from __future__ import annotations

import tomllib

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

    def test_agent_becomes_custom_agent_toml(self) -> None:
        """Write name, description, and body as developer_instructions."""
        body = 'Review "diffs" with C:\\paths.\n\n\tThen report.\n'
        item = Builder.item(
            Kind.AGENT,
            name="reviewer",
            frontmatter={"name": "reviewer", "description": "Review: code"},
            body=body,
        )

        result = CodexProfile().render(item)

        (output,) = result.files
        parsed = tomllib.loads(output.content.decode())
        assert (output.path, parsed, result.losses) == (
            "agents/reviewer.toml",
            {
                "name": "reviewer",
                "description": "Review: code",
                "developer_instructions": body.strip(),
            },
            (),
        ), f"Expected a lossless reviewer.toml, got {result!r}"

    @pytest.mark.parametrize(
        "body",
        [
            'One line with \\ and "quotes"',
            'Triple """ quotes\n"""',
            'Trailing quote\n"',
            "Windows\r\nline\r\r\nends\r and a lone return",
            "Tab\tbell\a null\x00 delete\x7f escape\x1b",
            "Unicode é — 漢字 🙂\nsecond\u2028line",
        ],
    )
    def test_agent_instructions_round_trip(self, body: str) -> None:
        """Escape every character TOML forbids; fold CRLF line ends to LF."""
        item = Builder.item(Kind.AGENT, body=body)

        result = CodexProfile().render(item)

        parsed = tomllib.loads(result.files[0].content.decode())
        expected = body.replace("\r\r\n", "\n").replace("\r\n", "\n").strip()
        assert parsed["developer_instructions"] == expected, (
            f"Expected {expected!r}, got {parsed['developer_instructions']!r}"
        )

    def test_agent_name_becomes_snake_case(self) -> None:
        """Lowercase the name and replace other characters with underscores."""
        item = Builder.item(Kind.AGENT, name="Code-Review.v2")

        result = CodexProfile().render(item)

        (output,) = result.files
        parsed = tomllib.loads(output.content.decode())
        assert (output.path, parsed["name"]) == (
            "agents/code_review_v2.toml",
            "code_review_v2",
        ), f"Expected snake_case file and name, got {output.path}, {parsed!r}"

    def test_agent_reports_dropped_fields(self) -> None:
        """Report tools, model, and VS Code variables Codex cannot honour."""
        item = Builder.item(
            Kind.AGENT,
            frontmatter={"description": "Sample", "tools": ["read"], "model": "x"},
            body="Open ${file}.\n",
        )

        result = CodexProfile().render(item)

        assert result.losses == (
            "drops frontmatter: tools, model",
            "uses VS Code variables other harnesses ignore: ${file}",
        ), f"Expected dropped fields and variables, got {result.losses!r}"

    @pytest.mark.parametrize(
        ("header", "body", "reason"),
        [
            ({}, "Body.\n", "description"),
            ({"description": "  "}, "Body.\n", "description"),
            ({"description": ["a"]}, "Body.\n", "description"),
            ({"description": "Sample"}, "\n  \n", "instructions"),
        ],
    )
    def test_agent_without_required_keys_is_refused(
        self, header: Frontmatter, body: str, reason: str
    ) -> None:
        """Codex needs a description and developer_instructions in every agent."""
        item = Builder.item(Kind.AGENT, frontmatter=header, body=body)

        result = CodexProfile().render(item)

        assert result.unsupported is not None, f"Expected refusal, got {result!r}"
        assert reason in result.unsupported, f"Expected {reason!r}, got {result!r}"

    @pytest.mark.parametrize("kind", [Kind.SKILL, Kind.PROMPT])
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


class TestCodexServerOptions:
    """Harness-neutral server options map to Codex keys; overrides pass through."""

    def test_enabled_timeouts_and_overrides_become_codex_keys(self) -> None:
        """One library entry carries everything Codex's own table held."""
        server: dict[str, JsonValue] = {
            "command": "cronometer",
            "enabled": False,
            "startupTimeoutSec": 60,
            "toolTimeoutSec": 30.5,
            "profiles": {
                "codex": {"default_tools_approval_mode": "prompt"},
                "claude": {"ignored": True},
            },
        }
        item = Builder.item(Kind.MCP, name="cronometer", frontmatter=server)

        result = CodexProfile().render(item)

        assert result.entries == (
            EntryOutput(
                "config.toml",
                "mcp_servers",
                "cronometer",
                {
                    "command": "cronometer",
                    "enabled": False,
                    "startup_timeout_sec": 60,
                    "tool_timeout_sec": 30.5,
                    "default_tools_approval_mode": "prompt",
                },
            ),
        ), f"Expected mapped options, got {result!r}"
        assert result.losses == (), f"Expected no losses, got {result.losses!r}"

    def test_enabled_true_writes_nothing(self) -> None:
        """Codex enables servers by default, so ``enabled: true`` adds no key."""
        item = Builder.item(Kind.MCP, frontmatter={"command": "a", "enabled": True})

        result = CodexProfile().render(item)

        assert result.entries[0].value == {"command": "a"}, f"Got {result!r}"

    def test_null_override_is_refused(self) -> None:
        """TOML has no null, so an override holding one cannot be written."""
        server: dict[str, JsonValue] = {
            "command": "a",
            "profiles": {"codex": {"tools": {"x": [None]}}},
        }
        item = Builder.item(Kind.MCP, frontmatter=server)

        result = CodexProfile().render(item)

        assert result.unsupported is not None, f"Expected refusal, got {result!r}"
        assert "null" in result.unsupported, f"Got {result.unsupported!r}"
