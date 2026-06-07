---
name: wdd-init-project
description: Initialize a repository for skill-driven Wave-Driven Development by creating the .wdd artifact structure, templates, constitution placeholder, and local agent-facing state without relying on runtime commands.
---

# WDD Init Project

Use this when a project does not yet have `.wdd/` artifacts or when the user asks to adopt WDD.

## User Input

Use any user-provided project name, boundaries, preferred agent, or storage guidance. If no guidance is provided, infer conservative defaults from repository docs and code.

## Preconditions

- Work from the repository root.
- Read repo instructions first.
- If `.wdd/constitution.md` already exists, do not overwrite it.
- If `.wdd/` exists, inspect it and update only missing scaffolding.

## Workflow

1. Inspect project context:
   - Read `AGENTS.md` if present.
   - Read `README.md` if present.
   - Identify package manager, language, test commands, and repo ownership boundaries.

2. Create directories if missing:

   ```text
   .wdd/
     epics/
     templates/
   ```

3. Create `.wdd/constitution.md` if missing:
   - Use the root `templates/constitution.md` when available.
   - Otherwise create the constitution from the schema in `wdd-constitution`.
   - Fill obvious values from repo context.
   - Leave explicit `TODO(...)` markers only for values that cannot be inferred safely.

4. Copy or create local templates under `.wdd/templates/`:
   - `constitution.md`
   - `epic.md`
   - `ticket.md`
   - `wave-plan.md`
   - `controller-state.md`
   - `implementation-brief.md`
   - `validation-checklist.md`

5. Create `.wdd/README.md`:
   - Explain that `.wdd/` is the durable source of truth.
   - List phase order: constitution, epic, tickets, validation, waves, start wave, orchestration, reconcile.
   - State that external trackers are adapters.

6. Report:
   - Files created.
   - Files preserved.
   - Missing constitution fields.
   - Recommended next phase.

## Done When

- `.wdd/` exists.
- `.wdd/constitution.md` exists and was not overwritten.
- `.wdd/templates/` contains all artifact templates.
- The next phase is `wdd-constitution` if placeholders remain, otherwise `wdd-start-epic`.

