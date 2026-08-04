# WDD artifact schema

WDD's durable state lives under `.wdd/` in the target repository. Since
schema v6 (the epic-scoped-state plan), an epic's own artifacts —
spec/design/plan/tasks/research — live in a **named, self-contained
directory** rather than as global singletons; only genuinely cross-epic
things (governance, machine config, durable shared context) stay flat at
the `.wdd/` root:

```text
.wdd/
  constitution.md          # global; human-authored governance; ratified via wddctl
  config.json               # global machine config (branching, verification, models, ...)
  state.json                # wddctl-owned; never hand-edit; schema v6
  state.md                  # generated projection (wddctl render)
  shared-context/           # global; durable discoveries, cross-epic by definition
  epics/<slug>/              # the active epic's own directory (see "Epic directories" below)
    config.json               # the epic's sparse config overlay; may be {}
    spec.md                    # human-authored; ratified via wddctl intake spec
    design.md                  # human-authored; ratified via wddctl intake design
    plan.json                  # the only planning input for this epic
    tasks/<TASK-ID>.md        # worker briefs, referenced by each task's specPath
    research/                  # research-rung artifacts specific to this epic
  archive/<slug>/            # a delivered epic, moved wholesale by `wddctl scope archive`
    record.json                # the state record at archive time (see below)
    ...                         # the rest of the archived epic's own directory content
  dispatch/                 # wddctl-owned; transient scratch, gitignored, never committed
  .gitignore                 # wddctl-owned; written by init/migrate, and re-ensured whenever dispatch/ is first created, to ignore dispatch/
```

`VERSION` — the CLI's own version identity, unrelated to the schema
version above — lives at the **repository root**, not under `.wdd/`; see
"`--version`" in [`docs/wddctl.md`](wddctl.md).

Everything here is either human-authored Markdown/JSON that `wddctl` reads,
or a `wddctl`-generated file that must not be hand-edited.

## Epic directories

`wddctl epic new --slug SLUG` (see [`docs/wddctl.md`](wddctl.md)) is the
first action of every epic: it creates `epics/<slug>/` with an empty
overlay and records `state.epic = <slug>` (a top-level `state.json` field,
`null` when no epic is active) — one locked mutation, so a crash can never
leave the directory and `state.epic` disagreeing about whether an epic
exists, other than the one idempotent crash-orphan shape `epic new`
adopts on re-run.

- **Slugs are the canonical scope identity.** `[a-z0-9][a-z0-9-]{1,63}`,
  unique against both `epics/` and `archive/` — immutable; there is no
  rename verb, and retiring one means archiving it. The scope id is always
  derived as `SCOPE-<slug>`; `plan apply` rejects any other id on a v6
  state.
- **Typed path namespaces, one resolver.** An artifact reference is
  resolved lexically, never by existence probing: a ref beginning
  `shared-context/` always means the global directory; `tasks/`,
  `research/`, `spec.md`, `design.md`, `plan.json` always mean the
  **active** epic's directory. Absolute paths, `..` segments, and refs
  beginning `epics/`, `archive/`, or `dispatch/` are rejected outright, as
  is the reserved name `record.json` (below). Every consumer — plan
  hashing, intake recording/drift, handover materialization, input
  binding, snapshot resolution — goes through this one resolver
  (`wave_delivery/paths.py`); nothing resolves paths its own way. Because
  refs stay namespace-relative in both `plan.json` and `state.json`, a
  task brief or plan file is copy-portable between epics without editing a
  single path.
- **`epics/<slug>/config.json`** is the epic's sparse config overlay — see
  "Epic configuration overlay" in [`docs/wddctl.md`](wddctl.md) for
  resolution order, the digest function, purpose-projected evidence
  digests, and the drift/cascade story. Only the allowlisted dotted leaves
  may appear in it: `models.planning`, `models.implementation`,
  `models.review`, `verification.commands`,
  `verification.unavailableJustification`, `merge.surface`, `riskRules`,
  `review.policy`. An overlay with any other key is refused by name at
  every entry point (load, `config set --epic`, `intake configure`
  approval) — machine-bound and repository-authority settings (`runners`,
  `worktrees.root`, branching) stay under constitution approval only.
- **`record.json` is a reserved filename.** No verb other than `scope
  archive` may create one; the typed resolver refuses it as an artifact
  ref; `epic new` refuses to adopt a directory that already contains one
  (it reads as an in-flight or completed archive transaction, not a fresh
  or orphaned epic).
- **`archive/<slug>/`** is where a delivered epic ends up — see "The
  archive transaction" below for how it gets there and what recovers a
  crash mid-move.

## `constitution.md`

Free-form governance Markdown. `wddctl` does not parse its content — it
tracks ratification as a separate event in `state.json`, keyed to a decision
fingerprint the human (or the constitution skill) ties to the text that was
actually approved. Execution stays blocked (`wddctl next` returns only a
`constitution_unratified` blocker) until `wddctl constitution ratify` is
called.

