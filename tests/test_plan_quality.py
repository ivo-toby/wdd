from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from wave_delivery.config import default_config
from wave_delivery.lint import lint_plan
from wave_delivery.plan import apply_risk_rules


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


if __name__ == "__main__":
    unittest.main()
