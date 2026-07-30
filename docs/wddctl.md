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
- When both are passed, the key wins: a retry carrying the key *and* the
  now-stale revision it was first issued with returns `"duplicate": true`
  rather than a `RevisionConflict`. That is the whole point of the key, and
  it matters most on `merge` and `refresh`, where the first attempt may
  already have moved Git.

The state lock records the holding pid and host. A lock whose pid is no
longer running on this host is reclaimed automatically, so a crash inside
the lock does not freeze the scope; a live holder is never stolen from, and
releasing only ever removes the lock this process actually took.

## Commands

### `init`

Scaffold a fresh `.wdd/` directory: `config.json` (machine-consumed knobs,
seeded with probed defaults), `constitution.md` (a prose draft — see
[`docs/artifact-schema.md`](artifact-schema.md)), empty `tasks/` and
`shared-context/` directories, and a pre-scope `state.json`. This replaces
what used to be prose-improvised setup — everything mechanical now exists
before an agent writes a line of `plan.json`.

```sh
wddctl init --repo .
```

```json
{
  "alreadyInitialized": false,
  "created": [
    ".wdd/config.json",
    ".wdd/constitution.md",
    ".wdd/tasks",
    ".wdd/shared-context",
    ".wdd/state.json"
  ],
  "hint": "run 'wddctl next' and follow it",
  "openQuestions": [
    {
      "options": [
        "pr",
        "local"
      ],
      "path": "merge.surface",
      "question": "Should each task ship as a real GitHub pull request, or stay fully local? Pull requests give you the familiar review surface (branches pushed, findings mirrored as PR comments); local keeps the whole loop offline with no pushes — good for solo or offline work."
    },
    {
      "path": "models",
      "question": "Which models should do the work? Three roles matter: everyday implementation, a stronger model for high-risk tasks, and review (usually your strongest — it guards the merges). Name models your agent harness understands, or say the harness defaults are fine."
    },
    {
      "path": "verification.commands",
      "question": "I couldn't detect a test or verification command in this repository. What command should prove a change works — for example 'npm test' or 'pytest -q'? If nothing runnable exists yet, say so and verification will be recorded as unavailable with your justification."
    }
  ]
}
```

`init` probes the repository the same way `constitution probe` does (see
below) to seed `config.json`'s `branching.targetBranch` and
`verification.commands`, and turns anything it couldn't determine — the
merge surface is always asked; a verification command only if none of the
usual markers (`tests/`, `package.json`, the worktree-cleanup test script)
were found — into `config.json`'s `openQuestions` array. That array is the
setup-phase contract: `wddctl next` refuses to advance past
`resolve_config` while it is non-empty, and `constitution ratify` refuses
outright while any question remains (see below).

Idempotent: run it again once `state.json` exists and nothing is touched —

```json
{
  "alreadyInitialized": true,
  "created": [],
  "hint": "state exists; run 'wddctl next' for what to do",
  "openQuestions": []
}
```

— so `init` is safe to run defensively at the start of a session without
risking a silent reset of already-answered questions or an already-ratified
constitution. If `config.json` already exists but `state.json` doesn't (a
partially-completed init, or a repo migrated with `migrate --governance`),
`init` reuses the existing `config.json` instead of overwriting it.

During setup, `wddctl status` and `wddctl next` report a reduced
setup-phase shape (`"phase": "setup"`, no `scope`) instead of the normal
summary — there is no scope yet for the normal shape to describe.

### `plan apply`

Create or update a scope from `plan.json`. Creates the scope's base branch if
it doesn't exist. Re-runnable: it diffs the new plan against the current
state and adds, removes, or updates tasks. Editing or removing a task that
has already left `todo` is refused.

```sh
wddctl plan apply --plan plan.json --repo . [--from-ref REF] [--dry-run] [--approved-by NAME]
```

- `--from-ref` — start point for a newly created base branch (default
  `HEAD`).
- `--dry-run` — compute and print the diff without writing state or creating
  a branch.
- `--approved-by NAME` — record approval: stamps `{"by": NAME, "at": <utc_now>}` into `scope.approval`. Re-apply without this flag preserves the last recorded approval (via `scope.approval` in the written state).

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

Note: unlike `plan lint` and `plan apply`, `preview --plan` reads the plan
file as-is — it does not overlay `config.json` defaults or risk rules, so its
projection can differ from what actually lands after a real apply.

### `plan lint`

Deterministic, advisory-only plan-quality checks — every one of them exists
because an agent-authored plan exhibited the failure in the wild. Lint sees
exactly what `plan apply` would see: the plan file overlaid with `config.json`
defaults and risk rules (see riskRules below), so a finding here is a finding
apply would also see.

```sh
wddctl plan lint --plan plan.json [--strict]
```

```json
{
  "findings": [
    {
      "code": "serialized_plan",
      "severity": "warning",
      "message": "4 tasks admit in 4 rounds — the plan is effectively serialized. Check dependsOn fan-out, conflictDomains overlap, and whether scope.maxConcurrent (currently 4) is the limiter."
    },
    {
      "code": "enumerated_domains",
      "severity": "warning",
      "task": "TASK-003-utils-helpers",
      "message": "TASK-003-utils-helpers lists 4 individual files under src/utils/ — consider the glob src/utils/** unless another task must write there concurrently."
    },
    {
      "code": "missing_brief",
      "severity": "warning",
      "task": "TASK-004-session-ui",
      "message": "TASK-004-session-ui: brief tasks/TASK-004-session-ui.md is effectively empty — a worker dispatched on it will improvise."
    }
  ],
  "strict": false
}
```

Codes:

