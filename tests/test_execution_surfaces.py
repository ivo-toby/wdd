from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from wave_delivery.cli import main
from wave_delivery.config import load_config, merge_settings
from wave_delivery.errors import ValidationError
from wave_delivery.plan import validate_plan
from wave_delivery.store import StateStore


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


def _plan(scope_overrides: dict | None = None) -> dict:
    plan = {
        "schemaVersion": 1,
        "kind": "wdd_plan",
        "scope": {
            "id": "SCOPE-x",
            "baseRef": None,
            "maxConcurrent": 3,
            "reviewPolicy": "risk_based",
            "reconcileEveryNMerges": 3,
        },
        "tasks": [
            {
                "id": "T1",
                "title": "T1",
                "specPath": "tasks/T1.md",
                "risk": "normal",
                "dependsOn": [],
                "conflictDomains": ["src/t1/**"],
            }
        ],
    }
    if scope_overrides:
        plan["scope"].update(scope_overrides)
    return plan


class MergeSettingsTest(unittest.TestCase):
    def _ready_repo(self, tmp: str):
        root = _git_repo(tmp)
        wdd = root / ".wdd"
        state = str(wdd / "state.json")
        assert _cli(state, "init", "--repo", str(root))[0] == 0
        assert _cli(state, "config", "set", "merge.surface", "pr")[0] == 0
        assert _cli(state, "config", "set", "merge.mode", "human")[0] == 0
        config = load_config(wdd)
        if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
            assert _cli(state, "config", "set", "verification.commands", '["true"]')[0] == 0
        assert _cli(state, "constitution", "ratify", "--by", "t")[0] == 0
        return root, wdd, state

    def test_defaults_from_config(self) -> None:
        # No scope override in the plan -> the effective settings come
        # straight from config.json, and the field stays absent from state.
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state_path = self._ready_repo(tmp)
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan()), encoding="utf-8")
            code, out = _cli(state_path, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
            self.assertEqual(code, 0, out)
            state = StateStore(wdd / "state.json").read()
            self.assertNotIn("mergeSurface", state["scope"])
            self.assertNotIn("mergeMode", state["scope"])
            config = load_config(wdd)
            self.assertEqual(merge_settings(state, config), {"surface": "pr", "mode": "human"})

    def test_scope_override_wins(self) -> None:
        # Plan sets mergeSurface "local" while config default is "pr" ->
        # effective surface is "local"; mode is untouched by the plan so it
        # still resolves from config ("human").
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state_path = self._ready_repo(tmp)
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan({"mergeSurface": "local"})), encoding="utf-8")
            code, out = _cli(state_path, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
            self.assertEqual(code, 0, out)
            state = StateStore(wdd / "state.json").read()
            self.assertEqual(state["scope"]["mergeSurface"], "local")
            self.assertNotIn("mergeMode", state["scope"])
            config = load_config(wdd)
            effective = merge_settings(state, config)
            self.assertEqual(effective, {"surface": "local", "mode": "human"})

    def test_legacy_no_config_defaults_local_controller(self) -> None:
        # config=None models a legacy repo that predates config.json; the
        # helper must fall back to local/controller regardless of scope.
        state = {
            "scope": {
                "id": "SCOPE-legacy",
                "baseRef": None,
                "maxConcurrent": None,
                "reviewPolicy": "risk_based",
            }
        }
        self.assertEqual(merge_settings(state, None), {"surface": "local", "mode": "controller"})

    def test_invalid_plan_value_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            validate_plan(_plan({"mergeSurface": "carrier-pigeon"}))
        with self.assertRaises(ValidationError):
            validate_plan(_plan({"mergeMode": "carrier-pigeon"}))


class BootstrapApplyMergeFieldsTest(unittest.TestCase):
    # Regression coverage: apply_plan's store-missing branch builds state via
    # state_from_plan() -> new_state(), neither of which knew about these
    # fields, so a plan setting them on the very first apply (no prior
    # init/state.json) silently lost them -- merge_settings() would then
    # resolve to config defaults against the operator's stated override.

    def test_first_apply_carries_scope_override_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            state_path = str(wdd / "state.json")
            plan_file = root / "plan.json"
            plan_file.write_text(
                json.dumps(_plan({"mergeSurface": "local", "mergeMode": "human"})),
                encoding="utf-8",
            )
            code, out = _cli(state_path, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
            self.assertEqual(code, 0, out)
            state = StateStore(wdd / "state.json").read()
            self.assertEqual(state["scope"]["mergeSurface"], "local")
            self.assertEqual(state["scope"]["mergeMode"], "human")

    def test_first_apply_omitting_fields_leaves_them_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            state_path = str(wdd / "state.json")
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan()), encoding="utf-8")
            code, out = _cli(state_path, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
            self.assertEqual(code, 0, out)
            state = StateStore(wdd / "state.json").read()
            self.assertNotIn("mergeSurface", state["scope"])
            self.assertNotIn("mergeMode", state["scope"])


if __name__ == "__main__":
    unittest.main()
