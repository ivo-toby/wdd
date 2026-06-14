---
name: wdd-start-wave
description: Activate the next pending WDD wave as a strategy-selected batch of eligible task files, update orchestration and controller state, then hand off to subagent-pr-orchestration.
---

# WDD Start Wave

Use this when the user asks to start, continue, resume, or activate
implementation for a planned WDD epic. This skill activates the next pending wave
as an eligible task batch when dependencies and conflict domains allow it.
Honor the epic `profile`, review mode, and monitoring mode recorded in
`epic.md` and `orchestration.json`. Honor the selected wave strategy when it
exists.

## User Input

If the user names an epic, wave, ticket, task, branch, or PR, use it. Otherwise
choose the first epic with a planned wave that is not done.

## Preconditions

- `wave-plan.md` exists.
- `orchestration.json` exists with `schemaVersion: 1`.
- `controller-state.md` exists or can be created from orchestration state.
- Task files for the target wave exist in status folders.
- The controller must not implement task code.
- A subagent orchestration mechanism must be available, or controller state must
  record that execution is blocked.
- The epic branch named by the epic and orchestration state must exist, or the
  controller must be able to create it from the target branch before any worker
  starts.
- If `controller-state.md` must be created, use this skill folder's
  `templates/controller-state.md` as the starting point. Do not require
  `.wdd/templates/` to exist.

## Workflow

1. Load:
   - Constitution.
   - Epic.
   - Shared-context index.
   - Wave plan.
   - `orchestration.json`.
   - Controller state.
   - Task files in the first pending or active wave.
   - Active profile, review mode, and monitoring mode.
   - Selected wave strategy, including execution mode, confirmation state, and
     bundle groups.

2. Select wave to activate or resume:
   - If a wave is `in_progress`, resume it.
   - Otherwise choose the first wave whose status is not `done`.
   - Do not skip to later waves unless the user explicitly instructs and
     dependencies are satisfied.

3. Verify wave strategy:
   - Read `strategy.profile`, `strategy.executionMode`, `strategy.reviewMode`,
     `strategy.monitoringMode`, `strategy.requiresUserConfirmation`, and
     `strategy.confirmedBy`.
   - Valid execution modes are `bundled`, `hybrid`, and `parallel`.
   - If `requiresUserConfirmation` is true and `confirmedBy` is missing, stop
     before moving task files or creating branches. Present the recommendation,
     rationale, confidence, and exact options to confirm or override.
   - If the strategy is stale relative to current task dependencies, conflict
     domains, or risk, record the mismatch and ask for confirmation before
     dispatch.

4. Determine eligible tasks:
   - Dependency status is resolved.
   - No active conflict-domain blocker exists.
   - No stale prerequisite blocks work.
   - Task status is not `blocked` or `cancelled`.
   - Task is in `todo/`, or already `in-progress/` or `review/` and needs
     resumed orchestration.

5. Allocate branch and worktree isolation before dispatch:
   - Identify the target branch, epic branch, and task or bundle branch
     convention from the constitution, epic, and `orchestration.json`.
   - Create or verify the epic branch before any worker starts. If the epic
     branch cannot be created or verified, block worker dispatch and record the
     exact reason in `orchestration.json` and `controller-state.md`.
   - For `parallel`, allocate one task branch and isolated worktree path per
     eligible repository-writing task.
   - For `bundled`, allocate one bundle branch and isolated worktree path for
     the whole wave.
   - For `hybrid`, allocate one branch and isolated worktree path per bundle
     group.
   - Do not create task branches, bundle branches, or worktrees yet. They must
     branch from an epic branch commit that already contains the activation
     artifact updates.

6. Activate the wave as a batch and sync controller state:
   - Mark the wave `in_progress` in `wave-plan.md`.
   - Move eligible new task files from `todo/` to `in-progress/`.
   - Keep resumed `in-progress/` and `review/` tasks in their current folders.
   - Do not imply sequential task execution.
   - Worker agents may run at the same time only for `parallel` tasks or
     independent `hybrid` bundles. `bundled` uses one worker for the wave.
   - Record non-eligible tasks and the reason they were not dispatched.
   - Update `orchestration.json` and `controller-state.md` with the moved task
     paths, task or bundle branches, assigned worktree paths, and pending
     worktree status.
   - Commit, merge, apply, or otherwise sync these activation artifact changes
     to the epic branch before creating task or bundle branches and worktrees.

7. Create or verify task branches, bundle branches, and worktrees:
   - For `parallel`, create or verify a dedicated task branch and isolated
     worktree per eligible repository-writing task.
   - For `bundled`, create or verify one bundle branch and isolated worktree for
     the wave.
   - For `hybrid`, create or verify one branch and isolated worktree per bundle
     group.
   - Verify each task or bundle worktree contains the current task files,
     `orchestration.json`, and `controller-state.md` from the synced epic branch.
   - If any controller-owned activation state changes after task or bundle
     branch or worktree creation, sync that state into the epic branch and each
     affected task or bundle branch before dispatch.
   - Do not ask workers to switch branches in the controller checkout. Workers
     start only in their assigned worktree.

