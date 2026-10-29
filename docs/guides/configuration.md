# Configuration reference

This guide documents every key in `config.json`. `skillsinc init` writes a working starting point; edit it, then run `skillsinc plan`.

The file keeps the `.json` extension but is read as JSONC: JSON plus `//` and `/* */` comments and trailing commas, as in VS Code's `settings.json` ([decision 0011](../decisions/0011-jsonc-configuration.md)). Duplicate keys and `null` values are errors; omit a key to get its default.

## Location

| Item        | Default                                                   | Override                             |
| ----------- | --------------------------------------------------------- | ------------------------------------ |
| Config file | `$XDG_CONFIG_HOME/skillsinc/config.json` (`~/.config`)    | `--config PATH`                      |
| Library     | `$XDG_CONFIG_HOME/skillsinc/library/` (written by `init`) | `init --source DIR`, or `path` below |
| State       | `$XDG_STATE_HOME/skillsinc/` (`~/.local/state`)           | `XDG_STATE_HOME`                     |

The XDG variables are honoured on Windows and macOS too. Relative paths in the file resolve against the file's directory; `~` expands to the home directory. Unknown keys are errors, so typos never pass silently.

When `config.json` is absent but a `config.jsonc` from an early build sits beside it, that file is read instead. Any path given to `--config` is read as JSONC whatever its extension, except `.toml`.

Editors treat a `.json` file as strict JSON and may flag comments. In VS Code, add this to your user settings:

```jsonc
"files.associations": { "**/skillsinc/config.json": "jsonc" }
```

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

## Optional self-management guidance

Starting with 0.3.0, `skillsinc init --with-guidance` adds `skills/skillsinc/SKILL.md` and
`instructions/skillsinc.instructions.md` to the selected source. These are
ordinary library files: edit them there, review `skillsinc plan`, and explicitly
run `skillsinc sync` to distribute them. No configuration schema key is added.
When Codex or VS Code is detected, init enables an `agents` destination for
shared skill discovery, even without this option and even if `~/.agents` has
not yet been created. Init does not create that destination directory.

The option does not create Git history or contact a remote. Keep the library in
your own local Git repository and review eligible files before committing or
publishing. Guidance upgrades never overwrite installed customizations.
Identical files are preserved; differing files block init, including with
`--force`. Plain init and manual-only syncing remain supported. For an existing
customized configuration, copy the two bundled assets into the library instead
of regenerating the config. See [the decision](../decisions/0014-optional-self-managed-guidance.md).

## `version`

Required. Must be `1`.

## `sources.<name>`

| Key        | Required | Default                                                                               | Meaning                                                           |
| ---------- | -------- | ------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `path`     | yes      | —                                                                                     | Library directory                                                 |
| `exclude`  | no       | `[".*", "INTENT.md", "*.intent.md", "__pycache__", "*.pyc"]`                          | File and directory names never read                               |
| `disabled` | no       | `[]`                                                                                  | Resource selectors suppressed across every route from this source |
| `layout`   | no       | `skills`, `instructions`, `agents`, `prompts`, `mcp`, `resources`, `hooks`, `plugins` | Object mapping a kind (or `plugins`) to its subdirectory          |

A missing kind directory means the library has none of that kind. Each `plugins/<plugin>/` directory is expanded into the kinds above ([decision 0005](../decisions/0005-plugins-expand-into-kinds.md)); a plugin manifest that names a different plugin or relocates `skills`, `agents`, `commands`, or `mcpServers`, and an item name used by both the library and a plugin, fail the source. A missing library directory, a skill without `SKILL.md` or `description`, a skill whose frontmatter `name` differs from its directory, invalid frontmatter, two files with the same name, an MCP server file that is not a JSON object with exactly one of `command` or `url`, or a hook file without `"version": 1` and a `hooks` object of event arrays fail the whole source.

An error scanning a nested skill directory also fails the whole source. Its
destinations stay unchanged, so an unreadable asset directory is never treated
as an intentional removal of its files.

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

### Disable resources while preserving their sources

Starting with **0.4.0**, set `sources.<name>.disabled` to suppress individual skills, instructions, agents, prompts, MCP servers, resources, or hooks across every route from that source:

```json
{
  "sources": {
    "library": {
      "path": "~/my-agent-library",
      "disabled": ["skills/old-skill", "instructions/old-rule", "resources/old-resource"]
    }
  }
}
```

This is a source entry within a complete configuration; keep its destinations and routes. Selectors are case-sensitive `kind/name` or `plugins/plugin/kind/name`. Kinds are `skills`, `instructions`, `agents`, `prompts`, `mcp`, `resources`, `hooks`, or `*`; names and plugin names accept glob patterns. Use logical item names, without `.md`, `.instructions.md`, or `.json` extensions. `skills/review` also matches a plugin's skill named `review`; use `plugins/kit/skills/review` to limit it to that plugin. Invalid selectors or non-string lists fail configuration validation.

