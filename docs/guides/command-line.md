# Command-line reference

This guide lists every command, option, exit code, and the JSON report format, for people scripting SkillSinc or scheduling it.

## Commands

| Command                                      | Purpose                                                                       |
| -------------------------------------------- | ----------------------------------------------------------------------------- |
| `skillsinc init`                             | Write a starter configuration                                                 |
| `skillsinc plan`                             | Show every change a sync would make; write nothing                            |
| `skillsinc sync`                             | Plan and apply, holding the run lock                                          |
| `skillsinc profiles`                         | List harness profiles and how each kind is supported                          |
| `skillsinc schedule install\|remove\|status` | Register, remove, or inspect a scheduled `sync` ([scheduling](scheduling.md)) |

Options go after the command, for example `skillsinc plan --json`.

| Option                     | Commands                           | Meaning                                                                                                                       |
| -------------------------- | ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `--config PATH`            | init, plan, sync, schedule install | Configuration file (default `$XDG_CONFIG_HOME/skillsinc/config.json`)                                                         |
| `--source DIR`             | init                               | Library to write into the starter configuration (default: create `$XDG_CONFIG_HOME/skillsinc/library`)                        |
| `--with-guidance`          | init                               | Add a portable SkillSinc skill and global instruction to that source; no implicit sync, scheduler, or Git operations          |
| `--force`                  | init                               | Overwrite an existing configuration                                                                                           |
| `-d`, `--destination NAME` | plan, sync                         | Limit to this destination; repeatable                                                                                         |
| `--adopt`                  | plan, sync                         | Take ownership of existing files instead of blocking                                                                          |
| `--json`                   | plan, sync                         | Print the JSON report instead of text                                                                                         |
| `-v`, `--verbose`          | plan, sync                         | List unsupported items, accepted losses, and every skill file                                                                 |
| `--log-file PATH`          | plan, sync                         | Append the report and diagnostics to PATH under a timestamped header instead of printing them; rotates to `PATH.1` past 1 MiB |
| `--interval DURATION`      | schedule install                   | Time between runs: `5m`, `1h`, `1h30m`, `1d`, or `HH:MM:SS` (default `1h`)                                                    |
| `--dry-run`                | schedule install                   | Print the scheduler definition without registering it                                                                         |
| `--scheduler NAME`         | schedule                           | `task-scheduler`, `launchd`, `systemd`, or `cron` instead of the platform default                                             |
| `--log-level LEVEL`        | all                                | `DEBUG`, `INFO`, `WARNING` (default), or `ERROR`, on stderr                                                                   |
| `--version`                | none                               | Print the version                                                                                                             |

## Exit codes

| Code | Meaning                                                               |
| ---: | --------------------------------------------------------------------- |
|    0 | Success; for `plan`, nothing is blocked                               |
|    1 | A source failed, a destination was blocked, or an apply step failed   |
|    2 | Usage or configuration error (including another config-level problem) |
|  130 | Interrupted                                                           |

`schedule status` exits 0 when a job is registered and 1 when not; `schedule remove` exits 0 either way.

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
| `destinations[].errors`  | Blocking errors; also apply failures after `sync`                     |
| `destinations[].changes` | `entry` names the `container/key` changed inside a shared JSON file   |
| `destinations[].skipped` | Items not delivered; `lossy` marks ones `allow_lossy` would accept    |
| `destinations[].notes`   | Losses accepted through `allow_lossy`                                 |
| `destinations[].applied` | Number of changes applied by `sync`                                   |

## Scheduling

`skillsinc schedule install --interval 5m` registers `sync` with Task Scheduler, launchd, a systemd user timer, or cron, carrying the `XDG_*` and harness-home variables set at install so scheduled runs see the same configuration and manifests. See the [scheduling guide](scheduling.md).