| code | trigger |
| --- | --- |
| `serialized_plan` | 3+ tasks admit one-per-round (or ≥75% of tasks do, for 4+ tasks) — dependencies or conflict domains have accidentally chained the whole plan into a queue. |
| `uniform_risk` | 4+ tasks and every one shares the same `risk` — under `risk_based` review this means either "review everything" (`high`) or "review nothing" (`normal`); confirm that's intended. |
| `enumerated_domains` | a task lists 4+ individual files (no wildcard) under the same directory — a candidate for the `dir/**` glob instead, unless another task genuinely needs to write there concurrently (repo-root, top-level files are exempt from this grouping). |
| `coarse_domain` | a single domain on one task overlaps 3+ other tasks' domains — it will serialize all of them; narrow it to what the task actually writes. |
| `missing_brief` | a task's `specPath` file doesn't exist, or has fewer than 2 non-blank lines — a worker dispatched on it will improvise. |
| `missing_spec` | `.wdd/spec.md` is missing or effectively empty — the finalize phase reviews the epic branch against it; run the intake first. |
| `nonprose_brief` | a task's brief starts with `{` or `[` — it reads as JSON/data, not the Markdown prose (objective, scope, verification) a worker needs. |

Every finding is `"severity": "warning"` — lint never blocks by default. Pass
`--strict` to turn any finding into a refusal (exit 2), naming the offending
codes:

```sh
wddctl plan lint --plan plan-bad.json --strict
# wddctl: plan lint --strict: enumerated_domains, missing_brief, serialized_plan
```

`plan apply` runs the same checks automatically on every call — the result
carries a `"lint"` array (empty when clean) alongside the usual diff, and
`plan apply --strict` refuses on the same terms `plan lint --strict` does,
before anything is written. Combined with `--dry-run`, `--strict` still
refuses (exit 2) on any finding — the refusal happens before the dry-run diff
is computed or printed, so a strict dry run never shows you the diff it would
have applied:

```sh
wddctl plan apply --plan plan-bad.json --repo . --strict
# wddctl: plan apply --strict: enumerated_domains, missing_brief, serialized_plan
wddctl plan apply --plan plan-good.json --repo .
# {"scope": "SCOPE-auth-refresh", "created": false, "diff": {...}, "lint": [], ...}
```

Lint is advisory everywhere lint runs, on `plan apply` included: nothing in
this check changes admission or merge semantics, only what gets printed.

### `config`

Read or write `.wdd/config.json` — every knob `wddctl` (or a dispatching
agent) mechanically consumes: branching conventions, verification commands,
review policy, merge surface/mode, concurrency, model aliases, risk rules,
task provider. The constitution stays prose for humans; this is its
machine-readable counterpart, created by `wddctl init` (see above).

```sh
wddctl config get merge.surface
# "pr"
wddctl config get review.policy
# "risk_based"
```

`get` takes a dotted path and prints the value as JSON. An unknown path
(anything not present in the schema — see `wave_delivery/config.py`'s
`DEFAULT_CONFIG`) is a validation error, not a silent `null`.

```sh
wddctl config set merge.surface pr
```

```json
{
  "openQuestions": 2,
  "path": "merge.surface",
  "value": "pr"
}
```

```sh
wddctl config set verification.commands '["python3 -m unittest"]'
```

```json
{
  "openQuestions": 1,
  "path": "verification.commands",
  "value": [
    "python3 -m unittest"
  ]
}
```

```sh
wddctl config set models '{"planning": null, "implementation": {"default": null, "highRisk": null}, "review": null}'
```

```json
{
  "openQuestions": 0,
  "path": "models",
  "value": {
    "implementation": {
      "default": null,
      "highRisk": null
    },
    "planning": null,
    "review": null
  }
}
```

`set` takes a dotted path and a value. The value is parsed as JSON first
(so `'["python3 -m unittest"]'`, `3`, or `true` work as you'd expect); if
that fails, it falls back to the literal string, which is what lets you
write `pr` instead of `'"pr"'` for a plain string field. The full config is
re-validated after the write, so setting `merge.surface` to anything other
than `pr` or `local` is refused.

Setting a path that appears in `config.json`'s `openQuestions` array
resolves that question — it's removed from the list as a side effect of the
write, which is how `set` doubles as the answer mechanism for the
open-questions contract `init` establishes. The response echoes the
remaining count so a caller resolving several questions in one pass can see
its own progress without a separate `get`.

`riskRules` — a list of `{"pattern": "<conflict-domain glob>", "risk":
"high"}` objects — is how a scope declares "these paths are always high risk"
once, instead of every plan re-deciding it per task:

```sh
wddctl config set riskRules '[{"pattern": "src/auth/**", "risk": "high"}]'
```

At `plan apply` (and `plan lint`, which overlays the same config so it sees
what apply would see), every task's `risk` is checked against the high
patterns: if any of the task's `conflictDomains` overlaps a pattern (the same
semantic overlap `domains.py` uses for admission, not string equality), the
task's risk becomes `high` regardless of what the plan file said. This is
strictly upward — a plan can request `high` risk that no rule matches and
keep it, but nothing in a rule ever downgrades a task the plan already marked
`high`. Concretely, with the rule above, a plan declaring all four tasks
`"risk": "normal"` came out of `plan apply` as:

```
TASK-001-token-types  -> high    # conflictDomains: ["src/auth/types.py"]
TASK-002-refresh-route -> high   # conflictDomains: ["src/auth/routes/refresh.py"]
TASK-003-utils-helpers -> normal # conflictDomains: ["src/utils/**"]
TASK-004-session-ui    -> high   # conflictDomains: ["src/auth/session.py"]
```

only `TASK-003` fell outside `src/auth/**` and kept the plan's declared
`normal`. An empty `riskRules` list (the default) is a no-op: risk stays
exactly what the plan file says.

```sh
wddctl config show
```

Prints the whole config object. Useful for showing a user everything before
`constitution ratify`, since ratifying signs this file's exact contents (see
below).

