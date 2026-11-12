# Project configurations

Starting with **0.6.0**, a repository can carry its own SkillSinc configuration that syncs chosen skills, instructions, agents, prompts, and MCP servers into that repository's harness folders instead of your user folders ([decision 0021](../decisions/0021-project-configurations.md)).

## Quick start

```bash
cd ~/src/my-app
skillsinc init --project --source ~/.config/skillsinc/library
skillsinc plan
skillsinc sync
```

`init --project` writes `.config/skillsinc/config.json`. Inside the project (any subdirectory), `plan`, `sync`, and `status` use that file automatically. Pass `--user` to use your user configuration from inside a project, or `--config PATH` for any other file.

Commit `.config/skillsinc/config.json`. SkillSinc keeps its state in `.config/skillsinc/state/` and writes a `.gitignore` there, so state never reaches Git.

## What changes in a project

| Aspect                     | User configuration                       | Project configuration                     |
| -------------------------- | ---------------------------------------- | ----------------------------------------- |
| File                       | `$XDG_CONFIG_HOME/skillsinc/config.json` | `<project>/.config/skillsinc/config.json` |
| Found by                   | default                                  | nearest one above the working directory   |
| Relative paths resolve to  | the file's directory                     | the project root                          |
| Destination default `path` | the harness's user folder                | the harness's project folder (below)      |
| State and run lock         | `$XDG_STATE_HOME/skillsinc/`             | `<project>/.config/skillsinc/state/`      |
| Remote source cache        | `$XDG_STATE_HOME/skillsinc/remotes/`     | the same shared cache                     |

Project folders by profile:

| Profile   | Folder     | Also writes                                                       |
| --------- | ---------- | ----------------------------------------------------------------- |
| `claude`  | `.claude/` | servers in `.mcp.json` at the project root                        |
| `copilot` | `.github/` | `prompts/*.prompt.md` (read by VS Code), servers in `mcp.json`    |
| `vscode`  | `.vscode/` | only servers in `mcp.json`; VS Code reads the rest from `.github` |
| `codex`   | `.codex/`  | instructions in `AGENTS.md` at the project root (managed block)   |
| `agents`  | `.agents/` | skills only                                                       |
| `generic` | none       | needs `path`                                                      |

A destination with an explicit `path` goes where you say, relative to the project root. Two configurations never share manifests, so the user `claude` destination and a project `claude` destination are independent, as are two projects.

The project root is the directory holding `.config/`. A file elsewhere becomes a project configuration with a top-level `"project"` key naming the root relative to the file, for example `"project": ".."` in `tools/skillsinc.json`.

## Example

```jsonc
// .config/skillsinc/config.json
{
  "version": 1,
  "sources": {
    "library": {
      "path": "~/.config/skillsinc/library",
      "include": ["skills/python-*", "instructions/python", "mcp/github"],
    },
    "team": { "github": "acme/agent-library", "refresh": "1h" },
  },
  "destinations": {
    "claude": {}, // .claude/ and .mcp.json
    "copilot": {}, // .github/
    "agents": {}, // .agents/ for Codex
  },
  "routes": [
    {
      "sources": ["library", "team"],
      "destinations": ["claude", "copilot", "agents"],
    },
  ],
}
```

## Task runners

Run SkillSinc from a task so contributors sync with one command. With [poethepoet](https://poethepoet.natn.io/) in `pyproject.toml`:

```toml
[tool.poe.tasks]
sync-skills = { cmd = "skillsinc sync", help = "Sync agent skills into this project" }
plan-skills = { cmd = "skillsinc plan", help = "Preview the skill sync" }
```

Poe runs tasks from the project root, so the project configuration is found without `--config`. If SkillSinc is not installed as a tool, use `uvx skillsinc sync`. The same works in `make`, `just`, or npm scripts.

## Things to know

- **Committed outputs.** Whether you commit `.claude/`, `.github/`, and the rest is up to you. State is per checkout, so in a fresh clone with committed outputs the first sync lists them as unmanaged and blocks until you pass `--adopt`, which tracks identical files and overwrites differing ones.
- **Moving the checkout** keeps its state: manifests record paths relative to the project.
- **Scheduling.** `skillsinc schedule install` registers the user configuration. A scheduled job runs outside the project, so it never picks up a project file; pass `--config` to schedule one project, or sync projects from a task instead.
- **Removing destinations** follows the [usual rules](configuration.md#remove-distributed-output) inside the project's own state; user and other projects' manifests are never considered.
- **Duplicate warnings.** Copilot and VS Code read `.claude/skills` and `.agents/skills` as well as `.github/skills`. Copilot skips skills a `claude` or `agents` destination already delivers; when both deliver the same skill, `plan` warns, as at the user level.
