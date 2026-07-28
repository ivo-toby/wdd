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
    # This environment's global gitconfig signs commits by default (gpg-agent
    # is unreachable from the sandbox). wddctl's own internal git calls (e.g.
    # merge_task's "wdd: merge ..." commit) never pass -c commit.gpgsign=false
    # themselves -- only the explicit test-authored commits below do -- so
    # disable it at the repo level once instead.
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=root, check=True)
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
    """Assumes submit already ran; records the review/verification/freshness
    evidence that takes the task from in_progress/review to merge_ready.

    Freshness is required so a caller can go on to actually call `wddctl
    merge`: task_gate's merge_ready branch re-checks freshness (unlike its
    in_progress branch, which does not), so a task without it is stuck at
    gate "needs_freshness" and `merge_task` refuses it.
    """
    code, out = _cli(
        state, "review", "record", "--task", task_id, "--reviewer", "t", "--findings", "[]"
    )
    assert code == 0, out
    code, out = _cli(state, "verify", "record", "--task", task_id, "--status", "passed")
    assert code == 0, out
    code, out = _cli(state, "freshness", "record", "--task", task_id, "--repo", str(root))
    assert code == 0, out
    assert StateStore(Path(state)).read()["tasks"][task_id]["status"] == "merge_ready"


def _scope_in_finalize(
    tmp: str, *, surface: str = "local", mode: str = "controller", gh_log: str | None = None
) -> tuple[Path, str, Path]:
    """Drive a fresh scratch scope through one task to the finalize phase.

    Shared across the finalize-verb test classes below: `wddctl finalize *`
    verbs all require derived_phase(state) in {"finalize", "delivered"},
    which only happens once every task is terminal. The cheapest path there
    is one task through the full execute-phase loop (start -> commit ->
    submit -> review/verify/freshness -> merge_ready -> merge), using the
    same start/submit/merge surface machinery a real run would (fake gh
    under the "pr" surface, exactly like test_execution_surfaces' HumanModeTest
    scaffolding).
    """
    root, state, bare = _bootstrap_ready_scope(tmp, surface=surface, mode=mode)
    _start_and_commit(state, root)
    if surface == "pr":
        assert gh_log is not None, "pr surface requires a gh_log path"
        with _fake_gh(gh_log):
            code, out = _cli(state, "submit", "--task", "T1", "--repo", str(root))
            assert code == 0, out
            _finish_to_merge_ready(state, root)
            code, out = _cli(state, "merge", "--task", "T1", "--repo", str(root))
            assert code == 0, out
    else:
        code, out = _cli(state, "submit", "--task", "T1", "--repo", str(root))
        assert code == 0, out
        _finish_to_merge_ready(state, root)
        code, out = _cli(state, "merge", "--task", "T1", "--repo", str(root))
        assert code == 0, out
    from wave_delivery.schema import derived_phase

    scope_state = StateStore(Path(state)).read()
    assert derived_phase(scope_state) == "finalize", scope_state
    return root, state, bare


def _deliver_scope(state: str, root: Path, *, by: str = "bob") -> None:
    """Drive an already-finalize-phase scope (from `_scope_in_finalize`) the
    rest of the way to "delivered": a local human merge of the epic branch
    into the checked-out "main" (the default target), then `finalize
    delivered`. Leaves derived_phase(state) == "delivered"."""
    base_ref = StateStore(Path(state)).read()["scope"]["baseRef"]
    subprocess.run(["git", "checkout", "-q", "main"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
         "merge", "--no-ff", "-m", "final merge", base_ref],
        cwd=root, check=True,
    )
    code, out = _cli(state, "finalize", "delivered", "--by", by, "--repo", str(root))
    assert code == 0, out
    from wave_delivery.schema import derived_phase

    assert derived_phase(StateStore(Path(state)).read()) == "delivered", out


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


