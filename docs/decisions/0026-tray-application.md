# 0026: A tray application as SkillSinc's own scheduler

Status: Accepted

## Context

Mike wants SkillSinc to be able to schedule itself: a tray icon with a menu (sync now, for everything or one profile; settings; check for updates; About with the version), a window of past syncs, and a settings window, with the schedule kept in `settings.json`. [Decision 0001](0001-rebuild-scope.md) ruled out a resident process and [decision 0012](0012-built-in-scheduling.md) delegated scheduling to the operating system; the tray is an opt-in alternative to that, not a replacement. VerdiClip, another of Mike's tools, has a PySide6 tray whose menu, popup, and About dialog this follows.

## Decision

- **PySide6, as an optional extra.** `skillsinc[tray]` adds `pyside6-essentials` (QtCore, QtGui, QtWidgets, QtNetwork; no WebEngine or multimedia). The command line never imports Qt; `skillsinc tray` imports it lazily and explains how to install the extra when it is missing. The `skillsinc-tray` GUI script starts it without a console on Windows.
- **Syncs run in a child process.** The tray starts `python -P -m skillsinc sync --trigger ... --config ...` with QProcess, one at a time with a queue, a 30-minute timeout, and cancel. A sync behaves exactly as on the command line, takes the same run lock, and records itself in `history.jsonl`; a crash in a sync cannot take the tray down, and the tray learns the result from the history rather than parsing output.
- **The schedule is the tray's.** It reads `schedule` from `settings.json`, counts from the last sync started by anything (so other syncs postpone it), and rechecks at least once a minute. Pausing (for a while, or until resumed) lives in memory only: restarting the tray resumes.
- **One tray per user and state folder.** A QLocalServer named for the user and a hash of the state folder owns the tray; a later launch forwards its arguments (`--sync`, `--show`, `--quit`) and exits. A `tray.lock` (QLockFile) in the state folder serializes starting launches, so only the launch that will listen removes a stale socket.
- **Killing a sync must not block the next.** Cancel and the timeout kill the child, which cannot release `sync.lock`, so the run lock now also counts as stale when the process it names no longer exists (not only after an hour).
- **Fail closed.** An invalid `settings.json` at start holds scheduled syncs, sign-in changes, and Sync now until it reads cleanly; sign-in entries that are not SkillSinc's are never replaced or removed.
- **Start at sign-in** is a `Run` value (Windows) or an XDG autostart entry (Linux) that `startup.launch_at_login` reconciles on start and on every settings change.
- **Settings stay a file.** The tray watches `settings.json` and applies edits at once, keeping the previous settings when an edit is invalid. Its Settings entry opens the file until a settings window replaces it.

## Alternatives considered

- **Run syncs in a thread of the tray.** Faster to start, but a sync's logging, signal handling, and failures would share the tray's process, and the result would differ from a command-line sync.
- **Tkinter or a web UI.** Tkinter has no tray icon; a local web UI needs a browser and a server, and still no tray icon.
- **The full PySide6 package.** It adds several hundred megabytes of modules the tray never uses.

## Consequences

The tray extra adds about 150 MB on install. Tests run Qt on the `offscreen` platform; Linux CI installs the EGL, GL, xkbcommon, fontconfig, D-Bus, and GLib runtime libraries. Mutation testing skips the tray package, which its journey tests cover. macOS has no start-at-sign-in support yet.
