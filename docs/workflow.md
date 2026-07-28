# The developer workflow

`docs/wddctl.md` documents commands. `docs/artifact-schema.md` documents file
formats. Neither says how a project actually moves from nothing to a merged
scope. This document does. Every command block below was run for real
against a scratch repository on this branch; nothing here is paraphrased
output.

## Talking to an agent

This is the normal way to use WDD. You open a session with Claude Code (or
Codex, or another agent with the skills installed) and say something like
"let's add refresh tokens" or "what's the status of the auth-refresh
scope?" You never type `wddctl` yourself. The agent reads a skill —
`wave-driven-development`, `wdd-setup`, `wdd-plan`, `wdd-run`, `wdd-worker`,
`wdd-review`, or `wdd-status` — decides it applies, and runs `wddctl`
commands on your behalf. This is a hard rule, not a suggestion: every one
of those skills but `wdd-worker` — which never runs `wddctl` at all — opens
with "you run every `wddctl` command in this skill yourself; presenting a
command to the user instead of executing it is a protocol violation." The
worker is deliberately excepted: `wddctl` resolves `--state` and `--repo`
relative to the working directory, and a worker's working directory is its
own isolated worktree, not the controller's checkout — running `wddctl`
there would read the wrong state file, or none at all. A worker commits and
reports back; the controller records the submission. Your intervention is
prose: "actually split TASK-002 differently," "block TASK-004." The agent
translates that into `wddctl block` or a `plan.json` edit.

**The hard rule is text, not enforcement.** A skill is a Markdown file
loaded into an agent's context window. It has no execution privileges of
its own — it cannot run a shell command any more than this paragraph can.
A step like `run_review → dispatch a reviewer per wdd-review; their
findings go into recordWith` is just text the agent reads and, because it
has shell access as part of its own tool set and not because the skill
granted it one, decides to act on. If the agent ignores the skill, nothing
stops it; the hard rule obligates, it doesn't enforce. Contrast the state
machine in `wave_delivery/engine.py`: if two tasks share a conflict domain,
`task.started` raises `IllegalTransition` regardless of what any skill says
or any agent intends. That distinction — prose an agent is obligated to
follow but could still ignore, versus a transition function that cannot be
argued with — is the whole design of WDD, and recurs throughout this
document as "enforced vs. convention."

## The three roles

WDD names three roles: controller, worker, reviewer — roles, not
processes; how they map onto actual agent sessions depends on your setup.

**Controller.** Runs the `wddctl next` loop, dispatches workers and
reviewers, routes P1/P2 findings back to a fix worker, runs reconciliation,
merges. Never writes task code, never hand-edits `state.json`. Usually one
long-lived agent session that stays alive for the whole scope, because it's
the thing holding the "what's next" thread. Its skill is `wdd-run`.