Conventional body sections:

- **Branching** — target branch, base branch naming convention (a scope's
  `baseRef`), task branch naming (`task/<TASK-ID>`, assigned by `wddctl
  start`).
- **Verification** — the project's real verification command(s) (tests,
  linters, type checks); what to record when no automated check applies.
- **Review policy** — which `reviewPolicy` this repo defaults to
  (`always` / `risk_based` / `none`), and which task categories should be
  marked `"risk": "high"` under `risk_based`.
- **Model aliases** — any model choices the user wants remembered, so later
  agents don't have to re-infer them.
- **Merge policy** — whether the controller merges automatically or a human
  approves each merge; the scope's default `reconcileEveryNMerges` and
  `maxConcurrent`.
- **Governance** — how to amend: edit, get explicit re-approval, re-ratify.
  A re-ratification changes behavior immediately for any scope not yet
  started.

`wddctl constitution probe --root .` gathers evidence from the repository
(instruction files, detectable verification commands, current branch) into a
proposal JSON file to speed up filling these sections in — it never ratifies
anything itself. See [`docs/wddctl.md`](wddctl.md) for the probe/ratify/status
commands.

## The intake ladder: `spec.md` and `design.md`

Between `epic new`/`intake configure` and `plan apply`, two more
human-authored Markdown files — `epics/<slug>/spec.md` and
`epics/<slug>/design.md` — are agreed and recorded via `wddctl intake
spec`/`design` (see "The intake ladder" in
[`docs/wddctl.md`](wddctl.md)). Both are fingerprinted at approval time
(SHA-256 over the exact bytes), so an edit afterward is drift, not a free
edit.

### `spec.md`

Four required `## ` sections: **Goal**, **In scope**, **Out of scope**,
**Acceptance criteria**. The Acceptance criteria section must be **wholly
numbered**: every checklist line matches `- [ ] AC-<n>: <text>`, the
numbers unique and contiguous from 1, with no unnumbered checklist line
anywhere in the section — final review walks `AC-1..AC-<N>` and nothing
else. `wddctl intake spec` records the exact count `N` as
`intake.spec.criteria`.

```markdown
# Spec

## Goal

Ship a greeting helper that says hello to the caller by name.

## In scope

- `greet(name)` function returning a friendly greeting string.

## Out of scope

- Internationalization / localization.

## Acceptance criteria

- [ ] AC-1: `greet("Ivo")` returns a string containing "Ivo".
- [ ] AC-2: `greet("")` raises a clear error instead of returning a blank greeting.
```

A plan task's `context` ref of the form `spec.md#AC-<n>` (see `tasks[]`
below) is how a task declares which acceptance criterion it discharges;
`plan lint`'s `missing_criteria` check looks for a full match against this
exact pattern.

### `design.md`

Four required `## ` sections: **Components**, **Interfaces**,
**Integration surfaces**, **Epic deliverable**. `wddctl intake design`
requires research to be recorded first, and requires
`--deliverable-command` — a non-empty command that proves the epic
deliverable, recorded alongside the design fingerprint as
`intake.design.deliverableCommand` and folded into `finalize verify
record`'s required command list (see "The finalize phase" in
[`docs/wddctl.md`](wddctl.md)).

**Integration surfaces** lists the paths the scope's tasks are expected to
own, one per bulleted line in the convention `` - `path` — owned by: ... ``
— `plan lint`'s `unowned_surface` check parses exactly this shape and warns
when a listed path isn't covered by any task's `conflictDomains`.

```markdown
# Design

## Components

- `greeting`: the `greet(name)` helper and its error path.

## Interfaces

- `greeting`: consumes nothing external, produces `greet(name) -> str`.

## Integration surfaces

- `src/greeting.py` — owned by: TASK-001-greeting

## Epic deliverable

`python3 -c "from src.greeting import greet; assert 'Ivo' in greet('Ivo')"`
succeeds against the merged epic branch.
```

## `plan.json`

