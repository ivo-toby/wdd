# Intake Ladder & Governance Implementation Plan (Phase 6a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The state-machine half of the front-half spec: schema v5, the intake ladder (spec → research → design) with fingerprint-bound approvals and cascade invalidation, the plan-approval composite, the drift extension of the execution gate, scope archive, the deliverable-command flow into finalize, and the new lint codes. Phases 6b (snapshots, input binding, dispatch/runners, model routing) and 6c (skills, docs) follow as separate plans once this lands.

**Architecture:** A new `wave_delivery/intake.py` owns artifact hashing, the intake evidence records, the rung verbs, and the ladder logic consumed by `setup_next_actions`. Drift checking extends the existing single execution gate (`require_fresh_governance` grows a sibling that all governed verbs already reach through the same CLI chokepoint). Cascade is mechanical: rung verbs clear downstream records and the scope approval. `new_setup_state()` and `new_state()` BOTH produce v5 with an empty (enforced) `intake: {}` — **migration is the only producer of `intake: {"legacy": true}`**, ever; a constructor that mints exemptions would be a non-migration bypass of the whole doctrine. `state_from_plan()` survives only as a transient projection helper (lint/preview); it never persists. Tests that hand-build active scopes either walk the ladder or set `state["intake"] = {"legacy": True}` explicitly in the test body — visible, greppable, and honest about simulating a migrated scope.

**Tech Stack:** Python 3 stdlib only; `unittest`; existing helpers/fixtures.

**Spec:** `docs/superpowers/specs/2026-08-01-front-half-intake-ladder-design.md` — sections cited per task. Where this plan and the spec disagree, the spec governs; flag the conflict rather than improvising.

## Global Constraints

- No new runtime dependencies. Errors via `errors.py`; all state writes through `apply_mutation` under the lock; event types for new records: `intake.spec_approved`, `intake.research_recorded`, `intake.design_approved`, `scope.archived`.
- SHA-256 fingerprints use the `governance_fingerprint` idiom (`"sha256:..."` prefix), one helper per artifact: `artifact_sha256(path)`.
- Every rung verb refuses: before ratification, on legacy scopes (`intake.legacy`), and in the delivered phase. Rung verbs are legal in setup AND execute phases (drift re-approval).
- Test churn is expected and budgeted: every helper walking init→ratify→apply gains the ladder steps. A canonical helper (below) is added once per test file, replacing ad-hoc sequences. Full suite green at the end of every task (gpg workaround: `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false`).
- New tests in `tests/test_intake.py` (new file, local helpers); modifications to existing test files only where helpers/ladder-churn demand.
- Conventional commits; never push.

**Canonical test helper** (added to each test file that needs it, adapted to its local `_cli`):

```python
def _walk_intake(state, wdd, approver="t"):
    (wdd / "spec.md").write_text(
        "# Spec\n\n## Goal\n\nShip it.\n\n## In scope\n\n- x\n\n"
        "## Out of scope\n\n- y\n\n## Acceptance criteria\n\n"
        "- [ ] AC-1: the thing works\n", encoding="utf-8")
    assert _cli(state, "intake", "spec", "--approved-by", approver)[0] == 0
    assert _cli(state, "intake", "research", "--skip", "--by", approver,
                "--reason", "no external contracts")[0] == 0
    (wdd / "design.md").write_text(
        "# Design\n\n## Components\n\n- core\n\n## Interfaces\n\n"
        "- core: consumes nothing, produces lib\n\n"
        "## Integration surfaces\n\n- `src/core.py` — owned by: core task\n\n"
        "## Epic deliverable\n\nThe lib imports.\n", encoding="utf-8")
    assert _cli(state, "intake", "design", "--approved-by", approver,
                "--deliverable-command", "true")[0] == 0
```

---

### Task 1: Schema v5 — intake section, constructors, migration

Spec §1 ("no path around the ladder"), §7 (wholesale legacy exemption).

**Files:** `wave_delivery/schema.py`, `wave_delivery/migration.py`; tests in `tests/test_intake.py` (create file with local `_git_repo`/`_cli` helpers copied per convention).

**Interfaces:**
- `SCHEMA_VERSION = 5`. `validate_state`: top-level `intake` REQUIRED (object). Valid shapes: `{"legacy": True}` or any subset of `{spec, research, design}` records — `spec`: `{by, at, criteria:int, sha256}`; `research`: `{by, at, done:bool, artifacts:[{path, sha256}]}` or `{by, at, skipped:True, reason}`; `design`: `{by, at, sha256, deliverableCommand:str|None}`. All strings non-empty where present.
- `new_setup_state()` → v5, `intake: {}`. `new_state()` → v5, `intake: {}` as well — constructors never mint `legacy`; only `migrate` writes it. Hand-built test scopes set it explicitly per the architecture note above.
- `migrate`: `SUPPORTED_SOURCE_VERSIONS = {2, 3, 4}`; v4→v5 = version bump + `intake: {"legacy": True}`; chain the earlier conversions. Version-hint message covers 2, 3, 4.
- `intake_complete(state) -> bool`: legacy → True; else spec+research+design records all present.

