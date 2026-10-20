# Configuration reference

This guide documents every key in `config.toml`. `skillsinc init` writes a working starting point; edit it, then run `skillsinc plan`.

## Location

| Item        | Default                                                | Override         |
| ----------- | ------------------------------------------------------ | ---------------- |
| Config file | `$XDG_CONFIG_HOME/skillsinc/config.toml` (`~/.config`) | `--config PATH`  |
| State       | `$XDG_STATE_HOME/skillsinc/` (`~/.local/state`)        | `XDG_STATE_HOME` |

The XDG variables are honoured on Windows and macOS too. Relative paths in the file resolve against the file's directory; `~` expands to the home directory. Unknown keys are errors, so typos never pass silently.

## Example

```toml
version = 1

[sources.library]
path = "~/agent-library"

[sources.team]
path = "~/src/team-agent-library"

[destinations.claude]          # profile defaults to the table name
[destinations.copilot]
[destinations.codex]
[destinations.agents]

[destinations.vscode]
enabled = false                # kept, but neither written nor cleaned

[[routes]]
sources = ["library", "team"]
destinations = ["claude", "copilot", "agents"]
include = ["skills/*", "instructions/*", "agents/*"]

[[routes]]
sources = ["library"]
destinations = ["codex"]
include = ["instructions/*"]
allow_lossy = true             # accept applyTo becoming advisory text
```

## `version`

Required. Must be `1`.

## `[sources.<name>]`

| Key       | Required | Default                                                      | Meaning                                  |
| --------- | -------- | ------------------------------------------------------------ | ---------------------------------------- |
| `path`    | yes      | —                                                            | Library directory                        |
| `exclude` | no       | `[".*", "INTENT.md", "*.intent.md", "__pycache__", "*.pyc"]` | File and directory names never read      |
| `layout`  | no       | `skills`, `instructions`, `agents`, `prompts`                | Table mapping a kind to its subdirectory |

A missing kind directory means the library has none of that kind. A missing library directory, a skill without `SKILL.md` or `description`, a skill whose frontmatter `name` differs from its directory, invalid frontmatter, or two files with the same name fail the whole source.

## `[destinations.<name>]`

| Key       | Required      | Default                   | Meaning                                |
| --------- | ------------- | ------------------------- | -------------------------------------- |
| `profile` | no            | the table name            | One of `skillsinc profiles`            |
| `path`    | for `generic` | the profile's user folder | Directory the harness reads            |
| `enabled` | no            | `true`                    | `false` skips the destination entirely |

Destinations may not share or nest directories. Profile defaults honour `CLAUDE_CONFIG_DIR`, `COPILOT_HOME`, `CODEX_HOME`, and `APPDATA`.

## `[[routes]]`

| Key            | Required | Default   | Meaning                                                  |
| -------------- | -------- | --------- | -------------------------------------------------------- |
| `sources`      | yes      | —         | Source names                                             |
| `destinations` | yes      | —         | Destination names                                        |
| `include`      | no       | `["*/*"]` | `kind/name` glob patterns to select                      |
| `exclude`      | no       | `[]`      | `kind/name` glob patterns to drop after `include`        |
| `allow_lossy`  | no       | `false`   | Accept conversions that drop fields or change activation |

An item reaching a destination through several routes is distributed once; it is lossy-allowed if any of those routes allows it. Two items that render to the same path with different content block the destination.

## Removing things

- Remove an item from a route (or the library): the next sync deletes its files.
- Set `enabled = false`: the destination is left exactly as it is.
- Point a destination at a new `path`: the plan refuses until you delete the old manifest named in the error, which releases the old files without deleting them.
