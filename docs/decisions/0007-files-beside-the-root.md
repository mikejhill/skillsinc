# 0007: Entry files beside a destination root

This record defines how a profile writes MCP entries into a configuration file that sits next to its destination directory instead of inside it. It is for anyone adding such a file to a profile.

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

[Decision 0002](0002-mcp-server-entries.md) limited MCP entries to files inside a destination root. Claude Code keeps user servers in `~/.claude.json`, beside the default `~/.claude` root, and VS Code keeps them in `Code/User/mcp.json`, the parent of its prompts folder. Every path check so far refuses `..`, and the operating system resolves `root/..` through a linked root, which would put the file beside the link target rather than beside the configured path.

## Decision

- **Declared, not computed.** A profile lists the exact files it may write beside its root in `Profile.outside`, each as `../<name>` (Claude Code: `../.claude.json`). Any other path with `..` is still refused, and only JSON entries (never whole files or blocks) use these paths today.
- **Placement hook.** `Profile.locate(path, root, environment)` maps a rendered entry file to its destination-relative path. Claude Code returns `../.claude.json` only for the default root `~/.claude` with no `CLAUDE_CONFIG_DIR`; for any other root, including `$CLAUDE_CONFIG_DIR`, the file is `<root>/.claude.json`, matching where Claude Code reads it.
- **Lexical sibling paths.** Only the leading `..` of a declared file is taken by name (the configured root's parent), never through links, so a linked `~/.claude` still puts servers in `~/.claude.json`. The rest of the root is never normalized, so a `..` inside a configured path keeps its operating-system meaning and the safety checks inspect exactly the location written.
- **Same checks.** The file itself must not be a link, a junction, or a directory. No source overlap check is added: a source containing the root's parent also contains the root, which the existing check refuses.
- **Same ownership.** The manifest keys the file by its destination-relative path (`../.claude.json`), so all of decision 0002's rules apply unchanged. Removing a created file never prunes the folder it sits in.
- **Permissions.** Rewriting any shared file (entries or managed blocks) keeps its permission bits, so a private `~/.claude.json` stays private.

## Consequences

- Claude Code rewrites `~/.claude.json` while it runs, so a sync can stop with "changed since the plan was made"; nothing is written, and a rerun succeeds.
- `~/.claude.json` is re-serialized with two-space indentation, the format Claude Code itself writes.
- The planner takes the `Environment`, so placement can depend on environment variables.