### `constitution probe` / `ratify` / `amend` / `status`

Execution is blocked until the constitution is explicitly ratified — see
`.wdd/config.json`'s prose companion, `constitution.md`, in
[`docs/artifact-schema.md`](artifact-schema.md). Ratifying signs both files
at once: the fingerprint is computed over `config.json`'s exact contents
*and* `constitution.md`'s exact text (`governance_fingerprint` in
`wave_delivery/config.py`). Editing either one after ratification is drift,
not a free edit — see "Governance drift" below.

```sh
wddctl constitution probe --root . --output .wdd/constitution-proposal.json
```

`probe` gathers repository evidence (instruction files, verification
commands it can detect, branch conventions) into a proposal JSON object with
its own `decisionFingerprint` — a hash of its `decisions`, independent of the
config/constitution fingerprint above. It never ratifies anything by
itself, and `init` already runs the same probe internally to seed
`config.json`'s defaults — running it by hand is mostly useful for
inspecting repository evidence, or for the deprecated `--proposal` pin
below.

Ratifying now needs only `--by`:

```sh
wddctl constitution ratify --by "ivo"
```

```json
{
  "decisionFingerprint": "sha256:0db6456b2f70f164e5408ce36db0aca6745477b82222eea21d443f6bc5377775",
  "duplicate": false,
  "revision": 1
}
```

Ratification refuses outright while `config.json` still has open questions
— tried against a fresh `init` before either question was answered:

```
wddctl: cannot ratify: 2 open config question(s) remain (merge.surface, verification.commands); resolve them with 'wddctl config set' first
```

`--decision-fingerprint` and `--proposal` still exist, mutually exclusive,
both optional. They matter for two different cases:

- A **legacy repository** with no `config.json` (predates `wddctl init`, or
  not yet migrated — see `migrate --governance`) has nothing to fingerprint
  automatically, so one of the two is required there, exactly as before.
- A **current repository** (`config.json` present) computes its fingerprint
  live from the files themselves; passing `--decision-fingerprint` there is
  accepted only as an assertion — it must match the live value or ratify
  refuses, on the theory that a caller pinning a fingerprint should get
  caught if reality moved out from under it. `--proposal` is **deprecated**
  in this case: it no longer contributes to the fingerprint at all, and the
  response carries a `"warning"` field saying so instead of silently doing
  nothing:

```sh
wddctl constitution amend --by "ivo" --proposal .wdd/constitution-proposal.json
```

```json
{
  "decisionFingerprint": "sha256:694622c3be36ad891308ea9735368b48bc5494878e360cb5ffeb0b90deda0017",
  "duplicate": false,
  "revision": 4,
  "warning": "--proposal is deprecated; the fingerprint now covers .wdd/config.json + constitution.md"
}
```

`ratify` is the initial act only — running it against an already-ratified
scope fails (`"constitution is already ratified; use 'wddctl constitution
amend' to change it"`). Every later governance change goes through `amend`,
which takes the same flags, requires a prior ratification, rejects a
fingerprint identical to the current one (nothing changed — `"amendment
fingerprint matches the ratified one; nothing changed"`), and records the
superseded fingerprint as `constitution.ratification.amendedFrom` so
governance history stays auditable.

```sh
wddctl constitution status
```

```json
{
  "ratification": {
    "at": "2026-07-28T14:39:50Z",
    "by": "ivo",
    "decisionFingerprint": "sha256:0db6456b2f70f164e5408ce36db0aca6745477b82222eea21d443f6bc5377775"
  },
  "stale": null,
  "status": "ratified"
}
```

`status --proposal FILE` additionally reports `"stale": true/false` against
that specific proposal file's fingerprint — this is the older, explicit
comparison and is independent of the live `config.json` + `constitution.md`
drift check below; pass a proposal only when you specifically want to know
whether a saved proposal file still matches what's ratified.

#### Governance drift

Once ratified, editing `config.json` or `constitution.md` without an `amend`
is drift, not a free edit. `wddctl next` detects it live (no proposal file
needed) and clears every action until it's resolved:

```sh
wddctl next --repo .
```

```json
{
  "actions": [],
  "blockers": [
    {
      "actual": "sha256:c01bf13c5ac11f4635b0119cecf419ebe5681a6e04a2e78874f66df85284ca04",
      "code": "governance_drift",
      "message": "config/constitution changed since ratification; amend before executing",
      "ratified": "sha256:0db6456b2f70f164e5408ce36db0aca6745477b82222eea21d443f6bc5377775"
    }
  ],
  "revision": 2,
  "scope": "SCOPE-demo",
  "truncated": false
}
```

The refusal isn't only advisory in `next` — every execution verb that
mutates task state (`start`, `submit`, `refresh`, `merge`, `review record`,
`review collect`, `verify record`, `verify collect`, `reconcile done`)
checks live governance before doing anything else, so bypassing `next`
doesn't bypass the gate:

```sh
wddctl start --task TASK-001-demo --repo .
```

```
wddctl: governance drift: config.json or constitution.md changed since ratification (ratified sha256:0db6456b2f70f164e5408ce36db0aca6745477b82222eea21d443f6bc5377775, current sha256:c01bf13c5ac11f4635b0119cecf419ebe5681a6e04a2e78874f66df85284ca04); run 'wddctl constitution amend --by NAME' after the user re-approves
```

`amend` clears it — the newly live fingerprint becomes the ratified one, and
`next` resumes normal operation:

```sh
wddctl constitution amend --by "ivo"
```

```json
{
  "decisionFingerprint": "sha256:c01bf13c5ac11f4635b0119cecf419ebe5681a6e04a2e78874f66df85284ca04",
  "duplicate": false,
  "revision": 3
}
```

