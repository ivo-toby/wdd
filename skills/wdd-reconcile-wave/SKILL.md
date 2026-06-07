---
name: wdd-reconcile-wave
description: Reconcile a completed WDD wave by inspecting merged work, verification, review findings, architecture drift, ticket status, and next-wave readiness before marking the wave done.
---

# WDD Reconcile Wave

Use this after all active-wave implementation PRs or patches have passed their merge gates.

## User Input

If the user names a wave, reconcile that wave. Otherwise reconcile the active wave from `controller-state.md`.

## Preconditions

- `controller-state.md` exists.
- Every ticket in the wave is merged or explicitly closed as not needed.
- Verification evidence exists for each ticket.
- Open P1/P2 feedback is resolved or explicitly accepted by the user.

## Workflow

1. Load:
   - Constitution.
   - Epic.
   - Design.
   - Wave plan.
   - Controller state.
   - Tickets in the wave.
   - PR links, commits, review notes, and verification evidence when available.

2. Verify merge gates:
   - Deliverable met.
   - Tests/checks passed or non-blocking failures documented.
   - High-rigor review completed when required.
   - No unresolved P1/P2 feedback.
   - Branch/PR cleanup status known.

3. Compare planned and actual work:
   - Files actually changed.
   - Architecture/design changes.
   - New dependencies.
   - New conflict domains.
   - Follow-up risks.

4. Update tickets:
   - Mark completed tickets `done`.
   - Add PR/adapter links if available.
   - Record verification evidence in the ticket body or controller state.

5. Update `wave-plan.md`:
   - Mark wave `done`.
   - Add completion date.
   - Add drift notes.
   - Update known conflict risks for future waves.

6. Update future tickets:
   - Adjust context, dependencies, conflict domains, verification, or scope based on actual merged architecture.
   - Do not start the next wave until these updates are made.

7. Update `controller-state.md`:
   - Mark all wave ticket gates `merged` or closed status.
   - Add cleanup result.
   - Add next-wave recommendation.

8. Report:
   - Wave status.
   - Tickets completed.
   - Verification evidence.
   - Drift found.
   - Future-ticket updates.
   - Whether the next wave is ready.

## Done When

- Wave is marked `done`.
- Completed tickets are marked `done`.
- Future tickets reflect drift and new conflict risks.
- The next phase is `wdd-start-wave` for the next pending wave or final epic closure.

