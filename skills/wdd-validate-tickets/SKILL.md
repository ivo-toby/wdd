---
name: wdd-validate-tickets
description: Compatibility wrapper for older WDD validation prompts; route validation into wdd-plan-epic because planning now validates epic, ticket, task, wave, shared-context, and orchestration readiness together.
---

# WDD Validate Tickets Compatibility Wrapper

Use this when the user asks for the older ticket-validation phase.

## User Input

Interpret validation requests as requests to validate the current epic plan:
ticket folders, task files, dependencies, conflict domains, waves, shared
context, orchestration state, and text-only portability.

## Preconditions

- Epic folder exists.
- Planning artifacts exist or are being created.
- The controller must not implement task code.

## Workflow

1. Explain internally that standalone ticket validation has been folded into
   `wdd-plan-epic`.
2. Invoke or follow `wdd-plan-epic` validation steps.
3. Validate:
   - Epic readiness.
   - Ticket folder structure.
   - Task frontmatter and body sections.
   - Task dependency graph.
   - Conflict domains.
   - Wave activation rules.
   - `orchestration.json` with `schemaVersion: 1`.
   - Shared-context structure.
   - Branch and merge policy.
   - Text-only portability.
4. Update `validation-checklist.md` with exact findings and fixes.

## Done When

- `validation-checklist.md` reflects current state.
- Blocking findings are fixed or recorded with specific user-needed questions.
- The next phase is `wdd-start-wave` only when planning validates.