```sh
wddctl next --repo .
```

```json
{
  "actions": [
    {
      "action": "start_task",
      "command": "wddctl start --task TASK-001-demo --repo .",
      "task": "TASK-001-demo"
    }
  ],
  "blockers": [],
  "revision": 3,
  "scope": "SCOPE-demo",
  "truncated": false
}
```

Read-only verbs (`status`, `next` itself, `render`, `freshness check`,
`doctor`, `monitor`), setup verbs (`init`, `config`, `plan`, `migrate`),
task-state verbs that don't execute anything (`block`, `unblock`, `cancel`,
`note`), `constitution` itself, and `release` are deliberately exempt —
they either don't act on ratified governance, or are how governance gets
re-signed in the first place. `release` in particular is cleanup of an
already-finished task: it runs after merge evidence has been recorded, so
there is nothing left for a drift check to protect. `event apply` is
governed, not exempt: it is a raw state transition, and the escape hatch
bypasses transitions, not governance.

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

When `.wdd/config.json` has a non-empty `models` object, `start_task` and
`assign_fix_writer` actions additionally carry a **`"model"`** key: the
task's derived risk selects `models.implementation.highRisk` (task `risk`
is `high`) or `models.implementation.default` (otherwise). `run_review`
carries `models.review`. The key is present only when the resolved value is
a real, non-empty string — a `models` object with a null or missing field
for that action produces no key at all, so a caller can safely check for
the key's presence rather than parsing a placeholder. No other action gains
a model. This is decoration only: `wddctl` never chooses a model for you,
it just routes the one already configured to the action that needs it —
see [`skills/wdd-run/SKILL.md`](../skills/wdd-run/SKILL.md) for how the
controller skill is expected to use it.

```json
{
  "action": "start_task",
  "command": "wddctl start --task T2 --repo .",
  "model": "claude-opus-4.1",
  "task": "T2"
}
```

(`T2` above is `risk: high`, so it drew `models.implementation.highRisk`;
a `risk: normal` task in the same scope draws `models.implementation.default`
instead.) See "Merge surfaces and modes" below for the `merge.mode: human`
decoration (`await_human_merge`), which also lives inside `next`'s action
list.

### `status`

```sh
wddctl status [--json]
```

Without `--json`, a short human-readable brief (scope, revision,
constitution status, task counts, active task count, reconciliation due
flag). With `--json`, the full summary `next` and `render` are built from.

During setup — before `plan apply` has created a scope — both forms print
the reduced setup shape instead:

```json
{
  "constitution": "draft",
  "openQuestions": 2,
  "phase": "setup",
  "scope": null
}
```

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
Re-submitting with *no* new commits is a no-op (`"action":
"already_recorded"`): it burns no revision and keeps the evidence, so an
innocent retry cannot demote a `merge_ready` task.

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
the directory. A worktree that Git no longer knows about *and* is absent
from disk — what a crash between `git worktree remove` and the state write
leaves behind — is recorded as `"cleanup": "already_removed"` rather than
refused, so release always converges.

### `block` / `unblock` / `cancel`

```sh
wddctl block --task TASK-001-token-types --reason "waiting on design sign-off"
wddctl unblock --task TASK-001-token-types
wddctl cancel --task TASK-001-token-types
```

`unblock` returns the task to `todo` (if it never got a PR) or `in_progress`
(if it did). Because a blocked task releases its conflict domains, the
`in_progress` path re-runs admission and refuses if a rival took the domain
while this task waited — cancel or finish the rival first. `cancel` is
terminal.

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

A separate `--governance` flag converts a pre-split repository (a
`constitution.md` with no `config.json` sibling, from before the fingerprint
covered both files) into the current split:

```sh
wddctl --state .wdd/state.json migrate --governance --dry-run
# {"wddDir": ".wdd", "wouldMigrate": true}
wddctl --state .wdd/state.json migrate --governance --apply
```

```json
{
  "backup": ".wdd/constitution.md.pre-config",
  "migrated": true,
  "modelsExtracted": true,
  "ratificationInvalidated": false
}
```

The old `constitution.md` is preserved at `constitution.md.pre-config` (only
when it had content), any `models` object found in a legacy JSON code block
is lifted into `config.json`'s `models`, and the constitution file itself is
replaced with the current prose template. If the scope was already
ratified, `ratificationInvalidated` comes back `true` and the constitution
reverts to `draft` — the fingerprint's meaning changed, so the user has to
see and approve the new split once via `wddctl constitution ratify` before
execution resumes. A no-op if `config.json` already exists
(`{"migrated": false, "reason": "config.json already exists"}`).

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
optional ones present. Also reports governance health —
`governance.configPresent`, `governance.configValid` (with an `error` string
on failure), and `governance.drift` (`null`, or the same shape `next` emits
in its `governance_drift` blocker) — computed from `.wdd/config.json` and
the current state if one exists. Doctor only reports; it never refuses.

```sh
wddctl doctor [--json]
```

## Merge surfaces and modes

Two independent knobs control where a task's review/merge lives and who
performs the merge: `merge.surface` (`"pr"` or `"local"`) and `merge.mode`
(`"controller"` or `"human"`). Both live under `.wdd/config.json`'s `merge`
object (`wddctl config get merge.surface` / `wddctl config get merge.mode`),
and both can be overridden per scope from `plan.json`'s `scope.mergeSurface`
/ `scope.mergeMode` — a scope override always wins over the config default.
`wave_delivery/config.py`'s `merge_settings(state, config)` resolves the
effective pair everywhere in the codebase; a legacy repo with no
`config.json` at all defaults to `{"surface": "local", "mode": "controller"}`
so pre-existing behavior never changes underneath it.

### The configuration matrix

