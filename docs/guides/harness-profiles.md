# Harness profiles

This guide lists exactly what each profile writes and which conversions lose information. Use it to decide which routes need `allow_lossy = true`.

## Conventions

- **exact**: the library file is copied byte for byte.
- **converted**: frontmatter is rewritten for the harness; losses are reported per item.
- Library files use the GitHub Copilot formats: `applyTo` on instructions, `tools`/`model` on agents, `${input:...}` variables in prompts.
- MCP servers are `mcp/<name>.json` files, each one server object in the `mcpServers` entry format (`type`, `command`, `args`, `env`, or `url`, `headers`). SkillSinc owns only its own keys inside a shared JSON file; see [decision 0002](../decisions/0002-mcp-server-entries.md).

## Plugins

Library plugins (`plugins/<plugin>/`, Claude Code layout) are expanded into skills, agents, prompts, and MCP servers before any profile sees them ([decision 0003](../decisions/0003-plugins-expand-into-kinds.md)). Each harness then receives the parts it supports, without the plugin namespace (`kit:reviewer` becomes `reviewer`).

| Plugin part          | Becomes     | `claude`                         | `copilot`, `vscode`                                                          |
| -------------------- | ----------- | -------------------------------- | ---------------------------------------------------------------------------- |
| `skills/<name>/`     | skill       | exact                            | exact (`copilot`)                                                            |
| `agents/<name>.md`   | agent       | exact                            | keeps `name`, `description`; other fields are losses                         |
| `commands/<name>.md` | prompt      | exact                            | `vscode` keeps `description`, `argument-hint`; `$ARGUMENTS`, `$1` are losses |
| `.mcp.json`          | MCP servers | refused (as for library items)   | `copilot` entries                                                            |
| everything else      | —           | loss on every item of the plugin | loss on every item of the plugin                                             |

Items that refer to `${CLAUDE_PLUGIN_ROOT}`, another `CLAUDE_PLUGIN_*` variable, or `${user_config.*}` are skipped everywhere: only an installed plugin resolves them.

## claude

Default path `$CLAUDE_CONFIG_DIR` or `~/.claude`. Reference: [Claude Code memory and rules](https://code.claude.com/docs/en/memory).

| Kind         | Output               | Conversion                                             |
| ------------ | -------------------- | ------------------------------------------------------ |
| skills       | `skills/<name>/`     | exact                                                  |
| instructions | `rules/<name>.md`    | `applyTo` → `paths`; `**` → no `paths` (always loaded) |
| agents       | `agents/<name>.md`   | keeps `name`, `description`; requires a description    |
| prompts      | `commands/<name>.md` | keeps `description`, `argument-hint`                   |

MCP servers are not written: Claude Code keeps user servers in `~/.claude.json`, outside this destination, and rewrites that file while running.

Losses: instructions without `applyTo` (on demand in Copilot, always loaded in Claude); `excludeAgent`; agent `tools`, `model`, `handoffs`, and other Copilot fields; prompt `agent`, `mode`, `model`, `tools`; VS Code `${...}` variables in prompt bodies.

## copilot

Default path `$COPILOT_HOME` or `~/.copilot`. Reference: [Copilot CLI custom instructions](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions).

| Kind         | Output                                  | Conversion |
| ------------ | --------------------------------------- | ---------- |
| skills       | `skills/<name>/`                        | exact      |
| instructions | `instructions/<name>.instructions.md`   | exact      |
| agents       | `agents/<name>.agent.md`                | exact      |
| prompts      | unsupported                             | —          |
| mcp          | `mcp-config.json` → `mcpServers.<name>` | exact      |

Loss: an instructions file without `applyTo`, which Copilot CLI never applies.

MCP servers are copied as written into `mcpServers` in `~/.copilot/mcp-config.json`; other servers and keys in the file are kept, and the file is rewritten with two-space indentation. A server with the same name that SkillSinc did not write blocks the destination until `--adopt`. A file that is not plain JSON (comments, duplicate keys) blocks it too. Write each server in the shape Copilot CLI documents; SkillSinc does not convert fields.

## vscode

Default path: the VS Code user `prompts` folder (`%APPDATA%\Code\User\prompts`, `~/Library/Application Support/Code/User/prompts`, or `$XDG_CONFIG_HOME/Code/User/prompts`). Instructions, agents, and prompts are copied exactly with their `.instructions.md`, `.agent.md`, and `.prompt.md` suffixes. VS Code reads skills from `~/.copilot/skills` and `~/.agents/skills`, so route skills to a `copilot` or `agents` destination. MCP servers are not written: VS Code reads them from `User/mcp.json` (key `servers`, comments allowed), outside the prompts folder.

## codex

Default path `$CODEX_HOME` or `~/.codex`. Instructions are combined, in name order, into a managed block in `AGENTS.md`; the rest of the file is preserved. Codex reads user skills from `~/.agents/skills` ([Codex skills](https://developers.openai.com/codex/skills)), so route skills to an `agents` destination.

Losses: `applyTo` globs become an advisory sentence; files without `applyTo` become always-on.

MCP servers are not written: Codex reads them from `[mcp_servers.<name>]` tables in `config.toml`, which needs a TOML editor that preserves formatting.

## agents

Default path `~/.agents`. Skills only, copied exactly to `skills/<name>/`. Codex, Copilot, and other [Agent Skills](https://agentskills.io/specification) readers discover this folder.

## generic

No default path. Copies the library layout verbatim: `skills/<name>/`, and `<kind>/<original file name>` for everything else, including `mcp/<name>.json`. Useful for exports and backups.
