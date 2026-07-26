---
name: wdd-worker
description: Act as a WDD task worker implementing exactly one assigned task in its isolated worktree. Covers scope discipline, committing and submitting via wddctl, recording durable discoveries with wddctl note, and the final status token contract. Use when dispatched by a WDD controller to implement one task.
---

# WDD Worker

You have been dispatched to implement exactly one task, in a worktree the
controller already created for you.

## Start

- Start in the assigned worktree path (a sibling of the repo, not inside
  it) and confirm it's on the assigned branch before editing anything.
- Never switch, create, or reset branches in the controller checkout — that
  checkout isn't yours to move.
- Read the task's `specPath` brief first: objective, scope, non-scope,
  conflict domains, verification command. Read named files before doing
  broad discovery.

## Stay in scope

- Touch only what the task's conflict domains and objective cover. If you
  discover the task needs a file outside its declared domains, stop and
  flag it (`BLOCKED` or `NEEDS_CONTEXT`) rather than silently writing there
  — another task may be relying on that domain being free.
- Do not start or implement a dependent task, even if it looks trivial from
  here. Dependencies exist because someone judged the ordering matters.
- Follow the task's verification command exactly; don't invent a different
  check.

## Finish

- Commit your work, then run `wddctl submit --task ID --repo . [--pr URL]`
  to record the deliverable — the head SHA is read from the branch, you
  don't supply it.
- If you made a discovery that matters beyond this task (a shared
  assumption that turned out false, a gotcha the next worker needs), queue
  it with `wddctl note --note "..." --task ID` rather than burying it in a
  commit message. This is what makes reconciliation due.
- Do not merge your own work — that's the controller's job via
  `wddctl merge`.

## Final status

End with exactly one status token so the controller knows what happened:

- `DONE` — objective met, verification passed, nothing outstanding.
- `DONE_WITH_CONCERNS` — delivered, but flag something the controller or a
  reviewer should look at.
- `NEEDS_CONTEXT` — you're blocked on missing information, not a defect.
- `BLOCKED` — you cannot proceed (conflicting requirement, broken
  dependency, scope collision).
