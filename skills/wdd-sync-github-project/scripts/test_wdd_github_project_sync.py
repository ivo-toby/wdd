#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import wdd_github_project_sync as sync


def remote_snapshot() -> dict:
    return {
        "project": {
            "owner": "ivo-toby",
            "number": 4,
            "id": "PVT_kwExample",
            "title": "GitHub Import",
            "url": "https://github.com/orgs/ivo-toby/projects/4",
            "repo": "ivo-toby/example",
            "wdd_id": "EPIC-github-import",
        },
        "items": [
            {
                "kind": "ticket",
                "wdd_id": "TICKET-001-runtime-smoke",
                "item_id": "PVTI_ticket",
                "issue_number": 7,
                "url": "https://github.com/ivo-toby/example/issues/7",
                "title": "Runtime smoke harness",
                "body": "Build a durable smoke harness.",
                "status": "Todo",
                "state": "OPEN",
                "updated_at": "2026-06-10T12:00:00Z",
            },
            {
                "kind": "task",
                "wdd_id": "TASK-001-seeded-smoke",
                "ticket": "TICKET-001-runtime-smoke",
                "wave": "WAVE-001",
                "item_id": "PVTI_task",
                "issue_number": 8,
                "url": "https://github.com/ivo-toby/example/issues/8",
                "title": "Seed smoke fixture",
                "body": "Create deterministic fixture data.",
                "status": "In Progress",
                "state": "OPEN",
                "updated_at": "2026-06-10T12:05:00Z",
            },
        ],
    }


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class WddGithubProjectSyncTests(unittest.TestCase):
    def test_pull_plan_creates_missing_local_epic_ticket_and_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = sync.plan_sync(root, remote_snapshot(), mode="pull")

            actions = [operation["action"] for operation in plan["operations"]]

            self.assertEqual(
                actions,
                [
                    "create_local_epic",
                    "create_local_ticket",
                    "create_local_task",
                    "write_manifest",
                ],
            )
            self.assertEqual(plan["epic_id"], "EPIC-github-import")
            self.assertEqual(plan["conflicts"], [])

    def test_apply_local_pull_writes_wdd_artifacts_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = sync.plan_sync(root, remote_snapshot(), mode="pull")

            sync.apply_local_operations(root, plan)

            epic_dir = root / ".wdd" / "epics" / "EPIC-github-import"
            self.assertTrue((epic_dir / "epic.md").exists())
            self.assertTrue(
                (
                    epic_dir
                    / "TICKET-001-runtime-smoke"
                    / "ticket.md"
                ).exists()
            )
            self.assertTrue(
                (
                    epic_dir
                    / "TICKET-001-runtime-smoke"
                    / "in-progress"
                    / "TASK-001-seeded-smoke.md"
                ).exists()
            )
            manifest = json.loads(
                (epic_dir / "adapters" / "github-project.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["schemaVersion"], 1)
            self.assertEqual(
                manifest["items"]["TASK-001-seeded-smoke"]["github"]["issueNumber"],
                8,
            )

    def test_push_plan_creates_remote_issue_ops_for_unlinked_local_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epic_dir = root / ".wdd" / "epics" / "EPIC-local-only"
            write_text(
                epic_dir / "epic.md",
                """---
id: EPIC-local-only
kind: epic
slug: local-only
title: Local Only
status: planned
target_branch: main
epic_branch: epic/local-only
adapter_links:
  github_issue: null
---

# Local Only

## Summary

Local epic.
""",
            )
            write_text(
                epic_dir / "TICKET-001-api" / "ticket.md",
                """---
id: TICKET-001-api
kind: ticket
epic: EPIC-local-only
slug: api
title: API Ticket
status: planned
adapter_links:
  github_issue: null
---

# API Ticket

## Summary

Create the API slice.
""",
            )
            write_text(
                epic_dir / "TICKET-001-api" / "todo" / "TASK-001-api-contract.md",
                """---
id: TASK-001-api-contract
kind: task
epic: EPIC-local-only
ticket: TICKET-001-api
wave: WAVE-001
slug: api-contract
title: API Contract
status: todo
adapter_links:
  github_issue: null
---

# TASK-001-api-contract: API Contract

## Objective

Define the API contract.
""",
            )

            plan = sync.plan_sync(
                root,
                {
                    "project": {
                        "owner": "ivo-toby",
                        "number": 4,
                        "id": "PVT_kwExample",
                        "repo": "ivo-toby/example",
                        "wdd_id": "EPIC-local-only",
                    },
                    "items": [],
                },
                mode="push",
                epic_id="EPIC-local-only",
            )

            actions = [operation["action"] for operation in plan["operations"]]

            self.assertIn("create_remote_issue", actions)
            self.assertIn("add_issue_to_project", actions)
            self.assertIn("update_project_fields", actions)
            self.assertEqual(plan["conflicts"], [])

    def test_sync_reports_conflict_when_local_and_remote_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epic_dir = root / ".wdd" / "epics" / "EPIC-github-import"
            write_text(
                epic_dir / "epic.md",
                """---
id: EPIC-github-import
kind: epic
slug: github-import
title: GitHub Import
status: planned
target_branch: main
epic_branch: epic/github-import
---

# GitHub Import
""",
            )
            ticket_path = epic_dir / "TICKET-001-runtime-smoke" / "ticket.md"
            old_ticket = """---
id: TICKET-001-runtime-smoke
kind: ticket
epic: EPIC-github-import
title: Runtime smoke harness
status: planned
---

# Runtime smoke harness

## Summary

Old synced summary.
"""
            write_text(ticket_path, old_ticket.replace("Old", "Locally changed"))
            old_local_hash = sync.fingerprint_text(old_ticket)
            old_remote_hash = sync.fingerprint_remote_item(
                remote_snapshot()["items"][0]
            )
            manifest = {
                "schemaVersion": 1,
                "epic": {"id": "EPIC-github-import"},
                "project": remote_snapshot()["project"],
                "items": {
                    "TICKET-001-runtime-smoke": {
                        "kind": "ticket",
                        "localPath": "TICKET-001-runtime-smoke/ticket.md",
                        "github": {
                            "itemId": "PVTI_ticket",
                            "issueNumber": 7,
                            "url": "https://github.com/ivo-toby/example/issues/7",
                        },
                        "fingerprints": {
                            "local": old_local_hash,
                            "remote": old_remote_hash,
                        },
                    }
                },
            }
            write_text(
                epic_dir / "adapters" / "github-project.json",
                json.dumps(manifest, indent=2),
            )
            changed_remote = remote_snapshot()
            changed_remote["items"][0]["body"] = "Remote changed the ticket body."

            plan = sync.plan_sync(root, changed_remote, mode="sync")

            self.assertEqual(len(plan["conflicts"]), 1)
            self.assertEqual(
                plan["conflicts"][0]["wdd_id"], "TICKET-001-runtime-smoke"
            )
            self.assertEqual(plan["operations"], [])

    def test_apply_local_status_update_moves_task_between_kanban_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initial_snapshot = remote_snapshot()
            initial_snapshot["items"][1]["status"] = "Todo"
            initial_plan = sync.plan_sync(root, initial_snapshot, mode="pull")
            sync.apply_local_operations(root, initial_plan)

            changed_snapshot = deepcopy(initial_snapshot)
            changed_snapshot["items"][1]["status"] = "In Progress"
            update_plan = sync.plan_sync(root, changed_snapshot, mode="sync")

            task_ops = [
                operation
                for operation in update_plan["operations"]
                if operation["action"] == "update_local_task"
            ]
            self.assertEqual(len(task_ops), 1)
            self.assertEqual(
                task_ops[0]["path"],
                "TICKET-001-runtime-smoke/in-progress/TASK-001-seeded-smoke.md",
            )

            sync.apply_local_operations(root, update_plan)

            epic_dir = root / ".wdd" / "epics" / "EPIC-github-import"
            self.assertFalse(
                (
                    epic_dir
                    / "TICKET-001-runtime-smoke"
                    / "todo"
                    / "TASK-001-seeded-smoke.md"
                ).exists()
            )
            self.assertTrue(
                (
                    epic_dir
                    / "TICKET-001-runtime-smoke"
                    / "in-progress"
                    / "TASK-001-seeded-smoke.md"
                ).exists()
            )


if __name__ == "__main__":
    unittest.main()
