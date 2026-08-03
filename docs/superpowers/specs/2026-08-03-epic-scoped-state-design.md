# Epic-Scoped State: Folders, Overlay Config, and Versioning

Status: draft for review.
Baseline: main after phase 6c + follow-ups (schema v5, intake ladder,
handover, runners, `worktrees.root`, `plan template`).

## Problem

Three gaps, observed after the first scope was delivered and a second begun:

1. **Scope artifacts are singletons.** `spec.md`, `design.md`, `plan.json`,
   `tasks/TASK-001.md` are global paths. `scope archive` preserves the
   state *records* (fingerprints included) but the artifact *files* stay in
   place and the next epic overwrites them. A delivered epic's record is
   not browsable as a unit; task-brief filenames collide across epics.
2. **Configuration is global-only.** Real epics differ: a large epic wants
   `merge.surface: pr` and a strong reviewer; a small one wants `local`
   and cheap models. Today that means mutating global config back and
   forth, churning governance each time.
3. **The CLI has no version identity.** No `--version` flag, no release
   tagging, no way to correlate an installed binary with repo history.

## Design principle

One epic at a time remains law — parallelism lives *inside* a scope, and
conflict-domain enforcement depends on it. An epic becomes a **named,
self-contained directory** with its own artifacts and a sparse config
overlay; everything else (governance, machine config, runners, durable
shared context) stays global. Choreography in the state machine, judgment
in skills, as always.

## 1. Epic directories

### Layout

```
.wdd/
  constitution.md          # global, unchanged
  config.json              # global machine config, unchanged
  state.json               # unchanged location; schema v6
  shared-context/          # global, cross-epic by definition
  epics/<slug>/
    config.json            # sparse overlay (§2); may be {}
    spec.md
    design.md
    plan.json
    tasks/<TASK-ID>.md
    research/              # research artifacts that are epic-specific
  archive/<slug>/          # moved wholesale at scope archive
    record.json            # the state record (today's archive JSON)
    ...the entire epic directory content...
```

- `shared-context/` remains global: it is durable knowledge future epics
  should find. A contract inventory typically starts under
  `epics/<slug>/research/` and is **promoted** to `shared-context/` by an
  explicit copy when it proves durable — promotion is a judgment call the
  skills own, not a mechanical rule.
- Everything under `epics/<slug>/` is committed (durable state doctrine);
  `dispatch/` and `.worktrees/` stay transient and gitignored as today.

### The slug is born at the top of the ladder

New verb, the first action of every epic:

```
wddctl epic new --slug <slug> [--title "..."]
```

- Slug: `[a-z0-9][a-z0-9-]{1,63}`, unique against `epics/` and
  `archive/`. Creates `epics/<slug>/` with an empty overlay and records
  `state.epic = <slug>` (a new top-level field; null when no epic is
  active). Refuses when an epic is already active — one at a time.
- `setup_next_actions` emits `create_epic` (judgment: name the work with
  the user, per `wdd-intake`) after governance is ratified and before
  `agree_spec`. The ladder is otherwise unchanged:
  `create_epic → configure (§2) → agree_spec → research → agree_design → plan`.
- All intake verbs, `plan apply`, lint, and brief resolution resolve
  artifact paths against `epics/<state.epic>/` instead of `.wdd/` flat.
  `specPath` and `context` refs in `plan.json` stay `.wdd`-relative in
  their serialized form (containment validation unchanged), so plans
  remain copy-portable; the resolver prefixes the active epic dir for
  epoch-scoped paths and falls through to `.wdd/` for `shared-context/`.
- Scope id derives from the slug at `plan apply` (`SCOPE-<slug>`) unless
  the plan names one explicitly; `plan template` emits the derived id as
  the placeholder.

### Archive moves the folder

`wddctl scope archive` (delivered-phase only, unchanged gate) now:

