# 0006: Hooks from Copilot CLI hook files

This record defines the library format for hooks and how SkillSinc writes them for each harness. It is for anyone extending hook support to another harness.

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

Hooks are commands a harness runs at lifecycle events (session start, before a tool runs, and so on). Copilot CLI reads whole hook files from `~/.copilot/hooks/*.json` ([hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference)). Claude Code reads them from the `hooks` key of `~/.claude/settings.json` ([hooks](https://code.claude.com/docs/en/hooks)), a file it shares with the user's other settings. The earlier ai-feature-sync tool copied Copilot hook files and nothing else.

The formats differ:

|         | Copilot CLI                                                                              | Claude Code                                                        |
| ------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Shape   | `{"version": 1, "hooks": {"<event>": [<hook>]}}`                                         | `{"hooks": {"<Event>": [{"matcher": ..., "hooks": [<handler>]}]}}` |
| Events  | camelCase (`preToolUse`), or PascalCase (`PreToolUse`) for the Claude-compatible payload | PascalCase only                                                    |
| Command | `bash`, `powershell`, `command`, or `exec` plus `args`                                   | `command`                                                          |
| Extras  | `cwd`, `env`, `timeoutSec` (default 30)                                                  | `timeout`                                                          |

## Decision

- **Library format.** One Copilot CLI hook file per `hooks/<name>.json`, validated as `"version": 1` plus a `hooks` object whose events are arrays of objects. Copilot's format is the source because Copilot CLI accepts both its own and Claude-style event names, so one file can serve both harnesses.
- **Copilot CLI.** Exact copy to `hooks/<name>.json`, owned as a whole file.
- **Claude Code.** Converted into `settings.json` with the entry mechanism from [decision 0002](0002-mcp-server-entries.md). The ownership unit is one event array (`hooks/PreToolUse`). Claude has no stable identity for one element of an event array, so finer ownership would mean guessing which elements are ours.
- **Merging.** Several hook files may use one event. Their converted groups are concatenated in item order into the one owned array, skipping groups already present; this is the `merge` flag on `EntryOutput`. Two sources defining the same hook therefore produce it once.
- **User hooks.** An event the user already hooks by hand is an unowned key: it blocks the destination until `--adopt`, which replaces that event's array. Events SkillSinc does not use are never touched.
- **Conversion.** Each Copilot hook entry becomes one matcher group with one command handler. camelCase events map to the matching Claude event; PascalCase events that Claude defines pass through. A missing timeout becomes an explicit 30 seconds so the Copilot default holds. Everything Claude cannot carry is reported as a loss, so the route needs `allow_lossy`: camelCase events (different input payload), events whose output JSON Claude reads differently (every event except `Stop`, `SubagentStop`, `SessionEnd`, and `PreCompact`; a Copilot `PreToolUse` guard's top-level `permissionDecision` is ignored by Claude, so it must never sync silently), events Claude lacks, matchers on camelCase tool events (different tool names), `powershell`, `cwd`, `env`, other fields, and non-command hooks. A file with nothing Claude can run is skipped, and so is a file with `"disableAllHooks": true`: setting Claude's global flag instead would also disable the user's own hooks.
- **Other harnesses.** VS Code reads user hooks from the Copilot CLI and Claude Code locations, so its profile refuses hooks and points there. Codex and `agents` refuse hooks.

## Consequences

- Only PascalCase `Stop`, `SubagentStop`, `SessionEnd`, and `PreCompact` hooks with `bash` or `command` handlers convert with no losses; every other hook needs `allow_lossy`, and its script must speak Claude's output protocol to work there.
- Users who already hook an event in `settings.json` must either move those hooks into the library or keep the event out of it; `--adopt` discards the hand-written array.
- `settings.json` is re-serialized with two-space indentation on the first sync that changes a hook, as with `mcp-config.json`.
