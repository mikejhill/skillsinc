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

| Key        | Required | Default                                                                               | Meaning                                                               |
| ---------- | -------- | ------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `path`     | one of   | —                                                                                     | Library directory                                                     |
| `github`   | one of   | —                                                                                     | GitHub repository `owner/repo`; see [remote sources](#remote-sources) |
| `git`      | one of   | —                                                                                     | Any Git URL; see [remote sources](#remote-sources)                    |
| `exclude`  | no       | `[".*", "INTENT.md", "*.intent.md", "__pycache__", "*.pyc"]`                          | File and directory names never read                                   |
| `include`  | no       | `["*/*"]`                                                                             | Resource selectors this source offers; others are left out            |
| `disabled` | no       | `[]`                                                                                  | Resource selectors suppressed across every route from this source     |
| `layout`   | no       | `skills`, `instructions`, `agents`, `prompts`, `mcp`, `resources`, `hooks`, `plugins` | Object mapping a kind (or `plugins`) to its subdirectory              |

A missing kind directory means the library has none of that kind. Each `plugins/<plugin>/` directory is expanded into the kinds above ([decision 0005](../decisions/0005-plugins-expand-into-kinds.md)); a plugin manifest that names a different plugin or relocates `skills`, `agents`, `commands`, or `mcpServers`, and an item name used by both the library and a plugin, fail the source. A missing library directory, a skill without `SKILL.md` or `description`, a skill whose frontmatter `name` differs from its directory, invalid frontmatter, two files with the same name, an MCP server file that is not a JSON object with exactly one of `command` or `url`, or a hook file without `"version": 1` and a `hooks` object of event arrays fail the whole source.

An error scanning a nested skill directory also fails the whole source. Its
destinations stay unchanged, so an unreadable asset directory is never treated
as an intentional removal of its files.

### Remote sources

Starting with **0.5.0**, a source can name a Git repository instead of a local directory ([decision 0019](../decisions/0019-remote-git-sources.md)). Give exactly one of `path`, `github`, or `git`:

```jsonc
"sources": {
  "public": { "github": "mikejhill/agent-skills" },
  "team": {
    "github": "acme/agent-library",     // public or private
    "ref": "main",                      // branch, tag, or full commit SHA
    "subdirectory": "library",          // the library inside the repository
    "refresh": "15m",                   // check the remote at most this often
  },
  "gitlab": { "git": "https://gitlab.com/acme/skills.git" },
}
```

| Key            | Default             | Meaning                                                                    |
| -------------- | ------------------- | -------------------------------------------------------------------------- |
| `ref`          | the remote's `HEAD` | Branch, tag, `refs/...` name, or full 40-character commit SHA              |
| `subdirectory` | repository root     | Relative path, using `/`, of the library inside the repository             |
| `refresh`      | every run           | Shortest time between remote checks, such as `15m` or `1h` (at least `1m`) |

`layout`, `exclude`, `include`, and `disabled` apply inside the checkout as for a local library. Remote sources need `git` on `PATH`. A repository containing symbolic links is refused, because a link could point at a file outside the checkout; the previous checkout keeps serving.

**Cost.** A refresh runs one `git ls-remote` for the ref: one HTTPS round trip of a few kilobytes, well within any host's limits even every five minutes. If the commit is unchanged, nothing is downloaded or rewritten. A new commit is fetched shallowly (`--depth 1`), checked out next to the previous checkout (kept for an hour so a concurrent run can finish reading it), into a cache under `$XDG_STATE_HOME/skillsinc/remotes/`, which keeps earlier objects, so only changed files travel. A full commit SHA in `ref` never contacts the remote once cached. `refresh` skips even the `ls-remote` until the interval has passed; a failed refresh is retried on the next run regardless.

**Authentication.** SkillSinc never prompts, so scheduled runs fail fast instead of hanging. For `github` sources it uses the first of `GH_TOKEN`, `GITHUB_TOKEN`, or `gh auth token` (when the GitHub CLI is signed in), passed to Git through its environment, never on a command line or in the cache. Otherwise, and for `git` sources, Git's own credential helper (Git Credential Manager, the macOS keychain, `gh auth setup-git`) or SSH keys apply; an SSH key needs no passphrase or must be loaded in an agent the scheduler can reach. A token needs read access to the repository's contents (a fine-grained token with "Contents: read", or a classic token with `repo` for private repositories). A `git` URL may not carry credentials or a user name (`https://TOKEN@host` included); an SSH login such as `git@host:` is fine. On Windows, Task Scheduler runs see saved user variables only: save a token with `setx GH_TOKEN …`, or rely on `gh` or Git Credential Manager.

**Failures.** A failed refresh never deletes anything. When a checkout is cached, the source is read from that last good commit and the run continues; the report prints a `warning:` line (JSON: `source_warnings`) and the run exits 1, so the scheduler's last result and the sync log show the problem. Without any cached checkout, the source fails like a missing directory and its destinations stay unchanged. `skillsinc status` shows each remote's commit, last check, last success, consecutive failures, and the last error with a remedy. GitHub reports a private repository the credentials cannot see as "repository not found"; SkillSinc treats that as an authentication problem.

Deleting `$XDG_STATE_HOME/skillsinc/remotes/` is safe; the next run fetches again. Versions through 0.4.x reject `github` and `git`.

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

### Select specific resources

Starting with **0.6.0**, `sources.<name>.include` limits a source to the resources it names, so you can take a few skills from a larger library, such as a public GitHub repository, without syncing the rest ([decision 0020](../decisions/0020-source-selection-and-state.md)):

```jsonc
"sources": {
  "community": {
    "github": "acme/agent-skills",
    "include": ["skills/code-review", "skills/release-notes", "plugins/kit/*/*"],
  },
}
```

Selectors use the same grammar as `disabled`: case-sensitive `kind/name` or `plugins/plugin/kind/name`, with glob patterns in any part. Everything the source holds is still read and validated, but resources left out behave exactly like resources deleted from the library: they are never delivered, and anything SkillSinc delivered from them before is removed on the next sync (edited output is released instead, as below). Widening the list delivers them again.

A selector that matches nothing prints `warning: sources.<name>.include: '<selector>' matches no item in the source`, so a typo or an upstream rename is visible in `plan` and `sync`. An empty list is a configuration error. Precedence: the source's `include` decides what the source offers, routes then select from that, and `disabled` overrides both.

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
- Leave an item out of a source's `include`: same as removing it from the library.
- Delete a destination from the configuration (starting with **0.6.0**): the
  next full `sync` removes everything SkillSinc owns there, as if every item
  had been removed, then forgets the destination's manifest. The plan lists it
  as `<name> (removed from configuration)`. `--destination` runs leave removed
  destinations alone.
- Set `"enabled": false`: the destination is left exactly as it is.
- Point a destination at a new `path`: the plan refuses until you delete the old manifest named in the error, which releases the old files without deleting them.

SkillSinc keeps state like Terraform: each destination's manifest records what
SkillSinc owns and which configuration file last synced it, and every sync
reconciles that state with the configuration. Only manifests recorded by the
configuration file being synced are destroyed when their destination
disappears; another existing configuration's manifests are left alone. A
manifest with no known owner (written before 0.6.0 and never synced since, or
recorded by a configuration file that no longer exists) prints a warning and is
never touched; sync once with the configuration that owns it, or delete the
named manifest to release its files. A removed destination whose folder is now a configured source is blocked, never
cleaned. Renaming a destination that keeps its
path blocks the old name's cleanup instead of deleting files the new name
needs: rename `<old>.json` in the manifests folder to `<new>.json`, as the
error explains.

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
