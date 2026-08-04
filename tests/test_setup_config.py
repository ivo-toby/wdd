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
    governance_drift,
    governance_fingerprint,
    load_config,
    save_config,
    set_value,
    validate_config,
)
from wave_delivery.errors import IllegalTransition, ValidationError
from wave_delivery.schema import (
    SCHEMA_VERSION,
    derived_phase,
    new_setup_state,
    new_state,
    validate_state,
)
from wave_delivery.plan import apply_plan
from wave_delivery.setup import CONSTITUTION_TEMPLATE, init_repository, migrate_governance, setup_next_actions
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

    def test_default_config_has_in_repo_worktrees_root(self) -> None:
        self.assertEqual(default_config()["worktrees"], {"root": ".worktrees"})

    def test_rejects_empty_worktrees_root(self) -> None:
        config = default_config()
        config["worktrees"]["root"] = ""
        with self.assertRaises(ValidationError):
            validate_config(config)

    def test_rejects_non_string_worktrees_root(self) -> None:
        config = default_config()
        config["worktrees"]["root"] = 123
        with self.assertRaises(ValidationError):
            validate_config(config)

    def test_missing_worktrees_key_is_backward_compatible(self) -> None:
        """A config.json written before worktrees.root existed must still load."""
        config = default_config()
        del config["worktrees"]
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

    def test_set_value_rejects_open_questions_path(self) -> None:
        # 'config set openQuestions []' would silently wipe the ratify gate
        # (and default merge.surface along with it) in one command.
        with self.assertRaises(ValidationError):
            set_value(default_config(), "openQuestions", [])
        with self.assertRaises(ValidationError):
            set_value(default_config(), "openQuestions.0.path", "merge.surface")


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
    def test_schema_version_is_6(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 6)

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
    def test_v3_state_migrates_to_v6(self) -> None:
        from wave_delivery.migration import convert, plan_migration

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
            self.assertEqual(result["to"], 6)
            # v3/v4 -> v5 is exempted wholesale via intake.legacy (spec §7);
            # it never mints itself, only migration does. v5 -> v6
            # (epic-scoped-state plan, Task 3) additionally stamps a
            # migration-time `configure` exemption alongside `legacy` (spec
            # Sec4) -- it never mints itself either.
            migrated = convert(state, wdd_dir=Path(tmp))
            self.assertIs(migrated["intake"]["legacy"], True)
            self.assertIs(migrated["intake"]["configure"]["legacy"], True)


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

    def test_init_scaffolds_worktrees_gitignore_at_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            result = init_repository(wdd, root)
            gitignore = root / ".gitignore"
            self.assertTrue(gitignore.is_file())
            self.assertIn(".worktrees/", gitignore.read_text(encoding="utf-8").splitlines())
            self.assertIn(str(gitignore), result["created"])

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
            config = set_value(
                config,
                "models",
                {"planning": None, "implementation": {"default": None, "highRisk": None}, "review": None},
            )
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
            # This test exercises scope adoption, not the intake ladder; mark
            # legacy per the plan's architecture note rather than fake-walk
            # a ladder that isn't the point here.
            state["intake"] = {"legacy": True}
            store.write(state)
            result = apply_plan(store, _minimal_plan())
            # Bootstrap removal (Task 3) collapsed the old "no file at all"
            # vs. "file exists with scope still null" distinction into one
            # case: apply_plan always finds an existing store now, so
            # "created" is redefined as "this apply just adopted a scope
            # where there was none" -- true here, same as a genuine first
            # apply after 'wddctl init'.
            self.assertTrue(result["created"])
            adopted = store.read()
            self.assertEqual(adopted["scope"]["id"], "SCOPE-demo")
            self.assertIn("TASK-001-first", adopted["tasks"])
            self.assertEqual(adopted["constitution"]["status"], "ratified")


class PlanConfigDefaultsTest(unittest.TestCase):
    def _configured_repo(self, tmp: str) -> tuple[Path, Path]:
        root = _git_repo(tmp)
        wdd = root / ".wdd"
        init_repository(wdd, root)
        config = load_config(wdd)
        config = set_value(config, "merge.surface", "local")
        config = set_value(config, "verification.commands", ["true"])
        config = set_value(config, "review.policy", "always")
        save_config(wdd, config)
        # Config-overlay tests, not intake ones: mark legacy so plan apply's
        # new ladder gate doesn't block on an unwalked ladder.
        store = StateStore(wdd / "state.json")
        state = store.read()
        state["intake"] = {"legacy": True}
        store.write(state)
        return root, wdd

    def test_omitted_scope_field_defaults_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd = self._configured_repo(tmp)
            plan = _minimal_plan()
            del plan["scope"]["reviewPolicy"]  # omitted entirely -> config wins
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "--state", str(wdd / "state.json"),
                        "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                    ]
                )
            self.assertEqual(code, 0)
            state = StateStore(wdd / "state.json").read()
            self.assertEqual(state["scope"]["reviewPolicy"], "always")

    def test_explicit_scope_field_is_not_overridden_by_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd = self._configured_repo(tmp)
            plan = _minimal_plan()
            plan["scope"]["reviewPolicy"] = "risk_based"  # explicit -> plan wins
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "--state", str(wdd / "state.json"),
                        "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                    ]
                )
            self.assertEqual(code, 0)
            state = StateStore(wdd / "state.json").read()
            self.assertEqual(state["scope"]["reviewPolicy"], "risk_based")


