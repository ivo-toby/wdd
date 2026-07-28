# Execution Surfaces Implementation Plan (Phase 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make config's execution knobs mechanical — model routing in `next` payloads, the PR merge surface (`merge.surface: "pr"`), and first-class human-merge mode (`merge.mode: "human"`) with Git-proven observation of merges the controller didn't make. Spec §5 (minus TDD, shipped in phase 3).

**Architecture:** Effective merge settings resolve per scope (plan-level override → config default) via one helper. A new `wave_delivery/github.py` wraps every `gh`/push interaction behind three functions so tests stub a fake `gh` on PATH and a bare-directory `origin` remote — no live GitHub anywhere. Human merges are recorded only through `wddctl merge --observed`, which proves the merge with `git merge-base --is-ancestor` before applying `task.merged` — the "live Git proves the merge" invariant extends, never weakens.

**Tech Stack:** Python 3 stdlib only; `unittest`; test doubles: fake `gh` executable + bare git remote.

## Global Constraints

- No new runtime dependencies. Errors via `wave_delivery/errors.py`.
- Spec §5 of `docs/superpowers/specs/2026-07-28-onboarding-and-workflow-redesign-design.md`; configuration matrix at the spec's end (local/controller, pr/controller, pr/human).
- state.json stays the source of truth on every surface; the PR is a projection. Mirroring failures (comments, PR creation) must never corrupt or block state recording — they degrade with a warning field.
- No live network in tests: every `gh` invocation goes through `wave_delivery/github.py`, which invokes the `gh` found on PATH; tests prepend a fixture dir containing a fake `gh` (records argv to a log file, prints canned JSON). Pushes target a local bare repo added as `origin`.
- `event apply --event task.merged` stays refused; `merge --observed` is the only observation path, and it must prove ancestry in Git before recording.
- New tests in a NEW file `tests/test_execution_surfaces.py`; local helper equivalents of `_git_repo`/`_cli` (do not import from other test files).
- Conventional commits; never skip hooks; do not push. Full suite green after every task (gpg workaround: `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false`).

---

### Task 1: Effective merge settings — per-scope override of surface and mode

**Files:**
- Modify: `wave_delivery/plan.py` (`validate_plan`: optional scope fields `mergeSurface`, `mergeMode`; carried into the normalized scope), `wave_delivery/schema.py` (`validate_state`: optional scope fields validated against the same enums), `wave_delivery/config.py` (new helper)
- Modify: `wave_delivery/cli.py` (`_overlaid_plan` result unaffected; `plan apply` writes the fields when present)
- Test: `tests/test_execution_surfaces.py` (new file)

**Interfaces:**
- Plan scope MAY carry `"mergeSurface": "pr"|"local"` and `"mergeMode": "controller"|"human"`; omitted → not stored (absent key in state scope, NOT null).
- `config.merge_settings(state, config) -> {"surface": str, "mode": str}` in `wave_delivery/config.py`: scope override wins, else `config["merge"]["surface"]`/`["mode"]`; with `config=None` (legacy repos) defaults `{"surface": "local", "mode": "controller"}` — legacy behavior unchanged.
- `_diff_plan`/`_apply_plan_to_state`: the two fields are adoptable/updatable like `reviewPolicy` (changing them is a scope change; allowed any time — they gate future actions only).

**Test contract (write these tests first; they define done):**

```python
# tests/test_execution_surfaces.py — skeleton the implementer completes
class MergeSettingsTest(unittest.TestCase):
    def test_defaults_from_config(self): ...        # no scope override -> config values
    def test_scope_override_wins(self): ...          # plan sets mergeSurface local -> effective local despite config pr
    def test_legacy_no_config_defaults_local_controller(self): ...
    def test_invalid_plan_value_rejected(self): ...  # mergeSurface "carrier-pigeon" -> ValidationError at validate_plan
```

Steps: failing tests → implement (`MERGE_SURFACES`/`MERGE_MODES` enums already exist in config.py — reuse; schema validates against them via import or duplicated frozen sets consistent with existing style) → full suite → commit `feat(config): per-scope merge surface and mode overrides`.

---

### Task 2: Model routing in `next` payloads

Spec §5: "`next`'s `start_task` and `run_review` payloads include the resolved model: implementation from the task's derived risk, review always `models.review`."

