# Intake Flow & Skills Rewrite Implementation Plan (Phase 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give WDD its front door — a spec-intake flow ending in a **recorded** plan approval (`wddctl plan apply --approved-by`) and a `.wdd/spec.md` the finalize phase (phase 5) will review against — and finish the agent-first skills/docs rewrite: every skill opens with the hard rule that the agent runs `wddctl` itself, `wdd-worker` gets TDD as a hard rule, and `docs/workflow.md` is rewritten agent-first.

**Architecture:** One small code change (approval recording threaded through `apply_plan`'s mutation, both creation and update paths). Everything else is prose with a verification obligation: every command named in a skill or doc must exist, spelled exactly, in the current CLI (checked against `build_parser`), and workflow.md transcripts are real captured output per repo convention.

**Tech Stack:** Python 3 stdlib only; `unittest`.

## Global Constraints

- No new runtime dependencies. Errors via `wave_delivery/errors.py`.
- Spec: `docs/superpowers/specs/2026-07-28-onboarding-and-workflow-redesign-design.md` §3 (intake) and §7 (skills/docs), plus the TDD bullet of §5 (prose half only — model dispatch and merge surfaces are phase 4).
- **The hard rule, verbatim, for every skill** (already present in `wdd-setup`; the exact sentence to reuse): "You run every `wddctl` command in this skill yourself. Presenting a command to the user instead of executing it is a protocol violation." Skills touching merge add the exception clause: "Exceptions: the human-owned final merge, and anything `merge.mode: human` reserves for people."
- Prose accuracy: every named command verified against `wave_delivery/cli.py`'s `build_parser` (or `--help`). Transcripts in docs are REAL output ("nothing here is paraphrased").
- New tests append to `tests/test_plan_quality.py` (helpers `_git_repo`, `_cli`, `_plan`, `_task` exist there).
- Conventional commits; never skip hooks; do not push.
- Full suite green after every task: `python3 -m unittest discover -s tests -q` (gpg workaround if needed: `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false`).

---

### Task 1: Recorded plan approval — `plan apply --approved-by`

Spec §3 step 5: "Approval is recorded: `wddctl plan apply --approved-by NAME` stamps the approval into state."

**Files:**
- Modify: `wave_delivery/plan.py` (`apply_plan`, signature at line ~336; both the creation path and the `apply_mutation` mutator path)
- Modify: `wave_delivery/cli.py` (plan-apply parser + handler)
- Modify: `wave_delivery/schema.py` (`validate_state`: scope may carry an `approval` object)
- Test: `tests/test_plan_quality.py`
- Docs: `docs/wddctl.md` (plan apply section: the flag and its meaning; 3-line addition, real output not required for a flag description but include the JSON key it produces)

**Interfaces:**
- `apply_plan(store, plan, *, repo=None, from_ref=None, dry_run=False, expected_revision=None, idempotency_key=None, approved_by: str | None = None)`. When `approved_by` is a non-empty string, the written state's `scope["approval"]` becomes `{"by": approved_by, "at": <utc_now()>}` — on the creation path, the adoption path, and plain re-apply alike. When `approved_by` is None, any existing `scope["approval"]` is preserved untouched (re-apply without the flag must not erase a recorded approval). The apply result JSON echoes `"approvedBy"` when given.
- `validate_state`: `scope.approval` optional; when present must be an object with non-empty-string `by` and `at`.
- CLI: `--approved-by NAME` optional on `plan apply` (not on `plan lint`/`preview`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_plan_quality.py`:

```python
class PlanApprovalTest(unittest.TestCase):
    def _ready_repo(self, tmp: str):
        root = _git_repo(tmp)
        wdd = root / ".wdd"
        state = str(wdd / "state.json")
        assert _cli(state, "init", "--repo", str(root))[0] == 0
        assert _cli(state, "config", "set", "merge.surface", "local")[0] == 0
        config = load_config(wdd)
        if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
            assert _cli(state, "config", "set", "verification.commands", '["true"]')[0] == 0
        assert _cli(state, "constitution", "ratify", "--by", "t")[0] == 0
        return root, wdd, state

    def test_approved_by_is_stamped_into_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state = self._ready_repo(tmp)
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan([_task("T1")])), encoding="utf-8")
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file),
                "--repo", str(root), "--approved-by", "ivo",
            )
            self.assertEqual(code, 0)
            self.assertIn("ivo", out)
            approval = StateStore(wdd / "state.json").read()["scope"]["approval"]
            self.assertEqual(approval["by"], "ivo")
            self.assertTrue(approval["at"])

    def test_reapply_without_flag_preserves_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state = self._ready_repo(tmp)
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan([_task("T1")])), encoding="utf-8")
            _cli(state, "plan", "apply", "--plan", str(plan_file),
                 "--repo", str(root), "--approved-by", "ivo")
            plan = _plan([_task("T1"), _task("T2")])
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, _ = _cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
            self.assertEqual(code, 0)
            approval = StateStore(wdd / "state.json").read()["scope"]["approval"]
            self.assertEqual(approval["by"], "ivo")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_plan_quality.PlanApprovalTest -v`
Expected: FAIL — argparse rejects `--approved-by` (exit 2, `_cli` assertion fires)

- [ ] **Step 3: Implement**

1. `plan.py`: add `approved_by: str | None = None` to `apply_plan`'s keyword-only params. Add a small helper applied to a state dict just before it is written on the creation path, and inside the mutator right after `_apply_plan_to_state` on the update path:

```python
def _stamp_approval(state: dict[str, Any], approved_by: str | None) -> dict[str, Any]:
    if approved_by:
        state["scope"]["approval"] = {"by": approved_by, "at": utc_now()}
    return state
