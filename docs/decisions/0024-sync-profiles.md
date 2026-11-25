# 0024: Sync profiles

Status: Accepted

## Context

Mike wants one `config.json` replicated across machines, where each machine applies only some segments of it, chosen explicitly or by conditions, and wants the CLI to select profiles on `sync` without requiring them. Until now a machine that needed a different segment needed a different file, and `enabled: false` on a destination could not be conditional.

"Profile" already names harness profiles (the destination `profile` key and `skillsinc profiles`). Mike chose the word, so the configuration keeps it for this feature; the harness table moves to `skillsinc harnesses`.

## Decision

- **Profiles tag routes.** Top-level `profiles` defines names with an optional `description`, `default` (true), and `when`. A route's `profiles` lists the profiles it belongs to; a route without them applies on every run. Routes are the existing unit that connects sources, filters, and destinations, so tagging them selects any segment (a source, a destination, or a few items) without a second filter grammar. Undefined names in a route are configuration errors.
- **Selection order.** `--profile` (repeatable, comma-separated, globs; unknown names are errors), else `SKILLSINC_PROFILES` (unknown names warn, so one machine-wide variable can serve several configurations, including project ones), else each profile's `default` and `when`. An explicit choice ignores `when`: what the user names is what runs. The variable keeps a per-machine choice out of the shared file and is carried into launchd, systemd, and cron jobs by `schedule install`, like the other variables.
- **Conditions.** `when` holds `host` (case-insensitive globs against the full or short host name), `os` (`windows`, `macos`, `linux`), and `env` (value glob, `true` for set, `false` for unset). Every listed key must hold. `SKILLSINC_HOSTNAME` overrides the host name, for tests and renamed machines.
- **Inactive routes are dropped before planning.** The selection produces a narrowed `Config`; the planner is unchanged. A destination that an active route reaches follows the narrowed routes completely, so items only an inactive profile delivered are removed there, as with a narrowed `include`. A destination reached only by inactive routes is disabled for the run, so it is neither planned nor destroyed: it stays in the configuration, and retirement only applies to destinations removed from the file. A machine therefore never writes to or deletes from harness folders outside its profiles, and a profile's path may not even exist elsewhere. `--destination` naming such a destination is an error rather than a silent no-op.
- **Reporting.** `plan` and `sync` print `Profiles: … (from …)` and the destinations left alone; the JSON report adds `profiles` (null without profiles). `skillsinc profiles [--json]` lists each profile, whether it is active here, why, and how many routes it tags.
- **CLI rename.** `skillsinc profiles` now lists sync profiles and `skillsinc harnesses` prints the harness table. SkillSinc is pre-1.0 and the old command was informational, so the break ships as a minor version.

## Alternatives considered

- **Profiles that own their own routes, sources, or destinations.** Nesting would duplicate the route grammar and make a route shared by two profiles a copy. Tags compose.
- **Partial syncs that never remove other profiles' items.** Tracking which profile delivered each file would need manifest changes, and a machine whose profile set shrinks would keep stale copies forever. Following the desired state is consistent with the rest of SkillSinc; the cost is that a narrower one-off `--profile` removes other profiles' items from shared destinations until the next full run, which `plan` previews.
- **A per-machine selection file.** An environment variable needs no new file location or format, works in projects, and schedulers already carry variables.

## Consequences

One configuration serves every machine. Configurations without `profiles` behave as before. Versions before 0.9.0 reject the new keys, so every machine sharing a file must upgrade first. Scripts calling `skillsinc profiles` for the harness table must call `skillsinc harnesses`.