**Files:**
- Modify: `wave_delivery/engine.py` (`decorate_actions` gains optional `models: dict | None = None`; `bounded_next_actions` passes it through)
- Modify: `wave_delivery/cli.py` (`next` handler passes `config["models"]` when config exists)
- Modify: `skills/wdd-run/SKILL.md` (dispatch guidance: when an action carries `"model"`, pass it to the subagent dispatch verbatim; absent model → dispatcher's default)
- Test: `tests/test_execution_surfaces.py`

**Interfaces:**
- Actions gain `"model"` only when the resolved value is a non-null string: `start_task` and `assign_fix_writer` → `models["implementation"]["highRisk"]` if the task's risk is high else `["default"]`; `run_review` → `models["review"]`. No other action gains the key. Engine stays pure: models arrive as a parameter, engine never reads files.

**Test contract:**

```python
class ModelRoutingTest(unittest.TestCase):
    def test_start_task_carries_risk_tiered_model(self): ...   # high-risk task -> highRisk value; normal -> default
    def test_run_review_carries_review_model(self): ...
    def test_no_models_config_adds_no_key(self): ...           # models all null -> no "model" key anywhere
    def test_cli_next_includes_model_end_to_end(self): ...     # init->ratify->apply->config set models...->next start_task has model
```

Steps: failing tests → implement → full suite → commit `feat(next): route models into start, fix, and review actions`.

---

### Task 3: `wave_delivery/github.py` + fake-gh fixture + PR-surface submit

**Files:**
- Create: `wave_delivery/github.py`
- Create: `tests/fixtures/fake-gh/gh` (executable python script; `chmod +x`; add via `git add --chmod=+x` if needed)
- Modify: `wave_delivery/leases.py` (`submit_task` gains the pr-surface step) and/or `wave_delivery/cli.py` submit handler — read `submit_task` first and put the surface logic in the CLI layer if leases.py is engine-pure (preferred: CLI layer orchestrates push+PR, then calls the existing `submit_task` with the PR reference)
- Test: `tests/test_execution_surfaces.py`

**Interfaces:**
- `github.push_branch(repo, branch) -> None` (`git push -u origin <branch>`; raises `IllegalTransition` with stderr excerpt on failure), `github.create_pr(repo, branch, base, title, body) -> str` (returns PR URL via `gh pr create --head ... --base ... --title ... --body ... --json url -q .url` — check the real gh CLI contract and pin the fake to whatever you implement), `github.comment_pr(repo, pr_ref, body) -> None`.
- CLI `submit` behavior: when effective surface is `"pr"` and `--pr` was NOT given: push the task branch, create the PR (title = task title, body = brief reference + head SHA), pass the returned URL as the recorded `pr`. When `--pr` IS given, record it as today (manual override). Surface `"local"`: unchanged. PR-creation failure after a successful push: record the submission WITHOUT pr (surface the error in the result as `"warning"`) — never lose the state transition. Push failure: abort before any state change.
- Fake `gh`: appends its argv as a JSON line to `$FAKE_GH_LOG` (env var set by tests) and prints a canned URL for `pr create`; exits 0. A `FAKE_GH_FAIL=1` env toggles nonzero exit for failure-path tests.

**Test contract:**

```python
class PrSurfaceSubmitTest(unittest.TestCase):
    # helper: scratch repo + bare origin (git init --bare) + PATH prepended with fixtures/fake-gh + FAKE_GH_LOG
    def test_pr_submit_pushes_and_creates_pr(self): ...   # branch exists in bare origin; gh log has pr create; state pr == canned URL
    def test_manual_pr_flag_skips_gh(self): ...           # --pr given -> no gh invocation logged
    def test_local_surface_never_touches_gh(self): ...
    def test_pr_create_failure_still_records_submission(self): ...  # FAKE_GH_FAIL -> submit exit 0, task in review/pr-null, warning present
```

Steps: failing tests → implement → full suite → commit `feat(surface): pr-surface submit pushes branch and opens PR via gh`.

---

### Task 4: Review mirroring + merge on both surfaces + human mode

**Files:**
- Modify: `wave_delivery/cli.py` (`review record` mirroring; `merge` surface/mode handling), `wave_delivery/merge.py` (post-merge base push on pr surface; `--observed` verification path), `wave_delivery/github.py` (already has `comment_pr`)
- Test: `tests/test_execution_surfaces.py`

**Interfaces:**
- `review record` on effective surface `"pr"` when the task has a `pr`: after recording state, post one comment (markdown table of findings, or "clean review" line) via `comment_pr`; comment failure → `"warning"` in result, exit 0.
- `merge` with mode `"controller"`, surface `"pr"`: existing local merge runs, then `github.push_branch(repo, base_ref)` pushes the updated base (GitHub then shows the PR merged). Push failure after local merge: result carries `"warning"` (state already recorded; pushing again is idempotent).
- `merge` with mode `"human"`: refused with `IllegalTransition` naming the PR and the observation command — UNLESS `--observed` is passed.
- `wddctl merge --task X --repo . --observed`: does NOT merge. Runs `git fetch origin <base_ref>` (best-effort; tolerate no-network by falling back to the local ref), then requires `git merge-base --is-ancestor <task.headSha> <base_ref>` true; on success applies the same `task.merged` event/bookkeeping the normal merge records (reuse merge.py's event application, skipping the Git-mutation half); on failure `IllegalTransition` ("head not reachable from base; the human merge has not happened"). Works for BOTH modes (a controller-mode scope where a human merged out-of-band is also legal to observe).
- `next` decoration: for `merge_ready` tasks when mode is `"human"`, the action becomes `await_human_merge` with no `command`, a `judgment` naming the PR, and `recordWith` = the `merge --observed` command. Mode controller: `merge_task` as today. (Decoration layer only — `task_gate` is untouched; pass the effective mode into `decorate_actions` alongside Task 2's models.)

**Test contract:**

```python
class ReviewMirrorTest(unittest.TestCase):
    def test_findings_mirrored_as_pr_comment(self): ...
    def test_comment_failure_never_blocks_recording(self): ...
class HumanModeTest(unittest.TestCase):
    def test_merge_refused_in_human_mode(self): ...
    def test_observed_merge_requires_ancestry(self): ...   # not merged -> IllegalTransition; after real local merge into base -> records task.merged
    def test_next_shows_await_human_merge(self): ...
class PrControllerMergeTest(unittest.TestCase):
    def test_merge_pushes_base_after_local_merge(self): ...  # bare origin's base ref advances
```

Steps: failing tests → implement → full suite → commit `feat(surface): review mirroring, base push, and observed human merges`.

---

### Task 5: Monitor detects human merges

**Files:**
- Modify: `wave_delivery/monitor.py` (`_observations`: for `merge_ready` tasks whose headSha is an ancestor of the base ref, emit action `record_human_merge`)
- Modify: `wave_delivery/cli.py` if the monitor result needs the observation command attached (mirror `decorate_actions` style: include the literal `merge --observed` command)
- Test: `tests/test_execution_surfaces.py`

**Interfaces:**
- Applies in ANY mode (out-of-band merges are worth surfacing everywhere) but only for `merge_ready` tasks with a recorded `headSha` and a resolvable base ref. No fetch inside monitor (it is the cheap tick); ancestry is checked against the local base ref only.

**Test contract:**

```python
class MonitorHumanMergeTest(unittest.TestCase):
    def test_monitor_flags_merged_head(self): ...      # merge task branch into base manually -> monitor action record_human_merge
    def test_monitor_quiet_when_not_merged(self): ...
```

Steps: failing tests → implement → full suite → commit `feat(monitor): flag merge-ready tasks already reachable from base`.

---

### Task 6: Docs + skills for the surfaces

**Files:**
- Modify: `docs/wddctl.md` (merge surface/mode section: the matrix, submit/review/merge behavior per surface, `--observed`, `await_human_merge`, monitor's `record_human_merge`; transcripts real where capturable with the fake-gh fixture — a scratch run with PATH pointing at the fixture is legitimate and should be labeled as using a stub remote), `README.md` (one paragraph: the configuration matrix)
- Modify: `skills/wdd-run/SKILL.md` (await_human_merge handling; model field already added in Task 2 — verify coherence), `skills/wdd-review/SKILL.md` (one line: on the pr surface the controller mirrors findings to the PR; the reviewer's own contract is unchanged)
- Test: none (prose); full suite sanity run.

Steps: capture transcripts → edit → verify command spellings against the CLI → full suite → commit `docs: document merge surfaces, human mode, and model routing`.

---

## Self-review checklist (after Task 6)

- Spec §5: models mechanical (Task 2), mergeSurface pr default-capable with local fallback (Tasks 1/3/4), human-merge first-class with review-before-human and observation (Tasks 4/5). Configuration matrix achievable: local/controller (unchanged), pr/controller (Tasks 3/4), pr/human (Tasks 4/5).
- The spec's "PR marked ready for review at merge_ready" nuance: covered by `await_human_merge` surfacing; actual gh draft/ready toggling is NOT implemented (deferral — requires draft-PR creation first, disproportionate here). Record in ledger.
- Phase-1 spec item "stub-gh testing" (§8) delivered by the fake-gh fixture.
- Finalize phase untouched (phase 5).
