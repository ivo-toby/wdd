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
yourself; the payload already did that.

## The judgment, action by action

- **`start_task`** → run `command`, then dispatch a worker into the returned
  worktree per `wdd-worker`. Give it two absolute paths: the worktree to
  work in, and its brief **in your checkout** — the worker's branch was cut
  from a committed base, so an uncommitted brief does not exist there.
  If you have picked up a scope someone else started (a clone with work
  already in flight), run `start` on the in-progress tasks too: it
  re-attaches their worktrees from their branches without restarting them.
- **`await_worker`** → the worker is still going. When it reports back, you
  run `recordWith` — workers never run `wddctl` themselves, because it
  resolves `--state` and `--repo` from the working directory and theirs is
  the worktree, not your checkout. Queue anything durable they reported with
  `wddctl note`.
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

## Worker status tokens

`DONE` / `DONE_WITH_CONCERNS` continue the loop; carry concerns into review.
`NEEDS_CONTEXT` means supply what's missing and let them continue — not a
blocker. `BLOCKED` → `wddctl block --task ID --reason "..."`, which frees the
task's conflict domains so others proceed; `wddctl unblock` returns it to the
queue, `wddctl cancel` drops it.

## Discipline

Evidence is pinned to the task's head SHA, so any new commit invalidates
review and verification. Don't fight that — it's what keeps merged code
actually reviewed. If `state.json` looks wrong, that's a bug to report, not
to patch by hand.
