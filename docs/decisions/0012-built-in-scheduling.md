# 0012: `skillsinc schedule` registers sync with the OS scheduler

This record is for anyone changing how scheduled runs are registered, or wondering why SkillSinc now writes scheduler definitions after [decision 0001](0001-rebuild-scope.md) cut login services.

- **Status:** Accepted
- **Date:** 2026-10-21

## Context

Decision 0001 kept SkillSinc free of a resident process: the operating system's scheduler calls `skillsinc sync`. Registration was left to example files (a PowerShell script, a launchd plist, systemd units). In practice every machine needed someone to fetch the right file, edit paths, and run it, and the script was not installed with the package. Mike asked for registration to be part of SkillSinc. Python packages cannot run code at install time, so registration has to be a command.

## Decision

- **`skillsinc schedule install|remove|status`.** Install registers `sync` with the platform's own scheduler, or replaces an earlier registration; remove deletes it; status shows the interval, the command, and the last result. There is still no resident process: the scheduler starts a normal `sync`, and the run lock keeps overlapping runs apart.
- **One definition SkillSinc owns by name per scheduler.** Windows: the per-user Task Scheduler task `SkillSinc sync` (the same name the example script used, so install replaces a scripted task), created from XML with `schtasks /Create /XML /F`. macOS: the launchd agent `io.github.mikejhill.skillsinc` in `~/Library/LaunchAgents`. Linux: `skillsinc.service` and `skillsinc.timer` under `$XDG_CONFIG_HOME/systemd/user`, or, without a systemd user session, one crontab line ending in `# skillsinc schedule`. Nothing else in the user's scheduler is touched.
- **The job runs the interpreter that ran `install`**, as `python -P -m skillsinc sync`, so it does not depend on `PATH` and `-P` keeps the job's working directory off the import path. On Windows it uses `pythonw.exe` beside `python.exe`, so a run opens no console window and needs no elevation. Install refuses an interpreter in uv's cache (`uvx`, `uv run --with`), since uv may delete it.
- **`sync --log-file PATH`** appends the report and diagnostics, under a timestamped header, to `$XDG_STATE_HOME/skillsinc/sync.log`, rotating it to `sync.log.1` past 1 MiB. systemd runs use the journal instead.
- **The environment is captured at install.** `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `CLAUDE_CONFIG_DIR`, `COPILOT_HOME`, and `CODEX_HOME`, when set, are written into the launchd agent, the systemd service, or the crontab line. Task Scheduler cannot carry variables, so install tells the user to save them with `setx`.
- **No registration at install time.** `skillsinc init` and the README point to `schedule install` instead.

## Consequences

- Reinstalling SkillSinc into a different environment (for example a different `uv tool` Python) needs `schedule install` again; `status` shows the interpreter in use.
- The example files stay for people who want to customize a definition by hand, but the guide leads with the command.
- Scheduler behavior is tested through a fake command runner; registering for real is verified by hand on each platform.
