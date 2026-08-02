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
`wave-driven-development`, `wdd-setup`, `wdd-intake`, `wdd-plan`, `wdd-run`,
`wdd-worker`, `wdd-review`, `wdd-status`, or `wdd-runners` — decides it
applies, and runs `wddctl` commands on your behalf. This is a hard rule, not
a suggestion: every one of those skills but `wdd-worker` — which never runs
`wddctl` at all — opens with "you run every `wddctl` command in this skill
yourself; presenting a command to the user instead of executing it is a
protocol violation." The worker is deliberately excepted: `wddctl` resolves
`--state` and `--repo` relative to the working directory, and a worker's
working directory is its own isolated worktree, not the controller's
checkout — running `wddctl` there would read the wrong state file, or none
at all. A worker commits and reports back; the controller records the
submission. Your intervention is prose: "actually split TASK-002
differently," "block TASK-004." The agent translates that into `wddctl
block` or a `plan.json` edit.

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
              ".wdd/shared-context", ".wdd/.gitignore", ".wdd/state.json"],
  "hint": "run 'wddctl next' and follow it",
  "openQuestions": [
    {"path": "merge.surface", "options": ["pr", "local"],
     "question": "Should each task ship as a real GitHub pull request, or stay fully local? Pull requests give you the familiar review surface (branches pushed, findings mirrored as PR comments); local keeps the whole loop offline with no pushes — good for solo or offline work."},
    {"path": "models",
     "question": "Which models should do the work? Three roles matter: everyday implementation, a stronger model for high-risk tasks, and review (usually your strongest — it guards the merges). Name models your agent harness understands, or say the harness defaults are fine."}
  ]
}
```

`init` probed the repository and found a verification command (a `tests/`
directory), so that question is pre-answered; the merge-surface and model
questions remain. The `.wdd/.gitignore` entry it writes covers
`dispatch/` — the runner-dispatch scratch directory below — from the first
commit. `next` during setup emits exactly one action at a time:

```
$ wddctl next
{"actions": [{"action": "resolve_config", "task": "-",
  "command": "wddctl config set <path> <value>",
  "judgment": "relay every listed question to the user in ONE round, in plain language (never show config paths or JSON syntax), then translate the answers into config set yourself",
  "questions": [...]}], "blockers": [], "phase": "setup", "revision": 0, "scope": null}
$ wddctl config set merge.surface local
{"openQuestions": 1, "path": "merge.surface", "value": "local"}
$ wddctl config set models '{"planning": null, "implementation": {"default": null, "highRisk": null}, "review": null}'
{"openQuestions": 0, "path": "models", "value": {"implementation": {"default": null, "highRisk": null}, "planning": null, "review": null}}
```

(The user said "harness defaults are fine," so the model mapping is
recorded as all nulls — dispatchers pick their own default.)

`next` moves to ratification, which signs `config.json` and
`constitution.md` together — the fingerprint is computed over both files'
exact contents:

```
$ wddctl next
{"actions": [{"action": "ratify", "task": "-",
  "command": "wddctl constitution ratify --by NAME",
  "judgment": "show the user config.json and constitution.md; ratify only after explicit sign-off"}]}
$ wddctl constitution ratify --by ivo
{"decisionFingerprint": "sha256:dfe31857...", "duplicate": false, "revision": 1}
$ wddctl next
{"actions": [{"action": "agree_spec", "task": "-",
  "recordWith": "wddctl intake spec --approved-by NAME",
  "judgment": "agree .wdd/spec.md with the user (goal, in/out of scope, numbered acceptance criteria) per the wdd-intake skill's spec stage, then record it"}]}
```

Governance done, `next` hands off to the intake ladder instead of `wdd-plan`
directly — that's the v5 front half.

### Intake: the ladder — spec, research, design

