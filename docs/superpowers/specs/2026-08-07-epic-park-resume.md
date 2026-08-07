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
  `reconcile`, `monitoring`, `leases` (when present) — into
  `state.parked[<slug>]`, records `{at}` alongside them, and resets
  those sections to their `new_setup_state()` shapes with `state.epic`
  cleared. `monitoring` IS scope-specific (archive resets it for the
  same reason) and travels with the park. Governance, events, telemetry,
  `probes`, and `appliedIdempotencyKeys` are global and untouched.
- **Worktrees are released at park; branches stay.** Before the state
  swap, park removes each active task's worktree (the existing `release`
  mechanics), refusing the whole park if any worktree has uncommitted
  changes — named path, no force option; commit or stash first. Branches
  keep the work; resume re-creates worktrees through `start`'s existing
  reattach path. This is what makes parking safe against Git itself: a
  parked epic holds no checkout claims.
- **Branch names are not scope-qualified** (`task/<TASK-ID>`), so a new
  epic reusing a task id would collide with the parked epic's branch.
  Guard at worktree creation (this phase): creating a branch for a task
  with no recorded lease REFUSES when the branch already exists —
  message names the branch and, when it belongs to a parked epic's
  recorded task, names that epic and `epic resume`. Silent adoption of
  another scope's branch is the failure mode this kills.
- Park does not interrupt an in-flight external dispatch — a runner
  mid-exec keeps running and burns its tokens; bounded by construction:
  every recording verb it would feed (`submit`, `review collect`, ...)
  is task-targeted and refuses post-park ("unknown task"), so no
  evidence can land in the wrong epic. The skill says: don't park while
  dispatches are running; the machine says: nothing breaks if you do.
- **No epic-directory moves.** `epics/<slug>/` stays in place; parked
  state references artifacts by the same recorded values; nothing
  re-derives while parked.
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
  they are never invisible — and `epic_orphans()` excludes parked slugs
  (a parked epic's directory is accounted-for state, not a crash
  candidate; without this exclusion doctor would report every parked
  epic as an orphan). `status` and setup-phase `next` name parked epics
  in judgment text without emitting actions for them.
- **`config get/set --epic` with no active epic** refuses (it already
  does for set) — and the refusal names any parked slugs and
  `epic resume`, so an operator inspecting a parked epic's overlay is
  told the real path instead of silently reading the global layer.
- **Resume and the chokepoint, pinned**: `epic resume` is governed and
  passes the GOVERNANCE freshness check against the current state
  (constitution/config still ratified — right and sufficient). The
  epic-level gates (epic config, intake, plan) evaluate the pre-swap
  setup state, where they are structurally no-ops — this is
  deliberate, not accidental: those gates judge the restored epic on
  the NEXT verb against post-resume state, which is the only state
  where their question is meaningful. Pinned by test, stated in the
  verb's docstring.
- **Schema v7.** `validate_state` accepts only the exact current
  version, so "additive on v6" is not a real option: SCHEMA_VERSION
  bumps to 7, `parked` is a required-but-defaulting `{}` map validated
  per entry (each value shape-checked as the parked section bundle +
  `{at}`), and `migrate` gains the v6→v7 step — a pure version bump
  plus `parked: {}`, no file moves; the migration hint recognizes v6.
  Constructors never mint parked entries.
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