```

(`utc_now` lives in `wave_delivery/engine.py` — import it.) Echo `"approvedBy": approved_by` into both result dicts when set.
2. `schema.py` `validate_state`, inside the non-null scope branch:

```python
        approval = scope.get("approval")
        if approval is not None:
            approval = _require_mapping(approval, "scope.approval")
            _require_string(approval.get("by"), "scope.approval.by")
            _require_string(approval.get("at"), "scope.approval.at")
```

3. `cli.py`: `plan_apply.add_argument("--approved-by", dest="approved_by", default=None)` and pass `approved_by=args.approved_by` through the `apply_plan` call.
4. `docs/wddctl.md`: 3-line addition under plan apply documenting the flag, the `scope.approval` state key, and that re-apply without the flag preserves the last recorded approval.

- [ ] **Step 4: Run tests, full suite, commit**

Run: `python3 -m unittest tests.test_plan_quality -v` — PASS.
Run: `python3 -m unittest discover -s tests -q` — PASS.

```bash
git add wave_delivery/plan.py wave_delivery/cli.py wave_delivery/schema.py tests/test_plan_quality.py docs/wddctl.md
git commit -m "feat(plan): record plan approval via --approved-by"
```

---

### Task 2: `wdd-plan` becomes the intake flow

Spec §3. Prose task with a verification obligation.

**Files:**
- Modify: `skills/wdd-plan/SKILL.md` (rewrite; current content covers decomposition mechanics — those sections survive, reorganized under the new flow)

**Required content (checklist for the implementer — draft the prose yourself, in the skill pack's existing voice; terse, imperative, no tutorial filler):**

1. Frontmatter `description` updated: intake flow from spec documents to an applied, approved scope (ingest → clarify → agree → decompose → approve), still named `wdd-plan`.
2. Opens with the hard rule (verbatim sentence from Global Constraints).
3. **Ingest**: read the documents/context the user brings. If no spec exists, say so and stop — WDD assumes specced work; writing the spec is the engineer's job.
4. **Challenge and clarify**: push back on gaps, contradictions, scope ambiguity; ask the user in compact rounds (batch questions, never one-per-message trickle).
5. **Agree**: write the agreed understanding to `.wdd/spec.md` with exactly these four sections: Goal, In scope, Out of scope, Acceptance criteria. State that the finalize phase reviews the epic branch against this file, so acceptance criteria must be checkable. Include a ~10-line skeleton example of the file.
6. **Decompose**: keep the existing guidance sections (task splitting, conflict domains, dependencies, maxConcurrent) — edited for flow, not rewritten. The risk section stays as rewritten in phase 2 (derived from riskRules).
7. **Present for approval**: show the user the plan summary AND `wddctl plan preview` output (noting preview reads the plan file without config overlays — the caveat documented in wddctl.md), plus any `wddctl plan lint` findings and their justifications. On explicit user approval: `wddctl plan apply --plan plan.json --repo . --approved-by <name>`. Never apply an unapproved plan; the name recorded is the human who approved, not the agent.
8. "Done when" block: scope applied, approval recorded (`scope.approval` in state), `wddctl next` shows `start_task` actions.
9. Every named command exists, spelled exactly as the CLI accepts (verify against `python3 scripts/wddctl.py plan --help` etc. — including `--approved-by` from Task 1).

- [ ] **Step 1: Rewrite the skill** per the checklist.
- [ ] **Step 2: Verify every command** named in the file against the CLI's `--help` output; note the verification in the commit body if any spelling needed adjusting.
- [ ] **Step 3: Full suite (prose-only; sanity), commit**

```bash
git add skills/wdd-plan/SKILL.md
git commit -m "feat(skills): rewrite wdd-plan as the spec-intake flow"
```

---

### Task 3: Hard-rule pass + TDD in `wdd-worker`

Spec §7 (hard rule everywhere) + §5's TDD bullet.

**Files:**
- Modify: `skills/wdd-run/SKILL.md`, `skills/wdd-review/SKILL.md`, `skills/wdd-status/SKILL.md`, `skills/wave-driven-development/SKILL.md` (hard-rule opening; run/router also get the merge-exception clause)
- Modify: `skills/wdd-worker/SKILL.md` (TDD section, and note the worker exception)

**Required changes:**

1. `wdd-run`, `wdd-review`, `wdd-status`: insert the verbatim hard-rule sentence as the first body paragraph (after the H1). `wdd-run` appends the exception clause (it owns merges). Do not otherwise rewrite these skills — phase 4 touches run/review behavior; keep this diff minimal.
2. `wave-driven-development` (router): add one line to the overview stating agents execute `wddctl` themselves; presenting commands to the user is a protocol violation.
3. `wdd-worker`: the worker never runs `wddctl` (existing rule — keep it; the hard rule does NOT get pasted here since it would contradict that). Instead add a **TDD** section after "Stay in scope":
   - Red first: before implementation, write the failing test(s) the brief's objective implies, run them, confirm they fail for the expected reason.
   - Green: implement the minimal change; run the tests; confirm pass.
   - State the honest exception: tasks with no meaningful red/green (docs, pure config) — say so in the final report instead of faking a cycle.
   - Evidence: the final report includes the RED command+failure and GREEN command+pass output; reviewers treat missing TDD evidence as a finding.
4. No command spellings change in this task; still re-verify any command the touched files name (they may predate phase 1/2 CLI changes — fix stale spellings found, e.g. old `constitution` flags).

- [ ] **Step 1: Make the edits** per the checklist.
- [ ] **Step 2: Grep-verify**: `grep -rn "wddctl" skills/` — every named verb/flag exists in current `build_parser`.
- [ ] **Step 3: Full suite (sanity), commit**

```bash
git add skills/
git commit -m "feat(skills): agent-first hard rule everywhere; TDD contract in wdd-worker"
```

---

### Task 4: `docs/workflow.md` rewritten agent-first

Spec §7: "workflow.md is rewritten agent-first; the human-at-the-terminal path becomes an appendix." Also closes the phase-1 deferral (its ratification walkthrough still teaches the pre-split probe/proposal flow).

**Files:**
- Modify: `docs/workflow.md` (484 lines today; substantial rewrite)

**Required content (structure contract — prose is the implementer's, transcripts are real):**

1. Keep the title and the "enforced vs. convention" framing — it is the document's core idea and still true.
2. **Reframe the two audiences**: the agent-driven path is primary and comes first; "someone driving wddctl directly" moves to a closing appendix ("Appendix: driving wddctl yourself") that keeps its still-accurate content.
3. The day-in-the-life narrative updates to the current lifecycle: `wddctl init` → one round of config questions (`resolve_config`) → ratify (fingerprint over config.json + constitution.md) → intake per `wdd-plan` (spec.md, lint, `--approved-by`) → the `next` loop with the three roles (controller/worker/reviewer — this section survives mostly as-is) → drift/amend governance.
4. Every transcript regenerated for real against the current CLI in a scratch repo (`python3 scripts/wddctl.py`; seed commits with `git -c user.email=t@t -c user.name=t -c commit.gpgsign=false commit`). Delete transcripts of flows that no longer exist (constitution `--proposal` ratification path may be shown once as the deprecated fallback or dropped — implementer's call, but no stale primary flow may remain).
5. The "skills do not call wddctl" passage: keep the mechanism explanation (skills are text; the agent's own shell runs commands) but align it with the hard rule — the skill text now *obligates* the agent to run commands itself; what remains advisory is whether the agent honors it, which is exactly why the state machine enforces the parts that matter.
6. Length discipline: the rewrite should land at roughly the current length or shorter. No new conceptual sections beyond what the lifecycle requires.
7. Cross-references stay valid: any section other docs link to by anchor (`grep -rn "workflow.md#" docs/ skills/ README.md`) keeps its heading or the referrer is updated.

- [ ] **Step 1: Inventory** current sections + anchors; capture fresh transcripts for each flow step in a scratch repo.
- [ ] **Step 2: Rewrite** per the structure contract.
- [ ] **Step 3: Verify**: every command spelling against the CLI; every transcript real; anchor check from item 7.
- [ ] **Step 4: Full suite (sanity), commit**

```bash
git add docs/workflow.md
git commit -m "docs: rewrite workflow.md agent-first with current lifecycle transcripts"
```

---

## Self-review checklist (after Task 4)

- Spec §3: intake steps 1–5 → Task 2; recorded approval → Task 1.
- Spec §7: hard rule in every skill → Tasks 2–3 (wdd-setup already had it); worker TDD → Task 3; workflow.md agent-first → Task 4.
- Phase-1 deferral "workflow.md stale ratification walkthrough" → closed by Task 4. Constitution-template second-person wording: **explicitly still deferred** (it lives in `wave_delivery/setup.py`; revisit if it bothers anyone in practice).
- Phases 4–5 untouched: no model dispatch, no PR surface, no finalize actions in this plan.