**Test contract:** v5 validation (legacy shape, partial ladder shapes, malformed records rejected, missing intake rejected); constructors' intake values; v4 state migrates to v5+legacy; `intake_complete` truth table.

Commit: `feat(schema): v5 with required intake section and legacy exemption`

---

### Task 2: `wave_delivery/intake.py` — hashing, records, rung verbs

Spec §1 (verbs, fingerprints), §2 (artifact requirements).

**Files:** create `wave_delivery/intake.py`; wire `intake` subparser (`spec`/`research`/`design`/`status`) in `cli.py`; tests in `tests/test_intake.py`.

**Interfaces:**
- `artifact_sha256(path) -> "sha256:..."`.
- `record_spec(store, wdd_dir, *, approved_by)`: refuses unless `.wdd/spec.md` exists, non-empty, contains the four `## ` sections, and its Acceptance criteria section is **wholly numbered**: every checklist line matches `- [ ] AC-<n>:`, the numbers are unique and contiguous from 1, and no unnumbered checklist line appears in the section — final review must be able to walk 1..N with nothing outside the numbering. Records the exact N; `apply_mutation` writes `intake.spec = {by, at, criteria, sha256}` **and clears** `intake.research`, `intake.design`, and `scope.approval` if present (cascade, spec §1).
- `record_research(store, wdd_dir, *, by, done_artifacts=None, skip_reason=None)`: exactly one mode; `--done` validates each artifact exists, is a regular file under `.wdd/` (containment via resolved-path prefix check), non-empty; records path+sha256 per artifact; clears `intake.design` + `scope.approval` (cascade). Refuses before `intake.spec` exists.
- `record_design(store, wdd_dir, *, approved_by, deliverable_command)`: `--deliverable-command` is **required and non-empty** — the epic deliverable's proof is not optional (spec §2); omission refuses with a message naming why. Refuses unless `.wdd/design.md` exists and non-empty and `intake.research` recorded; records `{by, at, sha256, deliverableCommand}`; clears `scope.approval` (cascade). Schema (Task 1): `design.deliverableCommand` is a required non-empty string on v5 records.
- All three: refuse pre-ratification, on legacy, in delivered phase. `intake status` prints the section + which rung is next.
- `intake_drift(state, wdd_dir) -> None | {"rung", "recorded", "actual"}`: first mismatched rung (spec → research artifacts → design), None for legacy/no-records; missing file counts as drift (`actual: "missing:<path>"`, the phase-4 idiom).

**Test contract:** happy walk records all three with correct hashes; AC-numbering refusal; research containment/emptiness refusals; mode exclusivity; ordering refusals (research before spec, design before research); cascade — **each** of the three re-approvals proves its full clearing set: spec → research+design+scope.approval cleared; research (incl. a changed `--skip` reason) → design+scope.approval cleared; design (incl. a changed deliverable command) → scope.approval cleared; drift detection per rung incl. deleted file; legacy/delivered/pre-ratification refusals; deliverable-command omission refused.

Commit: `feat(intake): fingerprint-bound spec, research, and design records`

---

### Task 3: Ladder in `next` + `plan apply` gate + bootstrap removal

Spec §1 (rungs, refusal), §3 (approval composite), §7.

**Files:** `wave_delivery/setup.py` (`setup_next_actions` rungs), `wave_delivery/plan.py` (`apply_plan`), `wave_delivery/cli.py`; tests in `tests/test_intake.py` + churn in existing files.

