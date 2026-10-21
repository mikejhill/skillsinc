# Scheduling

This guide shows how to run `skillsinc sync` on a schedule. SkillSinc has no resident process ([decision 0001](../decisions/0001-rebuild-scope.md)); `skillsinc schedule install` registers `sync` with the operating system's scheduler ([decision 0012](../decisions/0012-built-in-scheduling.md)), and the run lock makes an overlapping run exit 1 without writing.

## Before you schedule

- Install SkillSinc as a tool (`uv tool install skillsinc`). The scheduled job runs the Python that ran `schedule install`, so run it from that installation, not from `uvx` or a throwaway environment.
- Run `skillsinc plan` and one interactive `skillsinc sync` first. A scheduled run never passes `--adopt`, so unmanaged files block their destination until you decide.
- Exit code 1 means a source failed, a destination was blocked, or another run held the lock; check the log.

## `skillsinc schedule`

```bash
skillsinc schedule install --interval 5m   # register, or replace an earlier registration
skillsinc schedule status                  # interval, command, last result
skillsinc schedule remove                  # unregister
```

| Option                 | Actions | Meaning                                                                                         |
| ---------------------- | ------- | ----------------------------------------------------------------------------------------------- |
| `--interval DURATION`  | install | Time between runs: `5m`, `1h`, `1h30m`, `1d`, or `HH:MM:SS`; 1 minute to 31 days (default `1h`) |
| `--config PATH`        | install | Pass this configuration to every run (default: the usual lookup)                                |
| `--dry-run`            | install | Print the definition without registering it                                                     |
| `--scheduler NAME`     | all     | `task-scheduler`, `launchd`, `systemd`, or `cron` instead of the platform default               |

`install` is idempotent: running it again replaces the definition, for example with a new interval. `remove` exits 0 even if nothing was registered. `status` exits 0 when a job is registered and 1 when not.

| OS      | Scheduler (default)                   | Definition                                                         | Runs                                          | Log                                      |
| ------- | ------------------------------------- | ------------------------------------------------------------------ | --------------------------------------------- | ---------------------------------------- |
| Windows | Task Scheduler                        | Task `SkillSinc sync`                                              | At logon, then every interval                 | `$XDG_STATE_HOME\skillsinc\sync.log`     |
| macOS   | launchd                               | `~/Library/LaunchAgents/io.github.mikejhill.skillsinc.plist`       | At login, then every interval                 | `$XDG_STATE_HOME/skillsinc/sync.log`     |
| Linux   | systemd user timer                    | `skillsinc.service`, `skillsinc.timer` in `~/.config/systemd/user` | 1 minute after login, then every interval     | `journalctl --user -u skillsinc.service` |
| Linux   | cron (no systemd user session)        | One crontab line ending in `# skillsinc schedule`                  | Every interval (must divide an hour or a day) | `$XDG_STATE_HOME/skillsinc/sync.log`     |

`$XDG_STATE_HOME` defaults to `~/.local/state` on every platform. The log gets a `--- <time> skillsinc sync` header per run and moves to `sync.log.1` once it passes 1 MiB.

How each scheduler behaves:

- **Windows:** a per-user task that needs no administrator rights. It runs `pythonw.exe -P -m skillsinc sync --log-file …`, so no console window opens, skips a run while the previous one is still going, and stops one after 30 minutes. It runs only while you are logged on. Run it now with `Start-ScheduledTask -TaskName 'SkillSinc sync'`. A task registered by the older `Register-SkillSincTask.ps1` script has the same name, so `install` replaces it.
- **macOS:** the agent is loaded into your login session with `launchctl bootstrap`. Run it now with `launchctl kickstart "gui/$(id -u)/io.github.mikejhill.skillsinc"`.
- **Linux (systemd):** user timers run only while you are logged in unless lingering is on (`loginctl enable-linger`). Run it now with `systemctl --user start skillsinc.service`.

### Environment

Schedulers start jobs without your shell's environment. `install` writes `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `CLAUDE_CONFIG_DIR`, `COPILOT_HOME`, and `CODEX_HOME`, when set, into the launchd agent, the systemd service, or the crontab line, so scheduled runs use the same configuration, manifests, and harness folders. Run `install` again after changing them.

Task Scheduler cannot carry variables: tasks read your saved user variables. If you set any of these, save them with `setx` or System Properties; `install` reminds you when it sees one.

## Example files

The files in [`examples/scheduling/`](../../examples/scheduling/) do the same by hand, for people who want to edit a definition (for example to add a calendar schedule or run a Windows task while logged off). They assume the `skillsinc` executable at `~/.local/bin/skillsinc`; use the path from `uv tool dir --bin`.

| OS      | Files                                    | Runs                     | Log                                       |
| ------- | ---------------------------------------- | ------------------------ | ----------------------------------------- |
| Windows | `Register-SkillSincTask.ps1`             | At logon, then hourly    | `$XDG_STATE_HOME\skillsinc\sync.log`      |
| macOS   | `io.github.mikejhill.skillsinc.plist`    | At load, then hourly     | `~/Library/Logs/skillsinc.log`            |
| Linux   | `skillsinc.service`, `skillsinc.timer`   | Hourly, catching up      | `journalctl --user -u skillsinc.service`  |

They use the same task name, agent label, and unit names as `skillsinc schedule`, so `schedule remove` also removes them and `schedule install` replaces them.

### Windows: Task Scheduler

The script registers a per-user task named `SkillSinc sync` that runs at logon and every hour, appends output to `sync.log` in the SkillSinc state directory (`~\.local\state\skillsinc` unless `XDG_STATE_HOME` is set), skips a run while the previous one is still going, and stops one after 30 minutes. By default it needs no administrator rights. Tasks read the user's persistent environment variables, so set `XDG_*` with `setx` or System Properties, not only in a shell session.

```powershell
.\examples\scheduling\Register-SkillSincTask.ps1 -WhatIf      # show what it would register
.\examples\scheduling\Register-SkillSincTask.ps1               # register or replace
.\examples\scheduling\Register-SkillSincTask.ps1 -Interval (New-TimeSpan -Minutes 30) -Arguments '--config C:\agent\config.json'
Start-ScheduledTask -TaskName 'SkillSinc sync'                 # run now
.\examples\scheduling\Register-SkillSincTask.ps1 -Unregister   # remove
```

If script execution is disabled, run it with `powershell -ExecutionPolicy Bypass -File <path>`.

By default the task runs only while you are logged on, and a console window flashes briefly at each run. To avoid both, register once from an elevated shell with `-Background`: the task uses an S4U logon, which runs without a window and stores no password, but cannot reach network shares, so keep the library and harness folders on local disks.

### macOS: launchd

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

### Linux: systemd user timer

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

### cron

Any cron works too. Use the absolute path, and redirect output to a log:

```text
0 * * * * $HOME/.local/bin/skillsinc sync >> $HOME/.local/state/skillsinc/sync.log 2>&1
```
