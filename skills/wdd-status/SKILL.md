---
name: wdd-status
description: Show current Wave-Driven Development state from local .wdd text artifacts, including constitution status, epics, tickets, tasks, waves, orchestration gates, monitoring, shared-context reconciliation, validation, and next action.
---

# WDD Status

Use this when the user asks for progress, current state, next step, active wave,
blockers, task gates, or a dashboard view.

## User Input

If the user names an epic, report that epic. If the user asks for all state,
scan all `.wdd/epics/*/` folders.

## Preconditions

- Read-only unless the user explicitly asks to fix stale state.
- Do not infer completion without artifact evidence.
- Do not implement task code.

## Workflow

1. Check `.wdd/`:
   - If missing, report that WDD is not initialized and suggest
     `wdd-init-project`.
   - If present, read `.wdd/constitution.md`.

2. Scan epics:
   - Read each `epic.md`.
   - Detect ticket folders.
   - Count task files by status folder.
   - Read `wave-plan.md` if present.
   - Read `orchestration.json` if present.
   - Read `controller-state.md` if present.
   - Check for `epic-validation.md` and `final-pr.md`.

3. For each relevant epic, report:
   - Epic ID, title, and status.
   - Target branch and epic branch.
   - Ticket count.
   - Task count by status.
   - Wave plan status.
   - Active wave if any.
   - Active task gates.
   - Monitoring mode, cadence, status, scheduler reference, last check, next
     check, and whether manual fallback is required.
   - Branch freshness issues.
   - Open P1/P2 feedback.
   - Shared-context reconciliation status.
   - Epic validation state.
   - Final PR state.
   - Blockers.

4. Determine next action:
   - Missing constitution: `wdd-constitution`.
   - Epic draft without planning artifacts: `wdd-plan-epic`.
   - Planning artifacts incomplete: `wdd-plan-epic`.
   - Pending wave: `wdd-start-wave`.
   - Active wave with open gates: `subagent-pr-orchestration`.
   - Active wave with stale or missing monitoring: resume
     `subagent-pr-orchestration` using the fallback prompt in
     `controller-state.md`.
   - Completed active wave: `wdd-reconcile-wave`.
   - All waves complete without validation: `wdd-epic-validation`.
   - Validation passed without final PR draft: `wdd-final-pr`.

5. Output:
   - Keep concise by default.
   - Use tables only when multiple epics, tasks, or gates are present.
   - Include exact artifact paths.

## Done When

- Status reflects actual `.wdd/` artifacts.
- The next recommended skill is named.
- No files are modified unless the user explicitly requested repair.
