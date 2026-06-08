---
id: EPIC-example-feature-VALIDATION
kind: validation_checklist
epic: EPIC-example-feature
status: draft
created_at: YYYY-MM-DD
---

# Planning Validation Checklist: EPIC-example-feature

## Epic Readiness

- [ ] Epic has concrete deliverables.
- [ ] Definition of done is testable.
- [ ] Non-goals are explicit.
- [ ] Major risks and constraints are recorded.

## Ticket And Task Structure

- [ ] Each ticket folder has `ticket.md`.
- [ ] Each ticket contains kanban folders.
- [ ] Each task file has required frontmatter.
- [ ] Each task file has required body sections.
- [ ] Task files live in a status folder matching their status.

## Dependency And Conflict Soundness

- [ ] Every task dependency references an existing task.
- [ ] No dependency cycles exist.
- [ ] Conflict domains are explicit.
- [ ] Shared files, migrations, schemas, config, generated code, and shared
      tests are called out where relevant.

## Wave Readiness

- [ ] Waves schedule tasks, not tickets.
- [ ] Active-wave tasks can run concurrently only when dependencies,
      conflict-domain blockers, prerequisites, and blocked status allow it.
- [ ] Stop conditions require reconciliation before the next wave starts.

## Orchestration Readiness

- [ ] `orchestration.json` exists.
- [ ] `orchestration.json` includes `schemaVersion: 1`.
- [ ] Every planned task appears in orchestration state.
- [ ] Branch, PR or patch, gate, branch freshness, feedback, and verification
      fields are represented.
- [ ] Monitoring mode is recorded as `codex_thread_heartbeat`, `claude_loop`,
      `external_scheduler`, or `manual`.
- [ ] Monitoring fallback prompt is durable enough for a fresh controller to run
      the next heartbeat tick.
- [ ] Monitoring stop condition and next check are recorded.

## Shared Context

- [ ] `shared-context/index.md` exists.
- [ ] Shared-context resources are focused.
- [ ] Durable worker memory format is documented.
- [ ] Controller reconciliation rules are documented.

## Text-Only Portability

- [ ] Workflow does not require a CLI.
- [ ] Workflow does not require scripts.
- [ ] Workflow does not require Node.js or npm.
- [ ] Repo-native verification commands are optional and project-specific.

## Review And Merge Gates

- [ ] P1/P2 review policy is explicit.
- [ ] Workers do not merge their own PRs.
- [ ] Task PRs target the epic branch.
- [ ] Stale task branches are rebased or merged with the latest epic branch
      before merge.
- [ ] Relevant tests and review are rerun after material branch freshness
      updates.