8. Finalize `orchestration.json`:
   - Set wave status.
   - Set each active task status and current gate.
   - Record task file path after any movement.
   - Record branch, worktree path, worktree status, PR or patch, worker
     reference, reviewer reference, branch freshness, feedback, and
     verification when known.
   - Record active strategy, bundle group branches, bundle worktrees, worker
     references, and current bundle gates when using `bundled` or `hybrid`.

9. Finalize `controller-state.md`:
   - Active wave.
   - Active wave strategy and confirmation state.
   - Monitoring mode, cadence, status, scheduler reference, fallback prompt,
     next check, and stop condition.
   - Active task gates.
   - Worker worktree assignments and isolation status.
   - Branch freshness table.
   - Open P1/P2 feedback.
   - Verification status.
   - Shared-context reconciliation status.
   - Event log entry for activation or resume.
   - For `lite`, keep this file as a concise dashboard and avoid duplicating
     detailed state that already lives in `orchestration.json`.

10. Handoff:
   - Invoke `subagent-pr-orchestration`.
   - For `parallel`, dispatch one worker per eligible active task.
   - For `bundled`, dispatch one worker for the wave and give it all task files
     in the bundle.
   - For `hybrid`, dispatch one worker per bundle group.
   - Give each worker the exact assigned task or bundle files, worktree path,
     checked-out branch, and relevant repo instructions.
   - Tell each worker to start in the assigned worktree and not switch branches
     in the controller checkout.
   - Task files are the implementation briefs. Do not create a separate
     canonical brief artifact.

11. Establish monitoring before ending the controller turn:
   - This is a hard completion gate. Before sending the final response, do not
     mark wave start done or rely on human memory until monitoring is active,
     delegated, or downgraded with a durable manual fallback.
   - Prefer Codex thread heartbeat automation when available and the controller
     should keep returning to the same conversation.
   - Use adaptive cadence when `monitoring_mode` is `adaptive`: slower cadence,
     usually 15-30 minutes, while active workers have no PR or patch; faster
     cadence, usually 5 minutes, during review, fixes, branch freshness, or
     merge-ready gates.
   - Use tighter cadence for `full` only when risk justifies the token cost.
   - In Codex, use `tool_search` to expose `codex_app.automation_update`.
     Create or update a heartbeat automation with `kind` `heartbeat`,
     `destination` `thread`, `status` `ACTIVE`, a minute cadence such as
     `FREQ=MINUTELY;INTERVAL=5`, and a self-contained prompt to run
     `subagent-pr-orchestration` for the active epic and wave. Prefer updating
     an existing matching heartbeat over creating a duplicate.
   - Verify the Codex automation result or existing automation config before
     recording `mode` as `codex_thread_heartbeat`. The recorded
     `schedulerRef` must identify the active heartbeat automation by id or
     stable name.
   - If the Codex automation tool is unavailable, creation or update fails, or
     no active scheduler reference can be verified in the current turn, try the
     next scheduler option. Record the failed Codex attempt in the event log
     and do not leave monitoring mode as `codex_thread_heartbeat`.
   - Otherwise prefer Claude Code `/loop` when running in a Claude Code session
     that supports scheduled tasks.
   - Otherwise use an external scheduler such as a desktop scheduled task, cloud
     routine, GitHub Actions schedule, or project-specific adapter when one is
     available and authorized.
   - If no scheduler is available, downgrade to `manual`, record the next check
     due time, and write an exact fallback prompt that a human or fresh
     controller can run.
   - The fallback prompt must instruct the controller to read
     `orchestration.json` and `controller-state.md`, inspect every active worker
     and reviewer reference, update gates, and stop when tasks are ready for
     `wdd-reconcile-wave`.
   - Record the selected monitoring mode, cadence, status, scheduler reference,
     last check, next check, and fallback prompt in both `orchestration.json`
     and `controller-state.md`.
   - The final response must name the active scheduler reference, delegated
     scheduler, or manual fallback due time.

## Done When

- Active wave is marked `in_progress`.
- Wave strategy is confirmed or explicitly not confirmation-gated.
- Every eligible active-wave task is dispatched or recorded with a blocker.
- The epic branch is created or verified before any worker starts.
- Activation artifact changes are synced to the epic branch before task
  branches and worktrees are created.
- Each dispatched repository-writing task or bundle has a dedicated recorded
  worktree.
- Each dispatched bundle has a dedicated recorded worktree when execution mode
  is `bundled` or `hybrid`.
- `orchestration.json` and `controller-state.md` reflect current gates.
- Monitoring is active, externally delegated, or recorded as manual fallback
  with a durable resume prompt. If monitoring mode is `codex_thread_heartbeat`,
  the active Codex heartbeat automation has been created or verified before
  sending the final response.
- Subagent orchestration has started, or controller state records why it is
  blocked.
