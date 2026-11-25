# 0025: Sync history and application settings

Status: Accepted

## Context

Mike wants SkillSinc to be its own scheduler: a tray application that syncs on a schedule, shows past sync jobs, and keeps its own settings, with the schedule in `settings.json` rather than `config.json`. Two pieces come first because the tray depends on them and they are useful without it: a record of past syncs, and a settings file.

## Decision

- **Every sync records itself.** `skillsinc sync` appends one JSON line to `history.jsonl` in the state directory of the configuration it ran (the user's, or a project's), inside the state lock, so the tray, Task Scheduler, launchd, and a terminal all feed one history. A run that fails before it plans (a configuration error, a held lock) is recorded in the user state directory, where its configuration path still identifies it. `plan` records nothing.
- **Records are bounded.** A record keeps counts per action, every error (up to 50), and the first 200 changes. The file keeps `history.keep` runs (default 500) and is trimmed only once it holds twice that, and only read for trimming once it is large enough to hold that many records, so a sync pays almost nothing for it. History never fails a sync: a write error is logged.
- **The trigger is explicit.** `sync --trigger` names what started the run (`manual`, `scheduled`, `tray`, `tray-scheduled`, `tray-startup`). `schedule install` registers `--trigger scheduled`; the tray passes its own. Inferring it (no console means scheduled) would mislabel the tray's own runs.
- **`settings.json` beside `config.json`.** Application settings (schedule, profiles to sync, notifications, updates, start at sign-in, history size, which configuration) are per machine, while `config.json` is often shared, so they live in a separate file. It is JSONC with a bundled `settings.schema.json`, kept beside it like `config.schema.json`; saving splices only changed values with the JSONC editor, so comments stay. Unknown keys are errors, like in `config.json`.
- **The command line stays independent.** Only `history.keep` affects the CLI, and a broken `settings.json` warns instead of failing a sync.
- **`skillsinc history`** lists the runs of the configuration it would sync (text or `--json`), so scripts and tests read the same records the tray shows.

## Alternatives considered

- **The tray records its own runs.** Runs by Task Scheduler or a terminal would be missing, and a tray crash would lose the record of the sync it started.
- **SQLite.** Queryable, but a binary file for a few hundred small records adds locking and migration concerns that an append-only text file avoids; JSON lines can be read and repaired by hand.
- **Schedule settings in `config.json`.** A shared configuration would carry one machine's schedule to every machine.

## Consequences

Each sync appends a few hundred bytes (up to about 20 KB with many changes) to the state directory. Tests that snapshot the state directory leave `history.jsonl` out. Versions before this one ignore `settings.json` and leave `history.jsonl` alone.
