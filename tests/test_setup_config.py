from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from wave_delivery.cli import main
from wave_delivery.config import (
    check_ratifiable,
    default_config,
    get_value,
    governance_fingerprint,
    load_config,
    save_config,
    set_value,
    validate_config,
)
from wave_delivery.errors import ValidationError
from wave_delivery.schema import (
    SCHEMA_VERSION,
    derived_phase,
    new_setup_state,
    new_state,
    validate_state,
)
from wave_delivery.plan import apply_plan
from wave_delivery.setup import CONSTITUTION_TEMPLATE, init_repository, setup_next_actions
from wave_delivery.store import StateStore


class ConfigValidationTest(unittest.TestCase):
    def test_default_config_validates(self) -> None:
        validate_config(default_config())

    def test_default_config_is_a_fresh_copy(self) -> None:
        first = default_config()
        first["merge"]["surface"] = "local"
        self.assertEqual(default_config()["merge"]["surface"], "pr")

    def test_rejects_unknown_merge_surface(self) -> None:
        config = default_config()
        config["merge"]["surface"] = "carrier-pigeon"
        with self.assertRaises(ValidationError):
            validate_config(config)

    def test_rejects_bad_risk_rule(self) -> None:
        config = default_config()
        config["riskRules"] = [{"pattern": "src/**", "risk": "extreme"}]
        with self.assertRaises(ValidationError):
            validate_config(config)

    def test_rejects_non_local_task_provider(self) -> None:
        config = default_config()
        config["taskProvider"]["type"] = "jira"
        with self.assertRaises(ValidationError):
            validate_config(config)

    def test_rejects_malformed_open_question(self) -> None:
        config = default_config()
        config["openQuestions"] = [{"question": "no path key"}]
        with self.assertRaises(ValidationError):
            validate_config(config)


class ConfigStorageTest(unittest.TestCase):
    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            config = default_config()
            config["verification"]["commands"] = ["pytest -q"]
            save_config(wdd, config)
            self.assertEqual(load_config(wdd), config)

    def test_load_missing_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValidationError):
                load_config(Path(tmp) / ".wdd")

    def test_load_invalid_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir()
            (wdd / "config.json").write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_config(wdd)


class ConfigPathAccessTest(unittest.TestCase):
    def test_get_value_walks_dotted_path(self) -> None:
        self.assertEqual(get_value(default_config(), "merge.surface"), "pr")

    def test_get_value_unknown_path_raises(self) -> None:
        with self.assertRaises(ValidationError):
            get_value(default_config(), "merge.velocity")

    def test_set_value_returns_validated_copy(self) -> None:
        config = default_config()
        updated = set_value(config, "merge.surface", "local")
        self.assertEqual(updated["merge"]["surface"], "local")
        self.assertEqual(config["merge"]["surface"], "pr")

    def test_set_value_rejects_invalid_value(self) -> None:
        with self.assertRaises(ValidationError):
            set_value(default_config(), "merge.surface", "carrier-pigeon")

    def test_set_value_resolves_matching_open_question(self) -> None:
        config = default_config()
        config["openQuestions"] = [
            {"path": "merge.surface", "question": "pr or local?", "options": ["pr", "local"]}
        ]
        updated = set_value(config, "merge.surface", "local")
        self.assertEqual(updated["openQuestions"], [])


class ConfigCliTest(unittest.TestCase):
    def _run(self, tmp: str, *argv: str) -> tuple[int, str]:
        state = str(Path(tmp) / ".wdd" / "state.json")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["--state", state, *argv])
        return code, stdout.getvalue()

    def test_config_set_then_get_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            save_config(wdd, default_config())
            code, _ = self._run(tmp, "config", "set", "verification.commands", '["pytest -q"]')
            self.assertEqual(code, 0)
            code, out = self._run(tmp, "config", "get", "verification.commands")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out), ["pytest -q"])

    def test_config_set_bare_string_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            save_config(wdd, default_config())
            code, _ = self._run(tmp, "config", "set", "merge.surface", "local")
            self.assertEqual(code, 0)
            self.assertEqual(load_config(wdd)["merge"]["surface"], "local")


class SetupStateTest(unittest.TestCase):
    def test_schema_version_is_4(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 4)

    def test_new_setup_state_validates_with_null_scope(self) -> None:
        state = new_setup_state()
        validate_state(state)
        self.assertIsNone(state["scope"])
        self.assertEqual(state["tasks"], {})

    def test_null_scope_with_tasks_is_rejected(self) -> None:
        state = new_setup_state()
        state["tasks"]["TASK-001"] = {"id": "TASK-001"}
        with self.assertRaises(ValidationError):
            validate_state(state)

    def test_derived_phase(self) -> None:
        setup = new_setup_state()
        self.assertEqual(derived_phase(setup), "setup")
        ratified = new_setup_state()
        ratified["constitution"] = {
            "status": "ratified",
            "ratification": {"by": "ivo", "decisionFingerprint": "sha256:abc"},
        }
        self.assertEqual(derived_phase(ratified), "setup")  # still no scope
        executing = new_state("SCOPE-x", base_ref="wdd/x")
        executing["constitution"] = ratified["constitution"]
        self.assertEqual(derived_phase(executing), "execute")


