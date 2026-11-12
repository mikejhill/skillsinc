# 0021: Project configurations

Status: Accepted

## Context

Users want to sync a chosen set of skills, instructions, and MCP servers into one repository (`.claude/`, `.github/`, `AGENTS.md`, ...) as well as into their user folders, typically from a task runner such as `poe sync-skills`. The convention requested is a configuration at `./.config/skillsinc/config.json`.

The built-ins almost allow this with `--config`, but not safely or conveniently:

- Destinations without `path` default to the user folders (`~/.claude`), so a project file that omits `path` writes into the user's harness folders.
- Relative paths resolve against the file's directory, so a project file needs `../../.claude` everywhere.
- Manifests live in one state directory keyed by destination name. A project `claude` and the user `claude` share `manifests/claude.json`; the second sync refuses with "destination moved". Two projects collide the same way.
- Harnesses read different files in a repository than in their user folders: Claude Code reads servers from `.mcp.json`, Codex reads `AGENTS.md` at the repository root and `.codex/config.toml`, Copilot reads `.github/` (including `.github/mcp.json` and, for VS Code, `.github/prompts`), and VS Code reads servers from `.vscode/mcp.json`. The user-level profiles would write `.claude/.claude.json`, `.codex/AGENTS.md`, and `.github/mcp-config.json`, which nothing loads.

So the pattern needs core support.

## Decision

**Project scope.** A configuration file belongs to a project when it sits at `<root>/.config/skillsinc/config.json` (or `config.jsonc`), `<root>` is not the home directory, and it is not the user configuration (`$XDG_CONFIG_HOME/skillsinc/config.json`). Any other file opts in with a top-level `"project"` key naming the root relative to the file. Detecting the conventional location instead of requiring a key removes the risky case: a hand-written project file that forgets the key would otherwise sync into the user's folders.

In a project:

- relative paths resolve against the project root;
- a destination without `path` defaults to its profile's project folder: `claude` → `.claude`, `copilot` → `.github`, `vscode` → `.vscode`, `codex` → `.codex`, `agents` → `.agents`; `generic` still needs `path`;
- state (manifests, journals, the run lock) lives in `<root>/.config/skillsinc/state/`, which SkillSinc creates with a `.gitignore` of `*`, so it never reaches the repository. Remote source checkouts stay in the shared user cache, which has its own per-remote lock;
- manifests record their root and configuration relative to the project, so moving or renaming the checkout keeps its state.

**Discovery.** Without `--config`, `plan`, `sync`, and `status` use the nearest `.config/skillsinc/config.json` in the working directory or its parents, stopping below the home directory, and fall back to the user configuration. `--user` forces the user configuration. `init --project` writes the conventional file in the working directory. `schedule install` keeps its behaviour: without `--config`, the job runs from the scheduler's working directory (home, `/`, or `System32`), which finds the user configuration.

**Project layouts.** Profiles take an optional project root. In a project, Claude Code servers go to `../.mcp.json`; Codex instructions go to the root `../AGENTS.md`; Copilot writes `prompts/<name>.prompt.md` (VS Code's repository prompt folder) and servers to `mcp.json`; VS Code writes only `.vscode/mcp.json` and refuses other kinds, which it reads from the `copilot` destination's `.github`. Copilot in a repository also reads `.claude/skills` and `.agents/skills`, so its project `covered_by` includes `claude`, and VS Code's `also_reads` includes `copilot` prompts. Everything else matches the user layout.

Rejected: a `--project` flag without discovery (every task and shell needs it, and forgetting it syncs the user configuration from inside a project); state in `$XDG_STATE_HOME` keyed by a hash of the project path (orphaned when a checkout is deleted, lost when it is moved); one `.github` destination for both Copilot and VS Code with overlap checks relaxed (ownership of a shared directory would clash).

## Consequences

Running `skillsinc plan` or `sync` inside a directory tree with a project configuration now uses that file. The report starts with `Project: <root>`, and the JSON report gains `project` (null for the user configuration). A project's AGENTS.md and `.claude/CLAUDE.md` content is a managed block, so authored text around it is kept. Project state is per checkout: a fresh clone that committed generated files starts with them unmanaged, and its first sync blocks until `--adopt`. The `profiles` table still shows user-level summaries; project folders are documented in [harness profiles](../guides/harness-profiles.md#project-folders).
