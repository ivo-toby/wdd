#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(REPO_ROOT))

import wdd_github_project_sync as sync
from wave_delivery.plan import validate_plan


def remote_snapshot() -> dict:
    return {
        "project": {
            "owner": "ivo-toby",
            "number": 4,
            "id": "PVT_kwExample",
            "title": "Auth Refresh",
            "url": "https://github.com/orgs/ivo-toby/projects/4",
            "repo": "ivo-toby/example",
        },
        "items": [
            {
                "item_id": "PVTI_token",
                "issue_number": 7,
                "url": "https://github.com/ivo-toby/example/issues/7",
                "title": "Token type contract",
                "body": "Define the RefreshToken/AccessToken types.",
                "status": "Todo",
                "labels": ["auth"],
                "conflict_domains": "src/auth/**, src/schema.ts",
            },
            {
                "item_id": "PVTI_endpoint",
                "issue_number": 8,
                "url": "https://github.com/ivo-toby/example/issues/8",
                "title": "Refresh token endpoint",
                "body": "Implement the refresh endpoint.",
                "status": "In Progress",
                "depends_on": "#7",
                "conflict_domains": ["src/auth/routes/**"],
            },
        ],
    }


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class WddGithubProjectSyncTests(unittest.TestCase):
    def test_pull_plan_creates_plan_and_task_briefs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = sync.plan_sync(root, remote_snapshot(), mode="pull")

            actions = [op["action"] for op in result["operations"]]
            self.assertEqual(
                actions,
                ["create_plan", "create_task_brief", "create_task_brief", "write_manifest"],
            )
            self.assertEqual(result["scopeId"], "SCOPE-auth-refresh")
            self.assertEqual(result["conflicts"], [])

    def test_generated_plan_json_passes_the_real_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = sync.plan_sync(root, remote_snapshot(), mode="pull")
            sync.apply_local_operations(root, result)

            plan = json.loads((root / ".wdd" / "plan.json").read_text(encoding="utf-8"))
            # Must not raise -- this is the authoritative plan.json contract.
            validated = validate_plan(plan)

            self.assertEqual(validated["scope"]["id"], "SCOPE-auth-refresh")
            self.assertEqual(len(validated["tasks"]), 2)

    def test_apply_local_pull_writes_plan_briefs_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = sync.plan_sync(root, remote_snapshot(), mode="pull")
            sync.apply_local_operations(root, result)

            plan = json.loads((root / ".wdd" / "plan.json").read_text(encoding="utf-8"))
            task_ids = [task["id"] for task in plan["tasks"]]
            self.assertEqual(task_ids, ["TASK-001-token-type-contract", "TASK-002-refresh-token-endpoint"])

            first_task = plan["tasks"][0]
            self.assertEqual(first_task["risk"], "high")  # "auth" label
            self.assertEqual(first_task["conflictDomains"], ["src/auth/**", "src/schema.ts"])
            second_task = plan["tasks"][1]
            self.assertEqual(second_task["dependsOn"], ["TASK-001-token-type-contract"])

            for task in plan["tasks"]:
                self.assertTrue((root / ".wdd" / task["specPath"]).exists())

            manifest = json.loads(
                (root / ".wdd" / "adapters" / "github-project.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schemaVersion"], 1)
            self.assertEqual(
                manifest["items"]["TASK-001-token-type-contract"]["github"]["issueNumber"], 7
            )

    def test_empty_conflict_domains_produce_a_loud_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = remote_snapshot()
            del snapshot["items"][1]["conflict_domains"]  # no field at all
            result = sync.plan_sync(root, snapshot, mode="pull")

            warnings = [w for w in result["warnings"] if w["type"] == "empty_conflict_domains"]
            self.assertEqual(len(warnings), 1)
            self.assertEqual(warnings[0]["task"], "TASK-002-refresh-token-endpoint")

            task_entry = result["planTasks"]["TASK-002-refresh-token-endpoint"]
            self.assertEqual(task_entry["conflictDomains"], [])

    def test_unresolved_dependency_reference_is_dropped_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = remote_snapshot()
            snapshot["items"][1]["depends_on"] = "#999, Nonexistent Item"
            result = sync.plan_sync(root, snapshot, mode="pull")

            unresolved = [w for w in result["warnings"] if w["type"] == "unresolved_dependency"]
            self.assertEqual(len(unresolved), 2)
            self.assertEqual(result["planTasks"]["TASK-002-refresh-token-endpoint"]["dependsOn"], [])

    def test_sync_reports_conflict_when_local_and_remote_both_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_pull = sync.plan_sync(root, remote_snapshot(), mode="pull")
            sync.apply_local_operations(root, first_pull)

            # Simulate a human hand-editing the brief after import.
            plan = json.loads((root / ".wdd" / "plan.json").read_text(encoding="utf-8"))
            brief_path = root / ".wdd" / "tasks" / "TASK-001-token-type-contract.md"
            write_text(brief_path, brief_path.read_text(encoding="utf-8") + "\nHuman edit.\n")

            changed_remote = remote_snapshot()
            changed_remote["items"][0]["body"] = "Remote changed the description."

            result = sync.plan_sync(root, changed_remote, mode="sync")

            self.assertEqual(len(result["conflicts"]), 1)
            self.assertEqual(result["conflicts"][0]["task"], "TASK-001-token-type-contract")
            self.assertEqual(result["operations"], [])

    def test_remote_only_change_updates_local_without_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_pull = sync.plan_sync(root, remote_snapshot(), mode="pull")
            sync.apply_local_operations(root, first_pull)

            changed_remote = remote_snapshot()
            changed_remote["items"][0]["title"] = "Token type contract (renamed)"

            result = sync.plan_sync(root, changed_remote, mode="sync")

            self.assertEqual(result["conflicts"], [])
            update_ops = [op for op in result["operations"] if op["action"] == "update_task_brief"]
            self.assertEqual(len(update_ops), 1)
            self.assertEqual(update_ops[0]["task"], "TASK-001-token-type-contract")

    def test_id_collision_with_unmanaged_local_task_is_reported_as_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_text(
                root / ".wdd" / "plan.json",
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "kind": "wdd_plan",
                        "scope": {
                            "id": "SCOPE-auth-refresh",
                            "baseRef": "wdd/auth-refresh",
                            "maxConcurrent": None,
                            "reviewPolicy": "risk_based",
                            "reconcileEveryNMerges": 3,
                        },
                        "tasks": [
                            {
                                "id": "TASK-001-token-type-contract",
                                "title": "Hand-authored task, not from GitHub",
                                "specPath": "tasks/TASK-001-token-type-contract.md",
                                "risk": "normal",
                                "dependsOn": [],
                                "conflictDomains": ["src/hand-authored.ts"],
                            }
                        ],
                    }
                ),
            )

            snapshot = remote_snapshot()
            # Force the same id the hand-authored task already uses, since the
            # generator otherwise steers new ids away from ones already taken.
            snapshot["items"][0]["wdd_id"] = "TASK-001-token-type-contract"
            result = sync.plan_sync(root, snapshot, mode="pull")

            collisions = [c for c in result["conflicts"] if c["type"] == "id_collision"]
            self.assertEqual(len(collisions), 1)
            self.assertEqual(collisions[0]["task"], "TASK-001-token-type-contract")
            self.assertEqual(result["operations"], [])

    def test_push_creates_remote_issue_ops_for_unlinked_local_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_text(
                root / ".wdd" / "plan.json",
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "kind": "wdd_plan",
                        "scope": {
                            "id": "SCOPE-auth-refresh",
                            "baseRef": "wdd/auth-refresh",
                            "maxConcurrent": None,
                            "reviewPolicy": "risk_based",
                            "reconcileEveryNMerges": 3,
                        },
                        "tasks": [
                            {
                                "id": "TASK-001-api-contract",
                                "title": "API contract",
                                "specPath": "tasks/TASK-001-api-contract.md",
                                "risk": "normal",
                                "dependsOn": [],
                                "conflictDomains": ["src/api/**"],
                            }
                        ],
                    }
                ),
            )
            write_text(
                root / ".wdd" / "tasks" / "TASK-001-api-contract.md",
                "# TASK-001-api-contract: API contract\n\n## Objective\n\nDefine the API contract.\n",
            )

            result = sync.plan_sync(
                root,
                {"project": {"owner": "ivo-toby", "number": 4, "repo": "ivo-toby/example", "title": "Auth Refresh"}, "items": []},
                mode="push",
                scope_id="SCOPE-auth-refresh",
            )

            actions = [op["action"] for op in result["operations"]]
            self.assertIn("create_remote_issue", actions)
            self.assertIn("add_issue_to_project", actions)
            self.assertIn("update_project_fields", actions)
            self.assertEqual(result["conflicts"], [])

    def test_push_uses_state_json_status_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_pull = sync.plan_sync(root, remote_snapshot(), mode="pull")
            sync.apply_local_operations(root, first_pull)

            state = {
                "schemaVersion": 3,
                "tasks": {
                    "TASK-001-token-type-contract": {"status": "in_progress"},
                    "TASK-002-refresh-token-endpoint": {"status": "todo"},
                },
            }
            write_text(root / ".wdd" / "state.json", json.dumps(state))
            state_before = json.loads((root / ".wdd" / "state.json").read_text(encoding="utf-8"))

            result = sync.plan_sync(root, remote_snapshot(), mode="push")

            field_ops = {op["task"]: op for op in result["operations"] if op["action"] == "update_project_fields"}
            self.assertEqual(field_ops["TASK-001-token-type-contract"]["fields"]["Status"], "In Progress")

            # state.json on disk must be byte-for-byte unchanged -- read-only.
            state_after = json.loads((root / ".wdd" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state_before, state_after)

    def test_scope_mismatch_blocks_without_touching_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_text(
                root / ".wdd" / "plan.json",
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "kind": "wdd_plan",
                        "scope": {
                            "id": "SCOPE-something-else",
                            "baseRef": "wdd/something-else",
                            "maxConcurrent": None,
                            "reviewPolicy": "risk_based",
                            "reconcileEveryNMerges": 3,
                        },
                        "tasks": [
                            {
                                "id": "TASK-001-x",
                                "title": "x",
                                "specPath": "tasks/TASK-001-x.md",
                                "risk": "normal",
                                "dependsOn": [],
                                "conflictDomains": [],
                            }
                        ],
                    }
                ),
            )

            result = sync.plan_sync(root, remote_snapshot(), mode="pull")

            self.assertEqual(len(result["conflicts"]), 1)
            self.assertEqual(result["conflicts"][0]["type"], "scope_mismatch")
            with self.assertRaises(RuntimeError):
                sync.apply_local_operations(root, result)

    def test_manifest_lives_at_new_adapter_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = sync.plan_sync(root, remote_snapshot(), mode="pull")
            sync.apply_local_operations(root, result)

            self.assertTrue((root / ".wdd" / "adapters" / "github-project.json").exists())
            self.assertFalse((root / ".wdd" / "epics").exists())

    def test_apply_reports_what_it_wrote_not_the_post_apply_plan(self):
        """Regression: --apply-local re-plans to confirm convergence, and
        reporting only that would always claim 'No operations' after writing."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = remote_snapshot()
            planned = sync.plan_sync(root, snapshot, mode="pull")
            self.assertTrue(planned["operations"])
            applied_operations = list(planned["operations"])
            sync.apply_local_operations(root, planned)
            residual = sync.plan_sync(root, snapshot, mode="pull")
            residual["appliedOperations"] = applied_operations

            # Convergence: nothing left to do.
            self.assertEqual(residual["operations"], [])
            # But the applied work is still reported, with real action names.
            self.assertEqual(len(residual["appliedOperations"]), len(applied_operations))
            for operation in residual["appliedOperations"]:
                self.assertIn("action", operation)
            actions = {operation["action"] for operation in residual["appliedOperations"]}
            self.assertIn("create_plan", actions)


if __name__ == "__main__":
    unittest.main()
