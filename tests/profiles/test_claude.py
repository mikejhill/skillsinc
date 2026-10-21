"""Test Claude conversion fidelity and explicit loss reporting."""

from __future__ import annotations

import pytest
from tests.conftest import Builder

from skillsinc.frontmatter import FrontmatterCodec
from skillsinc.models import (
    BlockOutput,
    EntryOutput,
    Frontmatter,
    JsonValue,
    Kind,
    Rendering,
)
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

    def test_resource_is_copied_and_imported_from_claude_md(self) -> None:
        """Copy the resource verbatim and import it through the CLAUDE.md block."""
        item = Builder.item(Kind.RESOURCE, name="style", frontmatter={})

        result = ClaudeProfile().render(item)

        assert [(output.path, output.content) for output in result.files] == [
            ("resources/style.md", item.document.raw)
        ], f"Expected verbatim resource copy, got {result!r}"
        assert result.blocks == (BlockOutput("CLAUDE.md", "@resources/style.md"),), (
            f"Expected one CLAUDE.md import, got {result!r}"
        )
        assert result.losses == (), f"Expected no losses, got {result!r}"

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

    @pytest.mark.parametrize(
        ("server", "expected", "losses"),
        [
            ({"command": "gh"}, {"type": "stdio", "command": "gh"}, ()),
            (
                {"type": "local", "command": "gh", "args": ["mcp"], "env": {"A": "1"}},
                {"type": "stdio", "command": "gh", "args": ["mcp"], "env": {"A": "1"}},
                (),
            ),
            (
                {"type": "sse", "url": "https://x", "headers": {"A": "1"}},
                {"type": "sse", "url": "https://x", "headers": {"A": "1"}},
                (),
            ),
            (
                {"url": "https://x"},
                {"type": "http", "url": "https://x"},
                ("has no type, so it is written as an http server",),
            ),
            (
                {"command": "gh", "tools": ["*"]},
                {"type": "stdio", "command": "gh"},
                ("drops server fields: tools",),
            ),
        ],
    )
    def test_mcp_server_becomes_claude_json_entry(
        self,
        server: dict[str, JsonValue],
        expected: dict[str, JsonValue],
        losses: tuple[str, ...],
    ) -> None:
        """Servers get an explicit transport type and lose non-Claude fields."""
        item = Builder.item(Kind.MCP, name="github", frontmatter=server)

        result = ClaudeProfile().render(item)

        assert result.entries == (
            EntryOutput(".claude.json", "mcpServers", "github", expected),
        ), f"Expected one mcpServers entry, got {result!r}"
        assert result.losses == losses, f"Expected {losses!r}, got {result.losses!r}"

    @pytest.mark.parametrize(
        ("server", "reason"),
        [
            ({"type": "http", "command": "gh"}, "does not fit a server with a command"),
            ({"type": "stdio", "url": "https://x"}, "is not a remote transport"),
            ({"type": [], "command": "gh"}, "type must be a string"),
        ],
    )
    def test_mcp_server_with_mismatched_type_is_refused(
        self, server: dict[str, JsonValue], reason: str
    ) -> None:
        """A transport type that contradicts the server's shape is never guessed at."""
        result = ClaudeProfile().render(Builder.item(Kind.MCP, frontmatter=server))

        assert result.unsupported is not None, f"Expected refusal, got {result!r}"
        assert reason in result.unsupported, f"Expected {reason!r}, got {result!r}"

    def test_claude_json_sits_beside_the_default_root(
        self, environment: Environment
    ) -> None:
        """The default ``~/.claude`` root keeps its servers in ``~/.claude.json``."""
        root = environment.home / ".claude"

        result = ClaudeProfile().locate(".claude.json", root, environment)

        assert result == "../.claude.json", f"Expected sibling file, got {result!r}"

    @pytest.mark.parametrize("configured", [True, False])
    def test_claude_json_stays_inside_a_config_dir(
        self, environment: Environment, *, configured: bool
    ) -> None:
        """``$CLAUDE_CONFIG_DIR`` or a custom root holds its own ``.claude.json``."""
        root = environment.home / (".claude" if configured else "custom")
        variables = dict(environment.variables)
        if configured:
            variables["CLAUDE_CONFIG_DIR"] = str(root)
        custom = Environment(environment.home, variables, environment.platform)

        result = ClaudeProfile().locate(".claude.json", root, custom)

        assert result == ".claude.json", f"Expected file in root, got {result!r}"

    def test_other_files_are_not_relocated(self, environment: Environment) -> None:
        """Only ``.claude.json`` moves beside the root."""
        root = environment.home / ".claude"

        result = ClaudeProfile().locate("settings.json", root, environment)

        assert result == "settings.json", f"Expected unchanged path, got {result!r}"


class TestClaudeHooksRendering:
    """Hook files become mergeable ``hooks`` entries in ``settings.json``."""

    def test_hook_file_becomes_one_entry_per_event(self) -> None:
        """Emit sorted, mergeable entries keyed by Claude event."""
        item = Builder.item(
            Kind.HOOK,
            frontmatter={
                "version": 1,
                "hooks": {
                    "Stop": [{"bash": "b.sh"}],
                    "SessionEnd": [{"bash": "a.sh"}],
                },
            },
        )

        result = ClaudeProfile().render(item)

        def group(command: str) -> list[JsonValue]:
            handler = {"type": "command", "command": command, "timeout": 30}
            return [{"hooks": [handler]}]

        assert result == Rendering(
            entries=(
                EntryOutput(
                    "settings.json", "hooks", "SessionEnd", group("a.sh"), merge=True
                ),
                EntryOutput(
                    "settings.json", "hooks", "Stop", group("b.sh"), merge=True
                ),
            )
        ), f"Expected two settings.json entries, got {result!r}"

    @pytest.mark.parametrize(
        ("header", "reason"),
        [
            (
                {"version": 1, "hooks": {"errorOccurred": [{"bash": "a"}]}},
                "has no hooks Claude Code can run",
            ),
            (
                {
                    "version": 1,
                    "disableAllHooks": True,
                    "hooks": {"Stop": [{"bash": "a"}]},
                },
                "sets disableAllHooks, so none of its hooks run",
            ),
        ],
    )
    def test_hook_file_without_runnable_hooks_is_refused(
        self, header: Frontmatter, reason: str
    ) -> None:
        """Refuse a file whose hooks all fall outside Claude Code or are disabled."""
        item = Builder.item(Kind.HOOK, frontmatter=header)

        result = ClaudeProfile().render(item)

        assert result.unsupported == reason, f"Expected refusal, got {result!r}"
