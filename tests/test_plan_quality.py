from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from wave_delivery.cli import main
from wave_delivery.config import default_config, governance_fingerprint, load_config, save_config, set_value
from wave_delivery.engine import utc_now
from wave_delivery.lint import lint_plan
from wave_delivery.plan import MUTABLE_TASK_FIELDS, apply_risk_rules, brief_skeleton, plan_skeleton, validate_plan
from wave_delivery.schema import new_setup_state, new_state
from wave_delivery.setup import init_repository, migrate_governance, setup_next_actions
from wave_delivery.store import StateStore


def _plan(tasks: list[dict]) -> dict:
    return {
        "schemaVersion": 1,
        "kind": "wdd_plan",
        "scope": {
            "id": "SCOPE-demo",
            "baseRef": None,
            "maxConcurrent": 3,
            "reviewPolicy": "risk_based",
            "reconcileEveryNMerges": 3,
        },
        "tasks": tasks,
    }


def _task(task_id: str, *, risk: str = "normal", depends_on: list | None = None,
          domains: list | None = None) -> dict:
    return {
        "id": task_id,
        "title": task_id,
        "specPath": f"tasks/{task_id}.md",
        "risk": risk,
        "dependsOn": depends_on or [],
        "conflictDomains": domains if domains is not None else [f"src/{task_id}/**"],
    }


class RiskRulesTest(unittest.TestCase):
    def _config(self, rules: list[dict]) -> dict:
        config = default_config()
        config["riskRules"] = rules
        return config

    def test_matching_high_rule_raises_risk(self) -> None:
        plan = _plan([_task("T1", domains=["src/auth/token.py"])])
        out = apply_risk_rules(plan, self._config([{"pattern": "src/auth/**", "risk": "high"}]))
        self.assertEqual(out["tasks"][0]["risk"], "high")

    def test_non_matching_rule_leaves_risk(self) -> None:
        plan = _plan([_task("T1", domains=["docs/readme.md"])])
        out = apply_risk_rules(plan, self._config([{"pattern": "src/auth/**", "risk": "high"}]))
        self.assertEqual(out["tasks"][0]["risk"], "normal")

    def test_explicit_high_never_lowered(self) -> None:
        plan = _plan([_task("T1", risk="high", domains=["docs/readme.md"])])
        out = apply_risk_rules(plan, self._config([{"pattern": "docs/**", "risk": "normal"}]))
        self.assertEqual(out["tasks"][0]["risk"], "high")

    def test_input_plan_is_not_mutated(self) -> None:
        plan = _plan([_task("T1", domains=["src/auth/x.py"])])
        apply_risk_rules(plan, self._config([{"pattern": "src/auth/**", "risk": "high"}]))
        self.assertEqual(plan["tasks"][0]["risk"], "normal")

    def test_empty_domains_keep_plan_risk(self) -> None:
        plan = _plan([_task("T1", domains=[])])
        out = apply_risk_rules(plan, self._config([{"pattern": "**", "risk": "high"}]))
        self.assertEqual(out["tasks"][0]["risk"], "normal")

    def test_non_todo_task_keeps_stored_risk_on_new_matching_rule(self) -> None:
        # A riskRule ratified mid-scope must not re-derive risk for a task
        # that already left "todo" — that field becomes immutable the moment
        # a task starts, so re-deriving it would make every later re-apply of
        # the same plan file refuse forever (the exact bug this guards).
        plan = _plan([
            _task("T1", domains=["src/auth/token.py"]),
            _task("T2", domains=["src/auth/session.py"]),
        ])
        state = {
            "tasks": {
                "T1": {"status": "in_progress", "risk": "normal"},
                "T2": {"status": "todo", "risk": "normal"},
            }
        }
        out = apply_risk_rules(
            plan, self._config([{"pattern": "src/auth/**", "risk": "high"}]), state
        )
        by_id = {entry["id"]: entry for entry in out["tasks"]}
        self.assertEqual(by_id["T1"]["risk"], "normal")
        self.assertEqual(by_id["T2"]["risk"], "high")


def _codes(findings: list[dict]) -> set[str]:
    return {finding["code"] for finding in findings}


class LintSerializationTest(unittest.TestCase):
    def test_full_chain_warns(self) -> None:
        plan = _plan([
            _task("T1"),
            _task("T2", depends_on=["T1"]),
            _task("T3", depends_on=["T2"]),
        ])
        self.assertIn("serialized_plan", _codes(lint_plan(plan)))

    def test_parallel_plan_is_clean(self) -> None:
        plan = _plan([_task("T1"), _task("T2"), _task("T3")])
        self.assertNotIn("serialized_plan", _codes(lint_plan(plan)))

    def test_two_tasks_never_warn_serialization(self) -> None:
        plan = _plan([_task("T1"), _task("T2", depends_on=["T1"])])
        self.assertNotIn("serialized_plan", _codes(lint_plan(plan)))


