"""Phase-5 finalize-phase tests.

Shared helpers below copy the scratch-repo / _cli / bare-origin / fake-gh
pattern from tests/test_execution_surfaces.py verbatim (no cross-file
imports between test modules; the fake `gh` fixture itself IS shared, at
tests/fixtures/fake-gh). Later phase-5 tasks (finalize state section,
`wddctl finalize` verbs, the finalize `next`/`status` ladder) add test
classes to this same file and reuse these helpers.
"""

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
from wave_delivery.config import load_config
from wave_delivery.git import worktree_for
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


def _repo_with_origin(tmp: str) -> tuple[Path, Path]:
    root = _git_repo(tmp)
    bare = Path(tmp) / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=root, check=True)
    return root, bare


def _cli(state: str, *argv: str) -> tuple[int, str]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = main(["--state", state, *argv])
    return code, stdout.getvalue()


def _cli_full(state: str, *argv: str) -> tuple[int, str, str]:
    """Like _cli, but also captures stderr for asserting on refusal messages."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(["--state", state, *argv])
    return code, stdout.getvalue(), stderr.getvalue()


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


@contextlib.contextmanager
def _fake_gh(log_path: str, *, fail: bool = False):
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


def _gh_log(log_path: str) -> list[list[str]]:
    path = Path(log_path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _bootstrap_ready_scope(
    tmp: str, *, surface: str, mode: str = "controller", base_ref: str = "wdd/scope-x",
    review_policy: str = "always",
) -> tuple[Path, str, Path]:
    """repo + bare origin, configured for the given surface/mode, scope applied.

    base_ref defaults to a NEW branch (created off the repo's initial commit),
    deliberately distinct from the checked-out "main" -- see the equivalent
    helper in test_execution_surfaces.py for why that matters for merge.

    review_policy defaults to "always" so review/verification evidence is
    reachable for a normal-risk task without needing a high-risk plan; T1
    stays admissible (todo) for the caller to drive through start/commit.
    """
    root, bare = _repo_with_origin(tmp)
    wdd = root / ".wdd"
    state = str(wdd / "state.json")
    assert _cli(state, "init", "--repo", str(root))[0] == 0
    assert _cli(state, "config", "set", "merge.surface", surface)[0] == 0
    assert _cli(state, "config", "set", "merge.mode", mode)[0] == 0
    config = load_config(wdd)
    if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
        assert _cli(state, "config", "set", "verification.commands", '["true"]')[0] == 0
    assert _cli(state, "constitution", "ratify", "--by", "t")[0] == 0
    plan_file = root / "plan.json"
    plan_file.write_text(
        json.dumps(_plan({"baseRef": base_ref, "reviewPolicy": review_policy})),
        encoding="utf-8",
    )
    code, out = _cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
    assert code == 0, out
    return root, state, bare


def _start_and_commit(state: str, root: Path, task_id: str = "T1", message: str = "do work") -> None:
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


def _extra_commit(state: str, root: Path, task_id: str = "T1", message: str = "more work") -> None:
    """Add a second, genuinely new commit on top of an already-submitted task branch."""
    read_state = StateStore(Path(state)).read()
    task = read_state["tasks"][task_id]
    worktree = worktree_for(root, read_state["scope"]["id"], task_id, task.get("worktree"))
    (worktree / "change2.txt").write_text(f"{message}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=worktree, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "commit", "-qm", message],
        cwd=worktree, check=True,
    )


def _finish_to_merge_ready(state: str, root: Path, task_id: str = "T1") -> None:
    """Assumes submit already ran; records the review/verification evidence
    that takes the task from in_progress/review to merge_ready."""
    code, out = _cli(
        state, "review", "record", "--task", task_id, "--reviewer", "t", "--findings", "[]"
    )
    assert code == 0, out
    code, out = _cli(state, "verify", "record", "--task", task_id, "--status", "passed")
    assert code == 0, out
    assert StateStore(Path(state)).read()["tasks"][task_id]["status"] == "merge_ready"


class PrUpgradeEventTypeTest(unittest.TestCase):
    """Task 1: submit's branch:<sha> -> real-URL upgrade must log its own event type.

    Regression: apply_mutation's event_type callable ("chosen_event") only
    ever resolved "task.pr_recorded" / "task.head_updated" -- so the upgrade
    path (a direct field update that changes only the pr string) still got
    logged as "task.head_updated" in state["events"], even though it
    deliberately does not apply that event's semantics (it must not discard
    review/verification/freshness evidence). The outcome dict already said
    "task.pr_upgraded"; the persisted event lied about it.
    """

    def test_upgrade_logs_pr_upgraded_and_leaves_evidence_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _bootstrap_ready_scope(tmp, surface="pr")
            _start_and_commit(state, root)
            log_path = str(Path(tmp) / "gh.log")

            # First submit: gh is down, falls back to branch:<sha>.
            with _fake_gh(log_path, fail=True):
                code, out = _cli(state, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            first = json.loads(out)
            self.assertTrue(first["pr"].startswith("branch:"), first)

            # Reach merge_ready on the branch: fallback pr, so review and
            # verification evidence exist to prove the upgrade leaves them
            # alone rather than merely being absent both before and after.
            _finish_to_merge_ready(state, root)
            before = StateStore(Path(state)).read()["tasks"]["T1"]
            self.assertEqual(before["status"], "merge_ready")
            self.assertEqual(before["review"]["outcome"], "passed")
            self.assertEqual(before["verification"]["status"], "passed")

            # Resubmit: gh is healthy now, head has not moved -> upgrade.
            with _fake_gh(log_path):
                code, out = _cli(state, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            second = json.loads(out)
            self.assertEqual(second["pr"], "https://github.invalid/pr/1")

            after_state = StateStore(Path(state)).read()
            after = after_state["tasks"]["T1"]
            self.assertEqual(after["status"], before["status"])
            self.assertEqual(after["review"], before["review"])
            self.assertEqual(after["verification"], before["verification"])

            self.assertEqual(after_state["events"][-1]["type"], "task.pr_upgraded")
            self.assertEqual(after_state["events"][-1]["task"], "T1")

    def test_genuine_head_change_still_logs_head_updated_and_invalidates_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _bootstrap_ready_scope(tmp, surface="local")
            _start_and_commit(state, root)

            code, out = _cli(state, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            first = json.loads(out)
            self.assertTrue(first["pr"].startswith("branch:"), first)

            _finish_to_merge_ready(state, root)
            before = StateStore(Path(state)).read()["tasks"]["T1"]
            self.assertEqual(before["status"], "merge_ready")
            self.assertIsNotNone(before["review"])
            self.assertIsNotNone(before["verification"])

            # A genuinely new commit on the task branch -- this must NOT be
            # mistaken for a pr upgrade even though the pr string also stays
            # a branch: fallback (local surface never touches gh).
            _extra_commit(state, root)

            code, out = _cli(state, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)

            after_state = StateStore(Path(state)).read()
            after = after_state["tasks"]["T1"]
            self.assertEqual(after_state["events"][-1]["type"], "task.head_updated")
            self.assertEqual(after_state["events"][-1]["task"], "T1")
            self.assertNotEqual(after["headSha"], before["headSha"])
            self.assertIsNone(after["review"])
            self.assertIsNone(after["verification"])
            self.assertIsNone(after["freshness"])
            self.assertNotEqual(after["status"], "merge_ready")


class SubmitIdempotencyRetryTest(unittest.TestCase):
    """Review finding: persisted_event_type must never raise.

    apply_mutation resolves event_type() BEFORE its idempotency-key
    short-circuit (see engine.apply_mutation): the callable runs, then
    the duplicate check happens, then (only for a non-duplicate) the
    mutator runs. The original bare `chosen_event` callable never touched
    git, so a duplicate-key retry was guaranteed to reach the cheap
    short-circuit regardless of repo state. persisted_event_type (added to
    fix the pr-upgrade mislabel above) resolves the branch tip via
    resolve_ref, which raises when the branch is gone -- so without a
    guard, a retry after the branch was deleted or pruned out-of-band would
    fail instead of returning the recorded duplicate.
    """

    def test_retry_after_branch_deleted_returns_duplicate_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _bootstrap_ready_scope(tmp, surface="local")
            _start_and_commit(state, root)

            code, out = _cli(
                state, "submit", "--task", "T1", "--repo", str(root),
                "--idempotency-key", "fixed-key-1",
            )
            self.assertEqual(code, 0, out)
            first = json.loads(out)
            self.assertFalse(first["duplicate"], first)
            self.assertTrue(first["pr"].startswith("branch:"), first)

            read_state = StateStore(Path(state)).read()
            task = read_state["tasks"]["T1"]
            branch = task["branch"]
            worktree = worktree_for(root, read_state["scope"]["id"], "T1", task.get("worktree"))

            # Simulate the branch vanishing out-of-band (deleted, or pruned
            # by GC) between the original submit and a client's retry of the
            # same idempotency key. The worktree has it checked out, so it
            # must go first.
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)], cwd=root, check=True
            )
            subprocess.run(["git", "branch", "-D", branch], cwd=root, check=True)

            code, out = _cli(
                state, "submit", "--task", "T1", "--repo", str(root),
                "--idempotency-key", "fixed-key-1",
            )
            self.assertEqual(code, 0, out)
            second = json.loads(out)
            self.assertTrue(second["duplicate"], second)
            self.assertEqual(second["event"], "duplicate")
            self.assertEqual(second["pr"], first["pr"])


if __name__ == "__main__":
    unittest.main()
