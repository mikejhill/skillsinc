# 0003: Resources and Claude Code imports

This record defines the `resources` kind and how Claude Code loads it. It is for anyone adding resources to another harness.

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

`ai-feature-sync`, the local tool SkillSinc replaces, copies reference Markdown files to `~/.copilot/resources` and adds a managed `@`-import block to `CLAUDE.md` naming them. SkillSinc had neither the kind nor a Claude profile that writes a block. Claude Code expands `@path` imports in `CLAUDE.md` at launch and resolves relative paths against the importing file ([memory docs](https://code.claude.com/docs/en/memory)).

## Decision

- **Library format.** `resources/<name>.md`, one reference file per item. Files are copied byte for byte and never parsed, so frontmatter-like text is kept as written. Other file types in the folder are ignored, and names follow the usual path-safe rule.
- **Claude Code.** Each resource is copied to `resources/<name>.md` and contributes one `@resources/<name>.md` section to the managed block in `CLAUDE.md` (the block mechanism from Codex's `AGENTS.md`). Imports are listed in name order. The user's own `CLAUDE.md` text is never changed, and a `CLAUDE.md` SkillSinc created is deleted with its last import.
- **Copilot CLI.** Each resource is copied to `resources/<name>.md`. Copilot CLI documents no resource folder, so nothing loads it automatically; instructions and agents can reference the files.
- **Generic.** Copied verbatim to `resources/<name>.md`.
- **Others.** Codex, VS Code, and `agents` refuse resources with a hint to route them to `claude` or `copilot`. Codex could inline them into `AGENTS.md`, but that changes the file's size and meaning; it waits for a request.
- Both copies are reported as exact: the bytes are unchanged, and the Claude import is the intended activation, not a loss.

## Consequences

- Every resource routed to Claude Code is loaded into every session. Route large references elsewhere, or exclude them with route filters.
- A user who already imports the same file by hand gets a second import of SkillSinc's copy; Claude Code loads both.
