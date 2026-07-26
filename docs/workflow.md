# The developer workflow

`docs/wddctl.md` documents commands. `docs/artifact-schema.md` documents file
formats. Neither says how a developer actually spends a day working this
way. This document does. Every command block below was run against a real
scratch repository on this branch; nothing here is paraphrased output.

## Two audiences

WDD has two entry points, and it matters which one you're using, because
they imply different things about who types what.

**A developer talking to a coding agent.** This is the common case. You
open a session with Claude Code (or Codex, or another agent with the skills
installed) and say something like "let's add refresh tokens" or "what's the
status of the auth-refresh scope?" You never type `wddctl` yourself. The
agent reads a skill — `wave-driven-development`, `wdd-plan`, `wdd-run`,
`wdd-worker`, `wdd-review`, `wdd-status`, or `wdd-constitution` — decides
it applies, and runs `wddctl` commands on your behalf, showing you the
output or summarizing it. Your intervention, when you have one, is prose:
"actually split TASK-002 differently" or "block TASK-004, we're rethinking
the UI." The agent translates that into `wddctl block` or a `plan.json`
edit.

**Someone driving `wddctl` directly.** This is you, or an agent operating
without a human in the loop turn by turn, running the commands yourself:
scripting a CI step that checks `wddctl status --json`, debugging why a
task won't admit, or just preferring the terminal over a chat transcript.
Everything in `docs/wddctl.md` is written for this audience. This document
is written for both, but the transcripts below show the literal commands
so either audience can follow along.

**The important part: skills do not call `wddctl`.** A skill is a Markdown
file loaded into an agent's context window. It has no execution privileges
of its own — it cannot run a shell command any more than this paragraph
can. What a skill does is put text like this in front of the agent:

> `run_review` → dispatch a reviewer per `wdd-review`. Their findings go
> into `recordWith` in place of `'[]'`.

The agent reads that instruction and — because it has shell access as part
of its own tool set, not because the skill granted it one — decides to run
`wddctl review record --task ... --findings '[...]'`. If the agent ignores
the skill, nothing enforces the instruction; skill text is advisory, the
way a code review comment is advisory. Contrast this with the state
machine in `wave_delivery/engine.py`: if two tasks share a conflict domain,
the `task.started` transition raises `IllegalTransition` regardless of
what any skill says. That distinction — prose that an agent might ignore,
versus a transition function that cannot be argued with — is the whole
design of WDD, and it recurs throughout this document as "enforced vs.
convention."

## The three roles

WDD names three roles: controller, worker, reviewer. They are roles, not
processes — how they map onto actual agent sessions depends on your setup.

**Controller.** Runs the `wddctl next` loop, dispatches workers and
reviewers, routes P1/P2 findings back to a fix worker, runs reconciliation,
merges. The controller never writes task code and never hand-edits
`state.json`. In practice this is usually one long-lived agent session (or
a human with a terminal) that stays alive for the whole scope, because it's
the thing holding the "what's next" thread. Its skill is `wdd-run`.

**Worker.** Implements exactly one task, in the worktree the controller's
`wddctl start` created for it. A worker session is short-lived — it exists
for one task's lifetime and then is done. In an agent-driven setup, the
controller typically dispatches a worker as a subagent (Claude Code's Task
tool, or an equivalent) with the task ID and worktree path, and gets back a
status token when it finishes. Its skill is `wdd-worker`.

**Reviewer.** Reviews one task's diff against its brief and classifies
findings as P1/P2/P3. A reviewer session is also short-lived, dispatched
per review, and — this matters — should not be the same context that wrote
the diff, for the same reason a human wouldn't approve their own PR. Its
skill is `wdd-review`.

In a small scope worked by one person and one agent, all three roles can be
the same agent session switching hats: it reads `wdd-run`'s instructions to
decide what's next, then reads `wdd-worker`'s instructions while it
implements a task, then reads `wdd-review`'s instructions to review someone
else's task. Nothing in `wddctl` requires three separate processes — the
state machine doesn't know or care who ran a command, only that the
transition is legal. What it does care about is evidence: a review's
findings are pinned to a head SHA regardless of who produced them, so even
a self-review that's honest still leaves an auditable trail.