| surface | mode | `submit` | `review record` | `merge` |
| --- | --- | --- | --- | --- |
| `local` | `controller` | Records `branch:<name>@<sha>` — no network. | Recorded in `state.json` only. | `wddctl merge` performs the merge itself. |
| `pr` | `controller` | Pushes the task branch, runs `gh pr create`, records the returned URL. | Recorded in state, then mirrored as one `gh pr comment` on the PR. | `wddctl merge` merges locally, then pushes the advanced base ref to `origin` so GitHub shows the PR merged. |
| `pr` | `human` | Same as `pr`/`controller`: pushes and opens the PR. | Same as `pr`/`controller`: state first, PR comment mirrored. | `wddctl merge` refuses outright — the PR's human owner must merge it directly. `wddctl merge --observed` is the only way to record it (see below). |

(`local`/`human` is legal too — `merge.mode` and `merge.surface` are
orthogonal — but with no PR to point at, the `judgment` message in `next`
falls back to naming the task's branch instead of a URL; see
`engine.human_reference`.)

Every mirroring step (PR creation, PR comments) degrades to a `"warning"`
in the command's JSON result rather than losing the underlying state
transition — `state.json` is always the source of truth, the PR is only a
projection of it. Push failures are the one exception: a `submit` whose
`git push` fails aborts before any state change (the state must never claim
a submission that never left the machine), but once the push has succeeded,
a subsequent `gh pr create` failure still records the submission using the
same `branch:<name>@<sha>` fallback `local` surface uses, with the failure
surfaced as a `"warning"` instead of losing the branch that's already on
`origin`:

```json
{
  "branch": "task/T1",
  "duplicate": false,
  "event": "task.pr_recorded",
  "headSha": "8a6ccd619f5787d2f6b9a6377a6b5c4c9d90e2d0",
  "pr": "branch:task/T1@8a6ccd619f57",
  "revision": 4,
  "status": "in_progress",
  "task": "T1",
  "warning": "PR creation failed after push: gh pr create --head task/T1 --base wdd/demo2 --title Add a greeting helper --body wdd task T1\nspec: tasks/T1.md\nhead: 8a6ccd619f5787d2f6b9a6377a6b5c4c9d90e2d0 failed: fake-gh: forced failure (FAKE_GH_FAIL=1)"
}
```

(Captured with `FAKE_GH_FAIL=1` against the stub `gh` fixture described
below — the push to the bare `origin` remote had already landed
`task/T1` before `gh pr create` was made to fail.)

### Transcripts: stub `gh` + bare origin remote

Everything below is real output from a scratch repository, not
hand-written JSON. Nothing here talks to a real GitHub: `PATH` was
prepended with `tests/fixtures/fake-gh` (a fake `gh` executable that logs
its argv to `$FAKE_GH_LOG` and prints a canned PR URL for `pr create`), and
the scratch repo's `origin` remote is a local bare repository
(`git init --bare`), not a hosted one. This is the same test double the
test suite uses (`tests/test_execution_surfaces.py`), and it is the only
honest way to show `pr`-surface output without a live network call — no
part of these transcripts reached the real GitHub.

Setup:

```sh
export PATH="<repo>/tests/fixtures/fake-gh:$PATH"
export FAKE_GH_LOG=/path/to/gh.log
git init --bare origin.git            # stands in for a GitHub remote
git remote add origin ../origin.git
wddctl config set merge.surface pr
wddctl config set merge.mode controller
```

`pr`/`controller` submit — push, then `gh pr create`:

```sh
$ wddctl submit --task T1 --repo .
{
  "branch": "task/T1",
  "duplicate": false,
  "event": "task.pr_recorded",
  "headSha": "5d8ccd5f8bfdc0211a1ddbde6876b67228962cdd",
  "pr": "https://github.invalid/pr/1",
  "revision": 4,
  "status": "in_progress",
  "task": "T1"
}
$ cat "$FAKE_GH_LOG"
["pr", "create", "--head", "task/T1", "--base", "wdd/demo", "--title", "Add a greeting helper", "--body", "wdd task T1\nspec: tasks/T1.md\nhead: 5d8ccd5f8bfdc0211a1ddbde6876b67228962cdd"]
```

`pr`/`controller` merge — merges locally, then pushes the advanced base so
the bare `origin`'s base ref (what a real GitHub-hosted branch would show)
matches the local one:

```sh
$ wddctl merge --task T1 --repo .
{
  "action": "merged",
  "baseRef": "wdd/demo",
  "baseSha": "4b2bac01e6fe248f3149514fbb601b8f2ba91b50",
  "branch": "task/T1",
  "duplicate": false,
  "headSha": "5d8ccd5f8bfdc0211a1ddbde6876b67228962cdd",
  "revision": 7,
  "task": "T1"
}
$ git --git-dir=../origin.git rev-parse wdd/demo
4b2bac01e6fe248f3149514fbb601b8f2ba91b50   # matches local wdd/demo exactly
```

Review mirroring — `review record` on a task with a real PR posts one `gh
pr comment` (a markdown findings table) after state is recorded:

```sh
$ wddctl review record --task T2 --reviewer codex-review \
    --findings '[{"severity":"P2","summary":"hardcoded secret should come from config","file":"auth.py","line":2}]'
{
  "duplicate": false,
  "outcome": "blocking",
  "revision": 11,
  "status": "in_progress"
}
$ cat "$FAKE_GH_LOG"
["pr", "comment", "https://github.invalid/pr/1", "--body", "wddctl review by codex-review:\n\n| Severity | Summary | File | Line |\n| --- | --- | --- | --- |\n| P2 | hardcoded secret should come from config | auth.py | 2 |"]
```

A comment failure (`FAKE_GH_FAIL=1`) never loses the review — it degrades
to a `"warning"`, and the recorded outcome (`passed`, here) stands:

```sh
$ FAKE_GH_FAIL=1 wddctl review record --task T2 --reviewer codex-review --findings '[]'
{
  "duplicate": false,
  "outcome": "passed",
  "revision": 12,
  "status": "in_progress",
  "warning": "PR comment failed: gh pr comment https://github.invalid/pr/1 --body wddctl review by codex-review: clean review, no findings. failed: fake-gh: forced failure (FAKE_GH_FAIL=1)"
}
```

### `merge.mode: human` — `await_human_merge` and `--observed`

Under `merge.mode: human`, `wddctl next` never offers `merge_task` for a
`merge_ready` task — it offers **`await_human_merge`** instead: no
`command` (there is nothing for `wddctl` to run), a `judgment` naming the
PR (or the branch, if there is no real PR — see `human_reference` above),
and a `recordWith` that is the exact `merge --observed` invocation needed
once the human has actually merged it:

```json
{
  "action": "await_human_merge",
  "judgment": "https://github.invalid/pr/1 must be merged by its human owner directly; wddctl will not merge it in human mode. Once merged, record it with recordWith so live Git can prove it happened.",
  "recordWith": "wddctl merge --task T2 --repo . --observed",
  "task": "T2"
}
```

Running plain `wddctl merge --task T2 --repo .` against that task refuses
outright, naming the same PR and the same `--observed` escape hatch:

```
wddctl: merge mode is human: https://github.invalid/pr/1 must be merged by its human owner directly; wddctl will not merge it. Once merged, run 'wddctl merge --task T2 --repo . --observed' to record it.
```

`--observed` never mutates Git — it only proves a merge that already
happened and records it. Concretely: `wddctl merge --task ID --repo . --observed`
runs `git fetch origin <base_ref>` best-effort (tolerating no network or no
`origin` at all), then resolves the SHA to prove ancestry against —
preferring the freshly fetched `origin/<base_ref>` over the local
`<base_ref>` branch when a remote-tracking ref for it exists, because `git
fetch` only ever advances `refs/remotes/origin/<base_ref>`, never the local
branch, and a human merge that landed on the remote (e.g. clicking "Merge"
on GitHub) is real, provable evidence the instant the fetch completes even
though nothing has pulled it into the local branch yet. It then requires
`git merge-base --is-ancestor <task.headSha> <that SHA>` to be true. Fails
before the human merge exists:

```
wddctl: task T2 head e34ce96575c2225a630c0ffd1e0a2d16988f43a7 is not reachable from wdd/demo (4b2bac01e6fe248f3149514fbb601b8f2ba91b50); the human merge has not happened
```

Succeeds once it's true, applying the same `task.merged` bookkeeping a
normal merge records (revision bump, task -> `done`), without touching Git
at all:

```json
{
  "action": "observed_human_merge",
  "baseRef": "wdd/demo",
  "baseSha": "655eea9724fbe5caf8b1caee632cd589b43bc976",
  "branch": "task/T2",
  "duplicate": false,
  "headSha": "e34ce96575c2225a630c0ffd1e0a2d16988f43a7",
  "revision": 17,
  "task": "T2"
}
```

`--observed` works under **both** modes — a `controller`-mode scope a human
merged directly out-of-band (bypassing `wddctl merge` entirely) is just as
legitimately observable as a `human`-mode one; the flag only ever proves a
merge, it never asks who the mode says should have performed it.

### `monitor` and `record_human_merge`

`wddctl monitor --once --repo .` is the cheap, zero-LLM tick that also
watches for exactly this case: for any `merge_ready` task whose recorded
`headSha` is already an ancestor of the scope's *local* base ref (no fetch
— monitor is meant to be cheap, so it checks only what's already on disk),
it emits a `record_human_merge` action carrying the literal `merge
--observed` command, in any mode — an out-of-band merge is worth
surfacing even in `controller` mode, not just `human` mode:

```json
{
  "actions": [
    {
      "action": "record_human_merge",
      "command": "wddctl --state .wdd/state.json merge --task T2 --repo . --observed",
      "task": "T2"
    }
  ],
  "changed": true,
  "revision": 16,
  "scope": "SCOPE-demo"
}
```

