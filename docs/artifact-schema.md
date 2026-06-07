# WDD Artifact Schema

WDD artifacts are local markdown files. YAML frontmatter carries structured
state; markdown bodies carry context and instructions.

## Constitution

Path:

```text
.wdd/constitution.md
```

Required sections:

- Boundaries
- Prerequisites
- Agent Roles
- Ticket Rules
- Wave Rules
- Governance

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
created_at: "2026-06-07"
updated_at: "2026-06-07"
constitution_version: 1.0.0
ticket_count: 0
adapter_links:
  github_issue: null
  jira_epic: null
---
```

Required body sections:

- Product Brief / PRD
- Design Direction
- Acceptance Strategy
- Ticket Strategy
- Wave Strategy
- Open Questions

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

- Context
- End Goal / Deliverable
- Scope
- RED/GREEN TDD
- Acceptance Criteria
- Verification
- Review Handoff
- Out of Scope

## Wave Plan

Path:

```text
.wdd/epics/WDD-0001-auth-refresh/wave-plan.md
```

The wave plan is markdown with frontmatter. It must include:

- Ticket inventory.
- Dependency grid.
- Conflict grid or conflict notes.
- One section per wave.
- Grouping rationale for each wave.
- Stop condition after each wave.
- Known conflict risks.

## Controller State

Path:

```text
.wdd/epics/WDD-0001-auth-refresh/controller-state.md
```

The controller state is markdown with frontmatter. It must track:

- Active wave.
- Ticket gates.
- Brief paths.
- Branches.
- PR or patch references.
- Implementation and review thread IDs when available.
- Open P1/P2 feedback.
- Verification result.
- Cleanup state.

## Implementation Brief

Path:

```text
.wdd/epics/WDD-0001-auth-refresh/briefs/WDD-0001-T001-token-contract.md
```

The brief is the implementation subagent's source of truth. It must include:

- Deliverable.
- Required context.
- Scope.
- Out of scope.
- RED/GREEN requirement.
- Verification commands.
- PR or patch requirements.
- Required final status token.

