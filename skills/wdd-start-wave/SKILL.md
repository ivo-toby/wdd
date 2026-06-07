---
name: wdd-start-wave
description: Start the next pending WDD wave by writing controller state and implementation briefs, then hand off to subagent orchestration while the controller remains non-coding.
---

# WDD Start Wave

Use this when the user asks to start, continue, or resume implementation for a WDD epic.

## User Input

If the user names an epic or wave, use it. Otherwise choose the first epic with `wave-plan.md` and a pending wave.

## Preconditions

- `wave-plan.md` exists.
- Validation passed for the tickets in the target wave.
- The controller must not implement code.
- A subagent orchestration mechanism must be available, or controller state must record that execution is blocked.

## Workflow

1. Load:
   - Constitution.
   - Epic.
   - Wave plan.
   - Tickets in the first pending wave.
   - Existing controller state if present.

2. Select wave:
   - Choose the first wave whose status is not `done`.
   - If a wave is `in_progress`, resume it.
   - Do not skip to later waves unless the user explicitly instructs and dependencies are satisfied.

3. Update artifacts:
   - Set selected wave status to `in_progress` in `wave-plan.md`.
   - Set selected ticket statuses to `in_progress`.
   - Create or update `controller-state.md`.

4. Write implementation briefs:
   - Path: `briefs/<ticket-id>-<slug>.md`
   - Include YAML frontmatter with `kind: implementation_brief`, epic, ticket, wave, branch, status.
   - Include deliverable, context, scope, out-of-scope, TDD requirement, verification commands, branch, PR title, and final response contract.

5. Controller state must track each ticket:
   - Ticket ID.
   - Brief path.
   - Branch.
   - Implementation thread ID if known.
   - Review thread ID if known.
   - PR URL if known.
   - Gate: `no_pr`, `needs_review`, `reviewing`, `needs_fixes`, `merge_ready`, `merged`, or `blocked`.
   - Open P1/P2 feedback.
   - Verification result.

6. Handoff:
   - Invoke `subagent-pr-orchestration`.
   - Dispatch one implementation agent per active-wave ticket.
   - Give each agent exactly one brief.

## Done When

- Active wave is marked `in_progress`.
- `controller-state.md` exists.
- Every active-wave ticket has an implementation brief.
- Subagent orchestration has been started or controller state records why it is blocked.

