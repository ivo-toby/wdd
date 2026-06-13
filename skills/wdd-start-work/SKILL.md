---
name: wdd-start-work
description: Start a WDD micro-wave work packet for one bounded ticket or request that may split into a few parallel tasks without full epic ceremony. Use when the user asks for WDD micro, micro-wave, ticket-sized WDD, or controlled parallelism for a single chunky task.
---

# WDD Start Work

Create a compact work packet under `.wdd/work/` for one bounded request.

## Preconditions

- `.wdd/constitution.md` should exist. If `.wdd/` is missing, use
  `wdd-init-project` first unless the user only wants guidance.
- Work from the repository root.
- Do not create task files yet unless the work brief is clear enough to split.
- Use `templates/brief.md` from this skill folder when creating `brief.md`.

## Workflow

1. Determine work identity:
   - Slug: 2-5 words, lowercase kebab-case.
   - ID: `WORK-<slug>` unless the constitution defines another convention.
   - Folder: `.wdd/work/<work-id>/`.
   - Profile: `micro`.
   - Target branch: use constitution target branch.
   - Base branch: usually `work/<slug>`.

2. Create folder structure:

   ```text
   .wdd/work/<work-id>/
     brief.md
     tasks/
   ```

3. Write `brief.md`:
   - Summary.
   - Goal.
   - Scope.
   - Non-Scope.
   - Relevant Context.
   - Parallelization Notes.
   - Validation Strategy.
   - Definition of Done.
   - Open Questions.
   - Finish Notes.

4. Decide whether micro-wave planning is appropriate:
   - If the work is one small edit, recommend no WDD and stop.
   - If the work can split into 2-5 independent tasks, next phase is
     `wdd-plan-work`.
   - If the work is broader than one bounded ticket, recommend `wdd-start-epic`
     with `lite`, `standard`, or `full`.

## Done When

- `.wdd/work/<work-id>/brief.md` exists or a better lane is recommended.
- The brief records `profile: micro`.
- The next phase is `wdd-plan-work`, no WDD, or `wdd-start-epic`.
