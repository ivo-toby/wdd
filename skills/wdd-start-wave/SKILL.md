---
name: wdd-start-wave
description: Start the next available WDD wave by generating controller state and implementation briefs, then hand off to subagent orchestration without coding in the controller thread.
---

# WDD Start Wave

Use this when the user asks to start or continue WDD implementation.

## Workflow

1. Run `wdd start-wave <epic> --json`.
2. Read the generated `controller-state.yaml`.
3. Read each generated implementation brief.
4. Invoke `subagent-pr-orchestration`.
5. Dispatch one implementation subagent per active-wave ticket.

## Controller Rule

The wave controller must not implement code. It manages ticket state, subagents, PRs, reviews, verification evidence, and reconciliation.

## Handoff Requirements

Each subagent prompt must include:

- one brief only,
- exact branch,
- deliverable,
- out-of-scope,
- RED/GREEN TDD requirement,
- verification commands,
- commit/push/PR requirement when Git is available,
- final status token: `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`.

