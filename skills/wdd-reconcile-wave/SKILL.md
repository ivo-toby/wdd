---
name: wdd-reconcile-wave
description: Reconcile a completed WDD wave by inspecting task files, PRs or patches, reviews, verification, shared-context updates, architecture drift, and future-wave readiness before starting the next wave.
---

# WDD Reconcile Wave

Use this after all active-wave task PRs or patches have passed their merge gates
or have been explicitly closed, blocked, or cancelled.

## User Input

If the user names a wave, reconcile that wave. Otherwise reconcile the active
wave from `controller-state.md` and `orchestration.json`.

## Preconditions

- `orchestration.json` exists with `schemaVersion: 1`.
- `controller-state.md` exists.
- Every task in the wave is merged, merge-ready, blocked, cancelled, or
  explicitly closed according to policy.
- Verification evidence exists for completed tasks.
- Open P1/P2 feedback is resolved or explicitly accepted by the user.
- Use this skill folder's `templates/` directory as the starting point when
  creating controller-state or shared-context files during recovery or
  reconciliation. Do not require `.wdd/templates/` to exist.

## Workflow

1. Load:
   - Constitution.
   - Epic.
   - Shared context.
   - Wave plan.
   - `orchestration.json`.
   - Controller state.
   - Task files in the wave.
   - PR links, commits, review notes, and verification evidence when available.

2. Verify task gates:
   - Deliverable met.
   - Task definition of done met.
   - Verification passed or non-blocking failures documented.
   - Review completed when required.
   - No unresolved P1/P2 feedback.
   - Branch freshness checked before merge or merge-ready.
   - Branch and PR cleanup state known.
   - Worktree cleanup state known.

3. Compare planned and actual work:
   - Files actually changed.
   - Architecture or design drift.
   - New dependencies.
   - New conflict domains.
   - New shared-context discoveries.
   - Follow-up risks.

4. Reconcile shared context:
   - Merge controller-approved worker discoveries into focused resources.
   - Resolve concurrent shared-context edits.
   - Update `shared-context/index.md` pointers.
   - Prefer new focused resource files over large catch-all updates.

5. Update task files:
   - Move completed tasks to `done/`.
   - Move blocked tasks to `blocked/`.
   - Move cancelled tasks to `cancelled/`.
   - Record PR or patch references.
   - Record verification evidence and review outcome.

6. Clean up completed worktrees:
   - Remove each completed task or bundle worktree once its branch or PR has
     been merged, explicitly closed, cancelled, or safely blocked and all
     verification, review, and shared-context evidence has been captured.
   - Before removal, run `git status --short` in the assigned worktree or
     otherwise verify it has no uncommitted changes. Do not remove dirty
     worktrees; record `cleanup_blocked` with the exact path and reason.
   - Use `git worktree remove <assigned-worktree-path>` from the controller
     checkout for safe worktrees, then run `git worktree prune` if stale
     administrative entries remain.
   - Keep branch, PR, patch, commit, and verification references in the WDD
     artifacts after removing the local worktree.
   - Set each cleaned task or bundle `worktreeStatus` to `cleaned_up` and
     record cleanup evidence in `controller-state.md`.
   - If cleanup is intentionally deferred, record `cleanup_deferred`, the owner,
     and the concrete follow-up condition before closing reconciliation.

7. Update `wave-plan.md`:
   - Mark wave `done`, `blocked`, or partially closed as appropriate.
   - Add completion date.
   - Add drift notes.
   - Update known conflict risks for future waves.

8. Update future tasks:
   - Adjust context, dependencies, conflict domains, verification, branch
     expectations, or scope based on actual merged architecture.
   - Do not start the next wave until these updates are made.

9. Update `orchestration.json`:
   - Task statuses and paths.
   - Wave status.
   - Branch freshness.
   - Verification result.
   - Feedback state.
   - Shared-context reconciliation state.
   - Worktree cleanup state, including `cleaned_up`, `cleanup_blocked`, or
     `cleanup_deferred`.
   - Monitoring status set to `inactive`, `stopped`, or `blocked` with the
     reason recorded.
   - Next-wave readiness.

10. Update `controller-state.md`:
    - Mark wave outcome.
    - Record that monitoring is stopped, inactive, or blocked before closing the
      wave.
    - Add cleanup result.
    - Record drift and future-task updates.
    - Add next-wave or epic-validation recommendation.

## Done When

- Wave outcome is recorded.
- Completed tasks are in `done/`.
- Shared-context updates are reconciled or explicitly queued.
- Completed, closed, cancelled, or safely blocked worktrees have been removed
  with `worktreeStatus` recorded as `cleaned_up`, or cleanup is explicitly
  blocked/deferred with owner and reason.
- Active-wave monitoring is stopped, inactive, or explicitly blocked with a
  durable next action.
- Future tasks reflect drift and new conflict risks.
- The next phase is `wdd-start-wave` for the next pending wave, or
  `wdd-epic-validation` when all waves are complete.
