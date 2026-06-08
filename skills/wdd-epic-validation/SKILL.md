---
name: wdd-epic-validation
description: Validate a completed WDD epic branch after all waves are reconciled by checking deliverables, task evidence, reviews, shared context, integration state, branch state, and final PR readiness.
---

# WDD Epic Validation

Use this when all planned waves for an epic are complete and reconciled.

## User Input

If the user names an epic, validate that epic. If no epic is named, choose the
active epic whose waves are complete and whose final validation has not passed.

## Preconditions

- `epic.md` exists.
- `wave-plan.md` exists and all waves are done, blocked, cancelled, or explicitly
  closed.
- `orchestration.json` exists with `schemaVersion: 1`.
- `controller-state.md` exists.
- All completed tasks have verification evidence.
- No unresolved P1/P2 feedback remains unless explicitly accepted by the user.
- The controller must not implement new task code during epic validation.

## Workflow

1. Load validation context:
   - Constitution.
   - Epic.
   - Shared-context index and resources.
   - Ticket folders.
   - All task files in all status folders.
   - Wave plan.
   - `orchestration.json`.
   - Controller state.
   - PRs, patches, commits, review notes, and verification evidence when
     available.
   - Relevant code and docs named by the epic or task evidence.

2. Validate planned deliverables:
   - Every epic deliverable is complete or explicitly removed by user-approved
     scope change.
   - No ticket or task was silently skipped.
   - Task status folders match task frontmatter status.

3. Validate definitions of done:
   - Epic definition of done.
   - Ticket completion criteria.
   - Task-level definitions of done.

4. Validate review and feedback:
   - No unresolved P1 findings.
   - No unresolved P2 findings unless user accepted the risk.
   - P3 findings are recorded as non-blocking follow-ups when appropriate.

5. Validate verification evidence:
   - Task verification evidence exists.
   - Repo-native checks named by the epic were run, unavailable, or explicitly
     documented as non-blocking.
   - CI or external checks are green, unavailable, or documented.

6. Validate branch state:
   - Task work merged into the epic branch or marked merge-ready according to
     policy.
   - No task work merged directly to the target branch.
   - Stale branch gates were handled before merges.
   - Epic branch is ready to compare against the target branch.

7. Validate shared context:
   - Worker discoveries are reconciled or explicitly queued.
   - `shared-context/index.md` points to focused resources.
   - No large unindexed shared-context dump was introduced.

8. Validate implementation coherence:
   - Merged tasks work together as a coherent epic.
   - No obvious integration gaps remain.
   - Architecture drift is recorded.
   - Future follow-up tasks are named when needed.

9. Write or update `epic-validation.md`:
   - Validation summary.
   - Definition of done checklist.
   - Deliverable checklist.
   - Task state audit.
   - Review audit.
   - Verification evidence.
   - Shared-context audit.
   - Integration risks.
   - Branch state.
   - Result: `passed`, `failed`, or `blocked`.

10. Update orchestration and controller state:
    - Record validation result.
    - Record blockers.
    - Name next action.

## Done When

- `epic-validation.md` exists and reports `passed`, `failed`, or `blocked`.
- `orchestration.json` and `controller-state.md` reflect validation status.
- The next phase is `wdd-final-pr` only when validation passes.
