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


@dataclass(slots=True)
class ClaudeHooks:
    """The Claude Code hook groups for one Copilot hook file, plus what was lost."""

    events: dict[str, list[JsonValue]] = field(default_factory=dict)
    losses: list[str] = field(default_factory=list)

    @classmethod
    def convert(cls, hooks: Mapping[str, JsonValue]) -> ClaudeHooks:
        """Convert the ``hooks`` object of a Copilot CLI hook file."""
        result = cls()
        missing: list[str] = []
        camel: list[str] = []
        matched: list[str] = []
        dropped: set[str] = set()
        skipped: list[str] = []
        for event, entries in hooks.items():
            target = CAMEL_EVENTS.get(event) or (
                event if event in CLAUDE_EVENTS else None
            )
            if target is None:
                missing.append(event)
                continue
            if event in CAMEL_EVENTS:
                camel.append(event)
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict):
                    continue
                handler = cls._handler(entry)
                if handler is None:
                    skipped.append(event)
                    continue
                dropped.update(key for key in entry if key not in CARRIED_FIELDS)
                group: dict[str, JsonValue] = {"hooks": [handler]}
                matcher = entry.get("matcher")
                if isinstance(matcher, str) and matcher:
                    group = {"matcher": matcher, **group}
                    if event in TOOL_EVENTS:
                        matched.append(event)
                if "powershell" in entry and "bash" in entry:
                    dropped.add("powershell")
                result.events.setdefault(target, []).append(group)
        result.losses = cls._losses(missing, camel, matched, sorted(dropped), skipped)
        return result

    # Internals

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

    @staticmethod
    def _losses(
        missing: list[str],
        camel: list[str],
        matched: list[str],
        dropped: list[str],
        skipped: list[str],
    ) -> list[str]:
        """Describe everything the conversion could not carry."""
        losses: list[str] = []
        if missing:
            losses.append(f"drops events Claude Code lacks: {', '.join(missing)}")
        if camel:
            losses.append(
                "scripts get Claude's snake_case input, not Copilot's camelCase"
                f" input, for: {', '.join(camel)}"
            )
        if matched:
            losses.append(
                "matchers are tested against Claude tool names (Bash, Edit),"
                f" not Copilot's, for: {', '.join(sorted(set(matched)))}"
            )
        if dropped:
            losses.append(f"drops hook fields: {', '.join(dropped)}")
        if skipped:
            losses.append(
                "drops hooks with no bash, command, or exec command for:"
                f" {', '.join(sorted(set(skipped)))}"
            )
        return losses
