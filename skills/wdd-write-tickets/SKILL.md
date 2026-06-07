---
name: wdd-write-tickets
description: Decompose a WDD epic into detailed local markdown tickets with YAML frontmatter, explicit deliverables, dependencies, conflict domains, TDD guidance, acceptance criteria, verification, and review handoff.
---

# WDD Write Tickets

Use this after an epic has a sufficiently clear PRD and design.

## User Input

Consider any requested ticket count, priority, scope exclusions, or implementation strategy. Do not require the user to supply hidden planning context that is already in the epic artifacts.

## Preconditions

- `.wdd/constitution.md` exists.
- Epic folder exists under `.wdd/epics/`.
- `epic.md`, `prd.md`, and `design.md` exist.
- The controller is still planning; do not implement code.

## Workflow

1. Load context:
   - Constitution.
   - Epic.
   - PRD.
   - Design.
   - Decision records.
   - Relevant repo files named by the design.

2. Identify ticket boundaries:
   - One logical deliverable per ticket.
   - Ticket can be assigned to one implementation agent.
   - Ticket can be reviewed independently.
   - Ticket does not require hidden conversation context.

3. Assign IDs:
   - Tickets use `<epic-id>-TNNN`.
   - Slugs use lowercase kebab-case.
   - Files live at `tickets/<ticket-id>-<slug>.md`.

4. Write each ticket with required frontmatter:

   ```yaml
   id: WDD-0001-T001
   kind: ticket
   epic: WDD-0001
   slug: example-ticket
   title: Example Ticket
   status: todo
   wave: null
   depends_on: []
   conflict_domains:
     - path/or/domain/**
   branch: codex/wdd-0001-t001-example-ticket
   verification:
     - command proving the ticket works
   adapter_links:
     github_issue: null
     pull_request: null
   ```

5. Write each ticket body with required sections:
   - Context
   - End Goal / Deliverable
   - Scope
   - RED/GREEN TDD
   - Acceptance Criteria
   - Verification
   - Review Handoff
   - Out of Scope

6. Define dependencies:
   - Use ticket IDs only.
   - Foundation tickets should block consumers.
   - Data/API/contracts precede UI/runtime consumers.
   - Shared config/schema/migration work precedes dependent tickets.

7. Define conflict domains:
   - Include shared files, package manifests, generated types, schemas, migrations, config, path aliases, shared tests, and broad directories.
   - Use conservative domains when uncertain.
   - Conflict domains are for wave planning, not ownership.

8. Update `epic.md` frontmatter:
   - Set `ticket_count`.
   - Update status to `ticketed` if tickets are ready.
   - Update `updated_at`.

9. Create or update `validation-checklist.md` from the template.

## Ticket Quality Bar

Reject or rewrite tickets that:

- Require reading the planning conversation.
- Hide the deliverable in vague prose.
- Lack concrete verification.
- Combine unrelated domains.
- Have dependencies only described in prose.
- Omit out-of-scope.
- Would predictably collide with another parallel ticket.

## Done When

- All ticket files exist with required frontmatter and sections.
- Dependencies and conflict domains are explicit.
- `validation-checklist.md` exists.
- The next phase is `wdd-validate-tickets`.

