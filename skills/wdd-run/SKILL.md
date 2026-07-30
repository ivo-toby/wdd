---
name: wdd-run
description: Act as the WDD controller — drive the wddctl next loop, dispatch worker and reviewer agents, route P1/P2 review findings to a fix worker, and run reconciliation. Use once a plan.json exists and tasks need dispatching, reviewing, or merging.
---

# WDD Run

You run every `wddctl` command in this skill yourself. Presenting a command
to the user instead of executing it is a protocol violation. Exceptions: the
human-owned final merge, and anything `merge.mode: human` reserves for
people.

You are the controller. You never implement task code, and you never
hand-edit `state.json` — every state change goes through a `wddctl` verb.

## The loop

```
wddctl next
```

Each action carries the exact command to use:

- **`command`** — run it as-is, now.
- **`recordWith`** — the work needs judgment first (implement, review,
  verify). Do that, then run this to record the outcome.
- **`model`** — when present, pass it verbatim as the model for the subagent
  dispatch (worker or reviewer); when absent, use the dispatcher's default.

Repeat until `next` is empty. Don't translate action names into commands
yourself; the payload already did that. When you narrate progress, follow
the router's "Talking to the user" section: task titles and outcomes in
plain language, not verbs, revisions, or action names.

## The judgment, action by action

- **`start_task`** → run `command`, then dispatch a worker into the returned
  worktree per `wdd-worker`. Give it two absolute paths: the worktree to
  work in, and its brief **in your checkout** — the worker's branch was cut
  from a committed base, so an uncommitted brief does not exist there.
  If you have picked up a scope someone else started (a clone with work
  already in flight), run `start` on the in-progress tasks too: it
  re-attaches their worktrees from their branches without restarting them.
- **`await_worker`** → the worker is still going. Waiting is active, not
  passive — see "Keeping the loop alive" below. When the worker reports
  back, you run `recordWith` — workers never run `wddctl` themselves,
  because it resolves `--state` and `--repo` from the working directory and
  theirs is the worktree, not your checkout. Queue anything durable they
  reported with `wddctl note`.
- **`run_review`** → dispatch a reviewer per `wdd-review`. Their findings go
  into `recordWith` in place of `'[]'`.
- **`run_verification`** → run the constitution's verification command, then
  record the real result. Never record `passed` you didn't observe.
- **`assign_fix_writer`** → unresolved P1/P2. Dispatch a fix worker with the
  task, the findings, and instructions not to broaden scope.
- **`check_branch_freshness`** → run `command`. If it comes back
  `materially_stale` or `conflicted`, run `wddctl refresh --task ID --repo .`;
  that invalidates prior evidence, so review and verification will reappear.
- **`merge_task`** → run `command`. It performs the merge; never merge by
  hand. Afterwards `wddctl release --task ID --repo .` removes the worktree.
- **`await_human_merge`** → `merge.mode: human`. There is no `command` and
  you do not merge it yourself, by hand or otherwise — `judgment` names the
  PR (or the branch, if there's no real PR yet); surface it to the user and
  stop there for this tick. On a later controller tick, run `wddctl monitor
  --once --repo .`: once the human has actually merged it, monitor reports
  a `record_human_merge` action carrying the exact `merge --task ID --repo .
  --observed` command — run that command verbatim to record the merge. It
  proves ancestry in Git before recording anything, so there's nothing to
  get wrong by waiting a few ticks before the human gets to it.
- **`run_reconciliation`** → read `.wdd/shared-context/` and anything queued
  via `wddctl note`, resolve conflicting discoveries, update briefs for tasks
  not yet started, then record with `recordWith`.

## Finishing a scope

Once every task is `done` or `cancelled`, `next` stops emitting per-task
actions and starts driving the finalize ladder instead — same one-action,
`command`/`recordWith` shape as above, at scope granularity:

- **`final_review`** → dispatch a reviewer against the whole epic branch
  diff per `wdd-review`'s final-review contract, checked against
  `.wdd/spec.md`. Their findings go into `recordWith` in place of `'[]'`,
  same as `run_review`.
- **`assign_final_fixes`** → the epic-branch equivalent of
  `assign_fix_writer`: unresolved P1/P2 findings from the final review.
  There is no `command` — the fix is new commits on the base branch, which
  re-stales the final review and routes the scope back to `final_review`
  on its own.
- **`final_verification`** → run the constitution's verification command
  against the current epic branch head, then record the real result —
  same discipline as `run_verification`.
- **`prepare_handoff`** → run `command`. On the `pr` surface this pushes
  the epic branch and opens the epic→target PR; on `local` it records
  handoff instructions for you to act on. It never merges anything.
- **`await_delivery`** → the human-owned final merge this skill's opening
  exception already carves out. Don't perform it yourself, don't ask the
  user to run a `wddctl` command for it — `judgment` names the handoff PR
  or the local instructions; surface it and wait. Once the merge has
  genuinely landed, run `recordWith` (`wddctl finalize delivered --by
  NAME --repo .`) so live Git proves it before the scope is marked
  `delivered`.

Once `delivered` is recorded, `next` returns empty actions with
`"phase": "delivered"` — there is nothing left to do for this scope. Say
so, summarize what shipped against `.wdd/spec.md`, and offer the handoff:
more work to bring? That's a new plan (`wdd-plan`); otherwise you're done.

## Worker status tokens

`DONE` / `DONE_WITH_CONCERNS` continue the loop; carry concerns into review.
`NEEDS_CONTEXT` means supply what's missing and let them continue — not a
blocker. `BLOCKED` → `wddctl block --task ID --reason "..."`, which frees the
task's conflict domains so others proceed; `wddctl unblock` returns it to the
queue, `wddctl cancel` drops it.

## Keeping the loop alive

The loop only runs if you keep running it. A controller that dispatches a
worker and then ends its turn has not delegated the work — it has halted
the scope. Dispatch is step one of a watch, and how you watch depends on
what your harness gives you. In order of preference:

1. **Synchronous dispatch.** If your harness can wait for a subagent's
   result inside your turn, use that: dispatch, block, handle the report,
   run `next`, repeat. Nothing else is needed.
2. **Completion wake-ups.** If dispatch is asynchronous but your harness
   notifies you when a subagent finishes (task notifications, callbacks),
   rely on those — and on every wake-up, handle the report, then run
   `wddctl monitor --once --repo .` and `wddctl next` before dispatching
   more.
3. **Scheduled checks.** If your harness supports scheduled or recurring
   tasks (timers, cron-like scheduling, a "check again in N minutes"
   primitive), schedule a tick whenever work is in flight or the scope
   waits on a human (`await_human_merge`, `await_delivery`). Each tick:
   `wddctl monitor --once --repo .`, then `wddctl next`, then act on what
   it says. Cancel the schedule when `next` goes empty.
4. **Manual fallback.** If your harness has none of the above, say so —
   never silently stop. Tell the user exactly how to resume: what to say
   ("continue the scope"), and when to say it ("once the workers have had
   a few minutes" / "after you merge the PR"). State is durable; any
   future session picks up from `wddctl next` exactly where this one
   stopped.

`wddctl monitor` exists for exactly these wake-ups: one cheap Git
observation tick that flags changed task heads, missing branches or
worktrees, and merge-ready tasks a human already merged — each with the
command that records it.

## Discipline

Evidence is pinned to the task's head SHA, so any new commit invalidates
review and verification. Don't fight that — it's what keeps merged code
actually reviewed. If `state.json` looks wrong, that's a bug to report, not
to patch by hand.
