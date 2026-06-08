---
id: EPIC-example-feature-CONTROLLER
kind: controller_state
epic: EPIC-example-feature
active_wave: WAVE-001
status: in_progress
updated_at: YYYY-MM-DD
---

# Controller State: EPIC-example-feature

## Controller Rule

The controller manages waves, workers, reviewers, PRs or patches, feedback,
verification evidence, stale-branch checks, merges or merge-ready decisions,
shared-context reconciliation, and wave reconciliation. The controller does not
implement task code.

## Active Wave

Wave: WAVE-001

Activation: batch dispatch of concurrently eligible tasks.

## Monitoring

Mode: manual

Cadence: 5m

Status: inactive

Last check: None

Next check due: None

Scheduler reference: None

Fallback prompt:

```text
Run subagent-pr-orchestration for EPIC-example-feature WAVE-001. Read orchestration.json and controller-state.md, inspect every active worker and reviewer reference, update task gates, and stop when all active tasks are merged, blocked, cancelled, or ready for wdd-reconcile-wave.
```

Stop condition: all active-wave tasks are merged, blocked, cancelled, or ready
for `wdd-reconcile-wave`.

## Active Task Gates

| Task | Ticket | Branch | PR/Patch | Gate | Worker | Reviewer |
|------|--------|--------|----------|------|--------|----------|
| TASK-001-example-task | TICKET-001-example-ticket | task/TASK-001-example-task | None | not_started | None | None |

## Gate Definitions

- not_started: task has not been dispatched.
- no_pr: implementation has not produced a PR or equivalent patch.
- needs_review: PR or patch exists and review is not complete.
- reviewing: review is active.
- needs_fixes: unresolved P1/P2 feedback exists.
- merge_ready: verification, branch freshness, and P1/P2 gates are clear.
- merged: task is merged into the epic branch or accepted according to policy.
- blocked: controller cannot progress without user or external input.
- cancelled: task was intentionally abandoned or replaced.

## Branch Freshness

| Task | Epic Branch | Task Branch | Freshness | Required Action |
|------|-------------|-------------|-----------|-----------------|
| TASK-001-example-task | epic/example-feature | task/TASK-001-example-task | unknown | Check before merge |

## Open P1/P2 Feedback

- None.

## Verification Status

| Task | Verification | Result | Evidence |
|------|--------------|--------|----------|
| TASK-001-example-task | project-specific verification command | not_run | None |

## Shared Context Reconciliation

- No pending shared-context updates.

## Event Log

- YYYY-MM-DD: WAVE-001 activated.

## Next Action

- Dispatch eligible tasks or resume active gates.
