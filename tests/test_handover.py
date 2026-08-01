"""Phase-6b Task 1 tests: routing decoration -- task overrides + tiered review models.

Local helpers copied from tests/test_execution_surfaces.py / tests/test_intake.py
patterns (no cross-file imports between test modules, per the phase-6a/6b test
conventions -- see Global Constraints in the handover-and-runners plan). Later
6b tasks (snapshots, input binding, runners, e2e) add classes to this same
file.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from wave_delivery.config import default_config, load_config, validate_config
from wave_delivery.engine import bounded_next_actions
from wave_delivery.errors import ValidationError
from wave_delivery.schema import new_state, task_state
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
        from wave_delivery.cli import main

        code = main(["--state", state, *argv])
    return code, stdout.getvalue()


def _plan(scope_overrides: dict | None = None, task_overrides: dict | None = None) -> dict:
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
    if task_overrides:
        plan["tasks"][0].update(task_overrides)
    return plan


def _ratified(state: dict) -> dict:
    state["constitution"] = {
        "status": "ratified",
        "ratification": {
            "by": "tester",
            "decisionFingerprint": "sha256:x",
            "at": "2026-01-01T00:00:00Z",
        },
    }
    return state


def _mark_legacy(state: str) -> None:
    """Exempt a hand-built fixture from the phase-6a intake ladder.

    Copied from tests/test_execution_surfaces.py: this file exercises
    routing decoration, not intake, so it marks intake.legacy explicitly
    rather than fake-walk a ladder that isn't the point of the test.
    """
    store = StateStore(Path(state))
    current = store.read()
    current["intake"] = {"legacy": True}
    store.write(current)


def _review_ready_task(task_id: str = "T1", *, risk: str = "normal", model: str | None = None,
                        review_model: str | None = None) -> dict:
    task = task_state(
        task_id, risk=risk, conflict_domains=[f"src/{task_id.lower()}/**"],
        model=model, review_model=review_model,
    )
    task["status"] = "review"
    task["pr"] = "https://example.invalid/pr/1"
    task["headSha"] = "abc123"
    return task


class RoutingDecorationTest(unittest.TestCase):
    """Spec Sec3/Sec4: task model/reviewModel overrides + tiered models.review."""

    def test_plain_string_review_model_still_routes(self) -> None:
        """Regression: config.models.review as a plain string means both tiers."""
        state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
        state["tasks"]["T1"] = _review_ready_task("T1", risk="normal")
        models = {
            "planning": None,
            "implementation": {"default": None, "highRisk": None},
            "review": "haiku",
        }
        result = bounded_next_actions(state, models=models)
        by_task = {action["task"]: action for action in result["actions"]}
        self.assertEqual(by_task["T1"]["action"], "run_review")
        self.assertEqual(by_task["T1"]["model"], "haiku")

    def test_object_form_review_tiers_by_task_risk(self) -> None:
        state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
        high = task_state("HIGH", risk="high", conflict_domains=["src/high/**"])
        high["status"] = "review"
        high["pr"] = "https://example.invalid/pr/1"
        high["headSha"] = "abc123"
        norm = task_state("NORM", risk="normal", conflict_domains=["src/norm/**"])
        norm["status"] = "review"
        norm["pr"] = "https://example.invalid/pr/2"
        norm["headSha"] = "def456"
        state["tasks"]["HIGH"] = high
        state["tasks"]["NORM"] = norm
        models = {
            "planning": None,
            "implementation": {"default": None, "highRisk": None},
            "review": {"default": "haiku", "highRisk": "opus"},
        }
        result = bounded_next_actions(state, models=models)
        by_task = {action["task"]: action for action in result["actions"]}
        self.assertEqual(by_task["HIGH"]["model"], "opus")
        self.assertEqual(by_task["NORM"]["model"], "haiku")

    def test_task_model_override_wins_over_risk_tiered_implementation(self) -> None:
        state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
        state["tasks"]["T1"] = task_state(
            "T1", risk="high", conflict_domains=["src/t1/**"], model="task-override-model"
        )
        models = {
            "planning": None,
            "implementation": {"default": "sonnet", "highRisk": "opus"},
            "review": None,
        }
        result = bounded_next_actions(state, models=models)
        by_task = {action["task"]: action for action in result["actions"]}
        self.assertEqual(by_task["T1"]["action"], "start_task")
        self.assertEqual(by_task["T1"]["model"], "task-override-model")

    def test_task_review_model_override_wins_over_tiered_review(self) -> None:
        state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
        state["tasks"]["T1"] = _review_ready_task(
            "T1", risk="high", review_model="task-override-reviewer"
        )
        models = {
            "planning": None,
            "implementation": {"default": None, "highRisk": None},
            "review": {"default": "haiku", "highRisk": "opus"},
        }
        result = bounded_next_actions(state, models=models)
        by_task = {action["task"]: action for action in result["actions"]}
        self.assertEqual(by_task["T1"]["model"], "task-override-reviewer")

    def test_task_overrides_win_even_without_models_config(self) -> None:
        """Overrides are per-task fields, not config-dependent -- apply with models=None."""
        state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
        state["tasks"]["T1"] = task_state(
            "T1", risk="normal", conflict_domains=["src/t1/**"], model="solo-model"
        )
        result = bounded_next_actions(state, models=None)
        by_task = {action["task"]: action for action in result["actions"]}
        self.assertEqual(by_task["T1"]["model"], "solo-model")

    def test_all_null_adds_no_model_key(self) -> None:
        state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
        state["tasks"]["T1"] = task_state("T1", risk="normal", conflict_domains=["src/t1/**"])
        state["tasks"]["T2"] = _review_ready_task("T2", risk="normal")
        for models in (None, default_config()["models"]):
            result = bounded_next_actions(state, models=models)
            for action in result["actions"]:
                self.assertNotIn(
                    "model", action, f"unexpected model key with models={models!r}"
                )

    def test_config_validation_accepts_review_string_object_and_null(self) -> None:
        config = default_config()
        for value in ("haiku", {"default": "haiku", "highRisk": "opus"},
                      {"default": None, "highRisk": None}, None):
            config["models"]["review"] = value
            validate_config(config)  # must not raise

    def test_config_validation_rejects_bad_review_shapes(self) -> None:
        config = default_config()
        for bad in (123, [], "", {"default": 123}, {"default": ""}, {"highRisk": 5}):
            config["models"]["review"] = bad
            with self.assertRaises(ValidationError):
                validate_config(config)

    def test_cli_e2e_task_reviewmodel_reaches_run_review_payload(self) -> None:
        """Proves the state-side plumbing: a task carrying reviewModel in the
        plan is persisted and surfaces on its run_review action after
        start+submit, even with a tiered models.review configured."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            state_path = str(wdd / "state.json")
            self.assertEqual(_cli(state_path, "init", "--repo", str(root))[0], 0)
            self.assertEqual(
                _cli(state_path, "config", "set", "merge.surface", "local")[0], 0
            )
            self.assertEqual(
                _cli(
                    state_path, "config", "set", "models",
                    '{"planning": null, "implementation": {"default": null, "highRisk": null}, '
                    '"review": null}',
                )[0],
                0,
            )
            self.assertEqual(
                _cli(
                    state_path, "config", "set", "models.review",
                    '{"default": "haiku", "highRisk": "opus"}',
                )[0],
                0,
            )
            config = load_config(wdd)
            if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
                self.assertEqual(
                    _cli(state_path, "config", "set", "verification.commands", '["true"]')[0], 0
                )
            self.assertEqual(_cli(state_path, "constitution", "ratify", "--by", "t")[0], 0)
            _mark_legacy(state_path)
            plan = _plan(
                {"baseRef": "wdd/scope-x"},
                {"risk": "high", "reviewModel": "task-pinned-reviewer"},
            )
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, out = _cli(
                state_path, "plan", "apply", "--plan", str(plan_file), "--repo", str(root)
            )
            self.assertEqual(code, 0, out)

            code, out = _cli(state_path, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            worktree = Path(json.loads(out)["worktree"])
            (worktree / "change.txt").write_text("work\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=worktree, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "do work"],
                cwd=worktree, check=True,
            )
            code, out = _cli(state_path, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)

            code, out = _cli(state_path, "next")
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            review_actions = [a for a in result["actions"] if a["action"] == "run_review"]
            self.assertEqual(len(review_actions), 1, out)
            self.assertEqual(review_actions[0]["model"], "task-pinned-reviewer")


if __name__ == "__main__":
    unittest.main()
