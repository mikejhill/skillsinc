# 0020: Source selection and configuration-owned state

Status: Accepted

## Context

Remote sources ([decision 0019](0019-remote-git-sources.md)) make it easy to point at a large public library, but users often want only a few of its skills. Route `include` filters can express that, yet they repeat for every route reading the source and do not say what the source itself is. Users also expect SkillSinc's state to behave like Terraform's: whatever is no longer in the configured collection is removed, and changes are applied consistently.

Per-resource reconciliation already worked that way. Each destination's manifest records owned files, managed blocks, and config entries with digests; items removed from the library, a route, or a filter are deleted when unedited and released (`forget`) when edited; drift is repaired; a failed source read changes nothing. One gap remained: deleting a destination from the configuration left its manifest and every file it owned in place forever, with no warning.

## Decision

**Source `include`.** `sources.<name>.include` lists selectors (the `disabled` grammar: `kind/name` or `plugins/plugin/kind/name`, globs allowed) of the resources the source offers. The whole source is still read and validated, then filtered before planning, so a resource left out is planned exactly like one deleted from the library: never delivered, and its managed output removed. `disabled` still overrides. A selector that matches nothing produces a warning, because a typo or upstream rename would otherwise remove the resource silently; it does not block, matching how a deletion in the library is handled. An empty list is refused.

**Configuration-owned manifests.** Each manifest records the resolved path of the configuration file that last synced it, including destinations that were already up to date, so existing manifests are claimed by the first sync after upgrading. During a full `plan` or `sync` (no `--destination`), any manifest recorded by the current configuration file whose destination is no longer configured is planned against an empty desired state with the ordinary ownership rules, reported as `removed from configuration`, and deleted once it owns nothing. Entries it owns beside the root (`../name`) are cleaned like any other.

Safety limits: manifests recorded by another existing configuration file are ignored, so separate configurations can share one state directory; manifests with no recorded or no surviving owner only warn; a removed destination whose root overlaps a configured destination is blocked, because that is almost always a rename, and the error names the manifest to rename. `enabled: false` still freezes a destination without cleanup.

## Consequences

Removing a destination from the configuration now deletes its unedited managed output on the next scheduled sync. Users who want to keep generated files must set `enabled: false` or delete the manifest instead. The manifest gains an optional `config` key; older clients ignore it, so the manifest version stays 2. The JSON report gains `destinations[].retired`, and `profile` is null for removed destinations. Remote sources still fetch the whole repository; `include` filters after the checkout, which keeps the cache and its atomic update rules unchanged.
