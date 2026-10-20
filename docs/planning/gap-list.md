# Gap list: full sync between harnesses

This list records what SkillSinc still lacks to sync Agent Skills, plugins, instructions, and MCP servers between every harness in its profiles. It is for choosing the next feature. It compares SkillSinc with its own documented scope ([decision 0001](../decisions/0001-rebuild-scope.md), [harness profiles](../guides/harness-profiles.md)) and with `ai-feature-sync`, the private local tool SkillSinc replaces (reviewed 2026-10-20; not in this repository).

## Coverage today (2026-10-20)

| Kind         | Claude Code       | Codex                    | Copilot CLI       | VS Code                | `~/.agents` |
| ------------ | ----------------- | ------------------------ | ----------------- | ---------------------- | ----------- |
| Skills       | yes               | via `agents`             | yes               | via `copilot`/`agents` | yes         |
| Instructions | yes (`rules/`)    | yes (lossy, `AGENTS.md`) | yes               | yes                    | —           |
| Agents       | yes (lossy)       | **gap** (TOML agents)    | yes               | yes                    | —           |
| Prompts      | yes (`commands/`) | no harness format        | no harness format | yes                    | —           |
| MCP servers  | **gap**           | **gap**                  | yes (new)         | **gap**                | —           |
| Hooks        | **gap**           | **gap**                  | **gap**           | **gap**                | —           |
| Resources    | **gap**           | —                        | **gap**           | —                      | —           |
| Plugins      | **gap**           | **gap**                  | **gap**           | **gap**                | —           |

## Parity with ai-feature-sync

| ai-feature-sync feature                                                  | SkillSinc                                                     |
| ------------------------------------------------------------------------ | ------------------------------------------------------------- |
| Skills, agents, prompts, instructions into `~/.copilot` and `~/.claude`  | Covered, with format conversion instead of renaming           |
| Codex skills                                                             | Covered through the `agents` destination (`~/.agents/skills`) |
| Codex agents: `*.agent.md` → `~/.codex/agents/<name>.toml`               | **Gap 1**                                                     |
| Hook files (`*.json`) copied to `~/.copilot/hooks`                       | **Gap 2**                                                     |
| Resources (`*.md`) copied to `~/.copilot/resources`                      | **Gap 3**                                                     |
| `claude_md_import`: managed `@`-import block in `CLAUDE.md`              | **Gap 3** (the block mechanism exists; no profile uses it)    |
| Prompts copied to `~/.copilot/prompts`                                   | Refused on purpose: Copilot CLI documents no prompt folder    |
| Junctions and hard links                                                 | Not planned: copies plus ownership are safer (decision 0001)  |
| Optional `--prune`                                                       | Covered: owned files are always reconciled, edits released    |
| Windows Task Scheduler registration script                               | Covered: [scheduling examples](../guides/scheduling.md)       |

ai-feature-sync has no MCP, plugin, or VS Code support, so those gaps come from the documented scope only.

## Gaps, highest value first

1. **Codex agents.** Render agents into `~/.codex/agents/<name>.toml` with `name`, `description`, and the body as `developer_instructions`; report `tools`, `model`, and other fields as losses. ai-feature-sync already does this, so it is the most direct regression. Needs a small, safe TOML string writer (the standard library only reads TOML).
2. **Hooks.** A `hooks` kind. Copilot CLI hook files are whole JSON files under `~/.copilot/hooks` (plain copies). Claude Code reads `hooks` from `~/.claude/settings.json`, inside its root, so the entry mechanism from [decision 0002](../decisions/0002-mcp-server-entries.md) applies. Events differ across harnesses, so most conversions are lossy.
3. **Resources and Claude imports.** A `resources` kind of plain reference files, copied to `~/.copilot/resources`, plus for Claude a managed `@`-import block in `CLAUDE.md` naming them (the block mechanism already exists for Codex).
4. **Claude Code MCP servers.** Target `mcpServers` in `~/.claude.json` (or `$CLAUDE_CONFIG_DIR/.claude.json`). Needs a profile to name one file outside its root, with the same link and overlap checks. Claude Code rewrites this file while running, so expect precondition conflicts; the applier already fails closed and a rerun succeeds.
5. **VS Code MCP servers.** Target `servers` in `Code/User/mcp.json`. Same outside-root need, plus a choice between comment-preserving JSONC edits and refusing files with comments (today's behavior).
6. **Codex MCP servers.** Target `[mcp_servers.<name>]` in `~/.codex/config.toml`. Needs a formatting-preserving TOML editor (for example `tomlkit`); adding a dependency needs a decision record. `command`, `args`, and `env` carry over; check remote `url` servers against current Codex docs.
7. **Plugins.** Bundles of skills, agents, commands, hooks, and MCP servers with a manifest (`.claude-plugin/plugin.json` for Claude Code). Two options: expand a library plugin into its component kinds per harness (works with every profile, loses the bundle), or publish a local marketplace directory for harnesses that install plugins. Needs a decision; expanding first is cheaper and reuses everything above.
8. **More harnesses.** Cursor, Gemini CLI, OpenCode (see the [roadmap](roadmap.md)). Each needs its MCP location too.
9. **Native verification.** None of the mappings, including the new Copilot MCP entries, have been confirmed by loading the harness (roadmap item 2).
