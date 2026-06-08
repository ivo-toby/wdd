# WDD Epic-Task-Wave Pivot Design

## Context

The current WDD repository is already a skill-driven workflow: Markdown skills
are the runtime, `.wdd/` is the durable project state, and external systems are
optional adapters. The uploaded implementation brief asks for a pivot from a
ticket-execution model to an epic-first, ticket/task/wave model that is
coherent, resumable, reviewable, and hard to misuse.

The approved direction is to evolve the existing skill pack in place, keeping
`.wdd/epics/...` as the artifact root instead of moving epics to top-level
folders.

## Goals

- Make epics the planning and validation boundary.
- Make tickets containers for related work.
- Make tasks the independently executable unit assigned to worker agents.
- Group tasks into dependency-aware and conflict-aware waves.
- Preserve role separation between controller, worker, reviewer,
  feedback-fix, validator, and human reviewer.
- Store all workflow state in portable text artifacts.
- Support context-compression-safe orchestration through local durable state.
- Keep the framework usable in cloud agents such as Claude Code Cloud and
  Codex Cloud.

## Non-Goals

- Do not create or require a runtime CLI.
- Do not require scripts, Node.js, npm, local binaries, or generated validators.
- Do not make GitHub Projects a required storage backend.
- Do not let worker agents merge their own PRs.
- Do not merge task work directly into the target branch, usually `main`.

## Recommended Approach

Evolve the existing skill pack and templates in place.

This preserves useful existing behavior:

- local Markdown as source of truth
- skill-driven phase execution
- controller/implementation separation
- RED/GREEN TDD expectations
- high-rigor review gate
- P1/P2/P3 review labels
- heartbeat loop
- feedback routing
- wave reconciliation

It changes the artifact contract so tasks, not tickets, are executable.

## Artifact Model

The durable local structure is:

```text
.wdd/
  constitution.md
  templates/
  epics/
    EPIC-slug/
      epic.md
      wave-plan.md
      orchestration.json
      controller-state.md
      epic-validation.md
      final-pr.md

      shared-context/
        index.md
        resources/
          architecture.md
          discovered-conventions.md
          testing-strategy.md
          validation-strategy.md
          task-findings.md

      TICKET-001-slug/
        ticket.md
        todo/
          TASK-001-slug.md
        in-progress/
        review/
        done/
        blocked/
        cancelled/
```

`epic.md` is the implementation-ready epic specification.

`ticket.md` summarizes a meaningful slice of the epic and lists the tasks it
contains.

`TASK-*.md` files are the durable executable units. A task file moves through
kanban folders as its status changes. The file is moved, not copied.

`shared-context/index.md` provides progressive disclosure: short summaries,
links to focused resources, key decisions, warnings, constraints, and recent
worker discoveries.

`shared-context/resources/*.md` holds focused research and durable worker memory.

`wave-plan.md` is the human-readable task wave plan.

`orchestration.json` is the machine-readable resume surface for active waves,
task gates, branches, PRs, reviews, verification, and blockers.

`controller-state.md` is the human-readable operational log and heartbeat state.

## Skill Workflow

The phase order becomes:

```text
wdd-init-project
wdd-constitution
wdd-start-epic
wdd-plan-epic
wdd-start-wave
subagent-pr-orchestration
wdd-reconcile-wave
wdd-epic-validation
wdd-final-pr
wdd-status
```

`wdd-init-project` creates `.wdd/`, copies text templates, and explains that
external trackers are adapters.

`wdd-constitution` asks for setup decisions and records them:

- available model aliases
- model usage preferences for planning, simple implementation, complex
  implementation, review, feedback-fix, epic validation, and PR generation
- storage mode: local Markdown, GitHub Projects, or both when supported
- target branch
- epic and task branch naming conventions
- PR requirements
- local patch fallback policy
- review model
- whether P2 blocks merge
- whether P3 becomes follow-up work
- where review comments are written
- whether feedback fixes prefer the original worker or a fresh worker

`wdd-start-epic` turns vague input into an implementation-ready epic. It may
inspect code, docs, tests, existing skills, and current external docs when
needed. It should push back on vague, unsafe, or unrealistic requirements.

`wdd-plan-epic` replaces the current separate ticket-writing, ticket-validation,
and wave-planning phases. It creates or updates:

- `shared-context/`
- ticket folders with `ticket.md`
- task files under each ticket's `todo/`
- task dependencies
- conflict domains
- model-class assignments
- `wave-plan.md`
- `orchestration.json`
- initial `controller-state.md`

The existing `wdd-write-tickets`, `wdd-validate-tickets`, and `wdd-plan-waves`
skills should become compatibility wrappers that point users to `wdd-plan-epic`
and explain how their former responsibilities are now handled.

`wdd-start-wave` selects the next pending wave as a batch of concurrently
eligible tasks. It marks the wave in progress and dispatches every task in the
active wave that has no unresolved dependency, no active conflict-domain
blocker, no stale prerequisite, and no explicit blocked status. Each worker
still receives exactly one task file, but multiple worker agents may run at the
same time. Task files are the implementation briefs; the workflow should not
create a separate canonical brief artifact.

`subagent-pr-orchestration` coordinates workers and reviewers from task files
and orchestration state. It tracks each active task independently and does not
implement task code.

`wdd-reconcile-wave` runs after all active-wave tasks are merged, closed,
blocked, or cancelled. It updates future tasks for drift before the next wave
starts.

`wdd-epic-validation` validates the full epic branch after all waves finish.

`wdd-final-pr` prepares the final epic PR from the epic branch into the target
branch, with a comprehensive human-review description.

`wdd-status` remains read-only by default and reports actual artifact state.

## Branching And Merge Model

The workflow uses an epic base branch.

