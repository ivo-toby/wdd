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
- **`model`** — when present, this field is **binding**, not metadata: set
  it explicitly as the subagent's model on the spawn call (worker or
  reviewer). Spawning on your harness's default when the payload names a
  model is a routing violation — the user configured that routing at setup.
  If your harness cannot set a per-subagent model, stop and tell the user;
  the remedy is registering the model as a runner (`wdd-runners`), not
  dispatching on the wrong model. When the field is absent, the
  dispatcher's default is correct.

Repeat until `next` is empty. Don't translate action names into commands
yourself; the payload already did that. When you narrate progress, follow
the router's "Talking to the user" section: task titles and outcomes in
plain language, not verbs, revisions, or action names.

## Dispatch packet

How you assemble a worker's or reviewer's packet depends on whether the
task's routed model is harness-native or a registered runner.

**Harness-native (subagent) dispatch** — the common case. Hand the subagent
absolute paths into the task's **snapshot dir**, not your live checkout —
the snapshot is what the approved-bytes doctrine actually guarantees, and a
file edited (or edited-and-restored) after dispatch cannot reach the
subagent either way. `start`'s output gives you the snapshot dir as a
`.wdd`-relative path (e.g. `dispatch/TASK-001-1`); join it to `.wdd/`
yourself to get the absolute path before handing it to the subagent.

- **Worker packet**: the snapshot's brief and context-ref paths, the
  worktree to work in, its branch, the task's Deliverable (read from the
  brief — what the diff must produce), and the status-token contract
  (`wdd-worker`'s final line: `DONE` / `DONE_WITH_CONCERNS` /
  `NEEDS_CONTEXT` / `BLOCKED`).
- **Reviewer packet**: the snapshot's brief and context-ref paths, the diff
  (base SHA to head SHA), and the numbered acceptance criteria this task
  discharges — its `context` refs shaped `spec.md#AC-N` — as the "criteria
  it discharges" `wdd-review` reviews against.

**Runner-routed dispatch** — when the task's routed model (`model`/
`reviewModel` override, or tier config) names a configured runner, don't
hand-compose a subagent prompt at all:

```sh
wddctl dispatch --task ID --role worker|reviewer
```

One command assembles the same packet from the task's snapshot, execs the
runner, and reports the worker's status token or validates the reviewer's
result JSON for you to record with `wddctl review collect`. Registering and
probing a runner is `wdd-runners`' job, not this skill's — by the time a
task's model names one, it's already registered and probed.

## The judgment, action by action

- **`start_task`** → run `command`. Its output now carries the task's
  immutable **snapshot dir** alongside the worktree and branch — read-only
  copies of the brief and every `context` ref, materialized at start.
  Dispatch a worker per the Dispatch packet section below and `wdd-worker`.
  If you have picked up a scope someone else started (a clone with work
  already in flight), run `start` on the in-progress tasks too: it
  re-attaches their worktrees from their branches without restarting them
  (and without touching an already-recorded snapshot).
- **`await_worker`** → the worker is still going. Waiting is active, not
  passive — see "Keeping the loop alive" below. When the worker reports
  back, you run `recordWith` — workers never run `wddctl` themselves,
  because it resolves `--state` and `--repo` from the working directory and
  theirs is the worktree, not your checkout. Queue anything durable they
  reported with `wddctl note`.
- **`run_review`** → dispatch a reviewer per the Dispatch packet section
  below and `wdd-review`. Their findings go into `recordWith` in place of
  `'[]'`.
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
- **`final_verification`** → for a v5 (non-legacy) scope, evidence is
  `--results`: one ordered JSON array covering exactly the ratified global
  `verification.commands` then the scope's deliverable command (recorded
  at design approval), each entry `{"command": ..., "status": ...}` in that
  order. Overall passes only when every entry's status is `passed`. Run
  EVERY listed command yourself, against the current epic branch head, and
  record only the statuses you actually observed — the pre-filled array in
  `recordWith` is a template of what must be run, not a license to stamp
  `passed` on commands you didn't execute. Legacy scopes keep the old
  single-command `--status`/`--command` shape — same discipline as
  `run_verification`.
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
more work to bring? Archive the delivered scope first — `wddctl scope
archive --repo .` (governed; you run it) moves its records to
`.wdd/archive/` and restarts the intake ladder — then hand off to
`wdd-intake` for the next scope: "Scope's archived. Should I start the
ladder for the next one?" Nothing to bring? You're done.

