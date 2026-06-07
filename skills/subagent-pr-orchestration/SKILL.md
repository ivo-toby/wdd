---
name: subagent-pr-orchestration
description: Coordinate WDD implementation subagents from local implementation briefs, monitor PR or patch gates, route review feedback, update controller state, and merge only after verification and review pass.
---

# Subagent PR Orchestration

Use this when `wdd-start-wave` has created `controller-state.md` and implementation briefs, or when a single local ticket brief is ready for delegated implementation.

## User Input

Use any named epic, wave, ticket, branch, PR URL, or subagent thread ID. If none is provided, read the active controller state.

## Preconditions

- Controller state exists or a single implementation brief is provided.
- Each implementation agent receives exactly one brief.
- The controller does not implement ticket code.
- GitHub is optional. If no PR system exists, use branches, patches, or local status notes and preserve the same gates.

## Workflow

1. Load controller context:
   - `.wdd/constitution.md`
   - active epic `epic.md`
   - active `wave-plan.md`
   - `controller-state.md`
   - active implementation briefs

2. For each active ticket, ensure controller state tracks:
   - ticket ID and brief path,
   - implementation thread ID,
   - review thread ID,
   - branch,
   - PR or patch reference,
   - latest commit,
   - current gate,
   - open P1/P2 feedback,
   - verification result,
   - cleanup state.

3. Dispatch implementation subagents:
   - One ticket per subagent.
   - Include only the implementation brief plus required repo instructions.
   - Require RED/GREEN TDD.
   - Require branch, commit, verification, and PR or patch output.
   - Require final status token: `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`.

4. Implementation prompt contract:
   - Read the assigned brief first.
   - Inspect named files/domains before broad discovery.
   - Do not work outside ticket scope.
   - Do not start dependent tickets.
   - Produce verification evidence.
   - Return PR URL or patch reference.
   - Monitor and address routed P1/P2 feedback.

5. Review gate:
   - When an implementation PR or patch exists, start a separate high-rigor reviewer when available.
   - Reviewer checks brief compliance, correctness, tests, security, dependency boundaries, maintainability, and conflict domains.
   - Reviewer labels findings P1/P2/P3.
   - P1 and P2 block merge.

6. Heartbeat loop:
   - no_pr: inspect implementation state and nudge with the exact missing deliverable.
   - needs_review: start or request high-rigor review.
   - reviewing: poll review state and comments.
   - needs_fixes: route all current P1/P2 feedback directly to the owning implementation subagent.
   - merge_ready: verify evidence, then merge or mark ready according to repo policy.
   - merged: update local ticket and controller state.
   - blocked: record blocker, owner, and next required input.

7. Update `controller-state.md` after every meaningful event:
   - subagent started,
   - PR or patch created,
   - review started,
   - P1/P2 found,
   - fix pushed,
   - verification passed or failed,
   - merge completed,
   - cleanup completed,
   - blocker encountered.

8. Merge rules:
   - Deliverable is met.
   - Verification passes or non-blocking failures are explicitly documented.
   - High-rigor review has no unresolved P1/P2.
   - CI/checks are green or explicitly non-blocking.
   - Linked local ticket is updated.

9. Completion handoff:
   - When all active-wave tickets are merged or closed, invoke `wdd-reconcile-wave`.
   - Do not start the next wave before reconciliation.

## Done When

- Every active ticket has a current gate in `controller-state.md`.
- P1/P2 feedback is routed to the owning implementation subagent.
- Merge happens only after verification and review gates pass.
- Completed wave is ready for `wdd-reconcile-wave`.

