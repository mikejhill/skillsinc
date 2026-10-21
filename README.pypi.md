# SkillSinc

<!-- Generated from README.md by scripts/pypi_readme.py; do not edit. -->

[![CI](https://github.com/mikejhill/skillsinc/actions/workflows/ci.yml/badge.svg)](https://github.com/mikejhill/skillsinc/actions/workflows/ci.yml)
[![Release](https://github.com/mikejhill/skillsinc/actions/workflows/release.yml/badge.svg)](https://github.com/mikejhill/skillsinc/actions/workflows/release.yml)
[![License](https://img.shields.io/github/license/mikejhill/skillsinc)](https://github.com/mikejhill/skillsinc/blob/main/LICENSE)

Write your agent skills, instructions, agents, prompts, MCP servers, reference files, and hooks once. SkillSinc copies them into Claude Code, GitHub Copilot, VS Code, Codex, and the shared `~/.agents` folder, converting formats where it must and saying so when a conversion loses something.

It replaces hand-copying, symlinks, and per-tool sync scripts with one command that shows you every change before it makes it.

## How it works

![How it works](https://raw.githubusercontent.com/mikejhill/skillsinc/main/docs/assets/how-it-works.svg)

- **Your library is the source of truth.** A sync makes each destination match it: new items are added, changed items updated, removed items deleted.
- **SkillSinc only touches what it wrote.** It records every file it owns. An existing file it did not write blocks that destination until you review it and pass `--adopt`.
- **Nothing is written without a plan.** `skillsinc plan` lists every change; `skillsinc sync` applies the same plan.
- **Lossy conversions are opt-in.** If a harness cannot represent a field (for example, Claude ignores Copilot `tools` lists), the item is skipped until the route sets `allow_lossy = true`.
- **A broken source never deletes anything.** If a library cannot be read completely, its destinations are left untouched.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install git+https://github.com/mikejhill/skillsinc.git
```

## Quick start

1. Write a configuration. It enables a destination for every harness folder found on this machine and creates an empty library at `~/.config/skillsinc/library` (`$XDG_CONFIG_HOME/skillsinc/library`). Pass `--source DIR` to use an existing library instead:

    ```bash
    skillsinc init
    ```

2. Preview the changes:

    ```bash
    skillsinc plan
    ```

3. Apply them:

    ```bash
    skillsinc sync
    ```

4. Keep destinations in sync on a schedule (Task Scheduler on Windows, launchd on macOS, a systemd user timer or cron on Linux):

    ```bash
    skillsinc schedule install --interval 5m
    ```

    Installing the package cannot register this for you; run it once per machine. `skillsinc schedule status` shows the last result and `skillsinc schedule remove` undoes it. See [scheduling](https://github.com/mikejhill/skillsinc/blob/main/docs/guides/scheduling.md). An overlapping run exits without writing.

## Library layout

The layout matches GitHub Copilot's file conventions, so an existing Copilot library works unchanged:

```text
my-agent-library/
├── skills/<name>/SKILL.md            # plus any supporting files
├── instructions/<name>.instructions.md
├── agents/<name>.agent.md
├── prompts/<name>.prompt.md
├── mcp/<name>.json                   # one MCP server definition per file
├── resources/<name>.md               # reference files, copied as written
├── hooks/<name>.json                 # one Copilot CLI hook file per file
└── plugins/<plugin>/                 # Claude Code plugin layout, expanded into the kinds above
```

`INTENT.md`, `*.intent.md`, and hidden files are never distributed. A plugin's `skills/`, `agents/`, `commands/`, and `.mcp.json` are synced as skills, agents, prompts, and MCP servers; everything else in it (hooks, scripts, LSP servers) is reported as a loss. See [decision 0005](https://github.com/mikejhill/skillsinc/blob/main/docs/decisions/0005-plugins-expand-into-kinds.md).

## What each harness receives

| Profile   | Skills    | Instructions                 | Agents               | Prompts              | MCP servers                    | Resources                             | Hooks                   |
| --------- | --------- | ---------------------------- | -------------------- | -------------------- | ------------------------------ | ------------------------------------- | ----------------------- |
| `claude`  | `skills/` | `rules/<name>.md` (`paths`)  | `agents/<name>.md`   | `commands/<name>.md` | `~/.claude.json` `mcpServers`  | `resources/` + `CLAUDE.md` `@` import | `settings.json` `hooks` |
| `copilot` | `skills/` | `instructions/`              | `agents/`            | —                    | `mcp-config.json` `mcpServers` | `resources/`                          | `hooks/`                |
| `vscode`  | —         | `<name>.instructions.md`     | `<name>.agent.md`    | `<name>.prompt.md`   | `User/mcp.json` `servers`      | —                                     | —                       |
| `codex`   | —         | managed block in `AGENTS.md` | `agents/<name>.toml` | —                    | —                              | —                                     | —                       |
| `agents`  | `skills/` | —                            | —                    | —                    | —                              | —                                     | —                       |
| `generic` | verbatim  | verbatim                     | verbatim             | verbatim             | verbatim                       | verbatim                              | verbatim                |

Run `skillsinc profiles` for the same table. See [harness profiles](https://github.com/mikejhill/skillsinc/blob/main/docs/guides/harness-profiles.md) for exact conversions.

## Commands

| Command              | Purpose                                                   |
| -------------------- | --------------------------------------------------------- |
| `skillsinc init`     | Write a starter configuration                             |
| `skillsinc plan`     | Show every change a sync would make; write nothing        |
| `skillsinc sync`     | Apply the plan                                            |
| `skillsinc profiles` | List harness profiles and what each kind becomes          |
| `skillsinc schedule` | `install`, `remove`, or show `status` of a scheduled sync |

`plan` and `sync` accept `--destination NAME` (repeatable), `--adopt`, `--json`, `--verbose`, `--log-file PATH`, and `--config PATH`. Exit codes: `0` success, `1` a destination was blocked or failed, `2` configuration or usage error, `130` interrupted.

## Documentation

- [Configuration reference](https://github.com/mikejhill/skillsinc/blob/main/docs/guides/configuration.md)
- [Command-line reference](https://github.com/mikejhill/skillsinc/blob/main/docs/guides/command-line.md)
- [Scheduling](https://github.com/mikejhill/skillsinc/blob/main/docs/guides/scheduling.md)
- [Development guide](https://github.com/mikejhill/skillsinc/blob/main/docs/guides/development.md)
- [Roadmap](https://github.com/mikejhill/skillsinc/blob/main/docs/planning/roadmap.md)
- [Harness profiles](https://github.com/mikejhill/skillsinc/blob/main/docs/guides/harness-profiles.md)
- [Philosophy and goals](https://github.com/mikejhill/skillsinc/blob/main/docs/architecture/philosophy.md)
- [Architecture](https://github.com/mikejhill/skillsinc/blob/main/docs/architecture/overview.md)
- [Decision: rebuild scope](https://github.com/mikejhill/skillsinc/blob/main/docs/decisions/0001-rebuild-scope.md)
- [Decision: MCP server entries](https://github.com/mikejhill/skillsinc/blob/main/docs/decisions/0002-mcp-server-entries.md)
- [Decision: resources and Claude imports](https://github.com/mikejhill/skillsinc/blob/main/docs/decisions/0003-resources-and-claude-imports.md)
- [Decision: TOML entries with tomlkit](https://github.com/mikejhill/skillsinc/blob/main/docs/decisions/0004-toml-entries-with-tomlkit.md)
- [Decision: plugins expand into kinds](https://github.com/mikejhill/skillsinc/blob/main/docs/decisions/0005-plugins-expand-into-kinds.md)
- [Decision: entry files beside a destination root](https://github.com/mikejhill/skillsinc/blob/main/docs/decisions/0007-files-beside-the-root.md)
- [Decision: edit VS Code's JSONC mcp.json in place](https://github.com/mikejhill/skillsinc/blob/main/docs/decisions/0008-jsonc-edits-in-place.md)
- [Decision: one copy of each item per harness](https://github.com/mikejhill/skillsinc/blob/main/docs/decisions/0009-one-copy-per-harness.md)
- [Decision: MCP server options and per-profile overrides](https://github.com/mikejhill/skillsinc/blob/main/docs/decisions/0010-mcp-server-options.md)
- [Contributing](https://github.com/mikejhill/skillsinc/blob/main/CONTRIBUTING.md) · [Security](https://github.com/mikejhill/skillsinc/blob/main/SECURITY.md) · [Changelog](https://github.com/mikejhill/skillsinc/blob/main/CHANGELOG.md)

## License

[MIT](https://github.com/mikejhill/skillsinc/blob/main/LICENSE)