`wdd-intake` owns three rungs, one conversation, before any decomposition
happens: **spec → research → design**. `next` walks them one at a time and
names the recording verb; the skill supplies the judgment, `wddctl` the
bookkeeping. For this run: the agreed spec is refresh tokens plus a
retry-once client, with two numbered acceptance criteria
(`issueRefreshToken` existing with a typed signature, and a 401 triggering
exactly one silent refresh-and-retry). `.wdd/spec.md` gets the skill's
four-section skeleton; recording binds the approval to the file's exact
bytes:

```
$ wddctl intake spec --approved-by ivo
{"criteria": 2, "duplicate": false, "revision": 2}
$ wddctl next
{"actions": [{"action": "research", "task": "-",
  "recordWith": "wddctl intake research --done --by NAME --artifacts PATH... (or --skip --by NAME --reason '...')",
  "judgment": "read the named reference implementation and build the contract inventory per the wdd-intake skill's research stage, or record an explicit, attributed skip when no external contract applies"}]}
```

This scope touches nothing external — no reference API, no protocol to
transcribe — so research is skipped, explicitly and attributed, not
silently:

```
$ wddctl intake research --skip --by ivo --reason "no external contract; refresh tokens are opaque strings this repo issues itself"
{"duplicate": false, "revision": 3}
$ wddctl next
{"actions": [{"action": "agree_design", "task": "-",
  "recordWith": "wddctl intake design --approved-by NAME --deliverable-command '...'",
  "judgment": "agree .wdd/design.md (components, interfaces, integration surfaces, epic deliverable) with the user per the wdd-intake skill's design stage, then record it with the command that proves the epic deliverable"}]}
```

`.wdd/design.md` names two components (`TokenService`, `RefreshClient`),
their Consumes/Produces, the integration surfaces each owns
(`src/auth/tokens.ts`, `src/auth/client.ts`), and the epic deliverable —
the command that proves the whole scope works. The deliverable command is
required, not optional, and is recorded with the design approval, not in
global config:

```
$ wddctl intake design --approved-by ivo --deliverable-command "python3 -m unittest discover -s tests"
{"duplicate": false, "revision": 4}
$ wddctl next
{"actions": [{"action": "plan", "task": "-",
  "command": "wddctl plan apply --plan plan.json --repo . --approved-by NAME",
  "judgment": "decompose the work per the wdd-plan skill, write task briefs, show the user the diff for explicit approval, then apply with the approving human's name"}]}
```

Every rung binds to the bytes it approved; editing `spec.md` or
`design.md` after this point re-opens that rung, cascades downstream, and
during execution surfaces as an `intake_drift` blocker — `docs/wddctl.md`'s
intake section has the full drift-and-cascade transcript; this run stays
clean.

### Plan: decomposition and a recorded approval

`wdd-plan` no longer agrees anything — the ladder above already did. It
turns `.wdd/spec.md` and `.wdd/design.md` into `plan.json` plus one brief
per task, each brief now carrying **Deliverable** and **Interfaces**
sections and a `context` field pointing at the `.wdd`-relative files the
worker needs (`spec.md#AC-1`, `design.md`, …). Three tasks: token issuance,
the endpoint that wires it in, and the client's retry-once logic — one
worker, one branch, one diff, one merge each. Before applying, it shows the
projected admission order and any lint findings:

```
$ wddctl plan preview --plan plan.json
{"scope": "SCOPE-auth-refresh", "maxConcurrent": 3,
 "note": "projected admission order; rounds are a view, not a gate",
 "rounds": [{"round": 1, "tasks": ["TASK-001-token-types", "TASK-003-client-retry"]},
            {"round": 2, "tasks": ["TASK-002-refresh-endpoint"]}]}
$ wddctl plan lint --plan plan.json
{"findings": [], "strict": false}
```

Only on explicit approval does it apply. `--approved-by` now records a
**composite** fingerprint — over the normalized plan, every brief file, and
every `context`-ref file, not just the plan JSON:

```
$ wddctl plan apply --plan plan.json --repo . --approved-by ivo
{"approvedBy": "ivo", "created": true, "duplicate": false, "lint": [],
 "revision": 5, "scope": "SCOPE-auth-refresh",
 "diff": {"added": ["TASK-001-token-types", "TASK-002-refresh-endpoint",
                     "TASK-003-client-retry"], "removed": [], "updated": []},
 "base": {"action": "created", "baseRef": "wdd/auth-refresh", "from": "HEAD"}}
```

`scope.approval` is now part of state, visible in `status --json` for as
long as the scope exists. A later edit to a brief or a `context` file —
without re-running `plan apply --approved-by` — is `plan_drift`, caught by
the same execution gate as intake drift.

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
 "worktree": "/.../worktrees/SCOPE-auth-refresh/TASK-001-token-types",
 "snapshot": "dispatch/TASK-001-token-types-1", "revision": 6}
```

One call verified the task admissible, created its branch and an isolated
worktree, and flipped it to `in_progress`. `start`'s output now also names
the task's **snapshot dir** — a read-only, immutable copy of the brief and
every `context` ref, materialized the instant the task admits. Dispatch
packets are built from the snapshot, never the live checkout: a file edited
(or edited-and-restored) in the controller's own worktree after this point
cannot reach the worker or reviewer either way. Those same recorded digests
are what let `next` later report `inputs_changed` if a `context` file
changes while a task is mid-flight — `docs/wddctl.md`'s Runners section has
the full real `inputs_changed` → `rebind` transcript; this run doesn't hit
it. The controller dispatches a worker per `wdd-worker` into that worktree.

**Worker implements and commits; controller submits.** The worker writes
`issueRefreshToken` — using `Math.random()`, as it happens — commits it in
the worktree, never in the controller's own checkout, and reports back.
The worker never runs `wddctl` itself; the controller records the
submission on its behalf:

```
$ wddctl submit --task TASK-001-token-types --repo .
{"task": "TASK-001-token-types", "event": "task.pr_recorded",
 "headSha": "cf5f483...", "status": "review", "revision": 10}
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
  --findings '[{"severity":"P1","summary":"issueRefreshToken uses Math.random() for the token value; not cryptographically secure","file":"src/auth/tokens.ts","line":2}]'
{"duplicate": false, "outcome": "blocking", "status": "in_progress", "revision": 12}
```

A P1 or P2 finding sets `outcome: blocking` and drops the gate to
`needs_fixes`, regardless of how far along verification or freshness were.
`TASK-003-client-retry` has no such finding — verify, freshness, and merge
run straight through:

```
$ wddctl verify record --task TASK-003-client-retry --status passed --command "python3 -m unittest discover -s tests"
{"duplicate": false, "status": "merge_ready", "revision": 13}
$ wddctl freshness record --task TASK-003-client-retry --repo .
{"classification": "current", "duplicate": false, "revision": 14}
$ wddctl merge --task TASK-003-client-retry --repo .
{"action": "merged", "baseRef": "wdd/auth-refresh", "headSha": "16f9790...", "revision": 15}
$ wddctl release --task TASK-003-client-retry --repo .
{"cleanup": "cleaned_up", "duplicate": false, "revision": 16}
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
{"event": "task.head_updated", "headSha": "5d0673a...", "status": "review", "revision": 17}
```

`task.head_updated`, not `task.pr_recorded` — same PR reference, new head.
A new commit clears review, verification, and freshness outright and sends
the task back to `review`, with no memory that this diff was flagged once
already. The reviewer re-reviews from scratch, finds nothing, and the task
runs the same verify → freshness → merge → release sequence `TASK-003` did
(revision 22 by the time it's released). `TASK-002-refresh-endpoint` then
admits — its one dependency is merged — and runs the identical gate
sequence once implemented:

```
$ wddctl next
{"actions": [{"task": "TASK-002-refresh-endpoint", "action": "start_task",
  "command": "wddctl start --task TASK-002-refresh-endpoint --repo ."}], "blockers": []}
