# SkillSinc

<!-- Generated from README.md by scripts/pypi_readme.py; do not edit. -->

<!-- markdownlint-disable MD033 -->
![SkillSinc](https://raw.githubusercontent.com/mikejhill/skillsinc/main/docs/brand/wordmark-light.png)
<!-- markdownlint-enable MD033 -->

[![CI](https://github.com/mikejhill/skillsinc/actions/workflows/ci.yml/badge.svg)](https://github.com/mikejhill/skillsinc/actions/workflows/ci.yml)
[![Release](https://github.com/mikejhill/skillsinc/actions/workflows/release.yml/badge.svg)](https://github.com/mikejhill/skillsinc/actions/workflows/release.yml)
[![PyPI](https://img.shields.io/pypi/v/skillsinc)](https://pypi.org/project/skillsinc/)
[![License](https://img.shields.io/github/license/mikejhill/skillsinc)](https://github.com/mikejhill/skillsinc/blob/main/LICENSE)

Write your agent skills, instructions, agents, prompts, MCP servers, reference files, and hooks once. SkillSinc copies them into Claude Code, GitHub Copilot, VS Code, Codex, and the shared `~/.agents` folder, converting formats where it must and saying so when a conversion loses something.

It replaces hand-copying, symlinks, and per-tool sync scripts with a plan of the file actions a sync would perform.

SkillSinc is **alpha software for developers**. Automated tests cover Windows,
macOS, and Linux; native harness verification is partial. Review the
[verified behavior and remaining checks](https://github.com/mikejhill/skillsinc/blob/main/docs/guides/harness-profiles.md), keep
your source library versioned, and inspect a plan before enabling scheduled sync.

## How it works

![How it works](https://raw.githubusercontent.com/mikejhill/skillsinc/main/docs/assets/how-it-works.svg)

- **Your library is the source of truth.** A sync makes each destination match it: new items are added, changed items updated, removed items deleted.
- **SkillSinc only touches what it wrote.** It records every file it owns. An existing file it did not write blocks that destination until you review it and pass `--adopt`.
- **Nothing is written without a plan.** `skillsinc plan` previews current file actions; `skillsinc sync` calculates a fresh plan and checks files again before applying it.
- **Lossy conversions are opt-in.** If a harness cannot represent a field (for example, Claude ignores Copilot `tools` lists), the item is skipped until the route sets `allow_lossy = true`.
- **A broken source never deletes anything.** If a library cannot be read completely, its destinations are left untouched.
- **Disable resources without deleting originals.** From 0.4.0, a source's `disabled` selectors suppress items across every route. [Preview cleanup and preserve local edits](https://github.com/mikejhill/skillsinc/blob/main/docs/guides/configuration.md#disable-resources-while-preserving-their-sources) before syncing.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install skillsinc
```

This installs the latest published release. Run `skillsinc --version` to check
your installation and `uv tool upgrade skillsinc` to update it. The documentation
on `main` can describe unreleased features; consult the matching
[release tag](https://github.com/mikejhill/skillsinc/releases) for your version.

## Quick start

1. Write a configuration. It enables a destination for every harness folder found on this machine and creates an empty library at `~/.config/skillsinc/library` (`$XDG_CONFIG_HOME/skillsinc/library`). Pass `--source DIR` to use an existing library instead:

    ```bash
    skillsinc init
    ```

    Starting with 0.3.0, detecting Codex or VS Code also enables the shared
    `agents` destination for skill discovery, even when `~/.agents` does not
    exist yet. For older releases, add that destination and route skills to it
    in the [configuration](https://github.com/mikejhill/skillsinc/blob/main/docs/guides/configuration.md). Destination folders
    are created only when you explicitly sync.

    Optionally use `skillsinc init --with-guidance` to add a SkillSinc skill and
    always-on instruction to the library (**requires 0.3.0 or later**). They tell
    assistants to author shared
    guidance at its source and distribute it through SkillSinc. You own and can
    edit both files; differing existing files are never overwritten, even with
    `--force`. Nothing is synced, scheduled, or committed to Git by `init`.

2. Preview the changes:

    ```bash
    skillsinc plan
    ```

    Plans show paths, actions, conflicts, and conversion losses. They do not
    display content diffs; review your source changes separately before syncing.

3. Apply them:

    ```bash
    skillsinc sync
    ```

4. Optionally keep destinations in sync on a schedule (Task Scheduler on Windows, launchd on macOS, a systemd user timer or cron on Linux). Manual `skillsinc sync` remains supported:

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
| `codex`   | —         | managed block in `AGENTS.md` | `agents/<name>.toml` | —                    | `config.toml` `mcp_servers`    | —                                     | —                       |
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

## Editing, recovery, and uninstalling

Edit your source library: sync overwrites edits to managed destination content
that is still routed. SkillSinc does not keep backup versions or provide an
automatic rollback. Preserve source history and copy any destination edits you
want to retain before syncing or adopting existing files.

To uninstall, first run `skillsinc schedule remove` if you registered a job,
then run `uv tool uninstall skillsinc`. Uninstalling the package leaves your
library, configuration, ownership records, and distributed files in place;
harnesses can still load those files. See
[removing managed content and recovery](https://github.com/mikejhill/skillsinc/blob/main/docs/guides/configuration.md#removing-things)
to retire a destination or remove distributed content deliberately.

## Documentation

- [Brand assets and usage](https://github.com/mikejhill/skillsinc/blob/main/docs/brand/README.md)

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
