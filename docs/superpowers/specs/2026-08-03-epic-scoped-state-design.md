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
- **Typed path namespaces, one resolver.** Artifact references are
  resolved lexically, never by existence probing: a ref beginning
  `shared-context/` always means the global directory; `tasks/`,
  `research/`, `spec.md`, `design.md`, `plan.json` always mean the active
  epic's directory. Absolute paths, `..` segments, and refs beginning
  `epics/`, `archive/`, or `dispatch/` are rejected outright. Plan
  hashing, intake recording/drift, handover materialization, input
  binding, and snapshot resolution all use this single typed resolver —
  no site may resolve paths its own way. Serialized forms in `plan.json`
  and state stay in namespace-relative form, so plans remain
  copy-portable between epics.
- **The slug is the canonical scope identity.** Scope id is always
  `SCOPE-<slug>`; a plan naming any other id is rejected at apply (v6
  drops the override). Slugs are immutable — rename is unsupported and
  refused; retiring a slug means archiving it. Branch and worktree
  derivation therefore never collide across epics, including with
  archived ones (uniqueness is checked against `epics/` and `archive/`).
  `epic new` performs directory creation and state adoption as one
  mutation under the state lock; a crash-orphaned directory (dir exists,
  `state.epic` null, directory empty but for the empty overlay) is
  adopted idempotently by re-running the same `epic new`, and `doctor`
  reports any orphan it finds.

### Archive moves the folder

`wddctl scope archive` (delivered-phase only, unchanged gate) is a
recoverable transaction under the state lock:

1. Write `record.json` (today's archive JSON) **inside** `epics/<slug>/`.
2. Record `state.archivePending = <slug>` (one durable journal field).
3. One atomic rename: `epics/<slug>` → `archive/<slug>`. Refuses if the
   destination exists — no nesting, no overwrite.
4. Reset state to post-ratification setup, clearing `state.epic` and
   `archivePending`. The ladder restarts at `create_epic`.

Recovery is defined per crash point: `archivePending` set + source dir
present → retry from step 3; `archivePending` set + source gone +
destination present → complete step 4; `record.json` present without
`archivePending` → remove it and restart. Every state-loading command
performs this recovery before acting, so a crashed archive can never
leave `state.epic` pointing at a missing directory across more than one
invocation.

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

**The overlay allowlist is structural, not prose.** The exact dotted
leaves an overlay may contain: `models.planning`,
`models.implementation`, `models.review` (string or tier-object forms as
in global config), `verification.commands`,
`verification.unavailableJustification`, `merge.surface`, `riskRules`,
`review.policy` (the real key behind "reviewPolicy default"). Any other
key — `runners`, `worktrees.root`, branch settings, anything unlisted —
is rejected by name at every entry point: overlay load, `config set
--epic`, and `intake configure` approval. A hand-edited overlay with a
forbidden key cannot be approved; machine-bound and repository authority
stays under constitution approval only. `config set`/`get` gain `--epic`:
sets write the active epic's overlay (allowlist-checked), gets print the
merged view with a `source` marker per key (`epic` / `global` /
`default`).

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
- The record carries `{by, at, sha256}` in `state.intake.configure`,
  where the digest is over the **canonical resolved effective config** —
  the merged view of overlay + global config, canonically serialized —
  not the overlay bytes alone. A global config change therefore makes the
  configure record stale *by construction*, with no reliance on any other
  transition remembering to cascade. An empty overlay still produces a
  real digest — the fingerprint doctrine has no exemptions.
- Belt and braces: the v6 `constitution amend` transition additionally
  clears `scope.approval` explicitly (v5's does not — it only replaces
  `state.constitution`; this is a required transition change with a
  regression test, not an assumption).
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
- **Risk re-derivation covers started tasks.** The plan re-stamp
  recomputes derived risk for *every* task, not only `todo` ones. A
  non-todo task whose risk rises keeps its history, but its review gate
  recomputes against the new risk at verb time: if the new effective
  policy requires a review that was never recorded, `merge` refuses until
  one is. Risk raises are honored immediately; a risk *drop* never
  removes an already-applicable requirement (upward-only at execution,
  matching riskRules doctrine).
- **Evidence binds to the effective config it ran under.** Every
  verification and review record (task-level and finalize-level) gains an
  `effectiveConfig` digest field — the §2 resolved-config digest at
  recording time. Gates that consume evidence (`merge`, `freshness`,
  finalize handoff/delivered) compare the recorded digest against the
  current one: a mismatch on verification-relevant keys makes the
  evidence stale exactly like a headSha mismatch does today, with the
  refusal naming the re-run command. Merged evidence remains history, as
  ever.
- **Resolve once per invocation.** The CLI resolves the effective config
  and its digest exactly once, at admission, and threads that immutable
  snapshot through the command; no handler re-reads config files
  mid-command. This closes the check/use race — an overlay edited after
  admission cannot influence evidence recorded during the same
  invocation, and the digest recorded on evidence is the digest that was
  gate-checked.
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
  `main`, in a non-cancelling concurrency group (one release run at a
  time, queued not skipped). It fresh-fetches, recomputes the bump
  immediately before publishing, and pushes the release commit and tag
  **atomically in one `git push` of both refs**; if main moved since
  computation, it recomputes and retries (compare-and-swap loop). Bump
  classification uses a full-message Conventional Commits parser, not
  subject-grepping: type and optional scope from the header
  (`feat(api)!:` counts), `BREAKING CHANGE:`/`BREAKING-CHANGE:` footers
  scanned in the body, `!` on any type → major; `feat` → minor;
  everything else → patch. Commit selection is `last-tag..HEAD` with
  merge commits themselves excluded and their constituent commits
  classified (full DAG range, not first-parent), so squash and true
  merges classify identically. No prior tag → bootstrap `v0.1.0`. The
  release commit is `chore(release): vX.Y.Z [skip ci]`; a parser test
  matrix (scoped types, footer-only breaks, merge-only pushes, bootstrap)
  ships with the workflow. No publishing, no changelog generation in this
  phase — tag + file only.
- The schema version (v6) and the CLI version are independent; `migrate`
  keys off the schema, never the CLI version.

## 4. Compatibility and migration

- **Schema v6.** `state.epic` (slug or null) and
  `state.intake.configure` are required for non-legacy states.
- **`wddctl migrate`** converts v5 by a per-field table, tested for every
  task status × legacy/non-legacy intake × with/without snapshots:
  - Files: `spec.md`, `design.md`, `plan.json`, `tasks/`, and any
    research artifacts recorded in `intake.research.artifacts` move into
    `epics/<slug>/` (slug from the active scope id, or `legacy` when no
    scope). `shared-context/` stays put.
  - `tasks[].specPath` and `tasks[].context`: rewritten only where the
    namespace rule requires (epic-owned refs keep their
    namespace-relative form — `tasks/TASK-001.md` is already correct
    under the typed resolver; `shared-context/` refs are untouched).
  - `tasks[].inputs[].path`: rewritten in lockstep with the fields they
    were recorded from; digests unchanged (bytes moved, not edited), so
    input binding stays green across migration — pinned by test.
  - Attempt snapshots: immutable, left in place. Migration writes a
    `manifest.json` into each existing attempt dir naming the brief file
    explicitly; the dispatch prompt assembler prefers the manifest and
    only falls back to basename matching for pre-manifest dirs — never
    lexicographic guessing.
  - Intake: a v5 legacy scope (`intake.legacy` sole key) stays exactly
    that — the legacy exemption covers `configure` and the epic ladder
    wholesale; migration adds nothing to it. A non-legacy v5 scope keeps
    its real spec/research/design records (fingerprints still valid —
    file *content* did not change, and recorded artifact paths are
    rewritten alongside) and gains `configure: {"legacy": true}` — the
    same migration-artifact doctrine: constructors never mint it, and it
    exempts only the configure gate, nothing else.
  - Finalize evidence: existing records predate effective-config digests;
    they are grandfathered valid for the migrated epic only (documented
    as such in the record), and the first configure re-approval clears
    them like any config-relevant change.
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