class MigrationV3Test(unittest.TestCase):
    def test_v3_state_migrates_to_v4(self) -> None:
        from wave_delivery.migration import plan_migration

        with tempfile.TemporaryDirectory() as tmp:
            state = new_state("SCOPE-x", base_ref="wdd/x")
            state["schemaVersion"] = 3
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            result = plan_migration(path)
            # plan_migration's dry-run result carries migration metadata, not
            # the converted state itself: {"state", "from", "to", "tasks",
            # "backup", "notes"}. "to" is always SCHEMA_VERSION.
            self.assertEqual(result["from"], 3)
            self.assertEqual(result["to"], 4)


def _write_governance(wdd: Path, *, questions: list | None = None) -> None:
    config = default_config()
    config["openQuestions"] = questions or []
    save_config(wdd, config)
    (wdd / "constitution.md").write_text("# Constitution\n\nProse only.\n", encoding="utf-8")


class GovernanceFingerprintTest(unittest.TestCase):
    def test_fingerprint_is_stable_and_prefixed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            _write_governance(wdd)
            first = governance_fingerprint(wdd)
            self.assertTrue(first.startswith("sha256:"))
            self.assertEqual(first, governance_fingerprint(wdd))

    def test_fingerprint_changes_when_constitution_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            _write_governance(wdd)
            before = governance_fingerprint(wdd)
            (wdd / "constitution.md").write_text("# Changed\n", encoding="utf-8")
            self.assertNotEqual(before, governance_fingerprint(wdd))

    def test_fingerprint_changes_when_config_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            _write_governance(wdd)
            before = governance_fingerprint(wdd)
            save_config(wdd, set_value(load_config(wdd), "merge.surface", "local"))
            self.assertNotEqual(before, governance_fingerprint(wdd))

    def test_ratifiable_refused_with_open_questions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            _write_governance(
                wdd, questions=[{"path": "merge.surface", "question": "pr or local?"}]
            )
            with self.assertRaises(ValidationError):
                check_ratifiable(wdd)


def _git_repo(tmp: str) -> Path:
    root = Path(tmp) / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    return root


