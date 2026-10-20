# Gap list: full sync between harnesses

This list records what SkillSinc still lacks to sync Agent Skills, plugins, instructions, and MCP servers between every harness in its profiles. It is for choosing the next feature. The earlier comparison target, `ai-feature-sync`, is a local project outside this repository, so the comparison here is against SkillSinc's own documented scope ([decision 0001](../decisions/0001-rebuild-scope.md), [harness profiles](../guides/harness-profiles.md)).

## Coverage today (2026-10-20)

| Kind         | Claude Code       | Codex                    | Copilot CLI       | VS Code                | `~/.agents` |
| ------------ | ----------------- | ------------------------ | ----------------- | ---------------------- | ----------- |
| Skills       | yes               | via `agents`             | yes               | via `copilot`/`agents` | yes         |
| Instructions | yes (`rules/`)    | yes (lossy, `AGENTS.md`) | yes               | yes                    | —           |
| Agents       | yes (lossy)       | no                       | yes               | yes                    | —           |
| Prompts      | yes (`commands/`) | no                       | no harness format | yes                    | —           |
| MCP servers  | **gap**           | **gap**                  | yes (new)         | **gap**                | —           |
| Hooks        | **gap**           | **gap**                  | **gap**           | **gap**                | —           |
| Plugins      | **gap**           | **gap**                  | **gap**           | **gap**                | —           |

## Gaps, highest value first

1. **Claude Code MCP servers.** Target `mcpServers` in `~/.claude.json` (or `$CLAUDE_CONFIG_DIR/.claude.json`). Needs a profile to name one file outside its root, with the same link and overlap checks. Claude Code rewrites this file while running, so expect precondition conflicts; the applier already fails closed and a rerun succeeds.
2. **VS Code MCP servers.** Target `servers` in `Code/User/mcp.json`. Same outside-root need, plus a choice between comment-preserving JSONC edits and refusing files with comments (today's behavior).
3. **Codex MCP servers.** Target `[mcp_servers.<name>]` in `~/.codex/config.toml`. Needs a formatting-preserving TOML editor (for example `tomlkit`); adding a dependency needs a decision record. Field mapping: `command`/`args`/`env` carry over; remote `url` servers need checking against current Codex docs.
4. **Hooks.** Claude Code reads `hooks` from `~/.claude/settings.json`, inside its root, so the entry mechanism from [decision 0002](../decisions/0002-mcp-server-entries.md) applies directly. Needs a library format and per-harness event-name mapping; events differ across harnesses, so most conversions are lossy.
5. **Plugins.** Bundles of skills, agents, commands, hooks, and MCP servers with a manifest (`.claude-plugin/plugin.json` for Claude Code). Two options: expand a library plugin into its component kinds per harness (works with every profile, loses the bundle), or publish a local marketplace directory for harnesses that install plugins. Needs a decision; expanding first is cheaper and reuses everything above.
6. **Codex agents and prompts.** Codex has no user-level agent format in the profiles; verify whether current Codex releases read custom prompts before adding a mapping.
7. **More harnesses.** Cursor, Gemini CLI, OpenCode (see the [roadmap](roadmap.md)). Each needs its MCP location too.
8. **Native verification.** None of the mappings, including the new Copilot MCP entries, have been confirmed by loading the harness (roadmap item 2).
