# Scheduling

This guide shows how to run `skillsinc sync` on a schedule with the example files in [`examples/scheduling/`](../../examples/scheduling/). SkillSinc has no resident process ([decision 0001](../decisions/0001-rebuild-scope.md)); the operating system's scheduler calls `sync`, and the run lock makes an overlapping run exit 1 without writing.

## Before you schedule

- Install SkillSinc as a tool (`uv tool install skillsinc`) and use its absolute path from `uv tool dir --bin`. The examples assume `~/.local/bin/skillsinc`.
- Run `skillsinc plan` and one interactive `skillsinc sync` first. A scheduled run never passes `--adopt`, so unmanaged files block their destination until you decide.
- Give the job the same environment as your shell: `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, and any `CLAUDE_CONFIG_DIR`, `COPILOT_HOME`, or `CODEX_HOME` you set. A different environment means a different configuration or different manifests. Use `--config PATH` when in doubt.
- Exit code 1 means a source failed, a destination was blocked, or another run held the lock; check the log.

| OS      | Files                                    | Runs                     | Log                                       |
| ------- | ---------------------------------------- | ------------------------ | ----------------------------------------- |
| Windows | `Register-SkillSincTask.ps1`             | At logon, then hourly    | `$XDG_STATE_HOME\skillsinc\sync.log`      |
| macOS   | `io.github.mikejhill.skillsinc.plist`    | At load, then hourly     | `~/Library/Logs/skillsinc.log`            |
| Linux   | `skillsinc.service`, `skillsinc.timer`   | Hourly, catching up      | `journalctl --user -u skillsinc.service`  |

## Windows: Task Scheduler

The script registers a per-user task named `SkillSinc sync` that runs at logon and every hour, appends output to `sync.log` in the SkillSinc state directory (`~\.local\state\skillsinc` unless `XDG_STATE_HOME` is set), skips a run while the previous one is still going, and stops one after 30 minutes. By default it needs no administrator rights. Tasks read the user's persistent environment variables, so set `XDG_*` with `setx` or System Properties, not only in a shell session.

```powershell
.\examples\scheduling\Register-SkillSincTask.ps1 -WhatIf      # show what it would register
.\examples\scheduling\Register-SkillSincTask.ps1               # register or replace
.\examples\scheduling\Register-SkillSincTask.ps1 -Interval (New-TimeSpan -Minutes 30) -Arguments '--config C:\agent\config.toml'
Start-ScheduledTask -TaskName 'SkillSinc sync'                 # run now
.\examples\scheduling\Register-SkillSincTask.ps1 -Unregister   # remove
```

If script execution is disabled, run it with `powershell -ExecutionPolicy Bypass -File <path>`.

By default the task runs only while you are logged on, and a console window flashes briefly at each run. To avoid both, register once from an elevated shell with `-Background`: the task uses an S4U logon, which runs without a window and stores no password, but cannot reach network shares, so keep the library and harness folders on local disks.

## macOS: launchd

launchd does not expand `~`, so the install command substitutes your home directory for `@HOME@`. Edit `StartInterval` (seconds) or add `EnvironmentVariables` entries before loading.

```bash
mkdir -p ~/Library/LaunchAgents
sed "s|@HOME@|$HOME|g" examples/scheduling/io.github.mikejhill.skillsinc.plist \
  > ~/Library/LaunchAgents/io.github.mikejhill.skillsinc.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/io.github.mikejhill.skillsinc.plist
launchctl kickstart "gui/$(id -u)/io.github.mikejhill.skillsinc"   # run now
launchctl bootout "gui/$(id -u)/io.github.mikejhill.skillsinc"     # remove:
rm ~/Library/LaunchAgents/io.github.mikejhill.skillsinc.plist      # unload, then delete
```

## Linux: systemd user timer

The timer runs the service hourly with up to five minutes of random delay, and `Persistent=true` runs a missed hour after sleep or shutdown. Uncomment the `Environment=` lines in the service, or set variables in `~/.config/environment.d/`, to match your shell.

```bash
mkdir -p ~/.config/systemd/user
cp examples/scheduling/skillsinc.service examples/scheduling/skillsinc.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now skillsinc.timer
systemctl --user start skillsinc.service                # run now
journalctl --user -u skillsinc.service                  # log
systemctl --user disable --now skillsinc.timer          # remove
```

User timers run only while you are logged in unless lingering is on (`loginctl enable-linger`).

## cron

Any cron works too. Use the absolute path, and redirect output to a log:

```text
0 * * * * $HOME/.local/bin/skillsinc sync >> $HOME/.local/state/skillsinc/sync.log 2>&1
```