class InitTest(unittest.TestCase):
    def test_init_scaffolds_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            result = init_repository(wdd, root)
            self.assertFalse(result["alreadyInitialized"])
            self.assertTrue((wdd / "config.json").is_file())
            self.assertTrue((wdd / "constitution.md").is_file())
            self.assertTrue((wdd / "tasks").is_dir())
            self.assertTrue((wdd / "shared-context").is_dir())
            state = StateStore(wdd / "state.json").read()
            self.assertIsNone(state["scope"])

    def test_init_always_asks_about_merge_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            init_repository(root / ".wdd", root)
            config = load_config(root / ".wdd")
            self.assertIn("merge.surface", [q["path"] for q in config["openQuestions"]])

    def test_init_asks_verification_only_when_undetected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            (root / "package.json").write_text("{}", encoding="utf-8")
            init_repository(root / ".wdd", root)
            config = load_config(root / ".wdd")
            self.assertEqual(config["verification"]["commands"], ["npm test"])
            self.assertNotIn(
                "verification.commands", [q["path"] for q in config["openQuestions"]]
            )

    def test_init_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            init_repository(root / ".wdd", root)
            marker = (root / ".wdd" / "constitution.md").read_text(encoding="utf-8")
            result = init_repository(root / ".wdd", root)
            self.assertTrue(result["alreadyInitialized"])
            self.assertEqual(
                (root / ".wdd" / "constitution.md").read_text(encoding="utf-8"), marker
            )

    def test_template_has_no_placeholders_or_json(self) -> None:
        for banned in ("```json", "TBD", "TODO", "<", "state which"):
            self.assertNotIn(banned, CONSTITUTION_TEMPLATE)

    def test_init_preserves_existing_config(self) -> None:
        """Existing config.json should not be overwritten; openQuestions should survive."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            # First init to create config
            init_repository(wdd, root)
            original_config = load_config(wdd)
            # Manually modify an open question (simulate manual edits or prior resolution)
            modified_config = original_config.copy()
            modified_config["openQuestions"] = [
                q for q in modified_config["openQuestions"] if q["path"] != "merge.surface"
            ]
            modified_config["customField"] = "preserved_value"
            save_config(wdd, modified_config)
            # Remove state.json to simulate partial failure
            (wdd / "state.json").unlink()
            # Second init should preserve the modified config
            result = init_repository(wdd, root)
            self.assertFalse(result["alreadyInitialized"])
            reloaded_config = load_config(wdd)
            self.assertNotIn("merge.surface", [q["path"] for q in reloaded_config["openQuestions"]])
            self.assertEqual(reloaded_config.get("customField"), "preserved_value")
            # Config should not be listed in created (since it already existed)
            self.assertNotIn(str(wdd / "config.json"), result["created"])


class InitCliTest(unittest.TestCase):
    def test_cli_init_prints_next_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            state = str(root / ".wdd" / "state.json")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--state", state, "init", "--repo", str(root)])
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertFalse(payload["alreadyInitialized"])
            self.assertTrue(payload["openQuestions"])


class SetupNextTest(unittest.TestCase):
    def _initialized(self, tmp: str) -> tuple[Path, Path]:
        root = _git_repo(tmp)
        wdd = root / ".wdd"
        init_repository(wdd, root)
        return root, wdd

    def test_first_action_is_resolve_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, wdd = self._initialized(tmp)
            state = StateStore(wdd / "state.json").read()
            result = setup_next_actions(state, wdd)
            self.assertEqual(result["actions"][0]["action"], "resolve_config")
            self.assertTrue(result["actions"][0]["questions"])

    def test_after_resolution_action_is_ratify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, wdd = self._initialized(tmp)
            config = load_config(wdd)
            config = set_value(config, "merge.surface", "local")
            config = set_value(config, "verification.commands", ["true"])
            save_config(wdd, config)
            state = StateStore(wdd / "state.json").read()
            result = setup_next_actions(state, wdd)
            self.assertEqual(result["actions"][0]["action"], "ratify")

    def test_cli_next_routes_to_setup_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd = self._initialized(tmp)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--state", str(wdd / "state.json"), "next"])
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["actions"][0]["action"], "resolve_config")


class LegacyStateRoutingTest(unittest.TestCase):
    def test_legacy_state_with_scope_and_no_config_status(self) -> None:
        """Verify status falls back to execute-phase when config.json is missing."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            wdd.mkdir(parents=True)

            # Create legacy state with scope but no config.json
            state = new_state("SCOPE-legacy", base_ref="wdd/legacy")
            StateStore(wdd / "state.json").write(state)

            # Write minimal constitution to avoid unrelated errors
            (wdd / "constitution.md").write_text("# Constitution\n", encoding="utf-8")

            # status should exit 0 and output scope in JSON (not setup phase)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--state", str(wdd / "state.json"), "status", "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["scope"]["id"], "SCOPE-legacy")

    def test_legacy_state_with_scope_and_no_config_next(self) -> None:
        """Verify next falls back to execute-phase when config.json is missing."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            wdd.mkdir(parents=True)

            # Create legacy state with scope but no config.json
            state = new_state("SCOPE-legacy", base_ref="wdd/legacy")
            StateStore(wdd / "state.json").write(state)

            # Write minimal constitution to avoid unrelated errors
            (wdd / "constitution.md").write_text("# Constitution\n", encoding="utf-8")

            # next should exit 0 and have constitution_unratified blocker
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--state", str(wdd / "state.json"), "next"])
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            # Find the constitution_unratified blocker
            blockers = payload.get("blockers", [])
            self.assertTrue(any(b.get("code") == "constitution_unratified" for b in blockers))


def _minimal_plan() -> dict:
    return {
        "schemaVersion": 1,
        "kind": "wdd_plan",
        "scope": {
            "id": "SCOPE-demo",
            "baseRef": None,
            "maxConcurrent": 2,
            "reviewPolicy": "risk_based",
            "reconcileEveryNMerges": 3,
        },
        "tasks": [
            {
                "id": "TASK-001-first",
                "title": "First task",
                "specPath": "tasks/TASK-001-first.md",
                "risk": "normal",
                "dependsOn": [],
                "conflictDomains": ["src/**"],
            }
        ],
    }


class PlanAdoptionTest(unittest.TestCase):
    def test_apply_onto_setup_state_adopts_scope_and_keeps_ratification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            init_repository(wdd, root)
            store = StateStore(wdd / "state.json")
            state = store.read()
            state["constitution"] = {
                "status": "ratified",
                "ratification": {"by": "ivo", "decisionFingerprint": "sha256:abc"},
            }
            store.write(state)
            result = apply_plan(store, _minimal_plan())
            self.assertFalse(result["created"])
            adopted = store.read()
            self.assertEqual(adopted["scope"]["id"], "SCOPE-demo")
            self.assertIn("TASK-001-first", adopted["tasks"])
            self.assertEqual(adopted["constitution"]["status"], "ratified")


if __name__ == "__main__":
    unittest.main()
