# Epic-Scoped State Implementation Plan (Phase 7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Everything in the epic-scoped-state spec except skill prose: epic directories with typed path namespaces, the layered config overlay with projected digests, the configure gate, the transactional archive, v6 migration, and CLI versioning with release automation. Skills/workflow.md follow as phase 7b.

**Architecture:** Two foundations first — the typed path resolver (`wave_delivery/paths.py`, new) and the layered config machinery (`config.py`) — then schema v6 + migration on top of both, then the verbs and gates, then the archive transaction, versioning, and an e2e. Choreography in the state machine, judgment in skills, as always.

**Tech Stack:** Python 3 stdlib only; `unittest`; GitHub Actions for the release workflow (shell + python, no third-party actions beyond checkout).

**Spec:** `docs/superpowers/specs/2026-08-03-epic-scoped-state-design.md` at 415eb22 — three Sol rounds closed; spec governs; flag conflicts in reports.

## Global Constraints

- Stdlib only; errors via `errors.py`; state writes via `apply_mutation`; new event types: `epic.created`, `intake.configured`, `scope.archive_blocked`.
- One typed resolver, one digest function, one layered snapshot — NO site may resolve paths, merge config, or hash views its own way. Cross-reference comments at every consumer.
- Schema v6; migration is per-field per the spec's table; constructors never mint legacy/migration exemptions.
- New tests per task in `tests/test_epics.py` (new file; copy helper conventions from `tests/test_handover.py` — local helpers, no cross-file imports). Migration tests exercise every task status × legacy/non-legacy × with/without snapshots.
- Full suite FOREGROUND once per task (gpg workaround `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false`; ~7-8 min — wait it out, never background-and-park; check exit code directly, not through a pipe). Conventional commits; never push.

---

### Task 1: Typed path resolver

Spec §1 ("Typed path namespaces, one resolver").

**Files:** create `wave_delivery/paths.py`; consumers rewired in `plan.py` (hashing, specPath/context validation), `intake.py` (artifact refs, drift), `handover.py` (materialization, input binding), `runner.py` (snapshot resolution), `lint.py`; tests in `tests/test_epics.py`.

**Interfaces:**
- `resolve_artifact(ref, *, wdd_dir, epic) -> Path`: lexical namespaces — `shared-context/` → global; `tasks/`, `research/`, `spec.md`, `design.md`, `plan.json` → `epics/<epic>/`; reject absolute paths, `..` segments, refs starting `epics/`, `archive/`, `dispatch/`, and the reserved name `record.json`; `ValidationError` with the offending ref named. `epic=None` (pre-v6 call sites during the transition within this branch) resolves epic-namespace refs against flat `.wdd/` so tasks land green individually — Task 4 flips the wiring.
- Anchors (`#…`) stripped before resolution, preserved by callers (existing behavior).
- Serialized forms stay namespace-relative; no caller stores resolved paths.

**Test contract:** each namespace maps correctly; every rejection class refuses with the ref named; anchor handling unchanged; flat fallback with `epic=None` byte-compatible with today's resolution (regression-pinned against current behavior).

Commit: `feat(paths): typed artifact namespaces behind one resolver`

---

### Task 2: Layered config — overlay, digests, projections, derive_effective

Spec §2 (resolution, allowlist, digest function, projections, layered snapshot).

**Files:** `wave_delivery/config.py`; tests in `tests/test_epics.py`.

**Interfaces:**
- `load_layers(wdd_dir, epic) -> {defaults, global, overlay, effective}` — each layer validated at capture; overlay allowlist is the exact dotted-leaf set from the spec (`models.planning`, `models.implementation`, `models.review`, `verification.commands`, `verification.unavailableJustification`, `merge.surface`, `riskRules`, `review.policy`); any other overlay key rejected by name at load, `config set --epic`, and configure approval. Missing overlay file = empty overlay.
- `effective_config_digest(view) -> "sha256:..."` — the governance-fingerprint idiom: default-hydrated, source-markers stripped, recursively sorted keys, array order preserved, UTF-8, fixed separators, duplicate keys and non-finite numbers rejected at parse. The ONLY fingerprint implementation; settings frozen (breaking = migration).
- `project(view, purpose)` for purposes `plan`, `taskReview`, `finalReview`, `taskVerification`, `finalVerification` — key subsets per spec §2; digests via the same function.
- `derive_effective(layers, patch) -> layers'` — pure; patch applies to the overlay layer, revalidated with the loader's validators, effective recomputed. Override removal reveals the retained global value (pin with a masking test).
- `config get`/`set` `--epic` flag: set writes the active epic's overlay (allowlist-checked); get prints merged view with per-key `source` markers (`epic`/`global`/`default`). Source markers never enter any digest.

