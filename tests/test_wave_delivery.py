from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wave_delivery.engine import (
    apply_event,
    bounded_next_actions,
    render_controller_state,
    status_summary,
)
from wave_delivery.errors import IllegalTransition, RevisionConflict
from wave_delivery.schema import new_state, task_state
from wave_delivery.store import StateStore


class WaveDeliveryStateTests(unittest.TestCase):
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
            data={"headSha": "a", "findings": [], "reviewer": "reviewer-a"},
        )
        complete = self.apply(
            store,
            "verification.recorded",
            4,
            data={"headSha": "a", "status": "passed", "command": "python -m unittest"},
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


if __name__ == "__main__":
    unittest.main()