## The lifecycle of one task, gate by gate

This is a real run. The scope is `SCOPE-auth-refresh`, four tasks (the full
plan is in `docs/example-auth-refresh.md`); here we follow
`TASK-001-token-types` from nothing to merged. `next` output is trimmed to
the fields that matter for this task.

**Not started.** Before anything happens, `wddctl next` names the action
and hands you the literal command:

```
$ wddctl next
{
  "actions": [
    {"task": "TASK-001-token-types", "action": "start_task",
     "command": "wddctl start --task TASK-001-token-types --repo ."}
  ],
  ...
}
```

Note the `command` field. This is a change from earlier documentation of
this tool: `next` used to name an action and leave translating it into a
command as an exercise for the caller. It no longer does. For
`start_task`, `check_branch_freshness`, and `merge_task`, `command` is a
verb to run as-is, no judgment required. For actions that need judgment —
`await_worker`, `run_review`, `run_verification`, `assign_fix_writer`,
`run_reconciliation` — the payload instead carries `recordWith`: do the
work, then run the given command to record the outcome.

The controller runs the command:

```
$ wddctl start --task TASK-001-token-types --repo .
{
  "task": "TASK-001-token-types",
  "action": "create_branch_and_worktree",
  "branch": "task/TASK-001-token-types",
  "worktree": "/path/to/repo.wdd/worktrees/SCOPE-auth-refresh/TASK-001-token-types",
  "baseRef": "wdd/auth-refresh",
  "headSha": "34a4211...",
  "specPath": "tasks/TASK-001-token-types.md",
  "revision": 2
}
```

This did three things in one call: verified the task is admissible
(dependencies done, conflict domains free), created the branch and an
isolated worktree, and flipped the task to `in_progress`. The controller
now dispatches a worker into that worktree with the `wdd-worker` skill.

**Worker implements, commits, submits (`no_pr`).** The worker reads its
brief at `specPath`, writes code, commits it in the worktree — never in the
controller's own checkout — and calls `submit`:

```
$ wddctl submit --task TASK-001-token-types --repo .
{
  "task": "TASK-001-token-types",
  "event": "task.pr_recorded",
  "branch": "task/TASK-001-token-types",
  "headSha": "f9cdf2b...",
  "pr": "branch:task/TASK-001-token-types@f9cdf2b86ab5",
  "status": "review",
  "revision": 4
}
```

`submit` reads the head SHA from the branch itself — the worker never types
a SHA. Status went straight to `review` because this task is `"risk":
"high"` and the scope's `reviewPolicy` is `risk_based`. A normal-risk task
under the same policy would go to `in_progress` and skip straight to
verification; see `docs/example-auth-refresh.md` for that path (TASK-003
and TASK-004 both take it).

**Review finds a P1 (`needs_review` → `reviewing`).**

```
$ wddctl next
{"actions": [{"task": "TASK-001-token-types", "action": "run_review",
  "recordWith": "wddctl review record --task TASK-001-token-types --reviewer NAME --findings '[]'"}]}
```

The controller dispatches a reviewer per `wdd-review`. The reviewer reads
the diff, the brief, and finds something real:

```
$ wddctl review record --task TASK-001-token-types --reviewer "codex-review" \
  --findings '[{"severity":"P1","summary":"issueRefreshToken uses Math.random() for the token value; Math.random() is not cryptographically secure and refresh tokens must be unguessable","file":"src/auth/tokens.ts","line":9}]'
{"duplicate": false, "outcome": "blocking", "revision": 5, "status": "in_progress"}
```

A P1 or P2 finding sets `outcome: blocking` and drops the task's gate to
`needs_fixes`, regardless of how far along verification or freshness were.

**Fix cycle (`needs_fixes`).**

```
$ wddctl next
{"actions": [{"task": "TASK-001-token-types", "action": "assign_fix_writer",
  "recordWith": "wddctl submit --task TASK-001-token-types --repo ."}]}
```

