# wddctl command reference

`wddctl` is a dependency-free Python controller for the mechanical half of
Wave-Driven Development. It owns state transitions, conflict-domain
enforcement, Git worktree management, evidence tracking, and merging. Skills
own judgment — what a task should contain, whether a diff is correct — and
call `wddctl` to record the outcome.

## Invocation

Any of these run the same code:

```sh
wddctl <command>
python3 -m wave_delivery <command>
python3 scripts/wddctl.py <command>          # run in place, no install
```

Every command accepts a global `--state` flag for the state file path
(default `.wdd/state.json`):

```sh
wddctl --state /path/to/.wdd/state.json status
```

## The loop

```sh
wddctl next                     # what needs doing, and the command for it
<run the action's `command`, or do the judgment work>
<run the action's `recordWith` to record the outcome>
```

Repeat until `next` reports no actions. A typical task with no separate
review requirement costs six commands: `start`, `submit`, `verify record`,
`freshness record`, `merge`, `release`. There is no hand-authored JSON, no
manual revision tracking, and no manual `git merge`.

## Optional concurrency flags

Every mutating command accepts `--expected-revision N` and
`--idempotency-key KEY`. Both are optional everywhere:

- Omit them and `wddctl` reads the current revision under the same exclusive
  lock that guards the write, and gives the event a unique id. It does *not*
  dedupe by payload: a payload-derived key cannot tell a retry from a
  legitimately repeated event, and doing so silently wedged scopes (an
  argument-free `reconcile done` became a one-shot per scope). Retries are
  safe by construction instead — every transition is either guarded by the
  status it requires or writes evidence by overwriting it.
- Pass `--idempotency-key` when you genuinely need at-most-once semantics;
  it is honoured exactly, and a second call with the same key is a no-op.
- Pass them when several controllers or processes share one scope and you
  need a hard conflict instead of a silent merge of intent — `wddctl` raises
  `RevisionConflict` if the state moved since you last read it.

## Commands

### `plan apply`

Create or update a scope from `plan.json`. Creates the scope's base branch if
it doesn't exist. Re-runnable: it diffs the new plan against the current
state and adds, removes, or updates tasks. Editing or removing a task that
has already left `todo` is refused.

```sh
wddctl plan apply --plan plan.json --repo . [--from-ref REF] [--dry-run]
```

- `--from-ref` — start point for a newly created base branch (default
  `HEAD`).
- `--dry-run` — compute and print the diff without writing state or creating
  a branch.

```sh
wddctl plan apply --plan plan.json --repo . --dry-run
# {"scope": "SCOPE-auth-refresh", "created": false, "diff": {"added": [...], ...}}
wddctl plan apply --plan plan.json --repo .
```

### `plan preview`

Project the order tasks would be admitted in, grouped into rounds. This is a
view for humans, not a gate — the real controller admits each task the
moment its own dependencies and conflict domains clear, without waiting for
a round to finish.

```sh
wddctl plan preview [--plan plan.json]
```

Without `--plan`, it projects from the current `state.json`. With `--plan`,
it projects a plan that hasn't been applied yet.

### `constitution probe` / `ratify` / `status`

Execution is blocked until the constitution is explicitly ratified — see
`plan.json`'s companion, `.wdd/constitution.md`, in
[`docs/artifact-schema.md`](artifact-schema.md).

```sh
wddctl constitution probe --root . --output .wdd/constitution-proposal.json
```

`probe` gathers repository evidence (instruction files, verification
commands it can detect, branch conventions) into a proposal JSON object with
a `decisionFingerprint` — a hash of its `decisions`. It never ratifies
anything by itself.

```sh
wddctl constitution ratify --by "jordan" --proposal .wdd/constitution-proposal.json
# or, if you already have a fingerprint from elsewhere:
wddctl constitution ratify --by "jordan" --decision-fingerprint sha256:...
```

`--proposal` and `--decision-fingerprint` are mutually exclusive; exactly one
is required.

`ratify` is the initial act only — running it against an already-ratified
scope fails. Every later governance change goes through `amend`, which takes
the same flags:

```sh
wddctl constitution amend --by "jordan" --proposal .wdd/constitution-proposal.json
```

`amend` requires a prior ratification, rejects a fingerprint identical to the
current one (nothing changed), and records the superseded fingerprint as
`constitution.ratification.amendedFrom` so governance history stays auditable.

```sh
wddctl constitution status [--proposal .wdd/constitution-proposal.json]
```

Reports the current ratification and, when a proposal is passed, whether it
has drifted from what was ratified (`"stale": true`).

### `next`

The action queue — read-only, and the thing to run every iteration of the
loop.

```sh
wddctl next [--max-bytes N] [--repo PATH]
```

`--max-bytes` (default 4096) bounds the output size for prompt budgets;
`wddctl` shrinks the action list until the rendered JSON fits and reports
`"truncated": true` if anything was cut. Commands are attached before the
budget is measured, so the limit stays honest. Output shape:

```json
{
  "scope": "SCOPE-auth-refresh",
  "revision": 7,
  "actions": [
    {
      "task": "TASK-002-refresh-route",
      "action": "start_task",
      "command": "wddctl start --task TASK-002-refresh-route --repo ."
    },
    {
      "task": "TASK-001-token-types",
      "action": "await_worker",
      "recordWith": "wddctl submit --task TASK-001-token-types --repo ."
    }
  ],
  "blockers": [{"task": "TASK-003-session-ui", "code": "dependencies", "dependsOn": ["TASK-001-token-types"]}],
  "truncated": false
}
```

Each action carries the literal command to use, so no caller has to translate
an action name into an invocation:

- **`command`** — run it as-is, right now. Emitted for `start_task`,
  `check_branch_freshness`, and `merge_task`.
- **`recordWith`** — the step needs judgment first (implement, review, run
  the tests). Do that, then run this to record the outcome. Emitted for
  `await_worker`, `run_review`, `run_verification`, `assign_fix_writer`, and
  `run_reconciliation`.

An action never carries both: either it is mechanical enough to run now, or
it is a piece of work whose result gets recorded afterwards. `--repo` (default
`.`) and a non-default `--state` are echoed into the emitted commands so they
stay copy-pasteable; the default `--state` is omitted for brevity.

A single pass never proposes two conflicting starts: admission is simulated
task by task within the same call.

### `status`

```sh
wddctl status [--json]
```

Without `--json`, a short human-readable brief (scope, revision,
constitution status, task counts, active task count, reconciliation due
flag). With `--json`, the full summary `next` and `render` are built from.

### `render`

```sh
wddctl render --output state.md
```

Writes a Markdown projection of the current state (active task gates, next
actions, blockers). Generated — never hand-edit it; re-run `render` instead.

### `start`

Admit a task, create its isolated worktree, and mark it in progress — one
step. Admission (dependencies done, conflict domains free, under
`maxConcurrent`) is enforced here as well as inside the `task.started`
transition, so bypassing `next` still can't start two colliding tasks.

```sh
wddctl start --task TASK-001-token-types --repo .
```

```json
{
  "task": "TASK-001-token-types",
  "action": "created",
  "branch": "task/TASK-001-token-types",
  "worktree": "/path/to/repo.wdd/worktrees/SCOPE-auth-refresh/TASK-001-token-types",
  "baseRef": "wdd/auth-refresh",
  "headSha": "...",
  "specPath": "tasks/TASK-001-token-types.md",
  "revision": 3
}
```

Implement the task in the printed `worktree` path. Worktrees live beside the
repository at `<repo>.wdd/worktrees/<scope>/<task>`, never inside its working
tree, and the location is derived rather than stored (see
[`artifact-schema.md`](artifact-schema.md)).

Running `start` against a task that is already `in_progress`, `review`, or
`merge_ready` **re-attaches** it instead of restarting it: the worktree is
recreated from the task's existing branch and the task keeps its status,
evidence, and history. `action` comes back as `reattach:<what git did>`. This
is the handoff path — clone a repository whose committed `state.json` shows
work in flight, and one `start` puts you back in it:

```sh
git fetch origin 'refs/heads/task/*:refs/heads/task/*'
wddctl start --task TASK-001-token-types --repo .   # -> reattach:attach_existing_branch
```

Re-attaching fails if the task's branch is not present in this repository,
telling you to fetch it first — the worktree can be recreated, the commits
cannot.

### `submit`

Record a task's deliverable. The head SHA is read from the task's branch —
you never pass it by hand.

```sh
wddctl submit --task TASK-001-token-types --repo . [--pr URL]
```

Refuses to submit a worktree with uncommitted changes, or a branch with no
commits of its own past the base. Without `--pr`, the recorded reference is
`branch:<name>@<short-sha>`. Re-submitting after new commits records
`task.head_updated` instead of `task.pr_recorded`, and invalidates any
existing review and verification evidence — see Guarantees below.

### `review record`

Record findings inline. This is the normal path — no file needed.

```sh
wddctl review record --task TASK-001-token-types --reviewer "codex-review" \
  --findings '[{"severity":"P1","summary":"missing null check","file":"src/a.py","line":42}]'
```

Omit `--findings` or pass `[]` for a clean review. See "Findings JSON shape"
below for the schema. Base and head SHAs are supplied by the controller, not
the caller.

### `review run`

Run a configured reviewer command against the frozen base/head SHAs and feed
its result straight into state.

```sh
wddctl review run --task TASK-001-token-types --repo . \
  --command-json '["./scripts/run-reviewer.sh"]' --output review-result.json
```

The command runs with `WDDCTL_REVIEW_TASK`, `WDDCTL_REVIEW_BASE_SHA`, and
`WDDCTL_REVIEW_HEAD_SHA` in its environment, and must print a
`wddctl_review_result` JSON object (below) on stdout. Requires the task to
currently be in the review gate and the constitution to be ratified.

### `review collect`

Aggregate one or more externally produced reviewer result files — the path
for a reviewer that runs outside `wddctl`'s control (a hosted review bot, a
human's notes saved to a file, a CI job).

```sh
wddctl review collect --task TASK-001-token-types \
  --result review-a.json --result review-b.json
```

Every file must be a `wddctl_review_result` envelope (below) with matching
`baseSha`/`headSha`; findings from all files are merged and reviewers are
joined with `", "`.

### `verify record`

Record a verification outcome directly — no file needed.

```sh
wddctl verify record --task TASK-001-token-types \
  --status passed --command "pytest -q"
```

`--status` is one of `passed`, `failed`, `unavailable`. Use `unavailable`
when there's no meaningful automated check, rather than skipping
verification silently.

### `verify collect`

Read one externally produced verification result file — the `verify`
equivalent of `review collect`.

```sh
wddctl verify collect --task TASK-001-token-types --result verify-result.json
```

Must be a `wddctl_verification_result` envelope (below).

### External result file envelopes

`review record` and `verify record` need no file at all — they take findings
or a status as arguments and the controller fills in the SHAs. The envelope
formats below matter only for `review collect` / `verify collect`, which read
externally produced files. These were previously undocumented, and a
malformed or missing envelope silently broke the merge path — a collected
result with the wrong SHA or wrong `kind` is now rejected outright with a
validation error rather than accepted.

`wddctl_review_result` (for `review collect --result FILE`):

```json
{
  "schemaVersion": 1,
  "kind": "wddctl_review_result",
  "task": "TASK-001-token-types",
  "baseSha": "8f2c1a0...",
  "headSha": "3d9e7b4...",
  "reviewer": "external-reviewer-name",
  "findings": [
    {"severity": "P1", "summary": "missing null check", "file": "src/a.py", "line": 42}
  ]
}
```

`wddctl_verification_result` (for `verify collect --result FILE`):

```json
{
  "schemaVersion": 1,
  "kind": "wddctl_verification_result",
  "task": "TASK-001-token-types",
  "baseSha": "8f2c1a0...",
  "headSha": "3d9e7b4...",
  "status": "passed",
  "command": "pytest -q"
}
```