**Test contract:** per-key fallback; masking + removal-reveals-global; allowlist rejections by name at all three entry points; digest stability (key order, unicode, empty overlay ≠ missing key confusion impossible); projection partitioning (models.planning edit changes no evidence projection; verification edit changes exactly the two verification projections); derive_effective rejects what loading rejects.

Commit: `feat(config): epic overlay with layered resolution and projected digests`

---

### Task 3: Schema v6 + migration

Spec §4 (per-field table), §2 (evidence digest fields), §1 (state.epic).

**Files:** `wave_delivery/schema.py` (v6: `epic`, `intake.configure`, `archivePending`, `archiveBlocked`, evidence digest + resolved-decision fields), `wave_delivery/migration.py`, tests in `tests/test_epics.py`.

**Interfaces:**
- v6 validation: `epic` slug-or-null (`[a-z0-9][a-z0-9-]{1,63}`); `intake.configure` required non-legacy (`{by, at, sha256}` over the full effective view) with the two migration exemption shapes (`{"legacy": true, "sha256": ...}` under configure and the digest addition under `intake.legacy`) — constructors never mint them; `archivePending {slug, sourceRevision, archivedAt, recordSha256}` and `archiveBlocked {slug, collidingPath, at}` nullable; review evidence gains `resolvedRisk`/`reviewModel` + projection digest fields; verification evidence gains its projection digest.
- `migrate` v5→v6 per the spec table: move files into `epics/<slug>/` (slug from scope id, else `legacy`); rewrite `specPath`/`context`/`inputs[].path` in lockstep (digests untouched — pin input binding stays green); write `manifest.json` into existing attempt snapshot dirs naming the brief (dispatch prefers manifest, falls back to basename match, never lexicographic); stamp migration-time full digest under the configure/legacy exemption and projected digests onto existing evidence; refuse reserved-name collisions with a rename remedy; `shared-context/` untouched.

**Test contract:** the full spec matrix (task status × legacy/non-legacy × snapshots); post-migration input gate green; post-migration overlay edit → configure drift (exemption guards attribution, not drift); reserved-name refusal; archived v5 records untouched.

Commit: `feat(schema): v6 — epic identity, configure record, migration table`

---

### Task 4: Epic lifecycle — `epic new`, ladder wiring, flat-path retirement

Spec §1 (slug at the top of the ladder, canonical identity).

