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
from wave_delivery.lint import lint_plan
from wave_delivery.plan import apply_risk_rules
from wave_delivery.schema import new_state
from wave_delivery.setup import init_repository, migrate_governance, setup_next_actions
from wave_delivery.store import StateStore


def _plan(tasks: list[dict]) -> dict:
    return {
        "schemaVersion": 1,
        "kind": "wdd_plan",
        "scope": {
            "id": "SCOPE-q",
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
            plan = _plan([
                _task("T1"), _task("T2", depends_on=["T1"]), _task("T3", depends_on=["T2"]),
            ])
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, out = _cli(
                str(root / ".wdd" / "state.json"),
                "plan", "apply", "--plan", str(plan_file), "--repo", str(root), "--dry-run",
            )
            self.assertEqual(code, 0)
            self.assertIn("serialized_plan", out)


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
            config = load_config(wdd)
            if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
                self.assertEqual(
                    _cli(state, "config", "set", "verification.commands", '["true"]')[0], 0
                )
            self.assertEqual(_cli(state, "config", "set", "merge.reconcileEveryNMerges", "5")[0], 0)
            self.assertEqual(_cli(state, "config", "set", "concurrency.maxConcurrent", "2")[0], 0)
            self.assertEqual(_cli(state, "constitution", "ratify", "--by", "t")[0], 0)
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


if __name__ == "__main__":
    unittest.main()
