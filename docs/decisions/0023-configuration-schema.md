# 0023: A JSON Schema beside the configuration

Status: Accepted

## Context

`config.json` is JSONC ([decision 0011](0011-jsonc-configuration.md)). Editors treat a `.json` file as strict JSON, so every comment showed as a syntax error. The only remedy was a VS Code `files.associations` user setting, needed on every machine and invisible to anyone opening a project configuration.

VS Code's JSON support reads two non-standard keywords from a file's schema, `allowComments` and `allowTrailingCommas`, and then accepts both in a `.json` file. A schema also gives completion, hover text, and validation. Hotkit uses the same approach for its JSONC `config.json`.

## Decision

- **Bundled schema.** `src/skillsinc/config.schema.json` (draft 2020-12) describes every key the loader reads, with `allowComments` and `allowTrailingCommas` set and `additionalProperties: false`, matching the loader's unknown-key errors. Tests check it against the profile registry, the kinds, the starter files, the guide's example, and inputs the loader refuses.
- **Local copy, referenced relatively.** The configuration names it with `"$schema": "./config.schema.json"`. A local file loads offline and without VS Code's trust prompt for schemas from untrusted domains, and it always matches the installed version rather than `main`.
- **Existing files get the key once.** Mike asked that every existing configuration gain the key without manual edits, but only on the first upgrade: a user who deletes it opts out. The first `sync` with 0.8.0 or later splices `"$schema": "./config.schema.json",` in as the first member, keeping every other byte, and verifies the parsed result equals the old data plus `$schema`. `upgrades.json` in the configuration's state directory maps each configuration's absolute path to the newest SkillSinc version whose upgrades it received; the insertion runs only while that record is below 0.8.0 and the file has no `$schema`. A failed insertion is a warning and is not recorded, so the next sync retries it. The file is replaced atomically, keeping its mode and byte-order mark, through a link if it is one. This is the first edit SkillSinc makes to `config.json` after `init`; the record makes future one-time upgrades possible the same way.
- **The schema copy.** Whenever the file names `./config.schema.json` (or `config.schema.json`), `sync` creates the copy if the directory entry is missing, or replaces it if it is a regular file holding another SkillSinc schema version (same `$id`), via a sibling temporary file. A link (dangling or not), directory, FIFO, oversized file, invalid UTF-8, or a schema with another `$id` is not SkillSinc's and is left alone. `init` writes the copy too; a failure there is a warning after the configuration is written.
- **Planned, under the lock.** `plan` reports both kinds of change in a `Configuration:` section and the JSON `configuration` list, writing nothing. `sync` re-plans and applies them under the state lock after the destinations, and reports what it changed and any warnings (also in JSON).
- **Loader.** The root accepts `$schema` as a non-empty string and ignores its value.

## Consequences

Comments and trailing commas no longer show as errors, and editors flag typos and wrong types before `plan` does. Two machines sharing one configuration folder on different SkillSinc versions would rewrite the schema on each sync; no supported setup shares the folder. The schema mirrors the loader's rules where JSON Schema can express them (location alternatives, selector kinds, interval spellings, the generic profile's path); value ranges and cross-references stay loader-only. Versions before 0.8.0 reject `$schema` as an unknown key, so a file with the key needs 0.8.0 or later.
