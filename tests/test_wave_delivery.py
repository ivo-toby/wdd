from __future__ import annotations

import contextlib
import io
import json
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from wave_delivery.cli import main
from wave_delivery.constitution import (
    probe_repository,
    ratification_status,
    read_proposal,
    write_proposal,
)
from wave_delivery.doctor import inspect_capabilities
from wave_delivery.engine import (
    admission_blocker,
    decorate_actions,
    admission_schedule,
    apply_event,
    bounded_next_actions,
    event_id,
    reconciliation_due,
    render_controller_state,
    status_summary,
    task_gate,
    transition,
)
from wave_delivery.errors import IllegalTransition, RevisionConflict, ValidationError
from wave_delivery.freshness import check_freshness, record_freshness
from wave_delivery.git import worktree_for
from wave_delivery.leases import release_task, start_task, submit_task
from wave_delivery.merge import merge_task, refresh_task
from wave_delivery.migration import apply_migration, plan_migration
from wave_delivery.monitor import monitor_once
from wave_delivery.plan import (
    apply_plan,
    ensure_base_branch,
    read_plan,
    state_from_plan,
    validate_plan,
)
from wave_delivery.review import (
    collect_review,
    collect_verification,
    record_review,
    record_verification,
    run_review,
)
from wave_delivery.schema import new_state, task_state, validate_state
from wave_delivery.store import StateStore


def ratified(state: dict) -> dict:
    state["constitution"] = {
        "status": "ratified",
        "ratification": {"by": "tester", "decisionFingerprint": "sha256:x", "at": "2026-01-01T00:00:00Z"},
    }
    return state


class BaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def git(self, repo: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.strip()

    def repository(self, name: str = "proj") -> Path:
        repo = self.root / name
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.email", "test@example.com")
        self.git(repo, "config", "user.name", "Test")
        (repo / "src").mkdir()
        (repo / "src" / "schema.ts").write_text("schema\n", encoding="utf-8")
        (repo / "src" / "ui.ts").write_text("ui\n", encoding="utf-8")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "init")
        return repo

    def plan_document(self, **overrides) -> dict:
        plan = {
            "schemaVersion": 1,
            "kind": "wdd_plan",
            "scope": {
                "id": "SCOPE-demo",
                "baseRef": "wdd/demo",
                "maxConcurrent": None,
                "reviewPolicy": "risk_based",
                "reconcileEveryNMerges": 3,
            },
            "tasks": [
                {"id": "TASK-A", "conflictDomains": ["src/schema.ts"]},
                {"id": "TASK-B", "conflictDomains": ["src/schema.ts"]},
                {"id": "TASK-C", "conflictDomains": ["src/ui.ts"]},
            ],
        }
        plan["scope"].update(overrides.pop("scope", {}))
        plan.update(overrides)
        return plan

    def scope(self, repo: Path, plan: dict | None = None) -> StateStore:
        plan = validate_plan(plan or self.plan_document())
        store = StateStore(repo / ".wdd" / "state.json")
        apply_plan(store, plan, repo=repo)
        apply_event(
            store,
            event_type="constitution.ratified",
            task_id=None,
            data={"by": "tester", "decisionFingerprint": "sha256:x"},
        )
        return store

    def commit_in(self, worktree: str, filename: str, content: str) -> str:
        path = Path(worktree)
        (path / filename).write_text(content, encoding="utf-8")
        self.git(path, "add", "-A")
        self.git(path, "commit", "-qm", f"work on {filename}")
        return self.git(path, "rev-parse", "HEAD")


