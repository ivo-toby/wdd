---
name: wave-driven-development
description: Run the text-only Wave-Driven Development workflow using local markdown/json artifacts, including wdd-info routing, micro-wave work packets, profiled epics, concurrent task waves, controller gates, review, validation, and final PR handoff.
---

# Wave-Driven Development

Use this as the overview skill for large features, spikes, migrations, refactors,
hardening work, bug clusters, or any multi-task implementation that benefits
from planned parallel agent execution. Use `wdd-info` first when the user is
asking whether WDD is appropriate or which mode to choose.

## User Input

Treat the user's request as the workflow goal. If the request names a phase, use
the matching WDD phase skill. If the request is broad, start at the earliest
missing phase. If the request is one bounded ticket that may split into a few
parallel tasks, prefer the micro-wave path over a full epic.

## Preconditions

- Work from the repository root.
- Read repo instructions such as `AGENTS.md`, `README.md`, and project-specific
  docs.
- Use `.wdd/` as the durable local source of truth.
- Honor the constitution default profile and any artifact-level `profile`
  override.
- Do not rely on a runtime CLI, script, package manager, generated validator, or
  local binary.
- Keep GitHub, Jira, Linear, Postgram, and similar systems as adapters or
  mirrors, not required storage.

## Workflow

1. Determine current WDD state:
   - If the user asks for orientation, mode choice, ceremony/cost tradeoffs, or
     whether WDD is appropriate, use `wdd-info`.
   - If `.wdd/constitution.md` is missing, use `wdd-init-project`, then
     `wdd-constitution`.
   - If the constitution has blocking setup gaps, use `wdd-constitution`.
   - If the work is one bounded ticket that can split into 2-5 parallel tasks,
     use `wdd-start-work`, then `wdd-plan-work`, `wdd-run-work`, and
     `wdd-finish-work`.
   - If no epic exists for the requested work, use `wdd-start-epic`.
   - If the epic lacks ticket folders, task files, shared context, waves, or
     orchestration state, use `wdd-plan-epic`.
   - If a wave should start or resume, use `wdd-start-wave`, then
     `subagent-pr-orchestration`.
   - If an active wave completed, use `wdd-reconcile-wave`.
   - If all waves completed, use `wdd-epic-validation`.
   - If epic validation passed, use `wdd-final-pr`.
   - If the user asks where things stand, use `wdd-status`.

2. Enforce role separation:
   - The controller plans, activates waves, dispatches workers, monitors gates,
     routes feedback, merges or marks merge-ready, and reconciles drift.
   - Before any worker starts, the controller creates or verifies the epic
     branch that task PRs or patches will merge into.
   - Before dispatching parallel repository-writing workers, the controller
     syncs activation artifacts to the epic branch, creates or verifies one
     isolated worktree per task from that synced state, and records the assigned
     path.
   - Worker agents execute exactly one task file each.
   - Worker agents start in the assigned worktree and must not switch branches
     in the controller checkout.
   - Reviewer agents review one task PR or patch when required and classify
     P1/P2/P3 findings.
   - The controller does not implement task code.
   - Workers do not merge their own PRs.

3. Choose ceremony by profile:
   - `micro`: use `.wdd/work/` and skip ticket containers, wave plans, epic
     validation, and final PR artifacts unless the user upgrades the work.
   - `lite`: keep epic artifacts compact, use adaptive monitoring, and use
     risk-based review.
   - `standard`: use the normal epic workflow with token-conscious defaults.
   - `full`: preserve strict review, validation, monitoring, and auditability.

4. Maintain artifact locality:
   - Constitution: `.wdd/constitution.md`
   - Micro-wave folder: `.wdd/work/<work-id>/`
   - Micro-wave brief: `brief.md`
   - Micro-wave state: `state.json`
   - Micro-wave tasks: `tasks/<task-id>.md`
   - Epic folder: `.wdd/epics/<epic-id>/`
   - Epic: `epic.md`
   - Shared context: `shared-context/index.md` and
     `shared-context/resources/*.md`
   - Ticket container: `<ticket-id>/ticket.md`
   - Task files: `<ticket-id>/<status>/<task-id>.md`
   - Wave plan: `wave-plan.md`
   - Machine state: `orchestration.json` with `schemaVersion: 1`
   - Human controller state: `controller-state.md`
   - Epic validation: `epic-validation.md`
   - Final PR draft: `final-pr.md`

5. Treat tasks as executable units:
   - Tickets group related tasks.
   - Waves schedule tasks, not tickets.
   - Task files are the implementation briefs.
   - Task files move through `todo/`, `in-progress/`, `review/`, `done/`,
     `blocked/`, or `cancelled/`.

6. Activate waves safely:
   - A wave is activated as a batch of concurrently eligible tasks.
   - Dispatch every task in the active wave that has no unresolved dependency,
     no active conflict-domain blocker, no stale prerequisite, and no explicit
     blocked status.
   - Do not dispatch workers until the epic branch contains the current
     activation artifact state and each repository-writing task has a dedicated
     worktree checked out from that state on its task branch.
   - Track every active task independently in `orchestration.json` and
     `controller-state.md`.
   - Establish monitoring for active waves when available. Adaptive monitoring
     may use slower cadence during worker execution and faster cadence during
     review, fixes, or merge-ready gates while preserving scheduler
     verification.
   - Every monitoring tick must be bounded and idempotent: read current
     artifacts, poll worker and reviewer references, advance gates, update
     state, and stop when wave reconciliation is ready.

7. Preserve merge discipline:
   - The controller creates or verifies the epic branch from the target branch
     before worker dispatch.
   - Activation artifact changes are synced to the epic branch before task
     branches and worktrees are created.
   - Task branches branch from the epic branch.
   - Task branches are checked out in task-specific worktrees before workers
     start.
   - Task PRs target the epic branch.
   - No task work merges directly to the target branch.
   - Before merge, check branch freshness relative to the epic branch.
   - If stale, rebase or merge latest epic branch, rerun relevant verification,
     and rerun review when touched areas changed materially.
   - Final merge to the target branch happens through the final epic PR after
     epic validation and human review.

## Done When

- The current phase has updated concrete text artifacts.
- The next valid phase is named.
- Any blocker is recorded in the relevant artifact.
- No WDD phase required a CLI, script, package manager, generated validator, or
  local binary.