class FinalizeAndDeliveredPhasesTest(unittest.TestCase):
    """Task 2: Phase transitions for finalize and delivered.

    derived_phase now computes finalize (scope present + ratified +
    tasks non-empty + ALL task statuses in {done, cancelled}) and
    delivered (finalize phase + finalize.delivered marker present).
    """

    def test_all_done_tasks_transitions_to_finalize_phase(self) -> None:
        from wave_delivery.schema import derived_phase, new_state
        from wave_delivery.schema import task_state

        state = new_state("SCOPE-x", base_ref="main")
        state["constitution"]["status"] = "ratified"
        state["constitution"]["ratification"] = {
            "by": "alice",
            "decisionFingerprint": "fp",
        }
        state["tasks"]["T1"] = task_state("T1")
        state["tasks"]["T1"]["status"] = "done"
        state["tasks"]["T2"] = task_state("T2")
        state["tasks"]["T2"]["status"] = "cancelled"

        self.assertEqual(derived_phase(state), "finalize")

    def test_finalize_with_delivered_marker_transitions_to_delivered_phase(self) -> None:
        from wave_delivery.schema import derived_phase, new_state
        from wave_delivery.schema import task_state

        state = new_state("SCOPE-x", base_ref="main")
        state["constitution"]["status"] = "ratified"
        state["constitution"]["ratification"] = {
            "by": "alice",
            "decisionFingerprint": "fp",
        }
        state["tasks"]["T1"] = task_state("T1")
        state["tasks"]["T1"]["status"] = "done"
        state["finalize"] = {
            "delivered": {
                "at": "2024-01-01T00:00:00Z",
                "by": "bob",
                "headSha": "abc123",
            }
        }

        self.assertEqual(derived_phase(state), "delivered")

    def test_one_task_in_progress_stays_in_execute_phase(self) -> None:
        from wave_delivery.schema import derived_phase, new_state
        from wave_delivery.schema import task_state

        state = new_state("SCOPE-x", base_ref="main")
        state["constitution"]["status"] = "ratified"
        state["constitution"]["ratification"] = {
            "by": "alice",
            "decisionFingerprint": "fp",
        }
        state["tasks"]["T1"] = task_state("T1")
        state["tasks"]["T1"]["status"] = "done"
        state["tasks"]["T2"] = task_state("T2")
        state["tasks"]["T2"]["status"] = "in_progress"

        self.assertEqual(derived_phase(state), "execute")

    def test_unratified_stays_setup_phase(self) -> None:
        from wave_delivery.schema import derived_phase, new_state
        from wave_delivery.schema import task_state

        state = new_state("SCOPE-x", base_ref="main")
        state["constitution"]["status"] = "draft"
        state["tasks"]["T1"] = task_state("T1")
        state["tasks"]["T1"]["status"] = "done"

        self.assertEqual(derived_phase(state), "setup")

    def test_no_scope_stays_setup_phase(self) -> None:
        from wave_delivery.schema import derived_phase, new_setup_state

        state = new_setup_state()
        self.assertEqual(derived_phase(state), "setup")


