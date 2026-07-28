from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from wave_delivery.cli import main
from wave_delivery.config import default_config, load_config, merge_settings
from wave_delivery.engine import bounded_next_actions
from wave_delivery.errors import ValidationError
from wave_delivery.plan import validate_plan
from wave_delivery.schema import new_state, task_state
from wave_delivery.store import StateStore


FAKE_GH_DIR = str(Path(__file__).parent / "fixtures" / "fake-gh")


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


class ModelRoutingTest(unittest.TestCase):
    def test_start_task_carries_risk_tiered_model(self) -> None:
        state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
        state["tasks"]["HIGH"] = task_state(
            "HIGH", risk="high", conflict_domains=["src/high/**"]
        )
        state["tasks"]["NORM"] = task_state(
            "NORM", risk="normal", conflict_domains=["src/norm/**"]
        )
        models = {
            "planning": None,
            "implementation": {"default": "sonnet", "highRisk": "opus"},
            "review": None,
        }
        result = bounded_next_actions(state, models=models)
        by_task = {action["task"]: action for action in result["actions"]}
        self.assertEqual(by_task["HIGH"]["action"], "start_task")
        self.assertEqual(by_task["HIGH"]["model"], "opus")
        self.assertEqual(by_task["NORM"]["action"], "start_task")
        self.assertEqual(by_task["NORM"]["model"], "sonnet")

    def test_run_review_carries_review_model(self) -> None:
        state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
        task = task_state("T1", risk="normal", conflict_domains=["src/t1/**"])
        task["status"] = "review"
        task["pr"] = "https://example.invalid/pr/1"
        task["headSha"] = "abc123"
        state["tasks"]["T1"] = task
        models = {
            "planning": None,
            "implementation": {"default": None, "highRisk": None},
            "review": "haiku",
        }
        result = bounded_next_actions(state, models=models)
        by_task = {action["task"]: action for action in result["actions"]}
        self.assertEqual(by_task["T1"]["action"], "run_review")
        self.assertEqual(by_task["T1"]["model"], "haiku")

    def test_no_models_config_adds_no_key(self) -> None:
        state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
        state["tasks"]["HIGH"] = task_state(
            "HIGH", risk="high", conflict_domains=["src/high/**"]
        )
        reviewing = task_state("T1", risk="normal", conflict_domains=["src/t1/**"])
        reviewing["status"] = "review"
        reviewing["pr"] = "https://example.invalid/pr/1"
        reviewing["headSha"] = "abc123"
        state["tasks"]["T1"] = reviewing
        # models=None (no config.json) and an all-null models dict (config.json
        # present but nothing configured yet) must both add no "model" key.
        for models in (None, default_config()["models"]):
            result = bounded_next_actions(state, models=models)
            for action in result["actions"]:
                self.assertNotIn(
                    "model", action, f"unexpected model key with models={models!r}"
                )

    def test_cli_next_includes_model_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            state_path = str(wdd / "state.json")
            self.assertEqual(_cli(state_path, "init", "--repo", str(root))[0], 0)
            self.assertEqual(
                _cli(state_path, "config", "set", "merge.surface", "local")[0], 0
            )
            self.assertEqual(
                _cli(state_path, "config", "set", "models.implementation.default", '"sonnet"')[0],
                0,
            )
            self.assertEqual(
                _cli(state_path, "config", "set", "models.implementation.highRisk", '"opus"')[0],
                0,
            )
            config = load_config(wdd)
            if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
                self.assertEqual(
                    _cli(state_path, "config", "set", "verification.commands", '["true"]')[0], 0
                )
            self.assertEqual(_cli(state_path, "constitution", "ratify", "--by", "t")[0], 0)
            plan = _plan()
            plan["tasks"][0]["risk"] = "high"
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, out = _cli(
                state_path, "plan", "apply", "--plan", str(plan_file), "--repo", str(root)
            )
            self.assertEqual(code, 0, out)
            code, out = _cli(state_path, "next")
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            start_actions = [a for a in result["actions"] if a["action"] == "start_task"]
            self.assertEqual(len(start_actions), 1, out)
            self.assertEqual(start_actions[0]["model"], "opus")


