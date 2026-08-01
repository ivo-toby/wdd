"""Phase-6b tests: routing decoration (Task 1) + attempt snapshots/input digests (Task 2).

Local helpers copied from tests/test_execution_surfaces.py / tests/test_intake.py
patterns (no cross-file imports between test modules, per the phase-6a/6b test
conventions -- see Global Constraints in the handover-and-runners plan). Later
6b tasks (input binding, runners, e2e) add classes to this same file.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from wave_delivery.config import default_config, load_config, validate_config
from wave_delivery.engine import bounded_next_actions
from wave_delivery.errors import ValidationError
from wave_delivery.handover import (
    ensure_dispatch_gitignore,
    materialize_attempt,
    record_attempt,
)
from wave_delivery.intake import artifact_sha256
from wave_delivery.schema import new_state, task_state
from wave_delivery.setup import init_repository, migrate_governance
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
            # start (Task 2) materializes T1's brief into an attempt snapshot,
            # so it must exist on disk even though legacy plan apply itself
            # does not require it.
            (wdd / "tasks").mkdir(exist_ok=True)
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
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


# --- Task 2: attempt snapshots + input digests -----------------------------


def _ratified_repo(tmp: str) -> tuple[Path, str]:
    """A fresh git repo with .wdd/ initialized and the constitution ratified.

    Copied from tests/test_intake.py's helper of the same name (no cross-file
    imports between test modules): the intake ladder runs from here.
    """
    root = _git_repo(tmp)
    wdd = root / ".wdd"
    state = str(wdd / "state.json")
    assert _cli(state, "init", "--repo", str(root))[0] == 0
    assert _cli(state, "config", "set", "merge.surface", "local")[0] == 0
    assert (
        _cli(
            state, "config", "set", "models",
            '{"planning": null, "implementation": {"default": null, "highRisk": null}, '
            '"review": null}',
        )[0]
        == 0
    )
    config = load_config(wdd)
    if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
        assert _cli(state, "config", "set", "verification.commands", '["true"]')[0] == 0
    assert _cli(state, "constitution", "ratify", "--by", "t")[0] == 0
    return root, state


def _spec_text() -> str:
    return (
        "# Spec\n\n## Goal\n\nShip it.\n\n## In scope\n\n- x\n\n"
        "## Out of scope\n\n- y\n\n## Acceptance criteria\n\n- [ ] AC-1: the thing works\n"
    )


def _design_text() -> str:
    return (
        "# Design\n\n## Components\n\n- core\n\n## Interfaces\n\n"
        "- core: consumes nothing, produces lib\n\n## Integration surfaces\n\n"
        "- `src/core.py` -- owned by: core task\n\n## Epic deliverable\n\nThe lib imports.\n"
    )


def _walk_intake(state: str, wdd: Path, approver: str = "t") -> None:
    """Canonical ladder walk: spec -> research skip -> design. Copied from
    tests/test_intake.py's helper of the same name."""
    (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
    assert _cli(state, "intake", "spec", "--approved-by", approver)[0] == 0
    assert (
        _cli(
            state, "intake", "research", "--skip", "--by", approver,
            "--reason", "no external contracts",
        )[0]
        == 0
    )
    (wdd / "design.md").write_text(_design_text(), encoding="utf-8")
    assert (
        _cli(
            state, "intake", "design", "--approved-by", approver,
            "--deliverable-command", "true",
        )[0]
        == 0
    )


def _apply_v5_scope(
    root: Path,
    wdd: Path,
    state: str,
    *,
    task_id: str = "T1",
    context: list[str] | None = None,
) -> None:
    """Walk the intake ladder and composite-approve a one-task plan for
    task_id, with a real brief on disk (and optional context refs)."""
    _walk_intake(state, wdd)
    (wdd / "tasks").mkdir(exist_ok=True)
    (wdd / "tasks" / f"{task_id}.md").write_text(
        f"# {task_id}\n\nBrief body for {task_id}.\n", encoding="utf-8"
    )
    plan = {
        "schemaVersion": 1,
        "kind": "wdd_plan",
        "scope": {"id": "SCOPE-x", "baseRef": "wdd/scope-x"},
        "tasks": [
            {
                "id": task_id,
                "specPath": f"tasks/{task_id}.md",
                "risk": "normal",
                "context": context or [],
            }
        ],
    }
    plan_file = root / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    code, out = _cli(
        state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
        "--approved-by", "t",
    )
    assert code == 0, out


class DispatchGitignoreTest(unittest.TestCase):
    """Global Constraints: .wdd/dispatch/ is transient scratch, gitignored by
    both init and migrate --governance."""

    def test_init_writes_dispatch_gitignore_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            init_repository(wdd, root)
            gitignore = (wdd / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("dispatch/", gitignore.splitlines())

    def test_migrate_governance_writes_dispatch_gitignore_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            wdd.mkdir()
            state = new_state("SCOPE-legacy", base_ref="wdd/legacy")
            StateStore(wdd / "state.json").write(state)
            result = migrate_governance(wdd)
            self.assertTrue(result["migrated"])
            gitignore = (wdd / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("dispatch/", gitignore.splitlines())

    def test_ensure_dispatch_gitignore_is_idempotent_and_preserves_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir()
            (wdd / ".gitignore").write_text("state.json\n", encoding="utf-8")
            self.assertTrue(ensure_dispatch_gitignore(wdd))
            self.assertFalse(ensure_dispatch_gitignore(wdd))
            lines = (wdd / ".gitignore").read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines, ["state.json", "dispatch/"])


class AttemptSnapshotTest(unittest.TestCase):
    """Spec Sec3 ('Handover itself is immutable'): start materializes a
    read-only attempt snapshot and records source-file digests on the task."""

    def test_start_materializes_brief_and_context_with_permissions_and_digests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "shared-context").mkdir(exist_ok=True)
            (wdd / "shared-context" / "contract.md").write_text(
                "contract body\n", encoding="utf-8"
            )
            _apply_v5_scope(
                root, wdd, state, context=["shared-context/contract.md#orders"]
            )

            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["snapshot"], "dispatch/T1-1")
            self.assertEqual(result["inputsRecorded"], 2)

            attempt_dir = wdd / "dispatch" / "T1-1"
            self.assertTrue(attempt_dir.is_dir())
            self.assertEqual(stat.S_IMODE(attempt_dir.stat().st_mode), 0o700)

            brief_copy = attempt_dir / "tasks" / "T1.md"
            context_copy = attempt_dir / "shared-context" / "contract.md"
            self.assertEqual(
                brief_copy.read_text(encoding="utf-8"),
                (wdd / "tasks" / "T1.md").read_text(encoding="utf-8"),
            )
            self.assertEqual(context_copy.read_text(encoding="utf-8"), "contract body\n")
            for copy in (brief_copy, context_copy):
                self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o400)
                with self.assertRaises(PermissionError):
                    copy.write_text("tampered\n", encoding="utf-8")

            task = StateStore(Path(state)).read()["tasks"]["T1"]
            self.assertEqual(task["snapshot"], "dispatch/T1-1")
            self.assertEqual(
                {entry["path"] for entry in task["inputs"]},
                {"tasks/T1.md", "shared-context/contract.md"},
            )
            for entry in task["inputs"]:
                self.assertEqual(entry["sha256"], artifact_sha256(wdd / entry["path"]))

    def test_context_ref_anchor_strips_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "shared-context").mkdir(exist_ok=True)
            (wdd / "shared-context" / "contract.md").write_text("x\n", encoding="utf-8")
            _apply_v5_scope(
                root, wdd, state, context=["shared-context/contract.md#AC-1"]
            )
            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            copy = wdd / "dispatch" / "T1-1" / "shared-context" / "contract.md"
            self.assertTrue(copy.is_file())
            self.assertEqual(copy.read_text(encoding="utf-8"), "x\n")

    def test_duplicate_context_ref_copies_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "shared-context").mkdir(exist_ok=True)
            (wdd / "shared-context" / "contract.md").write_text("x\n", encoding="utf-8")
            _apply_v5_scope(
                root, wdd, state,
                context=[
                    "shared-context/contract.md#orders",
                    "shared-context/contract.md#refunds",
                ],
            )
            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            # brief + the one deduped context file, not brief + 2 context refs.
            self.assertEqual(result["inputsRecorded"], 2)
            task = StateStore(Path(state)).read()["tasks"]["T1"]
            self.assertEqual(len(task["inputs"]), 2)

    def test_v5_task_without_context_refs_snapshots_brief_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_v5_scope(root, wdd, state)
            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["inputsRecorded"], 1)
            task = StateStore(Path(state)).read()["tasks"]["T1"]
            self.assertEqual([entry["path"] for entry in task["inputs"]], ["tasks/T1.md"])

    def test_second_start_after_block_unblock_gets_attempt_two(self) -> None:
        """Re-start via block -> unblock -> start: leases.start_task's
        _reattach path (for an in-progress task) does not begin a new
        attempt on purpose (see the cli.py comment on 'start'), so this test
        drives the path that genuinely returns the task to 'todo' first."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_v5_scope(root, wdd, state)

            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)["snapshot"], "dispatch/T1-1")

            self.assertEqual(
                _cli(state, "block", "--task", "T1", "--reason", "waiting on something")[0], 0
            )
            self.assertEqual(_cli(state, "unblock", "--task", "T1")[0], 0)

            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["snapshot"], "dispatch/T1-2")
            self.assertTrue((wdd / "dispatch" / "T1-1").is_dir())
            self.assertTrue((wdd / "dispatch" / "T1-2").is_dir())

            task = StateStore(Path(state)).read()["tasks"]["T1"]
            self.assertEqual(task["snapshot"], "dispatch/T1-2")

    def test_reattach_does_not_materialize_a_new_attempt(self) -> None:
        """A plain re-run of 'start' on an already in_progress task reattaches
        the same worktree (leases._reattach); it must not mint a new attempt
        or silently re-bind input digests to whatever the source files are
        NOW (that would defeat Task 3's rebind gate)."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_v5_scope(root, wdd, state)

            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            first_snapshot = json.loads(out)["snapshot"]

            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertTrue(result["action"].startswith("reattach:"), result["action"])
            self.assertEqual(result["snapshot"], first_snapshot)
            self.assertFalse((wdd / "dispatch" / "T1-2").exists())

    def test_legacy_start_records_snapshot_but_no_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            state_path = str(wdd / "state.json")
            self.assertEqual(_cli(state_path, "init", "--repo", str(root))[0], 0)
            self.assertEqual(_cli(state_path, "config", "set", "merge.surface", "local")[0], 0)
            self.assertEqual(
                _cli(
                    state_path, "config", "set", "models",
                    '{"planning": null, "implementation": {"default": null, "highRisk": null}, '
                    '"review": null}',
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
            (wdd / "tasks").mkdir(exist_ok=True)
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nLegacy brief.\n", encoding="utf-8")
            plan = _plan({"baseRef": "wdd/scope-x"})
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, out = _cli(
                state_path, "plan", "apply", "--plan", str(plan_file), "--repo", str(root)
            )
            self.assertEqual(code, 0, out)

            code, out = _cli(state_path, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["inputsRecorded"], 0)
            self.assertIn("snapshot", result)

            task = StateStore(Path(state_path)).read()["tasks"]["T1"]
            self.assertEqual(task["inputs"], [])
            self.assertIsNotNone(task["snapshot"])
            self.assertTrue((wdd / task["snapshot"]).is_dir())


class SchemaHandoverFieldsTest(unittest.TestCase):
    """Direct schema.py coverage for the new optional task fields."""

    def test_task_state_defaults_snapshot_none_and_inputs_empty(self) -> None:
        task = task_state("T1")
        self.assertIsNone(task["snapshot"])
        self.assertEqual(task["inputs"], [])

    def test_validate_state_accepts_populated_inputs_and_snapshot(self) -> None:
        from wave_delivery.schema import validate_state

        state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
        state["tasks"]["T1"] = task_state("T1", conflict_domains=["src/t1/**"])
        state["tasks"]["T1"]["snapshot"] = "dispatch/T1-1"
        state["tasks"]["T1"]["inputs"] = [{"path": "tasks/T1.md", "sha256": "sha256:abc"}]
        validate_state(state)  # must not raise

    def test_validate_state_rejects_malformed_inputs_entry(self) -> None:
        from wave_delivery.schema import validate_state

        state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
        state["tasks"]["T1"] = task_state("T1", conflict_domains=["src/t1/**"])
        state["tasks"]["T1"]["inputs"] = [{"path": "tasks/T1.md"}]  # missing sha256
        with self.assertRaises(ValidationError):
            validate_state(state)


class MaterializeAttemptUnitTest(unittest.TestCase):
    """Direct (non-CLI) coverage of materialize_attempt/record_attempt."""

    def test_materialize_attempt_unknown_task_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir()
            state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
            with self.assertRaises(ValidationError):
                materialize_attempt(state, wdd, "NOPE")

    def test_materialize_attempt_missing_brief_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir()
            state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
            state["tasks"]["T1"] = task_state("T1", conflict_domains=["src/t1/**"])
            with self.assertRaises(ValidationError):
                materialize_attempt(state, wdd, "T1")


if __name__ == "__main__":
    unittest.main()