class FinalizeStateValidationTest(unittest.TestCase):
    """Task 2: Validate optional finalize section.

    validate_state must accept:
    - Absent finalize section (all existing states)
    - Valid finalize sections with optional review/verification/handoff/delivered
    And must reject:
    - Malformed delivered (missing required fields or wrong types)
    - Malformed review/verification/handoff (not dicts when present)
    """

    def test_absent_finalize_section_is_valid(self) -> None:
        from wave_delivery.schema import validate_state, new_state

        state = new_state("SCOPE-x")
        # No finalize section at all
        validate_state(state)  # Should not raise

    def test_empty_finalize_section_is_valid(self) -> None:
        from wave_delivery.schema import validate_state, new_state

        state = new_state("SCOPE-x")
        state["finalize"] = {}
        validate_state(state)  # Should not raise

    def test_finalize_with_review_dict_is_valid(self) -> None:
        from wave_delivery.schema import validate_state, new_state

        state = new_state("SCOPE-x")
        state["finalize"] = {
            "review": {
                "findings": [],
                "reviewer": "alice",
                "outcome": "passed",
            }
        }
        validate_state(state)  # Should not raise

    def test_finalize_with_verification_dict_is_valid(self) -> None:
        from wave_delivery.schema import validate_state, new_state

        state = new_state("SCOPE-x")
        state["finalize"] = {
            "verification": {
                "status": "passed",
                "command": "make test",
            }
        }
        validate_state(state)  # Should not raise

    def test_finalize_with_handoff_dict_is_valid(self) -> None:
        from wave_delivery.schema import validate_state, new_state

        state = new_state("SCOPE-x")
        state["finalize"] = {
            "handoff": {
                "pr": "https://github.com/org/repo/pull/123",
                "headSha": "abc123",
            }
        }
        validate_state(state)  # Should not raise

    def test_finalize_with_valid_delivered_is_valid(self) -> None:
        from wave_delivery.schema import validate_state, new_state

        state = new_state("SCOPE-x")
        state["finalize"] = {
            "delivered": {
                "at": "2024-01-01T00:00:00Z",
                "by": "bob",
                "headSha": "abc123",
            }
        }
        validate_state(state)  # Should not raise

    def test_finalize_review_not_dict_is_invalid(self) -> None:
        from wave_delivery.schema import validate_state, new_state
        from wave_delivery.errors import ValidationError

        state = new_state("SCOPE-x")
        state["finalize"] = {
            "review": "not a dict"
        }
        with self.assertRaises(ValidationError) as cm:
            validate_state(state)
        self.assertIn("finalize.review", str(cm.exception))

    def test_finalize_verification_not_dict_is_invalid(self) -> None:
        from wave_delivery.schema import validate_state, new_state
        from wave_delivery.errors import ValidationError

        state = new_state("SCOPE-x")
        state["finalize"] = {
            "verification": []
        }
        with self.assertRaises(ValidationError) as cm:
            validate_state(state)
        self.assertIn("finalize.verification", str(cm.exception))

    def test_finalize_handoff_not_dict_is_invalid(self) -> None:
        from wave_delivery.schema import validate_state, new_state
        from wave_delivery.errors import ValidationError

        state = new_state("SCOPE-x")
        state["finalize"] = {
            "handoff": "not a dict"
        }
        with self.assertRaises(ValidationError) as cm:
            validate_state(state)
        self.assertIn("finalize.handoff", str(cm.exception))

    def test_finalize_delivered_not_dict_is_invalid(self) -> None:
        from wave_delivery.schema import validate_state, new_state
        from wave_delivery.errors import ValidationError

        state = new_state("SCOPE-x")
        state["finalize"] = {
            "delivered": "not a dict"
        }
        with self.assertRaises(ValidationError) as cm:
            validate_state(state)
        self.assertIn("finalize.delivered", str(cm.exception))

    def test_finalize_delivered_missing_at_is_invalid(self) -> None:
        from wave_delivery.schema import validate_state, new_state
        from wave_delivery.errors import ValidationError

        state = new_state("SCOPE-x")
        state["finalize"] = {
            "delivered": {
                "by": "bob",
                "headSha": "abc123",
            }
        }
        with self.assertRaises(ValidationError) as cm:
            validate_state(state)
        self.assertIn("finalize.delivered.at", str(cm.exception))

    def test_finalize_delivered_missing_by_is_invalid(self) -> None:
        from wave_delivery.schema import validate_state, new_state
        from wave_delivery.errors import ValidationError

        state = new_state("SCOPE-x")
        state["finalize"] = {
            "delivered": {
                "at": "2024-01-01T00:00:00Z",
                "headSha": "abc123",
            }
        }
        with self.assertRaises(ValidationError) as cm:
            validate_state(state)
        self.assertIn("finalize.delivered.by", str(cm.exception))

    def test_finalize_delivered_missing_headSha_is_invalid(self) -> None:
        from wave_delivery.schema import validate_state, new_state
        from wave_delivery.errors import ValidationError

        state = new_state("SCOPE-x")
        state["finalize"] = {
            "delivered": {
                "at": "2024-01-01T00:00:00Z",
                "by": "bob",
            }
        }
        with self.assertRaises(ValidationError) as cm:
            validate_state(state)
        self.assertIn("finalize.delivered.headSha", str(cm.exception))

    def test_finalize_delivered_empty_string_at_is_invalid(self) -> None:
        from wave_delivery.schema import validate_state, new_state
        from wave_delivery.errors import ValidationError

        state = new_state("SCOPE-x")
        state["finalize"] = {
            "delivered": {
                "at": "",
                "by": "bob",
                "headSha": "abc123",
            }
        }
        with self.assertRaises(ValidationError) as cm:
            validate_state(state)
        self.assertIn("finalize.delivered.at", str(cm.exception))


