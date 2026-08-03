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
import unittest.mock
from pathlib import Path

from wave_delivery.config import default_config, load_config, save_config, set_value, validate_config
from wave_delivery.engine import bounded_next_actions
from wave_delivery.errors import IllegalTransition, ValidationError
from wave_delivery.handover import (
    ensure_dispatch_gitignore,
    inputs_status,
    materialize_attempt,
    record_attempt,
    rebind_attempt,
)
from wave_delivery.intake import artifact_sha256
from wave_delivery.runner import probe_command, runner_command_digest
from wave_delivery.schema import new_state, task_state
from wave_delivery.setup import init_repository, migrate_governance
from wave_delivery.store import StateStore


def _cli_full(state: str, *argv: str) -> tuple[int, str, str]:
    """Like _cli, but also captures stderr for asserting on refusal messages.

    Copied from tests/test_intake.py's helper of the same name (no
    cross-file imports between test modules)."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        from wave_delivery.cli import main

        code = main(["--state", state, *argv])
    return code, stdout.getvalue(), stderr.getvalue()


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
    """Canonical ladder walk: epic new -> spec -> research skip -> design.
    Copied from tests/test_intake.py's helper of the same name. Task 4
    (spec Sec1): the slug is born at the top of the ladder, so a real epic
    ("demo") must exist before any rung verb is legal on a non-legacy scope
    -- every rung artifact below lives under `epics/demo/`, not flat."""
    assert _cli(state, "epic", "new", "--slug", "demo")[0] == 0
    epic_dir = wdd / "epics" / "demo"
    (epic_dir / "spec.md").write_text(_spec_text(), encoding="utf-8")
    assert _cli(state, "intake", "spec", "--approved-by", approver)[0] == 0
    assert (
        _cli(
            state, "intake", "research", "--skip", "--by", approver,
            "--reason", "no external contracts",
        )[0]
        == 0
    )
    (epic_dir / "design.md").write_text(_design_text(), encoding="utf-8")
    assert (
        _cli(
            state, "intake", "design", "--approved-by", approver,
            "--deliverable-command", "true",
        )[0]
        == 0
    )


def _start_and_commit(state: str, root: Path, task_id: str = "T1", message: str = "do work") -> None:
    """Copied from tests/test_intake.py's helper of the same name (no
    cross-file imports between test modules)."""
    code, out = _cli(state, "start", "--task", task_id, "--repo", str(root))
    assert code == 0, out
    worktree = Path(json.loads(out)["worktree"])
    (worktree / "change.txt").write_text(f"{message}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=worktree, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "commit", "-qm", message],
        cwd=worktree, check=True,
    )


def _apply_v5_scope(
    root: Path,
    wdd: Path,
    state: str,
    *,
    task_id: str = "T1",
    context: list[str] | None = None,
    model: str | None = None,
    review_model: str | None = None,
    risk: str = "normal",
) -> None:
    """Walk the intake ladder and composite-approve a one-task plan for
    task_id, with a real brief on disk (and optional context refs / routing
    overrides -- model/review_model feed Task 4's dispatch tests; risk feeds
    the e2e test's need for a task where review is actually required)."""
    _walk_intake(state, wdd)
    epic_dir = wdd / "epics" / "demo"
    (epic_dir / "tasks").mkdir(exist_ok=True)
    (epic_dir / "tasks" / f"{task_id}.md").write_text(
        f"# {task_id}\n\nBrief body for {task_id}.\n\n## Deliverable\n\n"
        f"{task_id} works end to end.\n", encoding="utf-8"
    )
    task_entry: dict = {
        "id": task_id,
        "specPath": f"tasks/{task_id}.md",
        "risk": risk,
        "context": context or [],
    }
    if model is not None:
        task_entry["model"] = model
    if review_model is not None:
        task_entry["reviewModel"] = review_model
    plan = {
        "schemaVersion": 1,
        "kind": "wdd_plan",
        "scope": {"id": "SCOPE-demo", "baseRef": "wdd/scope-x"},
        "tasks": [task_entry],
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

    def test_start_ensures_dispatch_gitignore_even_when_missing(self) -> None:
        """I2 regression: `migrate_governance` early-returns ("config.json
        already exists") before its own `ensure_dispatch_gitignore` call, so
        an existing install whose .gitignore never got the dispatch/ entry
        (via `wddctl migrate`'s schema path, or any other reason it went
        missing) never reaches it that way. The entry must also be ensured
        at the point `dispatch/` itself is first created -- here, by
        `materialize_attempt` inside `start`."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            gitignore = wdd / ".gitignore"
            self.assertTrue(gitignore.exists())  # init already wrote it
            gitignore.unlink()
            _apply_v5_scope(root, wdd, state)

            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)

            self.assertTrue(gitignore.exists())
            self.assertIn("dispatch/", gitignore.read_text(encoding="utf-8").splitlines())


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
                (wdd / "epics" / "demo" / "tasks" / "T1.md").read_text(encoding="utf-8"),
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
                # shared-context/ refs are always global; everything else
                # (here, tasks/T1.md) resolves under the active epic's
                # namespace (Task 4, spec Sec1).
                source = (
                    wdd / entry["path"] if entry["path"].startswith("shared-context/")
                    else wdd / "epics" / "demo" / entry["path"]
                )
                self.assertEqual(entry["sha256"], artifact_sha256(source))

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

    def test_stuck_started_task_self_heals_on_retry_with_same_idempotency_key(self) -> None:
        """Simulates the crash window between task.started committing and
        materialize_attempt/record_attempt succeeding: a task left in_progress
        with snapshot/inputs cleared (StateStore hand-edit stands in for the
        real failure, cheaper and more deterministic than fault injection). A
        retry of 'start' with the SAME idempotency key used to hit
        duplicate=True and skip materialization forever (stuck permanently);
        it must now self-heal by materializing on that retry."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_v5_scope(root, wdd, state)

            code, out = _cli(
                state, "start", "--task", "T1", "--repo", str(root),
                "--idempotency-key", "retry-key",
            )
            self.assertEqual(code, 0, out)
            first_snapshot = json.loads(out)["snapshot"]
            self.assertEqual(first_snapshot, "dispatch/T1-1")

            store = StateStore(Path(state))
            stuck = store.read()
            stuck["tasks"]["T1"]["snapshot"] = None
            stuck["tasks"]["T1"]["inputs"] = []
            store.write(stuck)

            code, out = _cli(
                state, "start", "--task", "T1", "--repo", str(root),
                "--idempotency-key", "retry-key",
            )
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertTrue(result["duplicate"])
            self.assertIsNotNone(result.get("snapshot"))
            self.assertGreater(result.get("inputsRecorded", 0), 0)

            recorded = StateStore(Path(state)).read()["tasks"]["T1"]
            self.assertIsNotNone(recorded["snapshot"])
            self.assertTrue((wdd / recorded["snapshot"]).is_dir())

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

    def test_legacy_start_missing_brief_degrades_to_success_with_snapshot_error(self) -> None:
        """I1 regression: `start_task` commits `task.started` before
        `materialize_attempt` runs. On a legacy scope with a missing brief
        file, materialization used to hard-fail AFTER the transition landed
        -- the whole 'start' command failed, but the task was already
        in_progress, so a same-idempotency-key retry re-ran this exact block
        and hit the identical ValidationError forever: permanently wedged,
        worktree never printed. Legacy scopes record no `inputs` anyway, so
        degrading to a snapshot-less success loses no doctrine (v5 scopes
        catch a missing/edited source file earlier, via the chokepoint's
        plan_drift/intake-drift check, before 'start' ever reaches this
        point -- see the cli.py comment on 'start')."""
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

            # Remove the brief AFTER plan apply -- a legacy scope has no
            # composite baseline to catch this any earlier the way v5 does.
            (wdd / "tasks" / "T1.md").unlink()

            code, out = _cli(state_path, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertIsNone(result["snapshot"])
            self.assertIn("snapshotError", result)
            self.assertTrue(result["snapshotError"])
            self.assertTrue(result.get("worktree"))

            task = StateStore(Path(state_path)).read()["tasks"]["T1"]
            self.assertEqual(task["status"], "in_progress")
            self.assertIsNone(task["snapshot"])
            self.assertEqual(task["inputs"], [])


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

    def test_materialize_attempt_with_relative_wdd_dir(self) -> None:
        """Task 5 finding: the documented default invocation (`wddctl start
        --repo .` with no `--state`, so `wdd_dir` is the CLI's relative
        default `.wdd`) must not crash. Before the fix, the final
        `attempt_dir.relative_to(wdd_resolved)` compared a still-relative
        `attempt_dir` (built from the unresolved `wdd_dir` argument) against
        the resolved/absolute `wdd_resolved`, raising ValueError on every
        relative-path caller -- masked until now because every existing
        test passes an absolute `wdd_dir`."""
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir()
            (wdd / "tasks").mkdir()
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
            state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
            state["tasks"]["T1"] = task_state(
                "T1", conflict_domains=["src/t1/**"], spec_path="tasks/T1.md"
            )

            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                result = materialize_attempt(state, Path(".wdd"), "T1")
            finally:
                os.chdir(cwd)

            self.assertEqual(result["snapshot"], "dispatch/T1-1")
            self.assertTrue((wdd / "dispatch" / "T1-1" / "tasks" / "T1.md").is_file())

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

    def test_materialize_attempt_retries_past_a_precreated_colliding_dir(self) -> None:
        """The glob-count-then-mkdir(exist_ok=False) allocation in
        _next_attempt_number/materialize_attempt is not atomic: something can
        create the candidate dir between the count and the mkdir (a stray
        leftover from a previous partial run, a concurrent racer). Faking a
        pre-created 'T1-1' by forcing the computed attempt number to 1 (via
        mock, per the fix-round's own suggestion -- deterministic, no fault
        injection needed) reproduces exactly that collision: materialize_attempt
        must retry the next number rather than propagate a raw FileExistsError."""
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir()
            (wdd / "tasks").mkdir()
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
            state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
            state["tasks"]["T1"] = task_state(
                "T1", conflict_domains=["src/t1/**"], spec_path="tasks/T1.md"
            )

            (wdd / "dispatch").mkdir()
            (wdd / "dispatch" / "T1-1").mkdir()

            with unittest.mock.patch(
                "wave_delivery.handover._next_attempt_number", return_value=1
            ):
                result = materialize_attempt(state, wdd, "T1")
            self.assertEqual(result["snapshot"], "dispatch/T1-2")
            self.assertTrue((wdd / "dispatch" / "T1-2").is_dir())

    def test_materialize_attempt_gives_up_after_bounded_collision_retries(self) -> None:
        """Every candidate number from the computed attempt onward is already
        taken (pathological, but the retry loop must still terminate cleanly
        with a ValidationError rather than looping forever or leaking a raw
        FileExistsError)."""
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir()
            (wdd / "tasks").mkdir()
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
            state = _ratified(new_state("SCOPE-x", base_ref="wdd/x"))
            state["tasks"]["T1"] = task_state(
                "T1", conflict_domains=["src/t1/**"], spec_path="tasks/T1.md"
            )

            dispatch_dir = wdd / "dispatch"
            dispatch_dir.mkdir()
            for n in range(1, 101):
                (dispatch_dir / f"T1-{n}").mkdir()

            with unittest.mock.patch(
                "wave_delivery.handover._next_attempt_number", return_value=1
            ):
                with self.assertRaises(ValidationError):
                    materialize_attempt(state, wdd, "T1")


# --- Task 3: input-version binding ------------------------------------------


def _apply_v5_scope_two_tasks(
    root: Path, wdd: Path, state: str, *, contexts: dict[str, list[str]] | None = None
) -> None:
    """Walk the intake ladder and composite-approve a two-task plan (T1, T2)
    with distinct conflict domains, real briefs on disk, and optional
    per-task context refs -- for sibling-task-unaffected coverage that
    `_apply_v5_scope`'s single task cannot exercise."""
    _walk_intake(state, wdd)
    epic_dir = wdd / "epics" / "demo"
    (epic_dir / "tasks").mkdir(exist_ok=True)
    contexts = contexts or {}
    tasks = []
    for task_id in ("T1", "T2"):
        (epic_dir / "tasks" / f"{task_id}.md").write_text(
            f"# {task_id}\n\nBrief body for {task_id}.\n", encoding="utf-8"
        )
        tasks.append(
            {
                "id": task_id,
                "specPath": f"tasks/{task_id}.md",
                "risk": "normal",
                "conflictDomains": [f"src/{task_id.lower()}/**"],
                "context": contexts.get(task_id, []),
            }
        )
    plan = {
        "schemaVersion": 1,
        "kind": "wdd_plan",
        "scope": {"id": "SCOPE-demo", "baseRef": "wdd/scope-x"},
        "tasks": tasks,
    }
    plan_file = root / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    code, out = _cli(
        state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
        "--approved-by", "t",
    )
    assert code == 0, out


def _restamp_plan(root: Path, state: str, *, approved_by: str = "t2") -> None:
    """Re-apply the currently-applied plan.json unchanged, with a fresh
    --approved-by.

    Spec Sec3's 'empty diff, re-stamp' remedy for plan_drift: apply_plan's
    'unchanged' path (no task-field diff) still recomputes the composite
    over the CURRENT file bytes and re-stamps when --approved-by is passed,
    which is exactly what clears a composite mismatch caused by editing a
    brief/context file's bytes without touching plan.json itself.
    """
    plan_file = root / "plan.json"
    code, out = _cli(
        state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
        "--approved-by", approved_by,
    )
    assert code == 0, out


class InputVersionBindingTest(unittest.TestCase):
    """Spec Sec3 input-version binding: `inputs_status`, the task-targeted
    gate on submit/review/verify/refresh/merge, `rebind`, and `next`'s
    per-task `inputs_changed` surfacing -- including the pinned interplay
    with 6a's scope-wide `plan_drift`."""

    def test_context_edit_after_start_gates_submit_not_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "shared-context").mkdir(exist_ok=True)
            (wdd / "shared-context" / "contract.md").write_text(
                "contract v1\n", encoding="utf-8"
            )
            _apply_v5_scope_two_tasks(
                root, wdd, state, contexts={"T1": ["shared-context/contract.md"]}
            )
            _start_and_commit(state, root, "T1")
            _start_and_commit(state, root, "T2")

            # Drift T1's context file, then re-stamp the plan (spec Sec3's
            # remedy for the scope-wide composite) so what's left is purely
            # T1's own stale recorded digest -- isolating the per-task gate.
            (wdd / "shared-context" / "contract.md").write_text(
                "contract v2 -- edited\n", encoding="utf-8"
            )
            _restamp_plan(root, state)

            code, out, err = _cli_full(state, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 5, err)
            self.assertIn("inputs changed", err.lower())
            self.assertIn("rebind", err.lower())

            # T2 (no context ref, untouched) submits cleanly: the gate is
            # per-task, not scope-wide.
            code, out = _cli(state, "submit", "--task", "T2", "--repo", str(root))
            self.assertEqual(code, 0, out)

            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            changed = [a for a in result["actions"] if a["action"] == "inputs_changed"]
            self.assertEqual([a["task"] for a in changed], ["T1"])
            self.assertEqual(changed[0]["path"], "shared-context/contract.md")
            self.assertIn("rebind", changed[0]["recordWith"])
            self.assertIn("--task T1", changed[0]["recordWith"])
            # T1's own gate action (await_worker, pre-drift) is suppressed --
            # not emitted alongside inputs_changed for the same task.
            self.assertNotIn(
                "await_worker", {a["action"] for a in result["actions"] if a["task"] == "T1"}
            )

    def test_merge_also_refuses_when_inputs_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_v5_scope(root, wdd, state)
            _start_and_commit(state, root, "T1")
            self.assertEqual(_cli(state, "submit", "--task", "T1", "--repo", str(root))[0], 0)
            self.assertEqual(
                _cli(state, "verify", "record", "--task", "T1", "--status", "passed")[0], 0
            )
            self.assertEqual(
                _cli(state, "freshness", "record", "--task", "T1", "--repo", str(root))[0], 0
            )

            (wdd / "epics" / "demo" / "tasks" / "T1.md").write_text(
                "# T1\n\nBrief body for T1 -- edited.\n", encoding="utf-8"
            )
            _restamp_plan(root, state)

            code, out, err = _cli_full(state, "merge", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 5, err)
            self.assertIn("inputs changed", err.lower())

    def test_rebind_clears_the_gate_and_re_records_current_digests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_v5_scope(root, wdd, state)
            _start_and_commit(state, root, "T1")

            new_brief = "# T1\n\nBrief body for T1 -- edited.\n"
            epic_dir = wdd / "epics" / "demo"
            (epic_dir / "tasks" / "T1.md").write_text(new_brief, encoding="utf-8")
            _restamp_plan(root, state)

            code, out, err = _cli_full(state, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 5, err)

            code, out = _cli(state, "rebind", "--task", "T1", "--by", "reviewer-t", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["inputsRecorded"], 1)

            task = StateStore(Path(state)).read()["tasks"]["T1"]
            self.assertEqual(
                task["inputs"][0]["sha256"], artifact_sha256(epic_dir / "tasks" / "T1.md")
            )
            events = StateStore(Path(state)).read()["events"]
            rebound = [e for e in events if e["type"] == "task.rebound"]
            self.assertEqual(len(rebound), 1)
            self.assertEqual(rebound[0]["task"], "T1")

            # Now clear: submit succeeds, and next has no inputs_changed for T1.
            code, out = _cli(state, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            code, out = _cli(state, "next")
            changed = [a for a in json.loads(out)["actions"] if a["action"] == "inputs_changed"]
            self.assertEqual(changed, [])

    def test_rebind_records_by_and_at_on_the_task(self) -> None:
        """I4 regression: `apply_mutation` persists events as {revision,
        type, task, idempotencyKey, at} only -- the caller's `data` (here,
        rebind's `--by`) never lands anywhere durable on its own. `rebind`
        validated `--by` and then dropped it. It must now be persisted on
        the task itself, the `{by, at}` idiom `scope.approval` already
        uses."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_v5_scope(root, wdd, state)
            _start_and_commit(state, root, "T1")

            (wdd / "epics" / "demo" / "tasks" / "T1.md").write_text(
                "# T1\n\nBrief body for T1 -- edited.\n", encoding="utf-8"
            )
            _restamp_plan(root, state)

            task_before = StateStore(Path(state)).read()["tasks"]["T1"]
            self.assertIsNone(task_before.get("rebound"))

            code, out = _cli(
                state, "rebind", "--task", "T1", "--by", "reviewer-t", "--repo", str(root)
            )
            self.assertEqual(code, 0, out)

            task = StateStore(Path(state)).read()["tasks"]["T1"]
            self.assertEqual(task["rebound"]["by"], "reviewer-t")
            self.assertIsInstance(task["rebound"]["at"], str)
            self.assertTrue(task["rebound"]["at"])

    def test_rebind_refuses_when_nothing_to_rebind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_v5_scope(root, wdd, state)
            _start_and_commit(state, root, "T1")

            code, out, err = _cli_full(
                state, "rebind", "--task", "T1", "--by", "t", "--repo", str(root)
            )
            self.assertEqual(code, 5, err)
            self.assertIn("nothing to rebind", err.lower())

    def test_restart_after_block_unblock_rematerializes_and_clears(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_v5_scope(root, wdd, state)

            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)

            (wdd / "epics" / "demo" / "tasks" / "T1.md").write_text(
                "# T1\n\nBrief body for T1 -- edited.\n", encoding="utf-8"
            )
            _restamp_plan(root, state)

            state_obj = StateStore(Path(state)).read()
            self.assertIsNotNone(inputs_status(state_obj, wdd, "T1"))

            self.assertEqual(
                _cli(state, "block", "--task", "T1", "--reason", "resolving drift")[0], 0
            )
            self.assertEqual(_cli(state, "unblock", "--task", "T1")[0], 0)

            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["snapshot"], "dispatch/T1-2")

            state_obj = StateStore(Path(state)).read()
            self.assertIsNone(inputs_status(state_obj, wdd, "T1"))

    def test_done_task_brief_edit_not_gated_after_plan_restamp(self) -> None:
        """Merged evidence is history (spec Sec3): once a task is done, its
        recorded inputs are never re-checked, even after a later edit to its
        own brief -- gated only by the scope-wide plan_drift blocker (6a)
        until the plan is re-stamped, exactly like any other post-merge
        drift. This is the interplay pin's other half: the FIRST sentence
        ('editing a NOT-started -- here, a DONE -- task's brief fires
        scope-wide plan_drift only') applies to a merged task the same way
        it does to a not-yet-started one."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_v5_scope(root, wdd, state)
            _start_and_commit(state, root, "T1")
            self.assertEqual(_cli(state, "submit", "--task", "T1", "--repo", str(root))[0], 0)
            self.assertEqual(
                _cli(state, "verify", "record", "--task", "T1", "--status", "passed")[0], 0
            )
            self.assertEqual(
                _cli(state, "freshness", "record", "--task", "T1", "--repo", str(root))[0], 0
            )
            self.assertEqual(_cli(state, "merge", "--task", "T1", "--repo", str(root))[0], 0)
            self.assertEqual(
                StateStore(Path(state)).read()["tasks"]["T1"]["status"], "done"
            )

            (wdd / "epics" / "demo" / "tasks" / "T1.md").write_text(
                "# T1\n\nBrief body for T1 -- edited post-merge.\n", encoding="utf-8"
            )

            # Before re-stamp: scope-wide plan_drift blocks next's actions,
            # but T1 (done) contributes no inputs_changed action -- merged
            # evidence is never gated or surfaced. All tasks are terminal, so
            # this is finalize-phase `next`; --repo is required for its own
            # git-backed checks the same way it is for any other verb.
            code, out = _cli(state, "next", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["actions"], [])
            self.assertEqual(result["blockers"][0]["code"], "plan_drift")
            # `inputs_status` itself is a pure digest comparison, unaware of
            # task status -- it WOULD report this mismatch. The done/
            # cancelled exemption ("never gated, never surfaced") is
            # enforced at the call sites: `_apply_input_binding`'s
            # ACTIVE_STATUSES-only loop (proven by `actions == []` above,
            # not an inputs_changed entry for T1) and the CLI chokepoint's
            # identical status check (no governed verb is legal against a
            # done task's status anyway, but the chokepoint skips the
            # inputs_status call entirely so a stale digest never produces
            # the misleading "inputs changed" refusal in its place).
            state_obj = StateStore(Path(state)).read()
            self.assertIsNotNone(inputs_status(state_obj, wdd, "T1"))

            _restamp_plan(root, state)
            code, out = _cli(state, "next", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(
                [b for b in result["blockers"] if b.get("code") == "plan_drift"], []
            )
            self.assertEqual(
                [a for a in result["actions"] if a["action"] == "inputs_changed"], []
            )

    def test_legacy_scope_exempt_from_gate_and_rebind(self) -> None:
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
            self.assertEqual(
                _cli(state_path, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))[0],
                0,
            )

            _start_and_commit(state_path, root, "T1")
            task = StateStore(Path(state_path)).read()["tasks"]["T1"]
            self.assertEqual(task["inputs"], [])  # legacy: snapshot lands, inputs stay empty

            (wdd / "tasks" / "T1.md").write_text("# T1\n\nLegacy brief -- edited.\n", encoding="utf-8")

            # No composite doctrine to drift, and no recorded inputs to gate:
            # submit proceeds untouched.
            code, out = _cli(state_path, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)

            code, out, err = _cli_full(
                state_path, "rebind", "--task", "T1", "--by", "t", "--repo", str(root)
            )
            self.assertEqual(code, 5, err)
            self.assertIn("nothing to rebind", err.lower())

            code, out = _cli(state_path, "next")
            self.assertEqual(code, 0, out)
            self.assertEqual(
                [a for a in json.loads(out)["actions"] if a["action"] == "inputs_changed"], []
            )

    def test_interplay_started_task_edit_fires_both_plan_drift_and_inputs_changed(self) -> None:
        """The pinned interplay: editing a STARTED task's own context file
        fires 6a's scope-wide plan_drift (the composite covers every brief
        and context file together) AND this task's per-task inputs_changed
        at the same time -- and, because `require_fresh_intake` raises
        plan_drift before any task-targeted verb's own gate runs, the
        governed-verb chokepoint enforces the documented remedy order: you
        cannot even reach the task-level gate (let alone rebind) until the
        plan is re-stamped first."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_v5_scope(root, wdd, state)
            _start_and_commit(state, root, "T1")

            (wdd / "epics" / "demo" / "tasks" / "T1.md").write_text(
                "# T1\n\nBrief body for T1 -- edited.\n", encoding="utf-8"
            )

            # Both fire in `next`, simultaneously, before any remedy:
            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["blockers"][0]["code"], "plan_drift")
            # Exactly one action survives: plan_drift emptied the list, and
            # inputs_changed for T1 is the only thing added back on top.
            self.assertEqual(len(result["actions"]), 1)
            self.assertEqual(result["actions"][0]["action"], "inputs_changed")
            self.assertEqual(result["actions"][0]["task"], "T1")

            # Remedy order: submit refuses on the SCOPE-wide drift first
            # (require_fresh_intake fires at the chokepoint, before rebind
            # or any task-targeted verb's own gate is even reached) --
            # rebind is equally blocked for the same reason.
            code, out, err = _cli_full(state, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 5, err)
            self.assertIn("plan drift", err.lower())
            self.assertNotIn("inputs changed", err.lower())

            code, out, err = _cli_full(
                state, "rebind", "--task", "T1", "--by", "t", "--repo", str(root)
            )
            self.assertEqual(code, 5, err)
            self.assertIn("plan drift", err.lower())

            # Re-stamp first (the documented remedy order) -- plan_drift
            # clears, but T1's own inputs_changed remains: its recorded
            # digest was taken before the edit, so it still mismatches the
            # bytes the re-stamp just approved.
            _restamp_plan(root, state)

            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(
                [b for b in result["blockers"] if b.get("code") == "plan_drift"], []
            )
            changed = [a for a in result["actions"] if a["action"] == "inputs_changed"]
            self.assertEqual([a["task"] for a in changed], ["T1"])

            # Now rebind (the second half of the remedy) clears it.
            code, out = _cli(state, "rebind", "--task", "T1", "--by", "t", "--repo", str(root))
            self.assertEqual(code, 0, out)
            code, out = _cli(state, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)


# --- Task 4: runner registry, probes, dispatch ------------------------------


FAKE_RUNNER = str(Path(__file__).resolve().parent / "fixtures" / "fake-runner" / "fake-runner")


def _runner_command() -> list[str]:
    return [FAKE_RUNNER, "--prompt", "{prompt}", "--worktree", "{worktree}", "--logfile", "{logfile}"]


def _register_runner(state: str, name: str, command: list[str]) -> None:
    payload = json.dumps({name: {"command": command}})
    assert _cli(state, "config", "set", "runners", payload)[0] == 0


class RunnerConfigValidationTest(unittest.TestCase):
    """Spec Sec6: config.json's optional `runners` map."""

    def test_good_shape_validates(self) -> None:
        config = default_config()
        config["runners"] = {
            "local": {"command": ["pi", "--headless", "--cd", "{worktree}", "-p", "{prompt}"]}
        }
        validate_config(config)  # must not raise

    def test_absent_runners_key_still_validates(self) -> None:
        config = default_config()
        del config["runners"]
        validate_config(config)  # optional: backward compatible with pre-6b configs

    def test_bad_shapes_rejected(self) -> None:
        base = default_config()
        bad_runner_maps = [
            "not-a-dict",
            {"local": "not-a-dict"},
            {"local": {}},
            {"local": {"command": []}},
            {"local": {"command": "not-a-list"}},
            {"local": {"command": [1, 2]}},
            {"local": {"command": [""]}},
        ]
        for bad in bad_runner_maps:
            config = default_config()
            config["runners"] = bad
            with self.assertRaises(ValidationError):
                validate_config(config)


class ProbeCommandUnitTest(unittest.TestCase):
    """`probe_command`/`runner_command_digest` as pure functions."""

    def test_probe_succeeds_against_fake_runner(self) -> None:
        result = probe_command(_runner_command())
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["exitCode"], 0)
        self.assertTrue(result["tokenSeen"])
        self.assertIsInstance(result["wallMs"], int)

    def test_probe_fails_when_token_is_wrong(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"FAKE_RUNNER_TOKEN": "NOPE"}):
            result = probe_command(_runner_command())
        self.assertFalse(result["ok"])
        self.assertFalse(result["tokenSeen"])
        self.assertEqual(result["exitCode"], 0)

    def test_probe_fails_on_nonzero_exit(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"FAKE_RUNNER_FAIL": "1"}):
            result = probe_command(_runner_command())
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["exitCode"], 0)

    def test_digest_is_stable_and_order_sensitive(self) -> None:
        command = _runner_command()
        self.assertEqual(runner_command_digest(command), runner_command_digest(list(command)))
        reordered = [command[1], command[0], *command[2:]]
        self.assertNotEqual(runner_command_digest(command), runner_command_digest(reordered))

    def test_digest_rejects_empty_or_malformed_command(self) -> None:
        for bad in ([], [""], ["ok", 1], "not-a-list"):
            with self.assertRaises(ValidationError):
                runner_command_digest(bad)  # type: ignore[arg-type]