The single planning input for the active epic — conventionally staged at
the repository root and applied against `epics/<slug>/plan.json` inside
`.wdd/` (`plan apply`'s `--plan` flag takes any path; the file it *writes
into* `.wdd/` is always the active epic's own `plan.json`). `wddctl plan
apply` reads it and creates or updates a scope in `state.json`;
re-applying is safe and diffs the new plan against the current state.
`scope.id` must be exactly `SCOPE-<slug>` for the active epic — a plan
naming any other id is rejected at apply.

```json
{
  "schemaVersion": 1,
  "kind": "wdd_plan",
  "scope": {
    "id": "SCOPE-auth-refresh",
    "baseRef": "wdd/auth-refresh",
    "maxConcurrent": 3,
    "reviewPolicy": "risk_based",
    "reconcileEveryNMerges": 3
  },
  "tasks": [
    {
      "id": "TASK-001-token-types",
      "title": "Token type contract",
      "specPath": "tasks/TASK-001-token-types.md",
      "risk": "high",
      "dependsOn": [],
      "conflictDomains": ["src/auth/**", "src/schema.ts"]
    }
  ]
}
```

`schemaVersion` and `kind` are required and must be exactly `1` and
`"wdd_plan"`.

### `scope`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `id` | string, required | — | Scope identifier; stable across re-applies of the same plan. |
| `baseRef` | string or null | `null` | The scope's base branch. `plan apply` creates it (from `--from-ref`, default `HEAD`) if it doesn't exist. Cannot change once any task has left `todo`. |
| `maxConcurrent` | positive integer or null | `null` (unlimited) | Caps how many tasks may be active (`in_progress`/`review`/`merge_ready`) at once — the real limit on rebase churn. |
| `reviewPolicy` | `always` \| `risk_based` \| `none` | `risk_based` | `always`: every task needs a separate reviewer. `risk_based`: only `"risk": "high"` tasks do. `none`: no task does. |
| `reconcileEveryNMerges` | positive integer or null | `3` | A reconciliation checkpoint becomes due after this many merges, or immediately when a `wddctl note` is filed. |

### `tasks[]`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `id` | string, required | — | Task identifier, unique within the plan. |
| `title` | string | task `id` | Human-readable label. |
| `specPath` | string | `tasks/<id>.md` | Path (relative to `.wdd/`) to the task's worker brief. |
| `risk` | `normal` \| `high` | `normal` | Drives review requirement under `risk_based`. |
| `dependsOn` | string list | `[]` | Task IDs that must reach `done` before this task is admissible. Cycles are rejected at plan time. |
| `conflictDomains` | string list | `[]` | Paths/globs this task writes to. Two tasks sharing a domain are never concurrently active. A domain ending in `/**` is a path-prefix match; anything else uses `fnmatch`. |
| `context` | string list | `[]` | Handover refs of the form `<path>[#anchor]`, `.wdd`-relative; each path must resolve to a regular file inside `.wdd/` (containment enforced, no traversal). A ref of `spec.md#AC-<n>` is how a task declares the acceptance criterion it discharges. Persisted into task state (`MUTABLE_TASK_FIELDS`) and covered, byte-for-byte, by the plan-approval composite (see below) — editing a referenced file is plan drift even though the ref string itself didn't change. |
| `model` | string or null | `null` | Model alias for this task's implementation (decoration only in phase 6a; dispatch/routing lands in phase 6b). Persisted and composite-covered like `context`. |
| `reviewModel` | string or null | `null` | Model alias for this task's reviewer, same persistence/composite treatment as `model`. |

Editing or removing a task that has already started (left `todo`) is
refused — decompose further work into new task IDs instead.

### The plan-approval composite

`wddctl plan apply --approved-by NAME` records `scope.approval =
{by, at, sha256}`: the SHA-256 is a composite over the canonically
normalized plan document (task order and dict-key order both neutralized)
plus every task's brief file (`specPath`) and every `context`-ref file,
combined as sorted `(path, sha256)` pairs. Because `context`/`model`/
`reviewModel` are persisted into task state, the composite can be
recomputed later purely from `state.json` — the same reconstruction the
execution gate uses to detect **plan drift** (a brief or context file
edited after approval) without needing the original `plan.json` on disk.
See "The intake ladder" → "The plan-approval composite" in
[`docs/wddctl.md`](wddctl.md) for captured transcripts of both the
approval and the drift it catches.

## `state.json`

Owned entirely by `wddctl`. Never hand-edit it — every field here is the
output of applying validated events, and hand-editing breaks the guarantees
in [`docs/wddctl.md`](wddctl.md) (atomic writes, revisioned history, evidence
pinned to head SHA). Use `wddctl render --output state.md` for a read-only
Markdown projection instead.

Top-level shape:

```json
{
  "schemaVersion": 6,
  "revision": 7,
  "epic": "greeting-demo",
  "scope": { "...": "as in plan.json, plus maxConcurrent/reviewPolicy resolved, plus approval" },
  "constitution": { "status": "ratified", "ratification": { "by": "...", "decisionFingerprint": "...", "at": "..." } },
  "intake": { "configure": { "...": "..." }, "spec": { "...": "..." }, "research": { "...": "..." }, "design": { "...": "..." } },
  "tasks": { "TASK-001-token-types": { "...": "..." } },
  "finalize": { "review": { "...": "..." }, "verification": { "...": "..." }, "handoff": { "...": "..." }, "delivered": { "...": "..." } },
  "leases": { "TASK-001-token-types": { "...": "worktree bookkeeping" } },
  "reconcile": { "everyNMerges": 3, "mergesSinceCheckpoint": 1, "lastCheckpointAt": null, "pendingNotes": [] },
  "monitoring": { "mode": "manual", "status": "inactive", "observations": {} },
  "archivePending": null,
  "archiveBlocked": null,
  "probes": { "sha256:9c962d1...": { "at": "2026-08-01T19:56:10Z", "ok": true } },
  "events": [ { "revision": 7, "type": "task.merged", "task": "TASK-001-token-types", "idempotencyKey": "...", "at": "..." } ],
  "appliedIdempotencyKeys": ["..."],
  "telemetry": { "eventApplications": 12, "renderCount": 3 }
}
```

`epic` (schema v6, required) is the active epic's slug, or `null` when
none is active — the canonical scope identity source; see "Epic
directories" above. Written by `wddctl epic new` and cleared by `wddctl
scope archive`.

`archivePending` and `archiveBlocked` (schema v6, both nullable, `null`
outside an in-flight or blocked archive transaction) are the archive
transaction's own journal fields — see "`.wdd/archive/<slug>/`" below for
their shapes and "The archive transaction and its recovery" in
[`docs/wddctl.md`](wddctl.md) for what reads and clears them.

`finalize` is absent entirely before the scope reaches the `finalize`
phase, and removed again by `wddctl scope archive` (see below) — it is
never present as an empty object the way `intake`/`reconcile` are.

`probes` is absent until the first passing `wddctl dispatch
--probe-command`/`--probe` — keyed by `runner_command_digest`'s
`sha256:...` over the canonical JSON of the exact argv template (including
any unsubstituted `{worktree}`/`{prompt}`/`{logfile}` placeholders), never
by runner name: a name can be reassigned to a different command, but the
digest a probe proved never silently reattaches to new bytes. Written
outside `transition()` by a raw `apply_mutation` (`runner.probed` event) —
an **ungoverned observation**, the same precedent `monitor`'s own writes
use — because a runner must be provably usable before it is ever asked to
be ratified config, and gating the proof on ratification would be exactly
the governance cycle spec §6 rules out. Only ever grows (a failed probe
records nothing); `wddctl dispatch --task ID --role ...` refuses unless the
RATIFIED runner's exact command digest has an entry here with `"ok": true`
— see "Runners" in [`docs/wddctl.md`](wddctl.md).

### `intake` (schema v6, required)

Either `{"legacy": true}` — minted **only** by `wddctl migrate` for a
pre-v5 scope, never by `wddctl init`/`plan apply` — or any subset of four
records: `configure` (schema v6, the epic config approval; see below) plus
the three ladder-rung records, each fingerprint-bound to the artifact
bytes it approved:

```json
{
  "configure": { "by": "ivo", "at": "2026-08-01T19:56:40Z", "sha256": "sha256:1223d6..." },
  "spec": { "by": "ivo", "at": "2026-08-01T19:56:46Z", "criteria": 2, "sha256": "sha256:d35cc..." },
  "research": {
    "by": "ivo", "at": "2026-08-01T19:56:54Z", "done": true,
    "artifacts": [{"path": "shared-context/contract-inventory.md", "sha256": "sha256:b997d..."}]
  },
  "design": {
    "by": "ivo", "at": "2026-08-01T19:57:01Z",
    "sha256": "sha256:391ab...",
    "deliverableCommand": "python3 -c \"from src.greeting import greet; assert ('Ivo' in greet('Ivo'))\""
  }
}
```

`research`'s `done: true` shape carries `artifacts` (as above); its
alternative is an attributed skip: `{"by", "at", "skipped": true,
"reason": "..."}`. `intake_complete(state)` (the spec/research/design
trio) is `True` wholesale for `legacy`, else only once all three of those
records are present; `configure` is validated independently of that
trio's completeness (see "The configure record" below) and is required for
every non-legacy scope, `legacy` ones included. A fresh `wddctl init` or
`wddctl plan apply`'s internal state construction always produces
`intake: {}` — never `{"legacy": true}` — so a legacy scope only exists
because `wddctl migrate` produced it from a genuine pre-v5 state.

#### The configure record

`intake.configure`, written by `wddctl intake configure` (see
[`docs/wddctl.md`](wddctl.md)), has three legal shapes:

```json
{"by": "ivo", "at": "2026-08-01T19:56:40Z", "sha256": "sha256:1223d6..."}
```

the real, attributed approval — either form of `intake configure`
produces this shape; only the overlay bytes differ (the real overlay for
`--approved-by`, an empty one for `--use-defaults`). The `sha256` is
`effective_config_digest` over the fully resolved, default-hydrated,
canonically serialized view (overlay → global → default), never the
overlay bytes alone — see "Epic configuration overlay" in
[`docs/wddctl.md`](wddctl.md).

`wddctl migrate` mints the other two shapes — **never** the real one, and
no ordinary constructor mints either:

```json
{"legacy": true, "sha256": "sha256:...the migration-time full effective-config digest..."}
```

for a non-legacy v5 scope gaining epic-config identity for the first
time — the exemption covers only the missing human attribution; and the
identical shape again under a wholesale-`legacy` scope's own
`intake.legacy: true`, since migration stamps `configure` onto both kinds
of migrated scope alike. In both cases, **drift is still guarded from
there on**: any later change to the effective config (overlay or global)
mismatches the stamped digest exactly like an ordinary un-re-approved
change would, and only a real `intake configure --approved-by`/
`--use-defaults` remedies it — see `EpicConfigDriftAfterMigrationTest` in
`tests/test_epics.py` for the regression pin (a since-fixed bug once let
`legacy: true` disable drift detection wholesale for any migrated scope).

### `finalize.verification` (v5 vs. legacy shape)

```json
{
  "headSha": "8886d8e...",
  "commands": [
    {"command": "true", "status": "passed"},
    {"command": "python3 -c \"...\"", "status": "passed"}
  ],
  "status": "passed",
  "at": "2026-08-01T19:58:27Z",
  "configSha256": "sha256:..."
}
```

`commands` is present only for v5+ non-legacy scopes: the ratified global
`verification.commands` (from `config.json`) followed by the scope's
`intake.design.deliverableCommand`, in that exact order, recorded in one
atomic `finalize verify record --results '[...]'` call. Legacy scopes
instead carry the original singular pair, `{"headSha", "status",
"command", "justification", "at"}` — no `commands` key. Every read site
normalizes both shapes via `verification_commands()` to the same
`[{command, status}, ...]` view. `configSha256` (schema v6, optional —
absent on pre-migration evidence, present on everything `finalize verify
record` writes from Task 5 onward and back-filled onto pre-existing v5
records by migration) is the `finalVerification` **purpose-projected**
digest (verification.* plus the deliverable command) at recording time —
see "evidence digest fields" below.

#### Evidence digest fields (schema v6)

A task's `review` and `verification` objects, and `finalize.review`/
`finalize.verification`, gain up to three optional fields — optional
because pre-migration evidence carries none of them, and validation stays
permissive about their absence while shape-checking whichever ARE
present:

| Field | On | Meaning |
|---|---|---|
| `configSha256` | `review`, `verification`, `finalize.review`, `finalize.verification` | The **purpose-projected** `effective_config_digest` at recording time — `taskReview`'s projection (`models.review`, `review.policy`, `review.blockingSeverities`) for a task review, `finalReview`'s for the final one, `taskVerification`/`finalVerification`'s (`verification.*`, plus the deliverable command for the final one) for the two verification records. The SAME field name on every one of the four objects; which projection it was computed from is implied by which object it's attached to, never encoded in the field name itself. See "Epic configuration overlay" in [`docs/wddctl.md`](wddctl.md) for the projection definitions and why they partition the way they do. |
| `resolvedRisk` | task `review` only (not `finalize.review`) | The task's derived risk tier (`normal`/`high`) at the moment this review ran — bound alongside the config projection because a task's risk can rise without any config byte moving the projection; the consuming gate re-derives risk from current state and compares, staling the review on a mismatch exactly like a digest mismatch does. |
| `reviewModel` | task `review` and `finalize.review` | The review model actually selected and run under, for the same reason `resolvedRisk` is bound — a model reassignment between recording and merge is a re-derivable mismatch too, not silently ignored. |

None of the three ever appears on evidence recorded against a schema
predating this plan (pre-migration v5 records, or archived pre-v6 files)
— there is no digestless "grandfather" exemption once evidence exists on
a v6 state: migration stamps all three onto every existing record it
finds, computed from the then-effective config, and from that point every
later config change is checked against them through the ordinary gate
comparison.

Each entry under `tasks` carries: `id`, `title`, `specPath`, `status`
(`todo` / `in_progress` / `review` / `merge_ready` / `done` / `blocked` /
`cancelled`), `risk`, `dependsOn`, `conflictDomains`, `context`, `model`,
`reviewModel`, `branch`, `worktree`, `headSha`, `pr`, `review`,
`verification`, `freshness`, `merge`, `blocker`, `snapshot`, `inputs`, and
`rebound`.
`context`/`model`/`reviewModel` mirror `plan.json`'s `tasks[]` fields of the
same name (see above) — populated at `plan apply`, re-diffed on every
re-apply, and covered by the plan-approval composite. The `review`/
`verification`/`freshness`/`merge` objects each carry the `baseSha`/
`headSha` (or `baseRef`) the evidence was pinned to, so a stale or
mismatched entry is directly visible.

`snapshot` (string or `null`) is the `.wdd`-relative path of the task's most
recent attempt directory under `.wdd/dispatch/` (see above), recorded by
`wddctl start` in the same atomic `task.dispatched` mutation that follows a
successful `task.started` transition — never before it, so a failed `start`
never leaves a stale snapshot behind. `inputs` (list, `[]` by default) is
the recorded `[{"path", "sha256"}, ...]` — the SOURCE files' digests (the
live `.wdd` copies, not the read-only snapshot copies) at materialization
time, `.wdd`-relative paths, brief first then `context` refs in plan order,
each file appearing once even if referenced twice. This is the
**input-version binding** baseline: `inputs_status(state, wdd_dir, task_id)`
compares these recorded pairs against the CURRENT bytes at those same
paths, and a mismatch is what `wddctl next` surfaces as `inputs_changed`
and every task-targeted governed verb refuses on (see `rebind` and
"Runners" in [`docs/wddctl.md`](wddctl.md)). Legacy scopes (`intake.legacy`)
still get a `snapshot` (harmless and useful) but `inputs` stays `[]` always
— there is no approved-bytes doctrine predating them to bind evidence to.
A `wddctl rebind` re-records `inputs` against current bytes in place, without
touching `snapshot`, `review`, or `verification` — rebinding is a re-pin, not
a new attempt. It re-resolves the task's CURRENT `specPath`/`context` source
set (the same resolution `materialize_attempt` uses), not merely the
previously recorded paths re-hashed in place: a re-approval that also added
or removed a context ref since the attempt started is picked up too, so the
path set `inputs` names can change across a rebind, not only the digests.
`rebind` also records the human attribution on the task itself, as
`rebound: {"by", "at"}` (see below) — the `--by` name `rebind` requires.

`rebound` (object or `null`, `null` by default) is the attribution of the
task's most recent `wddctl rebind`: `{"by", "at"}`, the same idiom
`scope.approval` uses for plan-approval. It exists because the event log
does not durably store a mutation's `data` (see `events` below) — `rebind`'s
`--by` would otherwise be recorded nowhere but the log line's derived
`idempotencyKey`. Only ever set by `rebind`; never cleared, and a later
rebind simply overwrites it with the newer attribution.

`worktree` is normally `null`, and that is deliberate. A task's worktree lives
at `<repo>/<worktrees.root>/<scope>/<task>` — a pure function of the checkout
it belongs to and the current `config.json` (`worktrees.root`, default
`.worktrees`, resolved against the repo root when relative; an absolute value
is used as-is, anywhere on disk). Recording it would bake this machine's
directory name (and today's config value) into committed state, so a clone
into a differently named directory, or a later config change, would resolve
back to the wrong place. The location is derived instead, and the field only
holds a value when a caller passed an explicit `--worktree`, in which case it
is stored relative to the repository root (or absolute, if it names a
location outside it) and always wins over `worktrees.root` from then on — see
`git.worktree_for`'s docstring. A relative `worktrees.root` is gitignored at
the repository root automatically (idempotent, both at `init` and the moment
a worktree is first created under it); an absolute root outside the
repository needs no such entry and gets none. Changing `worktrees.root` for a
task that has no recorded override is governance drift like any other
`config.json` edit made after ratification — see "Governance drift" in
[`docs/wddctl.md`](wddctl.md) — not something re-derivation reconciles on its
own.

This is what makes a scope portable. A cloud agent can clone a repository
whose `state.json` says a task is `in_progress`, find no worktree (they are
never committed — they sit beside the repository), and run
`wddctl start --task ID --repo .` to re-attach: the branch is fetched, the
worktree is recreated in the new checkout's own tree, and the task continues
from where it was without restarting.

`events` is a full append-only history of every applied transition —
useful for auditing what happened and when, without needing to reconstruct
it from Git history. Every entry has exactly the shape shown above
(`revision`, `type`, `task`, `idempotencyKey`, `at`) — the caller's `data`
(e.g. a rebind's `by`, a probe's `digest`) feeds only the auto-generated
`idempotencyKey`'s derivation, not a stored payload field; the durable
record of *what* is whatever the mutator itself wrote elsewhere in
`state.json` (a task's re-recorded `inputs` and `rebound`, a new `probes`
entry), not the event log -- a rebind's `--by` specifically lands in
`tasks.<id>.rebound.by` (see above), not merely the event log's derived
`idempotencyKey`. Two event types added for runner dispatch: `task.rebound`
(task-scoped) marks exactly when `wddctl rebind` re-pinned a task's
`inputs`; `runner.probed` (scope-less — `task: null`) marks exactly when a
passing probe added an entry to `probes` (see above) — cross-reference the
`at` timestamp against `probes[digest].at` to tell which probe an entry
came from.

`gates`, in the sense of "what to do next for this task," are not stored —
they're computed live by `wddctl next` and `wddctl status` from the fields
above. See the gates table in [`docs/wddctl.md`](wddctl.md).

## `.wdd/archive/<slug>/`

`wddctl scope archive` (delivered-phase only — see "The intake ladder" in
[`docs/wddctl.md`](wddctl.md)) is a **transaction**, not a single write:
it writes `record.json` inside the still-active `epics/<slug>/`, then
atomically renames the whole directory to `archive/<slug>/`, then resets
`state.json`. `wddctl` never reads `record.json` back; it exists purely as
the durable record of a completed epic, since `state.json` itself is reset
to a fresh setup shape immediately after archiving. See "The archive
transaction and its recovery" in [`docs/wddctl.md`](wddctl.md) for the
full four-step sequence and its crash-recovery matrix — this section
covers the file shapes only.

### `record.json`

```json
{
  "scope": { "id": "SCOPE-greeting-demo", "baseRef": "wdd/greeting-demo", "...": "the delivered scope object, approval included" },
  "tasks": { "TASK-001-greeting": { "...": "every task's final state" } },
  "intake": { "configure": { "...": "..." }, "spec": { "...": "..." }, "research": { "...": "..." }, "design": { "...": "..." } },
  "finalize": { "review": { "...": "..." }, "verification": { "...": "..." }, "handoff": { "...": "..." }, "delivered": { "...": "..." } },
  "reconcile": { "everyNMerges": 3, "mergesSinceCheckpoint": 1, "lastCheckpointAt": null, "pendingNotes": [ { "at": "...", "note": "...", "task": null } ] },
  "leases": { "TASK-001-greeting": { "status": "released", "...": "worktree/branch/timestamps history" } },
  "eventCount": 17,
  "archivedAt": "2026-08-01T19:58:49Z"
}
```

Every field is a snapshot at `archivePending.sourceRevision` (below) —
`scope`, `tasks`, `intake`, `finalize`, `reconcile` (including any
still-pending `pendingNotes`), and `leases` (a task's full lease history,
not just its final released state). `eventCount` and `archivedAt` exist
only in `record.json`, not in `state.json` itself; note the shape does
**not** carry `epic` as a top-level key of its own — the slug lives in
the archive path itself (`archive/<slug>/record.json`), and in
`scope.id`'s `SCOPE-<slug>` derivation. `record.json` generation is a
**pure, deterministic function** of the state at `sourceRevision` (see
below) plus `archivedAt`, the one nondeterministic input — `wave_delivery/
finalize.py`'s `generate_archive_record` — so it can always be
regenerated byte-identically from an uncrashed state; nothing about it is
unreconstructable, which is what makes the recovery matrix's mid-crash
rows safe.

The no-leak guarantee this enables is total: after archiving,
`state.json`'s `scope`, `epic`, `tasks`, `finalize`, `intake`,
`archivePending`, `archiveBlocked`, `reconcile`, `monitoring.observations`,
and `leases` are all reset to the same fresh shape `wddctl init` produces
(`intake: {}`, a genuine new ladder — never re-marked `legacy`), while
`constitution` and the audit trail (`events`, `appliedIdempotencyKeys`,
`telemetry`) survive untouched. Two epics never collide on a path: slugs
are immutable and unique against both `epics/` and `archive/` (see "Epic
directories" above), so an archived directory's name can never be handed
to a later `epic new`.

### `state.archivePending` / `state.archiveBlocked` (schema v6, journal fields)

```json
{
  "archivePending": {
    "slug": "greeting-demo",
    "sourceRevision": 19,
    "archivedAt": "2026-08-01T19:58:49Z",
    "recordSha256": "sha256:..."
  },
  "archiveBlocked": null
}
```

`archivePending` exists only between transaction step 2 (recorded) and
step 4 (cleared on completion) — a caller reading `state.json` mid-
transaction is exactly what the recovery matrix exists to make safe.
`sourceRevision` names the exact state `record.json` was generated from
(excluding the `archivePending` journal event itself), so `recordSha256`
is independently reproducible against it. `archiveBlocked`
(`{slug, collidingPath, at}`) is the durable resting state left behind by
an **external collision** discovered mid-transaction — see "The archive
transaction and its recovery" in [`docs/wddctl.md`](wddctl.md) for the
full six-row recovery matrix, including this row's own remedy. Both
fields are `null` outside their respective in-flight/blocked windows, and
never both non-null at once.

## `.wdd/dispatch/`

Transient scratch, `wddctl`-owned, **never committed** — `init` and
`migrate --governance` write a `dispatch/` entry into `.wdd/.gitignore` for
it, and `materialize_attempt`/`dispatch_task` re-ensure the same entry the
moment `dispatch/` is first created (idempotent, content-preserving: it
only ever appends the one line, never touches anything else already in the
file) -- covering an install that predates the scratch dir and never runs
`init`/`migrate --governance` again. `wddctl` never reads this directory
back into `state.json`; it exists purely as working storage for two related
but distinct things, both keyed by sanitized task ID (`[A-Za-z0-9._-]`,
anything else replaced with `_`):

```text
.wdd/dispatch/
  <TASK-ID>-<attempt>/                     # attempt snapshot (wddctl start / dispatch --role reviewer)
    tasks/<TASK-ID>.md                     # copy of the task's brief, read-only
    shared-context/<...>                   # copy of every context-ref file, read-only
  <TASK-ID>-<role>-<attempt>.prompt        # the assembled dispatch packet (wddctl dispatch --task)
  <TASK-ID>-<role>-<attempt>.log           # wddctl's own captured stdout+stderr
  <TASK-ID>-<role>-<attempt>-runner.log    # the {logfile} placeholder target -- the runner's own transcript, if it writes one; a distinct path from the .log above so the two never collide
  <TASK-ID>-<role>-<attempt>-result.json   # reviewer only: the validated wddctl_review_result
```

**Attempt snapshots** (`<TASK-ID>-<attempt>/`): `wddctl start` copies the
task's brief and every `context`-ref file (anchors stripped for file
resolution; a file referenced twice copies once) into a fresh directory —
attempt numbering is 1 + the count of existing attempt dirs for that task,
never reused or overwritten, so every dispatch's inputs stay reconstructable
even after a later re-`start`. `wddctl dispatch --task ID --role reviewer`
materializes its OWN fresh attempt snapshot at dispatch time (never reusing
the worker's), since a reviewer's packet must reflect the CURRENT approved
bytes at review time, not whatever was current when the worker started. The
directory is `0700`; every file inside is `0400` (read-only) for snapshots,
`0600` for dispatch prompts/logs/results. Workers and reviewers are handed
these snapshot paths, never live files under `.wdd/` directly — a file
edited (or edited-and-restored) after being materialized cannot reach a
dispatched agent. The digests of the SOURCE files (not the snapshot copies)
are what get recorded on the task as `inputs` (see `state.json` below).

**Dispatch prompts/logs/results** (`wddctl dispatch --task ID --role
worker|reviewer`, see "Runners" in [`docs/wddctl.md`](wddctl.md)): one
`.prompt`/`.log` pair per invocation, numbered per task+role so nothing is
ever overwritten. The prompt is the exact assembled packet — role contract
line, brief + context snapshot paths, the Deliverable section, and either
the status-token contract (worker) or the `wddctl_review_result` JSON
contract (reviewer, reusing the exact schema `review collect` already
validates — see "External result file envelopes" in `docs/wddctl.md`). The log is the
runner's raw captured stdout+stderr; only a bounded 4KB tail of it is ever
echoed back in `dispatch`'s own JSON result — the full output lives only in
this file. A reviewer's successful, contract-valid run additionally writes
a `-result.json` file beside the log, ready to hand directly to `wddctl
review collect --result <path>`.

## Task briefs (`.wdd/epics/<slug>/tasks/<TASK-ID>.md`)

The worker implementation brief for one task, referenced by that task's
`specPath` in `plan.json`. There is no required frontmatter schema — `wddctl`
never reads these files, only the plan's `specPath` string that points at
them. Two `## ` sections are checked by `plan lint`'s `missing_deliverable`
and `missing_interfaces` codes (advisory, not enforced at `plan apply`) and
should be treated as required in practice:

- **Deliverable** — what this task's diff produces, observably; the
  reviewer's first question.
- **Interfaces** — what it consumes and produces, kept consistent with
  `design.md`'s own Interfaces section.

A useful brief still tends to cover everything else `plan lint` doesn't
check for:

- **Objective** — what this task delivers.
- **Scope / Non-scope** — what's in and explicitly out.
- **Relevant context** — pointers into `shared-context/`, prior task
  findings, or repo docs the worker shouldn't have to rediscover. Prefer a
  plan-level `context` ref (see `tasks[]` above) over prose for anything
  machine-verifiable — it's fingerprint-bound and drift-checked; prose
  here is not.
- **Dependencies and conflict domains** — restated from `plan.json` so the
  worker sees why it can or can't run yet.
- **Verification** — the command(s) that should pass before `submit`.
- **Definition of done.**

## `shared-context/`

Durable knowledge that should survive past any one task or agent context
window: architecture notes, discovered conventions, testing strategy,
constraints found mid-implementation. Free-form Markdown, organized however
the project prefers (an `index.md` plus focused resource files is a common
pattern). `wddctl note --note "..." [--task ID]` queues a discovery into
`state.json`'s `reconcile.pendingNotes` and makes a reconciliation checkpoint
due; folding a queued note into `shared-context/` durably (or deciding it
doesn't need to be) is part of what a reconciliation checkpoint is for.