class GovernanceDriftTest(unittest.TestCase):
    def _ratified_repo(self, tmp: str) -> tuple[Path, Path]:
        root = _git_repo(tmp)
        wdd = root / ".wdd"
        init_repository(wdd, root)
        config = load_config(wdd)
        config = set_value(config, "merge.surface", "local")
        config = set_value(config, "verification.commands", ["true"])
        save_config(wdd, config)
        store = StateStore(wdd / "state.json")
        state = store.read()
        state["constitution"] = {
            "status": "ratified",
            "ratification": {
                "by": "ivo",
                "decisionFingerprint": governance_fingerprint(wdd),
            },
        }
        # Governance-drift tests, not intake ones: mark legacy so the
        # (unrelated) apply_plan calls some of these tests make don't block
        # on an unwalked ladder.
        state["intake"] = {"legacy": True}
        store.write(state)
        return root, wdd

    def test_no_drift_after_ratification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, wdd = self._ratified_repo(tmp)
            state = StateStore(wdd / "state.json").read()
            self.assertIsNone(governance_drift(state, wdd))

    def test_editing_config_after_ratification_is_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, wdd = self._ratified_repo(tmp)
            save_config(wdd, set_value(load_config(wdd), "concurrency.maxConcurrent", 5))
            state = StateStore(wdd / "state.json").read()
            drift = governance_drift(state, wdd)
            self.assertIsNotNone(drift)
            self.assertNotEqual(drift["ratified"], drift["actual"])

    def test_cli_start_refuses_on_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd = self._ratified_repo(tmp)
            store = StateStore(wdd / "state.json")
            apply_plan(store, _minimal_plan())
            save_config(wdd, set_value(load_config(wdd), "concurrency.maxConcurrent", 5))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(
                    [
                        "--state", str(wdd / "state.json"),
                        "start", "--task", "TASK-001-first", "--repo", str(root),
                    ]
                )
            self.assertNotEqual(code, 0)
            self.assertIn("drift", stderr.getvalue())

    def test_deleting_constitution_after_ratification_is_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd = self._ratified_repo(tmp)
            store = StateStore(wdd / "state.json")
            apply_plan(store, _minimal_plan())
            (wdd / "constitution.md").unlink()

            state = store.read()
            drift = governance_drift(state, wdd)
            self.assertIsNotNone(drift)
            self.assertEqual(drift["actual"], "missing:constitution.md")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(
                    [
                        "--state", str(wdd / "state.json"),
                        "start", "--task", "TASK-001-first", "--repo", str(root),
                    ]
                )
            self.assertNotEqual(code, 0)
            self.assertIn("drift", stderr.getvalue())


class DoctorGovernanceTest(unittest.TestCase):
    def test_doctor_on_fresh_init_reports_valid_config_and_no_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            init_repository(wdd, root)

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--state", str(wdd / "state.json"), "doctor"])
            self.assertEqual(code, 0)
            governance = json.loads(stdout.getvalue())["governance"]
            self.assertTrue(governance["configPresent"])
            self.assertTrue(governance["configValid"])
            self.assertIsNone(governance["drift"])

    def test_doctor_reports_drift_after_ratified_config_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            init_repository(wdd, root)
            config = load_config(wdd)
            config = set_value(config, "merge.surface", "local")
            config = set_value(config, "verification.commands", ["true"])
            save_config(wdd, config)
            store = StateStore(wdd / "state.json")
            state = store.read()
            state["constitution"] = {
                "status": "ratified",
                "ratification": {"by": "ivo", "decisionFingerprint": governance_fingerprint(wdd)},
            }
            store.write(state)
            # Edit after ratification without an amend: drift.
            save_config(wdd, set_value(load_config(wdd), "concurrency.maxConcurrent", 5))

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--state", str(wdd / "state.json"), "doctor"])
            # doctor reports, never refuses.
            self.assertEqual(code, 0)
            governance = json.loads(stdout.getvalue())["governance"]
            self.assertTrue(governance["configPresent"])
            self.assertTrue(governance["configValid"])
            self.assertIsNotNone(governance["drift"])