**Interfaces:**
- `setup_next_actions` (ratified, non-legacy, scope null): emits exactly one of `agree_spec` / `research` / `agree_design` / `plan` per the intake records, each with `recordWith` (the intake verb) and `judgment` naming the `wdd-intake` stage (`wdd-plan` for the plan rung). Intake drift pre-apply: the mismatched rung re-emitted with a `stale: true` field.
- `apply_plan`: the `store.exists()==False` branch is **removed** — raise `ValidationError` naming `wddctl init`. For non-legacy states: refuse while `not intake_complete(state)` or `intake_drift` is non-None.
- **Approval composite**: `plan_composite(plan_dict, wdd_dir) -> "sha256:..."` over canonical normalized plan + sorted (path, sha256) of every task's brief and every `context` file. `--approved-by` records `scope.approval = {by, at, sha256: composite}`. A nonempty diff without `--approved-by` → refuse. Unchanged re-apply without the flag preserves approval (existing semantics).
- Plan task schema: optional `"context": [str]` (validated: `<path>[#anchor]`, path resolves to a regular file inside `.wdd/`, no traversal), optional `"model"`, `"reviewModel"` (non-empty strings; decoration lands in 6b). All three are **persisted into task state** (`task_state` fields, `validate_state` accepts, `MUTABLE_TASK_FIELDS` includes them so `_diff_plan` detects their changes and `_apply_plan_to_state` carries them) — otherwise Task 4's gate could not faithfully recompute the composite, and a context/model edit would masquerade as an unchanged plan. The composite covers them by construction (they are part of the normalized plan).
- Legacy states: all new gating skipped (existing behavior preserved bit-for-bit).

**Test contract:** rung sequencing through `next` (e2e: init→questions→ratify→`next` says agree_spec→…→plan→apply succeeds); apply refusals (no state; ladder incomplete; drifted rung; changed plan without approval); composite recorded and includes brief + context file bytes (edit a brief → composite differs); context containment refusals; legacy state applies exactly as before (regression pin).

