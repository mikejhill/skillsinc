# Gap list: full sync between harnesses

This list records what SkillSinc still lacks to sync Agent Skills, plugins, instructions, and MCP servers between every harness in its profiles. It is for choosing the next feature. It compares SkillSinc with its own documented scope ([decision 0001](../decisions/0001-rebuild-scope.md), [harness profiles](../guides/harness-profiles.md)) and with `ai-feature-sync`, the private local tool SkillSinc replaces (reviewed 2026-10-20; not in this repository).

## Coverage today (2026-10-20)

| Kind         | Claude Code       | Codex                    | Copilot CLI       | VS Code                | `~/.agents` |
| ------------ | ----------------- | ------------------------ | ----------------- | ---------------------- | ----------- |
| Skills       | yes               | via `agents`             | yes               | via `copilot`/`agents` | yes         |
| Instructions | yes (`rules/`)    | yes (lossy, `AGENTS.md`) | yes               | yes                    | —           |
| Agents       | yes (lossy)       | yes (lossy, TOML)        | yes               | yes                    | —           |
| Prompts      | yes (`commands/`) | no harness format        | no harness format | yes                    | —           |
| MCP servers  | yes (new)         | yes (converted)          | yes               | **gap**                | —           |
| Hooks        | yes (lossy, new)  | **gap**                  | yes (new)         | via `copilot`/`claude` | —           |
| Resources    | yes (new)         | —                        | yes (new)         | —                      | —           |
| Plugins      | expanded          | via `agents`             | expanded          | expanded               | skills only |

## Parity with ai-feature-sync

| ai-feature-sync feature                                                 | SkillSinc                                                     |
| ----------------------------------------------------------------------- | ------------------------------------------------------------- |
| Skills, agents, prompts, instructions into `~/.copilot` and `~/.claude` | Covered, with format conversion instead of renaming           |
| Codex skills                                                            | Covered through the `agents` destination (`~/.agents/skills`) |
| Codex agents: `*.agent.md` → `~/.codex/agents/<name>.toml`              | Covered, with the same snake_case names                       |
| Hook files (`*.json`) copied to `~/.copilot/hooks`                      | Covered, plus conversion into Claude Code `settings.json`     |
| Resources (`*.md`) copied to `~/.copilot/resources`                     | Covered: `resources` kind (decision 0003)                     |
| `claude_md_import`: managed `@`-import block in `CLAUDE.md`             | Covered: resources imported from `~/.claude/CLAUDE.md`        |
| Prompts copied to `~/.copilot/prompts`                                  | Refused on purpose: Copilot CLI documents no prompt folder    |
| Junctions and hard links                                                | Not planned: copies plus ownership are safer (decision 0001)  |
| Optional `--prune`                                                      | Covered: owned files are always reconciled, edits released    |
| Windows Task Scheduler registration script                              | Covered: [scheduling examples](../guides/scheduling.md)       |

ai-feature-sync has no MCP, plugin, or VS Code support, so those gaps come from the documented scope only.

## Gaps, highest value first

1. ~~**Codex agents.**~~ Done: agents render into `~/.codex/agents/<name>.toml` with `name`, `description`, and `developer_instructions`, written with `tomlkit`; names are lowercase snake_case as in ai-feature-sync, and `tools`, `model`, and other fields are reported as losses.
2. ~~**Hooks.**~~ Done for Copilot CLI and Claude Code ([decision 0006](../decisions/0006-hooks.md)). Remaining: Codex hooks, once its user hook location is confirmed against current Codex docs.
3. ~~**Resources and Claude imports.**~~ Done: a `resources` kind copied to `~/.copilot/resources` and `~/.claude/resources`, with a managed `@`-import block in `~/.claude/CLAUDE.md` ([decision 0003](../decisions/0003-resources-and-claude-imports.md)).
4. ~~**Claude Code MCP servers.**~~ Done: `mcpServers` in `~/.claude.json` through files declared beside the root ([decision 0007](../decisions/0007-files-beside-the-root.md)).
5. **VS Code MCP servers.** Target `servers` in `Code/User/mcp.json`. The outside-root mechanism from decision 0007 covers the location; still needs a choice between comment-preserving JSONC edits and refusing files with comments (today's behavior).
6. ~~**Codex MCP servers.**~~ Done: `[mcp_servers.<name>]` in `config.toml`, edited with `tomlkit` ([decision 0004](../decisions/0004-toml-entries-with-tomlkit.md)).
7. ~~**Plugins.**~~ Done: library plugins expand into skills, agents, prompts, and MCP servers ([decision 0005](../decisions/0005-plugins-expand-into-kinds.md)). Hooks inside plugins wait on gap 2; an opt-in local marketplace profile stays possible behind a new decision.
8. **More harnesses.** Cursor, Gemini CLI, OpenCode (see the [roadmap](roadmap.md)). Each needs its MCP location too.
9. ~~**Scheduling helper.**~~ Done: [scheduling examples](../guides/scheduling.md) for Task Scheduler, launchd, and a systemd user timer.
10. **Native verification.** None of the mappings, including the new Copilot MCP entries, have been confirmed by loading the harness (roadmap item 2).