```

## Where you intervene

The loop runs itself; your job is to notice when it shouldn't.

**Reading state.** `wddctl status` gives a one-line brief; `status --json`
is the full summary `next` and `render` are built from; `render --output
.wdd/state.md` writes a generated Markdown snapshot — read it, never edit
it, regenerate it after anything changes.

**Editing `plan.json` mid-flight.** `plan apply` is re-runnable and diffs
against current state — adding a task you missed is a plain re-apply, but
a nonempty diff now **requires** `--approved-by`; a bare re-apply refuses
outright:

```
$ wddctl plan apply --plan plan.json --repo .
wddctl: plan apply refuses: this plan changes the scope; re-run with --approved-by NAME once the user has reviewed the diff
$ wddctl plan apply --plan plan.json --repo . --approved-by ivo
{"approvedBy": "ivo", "created": false, "duplicate": false, "lint": [],
 "revision": 31, "diff": {"added": ["TASK-004-session-ui", "TASK-005-token-rotate"],
                           "removed": [], "updated": []}}
```

Safe at any point *before* a task starts — `plan apply` refuses outright to
edit or remove a task that has already left `todo`; you can only fix
domains on tasks that haven't started, or add new ones.

**Reconciliation and governance drift.** Every `reconcileEveryNMerges`
merges (3, here), a checkpoint comes due — and this is where a durable
discovery has to be written down or it's gone. `wddctl note` only queues
text in `reconcile.pendingNotes`, and `reconcile done` *deletes* the queue,
not marks it resolved. If a note matters beyond this moment, write it into
`.wdd/shared-context/` first; nothing else remembers it. Three tasks are
merged by the time the plan edit above lands, so the same `next` call that
proposes starting the two new tasks also reports the checkpoint:

```
$ wddctl next
{"actions": [
  {"task": "-", "action": "run_reconciliation", "code": "merge_count",
   "merges": 3, "recordWith": "wddctl reconcile done"},
  {"task": "TASK-004-session-ui", "action": "start_task",
   "command": "wddctl start --task TASK-004-session-ui --repo ."},
  {"task": "TASK-005-token-rotate", "action": "start_task",
   "command": "wddctl start --task TASK-005-token-rotate --repo ."}], "blockers": []}
$ wddctl note --note "refresh tokens are opaque strings, not JWTs"
{"duplicate": false, "revision": 36}
$ wddctl reconcile done
{"duplicate": false, "revision": 37}
```

Governance doesn't stay ratified through an unrecorded edit, either.
Loosen the review policy directly in `config.json` without going through
`amend`, and `next` refuses everything until it's re-signed — the same
check now also covers the intake ladder's fingerprints, not just
`config.json`/`constitution.md`:

```
$ wddctl config set review.policy always
{"openQuestions": 0, "path": "review.policy", "value": "always"}
$ wddctl next
{"actions": [], "blockers": [{"code": "governance_drift",
  "message": "config/constitution changed since ratification; amend before executing",
  "ratified": "sha256:dfe31857...", "actual": "sha256:d145263f..."}]}
$ wddctl constitution amend --by ivo
{"decisionFingerprint": "sha256:d145263f...", "duplicate": false, "revision": 38}
```

`next` unblocks the instant the fingerprint matches — this recurs for the
life of the scope, any time `config.json`, `constitution.md`, or an
approved intake/plan artifact changes.

**Blocking, unblocking, cancelling.** `TASK-005-token-rotate` is admitted
and submitted (status `review`) below; block it to demonstrate the verb:

```
$ wddctl block --task TASK-005-token-rotate --reason "waiting on design sign-off for token rotation"
{"duplicate": false, "revision": 46}
$ wddctl unblock --task TASK-005-token-rotate
{"duplicate": false, "revision": 47}
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