class ProbeCliTest(unittest.TestCase):
    """`dispatch --probe-command` (ungoverned) and `dispatch --probe NAME` (governed)."""

    def test_probe_command_without_state_reports_and_does_not_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = str(Path(tmp) / "nonexistent" / "state.json")
            code, out = _cli(
                state, "dispatch", "--probe-command", json.dumps(_runner_command())
            )
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertTrue(result["ok"])
            self.assertFalse(result["recorded"])
            self.assertIn("re-verified", result["note"])

    def test_probe_command_with_state_records_probes_at_matching_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            command = _runner_command()
            code, out = _cli(state, "dispatch", "--probe-command", json.dumps(command))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertTrue(result["ok"])
            self.assertTrue(result["recorded"])
            digest = result["digest"]
            self.assertEqual(digest, runner_command_digest(command))
            recorded = StateStore(Path(state)).read()["probes"]
            self.assertIn(digest, recorded)
            self.assertTrue(recorded[digest]["ok"])
            self.assertIsInstance(recorded[digest]["at"], str)

    def test_probe_command_failure_is_not_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            with unittest.mock.patch.dict(os.environ, {"FAKE_RUNNER_FAIL": "1"}):
                code, out = _cli(
                    state, "dispatch", "--probe-command", json.dumps(_runner_command())
                )
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertFalse(result["ok"])
            self.assertFalse(result["recorded"])
            self.assertEqual(StateStore(Path(state)).read().get("probes"), None)

    def test_probe_name_governed_refuses_under_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            state = str(wdd / "state.json")
            self.assertEqual(_cli(state, "init", "--repo", str(root))[0], 0)
            self.assertEqual(_cli(state, "config", "set", "merge.surface", "local")[0], 0)
            self.assertEqual(
                _cli(
                    state, "config", "set", "models",
                    '{"planning": null, "implementation": {"default": null, "highRisk": null}, '
                    '"review": null}',
                )[0], 0,
            )
            config = load_config(wdd)
            if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
                self.assertEqual(
                    _cli(state, "config", "set", "verification.commands", '["true"]')[0], 0
                )
            _register_runner(state, "test-runner", _runner_command())
            self.assertEqual(_cli(state, "constitution", "ratify", "--by", "t")[0], 0)

            # Drift: edit config.json after ratification without amending
            # (the test_setup_config.py precedent for provoking drift).
            save_config(wdd, set_value(load_config(wdd), "concurrency.maxConcurrent", 5))

            code, out, err = _cli_full(state, "dispatch", "--probe", "test-runner", "--repo", str(root))
            self.assertEqual(code, 5, err)
            self.assertIn("drift", err.lower())

    def test_probe_name_unknown_runner_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            code, out, err = _cli_full(state, "dispatch", "--probe", "nope", "--repo", str(root))
            self.assertEqual(code, 2, err)
            self.assertIn("unknown runner", err.lower())

    def test_probe_name_success_records_and_matches_command_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            command = _runner_command()
            _register_runner(state, "test-runner", command)
            # Registering a runner edits config.json; re-sign so this isn't
            # governance drift -- that path is covered by its own test above.
            self.assertEqual(_cli(state, "constitution", "amend", "--by", "t2")[0], 0)
            code, out = _cli(state, "dispatch", "--probe", "test-runner", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertTrue(result["ok"])
            self.assertTrue(result["recorded"])
            self.assertEqual(result["digest"], runner_command_digest(command))


def _dispatch_ready_scope(
    tmp: str, *, model: str | None = "test-runner", review_model: str | None = None
) -> tuple[Path, str, Path]:
    """A ratified v5 scope with a probed `test-runner` registered and a
    started T1 (worktree + attempt snapshot on disk) -- the common setup
    every dispatch test builds on."""
    root, state = _ratified_repo(tmp)
    wdd = root / ".wdd"
    command = _runner_command()
    _register_runner(state, "test-runner", command)
    # Registering a runner edits config.json; re-sign so probing/dispatch
    # below aren't blocked by governance drift.
    assert _cli(state, "constitution", "amend", "--by", "t2")[0] == 0
    assert _cli(state, "dispatch", "--probe", "test-runner", "--repo", str(root))[0] == 0
    _apply_v5_scope(root, wdd, state, model=model, review_model=review_model)
    code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
    assert code == 0, out
    return root, state, wdd


class DispatchTaskTest(unittest.TestCase):
    """`dispatch --task ID --role worker|reviewer`: governed, digest-gated,
    input-binding-gated one-shot exec against the fake-runner fixture."""

    def test_refuses_unprobed_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _register_runner(state, "test-runner", _runner_command())  # never probed
            self.assertEqual(_cli(state, "constitution", "amend", "--by", "t2")[0], 0)
            _apply_v5_scope(root, wdd, state, model="test-runner")
            self.assertEqual(_cli(state, "start", "--task", "T1", "--repo", str(root))[0], 0)

            code, out, err = _cli_full(
                state, "dispatch", "--task", "T1", "--role", "worker", "--repo", str(root)
            )
            self.assertEqual(code, 5, err)
            self.assertIn("no passing probe record", err.lower())

    def test_refuses_non_runner_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _dispatch_ready_scope(tmp, model="claude-opus-not-a-runner")
            code, out, err = _cli_full(
                state, "dispatch", "--task", "T1", "--role", "worker", "--repo", str(root)
            )
            self.assertEqual(code, 5, err)
            self.assertIn("not a configured runner", err.lower())
            self.assertIn("harness-native", err.lower())

    def test_worker_dispatch_runs_fake_runner_and_extracts_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _dispatch_ready_scope(tmp)
            code, out = _cli(
                state, "dispatch", "--task", "T1", "--role", "worker", "--repo", str(root)
            )
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["exitCode"], 0)
            self.assertEqual(result["statusToken"], "DONE")
            self.assertIn("DONE", result["tail"])

            log_path = Path(result["log"])
            self.assertTrue(log_path.is_file())
            self.assertEqual(log_path.name, "T1-worker-1.log")
            self.assertEqual(stat.S_IMODE(log_path.stat().st_mode), 0o600)
            dispatch_dir = wdd / "dispatch"
            self.assertEqual(stat.S_IMODE(dispatch_dir.stat().st_mode), 0o700)

    def test_logfile_placeholder_does_not_collide_with_wddctls_own_capture(self) -> None:
        """I3 regression: {logfile} used to substitute to the SAME path
        wddctl's own post-run `_write_dispatch_file(log_path, output)`
        writes to (an atomic os.replace) -- a runner that keeps its own
        transcript at {logfile} had it clobbered the instant wddctl's own
        capture landed. {logfile} must now resolve to a distinct sibling
        path, so both files survive with their own content intact."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _dispatch_ready_scope(tmp)
            sentinel = "runner's own transcript, untouched"
            with unittest.mock.patch.dict(
                os.environ, {"FAKE_RUNNER_LOGFILE_SENTINEL": sentinel}
            ):
                code, out = _cli(
                    state, "dispatch", "--task", "T1", "--role", "worker", "--repo", str(root)
                )
            self.assertEqual(code, 0, out)
            result = json.loads(out)

            wddctl_log = Path(result["log"])
            self.assertTrue(wddctl_log.is_file())
            self.assertEqual(wddctl_log.name, "T1-worker-1.log")

            runner_log = wddctl_log.with_name("T1-worker-1-runner.log")
            self.assertTrue(runner_log.is_file())
            self.assertEqual(runner_log.read_text(encoding="utf-8"), sentinel)
            self.assertNotEqual(wddctl_log.read_text(encoding="utf-8"), sentinel)

    def test_worker_dispatch_reports_non_done_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _dispatch_ready_scope(tmp)
            with unittest.mock.patch.dict(os.environ, {"FAKE_RUNNER_TOKEN": "NEEDS_CONTEXT"}):
                code, out = _cli(
                    state, "dispatch", "--task", "T1", "--role", "worker", "--repo", str(root)
                )
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)["statusToken"], "NEEDS_CONTEXT")

    def test_second_worker_dispatch_gets_attempt_two_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _dispatch_ready_scope(tmp)
            first = _cli(state, "dispatch", "--task", "T1", "--role", "worker", "--repo", str(root))
            self.assertEqual(first[0], 0, first[1])
            second = _cli(state, "dispatch", "--task", "T1", "--role", "worker", "--repo", str(root))
            self.assertEqual(second[0], 0, second[1])
            self.assertEqual(Path(json.loads(second[1])["log"]).name, "T1-worker-2.log")

    def test_reviewer_dispatch_validates_result_and_review_collect_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _dispatch_ready_scope(tmp, model="test-runner", review_model="test-runner")
            _start_and_commit(state, root, "T1")
            self.assertEqual(_cli(state, "submit", "--task", "T1", "--repo", str(root))[0], 0)

            with unittest.mock.patch.dict(os.environ, {"FAKE_RUNNER_REVIEW_RESULT": "1"}):
                code, out = _cli(
                    state, "dispatch", "--task", "T1", "--role", "reviewer", "--repo", str(root)
                )
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["exitCode"], 0)
            self.assertIsNotNone(result["resultPath"])
            self.assertEqual(result["findings"], 0)

            result_path = Path(result["resultPath"])
            self.assertTrue(result_path.is_file())
            self.assertEqual(stat.S_IMODE(result_path.stat().st_mode), 0o600)
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["kind"], "wddctl_review_result")
            self.assertEqual(payload["task"], "T1")

            code, out = _cli(
                state, "review", "collect", "--task", "T1",
                "--result", str(result_path), "--repo", str(root),
            )
            self.assertEqual(code, 0, out)
            task = StateStore(Path(state)).read()["tasks"]["T1"]
            self.assertEqual(task["review"]["outcome"], "passed")

    def test_reviewer_dispatch_with_findings_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _dispatch_ready_scope(tmp, model="test-runner", review_model="test-runner")
            _start_and_commit(state, root, "T1")
            self.assertEqual(_cli(state, "submit", "--task", "T1", "--repo", str(root))[0], 0)

            findings = json.dumps(
                [{"severity": "P1", "summary": "bug", "file": "a.py", "line": 1}]
            )
            with unittest.mock.patch.dict(
                os.environ,
                {"FAKE_RUNNER_REVIEW_RESULT": "1", "FAKE_RUNNER_FINDINGS": findings},
            ):
                code, out = _cli(
                    state, "dispatch", "--task", "T1", "--role", "reviewer", "--repo", str(root)
                )
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["findings"], 1)

            code, out = _cli(
                state, "review", "collect", "--task", "T1",
                "--result", result["resultPath"], "--repo", str(root),
            )
            self.assertEqual(code, 0, out)
            task = StateStore(Path(state)).read()["tasks"]["T1"]
            self.assertEqual(task["review"]["outcome"], "blocking")

    def test_edited_command_after_probe_refuses_on_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _dispatch_ready_scope(tmp)

            # Edit the registered runner's command bytes (still named
            # "test-runner"), then re-ratify so this isn't just governance
            # drift -- isolating the digest-mismatch refusal specifically.
            edited_command = _runner_command() + ["--extra-flag"]
            _register_runner(state, "test-runner", edited_command)
            self.assertEqual(_cli(state, "constitution", "amend", "--by", "t2")[0], 0)

            code, out, err = _cli_full(
                state, "dispatch", "--task", "T1", "--role", "worker", "--repo", str(root)
            )
            self.assertEqual(code, 5, err)
            self.assertIn("no passing probe record", err.lower())
            self.assertNotEqual(
                runner_command_digest(edited_command), runner_command_digest(_runner_command())
            )

    def test_start_and_dispatch_work_with_relative_state_path(self) -> None:
        """Task 5 finding, dispatch's side: `dispatch_task`'s dispatch_dir/
        prompt_path/log_path were built the same way materialize_attempt's
        were (from the raw, possibly-relative `wdd_dir`), then substituted
        into argv for a subprocess run with cwd=worktree -- a DIFFERENT
        directory. A relative prompt/log path there resolves against the
        WRONG cwd inside the runner process, not wddctl's own. Reproduces
        the whole CLI stack with the documented default invocation (no
        --state flag; relative default `.wdd/state.json`, cwd inside the
        repo) rather than the absolute --state every other test in this
        file passes."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            command = _runner_command()
            _register_runner(state, "test-runner", command)
            self.assertEqual(_cli(state, "constitution", "amend", "--by", "t2")[0], 0)
            self.assertEqual(
                _cli(state, "dispatch", "--probe", "test-runner", "--repo", str(root))[0], 0
            )
            _apply_v5_scope(root, wdd, state, model="test-runner")

            cwd = os.getcwd()
            os.chdir(root)
            try:
                code, out = _cli(".wdd/state.json", "start", "--task", "T1", "--repo", ".")
                self.assertEqual(code, 0, out)
                self.assertEqual(json.loads(out)["snapshot"], "dispatch/T1-1")

                code, out = _cli(
                    ".wdd/state.json", "dispatch", "--task", "T1", "--role", "worker",
                    "--repo", ".",
                )
                self.assertEqual(code, 0, out)
                dispatch_result = json.loads(out)
            finally:
                os.chdir(cwd)

            self.assertEqual(dispatch_result["statusToken"], "DONE")
            log_path = Path(dispatch_result["log"])
            self.assertTrue(log_path.is_absolute(), dispatch_result["log"])
            self.assertTrue(log_path.is_file())

    def test_dispatch_gated_by_input_binding_like_other_task_verbs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _dispatch_ready_scope(tmp)
            (wdd / "epics" / "demo" / "tasks" / "T1.md").write_text(
                "# T1\n\nBrief body for T1 -- edited.\n\n## Deliverable\n\nT1 works.\n",
                encoding="utf-8",
            )
            _restamp_plan(root, state)

            code, out, err = _cli_full(
                state, "dispatch", "--task", "T1", "--role", "worker", "--repo", str(root)
            )
            self.assertEqual(code, 5, err)
            self.assertIn("inputs changed", err.lower())


