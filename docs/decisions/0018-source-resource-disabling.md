# 0018: Source-wide resource disabling

Status: Accepted

## Context

Route exclusions can suppress individual resources while preserving their authored source files, but another route can select the same item again. Users need a reversible source-wide decision for redundant skills, instructions, and other resource kinds.

## Decision

Add optional `sources.<name>.disabled` selectors for every supported kind, using case-sensitive logical item identities and glob matching. Validate canonical kind/name and plugin-qualified selectors. Source disabling wins over every route from that source. Removing the selector re-enables normal routing. Existing configurations retain their behavior; older clients reject the new key explicitly.

Read and validate the whole source before planning cleanup. Mark selected disabled items as skipped with an explicit configuration reason in verbose and JSON plans. Reuse ownership-aware reconciliation: a sync removes unedited managed output, preserves and forgets edited output when its whole managed unit is retired, and leaves unmanaged content intact. If a still-managed shared unit contains local edits, block the destination instead of overwriting edits during partial resource retirement. Conservatively guard all overwriting updates while any routed source has a nonempty disable list, regardless of overlapping route filters or currently unmatched selectors. Re-enabling cannot silently reclaim edited content released from ownership. A disabled destination remains frozen, so no cleanup is implied by its setting.

## Consequences

The authored library remains unchanged. Configuration edits affect future scheduled syncs as well as manual syncs, so documentation distinguishes deselection, dry-run inspection, cleanup, and destination freezing. Downgrades require translating source-wide selectors into exclusions on every applicable route before removing the new key. Synthetic tests cover all resource kinds, overlapping routes, source isolation, plugin selectors, managed files, shared blocks, configuration entries, edited content, failed source reads, and process-level behavior.