class FinalizePhaseGateTest(unittest.TestCase):
    """Task 3: every finalize verb refuses outside the finalize/delivered phases.

    T1 stays "todo" here (nothing started) -- the cheapest state that is
    ratified + has a scope + has a non-terminal task, so derived_phase is
    "execute", not "finalize".
    """

    def test_all_finalize_verbs_refuse_in_execute_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _bootstrap_ready_scope(tmp, surface="local")
            for argv in (
                ("finalize", "review", "record", "--reviewer", "r", "--findings", "[]", "--repo", str(root)),
                ("finalize", "verify", "record", "--status", "passed", "--repo", str(root)),
                ("finalize", "handoff", "--repo", str(root)),
                ("finalize", "delivered", "--by", "bob", "--repo", str(root)),
            ):
                code, out, err = _cli_full(state, *argv)
                self.assertNotEqual(code, 0, (argv, out))
                self.assertIn("finalize", err.lower(), (argv, err))


class FinalizeReviewRecordTest(unittest.TestCase):
    """Task 3: `finalize review record` -- Spec §6's whole-epic-branch review."""

    def test_clean_review_records_passed_pinned_to_current_base_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            code, out = _cli(
                state, "finalize", "review", "record", "--reviewer", "alice",
                "--findings", "[]", "--repo", str(root),
            )
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["outcome"], "passed")

            base_ref = StateStore(Path(state)).read()["scope"]["baseRef"]
            base_sha = subprocess.run(
                ["git", "-C", str(root), "rev-parse", base_ref],
                check=True, text=True, stdout=subprocess.PIPE,
            ).stdout.strip()
            self.assertEqual(result["headSha"], base_sha)

            review = StateStore(Path(state)).read()["finalize"]["review"]
            self.assertEqual(review["reviewer"], "alice")
            self.assertEqual(review["findings"], [])
            self.assertEqual(review["headSha"], base_sha)

    def test_p1_finding_blocks_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            code, out = _cli(
                state, "finalize", "review", "record", "--reviewer", "alice",
                "--findings", '[{"severity":"P1","summary":"missing acceptance criterion"}]',
                "--repo", str(root),
            )
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)["outcome"], "blocked")
            self.assertEqual(
                StateStore(Path(state)).read()["finalize"]["review"]["outcome"], "blocked"
            )

    def test_review_record_after_delivered_is_refused(self) -> None:
        # Reviewer finding: mutating finalize verbs must refuse once
        # finalize.delivered is recorded -- recording new review evidence
        # against a scope whose human merge already happened is meaningless.
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            _deliver_scope(state, root)

            code, out, err = _cli_full(
                state, "finalize", "review", "record", "--reviewer", "r",
                "--findings", "[]", "--repo", str(root),
            )
            self.assertNotEqual(code, 0, out)
            self.assertIn("already delivered", err)
            self.assertIsNone(StateStore(Path(state)).read()["finalize"].get("review"))


class FinalizeVerifyRecordTest(unittest.TestCase):
    """Task 3: `finalize verify record` -- mirrors task-level verify's status
    vocabulary, plus a required justification for "unavailable"."""

    def test_passed_records_pinned_to_current_base_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            code, out = _cli(
                state, "finalize", "verify", "record", "--status", "passed",
                "--command", "pytest -q", "--repo", str(root),
            )
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["status"], "passed")

            base_ref = StateStore(Path(state)).read()["scope"]["baseRef"]
            base_sha = subprocess.run(
                ["git", "-C", str(root), "rev-parse", base_ref],
                check=True, text=True, stdout=subprocess.PIPE,
            ).stdout.strip()
            self.assertEqual(result["headSha"], base_sha)

            verification = StateStore(Path(state)).read()["finalize"]["verification"]
            self.assertEqual(verification["command"], "pytest -q")
            self.assertEqual(verification["headSha"], base_sha)

    def test_unavailable_without_justification_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            code, out, err = _cli_full(
                state, "finalize", "verify", "record", "--status", "unavailable", "--repo", str(root)
            )
            self.assertNotEqual(code, 0, out)
            self.assertIn("justification", err.lower())
            self.assertNotIn("verification", StateStore(Path(state)).read().get("finalize", {}))

    def test_unavailable_with_explicit_justification_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            code, out = _cli(
                state, "finalize", "verify", "record", "--status", "unavailable",
                "--justification", "no CI runner reachable from here", "--repo", str(root),
            )
            self.assertEqual(code, 0, out)
            verification = StateStore(Path(state)).read()["finalize"]["verification"]
            self.assertEqual(verification["status"], "unavailable")
            self.assertEqual(verification["justification"], "no CI runner reachable from here")

    def test_unavailable_falls_back_to_configured_default_justification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            wdd = root / ".wdd"
            assert _cli(
                state, "config", "set", "verification.unavailableJustification",
                '"no sandboxed network access"',
            )[0] == 0
            # Editing config.json after ratification is governance drift; a
            # governed verb (finalize verify record is one) refuses until
            # it is re-signed.
            assert _cli(state, "constitution", "amend", "--by", "t")[0] == 0

            code, out = _cli(
                state, "finalize", "verify", "record", "--status", "unavailable", "--repo", str(root)
            )
            self.assertEqual(code, 0, out)
            verification = StateStore(Path(state)).read()["finalize"]["verification"]
            self.assertEqual(verification["justification"], "no sandboxed network access")