**Files:** `wave_delivery/cli.py`, `wave_delivery/setup.py` (`create_epic` action ahead of the ladder), `wave_delivery/intake.py`/`plan.py`/`lint.py` (epic-aware resolution — flip Task 1's `epic=None` fallback off for v6 states), `wave_delivery/engine.py` (epic.created), tests.

**Interfaces:**
- `wddctl epic new --slug S [--title T]`: governed; slug validated + unique vs `epics/` and `archive/`; refuses when an epic is active or the directory holds a `record.json`; mkdir + empty overlay + `state.epic` in ONE locked mutation; idempotent adoption of the crash-orphan shape (dir with only empty overlay, state.epic null); `doctor` reports orphans.
- `setup_next_actions`: `create_epic` after ratify, before `configure_epic` → `agree_spec`; judgments name `wdd-intake`.
- Scope id derives as `SCOPE-<slug>`; `plan apply` rejects any other id on v6 states; `plan template` emits the derived placeholder note. Rename unsupported — no verb exists.

**Test contract:** ladder order; uniqueness incl. archived slugs; single-mutation crash-shape adoption; scope-id derivation + mismatch rejection; v6 states never resolve flat paths (regression: flat file present but epic file absent → refusal, not fallback).

Commit: `feat(epic): slug-canonical epic lifecycle and ladder wiring`

---

### Task 5: Configure gate, drift, and evidence binding

Spec §2 (configure step, drift/cascade, resolve-once, decision binding), §1 amendment (amend clears scope.approval).

**Files:** `wave_delivery/cli.py` (admission snapshot threading, chokepoint precedence governance → epic config → intake → plan), `wave_delivery/intake.py` (`intake configure` verb), `wave_delivery/engine.py` (amend transition clears scope.approval), `wave_delivery/plan.py` (re-stamp re-derives risk for ALL tasks), `wave_delivery/review.py`/`finalize.py`/`merge.py` (evidence records + gate comparisons of projected digests and resolved decisions), tests.

**Interfaces:**
- `wddctl intake configure --approved-by NAME` / `--use-defaults --by NAME`: records `{by, at, sha256}` over the derived post-mutation full view (layered snapshot + `derive_effective`); `agree_spec` refuses until recorded; `epic_config_drift` blocker + chokepoint refusal on mismatch; re-record clears `scope.approval` only.
- Resolve-once: CLI resolves layers + digests at admission, threads the snapshot; no handler re-reads config (grep-audit as part of the task).
- Evidence: task review records `resolvedRisk` + `reviewModel` + `taskReview` digest; final review `finalReview` digest + model; verifications their projections. Gates re-derive and compare; risk raises recompute review-required at merge (upward-only); `constitution amend` clears scope.approval (regression test).

**Test contract:** the three Sol P1 scenarios from round 1 (global-change bypass, started-task risk raise, obsolete verification evidence) each refused; check/use race closed (overlay edit after admission cannot influence recorded digest); unrelated-key edit stales nothing; precedence order pinned.

Commit: `feat(configure): epic config approval, drift gate, and evidence binding`

---

### Task 6: Transactional archive

Spec §1 (archive transaction, recovery matrix, lock layering).

**Files:** `wave_delivery/finalize.py` (archive_scope rewrite), `wave_delivery/store.py` (`read_raw_unlocked`/`recover_locked`/`load_recovered`), `wave_delivery/cli.py` (`archive_blocked` in next; recovery on load), tests.

**Interfaces:** exactly the spec's four steps, journal shape, deterministic record generation (state at sourceRevision minus the journal event + journaled archivedAt), and the six-row recovery matrix incl. the durable `archiveBlocked` resting state and active-epic-scoped cleanup (recovery never touches `archive/`). `apply_mutation` runs `recover_locked` under its existing lock.

**Test contract:** every matrix row simulated (crash injection between each step); record byte-reproducibility; collision → blocker → resolve → re-archive; recovery never reads `archive/` (assert by permission-denying the dir in a test); no self-deadlock (mutation during recovery-needed state).

Commit: `feat(archive): journaled epic archive with crash recovery`

---

### Task 7: Versioning and release automation

Spec §3.

**Files:** `VERSION` (bootstrap `0.1.0`), `wave_delivery/cli.py` (`--version`, doctor report), `scripts/install_wave_delivery.py` (copy VERSION), `scripts/release.py` (parser + CAS logic, unit-testable), `.github/workflows/release.yml`, tests in `tests/test_release.py` (new; pure-python parser/CAS tests, no network).

**Interfaces:** per spec §3 — full-message conventional-commit parser (header type/scope/`!`, BREAKING footers, squash-title classification, `Release-Bump:` trailer override, merge commits excluded/constituents included, bootstrap); bounded CAS (5 attempts, backoff) with `git push --atomic origin main vX.Y.Z`; triage: matching tag → idempotent success, moved main → recompute, foreign tag → fail closed naming both commits; workflow concurrency group `cancel-in-progress: false`.

**Test contract:** the spec's parser matrix; CAS triage decisions unit-tested with a fake git; `--version` output shape; installer carries VERSION.

Commit: `feat(release): wddctl --version and semver automation on main`

---

### Task 8: E2E + docs

**Files:** `tests/test_epics.py` (e2e), `docs/wddctl.md`, `docs/artifact-schema.md`.

**Test contract (e2e):** two full epics back to back — init → ratify → `epic new` → configure (overlay with a merge.surface override) → spec/research/design → plan → one task through worker/review/verify/merge with a mid-epic overlay edit refused and re-approved → finalize → delivered → archive → second `epic new` with `--use-defaults` proving total no-leak and no path collision; plus a v5-fixture migration walked to delivered. Docs: epic layout, configure verb, overlay + projections, archive transaction + recovery, `--version`; real transcripts, stub-labeled.

Commit: `test+docs: epic lifecycle e2e and v6 documentation`

---

## Self-review checklist (after Task 8)

- Spec §1: dirs/slug/namespaces (T1,T4), archive transaction (T6). §2: overlay/digests/projections/derive_effective (T2), configure gate + evidence binding (T5). §3: versioning (T7). §4: migration table (T3).
- All three Sol rounds' scenarios have named regression tests.
- One resolver / one digest / one snapshot — grep-audit shows no bypassing consumer.
- Deferred to 7b (NOT here): all skill prose, workflow.md.
