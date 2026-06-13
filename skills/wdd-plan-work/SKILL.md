---
name: wdd-plan-work
description: Plan a WDD micro-wave by splitting one work packet into 2-5 compact task briefs and state.json. Use after wdd-start-work when a bounded ticket benefits from limited parallel worker execution without full epic planning ceremony.
---

# WDD Plan Work

Split one work packet into compact parallel tasks only when parallelism helps.

## Preconditions

- `.wdd/work/<work-id>/brief.md` exists.
- The work is bounded enough to finish without epic validation.
- Use `templates/task.md` and `templates/state.json` from this skill folder.
- Do not create tickets, wave plans, shared-context resources, epic validation,
  or final PR artifacts for micro-waves unless the user asks to upgrade.

## Workflow

1. Load context:
   - Constitution.
   - Work brief.
   - Named files, docs, and tests from the brief.

2. Choose task shape:
   - Prefer 2-5 tasks.
   - Use one task if parallelism would add cost without reducing risk or time.
   - Upgrade to an epic when the work needs multiple waves, ticket containers,
     broad shared context, or high-risk validation.

3. Create compact task briefs in `.wdd/work/<work-id>/tasks/`:
   - Objective.
   - Scope and non-scope.
   - Context to read.
   - Likely files.
   - Dependencies.
   - Conflict domains.
   - Validation.
   - Done criteria.

4. Write `state.json`:
   - Include `"schemaVersion": 1`.
   - Include `profile: "micro"`.
   - Track task path, status, branch, worktree, gate, risk, review requirement,
     verification, PR or patch, and worker reference.
   - Include adaptive monitoring state and a durable fallback prompt.

5. Update `brief.md`:
   - Set `status: planned`.
   - Set `task_count`.
   - Add task inventory to Finish Notes when useful.

## Done When

- `tasks/*.md` exists for each planned micro task.
- `state.json` exists and tracks every task.
- The next phase is `wdd-run-work`, no WDD, or upgrade to an epic.