Note `recordWith` here is `submit`, not a special "fix" verb — a fix is
just more commits on the same branch. The controller dispatches a fix
worker with the task and the findings, explicitly told not to broaden
scope beyond what the P1 requires. The fix worker replaces `Math.random()`
with `crypto.randomBytes`, commits, and resubmits:

```
$ wddctl submit --task TASK-001-token-types --repo .
{"event": "task.head_updated", "headSha": "e6befd7...", "status": "review", "revision": 6}
```

The event is `task.head_updated`, not `task.pr_recorded` — same PR
reference, new head. This is the evidence-invalidation guarantee showing
up directly: a new commit clears the task's `review`, `verification`, and
`freshness` fields and sends it back to `review` because this task
requires review. `wddctl next` immediately shows `run_review` again, with
no memory that this exact diff already got flagged once — the reviewer
re-reviews the new head from scratch.

**Clean review, verification, freshness, merge.**

```
$ wddctl review record --task TASK-001-token-types --reviewer "codex-review" --findings '[]'
{"outcome": "passed", "status": "in_progress", "revision": 6}

$ wddctl verify record --task TASK-001-token-types --status passed --command "tsc --noEmit && vitest run tokens"
{"status": "merge_ready", "revision": 9}
```

Watch the status field: it jumped straight from `in_progress` to
`merge_ready` inside the `verify record` call itself, because passing
verification was the last thing this task needed. There is a documented
gate called `ready_to_merge` between "verification passed" and
`merge_ready` — in practice you will never see it in `next`'s output. The
transition that records verification checks internally whether the task
has reached `ready_to_merge` and, if so, immediately advances it to
`merge_ready` in the same call. By the time any external command reads the
state back, the task is already past that gate. Don't design tooling
around ever observing `ready_to_merge`.

```
$ wddctl freshness record --task TASK-001-token-types --repo .
{"classification": "current", "revision": 11}

$ wddctl merge --task TASK-001-token-types --repo .
{"action": "merged", "baseRef": "wdd/auth-refresh", "baseSha": "028b572...",
 "headSha": "e6befd7...", "revision": 13}

$ wddctl release --task TASK-001-token-types --repo .
{"cleanup": "cleaned_up", "revision": 15}
```

`merge` performed the actual `git merge` inside an integration worktree,
then called `git merge-base --is-ancestor` to prove the task's head really
landed in the base before it would record `task.merged` — there is no code
path that records a merge without that live check passing. `release`
removes the now-finished worktree. The task is `done`.

## Where a developer intervenes

The loop runs itself; a developer's job is to notice when it shouldn't.

**Reading state by hand.** `wddctl status` gives a one-line-per-concern
brief; `wddctl status --json` gives the full summary that `next` and
`render` are built from — useful for a script or for pasting into a
question to an agent. `wddctl render --output .wdd/state.md` writes a
generated Markdown snapshot; treat it exactly like `state.json` — read it,
never edit it, regenerate it after anything changes.

**Editing `plan.json` mid-flight.** `plan apply` is re-runnable and diffs
the new plan against current state. This came up for real in building the
example in this repo: two independent tasks (client-side retry and a
session-expiry banner) turned out to need a shared event-bus module
neither task's first-draft `conflictDomains` listed. Fixing it looked like
this:

```
$ wddctl plan apply --plan plan.json --repo .
{
  "created": false,
  "diff": {
    "added": [], "removed": [],
    "updated": [
      {"task": "TASK-003-client-retry", "changes": ["conflictDomains"]},
      {"task": "TASK-004-session-ui", "changes": ["conflictDomains"]}
    ]
  },
  "revision": 11
}
```

This is safe to do at any point *before* a task starts. `plan apply`
refuses outright to edit or remove a task that has already left `todo` —
you cannot retroactively redraw the domains of a task mid-flight; you can
only fix the domains of tasks that haven't started yet, or add new tasks
for work you missed.

**Blocking, unblocking, cancelling.**

```
$ wddctl block --task TASK-B --reason "waiting on design sign-off"
$ wddctl next   # TASK-B drops out of blockers-that-count-as-active;
                # its conflict domains free up for other tasks
$ wddctl unblock --task TASK-B
$ wddctl cancel --task TASK-A   # terminal, no way back
```