Both require `schemaVersion: 1` and the exact `kind` string. `baseSha` and
`headSha` must be the task's actual base/head — a file with a stale or wrong
SHA is rejected, since evidence pinned to the wrong commit is worse than no
evidence. `task` is optional but, if present, must match the `--task` you
pass; a mismatch is rejected. Get `baseSha`/`headSha` for an external tool
from `wddctl status --json` or the output of `wddctl start` / `wddctl submit`.

### Findings JSON shape (for `review record --findings`)

```json
[{"severity": "P1", "summary": "...", "file": "src/a.py", "line": 42}]
```

`severity` must be `P1`, `P2`, or `P3`; `summary` is required; `file` and
`line` are optional. `P1` and `P2` findings block merge (`needs_fixes` gate,
see below); `P3` does not.

### `freshness check`

Classify a branch against a base without mutating any task state — useful
for a quick look before committing to `freshness record`.

```sh
wddctl freshness check --repo . --base wdd/auth-refresh --head task/TASK-001-token-types \
  [--conflict-domain "src/auth/**"]
```

Returns one of `current`, `nonmaterially_stale`, `materially_stale`,
`conflicted`, computed with `git merge-tree` plus changed-file overlap and
conflict-domain matching.

### `freshness record`

Classify a task's head against the scope's configured base ref and record
the result — required before merge.

```sh
wddctl freshness record --task TASK-001-token-types --repo .
```

### `refresh`

Merge the scope base into a task branch and re-record its head. Use this
when `freshness record` reports `materially_stale` or `conflicted`.

```sh
wddctl refresh --task TASK-001-token-types --repo .
```

Refuses to run against a worktree with uncommitted changes. On a real
conflict, aborts the merge and reports which files conflicted — resolve them
directly in the task worktree, then re-run. On success, the new head
invalidates the task's review and verification evidence (see Guarantees) —
that's deliberate, since a new commit means the old evidence no longer
proves anything.

### `merge`

Performs the merge into the scope base itself, then records it — only after
proving it Git-verified.

```sh
wddctl merge --task TASK-001-token-types --repo .
```

Requires the task to be at the `merge_ready` gate. Re-checks freshness live
before merging and refuses if it's `materially_stale` or `conflicted`
(pointing you at `refresh`). Merges inside an integration worktree (or the
controller checkout itself, if it's already on the base branch with no
uncommitted changes), then verifies with `git merge-base --is-ancestor` that
the task head actually landed in the base before recording `task.merged`.
Recording without that live proof is not possible — see Guarantees.

### `release`

Remove a finished task's worktree.

```sh
wddctl release --task TASK-001-token-types --repo . [--keep-worktree]
```

Only valid once a task is `done`, `cancelled`, or `blocked`. Refuses to
remove a worktree that's dirty, detached, or checked out to an unexpected
branch. `--keep-worktree` releases the lease bookkeeping without deleting
the directory.

### `block` / `unblock` / `cancel`

```sh
wddctl block --task TASK-001-token-types --reason "waiting on design sign-off"
wddctl unblock --task TASK-001-token-types
wddctl cancel --task TASK-001-token-types
```

`unblock` returns the task to `todo` (if it never got a PR) or `in_progress`
(if it did). `cancel` is terminal.

### `note`

Queue a durable discovery for the next reconciliation checkpoint. Filing a
note makes reconciliation due immediately.

```sh
wddctl note --note "auth middleware now validates issuer, not just signature" [--task TASK-001-token-types]
```

### `reconcile status` / `reconcile done`

```sh
wddctl reconcile status
wddctl reconcile done
```

A checkpoint becomes due after `reconcileEveryNMerges` merges since the last
one, or when any note is pending. `next` surfaces this as a
`run_reconciliation` action. `reconcile done` clears the merge counter and
pending notes and stamps the checkpoint time.

### `migrate`

Convert schema-v2 controller state (produced by `wddctl init` on older
revisions) to the current schema. Dry-run first; `--apply` writes a
`.v2.bak` backup beside the state file before converting.

