---
name: subagent-pr-orchestration
description: Coordinate implementation subagents from WDD local ticket briefs or external issues, monitor PR/review gates, route P1/P2 feedback, merge only after verification, and persist compact controller state.
---

# Subagent PR Orchestration

Use this when a WDD wave or a single bounded ticket is ready for delegated implementation.

## Core Rule

The controller owns orchestration. The user must not relay messages between subagents, PRs, review comments, local ticket files, or external trackers.

When started from WDD, read `.wdd/epics/<epic>/controller-state.yaml` and the referenced `briefs/*.md`. Those local briefs are the source of truth. GitHub issues may exist, but they are adapter links.

## Start A Wave

1. Run or read the result of `wdd start-wave <epic> --json`.
2. For each returned ticket, read its implementation brief.
3. Dispatch only the tickets in the active wave.
4. Give each implementation subagent exactly one ticket.
5. Keep controller state compact and durable in `controller-state.yaml`; mirror to GitHub/Jira/PR comments only when adapters are in use.

## Implementation Subagent Prompt Must Include

- One ticket brief only.
- Exact branch from ticket frontmatter.
- Explicit deliverable and out-of-scope sections.
- RED/GREEN TDD requirement.
- Verification commands from frontmatter.
- Commit, push, and full PR requirement when Git is available.
- Instruction to monitor and address PR feedback.
- Required final status: `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`.

## Gate Loop

Track every active ticket with:

- ticket id and local path,
- implementation thread id,
- review thread id,
- branch and PR URL when available,
- latest commit,
- current gate: `no_pr`, `needs_review`, `reviewing`, `needs_fixes`, `merge_ready`, `merged`, or `blocked`,
- open P1/P2 feedback,
- verification result,
- cleanup status.

On each heartbeat:

- If no PR exists, inspect/nudge the implementation subagent.
- If a PR exists and no high-rigor review exists, spawn the strongest reviewer available.
- If review has P1/P2 feedback, route it directly to the implementation subagent.
- If fixes were pushed, re-check unresolved review threads and verification evidence.
- If no P1/P2 remain and verification/checks are acceptable, merge promptly.

## Review

Use a separate high-rigor reviewer for each implementation PR when available. The reviewer must check the ticket brief, spec compliance, correctness, tests, security, dependency boundaries, and maintainability. P1 and P2 findings block merge.

## Reconcile

After all tickets in a wave merge:

1. Run relevant verification locally or read trusted CI evidence.
2. Update `controller-state.yaml`.
3. Run `wdd reconcile <epic> --wave <n> --done`.
4. Update later tickets if merged architecture drifted from the plan.
5. Start the next wave only after reconciliation.

If direct subagent, heartbeat, GitHub, or review tooling is unavailable, preserve the same gates in local controller state and resume from there later.

