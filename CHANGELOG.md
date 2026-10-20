# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Rebuilt from scratch around a smaller scope; see [decision 0001](docs/decisions/0001-rebuild-scope.md).
- Package metadata lists the author by name only; the personal email address is removed.

### Added

- Resources: `resources/<name>.md` reference files are copied byte for byte into `~/.copilot/resources` and `~/.claude/resources`; Claude Code also gets a managed block of `@resources/<name>.md` imports in `CLAUDE.md` ([decision 0003](docs/decisions/0003-resources-and-claude-imports.md)).
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
