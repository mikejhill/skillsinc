# Harness profiles

This guide lists exactly what each profile writes and which conversions lose information. Use it to decide which routes need `allow_lossy = true`.

## Conventions

- **exact**: the library file is copied byte for byte.
- **converted**: frontmatter is rewritten for the harness; losses are reported per item.
- Library files use the GitHub Copilot formats: `applyTo` on instructions, `tools`/`model` on agents, `${input:...}` variables in prompts.
- MCP servers are `mcp/<name>.json` files, each one server object in the `mcpServers` entry format (`type`, `command`, `args`, `env`, or `url`, `headers`). SkillSinc owns only its own keys inside a shared JSON file; see [decision 0002](../decisions/0002-mcp-server-entries.md).
- Resources are `resources/<name>.md` reference files, copied byte for byte and never parsed; see [decision 0003](../decisions/0003-resources-and-claude-imports.md).
- Hooks are `hooks/<name>.json` files in the [Copilot CLI hook format](https://docs.github.com/en/copilot/reference/hooks-reference): `{"version": 1, "hooks": {"<event>": [<hook>, ...]}}`. Event names may be camelCase (`preToolUse`, Copilot's payload) or PascalCase (`PreToolUse`, the Claude-compatible payload); see [decision 0004](../decisions/0004-hooks.md).

## claude

Default path `$CLAUDE_CONFIG_DIR` or `~/.claude`. Reference: [Claude Code memory and rules](https://code.claude.com/docs/en/memory).

| Kind         | Output                            | Conversion                                             |
| ------------ | --------------------------------- | ------------------------------------------------------ |
| skills       | `skills/<name>/`                  | exact                                                  |
| instructions | `rules/<name>.md`                 | `applyTo` → `paths`; `**` → no `paths` (always loaded) |
| agents       | `agents/<name>.md`                | keeps `name`, `description`; requires a description    |
| prompts      | `commands/<name>.md`              | keeps `description`, `argument-hint`                   |
| resources    | `resources/<name>.md`             | exact, plus `@resources/<name>.md` in `CLAUDE.md`      |
| hooks        | `settings.json` → `hooks.<Event>` | one matcher group per Copilot hook entry               |

Each resource is imported from a managed block in `CLAUDE.md`, so Claude Code loads it in every session. Text outside the block is never changed; a `CLAUDE.md` SkillSinc created is deleted when the last resource goes.

MCP servers are not written: Claude Code keeps user servers in `~/.claude.json`, outside this destination, and rewrites that file while running.

Hooks are written into `hooks` in `~/.claude/settings.json` ([Claude Code hooks](https://code.claude.com/docs/en/hooks)). SkillSinc owns whole event arrays (`hooks/PreToolUse`, for example); every hook file that uses an event is merged into that one array, and the user's other events and settings are kept. An event the user already hooks by hand blocks the destination until `--adopt`, which replaces that event's array. Each Copilot hook entry becomes `{"matcher": ..., "hooks": [{"type": "command", "command": ..., "timeout": ...}]}`: `bash` (or `command`, or `exec` plus `args` joined into a quoted command) becomes `command`, and `timeoutSec` (or `timeout`, default 30) becomes `timeout`. camelCase events map to Claude events (`userPromptSubmitted` → `UserPromptSubmit`, `agentStop` → `Stop`, and so on); PascalCase events already named the Claude way pass through.

Losses: instructions without `applyTo` (on demand in Copilot, always loaded in Claude); `excludeAgent`; agent `tools`, `model`, `handoffs`, and other Copilot fields; prompt `agent`, `mode`, `model`, `tools`; VS Code `${...}` variables in prompt bodies. For hooks: camelCase events (scripts receive Claude's snake_case input instead of Copilot's camelCase input); events whose output JSON Claude reads differently (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `Notification`, `SubagentStart`; for example, Claude expects `permissionDecision` inside `hookSpecificOutput`); events Claude Code lacks (`errorOccurred`, `userPromptTransformed`); matchers on camelCase tool events (Claude tests them against its own tool names, such as `Bash`); `powershell`, `cwd`, `env`, and other fields; hooks with no `bash`, `command`, or `exec` (for example `powershell`-only, `http`, or `prompt` hooks). A hook file with nothing Claude Code can run, or with `"disableAllHooks": true`, is skipped.

## copilot

Default path `$COPILOT_HOME` or `~/.copilot`. Reference: [Copilot CLI custom instructions](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions).

| Kind         | Output                                  | Conversion |
| ------------ | --------------------------------------- | ---------- |
| skills       | `skills/<name>/`                        | exact      |
| instructions | `instructions/<name>.instructions.md`   | exact      |
| agents       | `agents/<name>.agent.md`                | exact      |
| prompts      | unsupported                             | —          |
| mcp          | `mcp-config.json` → `mcpServers.<name>` | exact      |
| resources    | `resources/<name>.md`                   | exact      |
| hooks        | `hooks/<name>.json`                     | exact      |

Loss: an instructions file without `applyTo`, which Copilot CLI never applies.

Copilot CLI does not load `resources/` on its own; instructions and agents can point at the files there.

MCP servers are copied as written into `mcpServers` in `~/.copilot/mcp-config.json`; other servers and keys in the file are kept, and the file is rewritten with two-space indentation. A server with the same name that SkillSinc did not write blocks the destination until `--adopt`. A file that is not plain JSON (comments, duplicate keys) blocks it too. Write each server in the shape Copilot CLI documents; SkillSinc does not convert fields.

## vscode

Default path: the VS Code user `prompts` folder (`%APPDATA%\Code\User\prompts`, `~/Library/Application Support/Code/User/prompts`, or `$XDG_CONFIG_HOME/Code/User/prompts`). Instructions, agents, and prompts are copied exactly with their `.instructions.md`, `.agent.md`, and `.prompt.md` suffixes. VS Code reads skills from `~/.copilot/skills` and `~/.agents/skills`, so route skills to a `copilot` or `agents` destination. MCP servers are not written: VS Code reads them from `User/mcp.json` (key `servers`, comments allowed), outside the prompts folder. Hooks are not written: VS Code reads user hooks from the Copilot CLI and Claude Code locations, so route hooks to a `copilot` or `claude` destination.

## codex

Default path `$CODEX_HOME` or `~/.codex`. Instructions are combined, in name order, into a managed block in `AGENTS.md`; the rest of the file is preserved. Codex reads user skills from `~/.agents/skills` ([Codex skills](https://developers.openai.com/codex/skills)), so route skills to an `agents` destination.

Losses: `applyTo` globs become an advisory sentence; files without `applyTo` become always-on.

MCP servers are not written: Codex reads them from `[mcp_servers.<name>]` tables in `config.toml`, which needs a TOML editor that preserves formatting. Hooks are not written.

## agents

Default path `~/.agents`. Skills only, copied exactly to `skills/<name>/`. Codex, Copilot, and other [Agent Skills](https://agentskills.io/specification) readers discover this folder.

## generic

No default path. Copies the library layout verbatim: `skills/<name>/`, and `<kind>/<original file name>` for everything else, including `mcp/<name>.json` and `hooks/<name>.json`. Useful for exports and backups.
