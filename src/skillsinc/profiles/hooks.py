"""Convert Copilot CLI hook files into Claude Code ``settings.json`` hook groups."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from skillsinc.models import JsonValue

# Copilot CLI camelCase events and the Claude Code event each one maps to
CAMEL_EVENTS: Final[Mapping[str, str]] = {
    "sessionStart": "SessionStart",
    "sessionEnd": "SessionEnd",
    "userPromptSubmitted": "UserPromptSubmit",
    "preToolUse": "PreToolUse",
    "postToolUse": "PostToolUse",
    "postToolUseFailure": "PostToolUseFailure",
    "permissionRequest": "PermissionRequest",
    "notification": "Notification",
    "agentStop": "Stop",
    "subagentStart": "SubagentStart",
    "subagentStop": "SubagentStop",
    "preCompact": "PreCompact",
}
CLAUDE_EVENTS: Final = frozenset(CAMEL_EVENTS.values())
# camelCase events whose matcher Copilot tests against its own tool names
TOOL_EVENTS: Final = frozenset(
    {"preToolUse", "postToolUse", "postToolUseFailure", "permissionRequest"}
)
CARRIED_FIELDS: Final = frozenset(
    {
        "type",
        "bash",
        "command",
        "powershell",
        "exec",
        "args",
        "timeoutSec",
        "timeout",
        "matcher",
    }
)
DEFAULT_TIMEOUT: Final = 30
# Claude events whose stdout JSON Claude reads differently from Copilot CLI
# (for example permissionDecision inside hookSpecificOutput); Stop, SubagentStop,
# SessionEnd, and PreCompact agree
OUTPUT_EVENTS: Final = frozenset(
    {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "PermissionRequest",
        "Notification",
        "SubagentStart",
    }
)

# Loss messages, in report order
MISSING: Final = "drops events Claude Code lacks"
CAMEL: Final = (
    "scripts get Claude's snake_case input, not Copilot's camelCase input, for"
)
OUTPUT: Final = (
    "Claude reads hook output JSON differently (for example, permissionDecision"
    " belongs inside hookSpecificOutput), for"
)
MATCHED: Final = (
    "matchers are tested against Claude tool names (Bash, Edit), not Copilot's, for"
)
DROPPED: Final = "drops hook fields"
SKIPPED: Final = "drops hooks with no bash, command, or exec command for"
LOSSES: Final = (MISSING, CAMEL, OUTPUT, MATCHED, DROPPED, SKIPPED)


@dataclass(slots=True)
class ClaudeHooks:
    """The Claude Code hook groups for one Copilot hook file, plus what was lost."""

    events: dict[str, list[JsonValue]] = field(default_factory=dict)
    losses: list[str] = field(default_factory=list)

    @classmethod
    def convert(cls, hooks: Mapping[str, JsonValue]) -> ClaudeHooks:
        """Convert the ``hooks`` object of a Copilot CLI hook file."""
        result = cls()
        found: dict[str, set[str]] = {message: set() for message in LOSSES}
        for event, entries in hooks.items():
            target = CAMEL_EVENTS.get(event) or (
                event if event in CLAUDE_EVENTS else None
            )
            if target is None:
                found[MISSING].add(event)
                continue
            if event in CAMEL_EVENTS:
                found[CAMEL].add(event)
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict):
                    continue
                group = cls._group(event, target, entry, found)
                if group is not None:
                    result.events.setdefault(target, []).append(group)
        result.losses = [
            f"{message}: {', '.join(sorted(names))}"
            for message, names in found.items()
            if names
        ]
        return result

    # Internals

    @classmethod
    def _group(
        cls,
        event: str,
        target: str,
        entry: Mapping[str, JsonValue],
        found: dict[str, set[str]],
    ) -> dict[str, JsonValue] | None:
        """Return the Claude matcher group for one entry, recording its losses."""
        handler = cls._handler(entry)
        if handler is None:
            found[SKIPPED].add(event)
            return None
        found[DROPPED].update(key for key in entry if key not in CARRIED_FIELDS)
        if "powershell" in entry:
            found[DROPPED].add("powershell")
        if target in OUTPUT_EVENTS:
            found[OUTPUT].add(event)
        group: dict[str, JsonValue] = {"hooks": [handler]}
        matcher = entry.get("matcher")
        if isinstance(matcher, str) and matcher:
            group = {"matcher": matcher, **group}
            if event in TOOL_EVENTS:
                found[MATCHED].add(event)
        return group

    @staticmethod
    def _handler(entry: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
        """Return the Claude command handler for one entry, or None if it has none."""
        if entry.get("type", "command") != "command":
            return None
        command = entry.get("bash", entry.get("command"))
        executable = entry.get("exec")
        if not isinstance(command, str) and isinstance(executable, str):
            args = entry.get("args", [])
            words = [str(arg) for arg in args] if isinstance(args, list) else []
            command = shlex.join([executable, *words])
        if not isinstance(command, str) or not command:
            return None
        timeout = entry.get("timeoutSec", entry.get("timeout", DEFAULT_TIMEOUT))
        return {"type": "command", "command": command, "timeout": timeout}
