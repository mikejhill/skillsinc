# 0027: Automatic update installs

Status: Accepted

## Context

[Decision 0026](0026-tray-application.md) has the tray check PyPI for new releases. Mike asked for the tray to apply updates too, and `updates.install` lets it do that without asking. An upgrade replaces the files of the running program, so it has to run after the tray exits, and an upgrade that fails must not turn into a loop of exits, failed installs and restarts. A review of the first version also found that a shell or batch script written to the state folder could be swapped out or misquote paths, and that a failed check could still start an install.

## Decision

- **Install only installations SkillSinc can identify.** That means a `uv tool` environment (its folder is named `skillsinc` and has uv's marker), a `pipx` environment (under `venvs/skillsinc`), or a virtual environment that has pip. The tool's home folder (`UV_TOOL_DIR`, `PIPX_HOME`) is passed on so the upgrade reaches the same environment. An editable install, a virtual environment without pip, or a system Python is left to the user, and the tray says how.
- **A stdlib helper runs the upgrade.** The tray starts the base interpreter with `-I -c <helper> <JSON plan>` as a detached, windowless process, and then exits. Nothing is written to disk to be run later, and every command is an argv list, so no shell parses a path. The helper:
  - waits up to 60 seconds for the tray's process to end;
  - appends the installer's output to `update.log` (only a regular file, never a link or a FIFO);
  - records `update-result.json` with the version, exit code and a note;
  - starts the tray again.
- **A failure is reported once and never retried automatically.** At start, the tray reads the record. A success (the running version reached the target) is announced and the record is removed. A failure is reported once, and the record stays, so an automatic install skips that release. The menu still offers it, and a later release replaces the record.
- **Install at a quiet moment.** An install waits until the running sync and any queued syncs finish, and until the settings window closes, and gives one notice while it waits. Before an automatic install runs, the tray checks `updates.check` and `updates.install` again, and drops the install if either was turned off in the meantime. Only a successful check that finds a newer release can start an automatic install.
- **Versions compare as PEP 440 releases.** Pre-releases and development releases rank below their final release, so the tray never "updates" to an older build.

## Alternatives considered

- **Keep the generated `update.cmd` / `update.sh`.** It is simple, but a file in a user-writable folder that runs later can be replaced, and `cmd` quoting breaks on paths that contain `%`, `^` or `&`.
- **Retry failed installs on the next check.** It recovers from transient failures, but a deterministic failure such as a locked file or a missing tool would exit and restart the tray every day. Reporting the failure once and letting the user choose Install is safer.
