# wddctl command reference

`wddctl` is a dependency-free Python controller for the mechanical half of
Wave-Driven Development. It owns state transitions, conflict-domain
enforcement, Git worktree management, evidence tracking, and merging. Skills
own judgment — what a task should contain, whether a diff is correct — and
call `wddctl` to record the outcome.

**Contents**

- [Invocation](#invocation), [The loop](#the-loop), [Optional concurrency
  flags](#optional-concurrency-flags)
- [Commands](#commands) — setup and planning verbs: [`init`](#init),
  [`plan apply`](#plan-apply), [`plan preview`](#plan-preview),
  [`plan lint`](#plan-lint), [`plan template`](#plan-template),
  [`config`](#config), `config get`/`set --epic`,
  `constitution probe`/`ratify`/`amend`/`status`
- [The intake ladder](#the-intake-ladder) — `epic new`, `intake configure`,
  `intake spec`/`research`/`design`, drift and cascade, the plan-approval
  composite
- [Epic configuration overlay](#epic-configuration-overlay) — the epic
  overlay's drift/remedy story, then the rest of the per-task command
  reference: `scope archive`, [`next`](#next), [`status`](#status),
  [`render`](#render), [`start`](#start), [`submit`](#submit),
  [`review record`](#review-record), [`review run`](#review-run),
  [`review collect`](#review-collect), [`verify record`](#verify-record),
  [`verify collect`](#verify-collect), [`freshness check`](#freshness-check),
  [`freshness record`](#freshness-record), [`refresh`](#refresh),
  [`merge`](#merge), [`rebind`](#rebind), [`dispatch`](#dispatch),
  [`release`](#release), `block`/`unblock`/`cancel`, [`note`](#note),
  `reconcile status`/`reconcile done`, [`migrate`](#migrate),
  [`monitor`](#monitor), [`event apply`](#event-apply), `--version`,
  [`doctor`](#doctor)
- [Merge surfaces and modes](#merge-surfaces-and-modes) — the
  surface×mode matrix, transcripts, `merge.mode: human`, `monitor` and
  `record_human_merge`
- [Runners](#runners) — registering and dispatching an external agent CLI
- [Gates (what `next` emits per task)](#gates-what-next-emits-per-task)
- [The finalize phase](#the-finalize-phase) — `finalize review`/`verify`
  record, `finalize handoff`, `finalize delivered`, the finalize ladder,
  full transcripts
- [Guarantees](#guarantees)

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
- `--approved-by NAME` — record approval: stamps `{"by": NAME, "at": <utc_now>, "sha256": <composite>}` into `scope.approval`, where the composite is a SHA-256 over the normalized plan plus every task brief and context file (see "The plan-approval composite" below). A nonempty diff requires this flag on v5 scopes; re-apply without it preserves the recorded approval. Legacy scopes keep the old `{by, at}` behavior.

```sh
wddctl plan apply --plan plan.json --repo . --dry-run
# {"scope": "SCOPE-auth-refresh", "created": false, "diff": {"added": [...], ...}}
wddctl plan apply --plan plan.json --repo . --approved-by NAME
# a nonempty diff on a non-legacy scope refuses without --approved-by
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
| `missing_spec` | the active epic's `spec.md` (`epics/<slug>/spec.md`) is missing or effectively empty — the finalize phase reviews the epic branch against it; run the intake first. |
| `nonprose_brief` | a task's brief starts with `{` or `[` — it reads as JSON/data, not the Markdown prose (objective, scope, verification) a worker needs. |
| `missing_deliverable` | a task's brief has no non-empty `## Deliverable` section — the reviewer's first question is whether the diff produces it. |
| `missing_interfaces` | a task's brief has no non-empty `## Interfaces` section — Consumes/Produces should be consistent with design.md. |
| `missing_context` | intake artifacts are recorded in state (spec/research/design) but a task carries no `context` refs — handover will rely on memory, not machine-carried evidence. |
| `missing_criteria` | a task has no `context` ref that fully matches `spec.md#AC-<n>` — advisory, since genuinely internal tasks discharge no criterion. |
| `unowned_surface` | design.md's `## Integration surfaces` lists a path that no task's `conflictDomains` cover — a surface with producers and no owning task is a design error caught mechanically. |

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

### `plan template`

Print a deterministic skeleton to fill in — the mechanical starting point
for wdd-plan decomposition, so an agent that reaches the plan stage doesn't
have to guess `plan init` or go hunting the filesystem for the skill's
template files. Pure emitter: no state is read or written, and it isn't a
governed verb, so it runs with no `.wdd/` at all, exactly like `--help`.

```sh
wddctl plan template
```

```json
{
  "kind": "wdd_plan",
  "schemaVersion": 1,
  "scope": {
    "baseRef": "wdd/your-scope-id",
    "id": "SCOPE-your-scope-id",
    "maxConcurrent": 3,
    "reconcileEveryNMerges": 3,
    "reviewPolicy": "risk_based"
  },
  "tasks": [
    {
      "conflictDomains": [
        "src/**"
      ],
      "context": [],
      "dependsOn": [],
      "id": "TASK-001",
      "model": null,
      "reviewModel": null,
      "risk": "normal",
      "specPath": "tasks/TASK-001.md",
      "title": "TASK-001: replace with a short task title"
    }
  ]
}
```

The output already passes `validate_plan()` as-is, so a filled-in copy stays
structurally legal throughout — the placeholder strings (scope id, task
id/title/specPath) are what need replacing, not the shape.

```sh
wddctl plan template --brief
```

```
# TASK-001: replace with a short task title

## Objective

Describe the outcome this task delivers and why it matters, in 1-3 sentences.

## Deliverable

Describe what the diff must produce, in terms a reviewer can check against
the code: the file(s) or behavior that exist once this task is done.

## Interfaces

Consumes:
- what this task reads or depends on from other tasks or existing code.

Produces:
- what this task creates that other tasks or consumers will depend on.

## Scope

- what is explicitly in scope for this task.

## Non-scope

- what is explicitly out of scope (usually: other tasks' deliverables).

## Files to read first

- paths worth reading before starting, if any.

## Conflict domains

- paths this task writes to (should match the plan's conflictDomains).

## Verification

`replace with the exact command that proves this task works`

## Definition of done

- [ ] Deliverable committed.
- [ ] Verification command passes.
- [ ] No changes outside the declared conflict domains.
```

The brief carries the two sections `plan lint`'s `missing_deliverable` and
`missing_interfaces` checks require (non-empty `## Deliverable` and
`## Interfaces`), plus the rest of `templates/task.md`'s shape, so a filled-in
copy is a normal brief, not a minimal one that merely passes lint.

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

`worktrees.root` (default `.worktrees`) is where `start` creates task
worktrees: a relative value resolves against the repository root (the
default lands at `<repo>/.worktrees/<scope>/<task>`); an absolute value is
used as-is, anywhere on disk. Whichever it resolves to is gitignored at the
repository root automatically — idempotently, both at `init` and the moment
a worktree is first created under it — except for an absolute root outside
the repository, which needs no entry and gets none.

```sh
wddctl config set worktrees.root "build/wdd-trees"
```

Already-started tasks are unaffected by a later change: a task's worktree
location is fixed the moment it is first created (recorded as an explicit
override only when it differs from the config-derived default — see
"Portability" in [`artifact-schema.md`](artifact-schema.md)), and a task
with no recorded override re-derives from whatever `worktrees.root`
currently says. Changing it after ratification is governance drift like any
other `config.json` edit (see "Governance drift" below) — the drift gate
catches a stray change; deliberately moving it mid-scope still needs
`wddctl constitution amend` the same as any other config change.

```sh
wddctl config show
```

Prints the whole config object. Useful for showing a user everything before
`constitution ratify`, since ratifying signs this file's exact contents (see
below).

### `config get`/`set --epic`

With an active epic, both `get` and `set` accept `--epic` to read or write
the **epic overlay** (`.wdd/epics/<slug>/config.json`) instead of the
global config — see "Epic configuration overlay" below for the full
resolution/digest/drift story; this is the mechanical surface for it.

```sh
$ wddctl config get --epic merge.surface
{
  "path": "merge.surface",
  "source": "global",
  "value": "local"
}
$ wddctl config set --epic merge.surface pr
{
  "epic": "greeting-demo",
  "path": "merge.surface",
  "value": "pr"
}
$ wddctl config get --epic merge.surface
{
  "path": "merge.surface",
  "source": "epic",
  "value": "pr"
}
```

- `get --epic` prints the **merged view** (epic overlay → global config →
  built-in default) with a `source` marker naming which layer answered —
  `"epic"`, `"global"`, or `"default"` — never the raw overlay file alone.
- `set --epic` writes only the active epic's overlay; the global
  `config.json` is untouched. `--epic` without an active epic (`state.epic`
  is `null`) refuses, naming `wddctl epic new` as the remedy.
- The overlay is a **sparse, allowlisted** set of dotted leaves — the exact
  set the epic-scoped-state design permits an epic to differ from global
  on: `models.planning`, `models.implementation`, `models.review`,
  `verification.commands`, `verification.unavailableJustification`,
  `merge.surface`, `riskRules`, `review.policy`. Anything else — `runners`,
  `worktrees.root`, branch settings, anything unlisted — is rejected **by
  name**, at `set --epic` and at `intake configure` approval alike:

```sh
$ wddctl config set --epic runners '{}'
wddctl: epic config overlay: key(s) not in the allowed overlay set (models.planning, models.implementation, models.review, verification.commands, verification.unavailableJustification, merge.surface, riskRules, review.policy): runners
```

  Machine-bound and repository-authority settings stay under constitution
  approval only — an epic cannot quietly reroute them.
- A `set --epic` value is parsed the same way a global `set`'s is (JSON
  first, falling back to the literal string — see above), and it does
  **not** by itself record anything: it edits the overlay on disk; `wddctl
  intake configure` is the separate, explicit approval step (see "The
  intake ladder" above). Editing the overlay after `configure` has already
  approved it is epic-config drift — see "Epic configuration overlay"
  below.

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

## The intake ladder

Between `constitution ratify` and `plan apply`, `wddctl next` walks five
rungs, one at a time: **epic → configure → spec → research → design**. The
first two — `wddctl epic new` and `wddctl intake configure` — name the
epic and settle its config overlay before any artifact is agreed (see
"Epic directories" and "Epic configuration overlay" below); the remaining
three are recorded by their own verbs (`wddctl intake spec` / `research` /
`design`), each bound to the exact bytes it approved — a SHA-256 of the
artifact, the same `governance_fingerprint` idiom the constitution uses.
Editing an already-approved artifact is drift, not a free edit, exactly
like editing `config.json`/`constitution.md` after ratification is. The
spec/research/design portion of the ladder is also **ordered and
cascading**: re-approving a rung clears every record after it (a spec
re-approval clears research, design, and the plan's composite approval; a
research re-approval clears design and the composite; a design
re-approval clears only the composite) — because a later record's
approval implicitly rests on the upstream bytes that just changed
underneath it. `epic new` and `configure` don't participate in that
cascade — there is nothing upstream of them to invalidate — but
`configure` has its own re-approval semantics; see "Epic configuration
overlay" below.

A migrated (legacy, `intake.legacy`) scope is exempt from the
**spec/research/design** rungs — `wddctl next` never emits one of those
rung actions for it, and the rung verbs themselves refuse outright — but
it is **not** exempt from `intake configure`: a legacy scope still gets an
epic (migration adopts or creates one), still carries an epic config
overlay, and `intake configure --approved-by` remains the reachable,
correct remedy for epic-config drift even though the rest of the ladder
stays permanently skipped (see "Migration" in
[`artifact-schema.md`](artifact-schema.md)). Every rung verb also refuses
before ratification, and once the scope reaches `delivered` (`wddctl scope
archive` is the only way back to a fresh ladder — see below).

Real, unedited output from one continuous scratch repository — `wddctl
init` through ratify, then the whole ladder — merge surface `local`:

```sh
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "create_epic",
      "command": "wddctl epic new --slug SLUG",
      "judgment": "name the work with the user in ONE short round (a lowercase-dash slug, and optionally a display title) per the wdd-intake skill, then create the epic. Slugs are immutable -- there is no rename verb; retiring one means archiving it.",
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "setup",
  "revision": 1,
  "scope": null
}
```

### `wddctl epic new`

The first action of every epic, per spec Sec1: names the work and creates
its directory in one governed, locked mutation.

```sh
wddctl epic new --slug greeting-demo [--title "Greeting helper"]
```

```json
{
  "duplicate": false,
  "epic": "greeting-demo",
  "revision": 2
}
```

- **Slug**: `[a-z0-9][a-z0-9-]{1,63}`, unique against both `epics/` and
  `archive/` — slugs are immutable (there is no rename verb; retiring one
  means archiving it), so a fresh epic never reuses a name a delivered one
  already carries. `--title` is optional decoration only; the slug alone
  is the canonical identity.
- Creates `epics/<slug>/` holding an empty overlay (`config.json: {}`) and
  records `state.epic = <slug>` — the whole thing is one locked mutation,
  so a crash can never leave a directory without a matching `state.epic`,
  or vice versa, in any way other than the idempotent crash-orphan shape
  below.
- Refuses when an epic is already active:

```sh
$ wddctl epic new --slug second-try
wddctl: an epic is already active ('greeting-demo'); archive it with 'wddctl scope archive --repo .' before starting a new one
```

  or when the slug collides with an archived epic:

```
wddctl: epic slug 'greeting-demo' is already used by an archived epic (.wdd/archive/greeting-demo); slugs are immutable and unique across epics/ and archive/ -- choose a different slug
```

- **Crash-orphan adoption**: if a previous `epic new` crashed after
  creating `epics/<slug>/` (empty overlay, nothing else) but before the
  state write landed, re-running the exact same `epic new --slug SLUG`
  adopts that directory idempotently instead of refusing — `wddctl doctor`
  additionally reports any such orphan it finds (`"epicOrphans": [...]`),
  so an operator doesn't have to go looking for the crash by hand. A
  directory that already holds a reserved `record.json` (an in-flight or
  completed archive transaction, or a hand-placed collision) is refused
  outright instead of adopted — see "Transactional archive" below.

Directory layout right after `epic new` (see [`artifact-schema.md`](artifact-schema.md)
for the full picture, including what `plan apply` and the intake rungs add
to it):

```text
.wdd/
  epics/
    greeting-demo/
      config.json    # the sparse overlay -- {} until `config set --epic` writes to it
```

### `wddctl intake configure`

The second rung, immediately after `epic new` and before `agree_spec`:
settles the epic's config overlay — merge surface, models, verification
commands, risk rules, review policy — with one explicit, attributed
decision. Two legal forms, both required to name a human:

```sh
wddctl intake configure --approved-by NAME            # overlay as currently written
wddctl intake configure --use-defaults --by NAME      # empty overlay, inherit everything
```

Before recording, use `wddctl config set --epic PATH VALUE` to write any
overlay leaves the epic actually needs (see "`config get`/`set --epic`"
below) — `configure` records whatever the overlay holds at that moment, it
does not itself prompt for values.

```sh
$ wddctl config set --epic merge.surface local
{
  "epic": "greeting-demo",
  "path": "merge.surface",
  "value": "local"
}
$ wddctl intake configure --approved-by ivo
{
  "duplicate": false,
  "revision": 3,
  "sha256": "sha256:1223d65d5c9ca75d52830c0abd5a6566b08bcc87c3300e78d400494941f25f90"
}
```

- The recorded `sha256` is over the **derived, post-mutation, fully
  resolved effective config** (overlay layered over global layered over
  built-in defaults, canonically serialized — the same
  `effective_config_digest` idiom "Epic configuration overlay" below
  describes) — not the overlay bytes alone, and not a value re-read from
  disk after the fact. A global config change therefore makes this record
  stale by construction, with no separate cascade to remember.
- `--use-defaults` is the explicit decision to inherit everything —
  silence about epic config is not an option, the same doctrine as
  research's `--skip --reason`. It also **resets the overlay file to
  `{}`** as a side effect: a nonempty, unapproved overlay left on disk
  behind a "defaults" approval would be a lie the next drift check
  couldn't catch.
- `agree_spec` refuses until `configure` is recorded:

```
wddctl: intake configure must be recorded before agree_spec (run 'wddctl intake configure --approved-by NAME' or '--use-defaults --by NAME')
```

- Re-recording `configure` mid-epic (the remedy for epic-config drift, or
  simply a genuine change of mind) does **not** clear spec/research/design
  — their content doesn't depend on config — but **does** clear
  `scope.approval`, since the plan was approved under the old effective
  config (models, riskRules, review policy all feed apply-time
  derivation). See "Epic configuration overlay" below for the full
  drift/cascade story and a worked mid-epic remedy.

> The worked examples in this subsection through "Plan drift" below
> predate the epic-scoped-state plan and were captured against a flat
> `.wdd/spec.md`/`.wdd/design.md`/`.wdd/tasks/<TASK-ID>.md` layout for
> narrative continuity with their original capture session. Under schema
> v6 the SAME artifact references — `spec.md`, `design.md`,
> `tasks/<TASK-ID>.md` — resolve through the typed path resolver into the
> **active epic's** directory instead (`epics/<slug>/spec.md`, and so on;
> see "Epic directories" in [`artifact-schema.md`](artifact-schema.md)) —
> the mechanisms these examples demonstrate (drift, cascade, the plan-
> approval composite) are otherwise unchanged; only the literal path
> underneath `.wdd/` moved. The "`wddctl epic new`"/"`wddctl intake
> configure`" examples above and "The archive transaction and its
> recovery" below use the current, epic-scoped paths throughout.

### `wddctl intake spec`

Records `epics/<slug>/spec.md`'s approval. Refuses unless the file exists,
non-empty, has all four required `## ` sections (Goal, In scope, Out of
scope, Acceptance criteria), and its Acceptance criteria section is
**wholly numbered**: every checklist line matches `- [ ] AC-<n>: ...`, the
numbers unique and contiguous from 1 — final review has to be able to walk
`1..N` with nothing outside the numbering.

```sh
$ wddctl intake spec --approved-by ivo
{
  "criteria": 2,
  "duplicate": false,
  "revision": 2
}
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "research",
      "judgment": "read the named reference implementation and build the contract inventory per the wdd-intake skill's research stage, or record an explicit, attributed skip when no external contract applies",
      "recordWith": "wddctl intake research --done --by NAME --artifacts PATH... (or --skip --by NAME --reason '...')",
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "setup",
  "revision": 2,
  "scope": null
}
```

### `wddctl intake research`

Exactly one of `--done` (with one or more `--artifacts PATH...`, each
`.wdd`-relative, existing, non-empty, and fingerprinted) or `--skip` (with
a non-empty `--reason`) — silence about research is not an option, and
neither form is anonymous. Refuses before a spec is recorded.

```sh
$ wddctl intake research --done --by ivo --artifacts shared-context/contract-inventory.md
{
  "duplicate": false,
  "revision": 3
}
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "agree_design",
      "judgment": "agree .wdd/design.md (components, interfaces, integration surfaces, epic deliverable) with the user per the wdd-intake skill's design stage, then record it with the command that proves the epic deliverable",
      "recordWith": "wddctl intake design --approved-by NAME --deliverable-command '...'",
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "setup",
  "revision": 3,
  "scope": null
}
```

(`wddctl intake research --skip --by NAME --reason "..."` is the other
mode — used in the E2E test and the drift/cascade examples below; both
modes record `by`/`at`, differing only in `done`+`artifacts` vs.
`skipped`+`reason`.)

### `wddctl intake design`

`--deliverable-command` is **required and non-empty** — the epic
deliverable's proof is not optional. Refuses unless `.wdd/design.md`
exists, non-empty, has all four required sections (Components, Interfaces,
Integration surfaces, Epic deliverable), and research is already recorded.

```sh
$ wddctl intake design --approved-by ivo --deliverable-command 'python3 -c "from src.greeting import greet; assert (\"Ivo\" in greet(\"Ivo\"))"'
{
  "duplicate": false,
  "revision": 4
}
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "plan",
      "command": "wddctl plan apply --plan plan.json --repo . --approved-by NAME",
      "judgment": "decompose the work per the wdd-plan skill, write task briefs, show the user the diff for explicit approval, then apply with the approving human's name",
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "setup",
  "revision": 4,
  "scope": null
}
$ wddctl intake status
{
  "intake": {
    "design": {
      "at": "2026-08-01T19:57:01Z",
      "by": "ivo",
      "deliverableCommand": "python3 -c \"from src.greeting import greet; assert (\\\"Ivo\\\" in greet(\\\"Ivo\\\"))\"",
      "sha256": "sha256:391abee7b5efcd4de0fe2f113db79784e9b0f295946f41b63649bafbaed86bfc"
    },
    "research": {
      "artifacts": [
        {
          "path": "shared-context/contract-inventory.md",
          "sha256": "sha256:b997dfe72a5e5e130a08c2c1d31de0fb0466ad972bcfebdb926cb87b7020034a"
        }
      ],
      "at": "2026-08-01T19:56:54Z",
      "by": "ivo",
      "done": true
    },
    "spec": {
      "at": "2026-08-01T19:56:46Z",
      "by": "ivo",
      "criteria": 2,
      "sha256": "sha256:d35ccd4e7c804285de3ecc65872ac654874efbb1fedfd4b8599d4f53133589ae"
    }
  },
  "nextRung": null
}
```

### Drift: an edit after approval

Editing an approved artifact's bytes without re-recording the rung is
drift — `next` re-emits the same rung action with `"stale": true` instead
of advancing, and the rung verb itself is exactly the remedy:

```sh
$ echo "unrelated edit after approval" >> .wdd/spec.md
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "agree_spec",
      "judgment": "agree .wdd/spec.md with the user (goal, in/out of scope, numbered acceptance criteria) per the wdd-intake skill's spec stage, then record it",
      "recordWith": "wddctl intake spec --approved-by NAME",
      "stale": true,
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "setup",
  "revision": 4,
  "scope": null
}
```

### Cascade: a re-approval clears downstream rungs

Re-recording `spec` above (the remedy for the drift, or simply a genuine
scope change) clears `research` and `design` — both approved research and
design implicitly rested on the spec bytes that just moved:

```sh
$ wddctl intake spec --approved-by ivo
{
  "criteria": 2,
  "duplicate": false,
  "revision": 5
}
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "research",
      "judgment": "read the named reference implementation and build the contract inventory per the wdd-intake skill's research stage, or record an explicit, attributed skip when no external contract applies",
      "recordWith": "wddctl intake research --done --by NAME --artifacts PATH... (or --skip --by NAME --reason '...')",
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "setup",
  "revision": 5,
  "scope": null
}
```

(Research and design have to be walked again from here before `plan apply`
is reachable — omitted from this transcript since it's identical to the
happy walk above.)

### The plan-approval composite

Once the ladder is complete, `wddctl plan apply --approved-by NAME`
records `scope.approval = {by, at, sha256}`, where the SHA-256 is a
**composite** over the canonically normalized plan document plus every
task's brief file and every `context` file it references, sorted by path.
Any nonempty diff against the currently-applied plan without
`--approved-by` is refused; a byte-identical re-apply (no diff at all)
silently preserves the existing approval, and a plan-file-unchanged
re-apply *with* `--approved-by` is how you re-stamp the composite after
editing a brief or context file that `_diff_plan` itself can't see (their
bytes are covered by the composite even though they aren't plan-file
fields):

```sh
$ wddctl plan apply --plan plan.json --repo . --approved-by ivo
{
  "approvedBy": "ivo",
  "base": {"action": "created", "baseRef": "wdd/greeting-demo", "baseSha": "b1fe1255b9092ab6075c172b2cd31ffe1cb6ce43", "from": "HEAD"},
  "created": true,
  "diff": {"added": ["TASK-001-greeting"], "removed": [], "scope": {"...": "..."}, "updated": []},
  "dryRun": false,
  "duplicate": false,
  "lint": [],
  "revision": 6,
  "scope": "SCOPE-greeting-demo"
}
```

`scope.approval` afterward:

```json
{
  "at": "2026-08-01T19:57:34Z",
  "by": "ivo",
  "sha256": "sha256:4446e7a9ccf9fca7b888e238be03a06833b5ab7172a2410c8af48605effb6abf"
}
```

Applying against no state at all (`wddctl init` never run, or `state.json`
missing) is refused outright, naming `wddctl init` — the old
store-missing-means-bootstrap-a-legacy-scope path is gone:

```
wddctl: cannot apply plan.json: no state at .wdd/state.json; run 'wddctl init --repo .' first
```

Applying with an incomplete or drifted ladder is refused too, before any
scope is created or touched:

```
wddctl: cannot apply plan.json: intake ladder is incomplete; walk it with 'wddctl intake spec/research/design' and 'wddctl next'
```

### Plan drift: editing a brief or context file after approval

A brief or `context` file edited after the composite was recorded is
**plan drift** — caught by the same execution gate that guards intake
drift, so it blocks every governed verb (`start` included), not only
`plan apply`:

```sh
$ echo "Edited after composite approval." >> .wdd/tasks/TASK-001-greeting.md
$ wddctl start --task TASK-001-greeting --repo .
wddctl: plan drift: recorded plan approval sha256:4446e7a9ccf9fca7b888e238be03a06833b5ab7172a2410c8af48605effb6abf no longer matches the applied plan's current bytes (sha256:2468e4d7da37d4a34309a4ed26a1e34d7c8273d9c09ade0776f8112be63541b7); a brief or context file changed since approval. Run 'wddctl plan apply --approved-by NAME' (an unchanged plan file is a pure re-stamp) to re-approve before resuming execution
$ wddctl next --repo .
{
  "actions": [],
  "blockers": [
    {
      "actual": "sha256:2468e4d7da37d4a34309a4ed26a1e34d7c8273d9c09ade0776f8112be63541b7",
      "code": "plan_drift",
      "message": "the applied plan's composite approval no longer matches its current bytes (a brief or context file changed, or it was never composite-approved); run 'wddctl plan apply --approved-by NAME' to re-stamp",
      "recorded": "sha256:4446e7a9ccf9fca7b888e238be03a06833b5ab7172a2410c8af48605effb6abf"
    }
  ],
  "revision": 6,
  "scope": "SCOPE-greeting-demo",
  "truncated": false
}
```

The remedy is a re-stamp — the plan file itself is unchanged, so the diff
is empty, but `--approved-by` moves the composite to cover the edited
brief:

```sh
$ wddctl plan apply --plan plan.json --repo . --approved-by ivo
{"approvedBy": "ivo", "created": false, "diff": {"added": [], "removed": [], "scope": {}, "updated": []}, "dryRun": false, "lint": [], "revision": 7, "scope": "SCOPE-greeting-demo", "unchanged": true}
$ wddctl start --task TASK-001-greeting --repo .
{
  "action": "create_branch_and_worktree",
  "baseRef": "wdd/greeting-demo",
  "branch": "task/TASK-001-greeting",
  "duplicate": false,
  "headSha": "b1fe1255b9092ab6075c172b2cd31ffe1cb6ce43",
  "revision": 8,
  "specPath": "tasks/TASK-001-greeting.md",
  "task": "TASK-001-greeting",
  "worktree": "/path/to/repo/.worktrees/SCOPE-greeting-demo/TASK-001-greeting"
}
```

Intake drift (an edited `spec.md`/`design.md`, or a changed/missing
research artifact, after the ladder was walked) is caught by the same
gate, named `intake_drift` instead of `plan_drift`, and is remedied by
re-walking the drifted rung (and everything the cascade re-clears) then
re-stamping `plan apply --approved-by` — see `ExecutionGateIntakeDriftTest`
in `tests/test_intake.py` for the full remedy walk.

## Epic configuration overlay

`.wdd/epics/<slug>/config.json` is a **sparse overlay**: only the leaves an
epic actually needs to differ on are present (an empty file, `{}`, is
common and legal). Every config-reading site resolves a key path through
one shared function, per key, never by reading either file directly:

```
epic overlay → global config.json → built-in default
```

`models.review` can be overridden while `models.implementation` falls
through to global, and so on, independently per leaf — see "`config
get`/`set --epic`" above for the mechanical surface.

**One digest function, byte-precise.** `effective_config_digest(view)` is
the only fingerprint implementation for the resolved view: input is the
fully parsed, default-hydrated merged view with source markers stripped;
serialization is JSON with recursively sorted object keys, array order
preserved, UTF-8, fixed separators; duplicate keys and non-finite numbers
are rejected at parse (the same precision `governance_fingerprint` already
holds constitution/config to). `intake configure`'s recorded `sha256` (see
above) is this digest over the full resolved view.

**Purpose-projected digests on evidence, not one blob.** A task's review
or verification record never stores the full-config digest — it stores
the digest of the **projection** relevant to its own purpose, computed by
the same function over a named key subset: `plan` (models, riskRules,
review.policy), `taskReview` (models.review, review.policy,
review.blockingSeverities), `finalReview` (models.review,
review.blockingSeverities), `taskVerification`/`finalVerification`
(verification.*, plus the deliverable command for the final one). A
`models.planning` edit therefore stales nothing downstream; a
`verification.commands` edit stales exactly the verification evidence, not
review. Review evidence additionally binds its **resolved decision
values** — the risk tier and review model it actually ran under — since a
task's risk can rise without any config byte moving its projection; the
consuming gate re-derives both from current state and compares (see
"evidence digest fields" in [`artifact-schema.md`](artifact-schema.md)).

### Drift and the mid-epic remedy chain

Editing the overlay (or the global config it layers over) after `intake
configure` approved it is **epic-config drift** — it joins the same
chokepoint governance drift and intake/plan drift already sit at, in this
precedence order: **governance → epic config → intake artifacts → plan
composite**. One blocker at a time; `next` names the current one and its
own remedy names the next.

Real, unedited output from a fresh scratch repository: `epic new` through
`plan apply` and `start` (an epic-scoped rerun of the same shape the
`greeting-demo` example above walks), then an overlay edit mid-epic,
refused via `start`'s re-attach path, remedied, and the plan drift it
cascades into, remedied in turn:

```sh
$ wddctl config set --epic models.planning "gpt-x"
{
  "epic": "greeting-demo",
  "path": "models.planning",
  "value": "gpt-x"
}
$ wddctl start --task TASK-001-greeting --repo .
wddctl: epic config drift: the epic overlay (or the global config it layers over) changed since intake.configure was approved (recorded sha256:7bd4aac7375f0554beca9b01974a5c1167f23d5fc1374f84bfe0de69a7fe98a1, current sha256:bfaa169ffd3fdacc4479011dddba5b3d4254f37a67b75b656a8a072ebb7b1cc1); run 'wddctl intake configure --approved-by NAME' (or --use-defaults --by NAME) to re-approve
$ wddctl intake configure --approved-by ivo
{
  "duplicate": false,
  "revision": 10,
  "sha256": "sha256:bfaa169ffd3fdacc4479011dddba5b3d4254f37a67b75b656a8a072ebb7b1cc1"
}
$ wddctl start --task TASK-001-greeting --repo .
wddctl: plan drift: this scope's plan was never composite-approved; run 'wddctl plan apply --approved-by NAME' to stamp the currently applied plan
$ wddctl plan apply --plan plan.json --repo . --approved-by ivo
{"approvedBy": "ivo", "created": false, "diff": {"added": [], "removed": [], "scope": {}, "updated": []}, "dryRun": false, "lint": [...], "revision": 11, "scope": "SCOPE-greeting-demo", "unchanged": true}
$ wddctl start --task TASK-001-greeting --repo .
{
  "action": "reattach:reuse",
  "branch": "task/TASK-001-greeting",
  "duplicate": false,
  "inputsRecorded": 1,
  "revision": 12,
  "snapshot": "dispatch/TASK-001-greeting-1",
  "specPath": "tasks/TASK-001-greeting.md",
  "status": "in_progress",
  "task": "TASK-001-greeting",
  "worktree": "/path/to/repo/.worktrees/SCOPE-greeting-demo/TASK-001-greeting"
}
```

(`intake configure`'s re-approval clears `scope.approval`, which is why
the SECOND `start` names `plan_drift` — "this scope's plan was never
composite-approved" — rather than repeating `epic_config_drift`: the
first blocker in precedence order is always the one refused on. The
re-stamp's `lint` array is elided above for length; see "`plan lint`"
above for what those specific findings mean.)

Two things worth naming explicitly about that second refusal: re-recording
`configure` clears `spec`/`research`/`design` **not at all** (their
content never depended on config) but clears `scope.approval` — the plan
was approved under the *old* effective config, and models/riskRules/review
policy all feed apply-time derivation, so execution can only resume after
a re-stamp. And the re-stamp's **risk re-derivation covers every task**,
not only `todo` ones: a task already in flight whose risk rises under the
new effective policy has its review gate recompute at verb time — if the
new policy now requires a review that was never recorded, `merge` refuses
until one is. A risk *drop* never removes an already-applicable
requirement (upward-only, matching `riskRules` doctrine generally).

**Global config changes mid-epic** still trip governance drift exactly as
before (`constitution amend` is still the remedy) — since resolution is
dynamic, a re-approved global change flows straight into every epic's
merged view, and the same plan re-stamp requirement applies. The v6
`constitution amend` transition also explicitly clears `scope.approval` as
part of the same belt-and-braces fix (v5's amend only replaced
`state.constitution`).

**Resolved once per invocation.** The CLI resolves the effective config
and its digest exactly once, at admission, and threads that snapshot
through the whole command — no handler re-reads config mid-command. An
overlay edited after a command starts cannot influence the evidence that
same invocation records; the digest stamped on evidence is the digest that
was actually gate-checked.

### `wddctl scope archive`

`delivered`-phase only: the ladder's final transition, and the only path
back to a fresh ladder. Since Task 6 of the epic-scoped-state plan, this is
no longer "write one JSON file" — it **moves the whole epic directory**,
`epics/<slug>/` (spec, design, plan, tasks, research, and the epic's own
overlay), to `archive/<slug>/`, with a `record.json` (the old archive
JSON's shape, unchanged — scope, tasks, intake, finalize, reconcile
including `pendingNotes`, leases, an event count, an archive timestamp)
written *inside* it before the move. `wddctl` never reads `record.json`
back; it is the durable record of a completed epic, since `state.json`
resets to a fresh setup shape immediately after.

```sh
$ wddctl scope archive --repo .
{
  "archived": ".wdd/archive/greeting-demo/record.json",
  "duplicate": false,
  "revision": 20,
  "scope": "SCOPE-greeting-demo"
}
```

```sh
$ find .wdd/archive -maxdepth 3 | sort
.wdd/archive
.wdd/archive/greeting-demo
.wdd/archive/greeting-demo/config.json
.wdd/archive/greeting-demo/design.md
.wdd/archive/greeting-demo/record.json
.wdd/archive/greeting-demo/spec.md
.wdd/archive/greeting-demo/tasks
.wdd/archive/greeting-demo/tasks/TASK-001-greeting.md
```

`record.json` itself (trimmed to the sections that prove the no-leak
guarantee — see [`artifact-schema.md`](artifact-schema.md) for the full
shape, and for `epicOrphans`/reserved-name detail):

```json
{
  "archivedAt": "2026-08-04T01:23:55Z",
  "eventCount": 18,
  "scope": {"id": "SCOPE-greeting-demo", "baseRef": "wdd/greeting-demo", "...": "approval included"},
  "intake": {"spec": {"...": "..."}, "research": {"...": "..."}, "design": {"...": "..."}},
  "finalize": {"review": {"...": "..."}, "verification": {"...": "..."}, "handoff": {"...": "..."}, "delivered": {"...": "..."}},
  "tasks": {"TASK-001-greeting": {"...": "..."}},
  "leases": {"TASK-001-greeting": {"status": "released", "...": "..."}},
  "reconcile": {"everyNMerges": 3, "mergesSinceCheckpoint": 1, "pendingNotes": []}
}
```

`state.json` afterward — a genuinely fresh setup phase, exactly as if
`wddctl init` had just been run against an already-ratified repository —
resets `scope: null`, `epic: null`, `tasks: {}`, `finalize` removed
entirely, `intake: {}` (a genuinely fresh ladder, never re-marked
`legacy`), `archivePending`/`archiveBlocked` both `null`, `reconcile`
fully reset (counters and pending notes both), `monitoring.observations`
cleared, and `leases` dropped. Governance (`constitution`) and the audit
trail (`events`, `telemetry`) are untouched — archiving retires an epic's
data, not the repository's own history or its ratified process. Refuses
outright before `delivered`:

```sh
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "create_epic",
      "command": "wddctl epic new --slug SLUG",
      "judgment": "...",
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "setup",
  "revision": 20,
  "scope": null
}
```

Slugs are immutable and unique against both `epics/` and `archive/` (see
"`wddctl epic new`" above), so this archived epic's name can never be
reused, and a fresh `epic new --slug greeting-demo` right after refuses
exactly the same way it would against a still-active epic of that name.

#### The archive transaction and its recovery

The move is a **recoverable transaction under the state lock**, per spec
Sec1 — not a single filesystem call the whole command hopes completes:

1. Write `record.json` **inside** `epics/<slug>/` — a reserved filename no
   other verb may create; the typed path resolver rejects it as an
   artifact ref outright, and `epic new` refuses a directory that already
   contains one.
2. Record `state.archivePending = {slug, sourceRevision, archivedAt,
   recordSha256}`. The record is a **deterministic function** of the state
   at `sourceRevision` (excluding the `archivePending` event itself) plus
   the one nondeterministic input, `archivedAt` — so regenerating it from
   an uncrashed state reproduces the exact bytes behind `recordSha256`;
   nothing here is unreconstructable.
3. One atomic rename: `epics/<slug>` → `archive/<slug>`.
4. Reset state to post-ratification setup (above), clearing `state.epic`
   and `archivePending`.

Every governed command and every read-only status command recovers from an
interrupted transaction automatically — there is no separate "resume
archive" verb to remember. A crash between any two steps leaves the
on-disk facts self-describing enough to finish or safely roll back: if
`record.json` exists but the rename never happened, it's re-verified (or
regenerated from the still-live state on a corrupt/missing hash) and the
rename retried; if the rename happened but the reset didn't, the *archived*
copy is re-verified and the reset completes; if a step-1 crash left a
`record.json` sitting in the still-active epic's own directory, it's
removed and nothing else happens (the transaction never began). Recovery
**never reads or touches anything under `archive/`** beyond the exact slug
named by the pending journal — a completed archive with no journal is the
success state, and pre-v6 archive files are never recovery's business
either. The full six-row matrix (including the durable
`archiveBlocked`/`archive_blocked` resting state below) is pinned row by
row in `tests/test_epics.py` — `ArchiveRecoveryRowOneTest`,
`ArchiveRecoveryRowTwoTest`, `ArchiveRecoveryHardErrorTest`,
`ArchiveRecoveryStrayRecordCleanupTest`, `RecoveryNeverReadsArchiveTest` —
each simulating the crash at the exact step it names.

**External collision** (something other than `wddctl` itself already
occupies `archive/<slug>/` when the rename is attempted) is the one row
that doesn't retry forever. Caught *before* the transaction ever starts,
it's a hard, immediate refusal — real, unedited output:

```sh
$ mkdir -p .wdd/archive/second-epic && echo "not ours" > .wdd/archive/second-epic/unrelated.txt
$ wddctl scope archive --repo .
wddctl: archive/second-epic/ already exists; slugs are unique across epics/ and archive/ -- this should be unreachable
$ rm -rf .wdd/archive/second-epic
$ wddctl scope archive --repo .
{
  "archived": ".wdd/archive/second-epic/record.json",
  "duplicate": false,
  "revision": 38,
  "scope": "SCOPE-second-epic"
}
```

If the SAME collision instead appears *mid-transaction* — after
`archivePending` was already journaled but before the rename lands, the
scenario `ArchiveCollisionBlockedResolutionE2ETest` exercises by crash-
injecting `os.rename` — recovery does not retry forever either: it removes
the generated `record.json`, clears the journal, and writes a durable
`state.archiveBlocked = {slug, collidingPath, at}`. `next` derives a
stable `archive_blocked` blocker from that field (so the promise survives
the journal's removal), `scope archive` itself refuses while it's set
(naming the colliding path), and re-running `scope archive` once the
collision is resolved clears the block and starts a clean transaction —
exactly the remedy shown above, just entered from a durable rather than an
immediate refusal.

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
  "worktree": "/path/to/repo/.worktrees/SCOPE-auth-refresh/TASK-001-token-types",
  "baseRef": "wdd/auth-refresh",
  "headSha": "...",
  "specPath": "tasks/TASK-001-token-types.md",
  "snapshot": "dispatch/TASK-001-token-types-1",
  "inputsRecorded": 2,
  "revision": 3
}
```

Implement the task in the printed `worktree` path. Worktrees live under
`config.json`'s `worktrees.root` (default `.worktrees`, resolved inside the
repository), and the location is derived rather than stored (see
"`config`" below and [`artifact-schema.md`](artifact-schema.md)). A relative
`worktrees.root` is gitignored at the repository root automatically, both by
`init` and the first time a worktree is actually created under it; an
absolute root outside the repository needs no such entry and gets none.

`start` also materializes an **attempt snapshot**: the task's brief and every
`context`-ref file are copied, read-only, into a fresh
`.wdd/dispatch/<task>-<attempt>/` directory (`snapshot` above, attempt
numbering per task, never overwritten), and the SOURCE files' digests are
recorded on the task (`inputsRecorded`). Workers and reviewers are handed
snapshot paths, never live controller files — see "Runners" below and
"Input-version binding" for why. A v5 scope with a task whose recorded
digests no longer match the current bytes is `inputs_changed`, not silently
re-bound; a legacy (pre-6b) scope still gets a snapshot (harmless and
useful) but records no `inputs` — there is no doctrine to bind evidence to.
`.wdd/dispatch/` is transient scratch, gitignored by `init`/`migrate`, and
also ensured the moment `dispatch/` itself is first created (`start` or
`dispatch`) -- so an existing install that predates the scratch dir still
gets the entry, even though it never runs `init`/`migrate --governance`
again. Never part of durable state.

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

### `rebind`

The remedy for **input-version binding**: the recorded human decision that
a task's already-collected work and evidence still stand despite its
recorded input digests no longer matching the current bytes.

```sh
wddctl rebind --task TASK-001-token-types --by NAME --repo .
```

```json
{"duplicate": false, "inputsRecorded": 2, "revision": 14}
```

Every started task's brief and `context`-ref files are digest-pinned at
`start` (see `start` above). If a re-approved plan (`plan apply
--approved-by`) changes one of THIS task's own inputs after it started, the
task's in-flight attempt and any unmerged review/verification evidence are
no longer trustworthy — `wddctl next` reports it as an `inputs_changed`
action (below), and every task-targeted governed verb aimed at that task
(`submit`, `review record`/`collect`, `verify record`/`collect`, `refresh`,
`merge`, `dispatch --task`) refuses with a message naming both remedies.
`rebind` is one of the two: it re-records the task's input digests against
the CURRENT bytes and marks the moment with a `task.rebound` event (see
[`artifact-schema.md`](artifact-schema.md) for the exact event-log shape)
— the attempt and its evidence are not redone, only re-pinned. The other
remedy is a fresh `start` (re-materializes and re-records, which is genuine
re-dispatch, not a rebind).

Refuses ("nothing to rebind") when the task has no recorded `inputs` at all
(a legacy scope, or one that never started) or its recorded digests already
match current bytes — there is nothing to re-pin. **Merged evidence is
history**: a `done`/`cancelled` task is never gated by input-version binding
and `rebind` refuses the same way for it — evidence for already-merged work
is judged by finalize's whole-epic review against the CURRENT spec, not
retroactively unmade by a later edit to a brief nobody will read again.
Rebinding does not touch a task's recorded `review`/`verification` evidence
— the decision is that the existing work (including any review already
collected on it) stands, not that it is re-judged. See "Runners" below for
a captured transcript of the full `inputs_changed` → `rebind` cycle.

`inputs_changed` (surfaced by `wddctl next`, per in-flight task) is the
per-task twin of the scope-wide `plan_drift` blocker (see "Plan drift"
above): `plan_drift` covers the WHOLE composite (any brief or context file,
across every task) and empties `next`'s action list until re-stamped;
`inputs_changed` covers only the ONE task whose own recorded digests
mismatch, surfaces alongside other tasks' unaffected actions, and is
remedied by `rebind` or re-`start`, not by `plan apply`. Editing a task's
own input fires both at once (the composite covers everything, so it always
drifts too) — `require_fresh_intake`'s `plan_drift` refusal fires first at
the governed-verb chokepoint, so the documented remedy order is: re-stamp
the plan first, then rebind (or re-dispatch) the named task.

### `dispatch`

Probe a candidate or already-configured runner command, or dispatch one
task through a configured runner — see "Runners" below for the full
picture (config shape, registration order, and a captured transcript
against a stub runner).

```sh
wddctl dispatch --probe-command '["codex", "exec", "--cd", "{worktree}", "{prompt}"]'
wddctl dispatch --probe RUNNER-NAME --repo .
wddctl dispatch --task TASK-001-token-types --role worker|reviewer --repo . [--timeout SECONDS]
```

Exactly one of `--probe-command`, `--probe`, or `--task` is required.
`--probe-command` is deliberately **ungoverned** (an explicit candidate the
operator just typed, never yet config); `--probe NAME` and `--task ...`
are both governed like any other execution verb, and `--task` is
additionally input-binding-gated like the other task-targeted verbs above.

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

Convert schema-v2, v3, or v4 controller state to the current v5 schema.
Dry-run first; `--apply` writes a `.v2.bak` backup beside the state file
before converting.

```sh
wddctl --state .wdd/state.json migrate --dry-run
wddctl --state .wdd/state.json migrate --apply
```

`--state` is a global option, so it goes before the subcommand.

All sources (v2, v3, v4) chain through an intermediate v4 shape, then the
v4 → v5 step is a pure schema bump plus `intake: {"legacy": true}` — a
wholesale exemption from the intake ladder (spec, research, design) and
plan-approval composite enforcement. Waves are dropped (scheduling is
derived from dependencies and conflict domains), every task defaults to
`risk: normal`, and recorded worktree paths are cleared because the location
is derived per checkout. Schema v2 required review for every task, so
`reviewPolicy` becomes `always` by default to preserve that obligation;
pass `--review-policy risk_based` to loosen it deliberately. Reading v2–v4
state without migrating fails with a message pointing here.

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

### `--version`

A global flag (works anywhere, before or independent of a subcommand),
backed by the `VERSION` file at the repository root — the single source of
truth `scripts/install_wave_delivery.py` copies next to the installed
package:

```sh
$ wddctl --version
wddctl 0.1.0 (b88d6d9)
```

`<version> (<short-sha-if-known>)`, via argparse's built-in version action.
The short SHA is best-effort (present when the installed copy can resolve
its own Git history; absent, not fabricated, otherwise). The schema
version (currently v6) and the CLI version are independent numbers —
`migrate` keys off the schema, never off `--version`'s output. `VERSION`
itself is bumped and tagged by a GitHub Actions workflow on push to
`main` (Conventional Commits classify the bump; `scripts/release.py`
carries the parser and the compare-and-swap tag logic) — no manual
version-bump step in the normal contribution flow.

### `doctor`

Optional capability report: Python version, and whether `git`, `gh`, `acli`,
`codex`, `claude` are on `PATH`. The core controller works with none of the
optional ones present. Also reports governance health —
`governance.configPresent`, `governance.configValid` (with an `error` string
on failure), and `governance.drift` (`null`, or the same shape `next` emits
in its `governance_drift` blocker) — computed from `.wdd/config.json` and
the current state if one exists. The report also carries `"version"` (the
same string `--version` prints, sha suffix excluded) and `"epicOrphans"` —
a list of epic slugs whose directory exists but whose crash left
`state.epic` unset (see "`wddctl epic new`" above for the idempotent
`epic new`-again remedy); empty when there's nothing to report. Doctor
only reports; it never refuses.

```sh
wddctl doctor [--json]
```

When `config.json` has a non-empty `runners` map (see "Runners" below),
the report grows a `runners` object, one entry per configured runner name,
each `{"argv0", "available"}` — the same `shutil.which` check idiom as the
`git`/`gh`/`acli`/`codex`/`claude` probes above, not a functional probe
(that's `wddctl dispatch --probe`, which actually execs the command). A
malformed entry whose `argv0` is itself an unsubstituted
`{worktree}`/`{prompt}`/`{logfile}` placeholder (which never belongs in
the binary position) is reported `{"unresolvable": true}` instead of a
misleading "not found". The key is absent entirely when there is no
config, an invalid one, or an empty `runners` map — nothing to report.

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

## Runners

A worker is file-in, file-out: worktree + brief + context in, commits +
status token out. Nothing requires it to be a subagent of the controller's
own harness. The optional `runners` map in `.wdd/config.json` makes a
`model` value (a task's `model`/`reviewModel` override, or the risk-tiered
`models.implementation`/`models.review` config) resolvable to an external
agent CLI instead of harness-native dispatch:

```json
"runners": {
  "qwen-local": {"command": ["pi", "--headless", "--model", "qwen3.6",
                              "--cd", "{worktree}", "-p", "{prompt}"]},
  "codex":      {"command": ["codex", "exec", "--cd", "{worktree}", "{prompt}"]}
}
```

**Resolution**: a `model` value naming a configured runner dispatches
through it; any other value is harness-native, exactly as before this
feature existed — fully backward compatible, and nothing about the
non-runner case changes. `{worktree}`/`{prompt}`/`{logfile}` are
substituted anywhere they appear in any argv element (substring
replacement, not whole-element matching), never `str.format` — a runner
command legitimately containing other brace text is never mistaken for a
placeholder. `{logfile}` resolves to a sibling path distinct from wddctl's
own `<task>-<role>-<n>.log` capture (`<task>-<role>-<n>-runner.log`) — a
runner that keeps its own transcript at `{logfile}` is never clobbered by
wddctl's own post-run write of the captured stdout+stderr.

**Registration order — probe, then config, then governance**: probing
must not require a runner to already be ratified config (that would be a
governance cycle to register anything), and must not silently execute an
unapproved command either.

1. `wddctl dispatch --probe-command '[...]'` — **ungoverned**: proves an
   explicit candidate command (one the operator just typed or approved in
   conversation, never yet config) by exec'ing it in a scratch temp
   directory against a canned trivial prompt (`"Reply with exactly:
   DONE"`). `ok` requires both a zero exit code AND the trailing
   non-empty output line reading exactly `DONE` — a command that exits 0
   without ever answering the prompt has proven nothing.
2. `wddctl config set runners '{"NAME": {"command": [...]}}'` registers it.
3. `wddctl constitution amend --by NAME` re-signs governance, since step 2
   edited `config.json` after ratification.

A passing probe records `probes[sha256(command)] = {"at", "ok": true}` in
`state.json` as an **ungoverned observation** — the same precedent
`monitor`'s own observation writes use — the moment state exists (before
that, `--probe-command` reports the result but notes registration will
re-probe once state exists, since there is nothing yet to record onto).
`wddctl dispatch --task ID --role worker|reviewer` — the actual task
dispatch — refuses outright unless the RATIFIED runner's exact command
digest has a passing probe record: editing the command after probing (even
just appending a flag) changes the digest and re-refuses, on purpose — the
guarantee follows the exact bytes a probe proved, not the runner's name.
`wddctl dispatch --probe NAME` re-verifies an already-ratified runner by
name and is itself **governed** (it executes config-loaded commands, so it
refuses under governance/intake/plan drift like any other execution verb);
the deliberately ungoverned path is only ever `--probe-command`.

**Dispatch** assembles the packet, execs the resolved runner once in the
task's worktree, and captures its output — no streaming, no interactive
sessions, no supervision, no retries, no timeout management beyond a
single optional `--timeout SECONDS` (these non-goals are load-bearing, not
an oversight: the child agent's own sandboxing, permissions, and timeouts
are its runner command's business, authored per machine by the operator).
The packet differs by `--role`:

- **worker**: brief + context paths from the task's own recorded
  **attempt snapshot** (see `start` above — never live controller files),
  the brief's `## Deliverable` section, and the status-token contract
  (`DONE` / `DONE_WITH_CONCERNS` / `NEEDS_CONTEXT` / `BLOCKED` on the
  trailing output line). The result reports `statusToken` — whichever of
  those appeared, or `null` if none did (a runner-side failure, reported,
  never raised).
- **reviewer**: a FRESH attempt snapshot (materialized at dispatch time,
  not reused from the worker's), plus the frozen `baseSha`/`headSha` of
  the diff under review, and the exact same `wddctl_review_result` JSON
  contract an internal reviewer already speaks (see "External result file
  envelopes" above) — no new evidence format is invented. Only a genuinely
  successful exit (code 0) is held to the JSON contract; a nonzero exit or
  timeout is reported as a plain failure, same as a worker's. A valid
  result is written to a sibling `<task>-reviewer-<n>-result.json` file,
  validated by the identical function `review collect` itself uses (SHA
  consistency with the frozen range included), ready to hand straight to
  `wddctl review collect --result <path>`.

**Log policy** (`.wdd/dispatch/`, spec §6): transient scratch, never
committed — `init` and `migrate --governance` write a `.wdd/.gitignore`
entry for `dispatch/`, and `start`/`dispatch` ensure the same entry again the
moment they first create the directory (idempotent, content-preserving),
covering an existing install that predates the scratch dir. The directory is
`0700`. Attempt snapshot files inside it are `0400` (read-only — see `start`
above); dispatch prompts/logs/results are `0600`. Filenames use attempt
numbering per task+role (`<task>-<role>-<n>.log`, never overwritten) with
task IDs sanitized to `[A-Za-z0-9._-]`. The result payload carries only a
bounded 4KB tail of the log; the full output lives only in the file on disk.
Raw agent output can contain anything — it gets file permissions and a
gitignore entry, not a place in durable state.

`doctor` reports, for every configured runner, whether its command's
`argv[0]` is present on `PATH` (see `doctor` above) — a cheap first check
before ever probing.

### Transcript: probe, register, dispatch, `inputs_changed`, `rebind`

Real, unedited output from a scratch repository — `wddctl init` through
the intake ladder, merge surface `local` — with `PATH` untouched: the
runner command below names `tests/fixtures/fake-runner/fake-runner`
directly (a stub that reads its `--prompt` file and prints a canned
`DONE`, or — with `FAKE_RUNNER_REVIEW_RESULT=1` set — a canned
`wddctl_review_result` JSON object; see the fixture's own docstring), by
absolute path rather than a real agent CLI, so this is honestly labeled a
**stub runner**, not a live one.

```sh
$ wddctl dispatch --probe-command '["/path/to/fake-runner", "--prompt", "{prompt}", "--worktree", "{worktree}", "--logfile", "{logfile}"]'
{
  "digest": "sha256:9c962d10736ce2b85989409be785f28bc2f6be169f25455bdb8ef93bd8ba6d55",
  "exitCode": 0,
  "ok": true,
  "recorded": true,
  "tokenSeen": true,
  "wallMs": 48
}
$ wddctl config set runners '{"stub-runner": {"command": ["/path/to/fake-runner", "--prompt", "{prompt}", "--worktree", "{worktree}", "--logfile", "{logfile}"]}}'
{"openQuestions": 0, "path": "runners", "value": {"stub-runner": {"command": ["...", "--prompt", "{prompt}", "--worktree", "{worktree}", "--logfile", "{logfile}"]}}}
$ wddctl constitution amend --by ivo
{"decisionFingerprint": "sha256:be9c1296657fce4f701d3783cab08ab28c7f68785a3af3373271e1547c7d0497", "duplicate": false, "revision": 6}
```

A plan applied with `"model": "stub-runner", "reviewModel": "stub-runner"`
on its one task, then started (the snapshot from `start` above), dispatches
cleanly through both roles:

```sh
$ wddctl dispatch --task TASK-001-greeting --role worker --repo .
{
  "digest": "sha256:9c962d10736ce2b85989409be785f28bc2f6be169f25455bdb8ef93bd8ba6d55",
  "exitCode": 0,
  "log": "/path/to/repo/.wdd/dispatch/TASK-001-greeting-worker-1.log",
  "model": "stub-runner",
  "role": "worker",
  "statusToken": "DONE",
  "tail": "fake-runner: task complete\nDONE\n",
  "task": "TASK-001-greeting",
  "timedOut": false,
  "wallMs": 72
}
```

(The stub never touches Git — the worker's actual commit still has to
happen, same as a harness-native worker's would, before `wddctl submit`.)

```sh
$ wddctl submit --task TASK-001-greeting --repo .
{"branch": "task/TASK-001-greeting", "duplicate": false, "event": "task.pr_recorded", "headSha": "5368b7c...", "pr": "branch:task/TASK-001-greeting@5368b7c12ae9", "revision": 11, "status": "in_progress", "task": "TASK-001-greeting"}
$ FAKE_RUNNER_REVIEW_RESULT=1 wddctl dispatch --task TASK-001-greeting --role reviewer --repo .
{
  "digest": "sha256:9c962d10736ce2b85989409be785f28bc2f6be169f25455bdb8ef93bd8ba6d55",
  "exitCode": 0,
  "findings": 0,
  "log": "/path/to/repo/.wdd/dispatch/TASK-001-greeting-reviewer-1.log",
  "model": "stub-runner",
  "resultPath": "/path/to/repo/.wdd/dispatch/TASK-001-greeting-reviewer-1-result.json",
  "role": "reviewer",
  "tail": "{\"schemaVersion\": 1, \"kind\": \"wddctl_review_result\", \"task\": \"TASK-001-greeting\", \"baseSha\": \"657e739...\", \"headSha\": \"5368b7c...\", \"reviewer\": \"fake-reviewer\", \"findings\": []}\n",
  "task": "TASK-001-greeting",
  "timedOut": false,
  "wallMs": 59
}
$ wddctl review collect --task TASK-001-greeting --result .wdd/dispatch/TASK-001-greeting-reviewer-1-result.json --repo .
{"duplicate": false, "revision": 12}
```

Now the `inputs_changed` → `rebind` cycle: a `context`-ref file this task
depends on is edited, the plan is re-stamped (clearing the scope-wide
`plan_drift` this always also causes — see "Plan drift" above — so what's
left is purely this task's own stale digest), and `next` surfaces the
per-task action:

```sh
$ echo "contract v2 -- edited" > .wdd/shared-context/contract.md
$ wddctl plan apply --plan plan.json --repo . --approved-by ivo2
{"approvedBy": "ivo2", "created": false, "diff": {"added": [], "removed": [], "scope": {}, "updated": []}, "dryRun": false, "revision": 13, "scope": "SCOPE-greeting-demo", "unchanged": true}
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "inputs_changed",
      "actual": "sha256:f27055290c4cae7357132615a90b7ced99dc0445fe05e8d7213dbfa9e19ebe3e",
      "judgment": "shared-context/contract.md changed since task TASK-001-greeting's attempt was dispatched: its recorded input digest no longer matches the current bytes, so unmerged review/verification evidence for it is no longer trustworthy. If a plan_drift blocker is also present above, re-stamp the plan first ('wddctl plan apply --approved-by NAME'); either way, then decide: rebind to accept the existing work as still valid, or discard it and re-dispatch a fresh attempt (block, unblock, then start).",
      "path": "shared-context/contract.md",
      "recordWith": "wddctl rebind --task TASK-001-greeting --by NAME --repo .",
      "recorded": "sha256:6ea6486aa832983fe38184095afa6ed73a406105470003d377bf6deabcb3be96",
      "task": "TASK-001-greeting"
    }
  ],
  "blockers": [],
  "revision": 13,
  "scope": "SCOPE-greeting-demo",
  "truncated": false
}
$ wddctl verify record --task TASK-001-greeting --status passed
wddctl: inputs changed for task TASK-001-greeting: shared-context/contract.md recorded sha256:6ea6486a..., now sha256:f2705529...; run 'wddctl rebind --task TASK-001-greeting --by NAME --repo .' to accept the existing work as still valid, or re-dispatch a fresh attempt (block, unblock, then start)
$ wddctl rebind --task TASK-001-greeting --by ivo --repo .
{"duplicate": false, "inputsRecorded": 2, "revision": 14}
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "run_verification",
      "recordWith": "wddctl verify record --task TASK-001-greeting --status passed --command '<verification command>'",
      "task": "TASK-001-greeting"
    }
  ],
  "blockers": [],
  "revision": 14,
  "scope": "SCOPE-greeting-demo",
  "truncated": false
}
```

The review evidence collected before the edit (`revision 12` above) is
still exactly what `verify record` and `merge` proceed on — `rebind` never
touched it; it only re-pinned the digest that was blocking everything else.

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

`inputs_changed` sits outside this linear ladder: it can interrupt any
in-flight (non-terminal) task, at any gate, whenever a plan re-approval
touched that task's OWN recorded brief/context digests (see `rebind` above,
under "Commands"). It suppresses that task's own ladder action
for the duration — `next` emits `inputs_changed` instead, not alongside —
while every other task's actions are untouched; `rebind` or a fresh `start`
is what returns the task to its ladder position.

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
`spec.md`'s acceptance criteria. Same P1/P2/P3 vocabulary and
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

Two contracts, chosen by the scope's own legacy-ness — never by which
flags happen to be passed:

**v5 non-legacy scopes** (the ladder was walked) record multi-command
evidence in **one atomic `--results` call**: a JSON array naming, in
order, every entry of the ratified global `verification.commands` then the
scope's `intake.design.deliverableCommand`. Append semantics are
forbidden — there is no partial-evidence state. Completeness is validated
exactly: missing, extra, or reordered entries are all refused and named.
Overall `status` is `passed` iff every entry passed.

```sh
wddctl finalize verify record --results '[{"command":"true","status":"passed"}, {"command":"...deliverable command...","status":"passed"}]' --repo .
```

```sh
$ wddctl finalize verify record --results '[{"command": "true", "status": "passed"}, {"command": "python3 -c \"from src.greeting import greet; assert (\\\"Ivo\\\" in greet(\\\"Ivo\\\"))\"", "status": "passed"}]' --repo .
{
  "duplicate": false,
  "headSha": "8886d8e1fd48df63a47e588dfa1561f42eb8e9f0",
  "revision": 14,
  "status": "passed"
}
```

`wddctl next` names the exact required command list for you (with
placeholder `"passed"` statuses to fill in), so a caller never has to
reconstruct it by hand:

```sh
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "final_verification",
      "judgment": "run full verification against the current epic branch head and record the result",
      "recordWith": "wddctl finalize verify record --results '[{\"command\": \"true\", \"status\": \"passed\"}, {\"command\": \"python3 -c \\\"from src.greeting import greet; assert (\\\\\\\"Ivo\\\\\\\" in greet(\\\\\\\"Ivo\\\\\\\"))\\\"\", \"status\": \"passed\"}]' --repo .",
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "finalize",
  "revision": 13,
  "scope": "SCOPE-greeting-demo"
}
```

A missing entry (here, the deliverable command omitted) is refused and
named:

```sh
$ wddctl finalize verify record --results '[{"command": "true", "status": "passed"}]' --repo .
wddctl: finalize verify record --results must name exactly the required commands, in order (the ratified global verification.commands then the scope's deliverable command): missing: ['python3 -c "from src.greeting import greet; assert (\\"Ivo\\" in greet(\\"Ivo\\"))"']
```

Legacy-shaped arguments are refused on a v5 scope, and vice versa:

```sh
$ wddctl finalize verify record --status passed --command "true" --repo .
wddctl: this is a v5 scope; record multi-command evidence with --results '[{"command":..., "status":...}, ...]' (--status/--command is the legacy contract)
```

**Legacy scopes** (`intake.legacy`) keep the original single-command
contract, unchanged bit-for-bit — `--results` is refused there:

```sh
wddctl finalize verify record --status passed|failed|unavailable --command CMD [--justification "..."] --repo .
```

`--status unavailable` requires `--justification` (or falls back to a
configured `verification.unavailableJustification`) — skipping
verification silently is no more acceptable at scope granularity than it
is per task. Every read site (`finalize status`, the handoff summary)
normalizes both shapes to the same `[{command, status}, ...]` view via
`verification_commands()`, so a legacy record reads as the one-entry list
it always was.

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

Real, unedited output from the same continuous scratch repository as "The
intake ladder" above — `wddctl init` through the ladder, through a single
task (default `reviewPolicy: risk_based`, `risk: normal`, so no
task-level review gate either), merged, then the finalize ladder driven
straight through to `delivered`. No network, no `gh`: `merge.surface
local`. This is a genuine v5 (non-legacy) scope, so final verification
uses the `--results` contract described above — note `final_review`'s
judgment naming the exact acceptance-criteria count (`AC-1..AC-2`) and
`design.md`'s epic deliverable, both sourced from the recorded intake
ladder.

```sh
$ wddctl status --json
{"finalize": {}, "phase": "finalize"}
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "final_review",
      "judgment": "dispatch a reviewer against the whole epic branch diff, per wdd-review's final-review contract, checked against spec.md; walk spec.md's acceptance criteria AC-1..AC-2 in order and confirm design.md's epic deliverable statement is observably true",
      "recordWith": "wddctl finalize review record --reviewer NAME --findings '[]' --repo .",
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "finalize",
  "revision": 12,
  "scope": "SCOPE-greeting-demo"
}
$ wddctl finalize review record --reviewer ivo --findings '[]' --repo .
{
  "duplicate": false,
  "headSha": "8886d8e1fd48df63a47e588dfa1561f42eb8e9f0",
  "outcome": "passed",
  "revision": 13
}
$ wddctl next --repo .
{
  "actions": [
    {
      "action": "final_verification",
      "judgment": "run full verification against the current epic branch head and record the result",
      "recordWith": "wddctl finalize verify record --results '[{\"command\": \"true\", \"status\": \"passed\"}, {\"command\": \"python3 -c \\\"from src.greeting import greet; assert (\\\\\\\"Ivo\\\\\\\" in greet(\\\\\\\"Ivo\\\\\\\"))\\\"\", \"status\": \"passed\"}]' --repo .",
      "task": "-"
    }
  ],
  "blockers": [],
  "phase": "finalize",
  "revision": 13,
  "scope": "SCOPE-greeting-demo"
}
$ wddctl finalize verify record --results '[{"command": "true", "status": "passed"}, {"command": "python3 -c \"from src.greeting import greet; assert (\\\"Ivo\\\" in greet(\\\"Ivo\\\"))\"", "status": "passed"}]' --repo .
{
  "duplicate": false,
  "headSha": "8886d8e1fd48df63a47e588dfa1561f42eb8e9f0",
  "revision": 14,
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
  "revision": 14,
  "scope": "SCOPE-greeting-demo"
}
$ wddctl finalize handoff --repo .
{
  "duplicate": false,
  "headSha": "8886d8e1fd48df63a47e588dfa1561f42eb8e9f0",
  "instructions": "push wdd/greeting-demo to your remote (e.g. 'git push origin wdd/greeting-demo') and open a pull request into main yourself; wddctl does not perform this on the local surface. Once the human merge lands, run 'wddctl finalize delivered --by NAME --repo .' to record it.",
  "pr": null,
  "revision": 15,
  "targetBranch": "main"
}
```

The human merge is not simulated JSON — it is a real `git merge` into the
target branch (`main`, here), performed exactly the way an operator would
click "Merge" on a PR or run it from their own terminal, entirely outside
`wddctl`:

```sh
$ git checkout -q main
$ git merge --no-ff -q wdd/greeting-demo -m "merge scope SCOPE-greeting-demo into main"
$ wddctl finalize delivered --by ivo --repo .
{
  "by": "ivo",
  "duplicate": false,
  "headSha": "8886d8e1fd48df63a47e588dfa1561f42eb8e9f0",
  "revision": 16,
  "targetBranch": "main"
}
$ wddctl next --repo .
{"actions": [], "blockers": [], "phase": "delivered", "revision": 16, "scope": "SCOPE-greeting-demo"}
```

`finalize status`'s verification carries the v5 `commands` list instead of
the legacy singular `command`/`status` pair:

```sh
$ wddctl finalize status
{
  "finalize": {
    "review": {
      "at": "2026-08-01T19:58:16Z",
      "findings": [],
      "headSha": "8886d8e1fd48df63a47e588dfa1561f42eb8e9f0",
      "outcome": "passed",
      "reviewer": "ivo"
    },
    "verification": {
      "at": "2026-08-01T19:58:27Z",
      "commands": [
        {"command": "true", "status": "passed"},
        {"command": "python3 -c \"from src.greeting import greet; assert (\\\"Ivo\\\" in greet(\\\"Ivo\\\"))\"", "status": "passed"}
      ],
      "headSha": "8886d8e1fd48df63a47e588dfa1561f42eb8e9f0",
      "status": "passed"
    }
  },
  "phase": "finalize"
}
```

(`finalize status` above still reports `"phase": "finalize"` because it
was captured just before `finalize delivered`; re-running it after the
merge above adds a `delivered: {at, by, headSha}` block and the phase
flips to `"delivered"`, exactly as `next` shows.)

From here, `wddctl scope archive --repo .` is the ladder's rollover — see
"The intake ladder" above for its captured output, which continues this
exact scratch session through to a fresh `agree_spec`.

### Transcript: a blocked final review

A P1 against `spec.md`'s acceptance criteria blocks the ladder
exactly like a task-level P1 blocks merge:

```sh
$ wddctl finalize review record --reviewer "codex-review" --repo . \
    --findings '[{"severity":"P1","summary":"acceptance criterion missing: no test proves greeting() returns exactly \"hello\"","file":"spec.md","line":0}]'
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
          "file": "spec.md",
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
- **Archive is a crash-safe transaction, never a partial move.** `scope
  archive`'s directory move (`epics/<slug>` → `archive/<slug>`) is
  recoverable at every step, verified by re-derivable byte-identical
  record generation rather than trust, and two epics never collide on a
  path — see "The archive transaction and its recovery" above.
