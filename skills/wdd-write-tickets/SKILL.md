---
name: wdd-write-tickets
description: Compatibility wrapper for older WDD prompts that asked to write tickets; route the work into wdd-plan-epic because tickets, tasks, validation, waves, and orchestration are now planned together.
---

# WDD Write Tickets Compatibility Wrapper

Use this when the user asks for the older ticket-writing phase.

## User Input

Interpret requests to write tickets as requests to plan the epic enough to
produce ticket containers and executable task files.

## Preconditions

- `.wdd/constitution.md` exists.
- Epic folder and `epic.md` exist.
- The controller is planning and must not implement task code.

## Workflow

1. Explain internally that standalone ticket writing has been folded into
   `wdd-plan-epic`.
2. Invoke or follow `wdd-plan-epic`.
3. Ensure output includes:
   - Ticket folders with `ticket.md`.
   - Task files in `todo/`.
   - Task dependencies.
   - Conflict domains.
   - Shared-context references.
   - Wave plan.
   - `orchestration.json`.
   - `controller-state.md`.

## Done When

- The request has been routed to `wdd-plan-epic`.
- Ticket containers and executable task files exist, or blockers are recorded.
- The next phase is `wdd-start-wave` when planning is complete.
