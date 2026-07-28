# Finalize Phase Implementation Plan (Phase 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the last task merges, the scope stops going quiet: `next` emits scope-level actions — final review of the epic branch against `.wdd/spec.md`, full verification, handoff preparation — and the scope reaches `delivered` only when Git proves the human merged the epic branch into the target. Spec §6. Plus one parked phase-4 defect fixed first.

**Architecture:** A new optional `state["finalize"]` section (review / verification / handoff / delivered evidence), mutated only through `wddctl finalize ...` verbs built on `apply_mutation` with scope-level event types (`final.review_recorded`, `final.verification_recorded`, `handoff.prepared`, `scope.delivered`). `derived_phase` grows two values: `finalize` (scope present, ratified, ≥1 task, all tasks terminal) and `delivered`. The delivery proof reuses phase 4's either-ref ancestry machinery against the **target branch**. Handoff on the pr surface pushes the epic branch and opens the epic→target PR via the existing `github.py`; wddctl never merges it.

**Tech Stack:** Python 3 stdlib only; `unittest`; fake-gh + bare-origin scaffolding from `tests/test_execution_surfaces.py` (copy the helper pattern into the new test file; no cross-file imports).

## Global Constraints

- No new runtime dependencies. Errors via `wave_delivery/errors.py`. All state writes through `apply_mutation`/`StateStore` under the lock.
- Spec §6 of `docs/superpowers/specs/2026-07-28-onboarding-and-workflow-redesign-design.md`: final review vs spec.md with P1/P2/P3 semantics (blocking severities from config `review.blockingSeverities`), full verification, handoff PR, **the final merge is human-owned — wddctl refuses to perform it**, `delivered` only on observed merge.
- Evidence pinned to the epic branch head SHA: any new commit on the base branch invalidates recorded final review/verification (same doctrine as task evidence).
- New tests in a NEW file `tests/test_finalize.py` with local helpers.
- Conventional commits; never skip hooks; do not push. Full suite green after every task (gpg workaround: `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false`; `-c commit.gpgsign=false` in scratch repos).

---

### Task 1: Fix the parked phase-4 audit defect — truthful event type on PR upgrade

Parked ruling from phase 4's final review: `leases.py`'s `branch:`→URL upgrade path (inside the `task.head_updated` "nothing moved" branch) persists an event **typed** `task.head_updated` while deliberately not applying that event's semantics; the outcome dict says `task.pr_upgraded` but `apply_mutation` resolves the logged type from the pre-mutation closure.

