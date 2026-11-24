# 0022: Item errors, lenient skill names, renames, and a narrower disable guard

Status: Accepted

## Context

A client reported three problems with large third-party libraries:

- A skill whose frontmatter `name` differs from its folder failed the whole source, which blocked every destination that read the source. Only the first bad skill was reported, so fixing 17 of them took 17 runs.
- The [Agent Skills specification](https://agentskills.io/specification) says `name` "must match the parent directory name", but its [client implementation guide](https://agentskills.io/client-implementation) says to "warn, load anyway" when it does not. Claude Code uses the frontmatter `name` as the command name and does not require a match.
- Two sources, or a library and one of its plugins, that define the same `kind/name` collide, and the only remedy was leaving one out.
- Any non-empty `sources.<name>.disabled` list blocked a whole destination when any managed file had local edits ([decision 0018](0018-source-resource-disabling.md)). A skill CLI that rewrites its own `uv.lock` stopped every sync to that destination.

## Decision

**Item errors.** A source read fails as a whole only when the source cannot be read reliably: a missing root, an unreadable directory, or an invalid plugin manifest. An item that is present but invalid (missing `SKILL.md` or description, invalid frontmatter or JSON, unsafe name, duplicate identity) becomes an error item. The planner blocks each destination a route sends an error item to and lists every such item; destinations that do not receive it proceed. Previously delivered output for an error item stays in place, because a blocked destination is not changed. When two items share an identity, both are kept as error items, so a route that selected either one (by plain or plugin selector) blocks rather than plans a removal.

**Lenient skill names.** A skill whose frontmatter `name` differs from its directory is accepted. The directory stays the identity, and the delivered `SKILL.md` has its `name` set to the directory name, editing only that line when it is safe and rendering the frontmatter again otherwise. Each correction is a plan note. Rewriting rather than passing the mismatch through keeps every harness on one spec-compliant name.

**Renames.** `sources.<name>.rename` maps exact selectors to new item names. Renames apply right after the read, before uniqueness checks, `include`, `disabled`, and routes, so every later selector uses the new name. A Markdown item's `name` field follows the new name. A key that matches nothing is a warning, like an unmatched `include` selector. Targets must be portable file names (no Windows device names, no trailing `.`), and a case-only rename is refused.

**Disable guard.** The guard against overwriting local edits while disabling applies only to the paths a disabled item would render to in that destination (whole files, managed-block files, and config files with entries), computed from every disabled item of a routed source whatever the route filters say. Other edited files are repaired as in any sync. A disabled item that cannot be read has unknown paths, so it protects every path, as before.

## Consequences

One broken item no longer stalls unrelated destinations, and a plan shows every broken item at once. A library that relied on a name mismatch failing loudly now syncs with notes instead. Renames are not globs, so remapping many items needs one key each; prefixes can be added later if needed. Versions before 0.7.0 reject the `rename` key.
