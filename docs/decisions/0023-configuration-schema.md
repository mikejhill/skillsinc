# 0023: A JSON Schema beside the configuration

Status: Accepted

## Context

`config.json` is JSONC ([decision 0011](0011-jsonc-configuration.md)). Editors treat a `.json` file as strict JSON, so every comment showed as a syntax error. The only remedy was a VS Code `files.associations` user setting, needed on every machine and invisible to anyone opening a project configuration.

VS Code's JSON support reads two non-standard keywords from a file's schema, `allowComments` and `allowTrailingCommas`, and then accepts both in a `.json` file. A schema also gives completion, hover text, and validation. Hotkit uses the same approach for its JSONC `config.json`.

## Decision

- **Bundled schema.** `src/skillsinc/config.schema.json` (draft 2020-12) describes every key the loader reads, with `allowComments` and `allowTrailingCommas` set and `additionalProperties: false`, matching the loader's unknown-key errors. Tests check it against the profile registry, the kinds, the starter files, the guide's example, and inputs the loader refuses.
- **Local copy, referenced relatively.** The configuration names it with `"$schema": "./config.schema.json"`. A local file loads offline and without VS Code's trust prompt for schemas from untrusted domains, and it always matches the installed version rather than `main`.
- **Who writes it.** `init` writes the key and the schema. `sync` writes the schema when the file names `./config.schema.json` (or `config.schema.json`) and the copy is missing or is an older SkillSinc schema, identified by its `$id`. A file of that name with another `$id`, invalid JSON, or invalid UTF-8 is left alone. `plan` writes nothing, as before. A failed write is a warning; it does not fail the sync. SkillSinc still never rewrites `config.json` itself, so existing files gain the key only when their owner adds it.
- **Loader.** The root accepts `$schema` as a non-empty string and ignores its value.

## Consequences

Comments and trailing commas no longer show as errors, and editors flag typos and wrong types before `plan` does. Two machines sharing one configuration folder on different SkillSinc versions would rewrite the schema on each sync; no supported setup shares the folder. Versions before 0.8.0 reject `$schema` as an unknown key, so a file with the key needs 0.8.0 or later.
