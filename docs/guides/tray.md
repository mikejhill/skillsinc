# The tray: SkillSinc as its own scheduler

`skillsinc tray` runs SkillSinc as a resident tray application instead of registering `sync` with the operating system's scheduler. It syncs on the schedule in [`settings.json`](settings.md), shows past syncs, and lets you sync at once, for every profile or one.

## Install and start

The tray needs Qt (PySide6), an optional extra, so the command-line tool stays small:

```bash
uv tool install "skillsinc[tray]"          # or: uv tool install --reinstall "skillsinc[tray]"
skillsinc tray                             # or the windowless launcher: skillsinc-tray
```

One tray runs per user and state folder. Running `skillsinc tray` again hands the request to the running tray and exits:

| Command                           | Effect                                                                      |
| --------------------------------- | --------------------------------------------------------------------------- |
| `skillsinc tray`                  | Start the tray, or show recent syncs if it already runs                     |
| `skillsinc tray --sync`           | Sync now (the scheduled profiles)                                           |
| `skillsinc tray --sync work home` | Sync now with these [sync profiles](configuration.md#profiles); `*` for all |
| `skillsinc tray --show NAME`      | Open `popup`, `history`, `settings`, or `about`                             |
| `skillsinc tray --quit`           | Stop the running tray                                                       |

Set `startup.launch_at_login` in `settings.json` to start the tray when you sign in: SkillSinc adds a `SkillSinc` value under the current user's `Run` key on Windows, or `~/.config/autostart/skillsinc-tray.desktop` on Linux, and removes it when the setting is turned off. An entry at that name that runs another program (or, on Linux, a link or special file there) is left alone and reported. Set `SKILLSINC_TRAY_SIGN_IN=off` to keep the tray away from sign-in entries entirely, for example on a managed machine or in tests. If you use the tray, remove an OS-scheduled job (`skillsinc schedule remove`) so syncs do not run twice; the run lock makes an overlap harmless but noisy.

## What it shows

- **Click the icon** for the newest syncs, with **Sync now**, **History**, and **Settings** buttons.
- **Right-click** for the menu: the last sync and when the next one is due, **Sync now**, **Sync profile** (all profiles, or one; tooltips say why each is active or not), **Cancel sync** while one runs, **Pause scheduled syncs** (1 hour, 4 hours, 24 hours, or until resumed), **Sync history…**, **Settings…** (the [settings window](#settings-window)), **Open configuration file**, **Open history and logs folder**, **About SkillSinc** (version, Python, Qt, configuration and state paths), and **Exit**.
- **The icon** carries a badge while a sync runs, after a failure, and while syncs are paused.
- **Notifications** report failed syncs, and also syncs that changed files when `notifications.changes` is on.
- **Sync history…** lists recorded syncs (the same records as `skillsinc history`), newest first, a thousand at a time with **Load older runs**, and the selected run's changes and errors.

Settings take effect when `settings.json` is saved; an invalid edit is reported and the previous settings stay in force. If `settings.json` is invalid when the tray starts, nothing automatic happens (no scheduled sync, no sign-in change, and Sync now refuses) until the file is fixed.

## Settings window

**Settings…** opens one window over both files:

- **General**: the [`settings.json`](settings.md) values: schedule on or off, interval, sync at start, which profiles scheduled syncs select (the ones active here, or only those ticked), notifications, update checks, start at sign-in, history size, and which configuration file to sync.
- **Sources**, **Destinations**, **Profiles**, **Routes**: every element of `config.json`, one row each, with **Add…**, **Edit…** (or double-click), and **Remove**. The forms cover each element's common keys (a source's folder, GitHub repository, or Git URL with ref, subdirectory, and refresh; a destination's harness, folder, and enabled switch; a profile's description and default; a route's sources, destinations, include and exclude patterns, lossy switch, and profiles). Keys the form does not show, such as a profile's `when` conditions, are kept as they are. Renaming a source, destination, or profile renames it in every route.

**Save** checks the edited configuration with the same loader `skillsinc sync` uses and writes nothing if it is invalid, showing why. It then writes only what changed: comments and formatting elsewhere in both files stay (a changed element, or the routes list when a route changed, is rewritten as a whole). If either file was edited on disk while the window was open, Save refuses rather than overwrite that edit; reopen the window to start from the file as it is.

## How syncs run

Each sync is a separate `skillsinc sync --trigger tray|tray-scheduled|tray-startup --config PATH` process, so a sync behaves exactly as on the command line, holds the same run lock, and records itself in the same history. One sync runs at a time; further requests queue (a duplicate is dropped), and a sync that runs past 30 minutes is stopped. A cancelled or stopped sync cannot release the run lock itself; the next sync, from the tray or the command line, sees that the lock's process is gone and takes it over. A queued sync runs against the configuration that was selected when it was requested. The schedule counts from the last sync started by anything, so a manual or OS-scheduled sync postpones the next tray sync, and it rechecks at least once a minute so sleep and clock changes do not leave it stale. The tray's own diagnostics go to `tray.log` in the state directory, at `INFO` unless `--log-level` says otherwise; a link or special file at that path is never written. See [decision 0026](../decisions/0026-tray-application.md).