class FinalizeHandoffTest(unittest.TestCase):
    """Task 3: `finalize handoff` -- pr surface pushes + opens the epic->target
    PR; local surface records pr: None with operator instructions. Never
    merges: there is no code path that performs the target merge itself."""

    def _clean_evidence(self, state: str, root: Path) -> None:
        code, out = _cli(
            state, "finalize", "review", "record", "--reviewer", "r", "--findings", "[]",
            "--repo", str(root),
        )
        assert code == 0, out
        code, out = _cli(
            state, "finalize", "verify", "record", "--status", "passed",
            "--command", "pytest -q", "--repo", str(root),
        )
        assert code == 0, out

    def test_pr_surface_pushes_base_and_creates_epic_to_target_pr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = str(Path(tmp) / "gh.log")
            root, state, bare = _scope_in_finalize(tmp, surface="pr", mode="controller", gh_log=log_path)
            self._clean_evidence(state, root)
            base_ref = StateStore(Path(state)).read()["scope"]["baseRef"]

            with _fake_gh(log_path):
                code, out = _cli(state, "finalize", "handoff", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["pr"], "https://github.invalid/pr/1")
            self.assertEqual(result["targetBranch"], "main")
            self.assertNotIn("instructions", result)

            log = _gh_log(log_path)
            creates = [entry for entry in log if entry[:2] == ["pr", "create"]]
            # One from submit (task branch -> scope base), one from handoff
            # (scope base -> target) -- the handoff call is the last.
            self.assertGreaterEqual(len(creates), 2, log)
            handoff_call = creates[-1]
            self.assertEqual(handoff_call[handoff_call.index("--head") + 1], base_ref)
            self.assertEqual(handoff_call[handoff_call.index("--base") + 1], "main")

            base_ref_local = subprocess.run(
                ["git", "-C", str(root), "rev-parse", base_ref],
                check=True, text=True, stdout=subprocess.PIPE,
            ).stdout.strip()
            base_ref_remote = subprocess.run(
                ["git", "--git-dir", str(bare), "rev-parse", base_ref],
                check=True, text=True, stdout=subprocess.PIPE,
            ).stdout.strip()
            self.assertEqual(base_ref_local, base_ref_remote, "handoff must push the base branch")

            handoff = StateStore(Path(state)).read()["finalize"]["handoff"]
            self.assertEqual(handoff["pr"], "https://github.invalid/pr/1")
            self.assertEqual(handoff["targetBranch"], "main")

    def test_local_surface_records_pr_none_with_operator_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local", mode="controller")
            self._clean_evidence(state, root)

            code, out = _cli(state, "finalize", "handoff", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertIsNone(result["pr"])
            self.assertIn("instructions", result)
            self.assertTrue(result["instructions"])

            handoff = StateStore(Path(state)).read()["finalize"]["handoff"]
            self.assertIsNone(handoff["pr"])

    def test_blocked_review_refuses_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            code, out = _cli(
                state, "finalize", "review", "record", "--reviewer", "r",
                "--findings", '[{"severity":"P1","summary":"bad"}]', "--repo", str(root),
            )
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)["outcome"], "blocked")

            code, out, err = _cli_full(state, "finalize", "handoff", "--repo", str(root))
            self.assertNotEqual(code, 0, out)
            self.assertIn("clean final review", err)
            self.assertIsNone(
                StateStore(Path(state)).read().get("finalize", {}).get("handoff")
            )

    def test_stale_evidence_after_new_base_commit_refuses_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            self._clean_evidence(state, root)
            scope_state = StateStore(Path(state)).read()
            base_ref = scope_state["scope"]["baseRef"]

            # Something moved the epic branch after evidence was recorded --
            # a direct commit, exactly like a task's evidence going stale
            # after a new commit lands on its branch. root stays on "main"
            # (merge_task's own base_ref checkout lives in the integration
            # worktree beside it -- see merge.py's _integration_dir), so
            # commit there directly instead of trying to check out base_ref
            # a second time in root.
            from wave_delivery.git import integration_worktree_path

            integration = integration_worktree_path(root, scope_state["scope"]["id"])
            (integration / "post_review_change.txt").write_text("late\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=integration, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
                 "commit", "-qm", "late change"],
                cwd=integration, check=True,
            )

            code, out, err = _cli_full(state, "finalize", "handoff", "--repo", str(root))
            self.assertNotEqual(code, 0, out)
            self.assertIn("stale", err)
            # Names what to redo, not just that it is stale.
            self.assertIn("finalize review record", err)

    def test_legacy_scope_without_config_gets_clear_migration_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            self._clean_evidence(state, root)
            (Path(state).parent / "config.json").unlink()

            code, out, err = _cli_full(state, "finalize", "handoff", "--repo", str(root))
            self.assertNotEqual(code, 0, out)
            self.assertIn("migrate --governance", err)

    def test_handoff_after_delivered_is_refused_with_no_new_gh_calls(self) -> None:
        # Reviewer finding: with finalize.delivered already recorded,
        # `finalize handoff` still exited 0, re-pushed the already-merged
        # base branch, and (pr surface) issued a second, genuinely duplicate
        # `gh pr create` against a branch that had already landed. Reproduce
        # that exact shape: a real handoff first (so a PR genuinely exists),
        # then deliver, then handoff again -- it must refuse before any push
        # or gh call, leaving the log exactly as the first handoff left it.
        with tempfile.TemporaryDirectory() as tmp:
            log_path = str(Path(tmp) / "gh.log")
            root, state, bare = _scope_in_finalize(tmp, surface="pr", mode="controller", gh_log=log_path)
            self._clean_evidence(state, root)

            with _fake_gh(log_path):
                code, out = _cli(state, "finalize", "handoff", "--repo", str(root))
            self.assertEqual(code, 0, out)

            _deliver_scope(state, root)
            log_before = _gh_log(log_path)

            with _fake_gh(log_path):
                code, out, err = _cli_full(state, "finalize", "handoff", "--repo", str(root))
            self.assertNotEqual(code, 0, out)
            self.assertIn("already delivered", err)

            log_after = _gh_log(log_path)
            self.assertEqual(log_after, log_before, "no new gh invocations must happen")

            # The handoff record from before delivery is untouched.
            handoff = StateStore(Path(state)).read()["finalize"]["handoff"]
            self.assertEqual(handoff["pr"], "https://github.invalid/pr/1")


