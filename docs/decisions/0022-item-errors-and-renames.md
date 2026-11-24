# 0022: Item errors, lenient skill names, and renames

Status: Accepted

## Context

A client reported three problems with large third-party libraries:

- A skill whose frontmatter `name` differs from its folder failed the whole source, which blocked every destination that read the source. Only the first bad skill was reported, so fixing 17 of them took 17 runs.
- The [Agent Skills specification](https://agentskills.io/specification) says `name` "must match the parent directory name", but its [client implementation guide](https://agentskills.io/client-implementation) says to "warn, load anyway" when it does not. Claude Code uses the frontmatter `name` as the command name and does not require a match.
- Two sources, or a library and one of its plugins, that define the same `kind/name` collide, and the only remedy was leaving one out.

## Decision

**Item errors.** A source read fails as a whole only when the source cannot be read reliably: a missing root, an unreadable directory, or an invalid plugin manifest. An item that is present but invalid (missing `SKILL.md` or description, invalid frontmatter or JSON, unsafe name, duplicate identity) becomes an error item. The planner blocks each destination a route sends an error item to and lists every such item; destinations that do not receive it proceed. Previously delivered output for an error item stays in place, because a blocked destination is not changed.

**Lenient skill names.** A skill whose frontmatter `name` differs from its directory is accepted. The directory stays the identity, and the delivered `SKILL.md` has its `name` set to the directory name, editing only that line when it is safe and rendering the frontmatter again otherwise. Each correction is a plan note. Rewriting rather than passing the mismatch through keeps every harness on one spec-compliant name.

**Renames.** `sources.<name>.rename` maps exact selectors to new item names. Renames apply right after the read, before uniqueness checks, `include`, `disabled`, and routes, so every later selector uses the new name. A Markdown item's `name` field follows the new name. A key that matches nothing is a warning, like an unmatched `include` selector.

## Consequences

One broken item no longer stalls unrelated destinations, and a plan shows every broken item at once. A library that relied on a name mismatch failing loudly now syncs with notes instead. Renames are not globs, so remapping many items needs one key each; prefixes can be added later if needed. Versions before 0.7.0 reject the `rename` key.