This is how a controller running the ordinary `next` loop notices "someone
merged this by hand" without a human having to say so — the next `monitor
--once` tick surfaces the exact command to record it, and running that
command is the same `--observed` path described above.

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

## The finalize phase

Once every task in a scope reaches `done` or `cancelled`, `derived_phase`
stops returning `execute`: the scope enters `finalize`, and `wddctl next` /
`wddctl status` switch to a reduced, scope-level shape — no more per-task
gates. A new family of verbs — `finalize review`, `finalize verify`,
`finalize handoff`, `finalize delivered`, `finalize status` — choreographs
what Spec §6 calls the finalize ladder: review the whole epic branch,
verify it, hand it off, and record the human's final merge.
`derived_phase` returns `delivered` once that merge is observed; there is
no phase after it. `wddctl status` and `wddctl next` (no special flag
needed) detect the phase automatically and switch shape:

```sh
wddctl status --json
# {"finalize": {}, "phase": "finalize"}
```

Every **mutating** finalize verb (`review record`, `verify record`,
`handoff`, `delivered`) refuses outside `finalize`/`delivered` ("finalize
verbs require the scope to be in the finalize or delivered phase..."), and
additionally refuses once `delivered` is recorded — there is nothing left
to review, verify, or hand off once the human has already merged
("this scope is already delivered; there is nothing left to \<review|verify|hand off\>").
`finalize status` is the exception: it reports in any phase (setup,
execute, finalize, delivered), the same way `wddctl status` always does.

### `finalize review record`

Reviews the **whole epic branch** — not one task's diff — against
`.wdd/spec.md`'s acceptance criteria. Same P1/P2/P3 vocabulary and
`review.blockingSeverities` (default P1/P2) as task-level review, but
evidence is pinned to the scope's **base-branch head SHA**, not a task
head — there is no task head left once every task is terminal.

```sh
wddctl finalize review record --reviewer NAME --findings '[...]' --repo .
```

Omit `--findings` or pass `[]` for a clean review. Outcome is `passed`
when no finding's severity is in `review.blockingSeverities`, else
`blocked`.

### `finalize verify record`

Mirrors the task-level contract, at scope granularity:

```sh
wddctl finalize verify record --status passed|failed|unavailable --command CMD --repo .
```

`--status unavailable` requires `--justification` (or falls back to a
configured `verification.unavailableJustification`) — skipping
verification silently is no more acceptable at scope granularity than it
is per task.

### `finalize handoff`

Requires a clean, fresh final review and a passed, fresh final
verification — "fresh" meaning both are pinned to the *current*
base-branch head; a new commit on the base after either was recorded
makes it stale, and the refusal names exactly which one to redo. The
target branch comes from `branching.targetBranch` (a scope that predates
`config.json` gets a clear error pointing at `migrate --governance`).

```sh
wddctl finalize handoff --repo .
```

- **`pr` surface**: pushes the base branch, then opens the epic→target PR
  via `gh` (title `WDD scope <id>`, body a generated summary of tasks and
  evidence) — the same `github.py` machinery task-level `submit` already
  uses.
- **`local` surface**: records the handoff with `pr: null`; the response's
  `instructions` field names the push-and-PR steps as the operator's to
  perform.

`wddctl` never merges the PR it opens — that code path does not exist
anywhere in `finalize.py`. See the transcripts below for both surfaces.

### `finalize delivered`

Proves — never performs — the human's final merge, reusing phase 4's
either-ref ancestry check against the **target** branch instead of a task
base: fetches `origin/<target>` best-effort, then requires the base
branch's head to be an ancestor of either the freshly-fetched
`origin/<target>` or the local `<target>` branch — neither is
authoritative over the other.

```sh
wddctl finalize delivered --by NAME --repo .
```

Fails by naming exactly what hasn't happened yet ("...the final merge has
not happened") before the merge lands; once it lands, `scope.delivered` is
recorded (`at`, `by`, `headSha`) and every later mutating finalize verb
refuses. Re-running `finalize delivered` itself after delivery is refused
too, not silently re-verified — `by`/`at` name who observed the merge and
when, and a retry cannot legitimately add anything the first call didn't
already prove.

### `finalize status`

```sh
wddctl finalize status
```

Prints `{"phase": ..., "finalize": {...}}` — the finalize section plus the
derived phase. `wddctl status` (no subcommand) already prints exactly this
once the scope reaches `finalize`/`delivered` — see above.

### The finalize ladder

`wddctl next` drives the same "exactly one action" discipline scope-level
that it does per task, in priority order:

```
final_review              (no review yet, or stale against the current base head)
  -> assign_final_fixes     (review present, fresh, and blocked — no command)
  -> final_verification     (review passed+fresh; verification missing/stale/not passed)
    -> prepare_handoff        (review + verification both passed+fresh; no handoff yet)
      -> await_delivery         (handoff recorded and fresh)
        -> delivered                (next returns empty actions, "phase": "delivered")