class PrSurfaceSubmitTest(unittest.TestCase):
    """CLI `submit` on the pr surface: push + gh, with a fake gh on PATH.

    Pushes target a local bare repo added as `origin`; no live network. The
    fake gh fixture logs every invocation to $FAKE_GH_LOG and prints a
    canned URL for `pr create`, matching real gh's contract of writing the
    new PR's URL to stdout.
    """

    def _repo_with_origin(self, tmp: str) -> tuple[Path, Path]:
        root = _git_repo(tmp)
        bare = Path(tmp) / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=root, check=True)
        return root, bare

    def _ready_task(self, tmp: str, *, surface: str) -> tuple[Path, str, Path]:
        """Drive the real CLI flow until T1 has a commit ready to submit."""
        root, bare = self._repo_with_origin(tmp)
        wdd = root / ".wdd"
        state = str(wdd / "state.json")
        self.assertEqual(_cli(state, "init", "--repo", str(root))[0], 0)
        self.assertEqual(_cli(state, "config", "set", "merge.surface", surface)[0], 0)
        config = load_config(wdd)
        if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
            self.assertEqual(
                _cli(state, "config", "set", "verification.commands", '["true"]')[0], 0
            )
        self.assertEqual(_cli(state, "constitution", "ratify", "--by", "t")[0], 0)
        plan_file = root / "plan.json"
        plan_file.write_text(json.dumps(_plan({"baseRef": "main"})), encoding="utf-8")
        code, out = _cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
        self.assertEqual(code, 0, out)
        code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
        self.assertEqual(code, 0, out)
        worktree = Path(json.loads(out)["worktree"])
        (worktree / "change.txt").write_text("work\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=worktree, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "-c", "commit.gpgsign=false", "commit", "-qm", "do work"],
            cwd=worktree, check=True,
        )
        return root, state, bare

    @contextlib.contextmanager
    def _fake_gh(self, log_path: str, *, fail: bool = False):
        saved = {key: os.environ.get(key) for key in ("PATH", "FAKE_GH_LOG", "FAKE_GH_FAIL")}
        os.environ["PATH"] = FAKE_GH_DIR + os.pathsep + saved["PATH"]
        os.environ["FAKE_GH_LOG"] = log_path
        if fail:
            os.environ["FAKE_GH_FAIL"] = "1"
        else:
            os.environ.pop("FAKE_GH_FAIL", None)
        try:
            yield
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    @staticmethod
    def _gh_log(log_path: str) -> list[list[str]]:
        path = Path(log_path)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    @staticmethod
    def _branches(bare: Path) -> str:
        # --git-dir (not cwd) avoids "safe.bareRepository is 'explicit'"
        # refusals some Git configs apply to commands run inside a bare repo.
        return subprocess.run(
            ["git", "--git-dir", str(bare), "branch", "--list"], check=True, text=True,
            stdout=subprocess.PIPE,
        ).stdout

    def test_pr_submit_pushes_and_creates_pr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = self._ready_task(tmp, surface="pr")
            log_path = str(Path(tmp) / "gh.log")
            with self._fake_gh(log_path):
                code, out = _cli(state, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["pr"], "https://github.invalid/pr/1")
            self.assertNotIn("warning", result)
            log = self._gh_log(log_path)
            self.assertTrue(any(entry[:2] == ["pr", "create"] for entry in log), log)
            self.assertIn("task/T1", self._branches(bare))

    def test_manual_pr_flag_skips_gh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = self._ready_task(tmp, surface="pr")
            log_path = str(Path(tmp) / "gh.log")
            with self._fake_gh(log_path):
                code, out = _cli(
                    state, "submit", "--task", "T1", "--repo", str(root),
                    "--pr", "https://example.invalid/manual/9",
                )
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["pr"], "https://example.invalid/manual/9")
            self.assertEqual(self._gh_log(log_path), [])
            # A manual --pr never pushes either: only gh is bypassed by
            # design, but this proves the branch was not force-published.
            self.assertNotIn("task/T1", self._branches(bare))

    def test_local_surface_never_touches_gh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = self._ready_task(tmp, surface="local")
            log_path = str(Path(tmp) / "gh.log")
            with self._fake_gh(log_path):
                code, out = _cli(state, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertTrue(result["pr"].startswith("branch:"), result)
            self.assertEqual(self._gh_log(log_path), [])
            self.assertNotIn("task/T1", self._branches(bare))

    def test_pr_create_failure_still_records_submission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = self._ready_task(tmp, surface="pr")
            log_path = str(Path(tmp) / "gh.log")
            with self._fake_gh(log_path, fail=True):
                code, out = _cli(state, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertIn("warning", result)
            # The push ran (and succeeded) before gh failed, so the state
            # transition is never lost: submit_task falls back to the same
            # branch reference a local-surface submit would record.
            self.assertTrue(result["pr"].startswith("branch:"), result)
            self.assertIn("task/T1", self._branches(bare))


if __name__ == "__main__":
    unittest.main()