**Churn (complete inventory — green-at-every-commit depends on it):**
- Task 1 fallout: schema-version literals and migration end-state assertions (`tests/test_setup_config.py` SCHEMA_VERSION==4 style checks, `test_wave_delivery` migration tests asserting v4) move to 5; hand-built scope states across all test files gain an explicit `state["intake"] = {"legacy": True}` line (or the file's helper does).
- Task 3 fallout: every init→ratify→apply helper (`test_setup_config`, `test_plan_quality`, `test_execution_surfaces`, `test_finalize`) gains `_walk_intake`; **every first apply is a nonempty diff, so every existing `plan apply` test call gains `--approved-by`** (grep-driven, mechanical); `test_wave_delivery`'s CLI e2e genuinely uses the no-state bootstrap (verified, plan.py:211 path) — convert it to init + `_walk_intake`, it is the realism test for the new lifecycle.
- Task 5 fallout: `tests/test_finalize.py` assertions on the singular `verification["command"]` shape move to the list shape.

Commit: `feat(plan): ladder-gated, composite-approved plan application; bootstrap removed`

---

### Task 4: Execution gate extension + `next` blockers

Spec §1 ("after apply" enforcement).

**Files:** `wave_delivery/config.py` or `intake.py` (gate helper), `wave_delivery/cli.py`; tests in `tests/test_intake.py`.

**Interfaces:**
- `require_fresh_intake(state, wdd_dir)`: non-legacy + scope present → raise `IllegalTransition` (message contains "intake drift" / "plan drift") when `intake_drift` is non-None or the recorded `scope.approval.sha256` no longer matches `plan_composite` recomputed from the applied state's briefs/context files (recompute against a plan dict reconstructed from state tasks — read how `_diff_plan` builds comparable structures and mirror it; document the reconstruction).
- Wired at the same chokepoint as `require_fresh_governance` for every governed verb. Execute-phase `next`: on either drift, actions emptied, blocker `intake_drift`/`plan_drift` at position 0 naming the stale rung/files and the re-approval command (`_apply_governance_drift` idiom — extract a shared helper if clean).
- Intake verbs + `plan apply --approved-by` remain legal under drift (they ARE the remedy); everything else governed refuses.

**Test contract:** post-apply spec.md edit → `start` refuses, `next` shows `intake_drift` blocker, `intake spec --approved-by` + re-walk + re-stamp restores execution; brief edit post-approval → `plan_drift` blocker, `plan apply --approved-by` (empty diff) restores; legacy scope unaffected (pin).

Commit: `feat(gate): intake and plan drift refuse execution until re-approved`

---

### Task 5: `scope archive` + deliverable command into finalize

Spec §1 (rollover), §2 (epic deliverable), §5 (multi-command evidence).

**Files:** `wave_delivery/finalize.py` (verification evidence shape; read `record_final_verification` and the handoff gates first), new archive logic in `wave_delivery/intake.py` or `finalize.py` (implementer's call, documented); `cli.py` (`scope archive` subparser); tests in `tests/test_intake.py`.

**Interfaces:**
- `final_verification` evidence becomes `{commands: [{command, status}], status}` and is recorded in **one atomic invocation**: `finalize verify record --results '[{"command":..., "status":...}, ...]'` (append semantics are forbidden — partial evidence must never exist in state). Recording validates completeness: the entries must equal, **exactly and in order**, the required list — global `verification.commands` then the scope's `deliverableCommand` (always present on v5 scopes, Task 2) — else refuse; missing, extra, or reordered entries are named in the error. Overall `status` is `passed` iff every entry passed. Legacy scopes keep the current single-command contract untouched; read sites (`_require_current_finalize_evidence`, ladder logic, `finalize status`) treat legacy records as a one-entry list.
- `wddctl scope archive --repo .`: delivered-phase only; writes `.wdd/archive/<scope-id>.json` (scope, tasks, intake, finalize, **reconcile** incl. pendingNotes, events count, archived-at) via `atomic_write_text`; then one `apply_mutation` (`scope.archived`) resetting every scope-carrying section to its `new_setup_state()` shape: `scope: null`, `tasks: {}`, `finalize` removed, `intake: {}`, `reconcile` fully fresh (counters AND pendingNotes), monitoring observations cleared. Governance untouched. The no-leak guarantee is total, not counters-only. Refuses when not delivered.
- After archive: `next` emits `agree_spec` (fresh ladder), proving rollover.
- `finalize_next_actions`'s `final_review` judgment is updated in code (not deferred prose): it names the criteria to walk (`AC-1..AC-<N>` from `intake.spec.criteria`) and the epic deliverable statement in `design.md`, alongside the existing spec.md instruction. Test asserts the judgment carries the criteria count.

**Test contract:** multi-command evidence recording + all-must-pass overall + one-entry backward read; handoff/delivered still gate on the new shape; archive refusal pre-delivered; archive → file exists with scope records, state reset, `next` says agree_spec; archived deliverable command does not appear in the next scope's finalize.

Commit: `feat(scope): archive delivered scopes; deliverable command joins final verification`

---

### Task 6: Lint pack — surfaces, criteria, deliverable

Spec §2 (`unowned_surface`), §3 (`missing_criteria`, `missing_deliverable`, `missing_context`).

**Files:** `wave_delivery/lint.py`, `docs/wddctl.md` codes table; tests in `tests/test_plan_quality.py` (the lint suite lives there).

**Interfaces:**
- `missing_deliverable`: brief lacks a non-empty `## Deliverable` section. `missing_interfaces`: brief lacks a non-empty `## Interfaces` section (the spec lints both required sections). `missing_context`: intake artifacts exist (spec/design/research recorded — lint needs `wdd_dir` state access; pass what `_overlaid_plan` can provide, document) and a task has no `context` refs. `missing_criteria`: task has no ref fully matching `spec.md#AC-<digits>` (prefix-only refs like `spec.md#AC-garbage` do not count; advisory). `unowned_surface`: parse `design.md`'s `## Integration surfaces` section for `` - `path` `` bullets; warn for any path not covered by some task's `conflictDomains` (reuse `domains` matching).
- All warnings; `--strict` semantics unchanged.

**Test contract:** each code fires on its fixture and stays quiet on the clean fixture; design-section parsing tolerates absent file/section (no crash, no finding).

Commit: `feat(lint): deliverable, context, criteria, and surface-ownership checks`

---

### Task 7: E2E + docs touch-up

**Files:** `tests/test_intake.py` (e2e), `docs/wddctl.md` (intake verbs + scope archive + updated setup transcripts — the ladder changes `next` output that existing transcripts show; re-capture for real), `docs/artifact-schema.md` (intake section, archive file).

**Test contract:** one full journey: init → questions → ratify → ladder (spec/research-skip/design) → plan apply --approved-by → task loop (one local task) → finalize ladder → delivered → scope archive → `next` says agree_spec. This is the new canonical lifecycle test.

Commit: `test+docs: full-lifecycle e2e and intake documentation`

---

## Self-review checklist (after Task 7)

- Spec §1: rungs+verbs (T2/T3), cascade (T2), bytes-binding (T2/T3), execution gate (T4), bootstrap removal (T3), rollover (T5). §2: artifact validation (T2), unowned_surface (T6), deliverable command (T2/T5). §3: composite (T3), context validation (T3), criteria mapping lint (T6). §5 evidence shape (T5). §7 legacy wholesale exemption (T1/T3/T4).
- Deferred to 6b (do NOT implement here): attempt snapshots, input-version binding + `rebind`, `inputs_changed`, `dispatch`/runners/probes, model/reviewModel decoration, review tiering. Deferred to 6c: wdd-intake/wdd-runners skills, wdd-plan slimming, workflow.md.
- Legacy pins: a migrated v4 scope must run the entire pre-existing lifecycle untouched — that regression test is not optional.
