"""Phase-6a intake-ladder tests.

Task 1 slice only: schema v5's required `intake` section, the constructors'
`intake: {}` (never `legacy`), and the v2/v3/v4 -> v5 migration chain that
is the sole producer of `intake: {"legacy": True}`. Later phase-6a tasks
(the `wddctl intake spec/research/design` verbs, the ladder in `next`,
`plan apply`'s composite gate, the execution-gate extension, `scope
archive`, and the e2e journey) add test classes to this same file.

Shared helpers below copy the scratch-repo / `_cli` pattern from
tests/test_finalize.py verbatim (no cross-file imports between test
modules) so later tasks in this file can drive `wddctl intake ...` through
the CLI once those verbs exist.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from wave_delivery.cli import main
from wave_delivery.errors import ValidationError
from wave_delivery.migration import (
    SUPPORTED_SOURCE_VERSIONS,
    apply_migration,
    convert,
    plan_migration,
    read_source,
)
from wave_delivery.schema import (
    SCHEMA_VERSION,
    intake_complete,
    new_setup_state,
    new_state,
    validate_state,
)
from wave_delivery.store import StateStore


def _git_repo(tmp: str) -> Path:
    root = Path(tmp) / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=root, check=True)
    (root / "seed").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "commit", "-qm", "seed"],
        cwd=root, check=True,
    )
    return root


def _cli(state: str, *argv: str) -> tuple[int, str]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = main(["--state", state, *argv])
    return code, stdout.getvalue()


def _v4_state() -> dict:
    """A well-formed v4 state: the current constructor shape minus `intake`,
    stamped back to schemaVersion 4 -- the genuine pre-v5 shape, since v4
    never had the field at all."""
    state = new_state("SCOPE-x", base_ref="wdd/x")
    del state["intake"]
    state["schemaVersion"] = 4
    return state


def _valid_spec_record(**overrides) -> dict:
    record = {"by": "t", "at": "2026-08-01T00:00:00Z", "criteria": 3, "sha256": "sha256:aaa"}
    record.update(overrides)
    return record


def _valid_research_done(**overrides) -> dict:
    record = {
        "by": "t",
        "at": "2026-08-01T00:00:00Z",
        "done": True,
        "artifacts": [{"path": ".wdd/shared-context/contract-inventory.md", "sha256": "sha256:bbb"}],
    }
    record.update(overrides)
    return record


def _valid_research_skipped(**overrides) -> dict:
    record = {
        "by": "t",
        "at": "2026-08-01T00:00:00Z",
        "skipped": True,
        "reason": "no external contracts",
    }
    record.update(overrides)
    return record


def _valid_design_record(**overrides) -> dict:
    record = {
        "by": "t",
        "at": "2026-08-01T00:00:00Z",
        "sha256": "sha256:ccc",
        "deliverableCommand": "true",
    }
    record.update(overrides)
    return record


class ConstructorIntakeTest(unittest.TestCase):
    """new_state() and new_setup_state() must NEVER mint `legacy` -- only
    migrate does (Sol-review P1: a constructor-minted exemption would be a
    doctrine bypass)."""

    def test_schema_version_is_5(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 5)

    def test_new_state_intake_is_empty_not_legacy(self) -> None:
        state = new_state("SCOPE-x")
        self.assertEqual(state["intake"], {})
        validate_state(state)

    def test_new_setup_state_intake_is_empty_not_legacy(self) -> None:
        state = new_setup_state()
        self.assertEqual(state["intake"], {})
        validate_state(state)


class IntakeShapeValidationTest(unittest.TestCase):
    """`validate_state`: intake is a required top-level object; valid shapes
    are `{"legacy": True}` or any subset of `{spec, research, design}`."""

    def _state(self, intake: dict) -> dict:
        state = new_state("SCOPE-x")
        state["intake"] = intake
        return state

    def test_missing_intake_key_is_rejected(self) -> None:
        state = new_state("SCOPE-x")
        del state["intake"]
        with self.assertRaises(ValidationError):
            validate_state(state)

    def test_non_object_intake_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            validate_state(self._state([]))

    def test_empty_intake_is_valid(self) -> None:
        validate_state(self._state({}))

    def test_legacy_shape_is_valid(self) -> None:
        validate_state(self._state({"legacy": True}))

    def test_legacy_false_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            validate_state(self._state({"legacy": False}))

    def test_legacy_combined_with_other_keys_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            validate_state(self._state({"legacy": True, "spec": _valid_spec_record()}))

    def test_unknown_intake_key_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            validate_state(self._state({"bogus": True}))

    def test_each_partial_ladder_subset_is_valid(self) -> None:
        validate_state(self._state({"spec": _valid_spec_record()}))
        validate_state(
            self._state({"spec": _valid_spec_record(), "research": _valid_research_done()})
        )
        validate_state(
            self._state(
                {
                    "spec": _valid_spec_record(),
                    "research": _valid_research_skipped(),
                    "design": _valid_design_record(),
                }
            )
        )
        # A record can be present out of ladder order at the schema level;
        # ordering enforcement belongs to the intake verbs (Task 2), not the
        # shape validator.
        validate_state(self._state({"design": _valid_design_record()}))

    def test_spec_record_requires_all_fields(self) -> None:
        for missing in ("by", "at", "criteria", "sha256"):
            record = _valid_spec_record()
            del record[missing]
            with self.assertRaises(ValidationError):
                validate_state(self._state({"spec": record}))

    def test_spec_criteria_must_be_a_positive_integer(self) -> None:
        for bad in (0, -1, "3", 1.5, True, None):
            with self.assertRaises(ValidationError):
                validate_state(self._state({"spec": _valid_spec_record(criteria=bad)}))

    def test_spec_string_fields_must_be_non_empty(self) -> None:
        for field in ("by", "at", "sha256"):
            with self.assertRaises(ValidationError):
                validate_state(self._state({"spec": _valid_spec_record(**{field: ""})}))

    def test_research_requires_exactly_one_of_done_or_skipped(self) -> None:
        neither = {"by": "t", "at": "2026-08-01T00:00:00Z"}
        with self.assertRaises(ValidationError):
            validate_state(self._state({"research": neither}))
        both = {**_valid_research_done(), **_valid_research_skipped()}
        with self.assertRaises(ValidationError):
            validate_state(self._state({"research": both}))

    def test_research_done_requires_artifacts_list_of_path_and_sha256(self) -> None:
        with self.assertRaises(ValidationError):
            validate_state(self._state({"research": _valid_research_done(artifacts="nope")}))
        with self.assertRaises(ValidationError):
            validate_state(
                self._state({"research": _valid_research_done(artifacts=[{"path": "x"}])})
            )

    def test_research_skipped_requires_a_non_empty_reason(self) -> None:
        with self.assertRaises(ValidationError):
            validate_state(self._state({"research": _valid_research_skipped(reason="")}))
        record = _valid_research_skipped()
        del record["reason"]
        with self.assertRaises(ValidationError):
            validate_state(self._state({"research": record}))

    def test_design_requires_all_fields(self) -> None:
        for missing in ("by", "at", "sha256", "deliverableCommand"):
            record = _valid_design_record()
            del record[missing]
            with self.assertRaises(ValidationError):
                validate_state(self._state({"design": record}))

    def test_design_deliverable_command_must_be_non_empty(self) -> None:
        """The epic deliverable's proof is not optional (spec §2)."""
        with self.assertRaises(ValidationError):
            validate_state(self._state({"design": _valid_design_record(deliverableCommand="")}))
        with self.assertRaises(ValidationError):
            validate_state(self._state({"design": _valid_design_record(deliverableCommand=None)}))


