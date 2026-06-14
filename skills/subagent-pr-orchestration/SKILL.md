---
name: subagent-pr-orchestration
description: Coordinate WDD task worker agents from task files, track each active task independently, run review gates, route feedback, reconcile shared context, and merge only after verification and branch freshness pass.
---

# Subagent PR Orchestration

Use this when `wdd-start-wave` has activated a wave, or when a single task file
is ready for delegated implementation.
Honor `profile`, `reviewMode`, and `monitoringMode` from `orchestration.json`
or micro-wave `state.json` when present. Honor wave or micro-wave
`executionMode` when a strategy is recorded.

## User Input

Use any named epic, wave, ticket, task, branch, PR URL, patch path, worker
thread ID, or review thread ID. If none is provided, read the active controller
state and orchestration state.

## Preconditions

- `orchestration.json` exists with `schemaVersion: 1`, or a single task file is
  provided.
- Each worker receives the task file set assigned by strategy: one task for
  `parallel` or single-task dispatch, all active wave tasks for `bundled`, or one
  bundle group's tasks for `hybrid`.
- The controller does not implement task code.
- The controller creates or verifies the epic branch before any worker starts.
- The controller creates or verifies one isolated worktree per repository-writing
  task or bundle before dispatch.
- Workers start in their assigned worktree and must not switch branches in the
  controller checkout.
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
   - Profile, review mode, and monitoring mode.
   - Wave strategy or micro-wave strategy, including `executionMode` and
     `bundleGroups`.

2. Ensure each active task has independent state:
   - Task ID and path.
   - Ticket ID.
   - Worker reference.
   - Reviewer reference.
   - Branch.
   - Assigned worker worktree path.
   - Worktree status.
   - PR or patch reference.
   - Latest commit.
   - Branch freshness.
   - Current gate.
   - Open P1/P2 feedback.
   - Verification result.
   - Shared-context update status.
   - Cleanup state.
   - For `bundled` or `hybrid`, also track bundle ID, bundle branch, bundle
     worktree, bundle worker reference, bundle review reference, and bundle
     current gate.

3. Dispatch implementation workers:
   - For `parallel`, dispatch one task file per worker.
   - For `bundled`, dispatch one worker with every task file in the wave.
   - For `hybrid`, dispatch one worker per bundle group with only that bundle's
     task files.
   - Before dispatch, verify the epic branch exists or create it from the
     target branch. If this cannot be done, record `blocked` and do not start
     workers.
   - Before creating task or bundle branches and worktrees, verify the epic
     branch contains the current activation artifact state: moved task paths,
     active gates, planned task or bundle branches, and assigned worktree paths.
     If not, sync those controller-owned artifact changes to the epic branch
     first, or record `blocked`.
   - Before dispatch, create or verify the task branch or bundle branch from the
     current epic branch. Branches must start from the epic branch commit that
     contains the current activation artifact state.
   - Before dispatch, create or verify one isolated worktree per repository
     task or bundle, checked out on its assigned branch.
   - Verify the assigned task file path or bundle task paths and current
     orchestration state exist in the worker worktree before starting the
     worker.
   - Record the assigned worktree path before starting the worker.
   - Require the worker to read assigned task file paths first.
   - Require the worker to inspect named files/domains before broad discovery.
   - Require the worker to read only relevant shared-context resources.
   - Tell the worker the exact worktree path and assigned branch to use.
   - Tell the worker to start in that worktree and never switch branches in the
     controller checkout.
   - Require RED/GREEN TDD unless the task explains why it is inapplicable.
   - Require branch, commit, verification evidence, and PR or patch output.
   - Require proposed shared-context updates when durable discoveries matter.
   - Require final status token: `DONE`, `DONE_WITH_CONCERNS`,
     `NEEDS_CONTEXT`, or `BLOCKED`.

4. Worker prompt contract:
   - Start in the assigned worktree path provided by the controller.
   - Confirm the worktree is on the assigned branch before editing.
   - Confirm the assigned task file path or bundle task paths and orchestration
     state exist in that worktree before editing.
   - Do not switch, create, or reset branches in the controller checkout.
   - Move or verify assigned task file status transitions from `todo/` to
     `in-progress/` when starting, inside the assigned worktree.
   - Stay within task scope.
   - Do not start dependent tasks.
   - Do not merge your own PR.
   - Move assigned task files to `review/` after PR or patch creation.
   - Return PR URL or patch reference.

