---
name: wdd-start-wave
description: Activate the next pending WDD wave as a concurrent batch of eligible task files, update orchestration and controller state, then hand off to subagent-pr-orchestration.
---

# WDD Start Wave

Use this when the user asks to start, continue, resume, or activate
implementation for a planned WDD epic. This skill activates the next pending wave
as a concurrent task batch when dependencies and conflict domains allow it.

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

## Workflow

1. Load:
   - Constitution.
   - Epic.
   - Shared-context index.
   - Wave plan.
   - `orchestration.json`.
   - Controller state.
   - Task files in the first pending or active wave.

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

4. Activate the wave as a batch:
   - Mark the wave `in_progress` in `wave-plan.md`.
   - Move eligible new task files from `todo/` to `in-progress/`.
   - Keep resumed `in-progress/` and `review/` tasks in their current folders.
   - Do not imply sequential task execution.
   - Worker agents may run at the same time.
   - Record non-eligible tasks and the reason they were not dispatched.

5. Update `orchestration.json`:
   - Set wave status.
   - Set each active task status and current gate.
   - Record task file path after any movement.
   - Record branch, PR or patch, worker reference, reviewer reference, branch
     freshness, feedback, and verification when known.

6. Update `controller-state.md`:
   - Active wave.
   - Active task gates.
   - Branch freshness table.
   - Open P1/P2 feedback.
   - Verification status.
   - Shared-context reconciliation status.
   - Event log entry for activation or resume.

7. Handoff:
   - Invoke `subagent-pr-orchestration`.
   - Dispatch one worker per eligible active task.
   - Give each worker exactly one task file plus relevant repo instructions.
   - Task files are the implementation briefs. Do not create a separate
     canonical brief artifact.

## Done When

- Active wave is marked `in_progress`.
- Every eligible active-wave task is dispatched or recorded with a blocker.
- `orchestration.json` and `controller-state.md` reflect current gates.
- Subagent orchestration has started, or controller state records why it is
  blocked.
