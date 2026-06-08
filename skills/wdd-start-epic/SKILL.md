---
name: wdd-start-epic
description: Start a WDD epic by turning vague input into an implementation-ready epic specification and progressive shared-context scaffold under .wdd/epics.
---

# WDD Start Epic

Use this when the user wants to start a feature, spike, migration, refactor,
hardening effort, bug cluster, or other broad work item.

## User Input

Treat the user request as the epic seed. Extract desired outcome, type,
constraints, non-goals, risks, and definition of done. Ask only for missing
information that blocks planning or architecture.

## Preconditions

- `.wdd/constitution.md` exists and has no blocking setup gaps.
- Work from the repository root.
- Read relevant existing code, docs, tests, and skills before choosing an epic
  shape.
- Do not create task files yet unless the user explicitly asks and the epic is
  ready for planning.

## Workflow

1. Determine epic identity:
   - Type: `feature`, `spike`, `migration`, `refactor`, or `hardening`.
   - Slug: 2-5 words, lowercase kebab-case.
   - ID: `EPIC-<slug>` unless the project constitution defines another
     convention.
   - Folder: `.wdd/epics/<epic-id>/`.
   - Epic branch: apply constitution convention, usually `epic/[epic-slug]`.
   - Target branch: use constitution target branch.

2. Create folder structure:

   ```text
   .wdd/epics/<epic-id>/
     epic.md
     shared-context/
       index.md
       resources/
   ```

3. Write `epic.md` with frontmatter and sections:
   - Summary.
   - Goal.
   - Background.
   - Product Context.
   - Technical Context.
   - Deliverables.
   - Non-Goals.
   - Assumptions.
   - Constraints.
   - Risks.
   - Dependencies.
   - Affected Areas.
   - Validation Strategy.
   - Definition of Done.
   - Open Questions.
   - Planning Notes.

4. Create `shared-context/index.md`:
   - Keep it short.
   - List available or expected resource files.
   - Record key decisions, warnings, constraints, and recent durable memory.

5. Create focused shared-context resources when useful:
   - `architecture.md`
   - `discovered-conventions.md`
   - `testing-strategy.md`
   - `validation-strategy.md`
   - `task-findings.md`
   - Domain-specific resources required for this epic.

6. Push back where needed:
   - Vague goals.
   - Unrealistic scope.
   - Unsafe assumptions.
   - Missing validation.
   - Hidden architecture or migration risks.

7. Validate epic readiness:
   - Goal is clear.
   - Deliverables are concrete.
   - Definition of done is testable.
   - Major unknowns are called out.
   - Scope boundaries are explicit.
   - Open questions do not block planning.

## Done When

- Epic folder exists under `.wdd/epics/`.
- `epic.md` is implementation-ready or records explicit blockers.
- `shared-context/index.md` exists.
- Initial focused shared-context resources exist when useful.
- The next phase is `wdd-plan-epic`, or the user is asked a specific blocking
  question.
