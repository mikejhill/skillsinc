# Application settings: `settings.json`

`config.json` says what to sync. `settings.json`, in the same folder (`$XDG_CONFIG_HOME/skillsinc/settings.json`, usually `~/.config/skillsinc/settings.json`), says how SkillSinc behaves on this machine when it runs as its own scheduler. Keep `config.json` shared between machines and `settings.json` local to each.

The file is optional: without it every setting takes its default. It is JSONC like `config.json`, and SkillSinc keeps `settings.schema.json` beside it so editors complete and validate it. When SkillSinc saves the file it changes only the values that changed, so your comments stay.

```jsonc
{
  "$schema": "./settings.schema.json",
  "version": 1,
  // null: the user configuration beside this file
  "config": null,
  "schedule": {
    "enabled": true,
    "interval": "5m",
    // null: the profiles active on this machine
    "profiles": null,
    "run_at_start": true,
  },
  "notifications": { "failures": true, "changes": false },
  "updates": { "check": true, "install": false },
  "startup": { "launch_at_login": false },
  "history": { "keep": 500 },
}
```

| Setting                   | Default | Meaning                                                                                                                   |
| ------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------- |
| `config`                  | `null`  | Configuration file to sync. Relative paths and `~` resolve against this folder. `null`: the user configuration            |
| `schedule.enabled`        | `true`  | Sync on a schedule                                                                                                        |
| `schedule.interval`       | `5m`    | Time between scheduled syncs: `5m`, `1h`, `1h30m`, `1d`, or `HH:MM:SS`; from one minute to 31 days                        |
| `schedule.profiles`       | `null`  | [Sync profiles](configuration.md#profiles) each scheduled sync selects, as `--profile` would. `null`: the active profiles |
| `schedule.run_at_start`   | `true`  | Sync as soon as SkillSinc starts                                                                                          |
| `notifications.failures`  | `true`  | Notify when a sync fails                                                                                                  |
| `notifications.changes`   | `false` | Also notify when a sync changes files                                                                                     |
| `updates.check`           | `true`  | Check PyPI for a new release                                                                                              |
| `updates.install`         | `false` | Install a release an automatic check finds without asking                                                                 |
| `startup.launch_at_login` | `false` | Start SkillSinc when you sign in                                                                                          |
| `history.keep`            | `500`   | Sync runs the [history](command-line.md#sync-history) keeps, 10 to 100000. Every `skillsinc sync` honours it              |

An unknown key, a wrong type, or an interval under a minute is an error that names the setting. `skillsinc sync` only reads `history.keep`, and a broken `settings.json` never stops a sync: it warns and keeps 500 runs.
