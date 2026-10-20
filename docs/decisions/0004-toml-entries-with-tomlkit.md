# 0004: Edit Codex `config.toml` entries with tomlkit

This record chooses how SkillSinc edits MCP server entries inside Codex's `config.toml`, and the dependency that requires. It is for anyone extending entries to another TOML file.

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

[Decision 0002](0002-mcp-server-entries.md) made MCP servers owned entries inside shared files and left Codex out because its file is TOML. Codex reads user servers from `[mcp_servers.<name>]` tables in `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`). That file is hand-written: comments, ordering, and other tables (`model`, `profiles`, `projects`) matter to the user. The standard library only reads TOML (`tomllib`), and rewriting the file from parsed data would drop every comment.

Options considered:

| Option                             | Preserves comments and layout | Notes                                                                 |
| ---------------------------------- | ----------------------------- | --------------------------------------------------------------------- |
| `tomllib` + a hand-written emitter | no                            | Re-serializes the whole file; loses comments. Rejected.               |
| `tomli-w`                          | no                            | Writer only; same loss.                                               |
| Hand-written text splicing         | partly                        | Fragile across dotted keys, inline tables, and out-of-order tables.   |
| `tomlkit`                          | yes                           | Style-preserving parser and editor; pure Python, MIT, used by Poetry. |

Codex's own config schema rejects unknown fields and fields of the other transport (`deny_unknown_fields` on its raw server config in `openai/codex`, `codex-rs/config/src/mcp_types.rs`, read 2026-10-20). A bad entry breaks loading the whole file, so conversion must write only fields Codex reads.

## Decision

- **Dependency.** Add `tomlkit` (`>=0.13.3`) as a runtime dependency. It is used only by the TOML entry editor in `entries.py`.
- **Editor.** `EntryFile.open` picks the editor by suffix: `.toml` gets `TomlEntryFile`, everything else stays strict JSON. Ownership, digests, manifests, and the applier are unchanged: entry digests are the same canonical JSON digest of the parsed value, so they do not depend on the file format.
- **Fail closed, verified.** Invalid TOML, duplicate keys, or a container that is not a table (for example `[[mcp_servers]]` or `mcp_servers = 3`) block the destination. Every edit is parsed back and compared with the intended data; any difference blocks instead of writing. TOML has no null, so a null value is refused.
- **Layout.** New entries are `[mcp_servers.<name>]` tables with nested objects written inline (`env = { K = "v" }`). Inside an inline `mcp_servers = { ... }`, entries are inline too. An emptied container is dropped (TOML cannot tell an empty table from a missing one). Trailing blank lines at the end of the file are trimmed, so removing an entry SkillSinc appended restores the original text.
- **Conversion.** Library servers use the `mcpServers` shape. Codex receives:
  - stdio (`command`): `command`, `args`, `env`, `cwd`;
  - streamable HTTP (`url`): `url`, and `headers` as `http_headers`;
  - `tools` as `enabled_tools`, omitted when it contains `"*"`.

  `type` `stdio`/`local` or `http`/`streamable-http` is accepted and not written. `sse` and unknown types are refused, as are carried fields of the wrong type. Every other field is dropped and reported as a loss, as is a `${...}` reference, which Codex does not expand.

## Consequences

- One new runtime dependency (pure Python, no transitive dependencies). The philosophy's "standard library plus PyYAML" becomes "plus PyYAML and tomlkit".
- Users keep their comments and layout; only the entry tables SkillSinc owns change.
- Codex-specific server options (`startup_timeout_sec`, `env_vars`, `bearer_token_env_var`, and so on) are not carried yet; they are reported as losses. Carrying them needs per-field type checks, since a wrong type breaks Codex's config.
- Other TOML targets (Codex agents in gap 1) can reuse `TomlEntryFile` or `tomlkit` directly.
