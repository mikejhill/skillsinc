# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Rebuilt from scratch around a smaller scope; see [decision 0001](docs/decisions/0001-rebuild-scope.md).
- Package metadata lists the author by name only; the personal email address is removed.
- `skillsinc init` without `--source` now points at, and creates, a default library at `$XDG_CONFIG_HOME/skillsinc/library` (`~/.config/skillsinc/library`) instead of a `~/agent-library` placeholder.

### Added

- Codex agents: library agents sync to `~/.codex/agents/<name>.toml` custom agents with `name` (lowercase snake_case), `description`, and `developer_instructions`; Copilot-only fields such as `tools` and `model` are reported as losses.
- [Scheduling examples](docs/guides/scheduling.md) for Windows Task Scheduler (registration script), macOS launchd, and a Linux systemd user timer.
- Codex MCP servers: library servers sync into `[mcp_servers.<name>]` tables in `config.toml`, keeping the user's comments, layout, and other tables. Only fields Codex reads are written; SSE servers are refused and other fields are reported as losses. Adds the `tomlkit` dependency ([decision 0004](docs/decisions/0004-toml-entries-with-tomlkit.md)).
- Resources: `resources/<name>.md` reference files are copied byte for byte into `~/.copilot/resources` and `~/.claude/resources`; Claude Code also gets a managed block of `@resources/<name>.md` imports in `CLAUDE.md` ([decision 0003](docs/decisions/0003-resources-and-claude-imports.md)).
- Plugins: `plugins/<plugin>/` directories in Claude Code's layout expand into skills, agents, prompts, and MCP servers for every profile; unsynced parts such as hooks are reported as losses, and items needing `${CLAUDE_PLUGIN_ROOT}` are refused ([decision 0005](docs/decisions/0005-plugins-expand-into-kinds.md)). Routes can select a whole plugin with `plugins/<plugin>/*`.
- Hooks: `hooks/<name>.json` library files in the Copilot CLI hook format are copied to `~/.copilot/hooks` and converted into owned, per-event `hooks` entries in `~/.claude/settings.json`, merging every file that uses an event and reporting what Claude Code cannot carry ([decision 0006](docs/decisions/0006-hooks.md)).
- Claude Code MCP servers: library servers sync into `mcpServers` in `~/.claude.json` (or `<root>/.claude.json` for `$CLAUDE_CONFIG_DIR` and other roots) with an explicit transport `type`, reporting dropped fields ([decision 0007](docs/decisions/0007-files-beside-the-root.md)). Profiles can declare entry files beside their root, and rewritten shared files keep their permissions.
- VS Code MCP servers: library servers sync into `servers` in `Code/User/mcp.json`, beside the prompts folder. The file is edited in place, so comments, trailing commas, and layout are kept ([decision 0008](docs/decisions/0008-jsonc-edits-in-place.md)).
- MCP servers: `mcp/<name>.json` library files sync into Copilot CLI's `mcp-config.json` as owned `mcpServers` entries, leaving the user's other servers and keys untouched ([decision 0002](docs/decisions/0002-mcp-server-entries.md)). Manifests move to version 2; version 1 manifests load unchanged.
- [Gap list](docs/planning/gap-list.md) of what remains to sync skills, plugins, instructions, and MCP servers across harnesses, including parity with the earlier ai-feature-sync tool.
- `init`, `plan`, `sync`, and `profiles` commands.
- Profiles for Claude Code, Copilot CLI, VS Code, Codex, the shared `~/.agents` folder, and generic copies.
- Ownership manifests, drift repair, `--adopt`, lossy-conversion opt-in, managed `AGENTS.md` block, and a run lock.
- Command-line, development, and roadmap guides for contributors.

### Fixed

- A changed executable flag on an otherwise identical file is now synced.
- A managed block edited by hand is released, not deleted, when its sections are removed.
- Recreating a deleted shared file records that SkillSinc created it, so it is deleted again when its block goes away.
- Adopting an unowned block with different content is reported as `adopt`, and restoring a hand-deleted block is noted.
- A path rendered as both a whole file and a managed block blocks the destination instead of writing twice.
