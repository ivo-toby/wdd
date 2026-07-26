from __future__ import annotations

import contextlib
import io
import json
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
    admission_schedule,
    apply_event,
    bounded_next_actions,
    derive_idempotency_key,
    reconciliation_due,
    render_controller_state,
    status_summary,
    task_gate,
    transition,
)
from wave_delivery.errors import IllegalTransition, RevisionConflict, ValidationError
from wave_delivery.freshness import check_freshness, record_freshness
from wave_delivery.leases import release_task, start_task, submit_task
from wave_delivery.merge import merge_task, refresh_task
from wave_delivery.monitor import monitor_once
from wave_delivery.plan import apply_plan, read_plan, state_from_plan, validate_plan
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

    def test_a_retried_identical_call_is_a_no_op(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        apply_event(store, event_type="note.added", task_id=None, data={"note": "same"})
        revision = store.read()["revision"]
        _, duplicate = apply_event(
            store, event_type="note.added", task_id=None, data={"note": "same"}
        )
        self.assertTrue(duplicate)
        self.assertEqual(store.read()["revision"], revision)

    def test_distinct_payloads_are_not_collapsed(self) -> None:
        self.assertNotEqual(
            derive_idempotency_key("note.added", None, {"note": "a"}),
            derive_idempotency_key("note.added", None, {"note": "b"}),
        )


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
        collect_review(store, task_id="TASK-A", result_paths=[review_file])

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
        collect_verification(store, task_id="TASK-A", result_path=verification_file)
        self.assertEqual(store.read()["tasks"]["TASK-A"]["status"], "merge_ready")

    def test_a_wrong_envelope_is_rejected_with_a_useful_message(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"baseSha": "a", "headSha": "b", "findings": []}), encoding="utf-8")
        with self.assertRaises(ValidationError) as raised:
            collect_review(store, task_id="TASK-A", result_paths=[bad])
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

    def test_release_refuses_a_dirty_worktree(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        self.drive_to_merge_ready(store, repo, "TASK-A", "src/schema.ts")
        merge_task(store, repo=repo, task_id="TASK-A")
        worktree = Path(store.read()["tasks"]["TASK-A"]["worktree"])
        (worktree / "stray.txt").write_text("unsaved\n", encoding="utf-8")
        with self.assertRaises(IllegalTransition):
            release_task(store, repo=repo, task_id="TASK-A")

    def test_release_removes_a_clean_worktree(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        self.drive_to_merge_ready(store, repo, "TASK-A", "src/schema.ts")
        merge_task(store, repo=repo, task_id="TASK-A")
        worktree = Path(store.read()["tasks"]["TASK-A"]["worktree"])
        release_task(store, repo=repo, task_id="TASK-A")
        self.assertFalse(worktree.exists())

    def test_release_refuses_an_unfinished_task(self) -> None:
        repo = self.repository()
        store = self.scope(repo)
        start_task(store, repo=repo, task_id="TASK-A")
        with self.assertRaises(IllegalTransition):
            release_task(store, repo=repo, task_id="TASK-A")


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
        worktree = StateStore(state_path).read()["tasks"]["TASK-A"]["worktree"]
        self.commit_in(worktree, "src/schema.ts", "cli change\n")
        self.assertEqual(run("submit", "--task", "TASK-A", "--repo", str(repo)), 0)
        self.assertEqual(
            run("verify", "record", "--task", "TASK-A", "--status", "passed", "--command", "pytest"),
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
