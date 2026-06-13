---
name: wdd-start-wave
description: Activate the next pending WDD wave as a concurrent batch of eligible task files, update orchestration and controller state, then hand off to subagent-pr-orchestration.
---

# WDD Start Wave

Use this when the user asks to start, continue, resume, or activate
implementation for a planned WDD epic. This skill activates the next pending wave
as a concurrent task batch when dependencies and conflict domains allow it.
Honor the epic `profile`, review mode, and monitoring mode recorded in
`epic.md` and `orchestration.json`.

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

2. Select wave to activate or resume:
   - If a wave is `in_progress`, resume it.
   - Otherwise choose the first wave whose status is not `done`.
   - Do not skip to later waves unless the user explicitly instructs and
     dependencies are satisfied.

3. Determine concurrently eligible tasks:
   - Dependency status is resolved.
   - No active conflict-domain blocker exists.
   - No stale prerequisite blocks work.
   - Task status is not `blocked` or `cancelled`.
   - Task is in `todo/`, or already `in-progress/` or `review/` and needs
     resumed orchestration.

4. Allocate branch and worktree isolation before dispatch:
   - Identify the target branch, epic branch, and task branch convention from
     the constitution, epic, and `orchestration.json`.
   - Create or verify the epic branch before any worker starts. If the epic
     branch cannot be created or verified, block worker dispatch and record the
     exact reason in `orchestration.json` and `controller-state.md`.
   - For each eligible task that will change repository files, allocate a task
     branch name and isolated worktree path.
   - Do not create task branches or task worktrees yet. They must branch from an
     epic branch commit that already contains the activation artifact updates.

5. Activate the wave as a batch and sync controller state:
   - Mark the wave `in_progress` in `wave-plan.md`.
   - Move eligible new task files from `todo/` to `in-progress/`.
   - Keep resumed `in-progress/` and `review/` tasks in their current folders.
   - Do not imply sequential task execution.
   - Worker agents may run at the same time.
   - Record non-eligible tasks and the reason they were not dispatched.
   - Update `orchestration.json` and `controller-state.md` with the moved task
     paths, task branches, assigned worktree paths, and pending worktree status.
   - Commit, merge, apply, or otherwise sync these activation artifact changes
     to the epic branch before creating task branches or worktrees.

6. Create or verify task branches and worktrees:
   - For each eligible task that will change repository files, create or verify
     a dedicated task branch from the synced epic branch.
   - Create or verify one isolated worktree per eligible task, checked out on
     that task branch.
   - Verify each task worktree contains the current `in-progress/...` task file,
     `orchestration.json`, and `controller-state.md` from the synced epic branch.
   - If any controller-owned activation state changes after task branch or
     worktree creation, sync that state into the epic branch and each affected
     task branch before dispatch.
   - Do not ask workers to switch branches in the controller checkout. Workers
     start only in their assigned worktree.

7. Finalize `orchestration.json`:
   - Set wave status.
   - Set each active task status and current gate.
   - Record task file path after any movement.
   - Record branch, worktree path, worktree status, PR or patch, worker
     reference, reviewer reference, branch freshness, feedback, and
     verification when known.

8. Finalize `controller-state.md`:
   - Active wave.
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

9. Handoff:
   - Invoke `subagent-pr-orchestration`.
   - Dispatch one worker per eligible active task.
   - Give each worker exactly one task file, the exact assigned worktree path,
     the checked-out task branch, and relevant repo instructions.
   - Tell each worker to start in the assigned worktree and not switch branches
     in the controller checkout.
   - Task files are the implementation briefs. Do not create a separate
     canonical brief artifact.

10. Establish monitoring before ending the controller turn:
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
- Every eligible active-wave task is dispatched or recorded with a blocker.
- The epic branch is created or verified before any worker starts.
- Activation artifact changes are synced to the epic branch before task
  branches and worktrees are created.
- Each dispatched repository-writing task has a dedicated recorded worktree.
- `orchestration.json` and `controller-state.md` reflect current gates.
- Monitoring is active, externally delegated, or recorded as manual fallback
  with a durable resume prompt. If monitoring mode is `codex_thread_heartbeat`,
  the active Codex heartbeat automation has been created or verified before
  sending the final response.
- Subagent orchestration has started, or controller state records why it is
  blocked.