```text
target branch, usually main
└── epic/[epic-slug]
    ├── task/[task-id]-[task-slug]
    ├── task/[task-id]-[task-slug]
    └── task/[task-id]-[task-slug]
```

Worker agents create task branches from the epic branch. Task PRs target the
epic branch.

Worker agents never merge their own PRs.

The controller/orchestrator owns the task merge gate. After verification and
review gates pass, it either merges the task PR into the epic branch or records
the task as `merge_ready` when repository policy requires a human to merge.

Before merging a task PR into the epic branch, the controller must check whether
the task branch is stale relative to the current epic branch. If stale, the
controller must require the worker or feedback-fix agent to rebase onto the
latest epic branch or merge the latest epic branch into the task branch, rerun
relevant verification, and rerun review when touched areas changed materially.
No stale task branch should be merged blindly.

The final epic PR targets the original target branch and is created only after
epic-level validation passes.

## Task Execution

Each worker receives exactly one task.

The worker must:

1. Move the task file from `todo/` to `in-progress/`.
2. Read the task file and relevant shared context.
3. Create or use the task branch from the epic branch.
4. Inspect named files and domains before broad discovery.
5. Stay within scope.
6. Avoid starting dependent tasks.
7. Follow RED/GREEN TDD unless the task explains why it is inapplicable.
8. Produce verification evidence.
9. Self-review the changes.
10. Write durable shared-context memory when useful.
11. Create a PR or patch.
12. Move the task file to `review/`.
13. Return one final token: `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or
    `BLOCKED`.

## Review And Feedback

When a task PR or patch exists, the controller starts a separate review agent
where available.

Review findings are classified:

- P1: blocking, must fix
- P2: important, blocks merge by default
- P3: non-blocking suggestion

The controller routes unresolved P1/P2 feedback back to the original worker
when its context is still useful, or to a fresh feedback-fix worker when safer.
Feedback-fix work must not broaden task scope.

Only the controller can move a task from `review/` to `done/`, and only after
verification and review gates pass or repository policy records a human-owned
merge decision.

## Durable Worker Memory

Worker discoveries that matter to later work go into shared context. They
should be concise and focused, not transcript dumps.

Each durable memory item uses this shape:

```markdown
### Short Title

- Source task: TASK-001
- Source PR/branch: task/TASK-001-example
- Status: confirmed | inferred | needs verification
- Summary:
- Why it matters:
- Affected files or areas:
- Follow-up implications:
```

Workers may append to an existing resource file, create a focused new resource,
and update `shared-context/index.md` with a short pointer.

Because workers may run concurrently, shared-context writes follow lightweight
discipline:

- Workers may propose shared-context updates in their task branch.
- The controller owns reconciliation of those updates into the epic branch.
- If two workers update the same shared-context resource, the controller
  resolves the conflict during review or wave reconciliation.
- Workers should prefer focused resource files over editing large shared files.

## Orchestration State

`orchestration.json` must be sufficient for a new controller instance to resume
without hidden conversation context.

It includes a schema version:

```json
{
  "schemaVersion": 1
}
```

Future workflow pivots should be able to migrate or at least detect old
orchestration state.

It tracks:

- schema version
- epic ID and title
- target branch
- epic base branch
- storage and model configuration
- wave order and status
- task order
- task dependencies
- task file paths
- task status
- ticket parent
- conflict domains
- assigned worker model class
- review model class
- worker thread or agent reference when available
- review thread or agent reference when available
- branch
- PR or patch reference
- latest commit
- branch freshness relative to the epic branch
- unresolved P1/P2 feedback
- verification result
- current gate

The controller updates this file after every meaningful event.

## Text-Only Portability

All workflow behavior must be expressible in Markdown skills, Markdown
templates, hand-editable JSON templates, and local artifacts.

The framework must not require:

- a CLI command
- a validator script
- Node.js
- npm
- shell helpers
- generated workflow files
- local-only automation

Repository-native checks can still be referenced as optional or repo-specific
verification when available. Examples include `git diff --check`, test
commands, linters, type checks, builds, and CI status. The workflow itself must
remain text-only and portable.

Repo verification for this pivot should use manual/static inspection:

- required skill files exist
- required templates exist
- skills describe the task-based artifact model
- skills do not require scripts or CLI commands
- templates match the approved `.wdd/epics/...` structure
- branching and merge policy is explicit
- `git diff --check` passes for whitespace sanity

## Implementation Plan Summary

1. Update README and artifact schema to describe the epic-ticket-task-wave
   model and text-only portability.
2. Add or update templates for task files, shared context, orchestration JSON,
   epic validation, and final PR.
3. Update existing skills to use tasks as executable units.
4. Add `wdd-plan-epic`, `wdd-epic-validation`, and `wdd-final-pr`.
5. Convert legacy planning skills into compatibility wrappers.
6. Retire the old implementation-brief template as a canonical artifact.
7. Remove script/CLI-oriented development scaffolding that conflicts with the
   text-only requirement.
8. Verify by static inspection and `git diff --check`.

## Approval Record

- Keep `.wdd/epics/...` as the artifact root.
- Use approach 1: evolve the current skill pack in place.
- Adopt ticket-contained task kanban folders.
- Make the controller/orchestrator the worker PR merge gatekeeper.
- Make all skills text-only and avoid scripts so the framework remains
  portable to cloud agents.
- Dispatch active-wave tasks concurrently when dependencies, conflict domains,
  prerequisites, and blocked status allow it.
- Version `orchestration.json` with `schemaVersion: 1`.
- Require a stale-branch/rebase gate before task PR merge.
- Let workers propose shared-context updates while the controller reconciles
  concurrent shared-context writes.
- Allow repo-native verification commands as optional checks without making the
  workflow depend on a CLI or scripts.
