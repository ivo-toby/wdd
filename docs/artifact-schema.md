# WDD artifact schema

WDD's durable state lives under `.wdd/` in the target repository:

```text
.wdd/
  constitution.md          # human-authored governance; ratified via wddctl
  plan.json                # the only planning input
  state.json               # wddctl-owned; never hand-edit
  state.md                 # generated projection (wddctl render)
  tasks/<TASK-ID>.md       # worker briefs, referenced by each task's specPath
  shared-context/          # durable discoveries
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

Editing or removing a task that has already started (left `todo`) is
refused — decompose further work into new task IDs instead.

## `state.json`

Owned entirely by `wddctl`. Never hand-edit it — every field here is the
output of applying validated events, and hand-editing breaks the guarantees
in [`docs/wddctl.md`](wddctl.md) (atomic writes, revisioned history, evidence
pinned to head SHA). Use `wddctl render --output state.md` for a read-only
Markdown projection instead.

Top-level shape:

```json
{
  "schemaVersion": 3,
  "revision": 7,
  "scope": { "...": "as in plan.json, plus maxConcurrent/reviewPolicy resolved" },
  "constitution": { "status": "ratified", "ratification": { "by": "...", "decisionFingerprint": "...", "at": "..." } },
  "tasks": { "TASK-001-token-types": { "...": "..." } },
  "leases": { "TASK-001-token-types": { "...": "worktree bookkeeping" } },
  "reconcile": { "everyNMerges": 3, "mergesSinceCheckpoint": 1, "lastCheckpointAt": null, "pendingNotes": [] },
  "monitoring": { "mode": "manual", "status": "inactive", "observations": {} },
  "events": [ { "revision": 7, "type": "task.merged", "task": "TASK-001-token-types", "idempotencyKey": "...", "at": "..." } ],
  "appliedIdempotencyKeys": ["..."],
  "telemetry": { "eventApplications": 12, "renderCount": 3 }
}
```

Each entry under `tasks` carries: `id`, `title`, `specPath`, `status`
(`todo` / `in_progress` / `review` / `merge_ready` / `done` / `blocked` /
`cancelled`), `risk`, `dependsOn`, `conflictDomains`, `branch`, `worktree`,
`headSha`, `pr`, `review`, `verification`, `freshness`, `merge`, and
`blocker`. The `review`/`verification`/`freshness`/`merge` objects each carry
the `baseSha`/`headSha` (or `baseRef`) the evidence was pinned to, so a stale
or mismatched entry is directly visible.

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
it from Git history.

`gates`, in the sense of "what to do next for this task," are not stored —
they're computed live by `wddctl next` and `wddctl status` from the fields
above. See the gates table in [`docs/wddctl.md`](wddctl.md).

## Task briefs (`.wdd/tasks/<TASK-ID>.md`)

The worker implementation brief for one task, referenced by that task's
`specPath` in `plan.json`. There is no required frontmatter schema — `wddctl`
never reads these files, only the plan's `specPath` string that points at
them. A useful brief still tends to cover:

- **Objective** — what this task delivers.
- **Scope / Non-scope** — what's in and explicitly out.
- **Relevant context** — pointers into `shared-context/`, prior task
  findings, or repo docs the worker shouldn't have to rediscover.
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
