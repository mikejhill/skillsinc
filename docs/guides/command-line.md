# Command-line reference

This guide lists every command, option, exit code, and the JSON report format, for people scripting SkillSinc or scheduling it.

## Commands

| Command              | Purpose                                              |
| -------------------- | ---------------------------------------------------- |
| `skillsinc init`     | Write a starter configuration                        |
| `skillsinc plan`     | Show every change a sync would make; write nothing   |
| `skillsinc sync`     | Plan and apply, holding the run lock                 |
| `skillsinc profiles` | List harness profiles and how each kind is supported |

Options go after the command, for example `skillsinc plan --json`.

| Option                     | Commands         | Meaning                                                               |
| -------------------------- | ---------------- | --------------------------------------------------------------------- |
| `--config PATH`            | init, plan, sync | Configuration file (default `$XDG_CONFIG_HOME/skillsinc/config.toml`) |
| `--source DIR`             | init             | Library to write into the starter configuration                       |
| `--force`                  | init             | Overwrite an existing configuration                                   |
| `-d`, `--destination NAME` | plan, sync       | Limit to this destination; repeatable                                 |
| `--adopt`                  | plan, sync       | Take ownership of existing files instead of blocking                  |
| `--json`                   | plan, sync       | Print the JSON report instead of text                                 |
| `-v`, `--verbose`          | plan, sync       | List unsupported items, accepted losses, and every skill file         |
| `--log-level LEVEL`        | all              | `DEBUG`, `INFO`, `WARNING` (default), or `ERROR`, on stderr           |
| `--version`                | none             | Print the version                                                     |

## Exit codes

| Code | Meaning                                                               |
| ---: | --------------------------------------------------------------------- |
| 0    | Success; for `plan`, nothing is blocked                               |
| 1    | A source failed, a destination was blocked, or an apply step failed   |
| 2    | Usage or configuration error (including another config-level problem) |
| 130  | Interrupted                                                           |

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

Run `skillsinc sync` from Task Scheduler, cron, or launchd using the absolute path from `uv tool dir --bin`. Keep the same `XDG_*` environment as interactive runs so the scheduled job sees the same configuration and manifests.
