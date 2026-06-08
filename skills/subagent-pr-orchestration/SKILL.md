---
name: subagent-pr-orchestration
description: Coordinate WDD task worker agents from task files, track each active task independently, run review gates, route feedback, reconcile shared context, and merge only after verification and branch freshness pass.
---

# Subagent PR Orchestration

Use this when `wdd-start-wave` has activated a wave, or when a single task file
is ready for delegated implementation.

## User Input

Use any named epic, wave, ticket, task, branch, PR URL, patch path, worker
thread ID, or review thread ID. If none is provided, read the active controller
state and orchestration state.

## Preconditions

- `orchestration.json` exists with `schemaVersion: 1`, or a single task file is
  provided.
- Each worker receives exactly one task file.
- The controller does not implement task code.
- Workers do not merge their own PRs.
- GitHub is optional. If no PR system exists, use branches, patches, or local
  status notes while preserving the same gates.

## Workflow

1. Load controller context:
   - Constitution.
   - Epic.
   - Shared-context index and relevant resources.
   - Wave plan.
   - `orchestration.json`.
   - `controller-state.md`.
   - Active task files.

2. Ensure each active task has independent state:
   - Task ID and path.
   - Ticket ID.
   - Worker reference.
   - Reviewer reference.
   - Branch.
   - PR or patch reference.
   - Latest commit.
   - Branch freshness.
   - Current gate.
   - Open P1/P2 feedback.
   - Verification result.
   - Shared-context update status.
   - Cleanup state.

3. Dispatch implementation workers:
   - One task file per worker.
   - Require the worker to read the task file first.
   - Require the worker to inspect named files/domains before broad discovery.
   - Require the worker to read only relevant shared-context resources.
   - Require RED/GREEN TDD unless the task explains why it is inapplicable.
   - Require branch, commit, verification evidence, and PR or patch output.
   - Require proposed shared-context updates when durable discoveries matter.
   - Require final status token: `DONE`, `DONE_WITH_CONCERNS`,
     `NEEDS_CONTEXT`, or `BLOCKED`.

4. Worker prompt contract:
   - Move the task file from `todo/` to `in-progress/` when starting.
   - Stay within task scope.
   - Do not start dependent tasks.
   - Do not merge your own PR.
   - Move the task file to `review/` after PR or patch creation.
   - Return PR URL or patch reference.

5. Review gate:
   - When a task PR or patch exists, start a separate reviewer where available.
   - Reviewer checks task compliance, correctness, tests, security,
     maintainability, dependency boundaries, conflict domains, scope control,
     and whether durable shared context should be extracted.
   - Reviewer labels findings P1, P2, or P3.
   - P1 and P2 block merge unless the constitution says otherwise and the user
     explicitly accepts the risk.

6. Feedback processing:
   - Route unresolved P1/P2 feedback to the original worker if its context is
     still usable.
   - Use a fresh feedback-fix worker when the original worker is unavailable,
     stale, context-compressed, or a fresh pass is safer.
   - Feedback-fix workers receive the task file, PR or patch, review comments,
     relevant shared context, verification expectations, and definition of done.
   - Feedback-fix work must not broaden task scope.

7. Branch freshness gate:
   - Before merge or merge-ready, check whether the task branch is stale
     relative to the current epic branch.
   - If stale, require rebase onto the latest epic branch or merge the latest
     epic branch into the task branch.
   - Rerun relevant verification after branch freshness updates.
   - Rerun review if touched areas changed materially.
   - Do not merge stale task branches blindly.

8. Shared-context write discipline:
   - Workers may propose shared-context updates in task branches.
   - The controller owns reconciliation into the epic branch.
   - If two workers update the same shared-context resource, resolve conflicts
     during review or wave reconciliation.
   - Prefer focused resource files over editing large shared files.

9. Merge gate:
   - Deliverable is met.
   - Task-level definition of done is met.
   - Verification passes or non-blocking failures are explicitly documented.
   - High-rigor review has no unresolved P1/P2 findings.
   - CI or repo checks are green, unavailable, or explicitly non-blocking.
   - Branch freshness is current.
   - Linked task file is updated.
   - Shared-context updates are reconciled or queued for reconciliation.
   - Controller merges into the epic branch, or marks `merge_ready` when
     repository policy requires human merge.

10. Heartbeat loop:
    - not_started: dispatch if eligible.
    - no_pr: inspect worker state and nudge exact missing deliverable.
    - needs_review: start or request review.
    - reviewing: poll review state and comments.
    - needs_fixes: route current P1/P2 feedback.
    - merge_ready: verify evidence, branch freshness, and merge policy.
    - merged: update task file, orchestration state, and controller state.
    - blocked: record blocker, owner, and next required input.

11. Update state after every meaningful event:
    - Worker started.
    - Task moved.
    - PR or patch created.
    - Review started.
    - P1/P2 found.
    - Feedback routed.
    - Fix pushed.
    - Branch freshness checked.
    - Verification passed or failed.
    - Merge or merge-ready decision completed.
    - Shared-context reconciliation completed or queued.
    - Blocker encountered.

12. Completion handoff:
    - When all active-wave tasks are merged, closed, blocked, or cancelled,
      invoke `wdd-reconcile-wave`.
    - Do not start the next wave before reconciliation.

## Done When

- Every active task has current independent state.
- P1/P2 feedback is routed.
- Branch freshness is enforced before merge.
- Merge happens only after verification, review, and freshness gates pass.
- Completed wave is ready for `wdd-reconcile-wave`.