class IntakeCompleteTest(unittest.TestCase):
    """Truth table: legacy is complete wholesale; otherwise all three rungs
    must be recorded."""

    def _state(self, intake: dict) -> dict:
        state = new_state("SCOPE-x")
        state["intake"] = intake
        return state

    def test_legacy_is_complete_even_with_no_records(self) -> None:
        self.assertTrue(intake_complete(self._state({"legacy": True})))

    def test_empty_intake_is_incomplete(self) -> None:
        self.assertFalse(intake_complete(self._state({})))

    def test_partial_ladders_are_incomplete(self) -> None:
        self.assertFalse(intake_complete(self._state({"spec": _valid_spec_record()})))
        self.assertFalse(
            intake_complete(
                self._state({"spec": _valid_spec_record(), "research": _valid_research_done()})
            )
        )

    def test_all_three_records_is_complete(self) -> None:
        self.assertTrue(
            intake_complete(
                self._state(
                    {
                        "spec": _valid_spec_record(),
                        "research": _valid_research_skipped(),
                        "design": _valid_design_record(),
                    }
                )
            )
        )


class MigrationV4ToV5Test(unittest.TestCase):
    """migrate: SUPPORTED_SOURCE_VERSIONS = {2, 3, 4}; v4 -> v5 is a bump
    plus `intake: {"legacy": True}`; earlier conversions still chain
    through it."""

    def test_supported_source_versions(self) -> None:
        self.assertEqual(SUPPORTED_SOURCE_VERSIONS, {2, 3, 4})

    def test_v4_converts_to_v5_with_legacy_intake(self) -> None:
        migrated = convert(_v4_state())
        self.assertEqual(migrated["schemaVersion"], 5)
        self.assertEqual(migrated["intake"], {"legacy": True})
        validate_state(migrated)

    def test_v4_conversion_preserves_scope_and_tasks(self) -> None:
        source = _v4_state()
        source["tasks"]["T1"] = {
            "id": "T1", "title": "T1", "specPath": "tasks/T1.md", "status": "todo",
            "risk": "normal", "dependsOn": [], "conflictDomains": [], "branch": None,
            "worktree": None, "headSha": None, "pr": None, "review": None,
            "verification": None, "freshness": None, "merge": None, "blocker": None,
        }
        migrated = convert(source)
        self.assertEqual(migrated["scope"]["id"], "SCOPE-x")
        self.assertIn("T1", migrated["tasks"])

    def test_v3_and_v2_still_chain_through_to_v5_legacy(self) -> None:
        v3 = deepcopy(_v4_state())
        v3["schemaVersion"] = 3
        migrated = convert(v3)
        self.assertEqual(migrated["schemaVersion"], 5)
        self.assertEqual(migrated["intake"], {"legacy": True})

    def test_version_hint_covers_2_3_and_4(self) -> None:
        for version in (2, 3, 4):
            state = _v4_state()
            state["schemaVersion"] = version
            with self.assertRaises(ValidationError) as raised:
                validate_state(state)
            self.assertIn("migrate --dry-run", str(raised.exception))

    def test_file_based_migration_reports_v4_source_to_v5_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps(_v4_state()), encoding="utf-8")
            result = plan_migration(path)
            self.assertEqual(result["from"], 4)
            self.assertEqual(result["to"], 5)

    def test_apply_migration_writes_a_valid_v5_legacy_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps(_v4_state()), encoding="utf-8")
            apply_migration(path)
            migrated = StateStore(path).read()
            self.assertEqual(migrated["schemaVersion"], 5)
            self.assertEqual(migrated["intake"], {"legacy": True})

    def test_reading_a_v4_file_directly_is_refused_with_a_migration_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps(_v4_state()), encoding="utf-8")
            with self.assertRaises(ValidationError) as raised:
                StateStore(path).read()
            self.assertIn("migrate --dry-run", str(raised.exception))

    def test_read_source_accepts_v4(self) -> None:
        source = read_source_from_dict(_v4_state())
        self.assertEqual(source["schemaVersion"], 4)


def read_source_from_dict(state: dict) -> dict:
    """read_source() takes a path; round-trip through a temp file so the
    same validation path (including the SUPPORTED_SOURCE_VERSIONS check) is
    exercised."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        path.write_text(json.dumps(state), encoding="utf-8")
        return read_source(path)


if __name__ == "__main__":
    unittest.main()