```sh
wddctl --state .wdd/state.json migrate --dry-run
wddctl --state .wdd/state.json migrate --apply
```

`--state` is a global option, so it goes before the subcommand.

Waves are dropped (scheduling is derived from dependencies and conflict
domains), every task defaults to `risk: normal`, and recorded worktree paths
are cleared because the location is derived per checkout. `reviewPolicy`
becomes `always`, because schema v2 required review for every task —
migrating must not silently drop that obligation. Pass
`--review-policy risk_based` to loosen it deliberately. Reading v2 state
without migrating fails with a message pointing here.

### `monitor`

One cheap, zero-LLM Git observation tick — branch/worktree state, nothing
else. Only writes state when observations actually changed.

```sh
wddctl monitor --once --repo . [--dry-run]
```

### `event apply`

Escape hatch: apply one raw state transition directly. Every other command
is a thin wrapper over this; use it only when nothing else fits.

```sh
wddctl event apply --event task.blocked --task TASK-001-token-types --data '{"reason":"..."}'
```

### `doctor`

Optional capability report: Python version, and whether `git`, `gh`, `acli`,
`codex`, `claude` are on `PATH`. The core controller works with none of the
optional ones present.

```sh
wddctl doctor [--json]
```

## Gates (what `next` emits per task)

A task moves through gates in this order as evidence accumulates:

```
not_started
  -> await_worker            (no PR recorded yet: no_pr)
  -> run_review               (only when review is required: needs_review / reviewing)
  -> run_verification          (needs_verification)
  -> assign_fix_writer          (unresolved P1/P2 findings: needs_fixes)
  -> check_branch_freshness      (needs_freshness)
  -> merge_task                   (merge_ready)
  -> done
```

Review is required when `scope.reviewPolicy` is `always`, or when it's
`risk_based` and the task's `risk` is `high`; under `none`, or `risk_based`
with `risk: normal`, review is skipped and `submit` goes straight to
`in_progress` awaiting verification.

Evidence is pinned to the task's head SHA. Any new commit (a `refresh`, or a
re-`submit`) invalidates both review and verification — the task falls back
to `needs_review` / `needs_verification` even if it passed before. This is
deliberate: evidence about a commit that no longer exists proves nothing
about the commit that replaced it.

## Guarantees

- **Atomic, revisioned writes.** State is written to a same-directory
  temporary file, `fsync`'d, and atomically replaced (`os.replace`), with the
  containing directory `fsync`'d too. A crash mid-write never leaves a
  half-written `state.json`.
- **Optional optimistic concurrency.** Every mutation takes an exclusive
  local file lock, reads the current revision under that lock, and records
  an idempotency key (explicit or derived) before writing — see "Optional
  concurrency flags" above.
- **External evidence is verified, not trusted.** `review collect` and
  `verify collect` require `--repo` and check that the envelope's `baseSha`
  is a real commit and an actual ancestor of the `headSha` it claims to
  describe. A result naming a nonexistent base is refused.
- **Conflict domains overlap semantically.** `src/auth/**` blocks
  `src/auth/token.py`; the comparison is not string equality. Where overlap
  is undecidable the answer is "they overlap", because over-blocking costs
  parallelism and under-blocking costs a silently lost diff.
- **Evidence pinned to head SHA.** Review and verification results carry the
  exact `baseSha`/`headSha` they were produced against; the transition layer
  rejects evidence that doesn't match the task's current head.
- **Git-verified merge.** `wddctl merge` never records a merge on trust — it
  performs the merge itself, then calls `git merge-base --is-ancestor` to
  prove the task head actually landed in the base before writing
  `task.merged`. There is no way to record a merge without live proof.
- **Enforced conflict-domain exclusion.** Two tasks whose `conflictDomains`
  overlap can never both be active — this is checked inside the
  `task.started` transition itself (`admission_blocker`), not only
  advertised by `next`. A caller that starts a task directly, bypassing
  `next`, gets the same refusal.