# --- Task 5: end-to-end journey (6a + 6b compose) ---------------------------


class RunnerDispatchEndToEndTest(unittest.TestCase):
    """One continuous journey proving 6a (intake ladder, plan-approval
    composite, plan_drift) and 6b (snapshots, input-version binding, rebind,
    runner registry, dispatch) compose: a v5 scope with a runner registered
    the real way (probe-command -> config set runners -> amend) walked
    through start, worker dispatch, submit, reviewer dispatch, review
    collect, an inputs_changed+rebind interlude, verify/freshness/merge, and
    the finalize ladder to delivered."""

    def test_runner_registration_through_delivered_with_rebind_interlude(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"

            # --- runner registration: probe-command -> config set -> amend
            # (spec Sec6's registration order: prove the candidate before it
            # is ever config, then route it through governance like any
            # other config change).
            command = _runner_command()
            code, out = _cli(state, "dispatch", "--probe-command", json.dumps(command))
            self.assertEqual(code, 0, out)
            probe_result = json.loads(out)
            self.assertTrue(probe_result["ok"])
            self.assertTrue(probe_result["recorded"])
            _register_runner(state, "stub-runner", command)
            code, out = _cli(state, "constitution", "amend", "--by", "ivo")
            self.assertEqual(code, 0, out)

            # --- v5 scope: one high-risk task (review actually required
            # under the default risk_based policy -- the Task-3 coverage gap
            # this closes needs real review evidence, not an incidental
            # skip), routed to the registered runner for both roles, with a
            # context ref to drift in the interlude below.
            (wdd / "shared-context").mkdir(exist_ok=True)
            (wdd / "shared-context" / "contract.md").write_text(
                "contract v1\n", encoding="utf-8"
            )
            _apply_v5_scope(
                root, wdd, state,
                context=["shared-context/contract.md#orders"],
                model="stub-runner", review_model="stub-runner", risk="high",
            )

            # --- start: snapshot recorded ---------------------------------
            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            start_result = json.loads(out)
            self.assertEqual(start_result["snapshot"], "dispatch/T1-1")
            self.assertEqual(start_result["inputsRecorded"], 2)
            worktree = Path(start_result["worktree"])

            # --- dispatch worker via the fake runner: DONE token extracted
            code, out = _cli(
                state, "dispatch", "--task", "T1", "--role", "worker", "--repo", str(root)
            )
            self.assertEqual(code, 0, out)
            worker_result = json.loads(out)
            self.assertEqual(worker_result["statusToken"], "DONE")
            self.assertIn("DONE", worker_result["tail"])

            # The fake runner only proves the token contract (it never
            # touches git); the worker's actual commit is driven directly in
            # the worktree `start` already handed back, same as every other
            # dispatch test in this file.
            (worktree / "change.txt").write_text("work\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=worktree, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "do work"],
                cwd=worktree, check=True,
            )

            # --- submit ------------------------------------------------------
            code, out = _cli(state, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)

            # --- dispatch reviewer via the fake runner: clean result JSON --
            with unittest.mock.patch.dict(os.environ, {"FAKE_RUNNER_REVIEW_RESULT": "1"}):
                code, out = _cli(
                    state, "dispatch", "--task", "T1", "--role", "reviewer", "--repo", str(root)
                )
            self.assertEqual(code, 0, out)
            reviewer_result = json.loads(out)
            self.assertEqual(reviewer_result["findings"], 0)
            self.assertIsNotNone(reviewer_result["resultPath"])

            # --- review collect ---------------------------------------------
            code, out = _cli(
                state, "review", "collect", "--task", "T1",
                "--result", reviewer_result["resultPath"], "--repo", str(root),
            )
            self.assertEqual(code, 0, out)
            pre_rebind_review = StateStore(Path(state)).read()["tasks"]["T1"]["review"]
            self.assertEqual(pre_rebind_review["outcome"], "passed")

            # --- interlude: inputs_changed + rebind mid-scope ----------------
            # Drift T1's context ref, then re-stamp the plan (clears the
            # scope-wide plan_drift so what's left is purely T1's own stale
            # digest -- isolating the per-task gate, same pattern as
            # InputVersionBindingTest above).
            (wdd / "shared-context" / "contract.md").write_text(
                "contract v2 -- edited\n", encoding="utf-8"
            )
            _restamp_plan(root, state)

            state_obj = StateStore(Path(state)).read()
            self.assertIsNotNone(inputs_status(state_obj, wdd, "T1"))

            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            changed = [a for a in json.loads(out)["actions"] if a["action"] == "inputs_changed"]
            self.assertEqual([a["task"] for a in changed], ["T1"])

            # verify refuses for T1 while inputs_changed stands.
            code, out, err = _cli_full(
                state, "verify", "record", "--task", "T1", "--status", "passed"
            )
            self.assertEqual(code, 5, err)
            self.assertIn("inputs changed", err.lower())

            # Rebind: the recorded human decision that T1's already-collected
            # review evidence still stands despite the context drift -- this
            # is the named Task-3 gap: risk is high, so that review evidence
            # was actually load-bearing (required by the gate), not
            # incidental. Rebind re-records digests; it must NOT touch the
            # review record (merged-evidence/human-decision semantics: the
            # decision is that the existing work -- including its review --
            # stands, not that it is re-judged).
            code, out = _cli(
                state, "rebind", "--task", "T1", "--by", "ivo", "--repo", str(root)
            )
            self.assertEqual(code, 0, out)
            rebind_result = json.loads(out)
            self.assertEqual(rebind_result["inputsRecorded"], 2)

            state_obj = StateStore(Path(state)).read()
            self.assertIsNone(inputs_status(state_obj, wdd, "T1"))
            post_rebind_review = state_obj["tasks"]["T1"]["review"]
            self.assertEqual(post_rebind_review, pre_rebind_review)

            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            self.assertEqual(
                [a for a in json.loads(out)["actions"] if a["action"] == "inputs_changed"], []
            )

            # --- resume: verify / freshness / merge --------------------------
            code, out = _cli(state, "verify", "record", "--task", "T1", "--status", "passed")
            self.assertEqual(code, 0, out)
            code, out = _cli(state, "freshness", "record", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            code, out = _cli(state, "merge", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            self.assertEqual(
                StateStore(Path(state)).read()["tasks"]["T1"]["status"], "done"
            )

            # --- finalize ladder through delivered ----------------------------
            code, out = _cli(
                state, "finalize", "review", "record",
                "--reviewer", "ivo", "--findings", "[]", "--repo", str(root),
            )
            self.assertEqual(code, 0, out)
            code, out = _cli(
                state, "finalize", "verify", "record",
                "--results", json.dumps(
                    [{"command": "true", "status": "passed"},
                     {"command": "true", "status": "passed"}]
                ),
                "--repo", str(root),
            )
            self.assertEqual(code, 0, out)
            code, out = _cli(state, "finalize", "handoff", "--repo", str(root))
            self.assertEqual(code, 0, out)

            # The human merge: a real git merge into the target branch,
            # exactly as an operator would do outside wddctl (same idiom as
            # the finalize-ladder transcript in docs/wddctl.md).
            subprocess.run(["git", "checkout", "-q", "main"], cwd=root, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "-c", "commit.gpgsign=false", "merge", "--no-ff", "-q", "wdd/scope-x",
                 "-m", "merge scope SCOPE-x into main"],
                cwd=root, check=True,
            )
            code, out = _cli(state, "finalize", "delivered", "--by", "ivo", "--repo", str(root))
            self.assertEqual(code, 0, out)

            code, out = _cli(state, "next", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["phase"], "delivered")
            self.assertEqual(result["actions"], [])
            self.assertEqual(result["blockers"], [])


if __name__ == "__main__":
    unittest.main()
