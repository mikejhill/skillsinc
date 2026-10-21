# 0008: Edit VS Code's JSONC `mcp.json` in place

This record replaces the "No JSONC" rule in [decision 0007](0007-files-beside-the-root.md). It is for anyone changing how SkillSinc edits a shared configuration file that allows comments.

- **Status:** Accepted
- **Date:** 2026-10-21

## Context

VS Code reads `User/mcp.json` as JSONC: `//` and `/* */` comments and trailing commas are allowed, and users rely on comments to document servers. Decision 0007 refused such files, because the JSON entry editor rewrites the whole file from parsed data and would drop every comment. Mike chose to keep comments rather than add an opt-in setting that strips them.

## Decision

- **Splice, never re-serialize.** `JsoncEntryFile` locates members by position (`skillsinc.jsonc`) and changes only the text of the one member being added, updated, or removed. Comments, indentation, line endings, a byte-order mark, key order, and trailing commas elsewhere in the file are kept byte for byte.
- **Match the surroundings.** A new member goes after the last thing inside its object (a trailing comment included), indented like its siblings, with a trailing comma when the file already uses them. Removing it gives back the original bytes.
- **Fail closed.** Every edit is parsed back (comments and trailing commas blanked, then the strict JSON rules from decision 0002) and compared with the intended data. Any difference, an unterminated comment or string, duplicate keys, or a non-object container blocks the destination; nothing is written.
- **Scope.** Only files whose harness reads them as JSONC use this editor: VS Code's `mcp.json`. Copilot CLI's `mcp-config.json` and Claude Code's `~/.claude.json` stay strict JSON, since those harnesses do not accept comments.
- **Limits.** A value SkillSinc replaces is written fresh, so a comment inside an owned server's value is lost when that server changes. Pruning never removes a comment: an emptied `servers` with a comment inside stays, and a file SkillSinc created is deleted with its last server only if it holds no comments.

## Consequences

- No new dependency; the scanner is about 300 lines of standard-library Python with its own tests.
- Users can keep comments in `mcp.json`; SkillSinc's entries look hand-written in place.
