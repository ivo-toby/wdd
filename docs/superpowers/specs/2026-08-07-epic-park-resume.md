# Epic Park and Resume

Status: draft for adversarial review.
Baseline: main at v0.3.x (epic-scoped state, adversarial-review phase).

## Problem

A pivot mid-epic sometimes genuinely means *a different epic* — an
amendment scope that must run now — but the current epic is neither
finished (archive requires `delivered`) nor abandoned (its merged tasks,
worktrees, and intake artifacts represent real, paid-for work). Today the
only options are pivot-in-place (rung cascade + plan reshape) or losing
the epic's in-flight state. Observed in the field on a real repo.

## Design principle

One active epic remains law. Parking is **suspension, not concurrency**:
a parked epic has no active claims — no admission, no gates evaluated,
no actions emitted — and resuming it re-enters every existing gate
exactly as if the intervening time had passed within the epic. Park adds
**no new doctrine**: staleness is caught by the freshness machinery,
config drift by the configure digest, exactly as they already work.

## Verbs

```
wddctl epic park                 # park the active epic
wddctl epic resume --slug S      # reactivate a parked epic
```

### `epic park`

- Governed; refuses when: no active epic; `state.archivePending` is set
  (finish the archive transaction first); `state.archiveBlocked` is set
  (resolve it first).
- Legal in ANY phase from post-configure through `delivered` — parking a
  delivered-but-unarchived epic is allowed (the retrospective can happen
  at resume).
- One locked `apply_mutation` (event `epic.parked`): moves the
  scope-carrying sections — `scope`, `tasks`, `intake`, `finalize`,
  `reconcile`, `leases` (when present) — into
  `state.parked[<slug>]`, records `{at}` alongside them, and resets
  those sections to their `new_setup_state()` shapes with `state.epic`
  cleared. Governance, events, telemetry, `probes`, and
  `appliedIdempotencyKeys` are global and untouched.
- **No file moves.** `epics/<slug>/` stays in place; worktrees and
  branches stay on disk. Parked state references them by the same
  recorded values; nothing re-derives while parked.
- After park, the ladder restarts at `create_epic` for the next epic.

### `epic resume --slug S`

- Governed; refuses when: an epic is already active (park or archive it
  first); the slug is not in `state.parked`; the slug's directory is
  missing (hard error naming the path — never guess).
- One locked `apply_mutation` (event `epic.resumed`): moves the parked
  sections back verbatim, sets `state.epic`, removes the `parked` entry.
- No re-validation happens at resume beyond schema validation of the
  restored state. Every staleness question is answered by the EXISTING
  gates on the next verb: interposed merges to the target branch surface
  through `check_branch_freshness`/`refresh` (evidence invalidation
  unchanged); interposed global-config changes surface as
  `epic_config_drift` against the restored configure digest; interposed
  edits to the parked epic's artifacts surface as `intake_drift`/
  `plan_drift`/`inputs_changed` through the recorded fingerprints. This
  is the point of the design: resume is a state swap, and the gates were
  already written to distrust elapsed time.

## Interactions, pinned

- **Slug uniqueness**: `epic new` already checks `epics/` and
  `archive/`; parked epics live under `epics/`, so collisions are
  already refused. Add `state.parked` keys to the check anyway
  (belt-and-braces for a hand-deleted directory) with a message naming
  `epic resume`.
- **Worktrees and conflict domains**: a parked epic's leases travel into
  `state.parked` — the active epic's admission checks look only at
  active state, so a parked epic's conflict domains constrain nothing.
  Worktree paths cannot collide: they are derived per scope
  (`.worktrees/<scope>/<task>`), and scope ids are slug-unique.
- **Branch interactions are the operator's**: the parked epic's base
  branch simply doesn't advance while parked. If the interposed epic
  merges work that the parked epic's branches will conflict with,
  `refresh` finds out at resume — same as any long-lived branch.
- **`doctor`** reports parked epics (slug, parked-at, task counts) so
  they are never invisible; `status` and setup-phase `next` name them in
  judgment text ("1 parked epic: provider-catalog-core — resume with
  ...") without emitting actions for them.
- **`migrate`**: `parked` is a new optional state field, absent = none;
  v6→v7 is additive (nullable), no file migration. Schema bump only if
  validation requires it — prefer additive-optional on v6 if the
  validator tolerates it cleanly; otherwise v7 with a trivial bump.
  (Implementation decides, documented; constructors never mint parked
  entries.)
- **Archive of a parked epic**: not directly — resume first, then
  archive from `delivered`. Archive's own gates are untouched.
- **Knowledge draft**: lives in `shared-context/` (global), untouched by
  park; the resumed epic keeps appending to the same file.

## Skills (prose, same phase)

- `wdd-intake`: a short "Parking an epic" section — when to park vs
  pivot-in-place (pivot when it's the same epic with a new approach;
  park when the new work is genuinely a different epic); the resume
  handoff ("Want to resume <slug>? Expect freshness and config gates to
  ask for re-approvals — that's the system re-trusting the world, not an
  error.").
- `wdd-run`: on resume, run `wddctl next` and treat whatever drift
  blockers appear as the normal remedy chain, not a corruption signal.
- Router: one judgment line ("mid-epic pivot to different work → park").

## Non-goals

- No concurrent execution, ever, and no concurrent intake in this phase
  — parked means fully inert.
- No automatic re-verification at resume — gates fire on the next verb,
  not on resume itself.
- No parking limit or eviction policy — parked entries are cheap state;
  operator judgment governs how many is too many (`doctor` keeps them
  visible).
