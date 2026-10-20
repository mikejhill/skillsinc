"""Test Copilot CLI to Claude Code hook conversion and its loss reports."""

from __future__ import annotations

from skillsinc.models import JsonValue
from skillsinc.profiles.hooks import ClaudeHooks


class TestClaudeHooks:
    """Each Copilot hook entry becomes one Claude matcher group."""

    def test_camel_case_events_map_to_claude_events(self) -> None:
        """Rename events, carry bash and timeoutSec, and report the payload change."""
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
            "scripts get Claude's snake_case input, not Copilot's camelCase input,"
            " for: sessionStart, agentStop"
        ], f"Expected payload loss only, got {result.losses!r}"

    def test_pascal_case_events_and_matchers_carry_exactly(self) -> None:
        """Claude-format events keep their matcher semantics with no losses."""
        hooks: dict[str, JsonValue] = {
            "PreToolUse": [{"matcher": "Bash", "bash": "guard.sh", "timeout": 9}]
        }

        result = ClaudeHooks.convert(hooks)

        assert (result.events, result.losses) == (
            {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {"type": "command", "command": "guard.sh", "timeout": 9}
                        ],
                    }
                ]
            },
            [],
        ), f"Expected exact carry, got {result!r}"

    def test_unrepresentable_parts_are_reported(self) -> None:
        """Report missing events, foreign matchers, dropped fields, skipped hooks."""
        hooks: dict[str, JsonValue] = {
            "errorOccurred": [{"bash": "err.sh"}],
            "preToolUse": [
                {
                    "matcher": "bash",
                    "bash": "a.sh",
                    "powershell": "a.ps1",
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
            "scripts get Claude's snake_case input, not Copilot's camelCase input,"
            " for: preToolUse, sessionEnd",
            "matchers are tested against Claude tool names (Bash, Edit), not"
            " Copilot's, for: preToolUse",
            "drops hook fields: cwd, env, powershell",
            "drops hooks with no bash, command, or exec command for: preToolUse",
        ], f"Expected every loss reported, got {result.losses!r}"

    def test_exec_becomes_a_quoted_command(self) -> None:
        """Join ``exec`` and ``args`` into one shell-safe command."""
        hooks: dict[str, JsonValue] = {
            "SessionStart": [{"exec": "my tool", "args": ["--flag", "a b"]}]
        }

        result = ClaudeHooks.convert(hooks)

        handler = result.events["SessionStart"][0]
        assert handler == {
            "hooks": [
                {
                    "type": "command",
                    "command": "'my tool' --flag 'a b'",
                    "timeout": 30,
                }
            ]
        }, f"Expected quoted exec command, got {handler!r}"