**Files:** Modify `wave_delivery/leases.py`; Test: `tests/test_finalize.py` (first test class; the scaffolding it needs — repo, init-to-submit flow with fake gh — is the file's shared helper set).

**Interfaces:** The persisted event for the upgrade transaction has `"type": "task.pr_upgraded"`. Mechanism: the `event_type` callable passed to `apply_mutation` must resolve `task.pr_upgraded` for this branch (it receives the locked pre-mutation state — it CAN see the same conditions the mutator sees: head unchanged + current pr is a `branch:` fallback + new pr is a real URL; mirror that predicate, or restructure so the mutator's outcome drives a resolved string — read `apply_mutation`'s contract first and pick the cleaner mechanism; document the choice).

**Test contract:** drive a task to the upgrade scenario (submit with `FAKE_GH_FAIL=1` → fallback; resubmit without → URL), then assert `state["events"][-1]["type"] == "task.pr_upgraded"` AND review/verification/status untouched. Plus: a genuine head change still logs `task.head_updated` with evidence invalidation.

Commit: `fix(leases): log pr upgrades under their own event type`

---

### Task 2: Phases `finalize` and `delivered` + the `finalize` state section

**Files:** Modify `wave_delivery/schema.py` (`derived_phase`, `validate_state`); Test: `tests/test_finalize.py`.

**Interfaces:**
- `derived_phase(state)`: existing rules first; then, when scope present + ratified + `state["tasks"]` non-empty + every task status in `{"done","cancelled"}`: `"delivered"` if `(state.get("finalize") or {}).get("delivered")` else `"finalize"`. A scope with zero tasks stays `"execute"` (plan validation forbids empty task lists anyway).
- `validate_state`: optional top-level `finalize` mapping with optional keys `review`, `verification`, `handoff` (each object-or-absent) and optional `delivered` (object with non-empty-string `at`, `by`, `headSha` when present). Absent section valid (all existing states).

**Test contract:** phase transitions (all-done → finalize; delivered marker → delivered; one task still in_progress → execute); validate_state accepts absent/valid sections, rejects malformed `delivered`.

Commit: `feat(schema): finalize and delivered phases with scope-level evidence section`

---

### Task 3: `wddctl finalize` verbs — review, verify, handoff, delivered

**Files:** Create `wave_delivery/finalize.py`; modify `wave_delivery/cli.py` (subparser `finalize` with subcommands `review`, `verify`, `handoff`, `delivered`, `status`); Test: `tests/test_finalize.py`.

**Interfaces (all verbs refuse outside the finalize/delivered phases; all are governed verbs — add to `GOVERNED_VERBS`):**
- `finalize review record --findings '[...]' --reviewer NAME --repo .`: findings validated with the existing `review.validate_findings`; evidence recorded under `finalize["review"]` pinned to the CURRENT base-branch head SHA (resolve `scope.baseRef` in the repo). Outcome `passed` when no finding's severity is in config `review.blockingSeverities` (default P1/P2), else `blocked`.
- `finalize verify record --status passed|failed|unavailable --command CMD --repo .` (mirror the task-level verify record contract, incl. `--justification` for unavailable): recorded under `finalize["verification"]`, pinned to base head.
- `finalize handoff --repo .`: requires review outcome `passed` + verification `passed`, both pinned to the CURRENT base head (stale evidence → `IllegalTransition` naming what to redo). Surface `"pr"`: `github.push_branch(repo, baseRef)` then `github.create_pr(repo, baseRef, targetBranch, title="WDD scope <id>", body=summary)`; records `finalize["handoff"] = {"pr": url, "headSha": ..., "at": ...}`. Surface `"local"`: records handoff with `pr: None` and the result carries instructions text (push/PR are the operator's). **Never merges.** Target branch comes from config `branching.targetBranch` (require config; legacy no-config scopes get a clear error naming the migration path).
- `finalize delivered --by NAME --repo .`: proves the base branch head is reachable from the TARGET branch using phase 4's either-ref approach (fetch best-effort, local target or `origin/<target>`); success → `finalize["delivered"] = {"at","by","headSha"}` via event `scope.delivered`; failure → `IllegalTransition` ("the final merge has not happened"). wddctl performing the target merge itself: there is deliberately no code path.
- `finalize status`: prints the section + phase.
- Event types: `final.review_recorded`, `final.verification_recorded`, `handoff.prepared`, `scope.delivered` — via `apply_mutation` custom mutators (pattern: `migrate_governance`, `observe_merge`).

**Test contract (the heart of the phase — be thorough):** refusal outside finalize phase; review with a P1 → blocked outcome and handoff refusal; clean review + passed verify → handoff records (pr surface: fake-gh log shows push+create, url recorded; local: pr None + instructions); stale evidence after a new base commit → handoff refuses; delivered refuses before target merge, succeeds after merging base→target locally; delivered via stale local target but merged origin target (either-ref) succeeds; `event apply --event scope.delivered` refused (extend the escape-hatch guard the same way task.merged is refused).

Commit: `feat(finalize): scope-level review, verification, handoff, and observed delivery`

---

### Task 4: `next` and `status` drive the finalize phase

**Files:** Modify `wave_delivery/cli.py` (`next`/`status` handlers route `derived_phase == "finalize"`/`"delivered"`), `wave_delivery/finalize.py` (a `finalize_next_actions(state, wdd_dir, repo, *, state_path=None) -> dict` mirroring `setup_next_actions`' shape); Test: `tests/test_finalize.py`.

**Interfaces:** one action at a time, priority order: `final_review` (no/blocked/stale review — recordWith `finalize review record`, judgment: dispatch a reviewer per wdd-review's final-review contract against `.wdd/spec.md`) → `assign_final_fixes` (review blocked: judgment names blocking findings; no command — fixes are new commits, which re-stale the evidence naturally) → `final_verification` (recordWith `finalize verify record`) → `prepare_handoff` (command `finalize handoff`) → `await_delivery` (no command; judgment names the handoff PR or instructions; recordWith `finalize delivered --by NAME`). Phase `delivered`: `next` returns empty actions with `"phase": "delivered"`; `status` shows the finalize section. Models: `final_review` carries `models.review` when configured (reuse the phase-4 parameter plumbing).

**Test contract:** the full ladder — each state of the finalize section produces exactly the expected single action; delivered phase yields empty actions; e2e: mini scope (1 task) through merge → next says final_review → record clean → final_verification → verify → prepare_handoff → handoff (local surface) → await_delivery → merge base into target → delivered → next empty.

Commit: `feat(next): drive the finalize ladder to delivered`

---

### Task 5: Docs + skills

**Files:** Modify `docs/wddctl.md` (finalize verbs + phase ladder, real transcripts via scratch repo + fake-gh where pr surface shown), `docs/workflow.md` (a short "Finishing a scope" section before the appendix — the narrative currently ends at reconciliation; extend with the finalize ladder, real transcripts), `skills/wdd-run/SKILL.md` (finalize-phase actions: dispatch final reviewer, run verification, handoff, await delivery — the human-owned final merge is one of the hard-rule EXCEPTIONS already carved out), `skills/wdd-review/SKILL.md` (final-review contract: review the whole epic branch diff against `.wdd/spec.md` acceptance criteria; same P1/P2/P3 semantics; record via `finalize review record`), `README.md` (lifecycle sentence: scopes end in an observed human merge).

Verify every command against the CLI; transcripts real; full suite sanity. Commit: `docs: document the finalize ladder and delivered state`

---

## Self-review checklist (after Task 5)

- Spec §6: final_review vs spec.md (T3/T4), final_verification (T3/T4), prepare_handoff pushing epic branch + PR with wddctl refusing the final merge (T3), `delivered` on observed merge (T3/T4). Phase-4 parked defect closed (T1).
- The spec's finalize actions emerge from `next` (T4) — same choreography-in-state-machine principle as setup.
- Deliberately NOT here: PR draft/ready toggling, human-approval tracking (both ledger-deferred in phase 4); demo-prep (enterprise overlay concern); Jira provider (consolidation phase).