1. Writes the state record to `.wdd/archive/<slug>/record.json`
   (same content as today's `<scope-id>.json`).
2. Moves `epics/<slug>/` into `.wdd/archive/<slug>/` (git mv semantics —
   the archive is committed history, not deletion).
3. Resets state to post-ratification setup and clears `state.epic`.
   Total no-leak as today; the ladder restarts at `create_epic`.

Nothing is ever overwritten; two epics never collide on a path.

## 2. Epic configuration overlay

### Resolution

`epics/<slug>/config.json` is a **sparse overlay**: only overridden keys
present. Resolution for any config key path, at every read site:

```
epic overlay → global config.json → built-in default
```

Resolution is per key path (e.g. `models.review` can be overridden while
`models.implementation` falls through), computed by a single shared
`resolve_config(state, wdd_dir)` that returns the merged view — no read
site may consult either file directly.

**Overridable keys** (the epic surface): `models`, `verification`
(commands + justification), `merge`, `riskRules`, `reviewPolicy` default.
**Global-only keys** (machine- or repo-bound, never overridable):
`runners` (probe evidence is machine-bound), `worktrees.root`,
governance/constitution settings. `config set`/`get` gain `--epic`: sets
write the overlay of the active epic, gets print the merged view with a
`source` marker per key (`epic` / `global` / `default`).

### The configure step

After `epic new`, `next` emits one `configure_epic` action (judgment: walk
the user through the epic-overridable keys in ONE compact round, in their
terms — which merge surface, which models, what proves this epic works —
per `wdd-intake`). Two legal outcomes, both explicit and attributed:

```
wddctl intake configure --approved-by NAME            # overlay as written
wddctl intake configure --use-defaults --by NAME      # empty overlay
```

- `--use-defaults` records the explicit decision to inherit everything;
  silence is not an option (the research-skip precedent).
- The record carries `{by, at, sha256(overlay bytes)}` in
  `state.intake.configure`. An empty overlay hashes the canonical empty
  object — the fingerprint doctrine has no exemptions.
- `agree_spec` refuses until `configure` is recorded.

### Drift and cascade

Epic-config drift mirrors governance drift, not the artifact cascade:

- The overlay fingerprint joins the execution-gate chokepoint for all
  governed verbs (checked alongside governance and intake freshness).
  Editing the overlay mid-epic surfaces an `epic_config_drift` blocker in
  `next` (actions emptied, re-approval command named), and refuses
  admission/merge until `intake configure --approved-by` re-records.
- Re-recording `configure` does **not** clear spec/research/design (their
  content does not depend on config), but **does** clear `scope.approval`
  — the plan was approved under the old effective config (models,
  riskRules, review policy all feed apply-time derivation), so execution
  resumes only after a `plan apply --plan ... --approved-by` re-stamp.
- Precedence order at the chokepoint: governance → epic config → intake
  artifacts → plan composite. One blocker at a time, remedies in that
  order.

### Global config changes mid-epic

Unchanged: global config edits still trip governance drift and
`constitution amend`. Since resolution is dynamic, a re-approved global
change flows into the merged view; the plan re-stamp requirement above
applies identically (scope.approval is cleared by governance re-approval
already — existing behavior, now documented as covering the merged view).

## 3. CLI version and releases

- **`VERSION` file** at repo root, single source of truth (`0.x.y` to
  start; the installer copies it next to the package).
  `wddctl --version` prints `wddctl <version> (<short-sha-if-known>)` via
  argparse's version action; `doctor` includes it in its report.
- **Semver automation on main:** a GitHub Actions workflow on push to
  `main` computes the bump from conventional-commit subjects since the
  last tag — `feat:` → minor, `fix:`/`chore:`/`docs:` → patch,
  `!`/`BREAKING CHANGE` → major — writes `VERSION`, commits
  `chore(release): vX.Y.Z [skip ci]`, and tags `vX.Y.Z`. Merge commits
  are classified by their branch's constituent commits. No publishing, no
  changelog generation in this phase — tag + file only.
- The schema version (v6) and the CLI version are independent; `migrate`
  keys off the schema, never the CLI version.

## 4. Compatibility and migration

- **Schema v6.** `state.epic` (slug or null) and
  `state.intake.configure` are required for non-legacy states.
- **`wddctl migrate`** converts v5: creates `epics/<scope-id-slug>/`
  (slugified from the active scope id, or `legacy` when no scope), moves
  `spec.md`, `design.md`, `plan.json`, `tasks/` into it, rewrites the
  state's recorded artifact paths, writes an empty overlay, and marks
  `intake.configure = {"legacy": true}` — migration-artifact exemption
  exactly like the v5 ladder exemption; constructors never mint it.
- Flat-layout reads are **not** supported post-migration — one resolver,
  no dual paths. Archived v5 records stay readable where they are.
- Skills (`wdd-intake`, `wdd-setup`, `wdd-plan`, router) and
  `workflow.md` update in the same phase, prose after machinery, as 6c
  followed 6a/6b.

## Non-goals

- **Concurrent epic execution.** One active epic; the leases and
  conflict-domain model stay single-scope. Explicitly parked.
- **Concurrent intake** (walking epic B's ladder while A executes) —
  attractive and structurally possible once epics are directories, but
  parked to keep this phase honest; noted as the natural v7 follow-up.
- **Per-epic runners or worktree roots** — machine-bound, global-only.
- **Publishing/packaging automation** beyond the version tag.
