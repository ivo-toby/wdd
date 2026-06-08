---
name: wdd-plan-waves
description: Compatibility wrapper for older WDD wave-planning prompts; route work into wdd-plan-epic because waves now schedule executable task files and require orchestration state.
---

# WDD Plan Waves Compatibility Wrapper

Use this when the user asks for the older wave-planning phase.

## User Input

Interpret wave-planning requests as requests to plan or replan task waves for an
epic, including dependencies, conflict domains, orchestration state, and
controller state.

## Preconditions

- Epic folder exists.
- Task files either exist or can be created from the epic.
- The controller must not implement task code.

## Workflow

1. Explain internally that standalone wave planning has been folded into
   `wdd-plan-epic`.
2. Invoke or follow `wdd-plan-epic` planning steps.
3. Ensure waves schedule tasks, not tickets.
4. Ensure each wave documents:
   - Eligible tasks.
   - Dependencies.
   - Conflict-domain decisions.
   - Activation rule for concurrent dispatch.
   - Stop condition requiring reconciliation.
5. Ensure `orchestration.json` and `controller-state.md` match the wave plan.

## Done When

- `wave-plan.md` is internally consistent.
- Every planned task has a wave assignment.
- `orchestration.json` tracks every planned task.
- The next phase is `wdd-start-wave`.
