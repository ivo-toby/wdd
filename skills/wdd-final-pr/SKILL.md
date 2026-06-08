---
name: wdd-final-pr
description: Prepare the final WDD epic pull request from the epic branch into the target branch after epic validation passes, including a comprehensive human-review description.
---

# WDD Final PR

Use this after `wdd-epic-validation` passes and the epic branch is ready for
human review into the target branch.

## User Input

If the user names an epic, prepare the final PR for that epic. Respect user
preferences for draft vs ready PR, target branch, PR title, and whether to use a
GitHub adapter or local text-only PR draft.

## Preconditions

- `epic-validation.md` exists and reports `passed`.
- `orchestration.json` exists with `schemaVersion: 1`.
- `controller-state.md` exists.
- Epic branch exists or the artifact records why PR creation must be manual.
- Final PR targets the original target branch, not another task branch.
- The controller must not implement new task code during final PR preparation.
- Use this skill folder's `templates/final-pr.md` as the starting point when
  creating `final-pr.md`. Do not require `.wdd/templates/` to exist.

## Workflow

1. Load:
   - Constitution.
   - Epic.
   - Epic validation report.
   - Wave plan.
   - `orchestration.json`.
   - Controller state.
   - Ticket and task summaries.
   - Shared context.
   - PR, patch, review, and verification references.

2. Confirm final PR gate:
   - Epic validation passed.
   - No unresolved P1/P2 feedback remains.
   - Task branches are merged or resolved according to policy.
   - Epic branch is the source branch.
   - Target branch is the constitution or epic target branch.

3. Draft `final-pr.md`:
   - Start from `templates/final-pr.md` in this skill folder when creating a new
     draft.
   - PR title.
   - Epic summary.
   - Completed deliverables.
   - Definition of done checklist.
   - Validation evidence.
   - Test results or repo-native verification status.
   - Wave summary.
   - Task summary.
   - Review summary.
   - Known limitations.
   - Risks.
   - Follow-up tasks.
   - Documentation updates.
   - References to epic, wave plan, orchestration, controller state, shared
     context, task files, and worker PRs or patches.

4. Create PR when an adapter is available and the user wants it:
   - Source: epic branch.
   - Target: target branch.
   - Draft or ready state according to user preference.
   - Body based on `final-pr.md`.

5. If no PR adapter is available:
   - Keep `final-pr.md` as the durable PR draft.
   - Record exact source and target branch names.
   - Record manual PR creation instructions.

6. Update state:
   - Add final PR URL or local draft path to `orchestration.json`.
   - Add event log entry to `controller-state.md`.
   - Mark final PR status as drafted or created.

## Done When

- `final-pr.md` contains a comprehensive human-review description.
- Final PR is created when supported, or local draft instructions are recorded.
- `orchestration.json` and `controller-state.md` link to the final PR or draft.
- Human review is the next step.