class AdmissionTests(BaseTest):
    """Conflict-domain exclusion must be an invariant, not advice."""

    def state_with_shared_domain(self) -> dict:
        state = ratified(new_state("SCOPE-x", base_ref="wdd/x"))
        state["tasks"]["TASK-A"] = task_state("TASK-A", conflict_domains=["src/schema.ts"])
        state["tasks"]["TASK-B"] = task_state("TASK-B", conflict_domains=["src/schema.ts"])
        return state

    def test_task_started_refuses_an_overlapping_conflict_domain(self) -> None:
        state = self.state_with_shared_domain()
        state = transition(state, "task.started", "TASK-A", {})
        with self.assertRaises(IllegalTransition) as raised:
            transition(state, "task.started", "TASK-B", {})
        self.assertIn("conflict_domains", str(raised.exception))
        self.assertIn("src/schema.ts", str(raised.exception))

    def test_domains_are_released_only_when_a_task_is_done(self) -> None:
        state = self.state_with_shared_domain()
        state = transition(state, "task.started", "TASK-A", {})
        for status in ("in_progress", "review", "merge_ready"):
            state["tasks"]["TASK-A"]["status"] = status
            self.assertEqual(admission_blocker(state, "TASK-B")["code"], "conflict_domains")
        state["tasks"]["TASK-A"]["status"] = "done"
        self.assertIsNone(admission_blocker(state, "TASK-B"))

    def test_cancelled_and_blocked_tasks_release_their_domains(self) -> None:
        state = self.state_with_shared_domain()
        state = transition(state, "task.started", "TASK-A", {})
        state = transition(state, "task.cancelled", "TASK-A", {})
        self.assertIsNone(admission_blocker(state, "TASK-B"))

    def test_dependencies_gate_admission(self) -> None:
        state = ratified(new_state("SCOPE-x", base_ref="wdd/x"))
        state["tasks"]["TASK-A"] = task_state("TASK-A")
        state["tasks"]["TASK-B"] = task_state("TASK-B", depends_on=["TASK-A"])
        self.assertEqual(admission_blocker(state, "TASK-B")["code"], "dependencies")
        with self.assertRaises(IllegalTransition):
            transition(state, "task.started", "TASK-B", {})

    def test_max_concurrent_caps_active_tasks(self) -> None:
        state = ratified(new_state("SCOPE-x", base_ref="wdd/x", max_concurrent=1))
        state["tasks"]["TASK-A"] = task_state("TASK-A")
        state["tasks"]["TASK-B"] = task_state("TASK-B")
        state = transition(state, "task.started", "TASK-A", {})
        blocker = admission_blocker(state, "TASK-B")
        self.assertEqual(blocker["code"], "max_concurrent")
        self.assertEqual(blocker["limit"], 1)
        with self.assertRaises(IllegalTransition):
            transition(state, "task.started", "TASK-B", {})

    def test_next_never_proposes_two_conflicting_starts_in_one_pass(self) -> None:
        state = self.state_with_shared_domain()
        result = bounded_next_actions(state)
        starts = [action["task"] for action in result["actions"] if action["action"] == "start_task"]
        self.assertEqual(starts, ["TASK-A"])
        self.assertEqual(
            [blocker["code"] for blocker in result["blockers"] if blocker.get("task") == "TASK-B"],
            ["conflict_domains"],
        )

    def test_start_task_enforces_admission_against_live_state(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        start_task(store, repo=repo, task_id="TASK-A")
        with self.assertRaises(IllegalTransition) as raised:
            start_task(store, repo=repo, task_id="TASK-B")
        self.assertIn("conflict_domains", str(raised.exception))
        self.assertFalse((repo.parent / "proj.wdd" / "worktrees" / "SCOPE-demo" / "TASK-B").exists())


class ReviewFindingRegressionTests(BaseTest):
    """One test per reviewer finding, so none of them can silently return."""

    def test_conflict_domains_overlap_semantically_not_by_string_equality(self) -> None:
        for held, wanted, expect_blocked in [
            ("src/auth/**", "src/auth/token.py", True),
            ("src/auth/**", "src/auth/deep/nested.ts", True),
            ("src/**", "src/auth/**", True),
            ("*.py", "src/x.py", True),
            ("src/*.ts", "src/a.ts", True),
            ("src/auth/**", "src/schema.ts", False),
            ("docs/**", "src/**", False),
            ("src/a.ts", "src/b.ts", False),
        ]:
            state = ratified(new_state("SCOPE-x", base_ref="wdd/x"))
            state["tasks"]["HOLDER"] = task_state("HOLDER", conflict_domains=[held])
            state["tasks"]["HOLDER"]["status"] = "in_progress"
            state["tasks"]["WANTER"] = task_state("WANTER", conflict_domains=[wanted])
            blocked = admission_blocker(state, "WANTER") is not None
            self.assertEqual(
                blocked, expect_blocked, f"{held!r} vs {wanted!r} should block={expect_blocked}"
            )

    def test_an_option_like_base_ref_never_reaches_git(self) -> None:
        repo = self.repository()
        self.git(repo, "branch", "victim")
        with self.assertRaises(ValidationError):
            validate_plan(self.plan_document(scope={"baseRef": "-D"}))
        with self.assertRaises(ValidationError):
            ensure_base_branch(repo, "-D", from_ref="victim")
        # The branch it would have deleted is still there.
        self.assertIn("victim", self.git(repo, "branch", "--format=%(refname:short)").split())

    def test_emitted_commands_quote_shell_metacharacters(self) -> None:
        result = decorate_actions(
            {"actions": [{"task": "TASK-A; touch /tmp/pwned", "action": "start_task"}]},
            repo=".",
        )
        command = result["actions"][0]["command"]
        self.assertNotIn("; touch", shlex.split(command)[0])
        # Splitting the way a shell would yields the task id as ONE argument.
        self.assertIn("TASK-A; touch /tmp/pwned", shlex.split(command))

    def test_merge_and_refresh_check_the_revision_before_touching_git(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(result["worktree"], "src/schema.ts", "x\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        record_verification(store, task_id="TASK-A", status="passed", repo=repo)
        record_freshness(store, repo=repo, task_id="TASK-A")
        base_before = self.git(repo, "rev-parse", "wdd/demo")

        with self.assertRaises(RevisionConflict):
            merge_task(store, repo=repo, task_id="TASK-A", expected_revision=0)
        self.assertEqual(self.git(repo, "rev-parse", "wdd/demo"), base_before)

        head_before = self.git(repo, "rev-parse", "task/TASK-A")
        with self.assertRaises(RevisionConflict):
            refresh_task(store, repo=repo, task_id="TASK-A", expected_revision=0)
        self.assertEqual(self.git(repo, "rev-parse", "task/TASK-A"), head_before)

    def test_tightening_the_policy_re_gates_a_merge_ready_task(self) -> None:
        repo = self.repository()
        store = self.scope(repo)  # risk_based, TASK-A is normal risk -> no review
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(result["worktree"], "src/schema.ts", "x\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        record_verification(store, task_id="TASK-A", status="passed", repo=repo)
        record_freshness(store, repo=repo, task_id="TASK-A")
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "merge_ready")
        self.assertIsNone(store.read()["tasks"]["TASK-A"]["review"])

        apply_plan(store, validate_plan(self.plan_document(scope={"reviewPolicy": "always"})), repo=repo)
        state = store.read()
        self.assertEqual(task_gate(state, state["tasks"]["TASK-A"]), "needs_review")
        with self.assertRaises(IllegalTransition):
            merge_task(store, repo=repo, task_id="TASK-A")

        # And the re-gated task is not wedged: a review unblocks it again.
        record_review(store, task_id="TASK-A", findings=[], reviewer="rev", repo=repo)
        record_freshness(store, repo=repo, task_id="TASK-A")
        merge_task(store, repo=repo, task_id="TASK-A")
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "done")

    def test_external_evidence_must_name_real_commits(self) -> None:
        repo = self.repository()
        store = self.scope(repo, self.plan_document(scope={"reviewPolicy": "always"}))
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        head = self.commit_in(result["worktree"], "src/schema.ts", "x\n")
        submit_task(store, repo=repo, task_id="TASK-A")

        bogus = self.root / "bogus.json"
        bogus.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": "wddctl_review_result",
                    "task": "TASK-A",
                    "baseSha": "0" * 40,
                    "headSha": head,
                    "reviewer": "external",
                    "findings": [],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(IllegalTransition) as raised:
            collect_review(store, task_id="TASK-A", result_paths=[bogus], repo=repo)
        self.assertIn("not a commit", str(raised.exception))

    def test_submit_measures_against_the_base_the_task_started_from(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        # TASK-C starts, does nothing. TASK-A lands work, advancing the base.
        start_task(store, repo=repo, task_id="TASK-C")
        worked, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(worked["worktree"], "src/schema.ts", "real work\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        record_verification(store, task_id="TASK-A", status="passed", repo=repo)
        record_freshness(store, repo=repo, task_id="TASK-A")
        merge_task(store, repo=repo, task_id="TASK-A")

        # TASK-C still has zero commits of its own; the moving base must not
        # make an untouched branch look like work.
        with self.assertRaises(IllegalTransition) as raised:
            submit_task(store, repo=repo, task_id="TASK-C")
        self.assertIn("nothing to submit", str(raised.exception))


class MigrationTests(BaseTest):
    def v2_state(self) -> dict:
        return {
            "schemaVersion": 2,
            "revision": 4,
            "scope": {"id": "SCOPE-old", "kind": "epic", "baseRef": "epic/x"},
            "constitution": {
                "status": "ratified",
                "ratification": {"by": "i", "decisionFingerprint": "f", "at": "t"},
            },
            "tasks": {
                "T1": {
                    "id": "T1",
                    "specPath": "tasks/T1.md",
                    "status": "in_progress",
                    "dependsOn": [],
                    "conflictDomains": ["src/**"],
                    "branch": "task/T1",
                    "worktree": "/machine/that/no/longer/exists",
                    "headSha": None,
                    "pr": None,
                    "review": None,
                    "verification": None,
                    "freshness": None,
                    "merge": None,
                    "blocker": None,
                }
            },
            "waves": {},
            "monitoring": {
                "mode": "manual",
                "status": "inactive",
                "lastCheckedAt": None,
                "nextCheckDueAt": None,
                "observations": {},
            },
            "events": [],
            "appliedIdempotencyKeys": [],
            "telemetry": {"eventApplications": 4, "renderCount": 0},
        }

    def test_v2_state_is_not_a_dead_end(self) -> None:
        path = self.root / "state.json"
        path.write_text(json.dumps(self.v2_state()), encoding="utf-8")
        with self.assertRaises(ValidationError) as raised:
            StateStore(path).read()
        self.assertIn("migrate", str(raised.exception))

    def test_dry_run_does_not_write(self) -> None:
        path = self.root / "state.json"
        original = json.dumps(self.v2_state())
        path.write_text(original, encoding="utf-8")
        plan_migration(path)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_apply_converts_and_backs_up(self) -> None:
        path = self.root / "state.json"
        path.write_text(json.dumps(self.v2_state()), encoding="utf-8")
        result = apply_migration(path)
        self.assertTrue(Path(result["backup"]).exists())

        migrated = StateStore(path).read()
        self.assertEqual(migrated["schemaVersion"], 3)
        self.assertEqual(migrated["tasks"]["T1"]["risk"], "normal")
        self.assertEqual(migrated["tasks"]["T1"]["title"], "T1")
        self.assertEqual(migrated["scope"]["reviewPolicy"], "risk_based")
        self.assertNotIn("waves", migrated)
        # The stale absolute worktree from the old machine is cleared.
        self.assertIsNone(migrated["tasks"]["T1"]["worktree"])
        self.assertEqual(migrated["revision"], 4)

    def test_migrating_current_state_is_refused(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        with self.assertRaises(ValidationError) as raised:
            plan_migration(store.path)
        self.assertIn("already schema", str(raised.exception))


class PlanTests(BaseTest):
    def test_plan_requires_its_envelope(self) -> None:
        with self.assertRaises(ValidationError):
            validate_plan({"tasks": []})
        with self.assertRaises(ValidationError):
            validate_plan({"kind": "wdd_plan", "schemaVersion": 2, "scope": {"id": "S"}, "tasks": []})

    def test_plan_rejects_unknown_dependency_and_cycles(self) -> None:
        with self.assertRaises(ValidationError):
            validate_plan(self.plan_document(tasks=[{"id": "TASK-A", "dependsOn": ["NOPE"]}]))
        with self.assertRaises(ValidationError) as raised:
            validate_plan(
                self.plan_document(
                    tasks=[
                        {"id": "TASK-A", "dependsOn": ["TASK-B"]},
                        {"id": "TASK-B", "dependsOn": ["TASK-A"]},
                    ]
                )
            )
        self.assertIn("cycle", str(raised.exception))

    def test_plan_defaults(self) -> None:
        plan = validate_plan(
            {
                "schemaVersion": 1,
                "kind": "wdd_plan",
                "scope": {"id": "SCOPE-x"},
                "tasks": [{"id": "TASK-A"}],
            }
        )
        self.assertEqual(plan["scope"]["reviewPolicy"], "risk_based")
        self.assertIsNone(plan["scope"]["maxConcurrent"])
        self.assertEqual(plan["tasks"][0]["risk"], "normal")
        self.assertEqual(plan["tasks"][0]["specPath"], "tasks/TASK-A.md")
        self.assertEqual(plan["tasks"][0]["title"], "TASK-A")

    def test_apply_creates_the_base_branch(self) -> None:
        repo = self.repository()
        store = StateStore(repo / ".wdd" / "state.json")
        result = apply_plan(store, validate_plan(self.plan_document()), repo=repo)
        self.assertTrue(result["created"])
        self.assertEqual(result["base"]["action"], "created")
        self.assertEqual(self.git(repo, "rev-parse", "--verify", "wdd/demo"), result["base"]["baseSha"])

    def test_dry_run_writes_nothing(self) -> None:
        repo = self.repository()
        store = StateStore(repo / ".wdd" / "state.json")
        apply_plan(store, validate_plan(self.plan_document()), repo=repo, dry_run=True)
        self.assertFalse(store.exists())

    def test_reapply_is_a_no_op_and_can_add_tasks(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        unchanged = apply_plan(store, validate_plan(self.plan_document()), repo=repo)
        self.assertTrue(unchanged["unchanged"])

        plan = self.plan_document()
        plan["tasks"].append({"id": "TASK-D", "conflictDomains": ["docs/**"]})
        result = apply_plan(store, validate_plan(plan), repo=repo)
        self.assertEqual(result["diff"]["added"], ["TASK-D"])
        self.assertIn("TASK-D", store.read()["tasks"])

    def test_started_tasks_cannot_be_edited_or_removed(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        start_task(store, repo=repo, task_id="TASK-A")

        edited = self.plan_document()
        edited["tasks"][0]["conflictDomains"] = ["src/other.ts"]
        with self.assertRaises(IllegalTransition) as raised:
            apply_plan(store, validate_plan(edited), repo=repo)
        self.assertIn("not todo", str(raised.exception))

        removed = self.plan_document()
        removed["tasks"] = [task for task in removed["tasks"] if task["id"] != "TASK-A"]
        with self.assertRaises(IllegalTransition):
            apply_plan(store, validate_plan(removed), repo=repo)

    def test_todo_tasks_can_be_removed(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        plan = self.plan_document()
        plan["tasks"] = [task for task in plan["tasks"] if task["id"] != "TASK-C"]
        apply_plan(store, validate_plan(plan), repo=repo)
        self.assertNotIn("TASK-C", store.read()["tasks"])

    def test_preview_projects_rounds_without_gating(self) -> None:
        plan = self.plan_document()
        plan["tasks"][1]["dependsOn"] = ["TASK-A"]
        rounds = admission_schedule(state_from_plan(validate_plan(plan)))
        self.assertEqual(rounds[0]["tasks"], ["TASK-A", "TASK-C"])
        self.assertEqual(rounds[1]["tasks"], ["TASK-B"])

    def test_read_plan_reports_a_missing_file(self) -> None:
        with self.assertRaises(ValidationError):
            read_plan(self.root / "absent.json")


class ConcurrencyTests(BaseTest):
    def test_optional_revision_uses_the_current_one(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        before = store.read()["revision"]
        apply_event(store, event_type="note.added", task_id=None, data={"note": "n"})
        self.assertEqual(store.read()["revision"], before + 1)

    def test_explicit_stale_revision_still_conflicts(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        with self.assertRaises(RevisionConflict):
            apply_event(
                store,
                event_type="note.added",
                task_id=None,
                data={"note": "n"},
                expected_revision=0,
            )

    def test_an_explicit_idempotency_key_gives_at_most_once(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        apply_event(
            store,
            event_type="note.added",
            task_id=None,
            data={"note": "same"},
            idempotency_key="deploy-42",
        )
        revision = store.read()["revision"]
        _, duplicate = apply_event(
            store,
            event_type="note.added",
            task_id=None,
            data={"note": "same"},
            idempotency_key="deploy-42",
        )
        self.assertTrue(duplicate)
        self.assertEqual(store.read()["revision"], revision)

    def test_repeating_an_identical_event_is_not_silently_swallowed(self) -> None:
        """Payload-derived keys cannot tell a retry from a legitimate repeat."""
        repo = self.repository()
        store = self.scope(repo)
        apply_event(store, event_type="note.added", task_id=None, data={"note": "same"})
        revision = store.read()["revision"]
        _, duplicate = apply_event(
            store, event_type="note.added", task_id=None, data={"note": "same"}
        )
        self.assertFalse(duplicate)
        self.assertEqual(store.read()["revision"], revision + 1)

    def test_event_ids_are_unique_per_application(self) -> None:
        self.assertNotEqual(
            event_id(1, "reconcile.completed", None, {}),
            event_id(2, "reconcile.completed", None, {}),
        )

    def test_reconcile_can_be_completed_more_than_once(self) -> None:
        """Regression: a payload-derived key made this a one-shot per scope."""
        repo = self.repository()
        store = self.scope(repo)
        for round_number in ("first", "second", "third"):
            apply_event(
                store, event_type="note.added", task_id=None, data={"note": round_number}
            )
            self.assertIsNotNone(reconciliation_due(store.read()))
            apply_event(store, event_type="reconcile.completed", task_id=None, data={})
            self.assertIsNone(
                reconciliation_due(store.read()),
                f"reconciliation stayed due after the {round_number} checkpoint",
            )

    def test_a_task_can_be_restarted_after_unblocking(self) -> None:
        """Regression: the restart collided with the original start's key."""
        repo = self.repository()
        store = self.scope(repo)
        start_task(store, repo=repo, task_id="TASK-A")
        apply_event(
            store, event_type="task.blocked", task_id="TASK-A", data={"reason": "no key"}
        )
        apply_event(store, event_type="task.unblocked", task_id="TASK-A", data={})
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "todo")

        result, duplicate = start_task(store, repo=repo, task_id="TASK-A")
        self.assertFalse(duplicate)
        self.assertNotEqual(result["action"], "duplicate")
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "in_progress")


class ReviewPolicyTests(BaseTest):
    def gate_after_submit(self, policy: str, risk: str) -> str:
        state = ratified(new_state("SCOPE-x", base_ref="wdd/x", review_policy=policy))
        state["tasks"]["TASK-A"] = task_state("TASK-A", risk=risk)
        state = transition(state, "task.started", "TASK-A", {})
        state = transition(state, "task.pr_recorded", "TASK-A", {"pr": "p", "headSha": "h" * 40})
        return task_gate(state, state["tasks"]["TASK-A"])

    def test_risk_based_reviews_only_high_risk_tasks(self) -> None:
        self.assertEqual(self.gate_after_submit("risk_based", "normal"), "needs_verification")
        self.assertEqual(self.gate_after_submit("risk_based", "high"), "reviewing")

    def test_always_and_none_policies(self) -> None:
        self.assertEqual(self.gate_after_submit("always", "normal"), "reviewing")
        self.assertEqual(self.gate_after_submit("none", "high"), "needs_verification")

    def test_blocking_findings_hold_the_merge(self) -> None:
        repo = self.repository()
        plan = self.plan_document(scope={"reviewPolicy": "always"})
        store = self.scope(repo, plan)
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(result["worktree"], "src/schema.ts", "changed\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        record_review(
            store,
            task_id="TASK-A",
            findings=[{"severity": "P1", "summary": "unsafe"}],
            reviewer="rev",
            repo=repo,
        )
        state = store.read()
        self.assertEqual(task_gate(state, state["tasks"]["TASK-A"]), "needs_fixes")
        with self.assertRaises(IllegalTransition):
            merge_task(store, repo=repo, task_id="TASK-A")

    def test_clean_review_then_verification_reaches_merge_ready(self) -> None:
        repo = self.repository()
        store = self.scope(repo, self.plan_document(scope={"reviewPolicy": "always"}))
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(result["worktree"], "src/schema.ts", "changed\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        record_review(store, task_id="TASK-A", findings=[], reviewer="rev", repo=repo)
        record_verification(store, task_id="TASK-A", status="passed", command="pytest", repo=repo)
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "merge_ready")

    def test_raising_the_policy_mid_flight_does_not_wedge_the_gate(self) -> None:
        repo = self.repository()
        store = self.scope(repo)  # risk_based; TASK-A is normal risk, so no review
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(result["worktree"], "src/schema.ts", "x\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "in_progress")

        tightened = self.plan_document(scope={"reviewPolicy": "always"})
        apply_plan(store, validate_plan(tightened), repo=repo)
        state = store.read()
        self.assertEqual(task_gate(state, state["tasks"]["TASK-A"]), "needs_review")
        # The task sits in in_progress, not review; recording must still be legal.
        record_review(store, task_id="TASK-A", findings=[], reviewer="rev", repo=repo)
        record_verification(store, task_id="TASK-A", status="passed", repo=repo)
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "merge_ready")

    def test_review_requires_a_submitted_task(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        start_task(store, repo=repo, task_id="TASK-A")
        with self.assertRaises((IllegalTransition, ValidationError)):
            record_review(store, task_id="TASK-A", findings=[], reviewer="rev", repo=repo)

    def test_findings_must_carry_a_valid_severity(self) -> None:
        repo = self.repository()
        store = self.scope(repo, self.plan_document(scope={"reviewPolicy": "always"}))
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(result["worktree"], "src/schema.ts", "changed\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        with self.assertRaises(ValidationError):
            record_review(
                store,
                task_id="TASK-A",
                findings=[{"severity": "blocker", "summary": "x"}],
                reviewer="rev",
                repo=repo,
            )


class EvidenceTests(BaseTest):
    def test_a_new_head_invalidates_review_and_verification(self) -> None:
        repo = self.repository()
        store = self.scope(repo, self.plan_document(scope={"reviewPolicy": "always"}))
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(result["worktree"], "src/schema.ts", "one\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        record_review(store, task_id="TASK-A", findings=[], reviewer="rev", repo=repo)
        record_verification(store, task_id="TASK-A", status="passed", repo=repo)
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "merge_ready")

        self.commit_in(result["worktree"], "src/schema.ts", "two\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        task = store.read()["tasks"]["TASK-A"]
        self.assertIsNone(task["review"])
        self.assertIsNone(task["verification"])
        self.assertEqual(task["status"], "review")

    def test_evidence_shas_come_from_the_controller(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        head = self.commit_in(result["worktree"], "src/schema.ts", "changed\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        record_verification(store, task_id="TASK-A", status="passed", repo=repo)
        verification = store.read()["tasks"]["TASK-A"]["verification"]
        self.assertEqual(verification["headSha"], head)
        self.assertEqual(verification["baseSha"], self.git(repo, "rev-parse", "wdd/demo"))

    def test_submit_refuses_an_empty_branch(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        start_task(store, repo=repo, task_id="TASK-A")
        with self.assertRaises(IllegalTransition) as raised:
            submit_task(store, repo=repo, task_id="TASK-A")
        self.assertIn("nothing to submit", str(raised.exception))

    def test_submit_refuses_uncommitted_changes(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(result["worktree"], "src/schema.ts", "one\n")
        (Path(result["worktree"]) / "src" / "schema.ts").write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(IllegalTransition) as raised:
            submit_task(store, repo=repo, task_id="TASK-A")
        self.assertIn("uncommitted", str(raised.exception))

    def test_external_result_envelopes_round_trip(self) -> None:
        repo = self.repository()
        store = self.scope(repo, self.plan_document(scope={"reviewPolicy": "always"}))
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        head = self.commit_in(result["worktree"], "src/schema.ts", "changed\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        base = self.git(repo, "merge-base", "wdd/demo", head)

        review_file = self.root / "review.json"
        review_file.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": "wddctl_review_result",
                    "task": "TASK-A",
                    "baseSha": base,
                    "headSha": head,
                    "reviewer": "external",
                    "findings": [{"severity": "P3", "summary": "nit"}],
                }
            ),
            encoding="utf-8",
        )
        collect_review(store, task_id="TASK-A", result_paths=[review_file], repo=repo)

        verification_file = self.root / "verify.json"
        verification_file.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": "wddctl_verification_result",
                    "task": "TASK-A",
                    "baseSha": base,
                    "headSha": head,
                    "status": "passed",
                    "command": "pytest -q",
                }
            ),
            encoding="utf-8",
        )
        collect_verification(store, task_id="TASK-A", result_path=verification_file, repo=repo)
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "merge_ready")

    def test_a_wrong_envelope_is_rejected_with_a_useful_message(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"baseSha": "a", "headSha": "b", "findings": []}), encoding="utf-8")
        with self.assertRaises(ValidationError) as raised:
            collect_review(store, task_id="TASK-A", result_paths=[bad], repo=repo)
        self.assertIn("wddctl_review_result", str(raised.exception))


class MergeTests(BaseTest):
    def drive_to_merge_ready(self, store: StateStore, repo: Path, task_id: str, filename: str) -> str:
        result, _ = start_task(store, repo=repo, task_id=task_id)
        head = self.commit_in(result["worktree"], filename, f"{task_id}\n")
        submit_task(store, repo=repo, task_id=task_id)
        record_verification(store, task_id=task_id, status="passed", repo=repo)
        record_freshness(store, repo=repo, task_id=task_id)
        return head

    def test_merge_performs_the_merge_and_records_it(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        head = self.drive_to_merge_ready(store, repo, "TASK-A", "src/schema.ts")
        result = merge_task(store, repo=repo, task_id="TASK-A")
        self.assertEqual(result["action"], "merged")
        base = self.git(repo, "rev-parse", "wdd/demo")
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(repo), "merge-base", "--is-ancestor", head, base]
            ).returncode,
            0,
        )
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "done")

    def test_merge_refuses_before_the_gate_opens(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(result["worktree"], "src/schema.ts", "x\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        with self.assertRaises(IllegalTransition) as raised:
            merge_task(store, repo=repo, task_id="TASK-A")
        self.assertIn("needs_verification", str(raised.exception))

    def test_a_merged_task_frees_its_conflict_domain_immediately(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        self.drive_to_merge_ready(store, repo, "TASK-A", "src/schema.ts")
        self.assertEqual(admission_blocker(store.read(), "TASK-B")["code"], "conflict_domains")
        merge_task(store, repo=repo, task_id="TASK-A")
        self.assertIsNone(admission_blocker(store.read(), "TASK-B"))

    def test_independent_tasks_run_concurrently(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        start_task(store, repo=repo, task_id="TASK-A")
        start_task(store, repo=repo, task_id="TASK-C")
        statuses = {task_id: task["status"] for task_id, task in store.read()["tasks"].items()}
        self.assertEqual(statuses["TASK-A"], "in_progress")
        self.assertEqual(statuses["TASK-C"], "in_progress")

    def test_refresh_brings_a_stale_branch_forward(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        self.drive_to_merge_ready(store, repo, "TASK-A", "src/schema.ts")
        started, _ = start_task(store, repo=repo, task_id="TASK-C")
        self.commit_in(started["worktree"], "src/ui.ts", "ui change\n")
        submit_task(store, repo=repo, task_id="TASK-C")
        merge_task(store, repo=repo, task_id="TASK-A")

        refreshed = refresh_task(store, repo=repo, task_id="TASK-C")
        self.assertEqual(refreshed["action"], "refreshed")
        self.assertEqual(store.read()["tasks"]["TASK-C"]["headSha"], refreshed["headSha"])
        self.assertIsNone(store.read()["tasks"]["TASK-C"]["verification"])

    def test_refresh_is_a_no_op_when_already_current(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(result["worktree"], "src/schema.ts", "x\n")
        self.assertEqual(
            refresh_task(store, repo=repo, task_id="TASK-A")["action"], "already_current"
        )

    def test_refresh_adopts_a_conflict_resolved_by_hand(self) -> None:
        """Regression: a hand-resolved merge left the task pinned to a stale head."""
        repo = self.repository()
        store = self.scope(repo)
        started, _ = self.drive_to_merge_ready(store, repo, "TASK-A", "src/schema.ts"), None
        stale = store.read()["tasks"]["TASK-A"]["headSha"]

        # Someone finishes the merge in the worktree themselves.
        worktree = worktree_for(repo, "SCOPE-demo", "TASK-A")
        (worktree / "src" / "schema.ts").write_text("hand resolved\n", encoding="utf-8")
        self.git(worktree, "add", "-A")
        self.git(worktree, "commit", "-qm", "resolve by hand")
        moved = self.git(worktree, "rev-parse", "HEAD")
        self.assertNotEqual(moved, stale)

        result = refresh_task(store, repo=repo, task_id="TASK-A")
        self.assertEqual(result["action"], "adopted_external_merge")
        self.assertEqual(store.read()["tasks"]["TASK-A"]["headSha"], moved)
        # Evidence for the old head must not survive.
        self.assertIsNone(store.read()["tasks"]["TASK-A"]["verification"])

    def test_release_refuses_a_dirty_worktree(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        self.drive_to_merge_ready(store, repo, "TASK-A", "src/schema.ts")
        merge_task(store, repo=repo, task_id="TASK-A")
        worktree = worktree_for(repo, "SCOPE-demo", "TASK-A")
        (worktree / "stray.txt").write_text("unsaved\n", encoding="utf-8")
        with self.assertRaises(IllegalTransition):
            release_task(store, repo=repo, task_id="TASK-A")

    def test_release_removes_a_clean_worktree(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        self.drive_to_merge_ready(store, repo, "TASK-A", "src/schema.ts")
        merge_task(store, repo=repo, task_id="TASK-A")
        worktree = worktree_for(repo, "SCOPE-demo", "TASK-A")
        release_task(store, repo=repo, task_id="TASK-A")
        self.assertFalse(worktree.exists())

    def test_release_refuses_an_unfinished_task(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        start_task(store, repo=repo, task_id="TASK-A")
        with self.assertRaises(IllegalTransition):
            release_task(store, repo=repo, task_id="TASK-A")


class PortabilityTests(BaseTest):
    """Committed state must survive being cloned to a different directory."""

    def test_the_default_worktree_location_is_not_stored_at_all(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        started, _ = start_task(store, repo=repo, task_id="TASK-A")
        # Storing it would bake in this checkout's directory name.
        self.assertIsNone(store.read()["tasks"]["TASK-A"]["worktree"])
        self.assertEqual(
            Path(started["worktree"]), worktree_for(repo, "SCOPE-demo", "TASK-A")
        )
        self.assertNotIn(str(self.root), json.dumps(store.read()["tasks"]["TASK-A"]))

    def test_a_differently_named_clone_resolves_into_its_own_tree(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        start_task(store, repo=repo, task_id="TASK-A")

        clone = self.root / "elsewhere" / "renamed-checkout"
        clone.parent.mkdir(parents=True)
        resolved = worktree_for(
            clone, "SCOPE-demo", "TASK-A", store.read()["tasks"]["TASK-A"]["worktree"]
        )
        # Follows the checkout; never points back at the original directory.
        self.assertEqual(
            resolved, clone.parent / "renamed-checkout.wdd" / "worktrees" / "SCOPE-demo" / "TASK-A"
        )
        self.assertNotIn("proj.wdd", str(resolved))

    def test_an_explicit_worktree_override_is_stored_relative(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        custom = self.root / "custom-trees" / "TASK-A"
        started, _ = start_task(store, repo=repo, task_id="TASK-A", worktree=custom)
        stored = store.read()["tasks"]["TASK-A"]["worktree"]
        self.assertIsNotNone(stored)
        self.assertFalse(Path(stored).is_absolute(), f"{stored} must be relative")
        self.assertEqual(Path(started["worktree"]), custom.resolve())
        self.assertEqual(worktree_for(repo, "SCOPE-demo", "TASK-A", stored), custom.resolve())

    def test_start_reattaches_a_started_task_whose_worktree_is_gone(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        first, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(first["worktree"], "src/schema.ts", "work\n")

        # Simulate the handoff: the branch is in the repository, the worktree
        # that lived beside the previous checkout is not.
        self.git(repo, "worktree", "remove", "--force", first["worktree"])
        self.assertFalse(Path(first["worktree"]).exists())
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "in_progress")

        again, duplicate = start_task(store, repo=repo, task_id="TASK-A")
        self.assertFalse(duplicate)
        self.assertTrue(again["action"].startswith("reattach:"))
        self.assertEqual(again["status"], "in_progress")
        self.assertTrue(Path(again["worktree"]).exists())
        # The work is still there, and the task did not restart.
        self.assertEqual(
            (Path(again["worktree"]) / "src" / "schema.ts").read_text(encoding="utf-8"), "work\n"
        )
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "in_progress")

    def test_reattach_then_continue_to_merge(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        first, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(first["worktree"], "src/schema.ts", "work\n")
        self.git(repo, "worktree", "remove", "--force", first["worktree"])

        start_task(store, repo=repo, task_id="TASK-A")
        submit_task(store, repo=repo, task_id="TASK-A")
        record_verification(store, task_id="TASK-A", status="passed", repo=repo)
        record_freshness(store, repo=repo, task_id="TASK-A")
        merge_task(store, repo=repo, task_id="TASK-A")
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "done")

    def test_reattach_refuses_when_the_branch_is_missing(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        first, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.git(repo, "worktree", "remove", "--force", first["worktree"])
        self.git(repo, "branch", "-D", "task/TASK-A")
        with self.assertRaises(IllegalTransition) as raised:
            start_task(store, repo=repo, task_id="TASK-A")
        self.assertIn("fetch it", str(raised.exception))


class ReconcileTests(BaseTest):
    def test_a_queued_note_makes_reconciliation_due(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        self.assertIsNone(reconciliation_due(store.read()))
        apply_event(store, event_type="note.added", task_id="TASK-A", data={"note": "found a thing"})
        self.assertEqual(reconciliation_due(store.read())["code"], "pending_notes")

    def test_merge_count_makes_reconciliation_due(self) -> None:
        repo = self.repository()
        store = self.scope(repo, self.plan_document(scope={"reconcileEveryNMerges": 1}))
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(result["worktree"], "src/schema.ts", "x\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        record_verification(store, task_id="TASK-A", status="passed", repo=repo)
        record_freshness(store, repo=repo, task_id="TASK-A")
        merge_task(store, repo=repo, task_id="TASK-A")
        self.assertEqual(reconciliation_due(store.read())["code"], "merge_count")
        actions = [a["action"] for a in bounded_next_actions(store.read())["actions"]]
        self.assertIn("run_reconciliation", actions)

    def test_reconcile_done_clears_the_checkpoint(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        apply_event(store, event_type="note.added", task_id=None, data={"note": "x"})
        apply_event(store, event_type="reconcile.completed", task_id=None, data={})
        state = store.read()
        self.assertIsNone(reconciliation_due(state))
        self.assertEqual(state["reconcile"]["pendingNotes"], [])


class LifecycleTests(BaseTest):
    def test_execution_is_blocked_until_ratification(self) -> None:
        repo = self.repository()
        store = StateStore(repo / ".wdd" / "state.json")
        apply_plan(store, validate_plan(self.plan_document()), repo=repo)
        with self.assertRaises(IllegalTransition):
            start_task(store, repo=repo, task_id="TASK-A")
        self.assertEqual(
            bounded_next_actions(store.read())["blockers"][0]["code"], "constitution_unratified"
        )

    def test_block_and_unblock(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        start_task(store, repo=repo, task_id="TASK-A")
        apply_event(store, event_type="task.blocked", task_id="TASK-A", data={"reason": "needs API key"})
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "blocked")
        self.assertIsNone(admission_blocker(store.read(), "TASK-B"))
        apply_event(store, event_type="task.unblocked", task_id="TASK-A", data={})
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "todo")

    def test_status_and_render_projections(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        start_task(store, repo=repo, task_id="TASK-A")
        summary = status_summary(store.read())
        self.assertEqual(summary["scope"]["id"], "SCOPE-demo")
        self.assertEqual(summary["taskCounts"]["in_progress"], 1)
        rendered = render_controller_state(store.read())
        self.assertIn("TASK-A", rendered)
        self.assertIn("Do not edit", rendered)

    def test_next_emits_the_literal_command_for_each_action(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        actions = {a["task"]: a for a in bounded_next_actions(store.read())["actions"]}
        self.assertEqual(actions["TASK-A"]["action"], "start_task")
        self.assertEqual(
            actions["TASK-A"]["command"], "wddctl start --task TASK-A --repo ."
        )
        # start_task is a command to run now, so it carries no follow-up recording.
        self.assertNotIn("recordWith", actions["TASK-A"])

        start_task(store, repo=repo, task_id="TASK-A")
        awaiting = {a["task"]: a for a in bounded_next_actions(store.read())["actions"]}
        self.assertEqual(awaiting["TASK-A"]["action"], "await_worker")
        self.assertEqual(
            awaiting["TASK-A"]["recordWith"], "wddctl submit --task TASK-A --repo ."
        )
        # await_worker is a wait, so there is nothing to run right now.
        self.assertNotIn("command", awaiting["TASK-A"])

    def test_emitted_commands_echo_a_non_default_state_path(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        actions = bounded_next_actions(store.read(), state_path="custom/state.json")["actions"]
        self.assertTrue(
            actions[0]["command"].startswith("wddctl --state custom/state.json start")
        )

    def test_every_emitted_command_is_a_real_cli_invocation(self) -> None:
        """A command that does not parse would send the agent down a dead end."""
        from wave_delivery.engine import ACTION_COMMANDS
        from wave_delivery.cli import build_parser

        parser = build_parser()
        for action, (run_now, record_with) in ACTION_COMMANDS.items():
            for template in (run_now, record_with):
                if not template:
                    continue
                rendered = template.format(task="TASK-A", repo=".")
                # Split the way a shell would: quoted placeholders are one token.
                argv = shlex.split(rendered)
                try:
                    parser.parse_args(argv)
                except SystemExit:  # pragma: no cover - failure path
                    self.fail(f"{action} emits an unparseable command: {rendered}")

    def test_render_includes_the_commands(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        rendered = render_controller_state(store.read())
        self.assertIn("wddctl start --task TASK-A --repo .", rendered)

    def test_next_output_stays_within_its_byte_budget(self) -> None:
        state = ratified(new_state("SCOPE-big", base_ref="wdd/big"))
        for index in range(60):
            state["tasks"][f"TASK-{index:03d}"] = task_state(f"TASK-{index:03d}")
        result = bounded_next_actions(state, max_bytes=2048)
        self.assertLessEqual(
            len((json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8")), 2048
        )
        self.assertTrue(result["truncated"])

    def test_monitor_observes_without_writing_when_unchanged(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        start_task(store, repo=repo, task_id="TASK-A")
        first = monitor_once(store, repo=repo)
        self.assertTrue(first["changed"])
        self.assertFalse(monitor_once(store, repo=repo)["changed"])


class FreshnessTests(BaseTest):
    def test_classifications(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(result["worktree"], "src/schema.ts", "task change\n")
        self.assertEqual(
            check_freshness(repo, base_ref="wdd/demo", head_ref="task/TASK-A")["classification"],
            "current",
        )

        self.git(repo, "checkout", "-q", "wdd/demo")
        (repo / "docs.md").write_text("docs\n", encoding="utf-8")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "unrelated base change")
        self.assertEqual(
            check_freshness(repo, base_ref="wdd/demo", head_ref="task/TASK-A")["classification"],
            "nonmaterially_stale",
        )
        self.assertEqual(
            check_freshness(
                repo, base_ref="wdd/demo", head_ref="task/TASK-A", conflict_domains=["docs.md"]
            )["classification"],
            "materially_stale",
        )

    def test_merge_refuses_a_materially_stale_branch(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(result["worktree"], "src/schema.ts", "task change\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        record_verification(store, task_id="TASK-A", status="passed", repo=repo)
        record_freshness(store, repo=repo, task_id="TASK-A")

        self.git(repo, "checkout", "-q", "wdd/demo")
        (repo / "src" / "schema.ts").write_text("conflicting base change\n", encoding="utf-8")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "base touches the same file")
        with self.assertRaises(IllegalTransition) as raised:
            merge_task(store, repo=repo, task_id="TASK-A")
        self.assertIn("refresh", str(raised.exception))


class SchemaTests(BaseTest):
    def test_state_validation_rejects_bad_shapes(self) -> None:
        state = new_state("SCOPE-x")
        state["tasks"]["TASK-A"] = task_state("TASK-A", depends_on=["MISSING"])
        with self.assertRaises(ValidationError):
            validate_state(state)

    def test_state_validation_rejects_cycles(self) -> None:
        state = new_state("SCOPE-x")
        state["tasks"]["TASK-A"] = task_state("TASK-A", depends_on=["TASK-B"])
        state["tasks"]["TASK-B"] = task_state("TASK-B", depends_on=["TASK-A"])
        with self.assertRaises(ValidationError):
            validate_state(state)

    def test_review_policy_and_concurrency_are_validated(self) -> None:
        with self.assertRaises(ValidationError):
            new_state("SCOPE-x", review_policy="sometimes")
        with self.assertRaises(ValidationError):
            new_state("SCOPE-x", max_concurrent=0)

    def test_store_round_trips_atomically(self) -> None:
        store = StateStore(self.root / "nested" / "state.json")
        store.write(new_state("SCOPE-x"))
        self.assertEqual(store.read()["scope"]["id"], "SCOPE-x")


class ConstitutionTests(BaseTest):
    def test_probe_writes_a_proposal_and_reports_drift(self) -> None:
        repo = self.repository()
        proposal = probe_repository(repo)
        path = self.root / "proposal.json"
        write_proposal(path, proposal)
        self.assertEqual(read_proposal(path)["decisionFingerprint"], proposal["decisionFingerprint"])

        store = self.scope(repo)
        status = ratification_status(store.read(), proposal)
        self.assertEqual(status["status"], "ratified")

    def test_double_ratification_is_illegal(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        with self.assertRaises(IllegalTransition) as raised:
            apply_event(
                store,
                event_type="constitution.ratified",
                task_id=None,
                data={"by": "other", "decisionFingerprint": "sha256:y"},
            )
        self.assertIn("amend", str(raised.exception))

    def test_amendment_re_ratifies_and_records_the_prior_fingerprint(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        apply_event(
            store,
            event_type="constitution.amended",
            task_id=None,
            data={"by": "ivo", "decisionFingerprint": "sha256:v2"},
        )
        ratification = store.read()["constitution"]["ratification"]
        self.assertEqual(ratification["decisionFingerprint"], "sha256:v2")
        self.assertEqual(ratification["amendedFrom"], "sha256:x")
        self.assertEqual(store.read()["constitution"]["status"], "ratified")

    def test_a_no_op_amendment_is_rejected(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        with self.assertRaises(IllegalTransition):
            apply_event(
                store,
                event_type="constitution.amended",
                task_id=None,
                data={"by": "ivo", "decisionFingerprint": "sha256:x"},
            )

    def test_amendment_requires_prior_ratification(self) -> None:
        repo = self.repository()
        store = StateStore(repo / ".wdd" / "state.json")
        apply_plan(store, validate_plan(self.plan_document()), repo=repo)
        with self.assertRaises(IllegalTransition):
            apply_event(
                store,
                event_type="constitution.amended",
                task_id=None,
                data={"by": "ivo", "decisionFingerprint": "sha256:y"},
            )


class ReviewCommandTests(BaseTest):
    def test_run_review_freezes_the_shas(self) -> None:
        repo = self.repository()
        store = self.scope(repo, self.plan_document(scope={"reviewPolicy": "always"}))
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        head = self.commit_in(result["worktree"], "src/schema.ts", "changed\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        base = self.git(repo, "merge-base", "wdd/demo", head)

        script = self.root / "reviewer.py"
        script.write_text(
            "import json, os\n"
            "print(json.dumps({'schemaVersion': 1, 'kind': 'wddctl_review_result',\n"
            "  'task': os.environ['WDDCTL_REVIEW_TASK'],\n"
            "  'baseSha': os.environ['WDDCTL_REVIEW_BASE_SHA'],\n"
            "  'headSha': os.environ['WDDCTL_REVIEW_HEAD_SHA'],\n"
            "  'reviewer': 'scripted', 'findings': []}))\n",
            encoding="utf-8",
        )
        output = run_review(
            store,
            repo=repo,
            task_id="TASK-A",
            command=["python3", str(script)],
            output=self.root / "out.json",
        )
        self.assertEqual(output["headSha"], head)
        self.assertEqual(output["baseSha"], base)


class CliTests(BaseTest):
    def setUp(self) -> None:
        super().setUp()
        for redirect in (contextlib.redirect_stdout, contextlib.redirect_stderr):
            silence = redirect(io.StringIO())
            silence.__enter__()
            self.addCleanup(silence.__exit__, None, None, None)

    def test_cli_drives_a_task_end_to_end(self) -> None:
        repo = self.repository()
        plan_path = repo / ".wdd" / "plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(self.plan_document()), encoding="utf-8")
        state_path = repo / ".wdd" / "state.json"

        def run(*arguments: str) -> int:
            return main(["--state", str(state_path), *arguments])

        self.assertEqual(run("plan", "apply", "--plan", str(plan_path), "--repo", str(repo)), 0)
        self.assertEqual(
            run("constitution", "ratify", "--by", "tester", "--decision-fingerprint", "sha256:x"), 0
        )
        self.assertEqual(run("start", "--task", "TASK-A", "--repo", str(repo)), 0)
        worktree = worktree_for(repo, "SCOPE-demo", "TASK-A")
        self.commit_in(worktree, "src/schema.ts", "cli change\n")
        self.assertEqual(run("submit", "--task", "TASK-A", "--repo", str(repo)), 0)
        self.assertEqual(
            run(
                "verify",
                "record",
                "--task",
                "TASK-A",
                "--status",
                "passed",
                "--command",
                "pytest",
                "--repo",
                str(repo),
            ),
            0,
        )
        self.assertEqual(run("freshness", "record", "--task", "TASK-A", "--repo", str(repo)), 0)
        self.assertEqual(run("merge", "--task", "TASK-A", "--repo", str(repo)), 0)
        self.assertEqual(StateStore(state_path).read()["tasks"]["TASK-A"]["status"], "done")

    def test_verify_command_flag_does_not_shadow_the_subcommand(self) -> None:
        parser_args = main
        repo = self.repository()
        store = self.scope(repo)
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(result["worktree"], "src/schema.ts", "x\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        code = parser_args(
            [
                "--state",
                str(repo / ".wdd" / "state.json"),
                "verify",
                "record",
                "--task",
                "TASK-A",
                "--status",
                "passed",
                "--command",
                "npm test",
                "--repo",
                str(repo),
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            store.read()["tasks"]["TASK-A"]["verification"]["command"], "npm test"
        )

    def test_a_merge_cannot_be_asserted_through_the_escape_hatch(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        result, _ = start_task(store, repo=repo, task_id="TASK-A")
        self.commit_in(result["worktree"], "src/schema.ts", "x\n")
        submit_task(store, repo=repo, task_id="TASK-A")
        code = main(
            [
                "--state",
                str(repo / ".wdd" / "state.json"),
                "event",
                "apply",
                "--event",
                "task.merged",
                "--task",
                "TASK-A",
                "--data",
                json.dumps(
                    {
                        "mergeVerified": True,
                        "baseRef": "wdd/demo",
                        "baseSha": "f" * 40,
                        "headSha": store.read()["tasks"]["TASK-A"]["headSha"],
                    }
                ),
            ]
        )
        self.assertNotEqual(code, 0)
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "in_progress")

    def test_doctor_reports_capabilities(self) -> None:
        capabilities = inspect_capabilities()
        self.assertIn("coreController", capabilities["capabilities"])
        self.assertTrue(capabilities["commands"]["git"])


if __name__ == "__main__":
    unittest.main()
