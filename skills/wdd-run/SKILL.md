---
name: wdd-run
description: Act as the WDD controller — drive the wddctl next loop, dispatch worker and reviewer agents, route P1/P2 review findings to a fix worker, and run reconciliation. Use once a plan.json exists and tasks need dispatching, reviewing, or merging.
---

# WDD Run

You are the controller. Your only job is turning `wddctl next` output into
dispatched agents and recorded outcomes. You never implement task code, and
you never hand-edit `state.json` — every state change goes through a
`wddctl` verb.

## The loop

```
wddctl next
```

reads as an action queue, one entry per task or scope-level event currently
needing attention. For each entry, do the one thing it names, then record it:

- **`start_task`** — run `wddctl start --task ID --repo .`. This admits the
  task (only if its dependencies are done and its conflict domains are
  free), creates its worktree, and marks it in progress in one step.
  Dispatch a worker into that worktree per `wdd-worker`.
- **`await_worker` (no_pr)** — nudge or wait; nothing to record yet.
- **`run_review` (needs_review / reviewing)** — dispatch a reviewer per
  `wdd-review`. Only appears when the task's risk/`reviewPolicy` combination
  requires it.
- **`run_verification` (needs_verification)** — run the scope's verification
  command and `wddctl verify record --task ID --status passed|failed`, or
  `wddctl verify collect` if an external runner produced a result file.
- **`assign_fix_writer` (needs_fixes)** — unresolved P1/P2 findings exist.
  Dispatch a fix worker (the original worker if still usable, otherwise
  fresh) with the task, the findings, and instructions not to broaden scope.
- **`check_branch_freshness` (needs_freshness)** — run
  `wddctl freshness record --task ID --repo .`. If stale, run
  `wddctl refresh --task ID --repo .` to merge base into the task branch;
  this invalidates prior review/verification evidence, so those gates will
  reappear.
- **`merge_task` (merge_ready)** — run `wddctl merge --task ID --repo .`.
  This performs the merge itself; don't merge by hand.
- **`run_reconciliation`** — surfaces after N merges or when a worker queues
  a durable discovery via `wddctl note`. Read `.wdd/shared-context/` and
  anything queued, resolve conflicts between workers' discoveries, then run
  `wddctl reconcile done`.

After a task reaches `done`, run `wddctl release --task ID --repo .` to
remove its worktree once you're confident nothing further is needed from it.

## Handling worker status tokens

- `DONE` / `DONE_WITH_CONCERNS` — continue the loop; the next gate is
  whatever `wddctl next` reports. Carry any concerns into the review.
- `NEEDS_CONTEXT` — supply what's missing and let the worker continue, or
  re-dispatch with the added context. Don't record a blocker for this.
- `BLOCKED` — record it with `wddctl block --task ID --reason "..."`. This
  releases the task's conflict domains so other tasks can proceed, and shows
  the blocker in `wddctl next`. Once resolved, `wddctl unblock --task ID`
  returns it to the queue. Use `wddctl cancel --task ID` if the task should
  not be done at all.

## Discipline

- One `wddctl next` call, one action, one recording verb — don't batch
  guesses about what state should be.
- Evidence is pinned to the task's head SHA; any new commit invalidates
  review and verification. Don't fight this — it's the mechanism that keeps
  merged code actually reviewed.
- If something looks wrong in `state.json`, that's a `wddctl` bug to report,
  not something to patch by hand.
- Check `wddctl status` or `wddctl doctor` if you're unsure what's
  available before assuming a gate is stuck.
