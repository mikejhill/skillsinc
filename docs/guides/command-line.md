# Command-line reference

This guide lists every command, option, exit code, and the JSON report format, for people scripting SkillSinc or scheduling it.

## Commands

| Command                                      | Purpose                                                                       |
| -------------------------------------------- | ----------------------------------------------------------------------------- |
| `skillsinc init`                             | Write a starter configuration                                                 |
| `skillsinc plan`                             | Show every change a sync would make; write nothing                            |
| `skillsinc sync`                             | Plan and apply, holding the run lock                                          |
| `skillsinc status`                           | Show each source's state, including remote refresh failures                   |
| `skillsinc profiles`                         | List harness profiles and how each kind is supported                          |
| `skillsinc schedule install\|remove\|status` | Register, remove, or inspect a scheduled `sync` ([scheduling](scheduling.md)) |

Options go after the command, for example `skillsinc plan --json`.

This reference follows `main`. `--with-guidance` requires 0.3.0 or later; check
`skillsinc --version` and use the matching release tag for an older installation.

| Option                     | Commands                                   | Meaning                                                                                                                       |
| -------------------------- | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| `--config PATH`            | init, plan, sync, status, schedule install | Configuration file (default `$XDG_CONFIG_HOME/skillsinc/config.json`)                                                         |
| `--source DIR`             | init                                       | Library to write into the starter configuration (default: create `$XDG_CONFIG_HOME/skillsinc/library`)                        |
| `--with-guidance`          | init                                       | Add a portable SkillSinc skill and global instruction to that source; no implicit sync, scheduler, or Git operations          |
| `--force`                  | init                                       | Overwrite an existing configuration                                                                                           |
| `-d`, `--destination NAME` | plan, sync                                 | Limit to this destination; repeatable                                                                                         |
| `--adopt`                  | plan, sync                                 | Take ownership of existing files instead of blocking                                                                          |
| `--json`                   | plan, sync, status                         | Print the JSON report instead of text                                                                                         |
| `-v`, `--verbose`          | plan, sync                                 | List unsupported items, accepted losses, and every skill file                                                                 |
| `--log-file PATH`          | plan, sync                                 | Append the report and diagnostics to PATH under a timestamped header instead of printing them; rotates to `PATH.1` past 1 MiB |
| `--interval DURATION`      | schedule install                           | Time between runs: `5m`, `1h`, `1h30m`, `1d`, or `HH:MM:SS` (default `1h`)                                                    |
| `--dry-run`                | schedule install                           | Print the scheduler definition without registering it                                                                         |
| `--scheduler NAME`         | schedule                                   | `task-scheduler`, `launchd`, `systemd`, or `cron` instead of the platform default                                             |
| `--log-level LEVEL`        | all                                        | `DEBUG`, `INFO`, `WARNING` (default), or `ERROR`, on stderr                                                                   |
| `--version`                | none                                       | Print the version                                                                                                             |

## Exit codes

| Code | Meaning                                                                                                        |
| ---: | -------------------------------------------------------------------------------------------------------------- |
|    0 | Success; for `plan`, nothing is blocked                                                                        |
|    1 | A source failed, a remote source was served from its cache, a destination was blocked, or an apply step failed |
|    2 | Usage or configuration error (including another config-level problem)                                          |
|  130 | Interrupted                                                                                                    |

`status` exits 1 when a local source directory is missing or a remote source's last refresh failed, else 0; it never contacts a remote. `schedule status` exits 0 when a job is registered and 1 when not; `schedule remove` exits 0 either way.

A run lock (`$XDG_STATE_HOME/skillsinc/sync.lock`) makes an overlapping `sync` exit 1. Locks older than one hour are treated as stale.

## Plan symbols

| Symbol | Action   | Meaning                                                    |
| ------ | -------- | ---------------------------------------------------------- |
| `+`    | `create` | Write a new file                                           |
| `~`    | `update` | Rewrite a managed file or block                            |
| `-`    | `delete` | Remove a managed file no longer in the library             |
| `!`    | `adopt`  | Overwrite an unmanaged file and take ownership (`--adopt`) |
| `=`    | `track`  | Record ownership of a file that already matches            |
| `?`    | `forget` | Stop managing a file without touching it                   |

## JSON report

`plan --json` and `sync --json` print one object. Consumers must ignore unknown fields; `version` changes only for incompatible changes.

```json
{
  "version": 1,
  "ok": true,
  "applied": false,
  "source_errors": {},
  "source_warnings": {},
  "destinations": [
    {
      "name": "claude",
      "profile": "claude",
      "path": "/home/user/.claude",
      "blocked": false,
      "errors": [],
      "changes": [
        {"action": "create", "path": "skills/review/SKILL.md", "block": false, "entry": null, "note": ""}
      ],
      "skipped": [
        {"item": "library:prompts/review", "reason": "uses VS Code variables ...", "lossy": true}
      ],
      "notes": [],
      "applied": 0
    }
  ]
}
```

| Field                    | Meaning                                                               |
| ------------------------ | --------------------------------------------------------------------- |
| `ok`                     | False if any source failed, destination blocked, or apply step failed |
| `applied`                | True for `sync`                                                       |
| `source_errors`          | Source name to error message                                          |
| `source_warnings`        | Source name to warning: a remote served from its last good checkout   |
| `destinations[].errors`  | Blocking errors; also apply failures after `sync`                     |
| `destinations[].changes` | `entry` names the `container/key` changed inside a shared JSON file   |
| `destinations[].skipped` | Items not delivered; `lossy` marks ones `allow_lossy` would accept    |
| `destinations[].notes`   | Losses accepted through `allow_lossy`                                 |
| `destinations[].applied` | Number of changes applied by `sync`                                   |

## Source status

`skillsinc status` reads the configuration and the remote caches, without network access, and prints each source:

```text
team: github acme/agent-library (ref main)
  state: failing (3 failed refreshes; serving the cached copy)
  commit: 1f2e3d4c5b6a
  updated: 2026-11-10T09:00:02-05:00
  last checked: 2026-11-12T08:55:01-05:00
  last success: 2026-11-11T17:40:00-05:00
  error: git ls-remote failed: fatal: could not read Username for 'https://github.com': terminal prompts disabled
  fix: check access: set GH_TOKEN or GITHUB_TOKEN, or sign in with `gh auth login` (see `gh auth status`); ...

Needs attention: team.
```

States are `ok`, `not-fetched` (configured, never synced), `failing`, and `missing` (a local directory that does not exist). `status --json` prints `{"version": 1, "ok": ..., "sources": [...]}` with the same fields plus `category` (`auth`, `network`, `ref`, `content`, `git-missing`, `timeout`, or `other`) and `failures`, for monitoring scripts.

## Scheduling

`skillsinc schedule install --interval 5m` registers `sync` with Task Scheduler, launchd, a systemd user timer, or cron, carrying the `XDG_*` and harness-home variables set at install so scheduled runs see the same configuration and manifests. See the [scheduling guide](scheduling.md).
