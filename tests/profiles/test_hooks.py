"""Test Copilot CLI to Claude Code hook conversion and its loss reports."""

from __future__ import annotations

from skillsinc.models import JsonValue
from skillsinc.profiles.hooks import ClaudeHooks

PAYLOAD = "scripts get Claude's snake_case input, not Copilot's camelCase input, for"
OUTPUT = (
    "Claude reads hook output JSON differently (for example, permissionDecision"
    " belongs inside hookSpecificOutput), for"
)


class TestClaudeHooks:
    """Each Copilot hook entry becomes one Claude matcher group."""

    def test_camel_case_events_map_to_claude_events(self) -> None:
        """Rename events, carry bash and timeoutSec, and report payload changes."""
        hooks: dict[str, JsonValue] = {
            "sessionStart": [{"type": "command", "bash": "start.sh", "timeoutSec": 5}],
            "agentStop": [{"command": "stop.sh"}],
        }

        result = ClaudeHooks.convert(hooks)

        assert result.events == {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "start.sh", "timeout": 5}]}
            ],
            "Stop": [
                {"hooks": [{"type": "command", "command": "stop.sh", "timeout": 30}]}
            ],
        }, f"Expected renamed events with timeouts, got {result.events!r}"
        assert result.losses == [
            f"{PAYLOAD}: agentStop, sessionStart",
            f"{OUTPUT}: sessionStart",
        ], f"Expected payload and output losses, got {result.losses!r}"

    def test_pascal_case_events_with_shared_output_carry_exactly(self) -> None:
        """Claude-format events whose output both harnesses read alike lose nothing."""
        hooks: dict[str, JsonValue] = {
            "Stop": [{"matcher": "x", "bash": "stop.sh", "timeout": 9}]
        }

        result = ClaudeHooks.convert(hooks)

        assert (result.events, result.losses) == (
            {
                "Stop": [
                    {
                        "matcher": "x",
                        "hooks": [
                            {"type": "command", "command": "stop.sh", "timeout": 9}
                        ],
                    }
                ]
            },
            [],
        ), f"Expected exact carry, got {result!r}"

    def test_pascal_case_pre_tool_use_reports_output_protocol(self) -> None:
        """A permission guard never syncs silently; Claude reads verdicts elsewhere."""
        hooks: dict[str, JsonValue] = {
            "PreToolUse": [{"matcher": "Bash", "bash": "guard.sh"}]
        }

        result = ClaudeHooks.convert(hooks)

        assert result.losses == [f"{OUTPUT}: PreToolUse"], (
            f"Expected output-protocol loss, got {result.losses!r}"
        )

    def test_unrepresentable_parts_are_reported(self) -> None:
        """Report missing events, foreign matchers, dropped fields, skipped hooks."""
        hooks: dict[str, JsonValue] = {
            "errorOccurred": [{"bash": "err.sh"}],
            "preToolUse": [
                {
                    "matcher": "bash",
                    "bash": "a.sh",
                    "cwd": "x",
                    "env": {"A": "1"},
                },
                {"powershell": "only.ps1"},
                {"type": "http", "url": "https://hooks.example"},
                "not an object",
            ],
            "sessionEnd": "not a list",
        }

        result = ClaudeHooks.convert(hooks)

        assert result.events == {
            "PreToolUse": [
                {
                    "matcher": "bash",
                    "hooks": [{"type": "command", "command": "a.sh", "timeout": 30}],
                }
            ]
        }, f"Expected one converted hook, got {result.events!r}"
        assert result.losses == [
            "drops events Claude Code lacks: errorOccurred",
            f"{PAYLOAD}: preToolUse, sessionEnd",
            f"{OUTPUT}: preToolUse",
            (
                "matchers are tested against Claude tool names (Bash, Edit), not"
                " Copilot's, for: preToolUse"
            ),
            "drops hook fields: cwd, env",
            "drops hooks with no bash, command, or exec command for: preToolUse",
        ], f"Expected every loss reported, got {result.losses!r}"

    def test_powershell_override_is_reported_beside_command(self) -> None:
        """Keep ``command`` and report the dropped Windows override."""
        hooks: dict[str, JsonValue] = {
            "Stop": [{"command": "stop.sh", "powershell": "stop.ps1"}]
        }

        result = ClaudeHooks.convert(hooks)

        assert result.losses == ["drops hook fields: powershell"], (
            f"Expected powershell loss, got {result.losses!r}"
        )

    def test_exec_becomes_a_quoted_command(self) -> None:
        """Join ``exec`` and ``args`` into one shell-safe command."""
        hooks: dict[str, JsonValue] = {
            "Stop": [{"exec": "my tool", "args": ["--flag", "a b"]}]
        }

        result = ClaudeHooks.convert(hooks)

        handler = result.events["Stop"][0]
        assert handler == {
            "hooks": [
                {
                    "type": "command",
                    "command": "'my tool' --flag 'a b'",
                    "timeout": 30,
                }
            ]
        }, f"Expected quoted exec command, got {handler!r}"
