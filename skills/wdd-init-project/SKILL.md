---
name: wdd-init-project
description: Initialize a repository for text-only Wave-Driven Development by creating .wdd artifacts, templates, epic storage, and constitution scaffolding without relying on a CLI or scripts.
---

# WDD Init Project

Use this when a project does not yet have `.wdd/` artifacts or when the user
asks to adopt WDD.

## User Input

Use any user-provided project name, boundaries, preferred agents, storage mode,
model preferences, or branch policy. If no guidance is provided, infer only safe
defaults from repository docs and code.

## Preconditions

- Work from the repository root.
- Read repo instructions first.
- If `.wdd/constitution.md` already exists, do not overwrite it.
- If `.wdd/` exists, inspect it and update only missing scaffolding.
- Do not run or require a WDD CLI, script, package manager, or validator.

## Workflow

1. Inspect project context:
   - Read `AGENTS.md` if present.
   - Read `README.md` if present.
   - Identify project type, package manager, verification commands, target
     branch, and ownership boundaries from repo evidence.

2. Create directories if missing:

   ```text
   .wdd/
     epics/
     templates/
   ```

3. Create `.wdd/constitution.md` if missing:
   - Use the root `templates/constitution.md` when available.
   - Fill only values that can be inferred safely.
   - Leave explicit questions in the body for values that require user choice.

4. Copy or create local templates under `.wdd/templates/`:
   - `constitution.md`
   - `epic.md`
   - `ticket.md`
   - `task.md`
   - `wave-plan.md`
   - `controller-state.md`
   - `validation-checklist.md`
   - `shared-context-index.md`
   - `shared-context-resource.md`
   - `orchestration.json`
   - `epic-validation.md`
   - `final-pr.md`

5. Create `.wdd/README.md`:
   - Explain that `.wdd/` is the durable source of truth.
   - List phase order: constitution, epic, plan epic, start wave,
     orchestration, reconcile, epic validation, final PR.
   - State that external trackers are adapters.
   - State that WDD itself is text-only and does not require scripts.

6. Report:
   - Files created.
   - Files preserved.
   - Constitution questions that still need user input.
   - Recommended next phase.

## Done When

- `.wdd/` exists.
- `.wdd/epics/` exists.
- `.wdd/constitution.md` exists and was not overwritten.
- `.wdd/templates/` contains the text templates.
- The next phase is `wdd-constitution` if setup choices remain, otherwise
  `wdd-start-epic`.