`unblock` returns a task to `todo` if it never got a PR, or `in_progress`
if it did. See "Failure modes" below for a sharp edge in the `todo` case.

**Forcing a refresh.** When `next` reports `check_branch_freshness` and the
classification comes back `materially_stale` or `conflicted`, don't wait
for it to resolve itself:

```
$ wddctl refresh --task TASK-B --repo .
```

This merges the scope's base into the task branch and re-records the head
— which, deliberately, invalidates any review or verification evidence
that was pinned to the old head. `next` will show `run_review` /
`run_verification` again even though nothing about the task's own logic
changed. That's not wasted work: the diff against the base is genuinely
different now, and the old approval was of a different diff.

## Failure modes and what to do

**A task's branch goes stale.** `next` shows `check_branch_freshness` with
a `materially_stale` classification (files the task touches, or that
overlap its conflict domains, moved on the base since the task started).
Run `wddctl refresh --task ID --repo .` — see above.

**A refresh conflicts.**

```
$ wddctl refresh --task TASK-B --repo .
wddctl: refreshing TASK-B from wdd/demo2 conflicts in: src/b.ts; resolve it in the task worktree, then re-run
```

Read this literally: `wddctl` already ran `git merge --abort` before
printing that message. There are no conflict markers waiting for you in
the worktree — `git status` there comes back clean. What you actually do
is redo the merge yourself, by hand, in the task worktree:

```
$ cd <task worktree>
$ git merge wdd/auth-refresh --no-edit
Auto-merging src/b.ts
CONFLICT (add/add): Merge conflict in src/b.ts
$ # edit src/b.ts to resolve the conflict
$ git add src/b.ts && git commit
```

After resolving by hand, run `wddctl refresh` again. It notices the branch
already contains the base and adopts the head you produced:

```
$ wddctl refresh --task TASK-B --repo .
{
  "action": "adopted_external_merge",
  "previousHeadSha": "e7aa296...",
  "headSha": "026ccd8...",
  "note": "branch was already current; recorded its head and invalidated evidence"
}
```

Evidence is deliberately invalidated, because the commit that was reviewed
and verified is no longer the tip. Review and verification reappear in
`next`. `wddctl submit` does the same thing and is equally valid here —
as far as `wddctl` is concerned, "resolved a conflict" and "have new work"
are the same event.

**A worker returns `BLOCKED`.** Run `wddctl block --task ID --reason
"..."`. This frees the task's conflict domains immediately so other tasks
can proceed instead of waiting on a task that isn't moving.
`wddctl unblock` returns it to the queue once the blocker is resolved
(to `todo` if it never submitted, otherwise to `in_progress`);
`wddctl cancel` if it's not coming back. Running `start` again after an
unblock picks the task back up — re-attaching its worktree if one already
exists rather than restarting the work.

**A P1 keeps reappearing.** If the same finding shows up on consecutive
reviews after a claimed fix, the fix worker likely patched the symptom in
a way the next commit's diff doesn't actually address the reviewer's
concern, or the reviewer is being handed stale context. Read the actual
diff between the two head SHAs yourself before dispatching a third fix
attempt — don't assume the loop will converge by repetition alone.

**Evidence invalidated by a new commit, and you didn't expect it.** This is
almost always working as intended (see the fix-cycle transcript above) —
but if a *verification* re-runs and now fails where it previously passed,
that's a real regression the new commit introduced, not a bookkeeping
artifact. Investigate the diff, don't just re-run verification hoping for
a different answer.

**`RevisionConflict` when two controllers share a scope.**

```
$ wddctl note --note "..." --expected-revision 5
wddctl: expected revision 5, found 14
```