class FinalizeDeliveredTest(unittest.TestCase):
    """Task 3: `finalize delivered` -- proves the epic branch head is
    reachable from the TARGET branch, reusing phase 4's either-ref ancestry
    machinery. Never merges anything itself."""

    def test_refuses_before_the_target_merge_has_happened(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            code, out, err = _cli_full(state, "finalize", "delivered", "--by", "bob", "--repo", str(root))
            self.assertNotEqual(code, 0, out)
            self.assertIn("has not happened", err)
            self.assertIsNone(
                StateStore(Path(state)).read().get("finalize", {}).get("delivered")
            )

    def test_succeeds_after_merging_base_into_target_locally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            base_ref = StateStore(Path(state)).read()["scope"]["baseRef"]

            # The human performs the final merge directly -- no wddctl
            # command involved, proving delivered needs none either.
            subprocess.run(["git", "checkout", "-q", "main"], cwd=root, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
                 "merge", "--no-ff", "-m", "final merge", base_ref],
                cwd=root, check=True,
            )

            code, out = _cli(state, "finalize", "delivered", "--by", "bob", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["by"], "bob")
            self.assertEqual(result["targetBranch"], "main")

            from wave_delivery.schema import derived_phase

            final_state = StateStore(Path(state)).read()
            self.assertEqual(final_state["finalize"]["delivered"]["by"], "bob")
            self.assertEqual(derived_phase(final_state), "delivered")

    def test_succeeds_via_stale_local_target_but_merged_origin_target(self) -> None:
        # Mirrors test_execution_surfaces.ObservedMergeEitherRefTest /
        # HumanModeTest's remote-merge case, at scope granularity: the human
        # merges the epic branch into target on a clone of origin and pushes
        # -- the controller's local target branch never moves.
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local", mode="controller")
            base_ref = StateStore(Path(state)).read()["scope"]["baseRef"]

            subprocess.run(["git", "-C", str(root), "push", "-q", "origin", "main:main"], check=True)
            subprocess.run(
                ["git", "-C", str(root), "push", "-q", "origin", f"{base_ref}:{base_ref}"], check=True
            )

            clone = Path(tmp) / "human-clone"
            subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
            subprocess.run(["git", "-C", str(clone), "checkout", "-q", "main"], check=True)
            subprocess.run(
                ["git", "-C", str(clone), "-c", "user.email=t@t", "-c", "user.name=t",
                 "-c", "commit.gpgsign=false", "merge", "--no-ff", "-m", "final merge",
                 f"origin/{base_ref}"],
                check=True,
            )
            subprocess.run(["git", "-C", str(clone), "push", "-q", "origin", "main"], check=True)

            local_main_before = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "main"],
                check=True, text=True, stdout=subprocess.PIPE,
            ).stdout.strip()
            origin_main_after = subprocess.run(
                ["git", "--git-dir", str(bare), "rev-parse", "main"],
                check=True, text=True, stdout=subprocess.PIPE,
            ).stdout.strip()
            self.assertNotEqual(
                local_main_before, origin_main_after,
                "the test setup must leave the controller's local target stale",
            )

            code, out = _cli(state, "finalize", "delivered", "--by", "bob", "--repo", str(root))
            self.assertEqual(code, 0, out)

            local_main_after = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "main"],
                check=True, text=True, stdout=subprocess.PIPE,
            ).stdout.strip()
            self.assertEqual(local_main_after, local_main_before)

    def test_rerunning_delivered_is_refused_and_leaves_the_record_untouched(self) -> None:
        # Pinned behavior (reviewer's "your judgment, documented" call):
        # re-running `finalize delivered` refuses rather than silently
        # re-verifying and overwriting `by`/`at` -- a second caller's name
        # must not be able to overwrite who was actually recorded as having
        # observed the merge.
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            _deliver_scope(state, root, by="bob")
            first = StateStore(Path(state)).read()["finalize"]["delivered"]
            self.assertEqual(first["by"], "bob")

            code, out, err = _cli_full(
                state, "finalize", "delivered", "--by", "carol", "--repo", str(root)
            )
            self.assertNotEqual(code, 0, out)
            self.assertIn("already delivered", err)

            second = StateStore(Path(state)).read()["finalize"]["delivered"]
            self.assertEqual(second, first)