FAKE_RUNNER = str(
    Path(__file__).resolve().parent / "fixtures" / "fake-runner" / "fake-runner"
)


class DoctorRunnersTest(unittest.TestCase):
    """Task 5 addendum (spec Sec6, doctor stays read-only): when config has a
    non-empty `runners` map, doctor reports per configured runner whether its
    command's argv[0] is on PATH -- the same shutil.which idiom the existing
    git/gh/acli/codex/claude probes use."""

    def test_no_runners_key_when_config_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            wdd.mkdir()

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--state", str(wdd / "state.json"), "doctor"])
            self.assertEqual(code, 0)
            self.assertNotIn("runners", json.loads(stdout.getvalue()))

    def test_no_runners_key_when_runners_map_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            init_repository(wdd, root)  # default config: runners == {}

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--state", str(wdd / "state.json"), "doctor"])
            self.assertEqual(code, 0)
            self.assertNotIn("runners", json.loads(stdout.getvalue()))

    def test_reports_available_and_unavailable_runner_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            init_repository(wdd, root)
            config = load_config(wdd)
            config["runners"] = {
                "stub-runner": {"command": [FAKE_RUNNER, "--prompt", "{prompt}"]},
                "missing-runner": {"command": ["nope-not-a-real-binary-xyz", "run"]},
            }
            save_config(wdd, config)

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--state", str(wdd / "state.json"), "doctor"])
            self.assertEqual(code, 0)
            runners = json.loads(stdout.getvalue())["runners"]
            self.assertEqual(runners["stub-runner"]["argv0"], FAKE_RUNNER)
            self.assertTrue(runners["stub-runner"]["available"])
            self.assertEqual(runners["missing-runner"]["argv0"], "nope-not-a-real-binary-xyz")
            self.assertFalse(runners["missing-runner"]["available"])

    def test_placeholder_in_argv0_reported_unresolvable_not_crashed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            init_repository(wdd, root)
            config = load_config(wdd)
            # A malformed runner entry -- the placeholder belongs in an
            # argument, never argv[0] -- must be reported, not crash doctor.
            config["runners"] = {"malformed": {"command": ["{worktree}", "run"]}}
            save_config(wdd, config)

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--state", str(wdd / "state.json"), "doctor"])
            self.assertEqual(code, 0)
            runners = json.loads(stdout.getvalue())["runners"]
            self.assertTrue(runners["malformed"]["unresolvable"])
            self.assertFalse(runners["malformed"]["available"])

    def test_no_runners_key_when_config_invalid(self) -> None:
        """An invalid config already fails governance's own configValid check;
        doctor must not additionally crash trying to probe runners out of it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            init_repository(wdd, root)
            (wdd / "config.json").write_text("not json", encoding="utf-8")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--state", str(wdd / "state.json"), "doctor"])
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertFalse(payload["governance"]["configValid"])
            self.assertNotIn("runners", payload)


LEGACY_CONSTITUTION = """---
id: WDD-CONSTITUTION
kind: constitution
---

# Project Constitution

## Model aliases

```json
{"models": {"planning": "model-a", "implementation": "model-b", "review": "model-c"}}
```

## Merge policy

- Merge mode: controller-merges-automatically
"""


class GovernanceMigrationTest(unittest.TestCase):
    def test_extracts_models_and_invalidates_ratification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            wdd.mkdir()
            (wdd / "constitution.md").write_text(LEGACY_CONSTITUTION, encoding="utf-8")
            state = new_state("SCOPE-legacy", base_ref="wdd/legacy")
            state["constitution"] = {
                "status": "ratified",
                "ratification": {"by": "ivo", "decisionFingerprint": "sha256:old"},
            }
            StateStore(wdd / "state.json").write(state)
            result = migrate_governance(wdd)
            self.assertTrue(result["migrated"])
            config = load_config(wdd)
            self.assertEqual(config["models"]["planning"], "model-a")
            self.assertEqual(config["models"]["implementation"]["default"], "model-b")
            self.assertEqual(config["models"]["review"], "model-c")
            self.assertTrue((wdd / "constitution.md.pre-config").is_file())
            migrated_state = StateStore(wdd / "state.json").read()
            self.assertEqual(migrated_state["constitution"]["status"], "draft")

    def test_noop_when_config_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            init_repository(wdd, root)
            result = migrate_governance(wdd)
            self.assertFalse(result["migrated"])


class SetupStatusScopeTest(unittest.TestCase):
    def test_status_reports_actual_scope_id_for_post_migrate_setup_state(self) -> None:
        """migrate_governance invalidates ratification but keeps a legacy scope,
        so the setup-shape status branch fires (derived_phase == 'setup') even
        though a real scope exists; it must report that scope's id, not null.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            wdd.mkdir()
            state = new_state("SCOPE-legacy", base_ref="wdd/legacy")
            state["constitution"] = {
                "status": "ratified",
                "ratification": {"by": "ivo", "decisionFingerprint": "sha256:old"},
            }
            StateStore(wdd / "state.json").write(state)
            (wdd / "constitution.md").write_text("# Constitution\n", encoding="utf-8")

            result = migrate_governance(wdd)
            self.assertTrue(result["ratificationInvalidated"])
            migrated_state = StateStore(wdd / "state.json").read()
            self.assertEqual(derived_phase(migrated_state), "setup")
            self.assertIsNotNone(migrated_state["scope"])

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--state", str(wdd / "state.json"), "status", "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["phase"], "setup")
            self.assertEqual(payload["scope"], "SCOPE-legacy")