5. Review gate:
   - When a task PR or patch exists, start a separate reviewer where available.
   - If review mode is `risk_based`, require separate review for high-risk
     tasks: auth, security, persistence, migrations, public APIs, generated
     code, broad shared contracts, or tasks marked high risk.
   - If review mode is `risk_based`, allow controller checklist review for
     low-risk docs, tests, isolated cleanup, or narrow internal changes when the
     constitution allows it.
   - If profile is `full`, prefer separate reviewer coverage for every
     repository-writing task.
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
   - Before merge or merge-ready, check whether the task or bundle branch is stale
     relative to the current epic branch.
   - If stale, require rebase onto the latest epic branch or merge the latest
     epic branch into the task or bundle branch.
   - Rerun relevant verification after branch freshness updates.
   - Rerun review if touched areas changed materially.
   - Do not merge stale task or bundle branches blindly.

8. Shared-context write discipline:
   - Workers may propose shared-context updates in task or bundle branches.
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
   - For `bundled`, the bundle gate clears only when every task in the bundle
     has verification evidence and no unresolved blocking feedback.
   - For `hybrid`, each bundle clears independently; wave reconciliation waits
     for all bundles.

10. Heartbeat loop:
    - Treat each heartbeat as one bounded, idempotent controller tick.
    - Start every tick by reading current `orchestration.json`,
      `controller-state.md`, task files, and relevant PR or patch state.
    - Do not depend on hidden conversation context from a prior tick.
    - If monitoring mode is `adaptive`, use slower cadence, usually 15-30
      minutes, while workers have no PR or patch; use faster cadence, usually 5
      minutes, during review, fixes, branch freshness, or merge-ready gates.
      Repeated no-change ticks may downgrade to manual fallback when safe.
    - If an active wave has missing, inactive, stale, or failed monitoring and
      tasks are not yet ready for reconciliation, repair monitoring before
      ending the tick. In Codex, create or update a thread heartbeat through
      `codex_app.automation_update` when available; otherwise delegate to the
      next scheduler option or downgrade to `manual` with a durable fallback
      prompt and due time.
    - Do not record or preserve `codex_thread_heartbeat` unless the active
      scheduler reference is verified in the current tick.
    - not_started: create or verify the epic branch, sync activation artifacts,
      create or verify task or bundle branches and assigned isolated worktrees
      from that synced state, then dispatch if eligible.
    - no_pr: inspect worker state and nudge exact missing deliverable.
    - needs_review: start or request review.
    - reviewing: poll review state and comments.
    - needs_fixes: route current P1/P2 feedback.
    - merge_ready: verify evidence, branch freshness, and merge policy.
    - merged: update task file, orchestration state, and controller state.
    - blocked: record blocker, owner, and next required input.
    - At the end of each tick, update monitoring last check, next check, status,
      scheduler reference, and fallback prompt.
    - If all active-wave tasks are merged, `merge_ready`, closed, blocked,
      cancelled, or otherwise ready for wave reconciliation, stop or deactivate
      the monitor and hand off to `wdd-reconcile-wave`.
    - If monitoring cannot be scheduled or resumed, set mode to `manual` and
      record the exact prompt and due time needed for a human or fresh agent to
      run the next tick.

11. Update state after every meaningful event:
    - Epic branch created or verified.
    - Task branch created or verified.
    - Worker worktree created or verified.
    - Worker started.
    - Bundle branch, worktree, or worker started.
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
    - Monitoring tick completed, rescheduled, stopped, or downgraded to manual.

12. Completion handoff:
    - When all active-wave tasks are merged, `merge_ready`, closed, blocked,
      cancelled, or otherwise ready for wave reconciliation, invoke
      `wdd-reconcile-wave`.
    - Do not start the next wave before reconciliation.

## Done When

- Every active task has current independent state.
- Every active bundle has current independent state when execution mode is
  `bundled` or `hybrid`.
- P1/P2 feedback is routed.
- Branch freshness is enforced before merge.
- Merge happens only after verification, review, and freshness gates pass.
- Completed wave is ready for `wdd-reconcile-wave`.
