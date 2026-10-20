# Philosophy and goals

This document states why SkillSinc exists, what it promises, and what it deliberately leaves out. Read it before proposing features: every change should serve these goals without weakening the safety rules.

## Problem

People who use several AI coding harnesses maintain the same skills and instructions in several places: `~/.claude`, `~/.copilot`, a VS Code profile folder, `~/.codex/AGENTS.md`, `~/.agents`. Copies drift, symlinks break or get followed into places they should not, and each harness wants slightly different files.

## Goal

One library, authored once, kept in sync with every harness the user chooses, with no surprises.

## Principles

1. **The library is the source of truth.** Destinations converge on it. Edits made directly inside managed files are overwritten (and the plan says so).
2. **Own only what you wrote.** SkillSinc records each file and block it writes. It never edits, deletes, or adopts anything else without `--adopt`.
3. **Plan, then apply.** The plan is computed in full before any write; `sync` applies exactly that plan and re-checks each file just before changing it.
4. **Fail closed.** An unreadable source, a damaged managed block, a link in a destination path, or an unmanaged file in the way blocks that destination. Other destinations still sync.
5. **Make losses visible.** A conversion that drops meaning is reported and skipped unless the route explicitly accepts it. Fields that look alike across harnesses are not assumed to mean the same thing.
6. **Small and boring.** Plain files, a TOML config, JSON manifests, standard library plus PyYAML. One command to preview, one to apply.

## Priorities

When goals conflict, earlier wins:

1. Never lose user data.
2. Correct output in each harness.
3. Clear, predictable behavior.
4. Breadth of harnesses and kinds.

## Non-goals

- Watching for changes or running as a service. A scheduler calling `skillsinc sync` covers this without a resident process.
- Fetching sources from Git. Keep the library in a local clone and update it with Git.
- Transformation pipelines or plugins. Write the library in the format you want; profiles do the necessary conversion.
- Merging MCP server or hook settings into harness configuration files. These live in shared JSON/TOML documents with harness-specific semantics; see the [rebuild decision](../decisions/0001-rebuild-scope.md).