class LintRiskDistributionTest(unittest.TestCase):
    def test_all_high_warns(self) -> None:
        plan = _plan([_task(f"T{n}", risk="high") for n in range(1, 5)])
        self.assertIn("uniform_risk", _codes(lint_plan(plan)))

    def test_all_normal_warns(self) -> None:
        plan = _plan([_task(f"T{n}") for n in range(1, 5)])
        self.assertIn("uniform_risk", _codes(lint_plan(plan)))

    def test_mixed_risk_is_clean(self) -> None:
        tasks = [_task(f"T{n}") for n in range(1, 5)]
        tasks[0]["risk"] = "high"
        self.assertNotIn("uniform_risk", _codes(lint_plan(_plan(tasks))))

    def test_three_tasks_never_warn_uniform(self) -> None:
        plan = _plan([_task(f"T{n}") for n in range(1, 4)])
        self.assertNotIn("uniform_risk", _codes(lint_plan(plan)))


class LintDomainGranularityTest(unittest.TestCase):
    def test_enumerated_file_list_warns_with_glob_suggestion(self) -> None:
        plan = _plan([
            _task("T1", domains=[
                "src/ah/constants.py", "src/ah/endpoints.py",
                "src/ah/errors.py", "src/ah/types.py",
            ]),
            _task("T2", domains=["docs/**"]),
        ])
        findings = [f for f in lint_plan(plan) if f["code"] == "enumerated_domains"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["task"], "T1")
        self.assertIn("src/ah/**", findings[0]["message"])

    def test_three_files_same_dir_is_clean(self) -> None:
        plan = _plan([
            _task("T1", domains=["src/a.py", "src/b.py", "src/c.py"]),
            _task("T2", domains=["docs/**"]),
        ])
        self.assertNotIn("enumerated_domains", _codes(lint_plan(plan)))

    def test_coarse_domain_overlapping_most_tasks_warns(self) -> None:
        plan = _plan([
            _task("T1", domains=["src/**"]),
            _task("T2", domains=["src/api/**"]),
            _task("T3", domains=["src/db/**"]),
            _task("T4", domains=["src/ui/**"]),
        ])
        findings = [f for f in lint_plan(plan) if f["code"] == "coarse_domain"]
        self.assertTrue(any("src/**" in f["message"] and f["task"] == "T1" for f in findings))

    def test_disjoint_domains_are_clean(self) -> None:
        plan = _plan([
            _task("T1", domains=["src/api/**"]),
            _task("T2", domains=["src/db/**"]),
            _task("T3", domains=["docs/**"]),
            _task("T4", domains=["tests/**"]),
        ])
        self.assertNotIn("coarse_domain", _codes(lint_plan(plan)))


def _git_repo(tmp: str) -> Path:
    root = Path(tmp) / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    (root / "seed").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "commit", "-qm", "seed"],
        cwd=root, check=True,
    )
    return root


def _cli(state: str, *argv: str) -> tuple[int, str]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = main(["--state", state, *argv])
    return code, stdout.getvalue()


def _mark_legacy(state: str) -> None:
    """Exempt a hand-built fixture from the phase-6a intake ladder.

    Most of this module's tests exercise plan/lint/governance mechanics, not
    intake -- per the plan's architecture note, these mark `intake.legacy`
    explicitly rather than fake-walk a ladder that isn't the point of the
    test (and legacy also means no approved-bytes composite is required).
    """
    store = StateStore(Path(state))
    current = store.read()
    current["intake"] = {"legacy": True}
    store.write(current)


def _walk_intake(state: str, wdd: Path, approver: str = "t") -> None:
    """Canonical ladder walk (plan Task 2/3): epic new -> spec -> research
    skip -> design. Task 4 (spec Sec1): the slug is born at the top of the
    ladder, so a real epic ("demo") must exist before any rung verb is legal
    on a non-legacy scope -- every rung artifact lives under `epics/demo/`."""
    assert _cli(state, "epic", "new", "--slug", "demo")[0] == 0
    epic_dir = wdd / "epics" / "demo"
    (epic_dir / "spec.md").write_text(
        "# Spec\n\n## Goal\n\nShip it.\n\n## In scope\n\n- x\n\n"
        "## Out of scope\n\n- y\n\n## Acceptance criteria\n\n"
        "- [ ] AC-1: the thing works\n", encoding="utf-8")
    assert _cli(state, "intake", "spec", "--approved-by", approver)[0] == 0
    assert _cli(state, "intake", "research", "--skip", "--by", approver,
                "--reason", "no external contracts")[0] == 0
    (epic_dir / "design.md").write_text(
        "# Design\n\n## Components\n\n- core\n\n## Interfaces\n\n"
        "- core: consumes nothing, produces lib\n\n"
        "## Integration surfaces\n\n- `src/core.py` — owned by: core task\n\n"
        "## Epic deliverable\n\nThe lib imports.\n", encoding="utf-8")
    assert _cli(state, "intake", "design", "--approved-by", approver,
                "--deliverable-command", "true")[0] == 0


def _write_briefs(wdd: Path, plan: dict) -> None:
    # PlanApprovalTest's only caller: writes into the active epic's
    # namespace (Task 4, spec Sec1), not flat.
    epic_dir = wdd / "epics" / "demo"
    (epic_dir / "tasks").mkdir(parents=True, exist_ok=True)
    for task in plan["tasks"]:
        path = epic_dir / task["specPath"]
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(f"# {task['id']}\n\nBrief.\n", encoding="utf-8")