**A refresh conflicts.** `TASK-004-session-ui` merges cleanly; while
`TASK-005-token-rotate` is still in flight, an out-of-band edit lands on
the base branch touching the same file:

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
{"action": "adopted_external_merge", "previousHeadSha": "772d4ad...",
 "headSha": "750e06e...", "duplicate": false, "revision": 45,
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
wddctl: expected revision 5, found 45
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
- **Approved-bytes fingerprints.** Every intake rung (`spec`/`research`/
  `design`) and the plan's composite approval are bound to a SHA-256 of the
  exact artifact bytes at approval time. Editing one afterward is drift,
  blocked by the same execution gate as governance drift
  (`intake_drift`/`plan_drift`); a **nonempty plan diff refuses `plan
  apply` outright without `--approved-by`** — see "Editing `plan.json`
  mid-flight" above for the real refusal.

**Convention — depends on the agent reading and following the skill:**

- **Conflict-domain accuracy.** Nothing checks whether a task's declared
  domains are *missing* a file it actually writes — `wdd-worker`'s "stay
  in scope" instruction, backstopped by an equally unenforced reviewer
  check.
- **Review and verification honesty.** `review record` and
  `verify record --status passed` take your word for it; the schema
  validates shape, not substance. The same is true of `--approved-by NAME`
  itself: the state machine now refuses a changed plan without it, but
  nothing checks that `NAME` actually read the diff before typing it —
  that discipline is `wdd-plan`'s and `wdd-intake`'s instruction, not a
  gate.
- **Task decomposition and sizing.** Nothing stops a `plan.json` with one
  task that touches the entire repository.
- **`.wdd/shared-context/`.** A plain folder `wddctl` never writes to; a
  discovery that must survive `reconcile done` needs a controller to write
  it there itself.

The pattern: every enforced item is checkable mechanically from Git and
JSON alone — does this SHA match, is this ref an ancestor of that one, does
this fingerprint match. Everything on the convention list requires
understanding what the code actually does — exactly the half of the job
WDD leaves to agents and skills rather than encoding into a transition
function.

## Finishing a scope

Everything above ends the moment the last task merges — that used to be
where the narrative, and the scope, went quiet. It doesn't anymore. The
instant every task reaches `done` (or `cancelled`), `derived_phase` moves
the scope into `finalize`, and `wddctl next` stops returning empty: it
starts naming scope-level work, one action at a time, the same discipline
it used per task. There is no separate command to trigger this — `status`
and `next` detect the phase and switch shape on their own.

The ladder mirrors a task's own gates, but at the whole-epic-branch level:
a final review against `.wdd/spec.md`'s acceptance criteria (the same
document `wdd-intake` wrote at intake — see above), full verification, a
handoff to the human who performs the actual merge, and `delivered` only
once that merge is Git-provably real. `wddctl` never merges the epic
branch into the target branch itself — that step is human-owned by
design, the same way `merge.mode: human` reserves a task's own merge for
a person; there is no code path anywhere that lets `wddctl` do it. This
run continues in a fresh scratch repository, one task, `merge.surface:
local`, verification `python3 -m unittest discover -s tests` plus a second
smoke command, and an intake-recorded epic deliverable command:

```
$ wddctl next --repo .
{"actions": [{"action": "final_review", "task": "-",
  "recordWith": "wddctl finalize review record --reviewer NAME --findings '[]' --repo .",
  "judgment": "dispatch a reviewer against the whole epic branch diff, per wdd-review's final-review contract, checked against .wdd/spec.md"}]}
```

`final_review` dispatches per `wdd-review`'s final-review contract: review
the whole diff between the epic branch and the target branch, not one
task's diff, checked against the acceptance criteria rather than a task
brief. Same P1/P2/P3 vocabulary, same blocking severities:

