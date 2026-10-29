# 0017: Asynchronous mutation feedback

Status: Accepted

## Context

Mutation testing takes substantially longer than the normal quality gates. PR iterations cancelled useful work, and incomplete outcomes could be omitted from the reported score. The complete 0.3.0 run scored 84.9%, below the unchanged 85% target, despite its report-only push job succeeding.

## Decision

Run full-package mutation testing on default-branch pushes, weekly, and explicit default-branch manual dispatches. It is independent of PR and release checks. Preserve running jobs and verdict caches. Require a nonempty, fully classified report before claiming a score meets the target.

A separate serialized publisher runs after mutation completion, including cancellation. It has issue-write permission; the producer has only content-read permission. Before accessing artifacts, the publisher refetches and validates the repository, workflow identity, event, default branch, and completed run. It checks out only publisher-controlled source at its own workflow SHA. Artifacts are immutable-ID, attempt-bound data; only a bounded ordinary JSON file is parsed, never executed. This boundary justifies the narrow `dangerous-triggers` suppression on the publisher's `workflow_run` trigger.

Maintain separate deduplicated findings and execution-failure issues assigned to the repository owner. Reject stale results. Only a newer complete comparable healthy descendant result may resolve findings; partial, malformed, skipped, cancelled, and failed execution cannot establish recovery. Mock tests cover issue lifecycle, pagination, stale evidence, malformed summaries, and recovery without writing real issues.

## Consequences

Normal CI, security, and release checks remain required. Mutation feedback can remain outstanding after a release, visibly tracked with run links, scope, counts, score, and triage guidance. Publisher failures remain visible as failed Actions runs; repairing the publisher does not itself establish mutation health.
