# 0011: SkillSinc's own configuration is JSONC

This record is for anyone changing the format or location of `config.json`.

- **Status:** Accepted
- **Date:** 2026-10-21

## Context

The configuration was TOML (`config.toml`). Mike prefers JSONC: the harness files SkillSinc writes (VS Code's `mcp.json`, Claude Code's `settings.json`, library MCP server and hook files) are JSON, so one syntax covers the whole setup, and editors give JSONC files comments, folding, and formatting by default. Nothing had been released, so there were no users to migrate besides Mike.

## Decision

- **JSONC syntax, `.json` name.** The default file is `$XDG_CONFIG_HOME/skillsinc/config.json`, read as JSONC whatever the extension. Mike chose the familiar `.json` name over `.jsonc` (the first cut of this decision used `config.jsonc`; when only that file exists it is still read). Editors may flag comments in a `.json` file; the configuration guide gives the VS Code `files.associations` line that fixes it. The file is parsed with the scanner from [decision 0008](0008-jsonc-edits-in-place.md): comments and trailing commas are blanked, keeping offsets so error line and column numbers point into the original text, then the strict JSON rules from [decision 0002](0002-mcp-server-entries.md) apply (duplicate keys, `NaN`, and numbers a rewrite would change are errors). A byte-order mark is accepted. An explicit `null` is an error rather than a stand-in for the default, so `"routes": null` cannot silently empty the routes and delete managed files.
- **Same keys.** `version`, `sources`, `destinations`, and `routes` keep their names and meaning; only the syntax changed. `version` stays `1`.
- **TOML is refused, with a pointer.** A `.toml` path, or a missing `config.json` with `config.toml` beside it, fails with a message linking the migration table in the configuration guide. There is no converter: TOML comments cannot be carried over automatically, and the only existing configuration was converted by hand.
- **`init` writes JSONC** with trailing commas on every member, so commenting a line in or out never breaks the file. It refuses a `.toml` target, which `plan` could not read.

## Consequences

- The configuration loader no longer uses `tomllib`. TOML stays a dependency for Codex's `config.toml` and agent files ([decision 0004](0004-toml-entries-with-tomlkit.md)).
- SkillSinc never writes `config.json` after `init`, so comments are safe.
