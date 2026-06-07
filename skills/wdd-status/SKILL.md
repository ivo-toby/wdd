---
name: wdd-status
description: Show current Wave-Driven Development state from local .wdd markdown artifacts, including constitution status, active epics, ticket counts, wave progress, controller gates, and next actions.
---

# WDD Status

Use this when the user asks for progress, current state, next step, active wave, blockers, or a dashboard view.

## User Input

If the user names an epic, report that epic. If the user asks for all state, scan all `.wdd/epics/*/` folders.

## Preconditions

- Read-only unless the user explicitly asks to fix stale state.
- Do not infer completion without artifact evidence.
- Do not implement code.

## Workflow

1. Check `.wdd/`:
   - If missing, report that WDD is not initialized and suggest `wdd-init-project`.
   - If present, read `.wdd/constitution.md`.

2. Scan epics:
   - Read each `epic.md`.
   - Count tickets.
   - Detect status from epic frontmatter, ticket frontmatter, `wave-plan.md`, and `controller-state.md`.

3. For each relevant epic, report:
   - Epic ID/title/status.
   - Ticket count by status.
   - Wave plan status.
   - Active wave if any.
   - Controller gates if active.
   - Blockers.

4. Determine next action:
   - Missing constitution: `wdd-constitution`.
   - Epic draft with no tickets: `wdd-write-tickets`.
   - Tickets not validated: `wdd-validate-tickets`.
   - Validated tickets without waves: `wdd-plan-waves`.
   - Pending wave: `wdd-start-wave`.
   - Active wave with open gates: `subagent-pr-orchestration`.
   - Merged active wave: `wdd-reconcile-wave`.

5. Output:
   - Keep concise by default.
   - Use tables only when multiple epics or gates are present.
   - Include exact artifact paths.

## Done When

- Status reflects actual `.wdd/` artifacts.
- The next recommended skill is named.
- No files are modified.

