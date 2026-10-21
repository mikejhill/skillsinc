# 0010: Harness-neutral MCP server options and per-profile overrides

This record is for anyone adding an MCP server field that only some harnesses understand.

- **Status:** Accepted
- **Date:** 2026-10-21

## Context

The goal is one library entry per MCP server for every harness. In a test run, four Codex servers could not move into the library. `fusion_360` had `enabled = false`, which SkillSinc would have dropped, turning the server back on. `cronometer` used `startup_timeout_sec`, `tool_timeout_sec`, and `default_tools_approval_mode`, which only Codex reads and which would have been lost. Codex rejects unknown keys, so these fields cannot simply be copied to every harness ([Codex MCP](https://developers.openai.com/codex/mcp)).

## Decision

- **Neutral options.** A library server may set `enabled` (boolean), `startupTimeoutSec`, and `toolTimeoutSec` (positive seconds). Profiles map them and never copy them verbatim:
  - Codex writes `enabled = false` (only when false), `startup_timeout_sec`, and `tool_timeout_sec`.
  - Claude Code, Copilot CLI, and VS Code document no per-server switch, so a server with `enabled: false` is refused there, and an owned entry is removed. They have no per-server timeouts, so timeouts are reported as a loss.
- **Per-profile overrides.** `profiles.<profile>` is an object merged into that profile's converted entry last, unchecked. Other profiles ignore it. This carries harness-only fields such as `"profiles": {"codex": {"default_tools_approval_mode": "prompt"}}` without making them losses elsewhere. Codex refuses an override holding a JSON null, which TOML cannot represent.
- **Validation.** Wrong types for any of these keys fail the source, as other malformed server files do.

## Consequences

- One `mcp/<name>.json` can now describe a server for every harness, including Codex-only tuning.
- Overrides bypass SkillSinc's field checks by design. A wrong key in an override reaches the harness, which may reject it.