class FinalizeStatusTest(unittest.TestCase):
    """Task 3: `finalize status` prints the finalize section and the phase."""

    def test_status_reflects_phase_and_recorded_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            code, out = _cli(state, "finalize", "status")
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["phase"], "finalize")
            self.assertEqual(result["finalize"], {})

            code, out = _cli(
                state, "finalize", "review", "record", "--reviewer", "r", "--findings", "[]",
                "--repo", str(root),
            )
            self.assertEqual(code, 0, out)

            code, out = _cli(state, "finalize", "status")
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["finalize"]["review"]["outcome"], "passed")


class FinalizeEscapeHatchTest(unittest.TestCase):
    """Task 3: the escape hatch that already refuses `event apply --event
    task.merged` refuses `scope.delivered` the same way."""

    def test_event_apply_scope_delivered_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            code, out, err = _cli_full(state, "event", "apply", "--event", "scope.delivered")
            self.assertNotEqual(code, 0, out)
            self.assertIn("finalize delivered", err)
            self.assertIsNone(
                StateStore(Path(state)).read().get("finalize", {}).get("delivered")
            )


class FinalizeGovernedVerbTest(unittest.TestCase):
    """Task 3: finalize review/verify/handoff/delivered are governed verbs --
    they refuse when config.json/constitution.md drifted from what was
    ratified, same as start/submit/merge/review/verify already do."""

    def test_governance_drift_blocks_finalize_review_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, bare = _scope_in_finalize(tmp, surface="local")
            assert _cli(state, "config", "set", "review.blockingSeverities", '["P1"]')[0] == 0

            code, out, err = _cli_full(
                state, "finalize", "review", "record", "--reviewer", "r", "--findings", "[]",
                "--repo", str(root),
            )
            self.assertNotEqual(code, 0, out)
            self.assertIn("drift", err.lower())


if __name__ == "__main__":
    unittest.main()