## Worker status tokens

`DONE` / `DONE_WITH_CONCERNS` continue the loop; carry concerns into review.
`NEEDS_CONTEXT` means supply what's missing and let them continue — not a
blocker. `BLOCKED` → `wddctl block --task ID --reason "..."`, which frees the
task's conflict domains so others proceed; `wddctl unblock` returns it to the
queue, `wddctl cancel` drops it.

## When `next` blocks or asks for a human decision

Three conditions empty out `next`'s actions and surface a `blockers` entry
naming the exact remedy — treat these as the priority, not something to
route around:

- **`governance_drift`** — config or the constitution changed since
  ratification. Amend before executing anything else; that's `wdd-setup`.
- **`intake_drift`** — an approved intake rung's artifact bytes changed
  since approval. The blocker names the stale rung. Remedy: re-run
  `wddctl intake <rung> ...` to re-approve it (re-approving cascades —
  walk every rung it clears, in order, per `wdd-intake`), then
  `wddctl plan apply --plan plan.json --repo . --approved-by NAME` to
  re-stamp the plan.
- **`plan_drift`** — the applied plan's composite approval no longer
  matches its current bytes (a brief or a `context` file changed since
  `plan apply --approved-by`). Remedy: `wddctl plan apply --plan plan.json
  --repo . --approved-by NAME` — with the plan file itself unchanged,
  that's a pure re-stamp.

Relay the blocker to the user in plain language ("a task's brief changed
after it was approved; I need you to re-approve it before I can keep
going"), get their sign-off, run the remedy yourself, then call `next`
again.

Separately, `next` can surface an **`inputs_changed`** action on an
individual active task: its brief or a context file changed after the
task's attempt was dispatched, so that task's unmerged review or
verification evidence is no longer trustworthy. This is a **human
decision, never the controller's own** — relay both options, don't pick
one silently:

- **Rebind** — the human decides the existing work still stands despite
  the input change: `wddctl rebind --task ID --by NAME --repo .`.
- **Re-dispatch fresh** — discard the in-flight attempt and start over
  against current inputs: `wddctl block --task ID --reason "..."`, then
  `wddctl unblock --task ID`, then `wddctl start` again. This only
  genuinely re-materializes a fresh snapshot for a task that's `in_progress`
  and never submitted (no PR yet) — `unblock` returns it to `todo`, so
  `start` runs a real new dispatch. A task that already submitted (`review`
  or `merge_ready`) keeps its PR through block/unblock, so `unblock`
  returns it to `in_progress` instead, and `start` on an active task is a
  reattach that PRESERVES the existing snapshot — it does not discard the
  mismatched attempt. For an already-submitted task, the mismatch stays a
  human decision: rebind, or cancel the task and replan it fresh.

If a `plan_drift` blocker is present at the same time, re-stamp the plan
first — a started task's own context edit fires both together, and the
composite re-stamp is the same underlying fix.

## Where each role runs

You — the session reading this — are the controller. Run the loop in your
own context; never dispatch "act as the controller" into a subagent.
Dispatch depth is usually one level, so a controller inside a subagent has
no way to spawn workers and ends up implementing tasks itself — the one
thing a controller must never do. Spend subagent dispatch on workers and
reviewers, nothing else.

If your harness offers no subagent dispatch at all, the roles collapse
into you: implement the task yourself in its worktree following
`wdd-worker`, then switch hats and review the diff per `wdd-review` before
recording. Tell the user this is the degraded single-session mode —
self-review is weaker than an independent reviewer, and the review record
should name you honestly rather than a phantom reviewer.

A review record is evidence, and evidence you invent is fabrication: never
record a review that did not happen, and never put a human's name in
`--reviewer` unless that human actually reviewed the diff. An empty
findings list from a review nobody ran is worse than no record — it
converts "unreviewed" into "reviewed and clean", which is a lie the merge
gate then trusts. If you cannot dispatch the configured reviewer, say so
and stop; recording a pass is not a fallback.

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
