---
name: wdd-start-epic
description: Start a profiled WDD epic by turning broad feature, migration, refactor, hardening, or bug-cluster input into an implementation-ready epic specification and progressive shared-context scaffold under .wdd/epics. Use wdd-start-work instead for single chunky tickets that fit micro-wave scope.
---

# WDD Start Epic

Use this when the user wants to start a feature, spike, migration, refactor,
hardening effort, bug cluster, or other broad work item. If the request is one
bounded ticket that can split into only a few tasks, recommend `wdd-start-work`
instead unless the user explicitly wants an epic.

## User Input

Treat the user request as the epic seed. Extract desired outcome, type,
constraints, non-goals, risks, preferred WDD profile, and definition of done.
Ask only for missing information that blocks planning or architecture.

## Preconditions

- `.wdd/constitution.md` exists and has no blocking setup gaps.
- Work from the repository root.
- Read relevant existing code, docs, tests, and skills before choosing an epic
  shape.
- Do not create task files yet unless the user explicitly asks and the epic is
  ready for planning.
- Use this skill folder's `templates/epic.md`,
  `templates/shared-context-index.md`, and
  `templates/shared-context-resource.md` as starting points when creating those
  artifacts. Do not require `.wdd/templates/` to exist.

## Workflow

1. Determine epic identity:
   - Type: `feature`, `spike`, `migration`, `refactor`, or `hardening`.
   - Slug: 2-5 words, lowercase kebab-case.
   - ID: `EPIC-<slug>` unless the project constitution defines another
     convention.
   - Folder: `.wdd/epics/<epic-id>/`.
   - Epic branch: apply constitution convention, usually `epic/[epic-slug]`.
   - Target branch: use constitution target branch.
   - Profile: use explicit user choice, otherwise constitution default. Valid
     epic profiles are `lite`, `standard`, and `full`; use `wdd-start-work` for
     `micro`.
   - Review mode: use epic or constitution default, usually `risk_based`.
   - Monitoring mode: use epic or constitution default, usually `adaptive`.

2. Create folder structure:

   ```text
   .wdd/epics/<epic-id>/
     epic.md
     shared-context/
       index.md
       resources/
   ```

3. Write `epic.md` with frontmatter and sections:
   - Start from `templates/epic.md` in this skill folder when available.
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
   - Frontmatter must include `profile`, `review_mode`, and `monitoring_mode`.

4. Create `shared-context/index.md`:
   - Start from `templates/shared-context-index.md` in this skill folder when
     available.
   - Keep it short.
   - List available or expected resource files.
   - Record key decisions, warnings, constraints, and recent durable memory.

5. Create focused shared-context resources when useful:
   - Start from `templates/shared-context-resource.md` in this skill folder when
     available.
   - `architecture.md`
   - `discovered-conventions.md`
   - `testing-strategy.md`
   - `validation-strategy.md`
   - `task-findings.md`
   - Domain-specific resources required for this epic.

6. Push back where needed:
   - Vague goals.
   - Unrealistic scope.
   - Work that should be a micro-wave instead of an epic.
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
- `epic.md` records `profile`, `review_mode`, and `monitoring_mode`.
- `shared-context/index.md` exists.
- Initial focused shared-context resources exist when useful.
- The next phase is `wdd-plan-epic`, or the user is asked a specific blocking
  question.
