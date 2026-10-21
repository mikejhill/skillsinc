# Scheduling examples

Run `skillsinc sync` on a schedule by hand. Most people should run `skillsinc schedule install` instead; these files are for editing a definition yourself. Installation, environment, and logs are covered in the [scheduling guide](../../docs/guides/scheduling.md).

| File                                   | Scheduler                            |
| -------------------------------------- | ------------------------------------ |
| `Register-SkillSincTask.ps1`           | Windows Task Scheduler               |
| `io.github.mikejhill.skillsinc.plist`  | macOS launchd agent                  |
| `skillsinc.service`, `skillsinc.timer` | Linux systemd user service and timer |