This only happens when you explicitly pass `--expected-revision` — it's
opt-in optimistic concurrency for the case where two controller processes
(two agent sessions, a human and an agent, a script and a person) are
racing on the same `state.json`. Re-read the current state
(`wddctl status --json` or the error's own "found" value) and retry your
call with the current revision. If you never pass `--expected-revision`,
you cannot hit this — `wddctl` reads the live revision under the same lock
that guards the write, so a lone controller never needs to think about it.

**Constitution unratified.** Every mutating command except constitution
commands refuses outright until `.wdd/constitution.md` has been ratified.
`wddctl next` reports this as a blocker with no actions at all:

```
$ wddctl next
{"actions": [], "blockers": [{"code": "constitution_unratified",
  "message": "Run wddctl constitution ratify before execution."}]}
```

Run `wddctl constitution probe`, review what it inferred, and ratify — see
`wdd-constitution`.

## What is enforced vs. what is convention

Get this distinction right, because it determines how much you have to
trust the agent doing the work.

**Enforced by the state machine — cannot be talked around:**

- **Conflict-domain exclusion.** Checked inside `task.started` itself
  (`admission_blocker`), not just advertised by `next`. Proven directly:
  starting a task whose domain overlaps an active task fails even when you
  bypass `next` and call `start` yourself —
  `wddctl: task TASK-004-session-ui is not admissible yet: conflict_domains (src/client/session-events.ts)`.
- **Dependency ordering.** A task with an unmet `dependsOn` cannot start;
  `admission_blocker` returns a `dependencies` code, same enforcement path
  as conflict domains.
- **`scope.maxConcurrent`.** Once that many tasks are active, no further
  task admits regardless of how free its domains are.
- **Evidence pinned to head SHA.** `review.recorded` and
  `verification.recorded` both reject data whose `headSha` doesn't match
  the task's current head. A new commit — from a fix, or a `refresh` —
  clears both fields outright; there is no code path to carry old evidence
  forward onto a new commit.
- **Git-verified merge.** `task.merged` requires `data.mergeVerified is
  True`, which `wddctl merge` only sets after calling
  `git merge-base --is-ancestor` for real. You cannot construct a call to
  `event apply` that fakes this without actually performing a real,
  verifiable merge first.

**Convention — depends on the agent actually reading and following the
skill:**

- **Conflict-domain accuracy.** The state machine enforces that two tasks
  sharing a listed domain don't run concurrently. It has no way to know if
  a task's `conflictDomains` list is *missing* a file it actually writes.
  If a worker strays outside its declared domains, nothing stops it —
  that's the `wdd-worker` skill's "stay in scope" instruction doing
  unenforced work, and the `wdd-review` skill's "diff stays inside declared
  domains" check is the backstop, also unenforced beyond a reviewer
  actually looking.
- **Review quality.** `review record` will happily accept `--findings '[]'`
  from a reviewer that didn't actually read the diff. The severity
  classification, the decision to flag something as P1 versus P3 — all of
  it is judgment the schema validates the *shape* of, not the *substance*
  of.
- **Task decomposition and sizing.** Nothing checks that a task is "one
  independently executable unit" as `wdd-plan` recommends. You can write a
  `plan.json` with one task that touches the entire repository, and
  `plan apply` will accept it.
- **`.wdd/shared-context/`.** `wddctl` never writes to this directory —
  it's a plain folder of Markdown files that only exists because a
  controller chooses to write into it. This matters more than it sounds:
  `wddctl note`'s pending notes live only in `state.json`'s
  `reconcile.pendingNotes`, and `reconcile done` deletes them — not marks
  them resolved, deletes them. The event log doesn't even retain the note
  text, only that a `note.added` event happened. If a discovery matters
  beyond the moment reconciliation clears, the controller must write it
  into `.wdd/shared-context/` *before* calling `reconcile done`; nothing
  else will remember it.
- **Verification honesty.** `verify record --status passed` takes your
  word for it. The `wdd-run` skill's explicit instruction — "never record
  `passed` you didn't observe" — is the only thing standing between this
  and a rubber stamp.

The pattern across every enforced item: it's something a state machine can
check mechanically from Git and JSON alone (does this SHA match, is this
task active, is this ref an ancestor of that one). Everything on the
convention list requires understanding what the code actually does — and
that's exactly the half of the job WDD deliberately leaves to agents and
skills rather than trying, and failing, to encode into a transition
function.
