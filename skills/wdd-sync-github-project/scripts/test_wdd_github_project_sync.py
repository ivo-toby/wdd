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

    # -- Finding 1 (P1): path traversal via a malicious remote "WDD ID" -----

    def test_malicious_wdd_id_cannot_escape_wdd_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = remote_snapshot()
            malicious_id = "TASK-/../../../escaped"
            snapshot["items"][0]["wdd_id"] = malicious_id

            result = sync.plan_sync(root, snapshot, mode="pull")

            # The malicious id must be reported as a blocking conflict, not
            # silently used to build a path.
            invalid_id_conflicts = [c for c in result["conflicts"] if c["type"] == "invalid_wdd_id"]
            self.assertEqual(len(invalid_id_conflicts), 1)
            self.assertIn(malicious_id, invalid_id_conflicts[0]["reason"])
            self.assertEqual(result["operations"], [])

            # apply_local_operations must refuse outright (same fail-closed
            # path as scope_mismatch/id_collision) -- a clear error is raised.
            with self.assertRaises(RuntimeError):
                sync.apply_local_operations(root, result)

            # Nothing was written anywhere, in particular not outside .wdd/.
            self.assertFalse((root / ".wdd").exists())
            self.assertFalse((root.parent / "escaped").exists())
            self.assertFalse((root.parent / "escaped.md").exists())

    def test_containment_check_is_independent_second_layer(self):
        """Even if a malicious specPath somehow bypassed WDD-ID validation
        upstream (a bug, a future code path, a hand-built result dict), the
        resolved-path containment check in apply_local_operations must still
        refuse to write outside .wdd/tasks/. Belt and braces."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            escaping_spec_path = "../../../escaped-via-bypass.md"
            forged_entry = {
                "id": "TASK-forged",
                "title": "Forged",
                "specPath": escaping_spec_path,
                "risk": "normal",
                "dependsOn": [],
                "conflictDomains": [],
            }
            forged_result = {
                "schemaVersion": 1,
                "mode": "pull",
                "scopeId": "SCOPE-forged",
                "project": {"base_ref": "wdd/forged"},
                "operations": [],
                "conflicts": [],  # simulates a bypass: no conflict was raised
                "warnings": [],
                "remoteItems": [],
                "planTasks": {"TASK-forged": forged_entry},
                "briefs": {"TASK-forged": "malicious content"},
                "existingPlan": None,
            }

            with self.assertRaises(RuntimeError):
                sync.apply_local_operations(root, forged_result)

            # plan.json itself is a legitimate, non-attacker-controlled path
            # and may already have been written before the brief write is
            # reached; what must never happen is the brief escaping .wdd/.
            self.assertFalse((root / ".wdd" / "tasks").exists())
            self.assertFalse((root.parent / "escaped-via-bypass.md").exists())

    # -- Finding 2 (P2): convergence path for tasks created by push ---------

    def test_record_link_lets_next_sync_match_instead_of_collide(self):
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

            push_snapshot = {
                "project": {"owner": "ivo-toby", "number": 4, "repo": "ivo-toby/example", "title": "Auth Refresh"},
                "items": [],
            }
            first_push = sync.plan_sync(root, push_snapshot, mode="push", scope_id="SCOPE-auth-refresh")
            self.assertIn("create_remote_issue", [op["action"] for op in first_push["operations"]])

            # Simulate a human applying that plan by hand: the issue now
            # exists on GitHub as #42. Record that link locally.
            recorded = sync.record_remote_links(root, {"TASK-001-api-contract": {"issueNumber": 42}})
            self.assertEqual(recorded, ["TASK-001-api-contract"])

            manifest = json.loads(
                (root / ".wdd" / "adapters" / "github-project.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["items"]["TASK-001-api-contract"]["github"]["issueNumber"], 42)

            # The next push/sync must now see the task as linked (matching
            # issue #42 in the remote snapshot) instead of creating another
            # remote issue / colliding.
            snapshot_with_issue = {
                "project": {"owner": "ivo-toby", "number": 4, "repo": "ivo-toby/example", "title": "Auth Refresh"},
                "items": [
                    {
                        "item_id": "PVTI_new",
                        "issue_number": 42,
                        "url": "https://github.com/ivo-toby/example/issues/42",
                        "title": "API contract",
                        "body": "Define the API contract.",
                        "status": "Todo",
                    }
                ],
            }
            second_push = sync.plan_sync(root, snapshot_with_issue, mode="push", scope_id="SCOPE-auth-refresh")
            self.assertEqual(second_push["conflicts"], [])
            self.assertNotIn("create_remote_issue", [op["action"] for op in second_push["operations"]])

    def test_record_link_rejects_unknown_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_text(
                root / ".wdd" / "plan.json",
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "kind": "wdd_plan",
                        "scope": {"id": "SCOPE-x", "baseRef": "wdd/x", "maxConcurrent": None,
                                  "reviewPolicy": "risk_based", "reconcileEveryNMerges": 3},
                        "tasks": [],
                    }
                ),
            )
            with self.assertRaises(RuntimeError):
                sync.record_remote_links(root, {"TASK-does-not-exist": {"issueNumber": 1}})

    def test_parse_record_link_cli_argument(self):
        task_id, link = sync.parse_record_link("TASK-001-api-contract=42")
        self.assertEqual(task_id, "TASK-001-api-contract")
        self.assertEqual(link, {"issueNumber": 42})

        task_id, link = sync.parse_record_link("TASK-001-api-contract=PVTI_abc123")
        self.assertEqual(link, {"itemId": "PVTI_abc123"})

        with self.assertRaises(RuntimeError):
            sync.parse_record_link("not-a-valid-spec")
        with self.assertRaises(RuntimeError):
            sync.parse_record_link("TASK-/../escape=42")

    # -- Finding 3 (P2): push must not invent "todo" with no controller state --

    def test_push_without_controller_state_does_not_regress_remote_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_pull = sync.plan_sync(root, remote_snapshot(), mode="pull")
            sync.apply_local_operations(root, first_pull)

            # `pull --apply-local` writes no state.json (by design -- wddctl
            # owns it exclusively). Push immediately afterwards must not map
            # the imported "In Progress" task back down to "Todo".
            self.assertFalse((root / ".wdd" / "state.json").exists())

            result = sync.plan_sync(root, remote_snapshot(), mode="push")

            status_ops = {
                op["task"]: op["fields"]["Status"]
                for op in result["operations"]
                if op["action"] == "update_project_fields" and "Status" in op.get("fields", {})
            }
            # No status-changing operation at all for the in-progress task --
            # in particular, it must never be pushed back to "Todo".
            self.assertNotIn("TASK-002-refresh-token-endpoint", status_ops)

            skipped = [
                w
                for w in result["warnings"]
                if w["type"] == "status_skipped_no_controller_state" and w["task"] == "TASK-002-refresh-token-endpoint"
            ]
            self.assertEqual(len(skipped), 1)

    # -- Finding 1 (P2): a symlinked container defeats the containment check --

    def test_symlinked_tasks_dir_cannot_escape_the_repository(self):
        """Reproduction: point `.wdd/tasks` at a directory outside the repo
        root. `.resolve()`-both-sides containment would follow the symlink
        on both `path` and `container` and see them agree -- and write an
        ordinary task brief outside the checkout. The fix anchors
        containment to the trusted `--root` and walks every component from
        there for a symlink, so this must raise instead of writing."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            outside = tmp_path / "outside"
            root.mkdir()
            outside.mkdir()
            (root / ".wdd").mkdir()
            (root / ".wdd" / "tasks").symlink_to(outside, target_is_directory=True)

            result = sync.plan_sync(root, remote_snapshot(), mode="pull")
            self.assertEqual(result["conflicts"], [])

            with self.assertRaises(RuntimeError) as ctx:
                sync.apply_local_operations(root, result)
            self.assertIn("symlink", str(ctx.exception))

            # Nothing was written through the symlink, in particular no task
            # brief landed in the directory it points at.
            self.assertEqual(list(outside.iterdir()), [])

    def test_symlinked_wdd_dir_cannot_escape_the_repository(self):
        """Same reproduction, one level up: `.wdd` itself is the symlink."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            outside = tmp_path / "outside"
            root.mkdir()
            outside.mkdir()
            root.joinpath(".wdd").symlink_to(outside, target_is_directory=True)

            result = sync.plan_sync(root, remote_snapshot(), mode="pull")
            self.assertEqual(result["conflicts"], [])

            with self.assertRaises(RuntimeError) as ctx:
                sync.apply_local_operations(root, result)
            self.assertIn("symlink", str(ctx.exception))

            self.assertEqual(list(outside.iterdir()), [])

    # -- Finding 2 (P2): a linked task missing from the snapshot must not ---
    # -- be recreated as a duplicate issue -----------------------------------

    def _write_linked_task(self, root: Path, github_link: dict) -> None:
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
        write_text(
            root / ".wdd" / "adapters" / "github-project.json",
            json.dumps(
                {
                    "schemaVersion": 1,
                    "updatedAt": "2026-01-01T00:00:00Z",
                    "scope": {"id": "SCOPE-auth-refresh"},
                    "project": {},
                    "items": {
                        "TASK-001-api-contract": {
                            "localPath": "tasks/TASK-001-api-contract.md",
                            "github": github_link,
                            "fingerprints": {"local": "sha256:x", "remote": "sha256:y"},
                        }
                    },
                }
            ),
        )

    def test_push_does_not_recreate_an_already_linked_task_missing_from_snapshot(self):
        """Reproduction: manifest still holds an issue link for a task, but
        the fetched Project snapshot is empty (the item was removed from the
        board, or the fetch was partial). This must never fall through to
        create_remote_issue -- that would duplicate an issue that already
        exists. The known issue number is re-added to the project instead."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_linked_task(root, {"issueNumber": 42})

            empty_snapshot = {
                "project": {"owner": "ivo-toby", "number": 4, "repo": "ivo-toby/example", "title": "Auth Refresh"},
                "items": [],
            }
            result = sync.plan_sync(root, empty_snapshot, mode="push", scope_id="SCOPE-auth-refresh")

            actions = [op["action"] for op in result["operations"]]
            self.assertEqual(actions.count("create_remote_issue"), 0)
            self.assertIn("add_issue_to_project", actions)
            re_add = next(op for op in result["operations"] if op["action"] == "add_issue_to_project")
            self.assertEqual(re_add["issueNumber"], 42)
            self.assertEqual(result["conflicts"], [])

            missing_warnings = [w for w in result["warnings"] if w["type"] == "linked_item_missing_from_snapshot"]
            self.assertEqual(len(missing_warnings), 1)
            self.assertEqual(missing_warnings[0]["task"], "TASK-001-api-contract")
            self.assertIn("42", missing_warnings[0]["reason"])

    def test_push_reports_conflict_for_linked_item_id_missing_from_snapshot(self):
        """Same reproduction, but only a bare Project item id (no issue
        number) was recorded. There is no safe auto-recovery -- this must
        block as a conflict rather than guess, and still never create."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_linked_task(root, {"itemId": "PVTI_gone"})

            empty_snapshot = {
                "project": {"owner": "ivo-toby", "number": 4, "repo": "ivo-toby/example", "title": "Auth Refresh"},
                "items": [],
            }
            result = sync.plan_sync(root, empty_snapshot, mode="push", scope_id="SCOPE-auth-refresh")

            actions = [op["action"] for op in result["operations"]]
            self.assertEqual(actions.count("create_remote_issue"), 0)
            self.assertEqual(result["operations"], [])

            conflicts = [c for c in result["conflicts"] if c["type"] == "linked_item_missing_from_snapshot"]
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0]["task"], "TASK-001-api-contract")
            self.assertIn("PVTI_gone", conflicts[0]["reason"])

    def test_push_with_controller_state_still_updates_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_pull = sync.plan_sync(root, remote_snapshot(), mode="pull")
            sync.apply_local_operations(root, first_pull)

            write_text(
                root / ".wdd" / "state.json",
                json.dumps({"schemaVersion": 3, "tasks": {"TASK-001-token-type-contract": {"status": "review"}}}),
            )

            result = sync.plan_sync(root, remote_snapshot(), mode="push")
            field_ops = {op["task"]: op for op in result["operations"] if op["action"] == "update_project_fields"}
            self.assertEqual(field_ops["TASK-001-token-type-contract"]["fields"]["Status"], "Review")


if __name__ == "__main__":
    unittest.main()
