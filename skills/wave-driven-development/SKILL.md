---
name: wave-driven-development
description: Run the full Wave-Driven Development workflow as portable agent instructions using local markdown artifacts, YAML frontmatter, validation gates, dependency waves, controller state, and subagent handoffs.
---

# Wave-Driven Development

Use this as the overview skill for large features, spikes, migrations, refactors, or multi-ticket implementation work.

## User Input

Treat the user's request as the workflow goal. If the request names a phase, use the matching WDD phase skill. If the request is broad, start at the earliest missing phase.

## Preconditions

- Work from the repository root.
- Read repo instructions such as `AGENTS.md`, `README.md`, and project-specific docs.
- Use `.wdd/` as the durable local source of truth.
- Do not rely on a runtime project-management command. Create, read, and update markdown artifacts directly.
- Keep external systems such as GitHub, Jira, Linear, and Postgram as adapters or mirrors, not required storage.

## Workflow

1. Determine current WDD state:
   - If `.wdd/constitution.md` is missing, use `wdd-init-project` and `wdd-constitution`.
   - If no epic exists for the requested work, use `wdd-start-epic`.
   - If the epic has no pick-up-ready tickets, use `wdd-write-tickets`.
   - If tickets exist but validation is missing or stale, use `wdd-validate-tickets`.
   - If tickets validate but no wave plan exists, use `wdd-plan-waves`.
   - If a wave should start, use `wdd-start-wave`, then `subagent-pr-orchestration`.
   - If a wave completed, use `wdd-reconcile-wave`.
   - If the user asks where things stand, use `wdd-status`.

2. Enforce role separation:
   - The controller agent plans, validates, schedules, monitors, and reconciles.
   - Implementation agents code from one implementation brief each.
   - The controller does not implement wave tickets.

3. Maintain artifact locality:
   - Constitution: `.wdd/constitution.md`
   - Epic folder: `.wdd/epics/<epic-id>-<slug>/`
   - Epic: `epic.md`
   - Product/design detail: `prd.md`, `design.md`
   - Tickets: `tickets/<ticket-id>-<slug>.md`
   - Validation checklist: `validation-checklist.md`
   - Wave plan: `wave-plan.md`
   - Controller state: `controller-state.md`
   - Implementation briefs: `briefs/<ticket-id>-<slug>.md`

4. Preserve YAML frontmatter:
   - All epics and tickets must have YAML frontmatter.
   - Wave plans, controller state, implementation briefs, and checklists should also use YAML frontmatter.
   - Frontmatter is machine-readable state. Markdown bodies are human/agent-readable context.

5. Keep handoffs explicit:
   - Each phase reports artifacts created or changed.
   - Each phase names the next valid phase.
   - Each blocker is recorded in the relevant artifact.

## Artifact Contract

Required epic frontmatter:

```yaml
id: WDD-0001
kind: epic
type: feature
slug: example-feature
title: Example Feature
status: draft
created_at: YYYY-MM-DD
updated_at: YYYY-MM-DD
constitution_version: 1.0.0
ticket_count: 0
adapter_links:
  github_issue: null
  jira_epic: null
```

Required ticket frontmatter:

```yaml
id: WDD-0001-T001
kind: ticket
epic: WDD-0001
slug: example-ticket
title: Example Ticket
status: todo
wave: null
depends_on: []
conflict_domains:
  - path/or/domain/**
branch: codex/wdd-0001-t001-example-ticket
verification:
  - command proving the ticket works
adapter_links:
  github_issue: null
  pull_request: null
```

## Done When

- The current phase has a concrete artifact update.
- The next phase is named.
- Any missing context is recorded as a blocker in the relevant artifact.
- No implementation code is written by the controller phase.