class LintBriefTest(unittest.TestCase):
    def test_missing_and_empty_briefs_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            (wdd / "tasks").mkdir(parents=True)
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nreal brief content here\n", encoding="utf-8")
            (wdd / "tasks" / "T2.md").write_text("\n\n", encoding="utf-8")
            plan = _plan([
                _task("T1"), _task("T2"), _task("T3"),
            ])
            for entry in plan["tasks"]:
                entry["specPath"] = f"tasks/{entry['id']}.md"
            findings = [f for f in lint_plan(plan, wdd) if f["code"] == "missing_brief"]
            self.assertEqual({f["task"] for f in findings}, {"T2", "T3"})

    def test_json_blob_brief_warns_nonprose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            (wdd / "tasks").mkdir(parents=True)
            (wdd / "tasks" / "T1.md").write_text(
                '{\n  "objective": "scaffold",\n  "files": ["a.py"]\n}\n',
                encoding="utf-8",
            )
            plan = _plan([_task("T1")])
            findings = [f for f in lint_plan(plan, wdd) if f["code"] == "nonprose_brief"]
            self.assertEqual([f["task"] for f in findings], ["T1"])

    def test_prose_brief_is_clean_of_nonprose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            (wdd / "tasks").mkdir(parents=True)
            (wdd / "tasks" / "T1.md").write_text(
                "# T1\n\nBuild the thing properly.\n", encoding="utf-8"
            )
            plan = _plan([_task("T1")])
            self.assertNotIn("nonprose_brief", _codes(lint_plan(plan, wdd)))

    def test_missing_spec_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            (wdd / "tasks").mkdir(parents=True)
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nDo it.\n", encoding="utf-8")
            plan = _plan([_task("T1")])
            self.assertIn("missing_spec", _codes(lint_plan(plan, wdd)))

    def test_present_spec_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            (wdd / "tasks").mkdir(parents=True)
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nDo it.\n", encoding="utf-8")
            (wdd / "spec.md").write_text(
                "# Spec\n\n## Goal\n\nShip the thing.\n", encoding="utf-8"
            )
            plan = _plan([_task("T1")])
            self.assertNotIn("missing_spec", _codes(lint_plan(plan, wdd)))

    def test_no_wdd_dir_skips_brief_check(self) -> None:
        plan = _plan([_task("T1")])
        self.assertNotIn("missing_brief", _codes(lint_plan(plan)))

    def test_blank_only_brief_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            (wdd / "tasks").mkdir(parents=True)
            (wdd / "tasks" / "T1.md").write_text("\n\n\n", encoding="utf-8")
            plan = _plan([_task("T1")])
            plan["tasks"][0]["specPath"] = "tasks/T1.md"
            findings = [f for f in lint_plan(plan, wdd) if f["code"] == "missing_brief"]
            self.assertEqual({f["task"] for f in findings}, {"T1"})


class LintCliTest(unittest.TestCase):
    def test_plan_lint_reports_findings_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            plan = _plan([
                _task("T1"), _task("T2", depends_on=["T1"]), _task("T3", depends_on=["T2"]),
            ])
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, out = _cli(str(root / ".wdd" / "state.json"), "plan", "lint", "--plan", str(plan_file))
            self.assertEqual(code, 0)
            self.assertIn("serialized_plan", out)

    def test_plan_lint_strict_fails_on_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            plan = _plan([
                _task("T1"), _task("T2", depends_on=["T1"]), _task("T3", depends_on=["T2"]),
            ])
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code, _ = _cli(str(root / ".wdd" / "state.json"), "plan", "lint", "--plan", str(plan_file), "--strict")
            self.assertNotEqual(code, 0)

    def test_apply_result_carries_lint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            state = str(root / ".wdd" / "state.json")
            assert _cli(state, "init", "--repo", str(root))[0] == 0
            _mark_legacy(state)
            plan = _plan([
                _task("T1"), _task("T2", depends_on=["T1"]), _task("T3", depends_on=["T2"]),
            ])
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root), "--dry-run",
            )
            self.assertEqual(code, 0, out)
            self.assertIn("serialized_plan", out)


