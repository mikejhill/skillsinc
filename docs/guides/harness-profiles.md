# Harness profiles

This guide lists exactly what each profile writes and which conversions lose information. Use it to decide which routes need `allow_lossy = true`.

## Conventions

- **exact**: the library file is copied byte for byte.
- **converted**: frontmatter is rewritten for the harness; losses are reported per item.
- Library files use the GitHub Copilot formats: `applyTo` on instructions, `tools`/`model` on agents, `${input:...}` variables in prompts.
- MCP servers are `mcp/<name>.json` files, each one server object in the `mcpServers` entry format (`type`, `command`, `args`, `env`, or `url`, `headers`). SkillSinc owns only its own keys inside a shared JSON file; see [decision 0002](../decisions/0002-mcp-server-entries.md). Three harness-neutral options are mapped per profile, never copied: `enabled` (`false` becomes `enabled = false` in Codex, and leaves the server out of harnesses with no per-server switch), and `startupTimeoutSec`/`toolTimeoutSec` (Codex `startup_timeout_sec`/`tool_timeout_sec`; a loss elsewhere). `profiles.<profile>` holds fields for one profile only, merged into its converted entry last and unchecked, for example `"profiles": {"codex": {"default_tools_approval_mode": "prompt"}}` ([decision 0010](../decisions/0010-mcp-server-options.md)).
- Resources are `resources/<name>.md` reference files, copied byte for byte and never parsed; see [decision 0003](../decisions/0003-resources-and-claude-imports.md).
- Hooks are `hooks/<name>.json` files in the [Copilot CLI hook format](https://docs.github.com/en/copilot/reference/hooks-reference): `{"version": 1, "hooks": {"<event>": [<hook>, ...]}}`. Event names may be camelCase (`preToolUse`, Copilot's payload) or PascalCase (`PreToolUse`, the Claude-compatible payload); see [decision 0006](../decisions/0006-hooks.md).

## Plugins

Library plugins (`plugins/<plugin>/`, Claude Code layout) are expanded into skills, agents, prompts, and MCP servers before any profile sees them ([decision 0005](../decisions/0005-plugins-expand-into-kinds.md)). Each harness then receives the parts it supports, without the plugin namespace (`kit:reviewer` becomes `reviewer`).

| Plugin part          | Becomes     | `claude`                         | `copilot`, `vscode`                                                                        |
| -------------------- | ----------- | -------------------------------- | ------------------------------------------------------------------------------------------ |
| `skills/<name>/`     | skill       | exact                            | exact (`copilot`)                                                                          |
| `agents/<name>.md`   | agent       | exact                            | keeps `name`, `description`; other fields are losses                                       |
| `commands/<name>.md` | prompt      | exact                            | `vscode` keeps `description`, `argument-hint`; `$ARGUMENTS`, `$1`, `` !`cmd` `` are losses |
| `.mcp.json`          | MCP servers | refused (as for library items)   | `copilot` entries                                                                          |
| everything else      | —           | loss on every item of the plugin | loss on every item of the plugin                                                           |

Items whose files (skill assets included) refer to `${CLAUDE_PLUGIN_ROOT}`, another `CLAUDE_PLUGIN_*` variable, or `${user_config.*}` are skipped everywhere: only an installed plugin resolves them.

## claude

Default path `$CLAUDE_CONFIG_DIR` or `~/.claude`. Reference: [Claude Code memory and rules](https://code.claude.com/docs/en/memory).

| Kind         | Output                               | Conversion                                                        |
| ------------ | ------------------------------------ | ----------------------------------------------------------------- |
| skills       | `skills/<name>/`                     | exact                                                             |
| instructions | `rules/<name>.md`                    | `applyTo` → `paths`; `**` → no `paths` (always loaded)            |
| agents       | `agents/<name>.md`                   | keeps `name`, `description`; requires a description               |
| prompts      | `commands/<name>.md`                 | keeps `description`, `argument-hint`                              |
| resources    | `resources/<name>.md`                | exact, plus `@resources/<name>.md` in `CLAUDE.md`                 |
| mcp          | `.claude.json` → `mcpServers.<name>` | explicit `type`; keeps `command`, `args`, `env`, `url`, `headers` |
| hooks        | `settings.json` → `hooks.<Event>`    | one matcher group per Copilot hook entry                          |

Each resource is imported from a managed block in `CLAUDE.md`, so Claude Code loads it in every session. Text outside the block is never changed; a `CLAUDE.md` SkillSinc created is deleted when the last resource goes.

MCP servers go into `~/.claude.json` for the default `~/.claude` root, or `<root>/.claude.json` for any other root (including `$CLAUDE_CONFIG_DIR`), which is where Claude Code reads user servers ([decision 0007](../decisions/0007-files-beside-the-root.md)). Each server gets an explicit `type`: `stdio` for a `command` server (Copilot's `local` included), and `http` or `sse` for a `url` server. Other servers and keys in the file are kept; the file keeps its permissions and is rewritten with two-space indentation. `~/.claude.json` must not be a link. Claude Code rewrites the file while running, so a sync can stop with "changed since the plan was made"; run it again.

Hooks are written into `hooks` in `~/.claude/settings.json` ([Claude Code hooks](https://code.claude.com/docs/en/hooks)). SkillSinc owns whole event arrays (`hooks/PreToolUse`, for example); every hook file that uses an event is merged into that one array, and the user's other events and settings are kept. An event the user already hooks by hand blocks the destination until `--adopt`, which replaces that event's array. Each Copilot hook entry becomes `{"matcher": ..., "hooks": [{"type": "command", "command": ..., "timeout": ...}]}`: `bash` (or `command`, or `exec` plus `args` joined into a quoted command) becomes `command`, and `timeoutSec` (or `timeout`, default 30) becomes `timeout`. camelCase events map to Claude events (`userPromptSubmitted` → `UserPromptSubmit`, `agentStop` → `Stop`, and so on); PascalCase events already named the Claude way pass through.

Losses: instructions without `applyTo` (on demand in Copilot, always loaded in Claude); `excludeAgent`; agent `tools`, `model`, `handoffs`, and other Copilot fields; prompt `agent`, `mode`, `model`, `tools`; VS Code `${...}` variables in prompt bodies; MCP server fields other than `type`, `command`, `args`, `env`, `url`, `headers` (for example Copilot's `tools`), and a `url` server without `type`, written as `http` (a server whose `type` contradicts its shape is refused). For hooks: camelCase events (scripts receive Claude's snake_case input instead of Copilot's camelCase input); events whose output JSON Claude reads differently (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `Notification`, `SubagentStart`; for example, Claude expects `permissionDecision` inside `hookSpecificOutput`); events Claude Code lacks (`errorOccurred`, `userPromptTransformed`); matchers on camelCase tool events (Claude tests them against its own tool names, such as `Bash`); `powershell`, `cwd`, `env`, and other fields; hooks with no `bash`, `command`, or `exec` (for example `powershell`-only, `http`, or `prompt` hooks). A hook file with nothing Claude Code can run, or with `"disableAllHooks": true`, is skipped.

Verified 2026-10-21 on Windows with Claude Code 2.1.288 and the library above:

- `claude mcp list` showed the synced servers.
- All 22 skills were installed. A `claude -p` session listed 20; the two skills that set `disable-model-invocation: true` stayed available as slash commands.
- Both agents and the prompt (as a command) appeared.
- The `CLAUDE.md` import block and the 4 `rules/` files were present.
- Path-scoped rules were not tested.

## copilot

Default path `$COPILOT_HOME` or `~/.copilot`. Reference: [Copilot CLI custom instructions](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions). Copilot CLI also reads skills from `~/.agents/skills`, so skills an `agents` destination receives are skipped here ([decision 0009](../decisions/0009-one-copy-per-harness.md)).

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

Verified 2026-10-21 on Windows with Copilot CLI 1.0.101 and a real library (22 skills, 4 instructions, 2 agents). Copilot CLI listed every skill once, from `~/.agents/skills`, with `~/.copilot/skills` left empty. The two skills it did not list set `disable-model-invocation: true`. Both agents were listed.

## vscode

Default path: the VS Code user `prompts` folder (`%APPDATA%\Code\User\prompts`, `~/Library/Application Support/Code/User/prompts`, or `$XDG_CONFIG_HOME/Code/User/prompts`). Instructions, agents, and prompts are copied exactly with their `.instructions.md`, `.agent.md`, and `.prompt.md` suffixes. VS Code reads skills from `~/.copilot/skills`, `~/.claude/skills`, and `~/.agents/skills`, and also reads instructions and agents from `~/.copilot/instructions` and `~/.copilot/agents`. Instructions and agents a `copilot` destination receives are skipped here, so VS Code lists each once ([decision 0009](../decisions/0009-one-copy-per-harness.md)). MCP servers go into `servers` in `User/mcp.json`, beside the prompts folder ([decision 0007](../decisions/0007-files-beside-the-root.md)), converted as for `claude`: an explicit `type`, and losses for fields other than `type`, `command`, `args`, `env`, `url`, `headers` (VS Code's own `cwd`, `envFile`, and `inputs` references are not carried). Other servers and keys, such as `inputs`, are kept. VS Code allows comments and trailing commas in this file, and SkillSinc keeps them: each change splices only the affected server's text, matching its siblings' indentation, and is parsed back before it is written ([decision 0008](../decisions/0008-jsonc-edits-in-place.md)). A comment inside a server SkillSinc owns is lost when that server changes. Unterminated comments, duplicate keys, or a `servers` that is not an object block the destination. `mcp.json` must not be a link. Hooks are not written: VS Code reads user hooks from the Copilot CLI and Claude Code locations, so route hooks to a `copilot` or `claude` destination.

Checked 2026-10-21 on Windows with VS Code 1.142.0 at the file level only: `User/prompts` held just the prompt, because instructions and agents came from `~/.copilot`. Discovery inside VS Code has not been confirmed yet.

## codex

Default path `$CODEX_HOME` or `~/.codex`. Instructions are combined, in name order, into a managed block in `AGENTS.md`; the rest of the file is preserved. Codex reads user skills from `~/.agents/skills` ([Codex skills](https://developers.openai.com/codex/skills)), so route skills to an `agents` destination.

Losses: `applyTo` globs become an advisory sentence; files without `applyTo` become always-on.

| Kind         | Output                                 | Conversion |
| ------------ | -------------------------------------- | ---------- |
| instructions | managed block in `AGENTS.md`           | lossy      |
| agents       | `agents/<name>.toml`                   | converted  |
| mcp          | `config.toml` → `[mcp_servers.<name>]` | converted  |
| others       | unsupported                            | —          |

MCP servers become `[mcp_servers.<name>]` tables in `config.toml`, edited in place with comments, ordering, and other tables kept ([decision 0004](../decisions/0004-toml-entries-with-tomlkit.md)). Codex rejects fields it does not know, so only these are written: `command`, `args`, `env`, `cwd` for stdio servers; `url` and `headers` (as `http_headers`) for streamable HTTP servers; `tools` as `enabled_tools` unless it contains `"*"`. `type` is checked, not written: SSE servers and unknown types are refused, as are carried fields of the wrong type. Losses: any other field, and `${...}` variables, which Codex does not expand. A server with the same name that SkillSinc did not write blocks until `--adopt`; invalid TOML, or an `mcp_servers` that is not a table, blocks the destination. Hooks are not written.

Agents become [custom agents](https://developers.openai.com/codex/subagents) at `agents/<name>.toml` with `name`, `description`, and the body as `developer_instructions`, the three keys Codex requires. The name is lowercased and every character other than a letter or digit becomes `_` (`Code-Review` becomes `code_review`), following Codex's examples and ai-feature-sync. Windows line endings in the body become plain newlines. An agent without a text description or with an empty body is refused. Codex's own `model`, `sandbox_mode`, and similar keys are not written; they inherit from the parent session. Losses: agent `tools`, `model`, `handoffs`, and other Copilot fields; VS Code `${...}` variables in the body.

Verified 2026-10-21 on Windows with Codex 0.159.3 and the library above: `codex mcp list` showed the routed servers, and both custom agents were listed. The `AGENTS.md` block was written, but nobody checked whether Codex reads it.

## agents

Default path `~/.agents`. Skills only, copied exactly to `skills/<name>/`. Codex, Copilot, and other [Agent Skills](https://agentskills.io/specification) readers discover this folder.

Verified 2026-10-21 on Windows: Codex 0.159.3 and Copilot CLI 1.0.101 each loaded all 22 skills from `~/.agents/skills`.

## generic

No default path. Copies the library layout verbatim: `skills/<name>/`, and `<kind>/<original file name>` for everything else, including `mcp/<name>.json` and `hooks/<name>.json`. Useful for exports and backups.