**Worker.** Implements exactly one task, in the worktree the controller's
`wddctl start` created for it. Short-lived — it exists for one task's
lifetime and then is done. The controller typically dispatches it as a
subagent (Claude Code's Task tool, or an equivalent) with the task ID and
worktree path, and gets back a status token when it finishes. Its skill is
`wdd-worker`.

**Reviewer.** Reviews one task's diff against its brief and classifies
findings as P1/P2/P3. Also short-lived, dispatched per review, and — this
matters — should not be the same context that wrote the diff, for the same
reason a human wouldn't approve their own PR. Its skill is `wdd-review`.

In a small scope worked by one person and one agent, all three roles can be
the same session switching hats — `wddctl` doesn't know or care who ran a
command, only that the transition is legal. What it does care about is
evidence: a review's findings are pinned to a head SHA regardless of who
produced them, so even a self-review that's honest leaves an auditable
trail.

## From nothing to merged

A real run, `wddctl init` through a merged, reconciled scope, captured
against a scratch repository seeded with one real commit.

### Setup: init, resolve, ratify

`wddctl init --repo .` scaffolds `.wdd/` deterministically — nothing here
is prose-improvised anymore:

```
$ wddctl init --repo .
{
  "alreadyInitialized": false,
  "created": [".wdd/config.json", ".wdd/constitution.md", ".wdd/tasks",
              ".wdd/shared-context", ".wdd/state.json"],
  "hint": "run 'wddctl next' and follow it",
  "openQuestions": [
    {"path": "merge.surface", "options": ["pr", "local"],
     "question": "Review/merge surface: 'pr' pushes task branches and mirrors review findings to pull-request comments; 'local' keeps the whole loop offline in state.json. Which should this repository use?"}
  ]
}
```

`init` probed the repository and found a verification command (a `tests/`
directory), so only the merge-surface question needs an answer. `next`
during setup emits exactly one action at a time:

```
$ wddctl next
{"actions": [{"action": "resolve_config", "task": "-",
  "command": "wddctl config set <path> <value>",
  "judgment": "ask the user every listed question in ONE round, then record each answer",
  "questions": [...]}], "blockers": [], "phase": "setup", "revision": 0, "scope": null}
$ wddctl config set merge.surface local
{"openQuestions": 0, "path": "merge.surface", "value": "local"}
```

`next` moves to ratification, which signs `config.json` and
`constitution.md` together — the fingerprint is computed over both files'
exact contents:

```
$ wddctl next
{"actions": [{"action": "ratify", "task": "-",
  "command": "wddctl constitution ratify --by NAME",
  "judgment": "show the user config.json and constitution.md; ratify only after explicit sign-off"}]}
$ wddctl constitution ratify --by ivo
{"decisionFingerprint": "sha256:b84d70...", "duplicate": false, "revision": 1}
$ wddctl next
{"actions": [{"action": "plan", "task": "-",
  "command": "wddctl plan apply --plan plan.json --repo .",
  "judgment": "decompose the work per the wdd-plan skill, write task briefs, then apply"}]}
```

### Intake: `wdd-plan`, `.wdd/spec.md`, and a recorded approval

`wdd-plan` is the front door: it turns "let's add refresh tokens" into an
agreed spec, a `plan.json`, and an applied, approved scope. It ingests
whatever the user brings, pushes back on gaps in one compact round of
questions, then writes the agreed understanding to `.wdd/spec.md` with
exactly four sections — Goal, In scope, Out of scope, Acceptance
criteria — checkable enough that a later finalize pass can review the epic
branch against it. For this run, the acceptance criteria that matter are
`issueRefreshToken` existing with a typed signature, and a 401 triggering
exactly one silent refresh-and-retry, not a loop (the skill's template has
the full four-section skeleton).

From there it decomposes into tasks — one worker, one branch, one diff,
one merge each — getting conflict domains right: too coarse serializes
everything, too narrow lets two workers clobber the same file. Before
applying, it shows the projected admission order and any lint findings:

```
$ wddctl plan preview --plan plan.json
{"scope": "SCOPE-auth-refresh", "maxConcurrent": 3,
 "note": "projected admission order; rounds are a view, not a gate",
 "rounds": [{"round": 1, "tasks": ["TASK-001-token-types", "TASK-003-client-retry"]},
            {"round": 2, "tasks": ["TASK-002-refresh-endpoint"]}]}
$ wddctl plan lint --plan plan.json
{"findings": [], "strict": false}
```

Only on explicit approval does it apply, and the approval is now recorded:

```
$ wddctl plan apply --plan plan.json --repo . --approved-by ivo
{"approvedBy": "ivo", "created": false, "duplicate": false, "lint": [],
 "revision": 3, "scope": "SCOPE-auth-refresh",
 "diff": {"added": ["TASK-001-token-types", "TASK-002-refresh-endpoint",
                     "TASK-003-client-retry"], "removed": [], "updated": []},
 "base": {"action": "created", "baseRef": "wdd/auth-refresh", "from": "HEAD"}}
```

`scope.approval` is now part of state, visible in `status --json` for as
long as the scope exists. Re-applying later without `--approved-by`
(adding a task, fixing a domain) preserves it rather than erasing it.

### The `next` loop, gate by gate

Following `TASK-001-token-types` (risk `high`) and `TASK-003-client-retry`
(risk `normal`) from nothing to merged; `next` output below is trimmed to
the fields that matter for a given step. Both admit at once — they share no
conflict domain and neither depends on the other:

```
$ wddctl next
{"actions": [
  {"task": "TASK-001-token-types", "action": "start_task",
   "command": "wddctl start --task TASK-001-token-types --repo ."},
  {"task": "TASK-003-client-retry", "action": "start_task",
   "command": "wddctl start --task TASK-003-client-retry --repo ."}],
 "blockers": [{"code": "dependencies", "task": "TASK-002-refresh-endpoint",
               "dependsOn": ["TASK-001-token-types"]}]}
```

`command` is a verb to run as-is, no judgment required. For actions that
need judgment instead (`await_worker`, `run_review`, `run_verification`,
`assign_fix_writer`, `run_reconciliation`), the payload carries
`recordWith`: do the work, then run the given command to record it.

```
$ wddctl start --task TASK-001-token-types --repo .
{"task": "TASK-001-token-types", "action": "create_branch_and_worktree",
 "branch": "task/TASK-001-token-types", "baseRef": "wdd/auth-refresh",
 "worktree": "/.../worktrees/SCOPE-auth-refresh/TASK-001-token-types", "revision": 4}
```

One call verified the task admissible, created its branch and an isolated
worktree, and flipped it to `in_progress`. The controller dispatches a
worker per `wdd-worker` into that worktree.

**Worker implements and commits; controller submits.** The worker writes
`issueRefreshToken` — using `Math.random()`, as it happens — commits it in
the worktree, never in the controller's own checkout, and reports back.
The worker never runs `wddctl` itself; the controller records the
submission on its behalf:

```
$ wddctl submit --task TASK-001-token-types --repo .
{"task": "TASK-001-token-types", "event": "task.pr_recorded",
 "headSha": "3f8edde...", "status": "review", "revision": 6}
```

`submit` reads the head SHA from the branch itself — the worker never types
one. Status went to `review` because this task is `"risk": "high"` under
`reviewPolicy: risk_based`. `TASK-003-client-retry` is normal risk, so its
`submit` goes straight to `in_progress`, and `next` shows it needing
verification, not review:

```
$ wddctl next
{"actions": [
  {"task": "TASK-001-token-types", "action": "run_review",
   "recordWith": "wddctl review record --task TASK-001-token-types --reviewer NAME --findings '[]'"},
  {"task": "TASK-003-client-retry", "action": "run_verification",
   "recordWith": "wddctl verify record --task TASK-003-client-retry --status passed --command '...'"}]}
```

**Review finds a P1.** The reviewer reads the diff against the brief and
finds something real:

```
$ wddctl review record --task TASK-001-token-types --reviewer "codex-review" \
  --findings '[{"severity":"P1","summary":"issueRefreshToken uses Math.random() for the token value; not cryptographically secure","file":"src/auth/tokens.ts","line":9}]'
{"duplicate": false, "outcome": "blocking", "status": "in_progress", "revision": 8}
```

A P1 or P2 finding sets `outcome: blocking` and drops the gate to
`needs_fixes`, regardless of how far along verification or freshness were.
`TASK-003-client-retry` has no such finding — verify, freshness, and merge
run straight through:

```
$ wddctl verify record --task TASK-003-client-retry --status passed --command true
{"duplicate": false, "status": "merge_ready", "revision": 9}
$ wddctl freshness record --task TASK-003-client-retry --repo .
{"classification": "current", "duplicate": false, "revision": 10}
$ wddctl merge --task TASK-003-client-retry --repo .
{"action": "merged", "baseRef": "wdd/auth-refresh", "headSha": "b489cf7...", "revision": 11}
$ wddctl release --task TASK-003-client-retry --repo .
{"cleanup": "cleaned_up", "duplicate": false, "revision": 12}
```

`verify record` jumped the status straight from `in_progress` to
`merge_ready` in one call — a documented `ready_to_merge` gate sits between
them, but the transition checks it internally and advances at once, so you
never observe it in `next`'s output. `merge` performed a real `git merge
--no-ff` into the scope's integration checkout, then called `git merge-base
--is-ancestor` to prove the head actually landed before recording
`task.merged` — no path records a merge without that check passing.

**Fix cycle.** Back on `TASK-001-token-types`, `next` names the fix, and
`recordWith` is plain `submit` — a fix is just more commits on the same
branch, not a special verb:

```
$ wddctl next
{"actions": [{"task": "TASK-001-token-types", "action": "assign_fix_writer",
  "recordWith": "wddctl submit --task TASK-001-token-types --repo ."}]}
```

The fix worker replaces `Math.random()` with `crypto.randomBytes`, commits
in its worktree, and reports back; the controller records the resubmission:

```
$ wddctl submit --task TASK-001-token-types --repo .
{"event": "task.head_updated", "headSha": "8f136d5...", "status": "review", "revision": 13}
```

`task.head_updated`, not `task.pr_recorded` — same PR reference, new head.
A new commit clears review, verification, and freshness outright and sends
the task back to `review`, with no memory that this diff was flagged once
already. The reviewer re-reviews from scratch, finds nothing, and the task
runs the same verify → freshness → merge → release sequence `TASK-003` did.

### Reconciliation and governance drift

Every `reconcileEveryNMerges` merges (3, here), a checkpoint comes due —
and this is where a durable discovery has to be written down or it's gone.
`wddctl note` only queues text in `reconcile.pendingNotes`, and
`reconcile done` *deletes* the queue, not marks it resolved. If a note
matters beyond this moment, write it into `.wdd/shared-context/` first;
nothing else remembers it:

```
$ wddctl next
{"actions": [{"task": "-", "action": "run_reconciliation", "code": "merge_count",
  "merges": 3, "recordWith": "wddctl reconcile done"}]}
$ wddctl note --note "refresh tokens are opaque strings, not JWTs"
{"duplicate": false, "revision": 25}
$ wddctl reconcile done
{"duplicate": false, "revision": 26}
```

Governance doesn't stay ratified through an unrecorded edit, either.
Loosen the review policy directly in `config.json` without going through
`amend`, and `next` refuses everything until it's re-signed:

```
$ wddctl config set review.policy always
{"openQuestions": 0, "path": "review.policy", "value": "always"}
$ wddctl next
{"actions": [], "blockers": [{"code": "governance_drift",
  "message": "config/constitution changed since ratification; amend before executing",
  "ratified": "sha256:ef67ee...", "actual": "sha256:f5831c..."}]}
$ wddctl constitution amend --by ivo
{"decisionFingerprint": "sha256:f5831c...", "duplicate": false, "revision": 27}
```

`next` unblocks the instant the fingerprint matches — this recurs for the
life of the scope, any time `config.json` or `constitution.md` changes.

## Where you intervene

The loop runs itself; your job is to notice when it shouldn't.

**Reading state.** `wddctl status` gives a one-line brief; `status --json`
is the full summary `next` and `render` are built from; `render --output
.wdd/state.md` writes a generated Markdown snapshot — read it, never edit
it, regenerate it after anything changes.

**Editing `plan.json` mid-flight.** `plan apply` is re-runnable and diffs
against current state — adding a task you missed is a plain re-apply:

```
$ wddctl plan apply --plan plan.json --repo .
{"created": false, "duplicate": false, "lint": [], "revision": 28,
 "diff": {"added": ["TASK-004-session-ui", "TASK-005-token-rotate"],
          "removed": [], "updated": []}}
```

Safe at any point *before* a task starts — `plan apply` refuses outright to
edit or remove a task that has already left `todo`; you can only fix
domains on tasks that haven't started, or add new ones.

**Blocking, unblocking, cancelling.**

```
$ wddctl block --task TASK-004-session-ui --reason "waiting on design sign-off"
{"duplicate": false, "revision": 29}
$ wddctl unblock --task TASK-004-session-ui
{"duplicate": false, "revision": 30}
```

Blocking frees the task's conflict domains immediately. `unblock` returns a
task to `todo` if it never got a PR, or `in_progress` if it did; `cancel`
is terminal.

**Forcing a refresh.** When `next` reports `check_branch_freshness` with
`materially_stale` or `conflicted`, run `wddctl refresh --task ID --repo .`
rather than waiting for it to resolve itself — it merges the base into the
task branch and re-records the head, deliberately invalidating any review
or verification evidence pinned to the old one. Not wasted work: the diff
against the base is genuinely different now.

## Failure modes and what to do

**A refresh conflicts.**

```
$ wddctl refresh --task TASK-005-token-rotate --repo .
wddctl: refreshing TASK-005-token-rotate from wdd/auth-refresh conflicts in:
src/auth/tokens.ts; resolve it in the task worktree, then re-run
```

Read literally: `wddctl` already ran `git merge --abort` before printing
that — `git status` in the worktree comes back clean, no conflict markers
waiting for you. Redo the merge by hand, in the task worktree:

```
$ cd <task worktree>
$ git merge wdd/auth-refresh --no-edit
CONFLICT (content): Merge conflict in src/auth/tokens.ts
$ # resolve the conflict in src/auth/tokens.ts
$ git add src/auth/tokens.ts && git commit --no-edit
```

Run `wddctl refresh` again. It notices the branch already contains the
base and adopts the head you produced:

```
$ wddctl refresh --task TASK-005-token-rotate --repo .
{"action": "adopted_external_merge", "previousHeadSha": "a88f45d...",
 "headSha": "e70a99c...", "duplicate": false, "revision": 33,
 "note": "branch was already current; recorded its head and invalidated evidence"}
```

Evidence is deliberately invalidated: the reviewed commit is no longer the
tip. `wddctl submit` does the same thing here and is equally valid — to
`wddctl`, "resolved a conflict by hand" and "have new work" are the same
event.

**A worker returns `BLOCKED`.** `wddctl block --task ID --reason "..."` —
see above. Starting the task again after `unblock` re-attaches its
worktree rather than restarting the work.

**A P1 keeps reappearing.** The fix worker likely patched the symptom
without addressing what the reviewer flagged, or the reviewer is working
from stale context. Read the diff between the two head SHAs yourself
before dispatching a third attempt — don't assume repetition converges on
its own.

**`RevisionConflict` when two controllers share a scope.**

```
$ wddctl note --note "..." --expected-revision 5
wddctl: expected revision 5, found 28
```

This only happens when you pass `--expected-revision` yourself — opt-in
optimistic concurrency for two controllers racing on the same `state.json`.
Re-read the current revision and retry. Omit the flag and you cannot hit
this: `wddctl` reads the live revision under the same lock that guards the
write.

## What is enforced vs. what is convention

Get this distinction right, because it determines how much you have to
trust the agent doing the work.

**Enforced by the state machine — cannot be talked around:**

- **Conflict-domain exclusion**, checked inside `task.started` itself, not
  just advertised by `next`.
- **Dependency ordering** — an unmet `dependsOn` blocks admission via the
  same `admission_blocker` path as conflict domains.
- **`scope.maxConcurrent`** — once that many tasks are active, nothing
  further admits regardless of how free its domains are.
- **Evidence pinned to head SHA.** `review.recorded` and
  `verification.recorded` reject data whose `headSha` doesn't match the
  current head; a new commit clears both fields outright.
- **Git-verified merge.** `task.merged` requires a real
  `git merge-base --is-ancestor` to have passed; no way to fake it.
- **Governance drift.** An unratified edit to `config.json` or
  `constitution.md` blocks every governed verb until `amend` re-signs it —
  checked live from the files, no proposal snapshot required.

**Convention — depends on the agent reading and following the skill:**

- **Conflict-domain accuracy.** Nothing checks whether a task's declared
  domains are *missing* a file it actually writes — `wdd-worker`'s "stay
  in scope" instruction, backstopped by an equally unenforced reviewer
  check.
- **Review and verification honesty.** `review record` and
  `verify record --status passed` take your word for it; the schema
  validates shape, not substance.
- **Task decomposition and sizing.** Nothing stops a `plan.json` with one
  task that touches the entire repository.
- **Plan approval.** `--approved-by NAME` records who signed off, but
  nothing refuses an apply that omits it — refusing an unapproved plan is
  `wdd-plan`'s instruction, not a gate the state machine enforces.
- **`.wdd/shared-context/`.** A plain folder `wddctl` never writes to; a
  discovery that must survive `reconcile done` needs a controller to write
  it there itself.

The pattern: every enforced item is checkable mechanically from Git and
JSON alone — does this SHA match, is this ref an ancestor of that one, does
this fingerprint match. Everything on the convention list requires
understanding what the code actually does — exactly the half of the job
WDD leaves to agents and skills rather than encoding into a transition
function.

## Appendix: driving `wddctl` yourself

Everything above assumes an agent is running the commands. Nothing stops
you from running them directly: scripting a CI step against `wddctl status
--json`, debugging why a task won't admit, or preferring a terminal to a
chat transcript. `docs/wddctl.md` is written for that audience — full flag
reference, every JSON shape, no assumed narrative. Every transcript above is
the literal command a person or an agent would type; there's no separate
"human syntax."

One thing changes: the hard rule binding an agent to a skill's obligations
doesn't apply to you. You can apply an unapproved plan or record a
verification you didn't run — nothing in `wddctl` stops a human operator,
because those obligations live in the skills, and you aren't reading one.
"Enforced vs. convention" above still tells you exactly what Git and JSON
catch regardless of who's typing, and what's on you either way.