def _cli_full(state: str, *argv: str) -> tuple[int, str, str]:
    """Like _cli, but also captures stderr for asserting on refusal messages."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(["--state", state, *argv])
    return code, stdout.getvalue(), stderr.getvalue()


class BaseEqualsTargetBranchTest(unittest.TestCase):
    """Finding 1's fix: scope.baseRef == branching.targetBranch makes the
    epic branch the branch it delivers into, so finalize's human-merge
    ancestry checks are vacuously satisfied (a ref is its own ancestor) --
    the delivery ladder could self-certify without any human merge ever
    happening. 'plan apply' refuses this outright (config-aware path only;
    legacy scopes without config.json are unaffected); 'plan lint' surfaces
    it as a warning finding (code base_is_target) so it is visible even
    without --strict."""

    def _ready_repo(self, tmp: str):
        root = _git_repo(tmp)
        wdd = root / ".wdd"
        state = str(wdd / "state.json")
        assert _cli(state, "init", "--repo", str(root))[0] == 0
        assert _cli(state, "config", "set", "merge.surface", "local")[0] == 0
        assert _cli(state, "config", "set", "models", '{"planning": null, "implementation": {"default": null, "highRisk": null}, "review": null}')[0] == 0
        config = load_config(wdd)
        if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
            assert _cli(state, "config", "set", "verification.commands", '["true"]')[0] == 0
        assert _cli(state, "constitution", "ratify", "--by", "t")[0] == 0
        _mark_legacy(state)
        return root, wdd, state

    def test_plan_apply_refuses_when_base_ref_equals_target_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state = self._ready_repo(tmp)
            target_branch = load_config(wdd)["branching"]["targetBranch"]
            plan = _plan([_task("T1")])
            plan["scope"]["baseRef"] = target_branch
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")

            code, out, err = _cli_full(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root)
            )
            self.assertNotEqual(code, 0, out)
            self.assertIn("scope.baseRef must differ from branching.targetBranch", err)
            self.assertIn(target_branch, err)
            self.assertIsNone(StateStore(wdd / "state.json").read()["scope"])

    def test_plan_apply_unaffected_when_base_ref_differs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state = self._ready_repo(tmp)
            plan = _plan([_task("T1")])
            plan["scope"]["baseRef"] = "wdd/scope-q"
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")

            code, out = _cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
            self.assertEqual(code, 0, out)

    def test_plan_apply_skips_check_without_config(self) -> None:
        # No-config (legacy, pre-config.json) path: nothing to compare
        # baseRef against, so the check is a no-op rather than a crash.
        # Task 3 removed apply_plan's no-state bootstrap, so state.json must
        # already exist; a hand-written legacy state without ever creating
        # config.json reproduces the "no config.json" scenario this test
        # defends, without also walking (or being exempt from) the intake
        # ladder via a real 'wddctl init'.
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            state = str(wdd / "state.json")
            legacy_state = new_setup_state()
            legacy_state["intake"] = {"legacy": True}
            legacy_state["constitution"] = {
                "status": "ratified",
                "ratification": {"by": "t", "decisionFingerprint": "sha256:x"},
            }
            StateStore(Path(state)).write(legacy_state)
            plan = _plan([_task("T1")])
            plan["scope"]["baseRef"] = "main"
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")

            code, out = _cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
            self.assertEqual(code, 0, out)

    def test_plan_lint_surfaces_base_is_target_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state = self._ready_repo(tmp)
            target_branch = load_config(wdd)["branching"]["targetBranch"]
            plan = _plan([_task("T1")])
            plan["scope"]["baseRef"] = target_branch
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")

            code, out = _cli(state, "plan", "lint", "--plan", str(plan_file))
            self.assertEqual(code, 0, out)
            findings = json.loads(out)["findings"]
            codes = {f["code"] for f in findings}
            self.assertIn("base_is_target", codes)
            finding = next(f for f in findings if f["code"] == "base_is_target")
            self.assertEqual(finding["severity"], "warning")

    def test_plan_lint_strict_fails_on_base_is_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state = self._ready_repo(tmp)
            target_branch = load_config(wdd)["branching"]["targetBranch"]
            plan = _plan([_task("T1")])
            plan["scope"]["baseRef"] = target_branch
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")

            code, out, err = _cli_full(state, "plan", "lint", "--plan", str(plan_file), "--strict")
            self.assertNotEqual(code, 0, out)
            self.assertIn("base_is_target", err)


class EventApplyGovernanceTest(unittest.TestCase):
    def test_event_apply_refuses_on_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            init_repository(wdd, root)
            config = load_config(wdd)
            config = set_value(config, "merge.surface", "local")
            if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
                config = set_value(config, "verification.commands", ["true"])
            save_config(wdd, config)
            store = StateStore(wdd / "state.json")
            state = store.read()
            state["constitution"] = {
                "status": "ratified",
                "ratification": {"by": "t", "decisionFingerprint": governance_fingerprint(wdd)},
            }
            store.write(state)
            save_config(wdd, set_value(load_config(wdd), "concurrency.maxConcurrent", 9))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code, _ = _cli(
                    str(wdd / "state.json"),
                    "event", "apply", "--event", "note.added", "--data", '{"note": "x"}',
                )
            self.assertNotEqual(code, 0)
            self.assertIn("drift", stderr.getvalue())


class MigrateGovernanceLockTest(unittest.TestCase):
    def test_invalidation_bumps_revision_and_records_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            wdd.mkdir()
            (wdd / "constitution.md").write_text("# Old\n", encoding="utf-8")
            state = new_state("SCOPE-legacy", base_ref="wdd/legacy")
            state["constitution"] = {
                "status": "ratified",
                "ratification": {"by": "t", "decisionFingerprint": "sha256:old"},
            }
            StateStore(wdd / "state.json").write(state)
            before = StateStore(wdd / "state.json").read()["revision"]
            result = migrate_governance(wdd)
            self.assertTrue(result["ratificationInvalidated"])
            after = StateStore(wdd / "state.json").read()
            self.assertEqual(after["revision"], before + 1)
            self.assertEqual(after["events"][-1]["type"], "governance.migrated")


class RepairConfigHintTest(unittest.TestCase):
    def test_missing_config_yields_repair_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            init_repository(wdd, root)
            (wdd / "config.json").unlink()
            state = StateStore(wdd / "state.json").read()
            result = setup_next_actions(state, wdd)
            self.assertEqual(result["actions"][0]["action"], "repair_config")


class ConfigOverlayEndToEndTest(unittest.TestCase):
    def test_omitted_scope_fields_default_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            state = str(wdd / "state.json")
            self.assertEqual(_cli(state, "init", "--repo", str(root))[0], 0)
            self.assertEqual(_cli(state, "config", "set", "merge.surface", "local")[0], 0)
            self.assertEqual(_cli(state, "config", "set", "models", '{"planning": null, "implementation": {"default": null, "highRisk": null}, "review": null}')[0], 0)
            config = load_config(wdd)
            if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
                self.assertEqual(
                    _cli(state, "config", "set", "verification.commands", '["true"]')[0], 0
                )
            self.assertEqual(_cli(state, "config", "set", "merge.reconcileEveryNMerges", "5")[0], 0)
            self.assertEqual(_cli(state, "config", "set", "concurrency.maxConcurrent", "2")[0], 0)
            self.assertEqual(_cli(state, "constitution", "ratify", "--by", "t")[0], 0)
            _mark_legacy(state)
            plan = _plan([_task("T1")])
            del plan["scope"]["reconcileEveryNMerges"]
            del plan["scope"]["maxConcurrent"]
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            self.assertEqual(
                _cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))[0], 0
            )
            adopted = StateStore(wdd / "state.json").read()
            self.assertEqual(adopted["reconcile"]["everyNMerges"], 5)
            self.assertEqual(adopted["scope"]["maxConcurrent"], 2)


class MidScopeRiskRuleReapplyTest(unittest.TestCase):
    def test_started_task_survives_new_matching_risk_rule_on_reapply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            state = str(wdd / "state.json")
            self.assertEqual(_cli(state, "init", "--repo", str(root))[0], 0)
            self.assertEqual(_cli(state, "config", "set", "merge.surface", "local")[0], 0)
            self.assertEqual(_cli(state, "config", "set", "models", '{"planning": null, "implementation": {"default": null, "highRisk": null}, "review": null}')[0], 0)
            config = load_config(wdd)
            if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
                self.assertEqual(
                    _cli(state, "config", "set", "verification.commands", '["true"]')[0], 0
                )
            self.assertEqual(_cli(state, "constitution", "ratify", "--by", "t")[0], 0)
            _mark_legacy(state)
            # start (phase-6b Task 2) materializes T1's brief into an attempt
            # snapshot, so it must exist on disk.
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")

            plan = _plan([_task("T1", domains=["src/auth/**"])])
            plan["scope"]["baseRef"] = "wdd/scope-q"
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, _ = _cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
            self.assertEqual(code, 0)

            code, _ = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0)
            self.assertEqual(StateStore(wdd / "state.json").read()["tasks"]["T1"]["status"], "in_progress")

            code, _ = _cli(
                state, "config", "set", "riskRules",
                '[{"pattern": "src/auth/**", "risk": "high"}]',
            )
            self.assertEqual(code, 0)
            code, _ = _cli(state, "constitution", "amend", "--by", "t")
            self.assertEqual(code, 0)

            # Re-applying the identical plan file must not refuse: T1 is
            # in_progress and the new rule now matches its domain, but its
            # stored risk ("normal") must be left alone.
            code, out = _cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
            self.assertEqual(code, 0, out)
            after = StateStore(wdd / "state.json").read()
            self.assertEqual(after["tasks"]["T1"]["risk"], "normal")

            # Adding a brand-new task to the same plan file must re-apply
            # cleanly, with the new task's risk derived (high) since it was
            # never in the "todo → started" path T1 went through.
            plan["tasks"].append(_task("T2", domains=["src/auth/session.py"]))
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, out = _cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
            self.assertEqual(code, 0, out)
            after = StateStore(wdd / "state.json").read()
            self.assertEqual(after["tasks"]["T1"]["risk"], "normal")
            self.assertEqual(after["tasks"]["T2"]["risk"], "high")


class PlanApprovalTest(unittest.TestCase):
    """These genuinely exercise the plan-approval composite (Task 3), so
    unlike this module's other fixtures they walk the real intake ladder
    (not `_mark_legacy`) and write real brief files -- both are now part of
    what a recorded approval binds to."""

    def _ready_repo(self, tmp: str):
        root = _git_repo(tmp)
        wdd = root / ".wdd"
        state = str(wdd / "state.json")
        assert _cli(state, "init", "--repo", str(root))[0] == 0
        assert _cli(state, "config", "set", "merge.surface", "local")[0] == 0
        assert _cli(state, "config", "set", "models", '{"planning": null, "implementation": {"default": null, "highRisk": null}, "review": null}')[0] == 0
        config = load_config(wdd)
        if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
            assert _cli(state, "config", "set", "verification.commands", '["true"]')[0] == 0
        assert _cli(state, "constitution", "ratify", "--by", "t")[0] == 0
        _walk_intake(state, wdd)
        return root, wdd, state

    def test_approved_by_is_stamped_into_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state = self._ready_repo(tmp)
            plan = _plan([_task("T1")])
            _write_briefs(wdd, plan)
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file),
                "--repo", str(root), "--approved-by", "ivo",
            )
            self.assertEqual(code, 0, out)
            self.assertIn("ivo", out)
            approval = StateStore(wdd / "state.json").read()["scope"]["approval"]
            self.assertEqual(approval["by"], "ivo")
            self.assertTrue(approval["at"])
            self.assertTrue(approval["sha256"].startswith("sha256:"))

    def test_reapply_a_changed_plan_without_approved_by_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state = self._ready_repo(tmp)
            plan = _plan([_task("T1")])
            _write_briefs(wdd, plan)
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            assert _cli(state, "plan", "apply", "--plan", str(plan_file),
                        "--repo", str(root), "--approved-by", "ivo")[0] == 0
            before = StateStore(wdd / "state.json").read()["scope"]["approval"]

            # Task 3: a nonempty diff (T2 added) without --approved-by now
            # refuses outright -- silently carrying the old approval forward
            # over changed scope, as the pre-ladder code did, is exactly
            # what the composite doctrine forbids.
            plan = _plan([_task("T1"), _task("T2")])
            _write_briefs(wdd, plan)
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, _out = _cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
            self.assertNotEqual(code, 0)
            after = StateStore(wdd / "state.json").read()["scope"]["approval"]
            self.assertEqual(before, after)

    def test_reapply_identical_plan_with_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state = self._ready_repo(tmp)
            plan = _plan([_task("T1")])
            _write_briefs(wdd, plan)
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, _ = _cli(state, "plan", "apply", "--plan", str(plan_file),
                           "--repo", str(root), "--approved-by", "ivo")
            self.assertEqual(code, 0)
            # Re-apply identical plan with --approved-by
            code, out = _cli(state, "plan", "apply", "--plan", str(plan_file),
                             "--repo", str(root), "--approved-by", "ivo")
            self.assertEqual(code, 0)
            self.assertIn("ivo", out)
            self.assertIn("unchanged", out)
            approval = StateStore(wdd / "state.json").read()["scope"]["approval"]
            self.assertEqual(approval["by"], "ivo")

    def test_empty_approved_by_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state = self._ready_repo(tmp)
            plan = _plan([_task("T1")])
            _write_briefs(wdd, plan)
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file),
                "--repo", str(root), "--approved-by", "   ",
            )
            self.assertNotEqual(code, 0)
            # Rejected before any mutation: the plan was never applied, so
            # scope stays unset just as it was right after init.
            after = StateStore(wdd / "state.json").read()
            self.assertIsNone(after["scope"])

    def test_changed_plan_reapply_with_approved_by_stamps_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state = self._ready_repo(tmp)
            plan = _plan([_task("T1")])
            _write_briefs(wdd, plan)
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, _ = _cli(state, "plan", "apply", "--plan", str(plan_file),
                           "--repo", str(root), "--approved-by", "ivo")
            self.assertEqual(code, 0)
            # Re-apply a CHANGED plan (new task added) with --approved-by: goes
            # through the mutator path, not the unchanged/dry-run shortcuts.
            plan = _plan([_task("T1"), _task("T2")])
            _write_briefs(wdd, plan)
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, out = _cli(state, "plan", "apply", "--plan", str(plan_file),
                             "--repo", str(root), "--approved-by", "ivo")
            self.assertEqual(code, 0, out)
            self.assertIn("ivo", out)
            approval = StateStore(wdd / "state.json").read()["scope"]["approval"]
            self.assertEqual(approval["by"], "ivo")
            self.assertTrue(approval["at"])

    def test_reapproval_with_different_name_overwrites_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state = self._ready_repo(tmp)
            plan = _plan([_task("T1")])
            _write_briefs(wdd, plan)
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, _ = _cli(
                state, "plan", "apply", "--plan", str(plan_file),
                "--repo", str(root), "--approved-by", "ivo",
            )
            self.assertEqual(code, 0)
            first = StateStore(wdd / "state.json").read()["scope"]["approval"]
            self.assertEqual(first["by"], "ivo")

            # Re-approve the identical plan under a different name: by/at
            # must be overwritten, not left as the first approver's.
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file),
                "--repo", str(root), "--approved-by", "someone-else",
            )
            self.assertEqual(code, 0, out)
            second = StateStore(wdd / "state.json").read()["scope"]["approval"]
            self.assertEqual(second["by"], "someone-else")

    def test_update_path_dry_run_echoes_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state = self._ready_repo(tmp)
            plan = _plan([_task("T1")])
            _write_briefs(wdd, plan)
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            # First apply, approved (Task 3: a nonempty diff requires it now)
            code, _ = _cli(state, "plan", "apply", "--plan", str(plan_file),
                           "--repo", str(root), "--approved-by", "ivo")
            self.assertEqual(code, 0)
            before = StateStore(wdd / "state.json").read()["scope"]["approval"]
            # Dry-run with --approved-by, against a CHANGED plan (T2 added)
            plan = _plan([_task("T1"), _task("T2")])
            _write_briefs(wdd, plan)
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, out = _cli(state, "plan", "apply", "--plan", str(plan_file),
                             "--repo", str(root), "--dry-run", "--approved-by", "ivo")
            self.assertEqual(code, 0, out)
            self.assertIn("ivo", out)
            # A dry run never writes: the recorded approval (and its
            # composite, still pinned to the one-task plan) is untouched.
            after = StateStore(wdd / "state.json").read()["scope"]["approval"]
            self.assertEqual(before, after)


def _state_with_spec_recorded() -> dict:
    """Cheapest legal state carrying a recorded intake.spec (Task 6 fixture).

    Schema v5 requires `intake` (schema.py's `_validate_intake`); a single
    hand-written `spec` record is enough to make `_intake_recorded` true --
    the walk-the-whole-ladder helper (`_walk_intake`) is unnecessary when the
    test is only about lint reading state, not about the ladder itself.
    """
    state = new_setup_state()
    state["intake"]["spec"] = {
        "by": "t",
        "at": utc_now(),
        "criteria": 1,
        "sha256": "sha256:" + "a" * 64,
    }
    return state


class LintDeliverableInterfacesTest(unittest.TestCase):
    """Task 6 / spec Sec3: the brief template's two required, linted sections."""

    def test_brief_missing_both_sections_warns_both_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            (wdd / "tasks").mkdir(parents=True)
            (wdd / "tasks" / "T1.md").write_text(
                "# T1\n\nJust an objective, no required sections.\n", encoding="utf-8"
            )
            plan = _plan([_task("T1")])
            findings = lint_plan(plan, wdd)
            codes = {(f["code"], f.get("task")) for f in findings}
            self.assertIn(("missing_deliverable", "T1"), codes)
            self.assertIn(("missing_interfaces", "T1"), codes)

    def test_brief_with_empty_deliverable_section_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            (wdd / "tasks").mkdir(parents=True)
            (wdd / "tasks" / "T1.md").write_text(
                "# T1\n\n## Deliverable\n\n## Interfaces\n\nConsumes nothing, produces lib.\n",
                encoding="utf-8",
            )
            plan = _plan([_task("T1")])
            findings = lint_plan(plan, wdd)
            self.assertIn("missing_deliverable", _codes(findings))
            self.assertNotIn("missing_interfaces", _codes(findings))

    def test_brief_with_both_sections_populated_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            (wdd / "tasks").mkdir(parents=True)
            (wdd / "tasks" / "T1.md").write_text(
                "# T1\n\n## Deliverable\n\nThe lib imports and exports `foo()`.\n\n"
                "## Interfaces\n\nConsumes nothing, produces lib.\n",
                encoding="utf-8",
            )
            plan = _plan([_task("T1")])
            codes = _codes(lint_plan(plan, wdd))
            self.assertNotIn("missing_deliverable", codes)
            self.assertNotIn("missing_interfaces", codes)

    def test_missing_brief_file_does_not_also_report_section_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            (wdd / "tasks").mkdir(parents=True)
            plan = _plan([_task("T1")])
            findings = lint_plan(plan, wdd)
            self.assertIn("missing_brief", _codes(findings))
            self.assertNotIn("missing_deliverable", _codes(findings))
            self.assertNotIn("missing_interfaces", _codes(findings))


class LintMissingCriteriaTest(unittest.TestCase):
    """Task 6 / spec Sec3: `spec.md#AC-<n>` context refs are the authoritative
    task -> acceptance-criteria mapping; advisory when a task discharges none."""

    def test_task_with_no_context_warns(self) -> None:
        plan = _plan([_task("T1")])
        self.assertIn("missing_criteria", _codes(lint_plan(plan)))

    def test_task_with_full_ac_ref_is_clean(self) -> None:
        plan = _plan([_task("T1")])
        plan["tasks"][0]["context"] = ["spec.md#AC-3"]
        self.assertNotIn("missing_criteria", _codes(lint_plan(plan)))

    def test_prefix_junk_ac_ref_still_warns(self) -> None:
        # spec.md#AC-garbage is not a full match of `spec.md#AC-<digits>` --
        # prefix junk does not count as discharging a criterion.
        plan = _plan([_task("T1")])
        plan["tasks"][0]["context"] = ["spec.md#AC-garbage"]
        self.assertIn("missing_criteria", _codes(lint_plan(plan)))

    def test_non_ac_context_ref_still_warns(self) -> None:
        plan = _plan([_task("T1")])
        plan["tasks"][0]["context"] = ["shared-context/contract-inventory.md#orders"]
        self.assertIn("missing_criteria", _codes(lint_plan(plan)))


class LintMissingContextTest(unittest.TestCase):
    """Task 6 / spec Sec3: `missing_context` only fires once intake artifacts
    are recorded in state -- a scope that hasn't run the ladder yet has no
    handover artifacts to reference, so silence there is correct, not a gap."""

    def test_no_state_is_quiet_even_without_context_refs(self) -> None:
        plan = _plan([_task("T1")])
        self.assertNotIn("missing_context", _codes(lint_plan(plan, state=None)))

    def test_legacy_state_is_quiet(self) -> None:
        state = new_setup_state()
        state["intake"] = {"legacy": True}
        plan = _plan([_task("T1")])
        self.assertNotIn("missing_context", _codes(lint_plan(plan, state=state)))

    def test_recorded_spec_and_task_without_context_warns(self) -> None:
        state = _state_with_spec_recorded()
        plan = _plan([_task("T1")])
        findings = [f for f in lint_plan(plan, state=state) if f["code"] == "missing_context"]
        self.assertEqual([f["task"] for f in findings], ["T1"])

    def test_recorded_spec_and_task_with_context_is_clean(self) -> None:
        state = _state_with_spec_recorded()
        plan = _plan([_task("T1")])
        plan["tasks"][0]["context"] = ["spec.md#AC-1"]
        self.assertNotIn("missing_context", _codes(lint_plan(plan, state=state)))

    def test_recorded_intake_via_cli_plan_lint_surfaces_missing_context(self) -> None:
        # End-to-end plumbing check: `_overlaid_plan` threads state.json's
        # intake records into `lint_plan` for the real `plan lint` CLI path,
        # not just direct calls into the lint module. No `init`/config.json
        # here on purpose (mirrors this file's no-config lint fixtures) --
        # the point is state.json alone, hand-written, reaching lint_plan.
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            state_path = wdd / "state.json"
            StateStore(state_path).write(_state_with_spec_recorded())
            plan = _plan([_task("T1")])
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")

            code, out = _cli(str(state_path), "plan", "lint", "--plan", str(plan_file))
            self.assertEqual(code, 0, out)
            self.assertIn("missing_context", out)


class LintUnownedSurfaceTest(unittest.TestCase):
    """Task 6 / spec Sec2: design.md's Integration surfaces vs. conflictDomains."""

    def test_no_design_file_is_quiet_and_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir()
            plan = _plan([_task("T1")])
            self.assertNotIn("unowned_surface", _codes(lint_plan(plan, wdd)))

    def test_design_file_without_surfaces_section_is_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir()
            (wdd / "design.md").write_text(
                "# Design\n\n## Components\n\n- core\n\n## Interfaces\n\n"
                "- core: consumes nothing, produces lib\n\n## Epic deliverable\n\nIt runs.\n",
                encoding="utf-8",
            )
            plan = _plan([_task("T1")])
            self.assertNotIn("unowned_surface", _codes(lint_plan(plan, wdd)))

    def test_uncovered_surface_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir()
            (wdd / "design.md").write_text(
                "# Design\n\n## Integration surfaces\n\n"
                "- `src/shared/registry.py` — owned by: registry\n",
                encoding="utf-8",
            )
            plan = _plan([_task("T1", domains=["src/unrelated/**"])])
            findings = [f for f in lint_plan(plan, wdd) if f["code"] == "unowned_surface"]
            self.assertEqual(len(findings), 1)
            self.assertIn("src/shared/registry.py", findings[0]["message"])

    def test_covered_surface_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir()
            (wdd / "design.md").write_text(
                "# Design\n\n## Integration surfaces\n\n"
                "- `src/shared/registry.py` — owned by: registry\n",
                encoding="utf-8",
            )
            plan = _plan([_task("T1", domains=["src/shared/**"])])
            self.assertNotIn("unowned_surface", _codes(lint_plan(plan, wdd)))


class PlanTemplateTest(unittest.TestCase):
    """`wddctl plan template`: deterministic skeleton emitter (Task: no state
    read/write, no governance gating -- must work mid-setup, exactly like
    `--help`)."""

    def test_preview_without_plan_before_apply_refuses_cleanly(self) -> None:
        # Regression: state["scope"] is None pre-apply; preview crashed with
        # a NoneType traceback instead of a refusal (observed live).
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            state = str(Path(tmp) / ".wdd" / "state.json")
            code, _ = _cli(state, "init", "--repo", tmp)
            self.assertEqual(code, 0)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code, _ = _cli(state, "plan", "preview")
            self.assertEqual(code, 2)
            self.assertIn("no scope has been applied", stderr.getvalue())

    def test_plan_skeleton_parses_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = str(Path(tmp) / "scratch" / ".wdd" / "state.json")
            code, out = _cli(state, "plan", "template")
            self.assertEqual(code, 0)
            parsed = json.loads(out)
            validate_plan(parsed)  # raises ValidationError on failure

    def test_plan_skeleton_contains_all_mutable_task_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = str(Path(tmp) / "scratch" / ".wdd" / "state.json")
            code, out = _cli(state, "plan", "template")
            self.assertEqual(code, 0)
            for field in MUTABLE_TASK_FIELDS:
                self.assertIn(f'"{field}"', out)

    def test_brief_skeleton_has_required_lint_headings_and_reads_as_prose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = str(Path(tmp) / "scratch" / ".wdd" / "state.json")
            code, out = _cli(state, "plan", "template", "--brief")
            self.assertEqual(code, 0)
            self.assertIn("## Deliverable", out)
            self.assertIn("## Interfaces", out)
            # nonprose_brief (lint.py) trips when the stripped text starts
            # with '{' or '[' -- assert the inverse holds for the skeleton.
            self.assertNotIn(out.lstrip()[:1], "{[")

    def test_filled_in_skeleton_pair_passes_deliverable_and_interfaces_lint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            (wdd / "tasks").mkdir(parents=True)
            plan = plan_skeleton()
            # plan_skeleton's placeholder task specPath is where the emitted
            # brief skeleton is expected to land once filled in.
            spec_path = plan["tasks"][0]["specPath"]
            (wdd / spec_path).parent.mkdir(parents=True, exist_ok=True)
            (wdd / spec_path).write_text(brief_skeleton(), encoding="utf-8")
            findings = lint_plan(plan, wdd)
            codes = _codes(findings)
            self.assertNotIn("missing_brief", codes)
            self.assertNotIn("nonprose_brief", codes)
            self.assertNotIn("missing_deliverable", codes)
            self.assertNotIn("missing_interfaces", codes)

    def test_works_with_no_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = str(Path(tmp) / "no-such-dir" / ".wdd" / "state.json")
            code, _ = _cli(state, "plan", "template")
            self.assertEqual(code, 0)
            code, _ = _cli(state, "plan", "template", "--brief")
            self.assertEqual(code, 0)

    def test_deterministic_across_invocations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = str(Path(tmp) / ".wdd" / "state.json")
            _, out1 = _cli(state, "plan", "template")
            _, out2 = _cli(state, "plan", "template")
            self.assertEqual(out1, out2)
            _, brief1 = _cli(state, "plan", "template", "--brief")
            _, brief2 = _cli(state, "plan", "template", "--brief")
            self.assertEqual(brief1, brief2)


if __name__ == "__main__":
    unittest.main()