Precedence is explicit: route `include` selects items; that route's `exclude` drops them; any remaining match from another route can still select them. A source's `disabled` list then overrides **all** routes, including explicit includes and `allow_lossy`. It affects only that named source. Remove a selector to enable the item again. Source files are never edited, moved, or deleted. Disabled items are still read and validated with their source; a broken source still blocks cleanup.

Run `skillsinc plan --verbose` or `skillsinc plan --json` before syncing. Selected disabled items appear under `skipped` with the reason `disabled by sources.<name>.disabled`; proposed removals and ownership releases appear in `changes`. Planning does not apply them. Disabling stops future emission; cleanup happens only on an explicit or already scheduled sync. Unedited managed files, blocks, and JSON/TOML entries are removed. Edited output whose whole managed unit is retired is preserved and released from ownership (`forget`). If a shared block or entry still contains enabled resources, disabling must not overwrite edits to that unit: the destination is blocked for review instead. This conservative guard blocks all updates that would overwrite edits while any routed source has a nonempty `disabled` list, even when route filters also exclude those items or a selector currently matches nothing. Unmanaged content is preserved. Re-enabling does not authorize overwriting released local edits: review any ownership conflict before adopting or restoring source content.

Setting a whole destination's `enabled` to `false` instead freezes it, including cleanup: previously generated content can remain active in its harness. To remove redundant generated content, keep the destination enabled, disable the source resource, inspect the plan, and sync only the intended destinations. Existing scheduled syncs also use the changed configuration; pause them separately before editing if cleanup must wait.

Preserved edited output can also remain active in its harness after a `forget` action. Disabling source emission cannot guarantee deactivation while preserving such live local edits. Review retained files, blocks, or entries and move or remove their local content deliberately after saving it elsewhere when deactivation is required.

Versions through 0.3.x reject the new `disabled` key. Upgrade before adding it. On older versions, use `routes[].exclude` on every applicable route to suppress an item without deleting its source. Before downgrading, translate disabled selectors into exclusions on every applicable route, then remove the new key; simply dropping the key would re-enable those resources.

### Remove distributed output

- Remove an item from a route (or the library): the next sync deletes its
  unedited managed files, blocks, and entries. Locally edited content is left
  in place and released from ownership; review the plan for `forget` actions.
- Set `"enabled": false`: the destination is left exactly as it is.
- Point a destination at a new `path`: the plan refuses until you delete the old manifest named in the error, which releases the old files without deleting them.

To remove distributed content while keeping the source library, leave the
destination enabled and remove it from the routes. Run `skillsinc plan`, review
the proposed deletions and releases, then `skillsinc sync`. Remove the destination
from the configuration only after that cleanup. Disabling or deleting a
destination from the configuration does not clean its files.

SkillSinc has no automatic backup or rollback. Before adopting existing content
or editing routes, copy any files you may want to restore and keep the source
library under version control. To recover a prior library version, stop the
scheduled sync with `skillsinc schedule remove`, restore the intended source
version, inspect `skillsinc plan`, and sync when the result is correct. A
destination edit to content that is still routed is overwritten by the source;
copy that edit into the library first if you intend to keep it.

To uninstall the tool, remove the scheduled job while the executable is still
available, then use the package manager that installed it:

```bash
skillsinc schedule remove
uv tool uninstall skillsinc
```

The package manager does not remove your library, configuration, manifests, or
distributed content. Keep ownership records if you intend to reinstall or resume
management; removing them makes existing output unmanaged. Older releases may
not understand a newer configuration or manifest: preserve both before a
downgrade, use documentation for that version, and preview before syncing.

## Migrating from TOML

Builds before 0.1.0 read `config.toml`. SkillSinc now refuses a `.toml` file, and names it when `config.json` is missing but `config.toml` sits beside it. The keys are unchanged; only the syntax moves:

| TOML                                     | JSONC                                          |
| ---------------------------------------- | ---------------------------------------------- |
| `version = 1`                            | `"version": 1,`                                |
| `[sources.library]` + `path = "..."`     | `"sources": { "library": { "path": "..." } }`  |
| `[sources.x.layout]` + `skills = "pkgs"` | `"x": { ..., "layout": { "skills": "pkgs" } }` |
| `[destinations.claude]` (empty table)    | `"destinations": { "claude": {} }`             |
| `[[routes]]` (one per route)             | `"routes": [ { ... }, { ... } ]`               |
| `# comment`                              | `// comment`                                   |

Alternatively, run `skillsinc init` to write a fresh `config.json` beside it, then copy your sources, destinations, and routes into it. Delete `config.toml` afterwards.
