# 0016: Bind release tests, artifacts and publication to one reviewed commit

- **Status:** Proposed
- **Date:** 2026-10-22

## Context

An unpublished tag could previously select package source different from the commit used by CI and provenance. A label alone also identified generated version PRs, and held runs could be approved automatically. Version-pinning a downloaded CLI did not lock its dependencies.

## Decision

Owner-triggered main jobs perform privileged work. Data-only helpers validate bot identity, repository, branch, parent, immutable head and exactly the generated version/changelog diff before dispatching read-only CI. They never load PR code or approve a run. The native CodeQL analysis and upload behavior stays unchanged.

Publication requires the tag, workflow, CI and build to identify the same immutable commit. Publishers download the producing job's artifact ID from the same run. Tool installation uses a committed integrity lock and disables lifecycle scripts; no personal token fallback is used.

## Consequences

Old unpublished tags require retrying their original run, rather than using a newer main run. Changes to generated release files fail closed and need review. Existing environment reviewers still apply. No workflow can make compromised dependencies, the runner or the owner's account risk-free. Before merging this workflow change, verify dispatched checks attach to the next generated release PR; no publication or environment change is part of this PR.
