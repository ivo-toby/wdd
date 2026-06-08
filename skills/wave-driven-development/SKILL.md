---
name: wave-driven-development
description: Run the full text-only Wave-Driven Development workflow using local markdown/json artifacts, epic-ticket-task hierarchy, concurrent task waves, controller gates, review, validation, and final PR handoff.
---

# Wave-Driven Development

Use this as the overview skill for large features, spikes, migrations, refactors,
hardening work, bug clusters, or any multi-task implementation that benefits
from planned parallel agent execution.

## User Input

Treat the user's request as the workflow goal. If the request names a phase, use
the matching WDD phase skill. If the request is broad, start at the earliest
missing phase.

## Preconditions

- Work from the repository root.
- Read repo instructions such as `AGENTS.md`, `README.md`, and project-specific
  docs.
- Use `.wdd/` as the durable local source of truth.
- Do not rely on a runtime CLI, script, package manager, generated validator, or
  local binary.
- Keep GitHub, Jira, Linear, Postgram, and similar systems as adapters or
  mirrors, not required storage.

## Workflow

1. Determine current WDD state:
   - If `.wdd/constitution.md` is missing, use `wdd-init-project`, then
     `wdd-constitution`.
   - If the constitution has blocking setup gaps, use `wdd-constitution`.
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
   - Worker agents execute exactly one task file each.
   - Reviewer agents review one task PR or patch and classify P1/P2/P3
     findings.
   - The controller does not implement task code.
   - Workers do not merge their own PRs.

3. Maintain artifact locality:
   - Constitution: `.wdd/constitution.md`
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

4. Treat tasks as executable units:
   - Tickets group related tasks.
   - Waves schedule tasks, not tickets.
   - Task files are the implementation briefs.
   - Task files move through `todo/`, `in-progress/`, `review/`, `done/`,
     `blocked/`, or `cancelled/`.

5. Activate waves safely:
   - A wave is activated as a batch of concurrently eligible tasks.
   - Dispatch every task in the active wave that has no unresolved dependency,
     no active conflict-domain blocker, no stale prerequisite, and no explicit
     blocked status.
   - Track every active task independently in `orchestration.json` and
     `controller-state.md`.
   - Establish monitoring for active waves when available, preferring Codex
     thread heartbeat automation, then Claude Code `/loop`, then an external
     scheduler, then a manual fallback prompt recorded in artifacts.
   - Every monitoring tick must be bounded and idempotent: read current
     artifacts, poll worker and reviewer references, advance gates, update
     state, and stop when wave reconciliation is ready.

6. Preserve merge discipline:
   - Task branches branch from the epic branch.
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