```
$ wddctl finalize review record --reviewer codex-review --findings '[]' --repo .
{"duplicate": false, "headSha": "20bbd294...", "outcome": "passed", "revision": 13}
$ wddctl next --repo .
{"actions": [{"action": "final_verification", "task": "-",
  "recordWith": "wddctl finalize verify record --results '[...]' --repo .",
  "judgment": "run full verification against the current epic branch head and record the result"}]}
```

`final_verification`'s evidence is now a **list**, not one command: every
ratified global `verification.commands` entry, in order, plus the scope's
own deliverable command recorded at `intake design` — overall `status` is
`passed` only when every entry is:

```
$ wddctl finalize verify record --results '[{"command": "python3 -m unittest discover -s tests", "status": "passed"}, {"command": "python3 -c \"print(1)\"", "status": "passed"}, {"command": "python3 -c \"from src.greeting import greet; assert (\\\"Ivo\\\" in greet(\\\"Ivo\\\"))\"", "status": "passed"}]' --repo .
{"duplicate": false, "headSha": "20bbd294...", "revision": 14, "status": "passed"}
```

A clean review and a passed verification, both pinned to the current base
head, unlock `prepare_handoff`:

```
$ wddctl next --repo .
{"actions": [{"action": "prepare_handoff", "task": "-",
  "command": "wddctl finalize handoff --repo ."}]}
$ wddctl finalize handoff --repo .
{"duplicate": false, "headSha": "20bbd294...", "pr": null, "revision": 15,
 "targetBranch": "main",
 "instructions": "push wdd/finalize-demo to your remote (e.g. 'git push origin wdd/finalize-demo') and open a pull request into main yourself; wddctl does not perform this on the local surface. Once the human merge lands, run 'wddctl finalize delivered --by NAME --repo .' to record it."}
```

On the `local` surface, `handoff` only records that it happened and
returns instructions — the push and the PR are the operator's to run. On
the `pr` surface it pushes the base branch itself and opens the
epic→target PR via `gh`, the same way task-level `submit` opens one (see
[`docs/wddctl.md`](wddctl.md#the-finalize-phase) for a real transcript of
that path against the stub `gh` fixture). Either way, `next` now names the
one thing left:

```
$ wddctl next --repo .
{"actions": [{"action": "await_delivery", "task": "-",
  "recordWith": "wddctl finalize delivered --by NAME --repo .",
  "judgment": "wait for the human-owned final merge via the local handoff: push wdd/finalize-demo to your remote and open a pull request into main yourself; wddctl does not perform this on the local surface; once it lands, record it with recordWith so live Git can prove it happened"}]}
```

The merge itself is not a `wddctl` command — it's an ordinary `git merge`
(or clicking "Merge" on the handoff PR), performed by whoever owns the
target branch, entirely outside the controller's reach:

```
$ git merge --no-ff -q wdd/finalize-demo -m "merge scope SCOPE-finalize-demo into main"
$ wddctl finalize delivered --by ivo --repo .
{"by": "ivo", "duplicate": false, "headSha": "20bbd294...", "revision": 16, "targetBranch": "main"}
$ wddctl next --repo .
{"actions": [], "blockers": [], "phase": "delivered", "revision": 16, "scope": "SCOPE-finalize-demo"}
```

`finalize delivered` proves this the same way `merge --observed` proves a
human task merge (see "`merge.mode: human`" above): it fetches the target
branch best-effort and requires the base branch's head to be a real
ancestor of it — of either the freshly fetched `origin/<target>` or the
local `<target>`, whichever actually has the merge. Run it before the
merge happens and it refuses, naming exactly that: "the final merge has
not happened." A P1 against the acceptance criteria blocks the ladder
exactly like a task-level P1 blocks merge — `next` reports
`assign_final_fixes` instead of `final_verification`, with no `command` of
its own, because the fix is new commits on the base branch, and any new
commit re-stales the review and routes back to `final_review` on its own.

**Scope archive, and the ladder restarts.** `delivered` is no longer a dead
end. `wddctl scope archive` retires the completed scope's records (scope,
tasks, intake, finalize, plan approval) into `.wdd/archive/<scope-id>.json`
and resets state to post-ratification setup — governance stands, but the
intake ladder starts over for whatever comes next:

