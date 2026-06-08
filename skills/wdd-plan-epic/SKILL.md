---
name: wdd-plan-epic
description: Plan a WDD epic end to end by creating shared context, ticket containers, task kanban files, dependency and conflict grids, waves, schema-versioned orchestration state, and controller state.
---

# WDD Plan Epic

Use this after an epic has a sufficiently clear goal, deliverables, constraints,
definition of done, and planning notes.

## User Input

Respect user constraints such as maximum parallelism, preferred review cadence,
model usage, storage mode, high-risk areas, sequencing constraints, and scope
exclusions. Do not require the user to repeat context already present in the
epic or shared-context artifacts.

## Preconditions

- `.wdd/constitution.md` exists.
- Epic folder exists under `.wdd/epics/`.
- `epic.md` exists and is ready for planning.
- The controller is still planning and must not implement task code.
- WDD operation remains text-only. Do not depend on a CLI, scripts, generated
  validators, Node.js, npm, or local binaries.

## Workflow

1. Load context:
   - Constitution.
   - Epic.
   - Existing shared context.
   - Relevant repo files named by the epic.
   - Relevant docs and tests.
   - Existing task or ticket artifacts if this is a replan.

2. Build or update progressive shared context:
   - Keep `shared-context/index.md` concise.
   - Create focused `shared-context/resources/*.md` files for architecture,
     conventions, testing, validation, API contracts, SDK notes, migration
     notes, or other useful planning context.
   - Record key decisions, warnings, constraints, and recent durable memory in
     the index.

3. Identify ticket containers:
   - Tickets group related work.
   - Tickets are meaningful slices of the epic such as domain model, API,
     migration, UI, integration, tests, docs, or refactor phase.
   - Tickets are not assigned directly to workers.
   - Each ticket folder is `.wdd/epics/<epic-id>/<ticket-id>/`.

4. Break tickets into task files:
   - Each task is independently executable by one worker.
   - Each task fits in one focused implementation loop.
   - Each task has objective, scope, non-scope, local context, shared-context
     references, likely files, dependencies, conflict domains, model class,
     RED/GREEN TDD plan, definition of done, validation steps, branch, and PR
     expectations.
   - Place new task files in the ticket's `todo/` folder.

5. Create every ticket folder with kanban folders:

   ```text
   <ticket-id>/
     ticket.md
     todo/
     in-progress/
     review/
     done/
     blocked/
     cancelled/
   ```

6. Define dependencies:
   - Use task IDs for task dependencies.
   - Foundation tasks block consumers.
   - Data, schema, public API, generated type, config, and migration tasks
     precede dependent runtime, UI, and test tasks.
   - Detect cycles and fix them before wave planning.

7. Define conflict domains:
   - Include likely files, broad directories, migrations, schemas, generated
     code, package manifests, lockfiles, shared fixtures, auth boundaries,
     persistence boundaries, and central config.
   - Treat conflict domains as planning signals, not ownership claims.

8. Assign model classes:
   - Use constitution aliases.
   - Stronger models should handle epic planning, review, feedback fixes when
     risky, and epic validation.
   - Implementation tasks should use the cheapest capable model class unless
     complexity or risk requires more.

9. Build wave plan:
   - Waves schedule tasks, not tickets.
   - Start with tasks that have no unmet dependencies.
   - Add tasks to the same wave only when conflict-domain risk is acceptable.
   - Be aggressive about safe parallelism and conservative around shared types,
     migrations, generated files, central config, public APIs, persistence,
     auth, test fixtures, package manifests, and lockfiles.
   - Record stop conditions requiring reconciliation before the next wave.

10. Write `wave-plan.md`:
    - Task inventory.
    - Dependency grid.
    - Conflict grid.
    - One section per wave.
    - Activation rules.
    - Stop conditions.
    - Known conflict risks.
    - Manual adjustments and rationale.

11. Write `orchestration.json`:
    - Include `"schemaVersion": 1`.
    - Track epic ID, target branch, epic branch, model configuration, storage
      mode, waves, task order, task file paths, dependencies, conflict domains,
      assigned models, review models, worker/review references, branches, PRs,
      latest commits, branch freshness, blocking feedback, verification, and
      current gates.

12. Write initial `controller-state.md`:
    - State controller rule.
    - List pending waves.
    - Track current gates for planned tasks.
    - Include branch freshness table.
    - Include shared-context reconciliation notes.
    - Include next action.

13. Write or update `validation-checklist.md`:
    - Validate epic readiness.
    - Validate ticket and task structure.
    - Validate dependency graph.
    - Validate conflict domains.
    - Validate wave activation readiness.
    - Validate orchestration schema and coverage.
    - Validate text-only portability.

14. Update `epic.md` frontmatter and planning notes:
    - Set ticket and task counts.
    - Update status to `planned` when planning is complete.
    - Update the date.

## Done When

- `shared-context/index.md` exists and points to focused resources.
- Ticket folders exist with `ticket.md` and kanban folders.
- Task files exist under ticket `todo/` folders.
- `wave-plan.md` schedules tasks.
- `orchestration.json` exists with `schemaVersion: 1`.
- `controller-state.md` exists.
- `validation-checklist.md` records current planning readiness.
- The next phase is `wdd-start-wave`, or planning blockers are recorded with
  specific user-needed questions.
