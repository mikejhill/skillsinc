# Configuration reference

This guide documents every key in `config.jsonc`. `skillsinc init` writes a working starting point; edit it, then run `skillsinc plan`.

The file is JSONC: JSON plus `//` and `/* */` comments and trailing commas, as in VS Code's settings ([decision 0011](../decisions/0011-jsonc-configuration.md)). Duplicate keys and `null` values are errors; omit a key to get its default.

## Location

| Item        | Default                                                   | Override                             |
| ----------- | --------------------------------------------------------- | ------------------------------------ |
| Config file | `$XDG_CONFIG_HOME/skillsinc/config.jsonc` (`~/.config`)   | `--config PATH`                      |
| Library     | `$XDG_CONFIG_HOME/skillsinc/library/` (written by `init`) | `init --source DIR`, or `path` below |
| State       | `$XDG_STATE_HOME/skillsinc/` (`~/.local/state`)           | `XDG_STATE_HOME`                     |

The XDG variables are honoured on Windows and macOS too. Relative paths in the file resolve against the file's directory; `~` expands to the home directory. Unknown keys are errors, so typos never pass silently.

## Example

```jsonc
{
  "version": 1,
  "sources": {
    "library": { "path": "~/.config/skillsinc/library" },
    "team": { "path": "~/src/team-agent-library" },
  },
  "destinations": {
    "claude": {}, // profile defaults to the destination name
    "copilot": {},
    "codex": {},
    "agents": {},
    "vscode": { "enabled": false }, // kept, but neither written nor cleaned
  },
  "routes": [
    {
      "sources": ["library", "team"],
      "destinations": ["claude", "copilot", "agents"],
      "include": ["skills/*", "instructions/*", "agents/*"],
    },
    {
      "sources": ["library"],
      "destinations": ["codex"],
      "include": ["instructions/*"],
      "allow_lossy": true, // accept applyTo becoming advisory text
    },
  ],
}
```

## `version`

Required. Must be `1`.

## `sources.<name>`

| Key       | Required | Default                                                                               | Meaning                                                  |
| --------- | -------- | ------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| `path`    | yes      | —                                                                                     | Library directory                                        |
| `exclude` | no       | `[".*", "INTENT.md", "*.intent.md", "__pycache__", "*.pyc"]`                          | File and directory names never read                      |
| `layout`  | no       | `skills`, `instructions`, `agents`, `prompts`, `mcp`, `resources`, `hooks`, `plugins` | Object mapping a kind (or `plugins`) to its subdirectory |

A missing kind directory means the library has none of that kind. Each `plugins/<plugin>/` directory is expanded into the kinds above ([decision 0005](../decisions/0005-plugins-expand-into-kinds.md)); a plugin manifest that names a different plugin or relocates `skills`, `agents`, `commands`, or `mcpServers`, and an item name used by both the library and a plugin, fail the source. A missing library directory, a skill without `SKILL.md` or `description`, a skill whose frontmatter `name` differs from its directory, invalid frontmatter, two files with the same name, an MCP server file that is not a JSON object with exactly one of `command` or `url`, or a hook file without `"version": 1` and a `hooks` object of event arrays fail the whole source.

## `destinations.<name>`

| Key       | Required      | Default                   | Meaning                                |
| --------- | ------------- | ------------------------- | -------------------------------------- |
| `profile` | no            | the destination name      | One of `skillsinc profiles`            |
| `path`    | for `generic` | the profile's user folder | Directory the harness reads            |
| `enabled` | no            | `true`                    | `false` skips the destination entirely |

Destinations may not share or nest directories. Profile defaults honour `CLAUDE_CONFIG_DIR`, `COPILOT_HOME`, `CODEX_HOME`, and `APPDATA`.

## `routes[]`

| Key            | Required | Default   | Meaning                                                  |
| -------------- | -------- | --------- | -------------------------------------------------------- |
| `sources`      | yes      | —         | Source names                                             |
| `destinations` | yes      | —         | Destination names                                        |
| `include`      | no       | `["*/*"]` | `kind/name` glob patterns to select                      |
| `exclude`      | no       | `[]`      | `kind/name` glob patterns to drop after `include`        |
| `allow_lossy`  | no       | `false`   | Accept conversions that drop fields or change activation |

Plugin items match both `kind/name` and `plugins/<plugin>/kind/name`, so `"include": ["plugins/kit/*"]` selects one whole plugin and `"exclude": ["plugins/kit/*"]` drops it. Anything a plugin holds that SkillSinc does not sync (hooks, LSP servers, scripts, runtime manifest keys) is a loss on each of its items, so syncing part of a plugin needs `"allow_lossy": true`.

Some harnesses also read another profile's folder: Copilot CLI and VS Code read `~/.agents/skills`, and VS Code also reads `~/.claude/skills` and `~/.copilot/{skills,instructions,agents}`. When both destinations are at their default paths, `copilot` skips skills an `agents` destination receives, and `vscode` skips instructions and agents a `copilot` destination receives, so each harness loads one copy. Any other item one harness would load from two destinations prints a `warning:` line in `plan` and `sync`; it does not block ([decision 0009](../decisions/0009-one-copy-per-harness.md)).

An item reaching a destination through several routes is distributed once; it is lossy-allowed if any of those routes allows it. Two items that render to the same path, or the same JSON entry, with different content block the destination.

## Removing things

- Remove an item from a route (or the library): the next sync deletes its files.
- Set `"enabled": false`: the destination is left exactly as it is.
- Point a destination at a new `path`: the plan refuses until you delete the old manifest named in the error, which releases the old files without deleting them.

## Migrating from TOML

Builds before 0.1.0 read `config.toml`. SkillSinc now refuses a `.toml` file, and names it when `config.jsonc` is missing but `config.toml` sits beside it. The keys are unchanged; only the syntax moves:

| TOML                                     | JSONC                                          |
| ---------------------------------------- | ---------------------------------------------- |
| `version = 1`                            | `"version": 1,`                                |
| `[sources.library]` + `path = "..."`     | `"sources": { "library": { "path": "..." } }`  |
| `[sources.x.layout]` + `skills = "pkgs"` | `"x": { ..., "layout": { "skills": "pkgs" } }` |
| `[destinations.claude]` (empty table)    | `"destinations": { "claude": {} }`             |
| `[[routes]]` (one per route)             | `"routes": [ { ... }, { ... } ]`               |
| `# comment`                              | `// comment`                                   |

Alternatively, run `skillsinc init` to write a fresh `config.jsonc` beside it, then copy your sources, destinations, and routes into it. Delete `config.toml` afterwards.
