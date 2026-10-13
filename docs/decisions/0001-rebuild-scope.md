# 0001: Rebuild SkillSinc with a smaller, verifiable scope

This record explains why the first implementation was replaced and what was cut. It is for anyone wondering where a removed feature went.

- **Status:** Accepted
- **Date:** 2026-10-13

## Context

The first implementation (three commits, about 10,700 lines) tried to cover eleven harness surfaces, Git sources, a filesystem watcher, OS login services, SQLite ownership with journals, entry-level JSON/JSONC/TOML editing, transformation pipelines, and an executable plugin protocol. Its own roadmap noted that none of the harness behavior had been verified natively. Documentation was dense, spread flat across `docs/`, and mixed requirements with release evidence.

The core idea was sound: one authored library, source-authoritative sync, explicit ownership, no silent losses.

## Decision

Rebuild around that core and cut everything not needed to deliver it safely today.

| Kept                                                     | Cut or deferred                          | Why                                                                   |
| -------------------------------------------------------- | ---------------------------------------- | --------------------------------------------------------------------- |
| Local library sources                                    | Git source provider                      | A local clone plus `git pull` does the same with no new failure modes |
| Claude, Copilot, VS Code, Codex, `~/.agents`, generic    | Cursor, Gemini, OpenCode profiles        | Not installed or verifiable here; a profile is one small class to add |
| Skills, instructions, agents, prompts                    | MCP servers and hooks                    | Need key-level edits of shared config files with differing semantics  |
| Route `include`/`exclude` filters and `allow_lossy`      | Transformation pipelines and plugins     | The library is authored in the target format; profiles convert        |
| JSON manifests, per-file atomic writes, preconditions    | SQLite, journals, commit groups          | Same guarantees for whole files with far less machinery               |
| One managed Markdown block                               | Entry-preserving JSON/JSONC/TOML editing | Only needed for MCP and hooks                                         |
| Lock for overlapping runs; scheduler-friendly exit codes | Watch mode and login services            | A scheduler calling `sync` is simpler and observable                  |

## Consequences

- The codebase is small enough to read in one sitting, and every behavior has a test.
- MCP and hook distribution is the most valuable deferred feature. It should arrive as a separate design that defines key-level ownership for each harness's config format.
- Reinstating any cut feature requires a new decision record.