```
$ wddctl scope archive --repo .
{"archived": ".wdd/archive/SCOPE-finalize-demo.json", "duplicate": false,
 "revision": 17, "scope": "SCOPE-finalize-demo"}
$ wddctl next --repo .
{"actions": [{"action": "agree_spec", "task": "-",
  "recordWith": "wddctl intake spec --approved-by NAME",
  "judgment": "agree .wdd/spec.md with the user (goal, in/out of scope, numbered acceptance criteria) per the wdd-intake skill's spec stage, then record it"}],
 "blockers": [], "phase": "setup", "revision": 17, "scope": null}
```

Nothing scope-specific — the deliverable command included — carries
forward. One repository, successive scopes, each earning its own spec,
research, and design from scratch.

A scope's lifecycle sentence, end to end: a scope starts at `plan apply`
and ends at an **observed human merge** — `delivered` is never recorded on
trust, only on live Git proof that the epic branch actually landed in the
target branch.

## Appendix: a local runner, worked

Everything above dispatches workers and reviewers as subagents of the
controller's own harness. `wddctl` can also dispatch through a registered
**runner** — an external agent CLI, exec'd headless, file-in file-out.
`wdd-runners` owns setup judgment; this is one real registration and
dispatch, against the committed stub `tests/fixtures/fake-runner/fake-runner`
(a stub because it's a canned deterministic script, not a real agent —
labeled as such rather than passed off as a live CLI) so the transcript
needs nothing installed on your machine to reproduce. `docs/wddctl.md`'s
Runners section has the full config/resolution/`inputs_changed` reference;
this is the worked example.

A task's `plan.json` entry named the runner directly (`"model":
"stub-runner"`) and was started like any other task. Registration order is
law — probe the explicit candidate first, register it, then re-sign
governance:

```
$ wddctl dispatch --probe-command '["/path/to/fake-runner", "--prompt", "{prompt}", "--worktree", "{worktree}", "--logfile", "{logfile}"]'
{"digest": "sha256:9c962d10...", "exitCode": 0, "ok": true, "recorded": true,
 "tokenSeen": true, "wallMs": 71}
$ wddctl config set runners '{"stub-runner": {"command": ["/path/to/fake-runner", "--prompt", "{prompt}", "--worktree", "{worktree}", "--logfile", "{logfile}"]}}'
{"openQuestions": 0, "path": "runners", "value": {"stub-runner": {"command": [...]}}}
$ wddctl constitution amend --by ivo
{"decisionFingerprint": "sha256:f52af4be...", "duplicate": false, "revision": 9}
```

The probe records a digest of the exact command bytes; `dispatch --task`
refuses any runner whose ratified command lacks a passing probe on that
same digest — a runner that was never probed is configuration fiction, not
a shortcut. With registration and governance done, dispatching the worker
is one command:

```
$ wddctl dispatch --task TASK-001-placeholder --role worker --repo .
{"digest": "sha256:9c962d10...", "exitCode": 0,
 "log": ".../dispatch/TASK-001-placeholder-worker-1.log",
 "model": "stub-runner", "role": "worker", "statusToken": "DONE",
 "tail": "fake-runner: task complete\nDONE\n",
 "task": "TASK-001-placeholder", "timedOut": false, "wallMs": 70}
```

The log tail is right there in the result; the full file on disk holds the
rest. The `statusToken` is read straight from the trailing output line —
`DONE` continues the loop exactly like a subagent worker's own status line
would. The stub never touches Git, so the commit it's supposed to have
made still has to exist before `wddctl submit`, same discipline as any
worker, harness-native or not.

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