```

`assign_final_fixes` carries no `command`/`recordWith` deliberately: the
fix is new commits on the base branch, and any new commit re-stales the
review, which routes back to `final_review` on its own — the same
self-healing loop task-level `needs_fixes` already relies on.
`final_review` carries `models.review` when configured, exactly like
task-level `run_review`.

### Transcript: the finalize ladder, local surface, start to `delivered`

Real, unedited output from one continuous scratch repository —
`wddctl init` through a single task (`reviewPolicy: none`, so no
task-level review gate) — merged, then the finalize ladder driven straight
through to `delivered`. No network, no `gh`: `merge.surface local`.

```sh
$ wddctl status --json
{"finalize": {}, "phase": "finalize"}
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "final_review",
      "judgment": "dispatch a reviewer against the whole epic branch diff, per wdd-review's final-review contract, checked against .wdd/spec.md",
      "recordWith": "wddctl finalize review record --reviewer NAME --findings '[]' --repo .",
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "finalize",
  "revision": 8,
  "scope": "SCOPE-finalize-demo"
}
$ wddctl finalize review record --reviewer "codex-review" --findings '[]' --repo .
{
  "duplicate": false,
  "headSha": "3ff80c2d843bbb7b580f26f5caa00ffb3ac4d27d",
  "outcome": "passed",
  "revision": 9
}
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "final_verification",
      "judgment": "run full verification against the current epic branch head and record the result",
      "recordWith": "wddctl finalize verify record --status passed --command '<verification command>' --repo .",
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "finalize",
  "revision": 9,
  "scope": "SCOPE-finalize-demo"
}
$ wddctl finalize verify record --status passed --command "python3 -m unittest" --repo .
{
  "duplicate": false,
  "headSha": "3ff80c2d843bbb7b580f26f5caa00ffb3ac4d27d",
  "revision": 10,
  "status": "passed"
}
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "prepare_handoff",
      "command": "wddctl finalize handoff --repo .",
      "judgment": "push the epic branch and open the handoff to the human who performs the final merge (pr surface), or record local handoff instructions (local surface)",
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "finalize",
  "revision": 10,
  "scope": "SCOPE-finalize-demo"
}
$ wddctl finalize handoff --repo .
{
  "duplicate": false,
  "headSha": "3ff80c2d843bbb7b580f26f5caa00ffb3ac4d27d",
  "instructions": "push wdd/finalize-demo to your remote (e.g. 'git push origin wdd/finalize-demo') and open a pull request into main yourself; wddctl does not perform this on the local surface. Once the human merge lands, run 'wddctl finalize delivered --by NAME --repo .' to record it.",
  "pr": null,
  "revision": 11,
  "targetBranch": "main"
}
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "await_delivery",
      "judgment": "wait for the human-owned final merge via the local handoff: push wdd/finalize-demo to your remote and open a pull request into main yourself; wddctl does not perform this on the local surface; once it lands, record it with recordWith so live Git can prove it happened",
      "recordWith": "wddctl finalize delivered --by NAME --repo .",
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "finalize",
  "revision": 11,
  "scope": "SCOPE-finalize-demo"
}
```

The human merge is not simulated JSON — it is a real `git merge` into the
target branch (`main`, here), performed exactly the way an operator would
click "Merge" on a PR or run it from their own terminal, entirely outside
`wddctl`:

```sh
$ git merge --no-ff -q wdd/finalize-demo -m "merge scope SCOPE-finalize-demo into main"
$ wddctl finalize delivered --by ivo --repo .
{
  "by": "ivo",
  "duplicate": false,
  "headSha": "3ff80c2d843bbb7b580f26f5caa00ffb3ac4d27d",
  "revision": 12,
  "targetBranch": "main"
}
$ wddctl next --repo .
{"actions": [], "blockers": [], "phase": "delivered", "revision": 12, "scope": "SCOPE-finalize-demo"}
$ wddctl status --json
{
  "finalize": {
    "delivered": {
      "at": "2026-07-28T23:05:04Z",
      "by": "ivo",
      "headSha": "3ff80c2d843bbb7b580f26f5caa00ffb3ac4d27d"
    },
    "handoff": {
      "at": "2026-07-28T23:05:03Z",
      "headSha": "3ff80c2d843bbb7b580f26f5caa00ffb3ac4d27d",
      "pr": null,
      "targetBranch": "main"
    },
    "review": {
      "at": "2026-07-28T23:05:03Z",
      "findings": [],
      "headSha": "3ff80c2d843bbb7b580f26f5caa00ffb3ac4d27d",
      "outcome": "passed",
      "reviewer": "codex-review"
    },
    "verification": {
      "at": "2026-07-28T23:05:03Z",
      "command": "python3 -m unittest",
      "headSha": "3ff80c2d843bbb7b580f26f5caa00ffb3ac4d27d",
      "justification": null,
      "status": "passed"
    }
  },
  "phase": "delivered"
}
```

### Transcript: a blocked final review

A P1 against `.wdd/spec.md`'s acceptance criteria blocks the ladder
exactly like a task-level P1 blocks merge:

```sh
$ wddctl finalize review record --reviewer "codex-review" --repo . \
    --findings '[{"severity":"P1","summary":"acceptance criterion missing: no test proves greeting() returns exactly \"hello\"","file":".wdd/spec.md","line":0}]'
{
  "duplicate": false,
  "headSha": "20742a47bb8c834df23247e93aaeffb63f53e0da",
  "outcome": "blocked",
  "revision": 9
}
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "assign_final_fixes",
      "findings": [
        {
          "file": ".wdd/spec.md",
          "line": 0,
          "severity": "P1",
          "summary": "acceptance criterion missing: no test proves greeting() returns exactly \"hello\""
        }
      ],
      "judgment": "assign fixes for the blocking findings from the final review (P1: acceptance criterion missing: no test proves greeting() returns exactly \"hello\"); new commits on the base branch re-stale the review, which brings the scope back to final_review automatically",
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "finalize",
  "revision": 9,
  "scope": "SCOPE-finalize-demo"
}
$ wddctl finalize handoff --repo .
wddctl: handoff requires a clean final review; run 'wddctl finalize review record --reviewer NAME --findings [] --repo .'
```

### Transcript: `finalize handoff` on the `pr` surface (stub `gh`)

Same stub-`gh` + bare-origin double documented above for task-level `pr`
surface transcripts (`tests/fixtures/fake-gh`, prepended onto `PATH`, no
real network) — this is the only honest way to show `pr`-surface finalize
output. The stub always prints the same canned PR URL regardless of
arguments, which is why the task's PR and the epic's PR below show the
identical `https://github.invalid/pr/1`; a real `gh` would not do that —
only the logged argv distinguishes the two calls.

```sh
$ wddctl finalize handoff --repo .
{
  "duplicate": false,
  "headSha": "9a4c80ff17be3b71f39d6aeb4382cb210a409389",
  "pr": "https://github.invalid/pr/1",
  "revision": 11,
  "targetBranch": "main"
}
$ cat "$FAKE_GH_LOG"
["pr", "create", "--head", "task/TASK-001-greeting", "--base", "wdd/finalize-demo", "--title", "Add greeting helper", "--body", "wdd task TASK-001-greeting\nspec: tasks/TASK-001-greeting.md\nhead: a3de4188d96267b96d6bce7edeb265e3e6e65356"]
["pr", "create", "--head", "wdd/finalize-demo", "--base", "main", "--title", "WDD scope SCOPE-finalize-demo", "--body", "wdd scope SCOPE-finalize-demo\n\nTasks:\n- TASK-001-greeting: Add greeting helper (done)\n\nFinal review: passed by codex-review\nFinal verification: passed (python3 -m unittest)"]
$ git ls-remote origin "wdd/finalize-demo"
9a4c80ff17be3b71f39d6aeb4382cb210a409389	refs/heads/wdd/finalize-demo
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "await_delivery",
      "judgment": "wait for the human-owned final merge via the handoff PR https://github.invalid/pr/1; once it lands, record it with recordWith so live Git can prove it happened",
      "recordWith": "wddctl finalize delivered --by NAME --repo .",
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "finalize",
  "revision": 11,
  "scope": "SCOPE-finalize-demo"
}
```

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