class EndToEndSetupTest(unittest.TestCase):
    def _cli(self, state: str, *argv: str) -> dict:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["--state", state, *argv])
        self.assertEqual(code, 0, f"wddctl {' '.join(argv)} failed")
        return json.loads(stdout.getvalue())

    def test_full_setup_reaches_start_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            (root / "README.md").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                [
                    "git",
                    "-c", "user.email=t@t",
                    "-c", "user.name=t",
                    "-c", "commit.gpgsign=false",
                    "commit", "-qm", "seed",
                ],
                cwd=root, check=True,
            )
            wdd = root / ".wdd"
            state = str(wdd / "state.json")

            payload = self._cli(state, "init", "--repo", str(root))
            self.assertFalse(payload["alreadyInitialized"])

            payload = self._cli(state, "next")
            self.assertEqual(payload["actions"][0]["action"], "resolve_config")

            self._cli(state, "config", "set", "merge.surface", "local")
            self._cli(state, "config", "set", "models", '{"planning": null, "implementation": {"default": null, "highRisk": null}, "review": null}')
            self._cli(state, "config", "set", "verification.commands", '["true"]')

            payload = self._cli(state, "next")
            self.assertEqual(payload["actions"][0]["action"], "ratify")

            self._cli(state, "constitution", "ratify", "--by", "test")

            # Task 4: create_epic is the ladder's true first rung.
            payload = self._cli(state, "next")
            self.assertEqual(payload["actions"][0]["action"], "create_epic")

            self._cli(state, "epic", "new", "--slug", "demo")
            epic_dir = wdd / "epics" / "demo"

            # Task 5: configure_epic is the ladder's middle rung, between
            # create_epic and agree_spec (spec Sec2).
            payload = self._cli(state, "next")
            self.assertEqual(payload["actions"][0]["action"], "configure_epic")

            self._cli(state, "intake", "configure", "--use-defaults", "--by", "test")

            payload = self._cli(state, "next")
            self.assertEqual(payload["actions"][0]["action"], "agree_spec")

            (epic_dir / "spec.md").write_text(
                "# Spec\n\n## Goal\n\nShip it.\n\n## In scope\n\n- x\n\n"
                "## Out of scope\n\n- y\n\n## Acceptance criteria\n\n"
                "- [ ] AC-1: the thing works\n",
                encoding="utf-8",
            )
            self._cli(state, "intake", "spec", "--approved-by", "test")

            payload = self._cli(state, "next")
            self.assertEqual(payload["actions"][0]["action"], "research")

            self._cli(
                state, "intake", "research", "--skip", "--by", "test",
                "--reason", "no external contracts",
            )

            payload = self._cli(state, "next")
            self.assertEqual(payload["actions"][0]["action"], "agree_design")

            (epic_dir / "design.md").write_text(
                "# Design\n\n## Components\n\n- core\n\n## Interfaces\n\n"
                "- core: consumes nothing, produces lib\n\n"
                "## Integration surfaces\n\n- `src/core.py` — owned by: core task\n\n"
                "## Epic deliverable\n\nThe lib imports.\n",
                encoding="utf-8",
            )
            self._cli(
                state, "intake", "design", "--approved-by", "test", "--deliverable-command", "true"
            )

            payload = self._cli(state, "next")
            self.assertEqual(payload["actions"][0]["action"], "plan")
            self.assertIn("--approved-by", payload["actions"][0]["command"])

            # _minimal_plan's scope id ("SCOPE-demo") is already the
            # epic-derived id -- v6 plan apply rejects any other (Task 4).
            plan_file = root / "plan.json"
            plan = _minimal_plan()
            plan["scope"]["baseRef"] = "wdd/demo"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            (epic_dir / "tasks").mkdir(parents=True, exist_ok=True)
            (epic_dir / "tasks" / "TASK-001-first.md").write_text("# Brief\n", encoding="utf-8")
            self._cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "test",
            )

            payload = self._cli(state, "next", "--repo", str(root))
            actions = [action["action"] for action in payload["actions"]]
            self.assertIn("start_task", actions)


if __name__ == "__main__":
    unittest.main()
