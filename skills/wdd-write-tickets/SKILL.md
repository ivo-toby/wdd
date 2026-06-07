---
name: wdd-write-tickets
description: Decompose a WDD epic into local markdown tickets with YAML frontmatter, explicit deliverables, dependencies, conflict domains, verification, and review handoff notes.
---

# WDD Write Tickets

Use this when an epic has enough PRD/design context and needs implementation tickets.

## Ticket Requirements

Each ticket must include frontmatter for:

- `id`, `kind`, `epic`, `slug`, `title`, `status`
- `depends_on`
- `conflict_domains`
- `branch`
- `verification`
- `adapter_links`

Each ticket body must include:

- Context
- End Goal / Deliverable
- Scope
- RED/GREEN TDD
- Acceptance Criteria
- Verification
- Review Handoff
- Out of Scope

## Workflow

1. Read the epic, PRD, design, constitution, and likely touched files.
2. Create one bounded ticket per logical deliverable with `wdd ticket create`.
3. Edit generated ticket bodies so they are pick-up-ready for one implementation agent.
4. Prefer explicit dependencies over prose sequencing.
5. Name conflict domains conservatively for shared files, schemas, config, migrations, generated types, and shared tests.
6. Run `wdd validate <epic> --json`.

