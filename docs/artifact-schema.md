# WDD artifact schema

WDD's durable state lives under `.wdd/` in the target repository:

```text
.wdd/
  constitution.md          # human-authored governance; ratified via wddctl
  spec.md                  # human-authored; ratified via wddctl intake spec
  design.md                # human-authored; ratified via wddctl intake design
  plan.json                # the only planning input
  state.json               # wddctl-owned; never hand-edit
  state.md                 # generated projection (wddctl render)
  tasks/<TASK-ID>.md       # worker briefs, referenced by each task's specPath
  shared-context/          # durable discoveries, and research-rung artifacts
  archive/<SCOPE-ID>.json  # wddctl-owned; written by `wddctl scope archive`
  dispatch/                # wddctl-owned; transient scratch, gitignored, never committed
  .gitignore                # wddctl-owned; written by init/migrate to ignore dispatch/
```

Everything here is either human-authored Markdown/JSON that `wddctl` reads,
or a `wddctl`-generated file that must not be hand-edited.

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

Between ratification and `plan apply`, two more human-authored Markdown
files are agreed and recorded via `wddctl intake spec`/`design` (see
"The intake ladder" in [`docs/wddctl.md`](wddctl.md)). Both are fingerprinted
at approval time (SHA-256 over the exact bytes), so an edit afterward is
drift, not a free edit.

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

