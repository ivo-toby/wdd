# WDD Artifact Schema

WDD stores durable planning and controller state in local files. Markdown
artifacts use YAML frontmatter for metadata and Markdown bodies for human and
agent-readable instructions.

## Epic

Path:

```text
.wdd/epics/WDD-0001-auth-refresh/epic.md
```

Frontmatter:

```yaml
---
id: WDD-0001
kind: epic
type: feature
slug: auth-refresh
title: Auth Refresh
status: draft
created_at: "2026-06-07T18:00:00.000Z"
updated_at: "2026-06-07T18:00:00.000Z"
constitution_version: 1
adapter_links:
  github_issue: null
  github_project: null
---
```

Body sections should cover the product brief, design direction, ticket strategy,
and wave strategy. `prd.md` and `design.md` can hold longer supporting text.

## Ticket

Path:

```text
.wdd/epics/WDD-0001-auth-refresh/tickets/WDD-0001-T001-token-contract.md
```

Frontmatter:

```yaml
---
id: WDD-0001-T001
kind: ticket
epic: WDD-0001
slug: token-contract
title: Token Contract
status: todo
wave: null
depends_on: []
conflict_domains:
  - src/auth/**
branch: codex/wdd-0001-t001-token-contract
verification:
  - npm test -- auth
adapter_links:
  github_issue: null
  pull_request: null
---
```

Required body sections:

- `## Context`
- `## End Goal / Deliverable`
- `## Scope`
- `## RED/GREEN TDD`
- `## Acceptance Criteria`
- `## Verification`
- `## Review Handoff`
- `## Out of Scope`

## Wave Plan

Path:

```text
.wdd/epics/WDD-0001-auth-refresh/wave-plan.yaml
```

Shape:

```yaml
epic: WDD-0001
status: planned
generated_at: "2026-06-07T18:10:00.000Z"
waves:
  - wave: 1
    status: pending
    tickets:
      - WDD-0001-T001
    conflict_domains:
      - src/auth/**
    reason: Dependencies satisfied without overlapping conflict domains.
```

The planner groups dependency-ready tickets together when their
`conflict_domains` do not overlap. Tickets delayed for conflict avoidance are
pushed to later waves with a reason.

## Controller State

Path:

```text
.wdd/epics/WDD-0001-auth-refresh/controller-state.yaml
```

Shape:

```yaml
epic: WDD-0001
current_wave:
  wave: 1
  status: in_progress
controller_rule: The wave controller manages state and subagents; it does not implement code.
tickets:
  - id: WDD-0001-T001
    title: Token Contract
    branch: codex/wdd-0001-t001-token-contract
    brief_path: .wdd/epics/WDD-0001-auth-refresh/briefs/WDD-0001-T001-token-contract.md
    current_gate: no_pr
    verification:
      - npm test -- auth
```

Controllers update this file as subagents open PRs, reviews run, fixes land,
verification passes, and waves are reconciled.

