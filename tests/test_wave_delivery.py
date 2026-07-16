from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from wave_delivery.constitution import (
    probe_repository,
    ratification_status,
    read_proposal,
    write_proposal,
)
from wave_delivery.doctor import inspect_capabilities
from wave_delivery.engine import (
    apply_event,
    bounded_next_actions,
    render_controller_state,
    status_summary,
)
from wave_delivery.errors import IllegalTransition, RevisionConflict, ValidationError
from wave_delivery.freshness import check_freshness
from wave_delivery.leases import ensure_lease, release_lease
from wave_delivery.migration import apply_migration, build_migration_plan, rollback_migration
from wave_delivery.monitor import monitor_once
from wave_delivery.review import collect_review, collect_verification, run_review
from wave_delivery.schema import new_state, task_state
from wave_delivery.store import StateStore


class WaveDeliveryStateTests(unittest.TestCase):
    def git(self, repo: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def git_output(self, repo: Path, *arguments: str) -> str:
        return subprocess.check_output(["git", "-C", str(repo), *arguments], text=True).strip()

    def git_repository(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        self.git(repo, "init", "-b", "main")
        self.git(repo, "config", "user.email", "wdctl@example.test")
        self.git(repo, "config", "user.name", "wdctl test")
        (repo / "README.md").write_text("initial\n", encoding="utf-8")
        self.git(repo, "add", "README.md")
        self.git(repo, "commit", "-m", "initial")
        return repo

    def make_store(self) -> tuple[tempfile.TemporaryDirectory[str], StateStore]:
        directory = tempfile.TemporaryDirectory()
        store = StateStore(Path(directory.name) / "orchestration.json")
        state = new_state("EPIC-controller")
        state["tasks"]["TASK-001"] = task_state("TASK-001")
        store.write(state)
        return directory, store

    def apply(
        self,
        store: StateStore,
        event_type: str,
        revision: int,
        *,
        task: str | None = "TASK-001",
        data: dict | None = None,
        key: str | None = None,
    ) -> dict:
        state, duplicate = apply_event(
            store,
            event_type=event_type,
            task_id=task,
            data=data or {},
            idempotency_key=key or f"key-{revision}-{event_type}",
            expected_revision=revision,
        )
        self.assertFalse(duplicate)
        return state

    def ratify(self, store: StateStore, revision: int = 0) -> dict:
        return self.apply(
            store,
            "constitution.ratified",
            revision,
            task=None,
            data={"by": "Ivo", "decisionFingerprint": "sha256:test"},
        )

    def test_execution_requires_explicit_ratification(self):
        directory, store = self.make_store()
        self.addCleanup(directory.cleanup)
        with self.assertRaises(IllegalTransition):
            self.apply(store, "task.started", 0)
        self.assertEqual(store.read()["revision"], 0)

    def test_events_are_revisioned_and_idempotent(self):
        directory, store = self.make_store()
        self.addCleanup(directory.cleanup)
        self.ratify(store)
        state = self.apply(store, "task.started", 1, key="start-task")
        self.assertEqual(state["revision"], 2)
        retried, duplicate = apply_event(
            store,
            event_type="task.started",
            task_id="TASK-001",
            data={},
            idempotency_key="start-task",
            expected_revision=1,
        )
        self.assertTrue(duplicate)
        self.assertEqual(retried["revision"], 2)
        with self.assertRaises(RevisionConflict):
            self.apply(store, "task.blocked", 1, data={"reason": "stale"})
        blocked = self.apply(store, "task.blocked", 2, data={"reason": "paused"})
        self.assertEqual(blocked["tasks"]["TASK-001"]["status"], "blocked")

    def test_sha_bound_review_and_verification_are_invalidated(self):
        directory, store = self.make_store()
        self.addCleanup(directory.cleanup)
        self.ratify(store)
        self.apply(store, "task.started", 1)
        self.apply(
            store,
            "task.pr_recorded",
            2,
            data={"pr": "https://example.test/pr/1", "headSha": "a"},
        )
        self.apply(
            store,
            "review.recorded",
            3,
            data={"baseSha": "root", "headSha": "a", "findings": [], "reviewer": "reviewer-a"},
        )
        complete = self.apply(
            store,
            "verification.recorded",
            4,
            data={"baseSha": "root", "headSha": "a", "status": "passed", "command": "python -m unittest"},
        )
        self.assertEqual(complete["tasks"]["TASK-001"]["status"], "merge_ready")
        invalidated = self.apply(
            store,
            "task.head_updated",
            5,
            data={"headSha": "b"},
        )
        task = invalidated["tasks"]["TASK-001"]
        self.assertEqual(task["status"], "in_progress")
        self.assertIsNone(task["review"])
        self.assertIsNone(task["verification"])

    def test_next_respects_dependencies_and_conflict_domains(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = StateStore(Path(directory.name) / "state.json")
        state = new_state("EPIC-next")
        state["constitution"] = {
            "status": "ratified",
            "ratification": {"by": "Ivo", "decisionFingerprint": "sha256:test"},
        }
        state["tasks"] = {
            "TASK-001": task_state("TASK-001", conflict_domains=["src/shared.py"]),
            "TASK-002": task_state("TASK-002", depends_on=["TASK-001"]),
            "TASK-003": task_state("TASK-003", conflict_domains=["src/shared.py"]),
        }
        store.write(state)
        next_step = bounded_next_actions(store.read())
        self.assertEqual(next_step["actions"], [{"task": "TASK-001", "action": "start_task"}])
        self.assertTrue(any(item.get("task") == "TASK-002" for item in next_step["blockers"]))
        self.assertTrue(any(item.get("task") == "TASK-003" for item in next_step["blockers"]))

    def test_status_and_render_are_deterministic(self):
        directory, store = self.make_store()
        self.addCleanup(directory.cleanup)
        summary = status_summary(store.read())
        self.assertEqual(summary["taskCounts"]["todo"], 1)
        first = render_controller_state(store.read())
        second = render_controller_state(store.read())
        self.assertEqual(first, second)
        self.assertIn("Generated by wdctl", first)

    def test_next_output_stays_within_the_default_prompt_budget(self):
        state = new_state("EPIC-bounded")
        state["constitution"] = {
            "status": "ratified",
            "ratification": {"by": "Ivo", "decisionFingerprint": "sha256:test"},
        }
        for number in range(40):
            task_id = f"TASK-{number:03d}"
            task = task_state(task_id)
            task["status"] = "blocked"
            task["blocker"] = "A deliberately long blocker for bounded next output. " * 4
            state["tasks"][task_id] = task
        payload = bounded_next_actions(state)
        self.assertLessEqual(
            len(json.dumps(payload, separators=(",", ":")).encode("utf-8")), 2048
        )
        self.assertTrue(payload["truncated"])

    def test_state_file_remains_json_after_event_application(self):
        directory, store = self.make_store()
        self.addCleanup(directory.cleanup)
        self.ratify(store)
        parsed = json.loads(store.path.read_text(encoding="utf-8"))
        self.assertEqual(parsed["revision"], 1)
        self.assertFalse(store.lock_path.exists())

    def test_epic_migration_moves_tasks_to_stable_paths_and_rolls_back(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        state_path = root / "orchestration.json"
        old_task = root / "TICKET-001" / "todo" / "TASK-001.md"
        old_task.parent.mkdir(parents=True)
        old_task.write_text("# Original task\n", encoding="utf-8")
        legacy = {
            "schemaVersion": 1,
            "epic": {"id": "EPIC-migrate"},
            "waves": [
                {
                    "id": "WAVE-001",
                    "status": "planned",
                    "tasks": [
                        {
                            "id": "TASK-001",
                            "path": "TICKET-001/todo/TASK-001.md",
                            "status": "todo",
                            "dependsOn": [],
                            "conflictDomains": ["src/example.py"],
                            "branch": "task/TASK-001",
                            "workerWorktree": None,
                            "latestCommit": None,
                            "pr": None,
                            "verification": None,
                        }
                    ],
                }
            ],
            "monitoring": {"mode": "manual", "status": "inactive"},
        }
        state_path.write_text(json.dumps(legacy, indent=2) + "\n", encoding="utf-8")

        plan = build_migration_plan(state_path)
        self.assertEqual(plan["scope"], {"id": "EPIC-migrate", "kind": "epic"})
        self.assertEqual(plan["moves"][0]["target"], "TICKET-001/tasks/TASK-001.md")
        self.assertEqual(plan["targetState"]["constitution"]["status"], "draft")

        result = apply_migration(plan)
        migrated = StateStore(state_path).read()
        self.assertEqual(migrated["schemaVersion"], 2)
        self.assertEqual(migrated["tasks"]["TASK-001"]["specPath"], "TICKET-001/tasks/TASK-001.md")
        self.assertFalse(old_task.exists())
        self.assertTrue((root / "TICKET-001" / "tasks" / "TASK-001.md").exists())
        self.assertTrue(Path(result["backupDirectory"]).is_dir())

        rollback_migration(result["backupDirectory"])
        restored = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(restored, legacy)
        self.assertTrue(old_task.exists())

    def test_micro_wave_migration_keeps_existing_task_paths(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state_path = Path(directory.name) / "state.json"
        legacy = {
            "schemaVersion": 1,
            "kind": "micro_wave_state",
            "work": {"id": "WORK-migrate"},
            "tasks": [
                {
                    "id": "TASK-001",
                    "path": "tasks/TASK-001.md",
                    "status": "todo",
                }
            ],
        }
        state_path.write_text(json.dumps(legacy), encoding="utf-8")
        plan = build_migration_plan(state_path)
        self.assertEqual(plan["scope"]["kind"], "micro_wave")
        self.assertEqual(plan["moves"], [])
        self.assertEqual(plan["targetState"]["tasks"]["TASK-001"]["specPath"], "tasks/TASK-001.md")

    def test_rollback_refuses_to_overwrite_a_migrated_task_change(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        state_path = root / "orchestration.json"
        old_task = root / "TICKET-001" / "todo" / "TASK-001.md"
        old_task.parent.mkdir(parents=True)
        old_task.write_text("# Original task\n", encoding="utf-8")
        state_path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "epic": {"id": "EPIC-safe-rollback"},
                    "waves": [
                        {
                            "id": "WAVE-001",
                            "tasks": [
                                {
                                    "id": "TASK-001",
                                    "path": "TICKET-001/todo/TASK-001.md",
                                    "status": "todo",
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = apply_migration(build_migration_plan(state_path))
        (root / "TICKET-001" / "tasks" / "TASK-001.md").write_text(
            "# Changed after migration\n", encoding="utf-8"
        )
        with self.assertRaises(ValidationError):
            rollback_migration(result["backupDirectory"])

    def test_constitution_probe_has_stable_fingerprint_and_detects_drift(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "tests").mkdir()
        (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
        proposal = probe_repository(root)
        proposal_path = root / "proposal.json"
        write_proposal(proposal_path, proposal)
        loaded = read_proposal(proposal_path)
        self.assertEqual(loaded["decisionFingerprint"], proposal["decisionFingerprint"])
        state = new_state("EPIC-constitution")
        state["constitution"] = {
            "status": "ratified",
            "ratification": {"by": "Ivo", "decisionFingerprint": proposal["decisionFingerprint"]},
        }
        self.assertFalse(ratification_status(state, loaded)["stale"])
        loaded["decisions"]["profileDefault"] = "full"
        loaded["decisionFingerprint"] = "sha256:changed"
        self.assertTrue(ratification_status(state, loaded)["stale"])

    def test_lease_ensure_and_release_manage_an_isolated_worktree(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        repo = self.git_repository(root)
        store = StateStore(root / "state.json")
        state = new_state("EPIC-lease")
        state["constitution"] = {
            "status": "ratified",
            "ratification": {"by": "Ivo", "decisionFingerprint": "sha256:test"},
        }
        state["tasks"]["TASK-001"] = task_state("TASK-001")
        store.write(state)
        worktree = root / "worker"

        ensured, duplicate = ensure_lease(
            store,
            repo=repo,
            task_id="TASK-001",
            branch="task/TASK-001",
            worktree=worktree,
            base_ref="main",
            idempotency_key="lease-ensure",
            expected_revision=0,
        )
        self.assertFalse(duplicate)
        self.assertTrue(worktree.is_dir())
        self.assertEqual(ensured["revision"], 1)
        self.assertEqual(store.read()["leases"]["TASK-001"]["status"], "active")
        retried, duplicate = ensure_lease(
            store,
            repo=repo,
            task_id="TASK-001",
            branch="task/TASK-001",
            worktree=worktree,
            base_ref="main",
            idempotency_key="lease-ensure",
            expected_revision=0,
        )
        self.assertTrue(duplicate)
        self.assertEqual(retried["revision"], 1)

        released, duplicate = release_lease(
            store,
            repo=repo,
            task_id="TASK-001",
            idempotency_key="lease-release",
            expected_revision=1,
        )
        self.assertFalse(duplicate)
        self.assertEqual(released["cleanup"], "cleaned_up")
        self.assertFalse(worktree.exists())
        self.assertEqual(store.read()["leases"]["TASK-001"]["status"], "released")

    def test_freshness_is_risk_based_and_enforced_before_merge(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        repo = self.git_repository(root)
        self.git(repo, "checkout", "-b", "task/TASK-001")
        (repo / "docs.md").write_text("task change\n", encoding="utf-8")
        self.git(repo, "add", "docs.md")
        self.git(repo, "commit", "-m", "task change")
        self.git(repo, "checkout", "main")
        (repo / "src.py").write_text("base change\n", encoding="utf-8")
        self.git(repo, "add", "src.py")
        self.git(repo, "commit", "-m", "base change")

        nonmaterial = check_freshness(
            repo, base_ref="main", head_ref="task/TASK-001", conflict_domains=[]
        )
        self.assertEqual(nonmaterial["classification"], "nonmaterially_stale")
        material = check_freshness(
            repo,
            base_ref="main",
            head_ref="task/TASK-001",
            conflict_domains=["src/**"],
        )
        self.assertEqual(material["classification"], "materially_stale")

        store = StateStore(root / "state.json")
        state = new_state("EPIC-freshness")
        state["constitution"] = {
            "status": "ratified",
            "ratification": {"by": "Ivo", "decisionFingerprint": "sha256:test"},
        }
        task = task_state("TASK-001")
        task["status"] = "merge_ready"
        task["headSha"] = nonmaterial["headSha"]
        state["tasks"]["TASK-001"] = task
        store.write(state)
        recorded = self.apply(
            store,
            "freshness.recorded",
            0,
            data=nonmaterial,
        )
        self.assertEqual(recorded["tasks"]["TASK-001"]["freshness"]["classification"], "nonmaterially_stale")
        merged = self.apply(store, "task.merged", 1, data={})
        self.assertEqual(merged["tasks"]["TASK-001"]["status"], "done")

    def test_monitor_writes_only_when_git_observations_change(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        repo = self.git_repository(root)
        store = StateStore(root / "state.json")
        state = new_state("EPIC-monitor")
        task = task_state("TASK-001")
        task["branch"] = "main"
        task["headSha"] = self.git_output(repo, "rev-parse", "main")
        state["tasks"]["TASK-001"] = task
        store.write(state)

        initial = monitor_once(store, repo=str(repo))
        self.assertTrue(initial["changed"])
        self.assertEqual(initial["revision"], 1)
        unchanged = monitor_once(store, repo=str(repo))
        self.assertFalse(unchanged["changed"])
        self.assertEqual(unchanged["revision"], 1)

        (repo / "README.md").write_text("changed\n", encoding="utf-8")
        self.git(repo, "add", "README.md")
        self.git(repo, "commit", "-m", "branch advanced")
        advanced = monitor_once(store, repo=str(repo))
        self.assertTrue(advanced["changed"])
        self.assertEqual(advanced["revision"], 2)
        self.assertIn(
            {"task": "TASK-001", "action": "record_head_change"}, advanced["actions"]
        )

    def test_review_and_verification_collection_are_sha_bound(self):
        directory, store = self.make_store()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.ratify(store)
        self.apply(store, "task.started", 1)
        self.apply(
            store,
            "task.pr_recorded",
            2,
            data={"pr": "https://example.test/pr/1", "headSha": "head-a"},
        )
        review_a = root / "review-a.json"
        review_b = root / "review-b.json"
        review_a.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": "wdctl_review_result",
                    "task": "TASK-001",
                    "baseSha": "base-a",
                    "headSha": "head-a",
                    "reviewer": "reviewer-a",
                    "findings": [],
                }
            ),
            encoding="utf-8",
        )
        review_b.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": "wdctl_review_result",
                    "task": "TASK-001",
                    "baseSha": "base-a",
                    "headSha": "head-a",
                    "reviewer": "reviewer-b",
                    "findings": [{"severity": "P3", "summary": "Document this later"}],
                }
            ),
            encoding="utf-8",
        )
        reviewed, duplicate = collect_review(
            store,
            task_id="TASK-001",
            result_paths=[review_a, review_b],
            idempotency_key="review-aggregate",
            expected_revision=3,
        )
        self.assertFalse(duplicate)
        self.assertEqual(reviewed["tasks"]["TASK-001"]["review"]["baseSha"], "base-a")
        verification = root / "verification.json"
        verification.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": "wdctl_verification_result",
                    "task": "TASK-001",
                    "baseSha": "base-a",
                    "headSha": "head-a",
                    "status": "passed",
                    "command": "python3 -m unittest",
                }
            ),
            encoding="utf-8",
        )
        verified, duplicate = collect_verification(
            store,
            task_id="TASK-001",
            result_path=verification,
            idempotency_key="verification-1",
            expected_revision=4,
        )
        self.assertFalse(duplicate)
        self.assertEqual(verified["tasks"]["TASK-001"]["status"], "merge_ready")

    def test_review_run_validates_its_frozen_sha_output(self):
        directory, store = self.make_store()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        state = store.read()
        state["constitution"] = {
            "status": "ratified",
            "ratification": {"by": "Ivo", "decisionFingerprint": "sha256:test"},
        }
        state["tasks"]["TASK-001"]["status"] = "review"
        state["tasks"]["TASK-001"]["headSha"] = "head-a"
        store.write(state)
        output = root / "review.json"
        command = [
            sys.executable,
            "-c",
            "import json; print(json.dumps({'schemaVersion': 1, 'kind': 'wdctl_review_result', 'task': 'TASK-001', 'baseSha': 'base-a', 'headSha': 'head-a', 'reviewer': 'test', 'findings': []}))",
        ]
        result = run_review(
            store,
            repo=root,
            task_id="TASK-001",
            command=command,
            output=output,
            base_sha="base-a",
        )
        self.assertTrue(output.exists())
        self.assertEqual(result["headSha"], "head-a")

    def test_doctor_reports_core_and_optional_capabilities(self):
        doctor = inspect_capabilities()
        self.assertTrue(doctor["python"]["supported"])
        self.assertTrue(doctor["capabilities"]["coreController"])
        self.assertIn("git", doctor["commands"])

    def test_installer_creates_working_posix_and_windows_launchers(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        prefix = Path(directory.name) / "install"
        script = Path(__file__).resolve().parents[1] / "scripts" / "install_wave_delivery.py"
        subprocess.run(
            [sys.executable, str(script), "--prefix", str(prefix)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        launcher = prefix / "bin" / "wdctl"
        self.assertTrue(launcher.exists())
        self.assertTrue((prefix / "bin" / "wdctl.cmd").exists())
        help_output = subprocess.check_output([str(launcher), "--help"], text=True)
        self.assertIn("monitor", help_output)


if __name__ == "__main__":
    unittest.main()