The single planning input. `wddctl plan apply` reads it and creates or
updates a scope in `state.json`; re-applying is safe and diffs the new plan
against the current state.

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
  "schemaVersion": 5,
  "revision": 7,
  "scope": { "...": "as in plan.json, plus maxConcurrent/reviewPolicy resolved, plus approval" },
  "constitution": { "status": "ratified", "ratification": { "by": "...", "decisionFingerprint": "...", "at": "..." } },
  "intake": { "spec": { "...": "..." }, "research": { "...": "..." }, "design": { "...": "..." } },
  "tasks": { "TASK-001-token-types": { "...": "..." } },
  "finalize": { "review": { "...": "..." }, "verification": { "...": "..." }, "handoff": { "...": "..." }, "delivered": { "...": "..." } },
  "leases": { "TASK-001-token-types": { "...": "worktree bookkeeping" } },
  "reconcile": { "everyNMerges": 3, "mergesSinceCheckpoint": 1, "lastCheckpointAt": null, "pendingNotes": [] },
  "monitoring": { "mode": "manual", "status": "inactive", "observations": {} },
  "probes": { "sha256:9c962d1...": { "at": "2026-08-01T19:56:10Z", "ok": true } },
  "events": [ { "revision": 7, "type": "task.merged", "task": "TASK-001-token-types", "idempotencyKey": "...", "at": "..." } ],
  "appliedIdempotencyKeys": ["..."],
  "telemetry": { "eventApplications": 12, "renderCount": 3 }
}
```

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

### `intake` (schema v5, required)

Either `{"legacy": true}` — the sole key, minted **only** by `wddctl
migrate` for a pre-v5 scope, never by `wddctl init`/`plan apply` — or any
subset of three records, one per ladder rung, each fingerprint-bound to
the artifact bytes it approved:

```json
{
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
"reason": "..."}`. `intake_complete(state)` is `True` wholesale for
`legacy`, else only once all three records are present. A fresh `wddctl
init` or `wddctl plan apply`'s internal state construction always produces
`intake: {}` — never `{"legacy": true}` — so a legacy scope only exists
because `wddctl migrate` produced it from a genuine pre-v5 state.

### `finalize.verification` (v5 vs. legacy shape)

```json
{
  "headSha": "8886d8e...",
  "commands": [
    {"command": "true", "status": "passed"},
    {"command": "python3 -c \"...\"", "status": "passed"}
  ],
  "status": "passed",
  "at": "2026-08-01T19:58:27Z"
}
```

`commands` is present only for v5 non-legacy scopes: the ratified global
`verification.commands` (from `config.json`) followed by the scope's
`intake.design.deliverableCommand`, in that exact order, recorded in one
atomic `finalize verify record --results '[...]'` call. Legacy scopes
instead carry the original singular pair, `{"headSha", "status",
"command", "justification", "at"}` — no `commands` key. Every read site
normalizes both shapes via `verification_commands()` to the same
`[{command, status}, ...]` view.

Each entry under `tasks` carries: `id`, `title`, `specPath`, `status`
(`todo` / `in_progress` / `review` / `merge_ready` / `done` / `blocked` /
`cancelled`), `risk`, `dependsOn`, `conflictDomains`, `context`, `model`,
`reviewModel`, `branch`, `worktree`, `headSha`, `pr`, `review`,
`verification`, `freshness`, `merge`, `blocker`, `snapshot`, and `inputs`.
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
A `wddctl rebind` re-records `inputs` against current bytes in place
(same paths re-resolved, new digests) without touching `snapshot`, `review`,
or `verification` — rebinding is a re-pin, not a new attempt.

`worktree` is normally `null`, and that is deliberate. A task's worktree lives
at `<repo>.wdd/worktrees/<scope>/<task>` — a pure function of the checkout it
belongs to. Recording it would bake this machine's directory name into
committed state, so a clone into a differently named directory would resolve
back to the original machine's worktree. The location is derived instead, and
the field only holds a value when a caller passed an explicit `--worktree`,
in which case it is stored relative to the repository root.

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
`state.json` (a task's re-recorded `inputs`, a new `probes` entry), not the
event log. Two event types added for runner dispatch: `task.rebound`
(task-scoped) marks exactly when `wddctl rebind` re-pinned a task's
`inputs`; `runner.probed` (scope-less — `task: null`) marks exactly when a
passing probe added an entry to `probes` (see above) — cross-reference the
`at` timestamp against `probes[digest].at` to tell which probe an entry
came from.

`gates`, in the sense of "what to do next for this task," are not stored —
they're computed live by `wddctl next` and `wddctl status` from the fields
above. See the gates table in [`docs/wddctl.md`](wddctl.md).

## `.wdd/archive/<SCOPE-ID>.json`

Written once, by `wddctl scope archive` (delivered-phase only — see "The
intake ladder" in [`docs/wddctl.md`](wddctl.md)). `wddctl` never reads this
file back; it exists purely as the durable record of a completed scope,
since `state.json` itself is reset to a fresh setup shape immediately
after archiving.

```json
{
  "scope": { "id": "SCOPE-greeting-demo", "baseRef": "wdd/greeting-demo", "...": "the delivered scope object, approval included" },
  "tasks": { "TASK-001-greeting": { "...": "every task's final state" } },
  "intake": { "spec": { "...": "..." }, "research": { "...": "..." }, "design": { "...": "..." } },
  "finalize": { "review": { "...": "..." }, "verification": { "...": "..." }, "handoff": { "...": "..." }, "delivered": { "...": "..." } },
  "reconcile": { "everyNMerges": 3, "mergesSinceCheckpoint": 1, "lastCheckpointAt": null, "pendingNotes": [ { "at": "...", "note": "...", "task": null } ] },
  "leases": { "TASK-001-greeting": { "status": "released", "...": "worktree/branch/timestamps history" } },
  "eventCount": 17,
  "archivedAt": "2026-08-01T19:58:49Z"
}
```

Every field is a snapshot at archive time — `scope`, `tasks`, `intake`,
`finalize`, `reconcile` (including any still-pending `pendingNotes`), and
`leases` (a task's full lease history, not just its final released state).
`eventCount` and `archivedAt` exist only in the archive file, not in
`state.json` itself. The no-leak guarantee this file's existence enables
is total: after archiving, `state.json`'s `scope`, `tasks`, `finalize`,
`intake`, `reconcile`, `monitoring.observations`, and `leases` are all
reset to the same fresh shape `wddctl init` produces (`intake: {}`, a
genuine new ladder — never re-marked `legacy`), while `constitution` and
the audit trail (`events`, `appliedIdempotencyKeys`, `telemetry`) survive
untouched.

## `.wdd/dispatch/`

Transient scratch, `wddctl`-owned, **never committed** — `init` and
`migrate --governance` write a `dispatch/` entry into `.wdd/.gitignore` for
it (idempotent, content-preserving: it only ever appends the one line,
never touches anything else already in the file). `wddctl` never reads
this directory back into `state.json`; it exists purely as working storage
for two related but distinct things, both keyed by sanitized task ID
(`[A-Za-z0-9._-]`, anything else replaced with `_`):

```text
.wdd/dispatch/
  <TASK-ID>-<attempt>/                     # attempt snapshot (wddctl start / dispatch --role reviewer)
    tasks/<TASK-ID>.md                     # copy of the task's brief, read-only
    shared-context/<...>                   # copy of every context-ref file, read-only
  <TASK-ID>-<role>-<attempt>.prompt        # the assembled dispatch packet (wddctl dispatch --task)
  <TASK-ID>-<role>-<attempt>.log           # the runner's captured stdout+stderr
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

## Task briefs (`.wdd/tasks/<TASK-ID>.md`)

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
