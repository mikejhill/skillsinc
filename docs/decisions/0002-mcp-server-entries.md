# 0002: MCP servers as owned entries in shared JSON files

This record defines how SkillSinc writes MCP servers into harness configuration files it shares with the user and the harness. It is for anyone extending MCP support to another harness.

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

[Decision 0001](0001-rebuild-scope.md) deferred MCP servers because they live inside shared configuration files, not in files SkillSinc can own whole. The ownership model (record what was written, never touch the rest, block on anything unexpected) must hold at key level.

Where each harness reads user MCP servers:

| Harness     | File                               | Container            | Format | Inside the destination root?        |
| ----------- | ---------------------------------- | -------------------- | ------ | ----------------------------------- |
| Copilot CLI | `~/.copilot/mcp-config.json`       | `mcpServers`         | JSON   | yes                                 |
| Claude Code | `~/.claude.json`                   | `mcpServers`         | JSON   | no (sibling of `~/.claude`)         |
| VS Code     | `Code/User/mcp.json`               | `servers`            | JSONC  | no (parent of the prompts folder)   |
| Codex       | `~/.codex/config.toml`             | `[mcp_servers.<n>]`  | TOML   | yes                                 |

## Decision

- **Library format.** One server per `mcp/<name>.json` file, holding the server object in the `mcpServers` entry shape. It must be a JSON object with exactly one of `command` or `url`; duplicate keys fail the source.
- **Ownership unit.** An entry is `container/key` inside one file. The manifest (version 2) stores, per file, the digest of each owned entry's value (canonical JSON, so key order and whitespace never count as drift) and whether SkillSinc created the file. Version 1 manifests load with no entries.
- **Ownership rules.** The existing table applies per entry: create, update (noting local edits), block an unowned key until `--adopt`, delete an unchanged removed entry, release an edited one. A file SkillSinc created is deleted, and its emptied container dropped, when the last entry goes.
- **Writing.** Each entry change is planned against the bytes the previous change produced, so the applier's precondition check still catches any concurrent edit. The file is re-serialized with two-space indentation; all other keys and their order are kept.
- **Fail closed.** A file that is not a plain JSON object (comments, trailing commas, duplicate keys, `NaN`, or a number a rewrite would change, such as `1e400`) or whose container is not an object (including `null`) blocks the destination. SkillSinc never guesses at JSONC or TOML.
- **First harness.** Copilot CLI, the only JSON target inside its destination root. The others refuse MCP items with the reason, until each gets its own follow-up:
  - Claude Code and VS Code need a way for a profile to write one named file outside its root, and Claude Code rewrites `~/.claude.json` while running.
  - VS Code needs comment-preserving JSONC edits, or a rule that SkillSinc only edits comment-free files.
  - Codex needs a formatting-preserving TOML editor, which means a new dependency (done in [decision 0004](0004-toml-entries-with-tomlkit.md)).

## Consequences

- Hooks and other settings that live in JSON files inside a destination (for example `~/.claude/settings.json`) can reuse the same entry mechanism.
- Users whose `mcp-config.json` uses unusual formatting see it normalized on the first sync that changes an entry.
- No new runtime dependency.
