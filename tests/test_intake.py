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
from wave_delivery.config import load_config
from wave_delivery.errors import IllegalTransition, ValidationError
from wave_delivery.intake import (
    artifact_sha256,
    intake_drift,
    intake_status,
    record_design,
    record_research,
    record_spec,
)
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


def _cli_full(state: str, *argv: str) -> tuple[int, str, str]:
    """Like _cli, but also captures stderr for asserting on refusal messages."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(["--state", state, *argv])
    return code, stdout.getvalue(), stderr.getvalue()


def _plan_document(
    task_ids: tuple[str, ...] | list[str], *, scope_id: str = "SCOPE-x", base_ref: str = "wdd/scope-x"
) -> dict:
    return {
        "schemaVersion": 1,
        "kind": "wdd_plan",
        "scope": {"id": scope_id, "baseRef": base_ref},
        "tasks": [{"id": task_id, "specPath": f"tasks/{task_id}.md"} for task_id in task_ids],
    }


def _ratified_repo(tmp: str) -> tuple[Path, str]:
    """A fresh git repo with .wdd/ initialized and the constitution ratified.

    No scope: intake ladder tests run entirely in the setup phase (scope
    null throughout intake, per the spec) unless a test explicitly hand-
    builds an execute-phase state (the cascade/drift-with-scope-approval
    tests, which simulate Task 3's not-yet-landed plan-apply integration).
    """
    root = _git_repo(tmp)
    wdd = root / ".wdd"
    state = str(wdd / "state.json")
    assert _cli(state, "init", "--repo", str(root))[0] == 0
    assert _cli(state, "config", "set", "merge.surface", "local")[0] == 0
    assert _cli(
        state, "config", "set", "models",
        '{"planning": null, "implementation": {"default": null, "highRisk": null}, "review": null}',
    )[0] == 0
    config = load_config(wdd)
    if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
        assert _cli(state, "config", "set", "verification.commands", '["true"]')[0] == 0
    assert _cli(state, "constitution", "ratify", "--by", "t")[0] == 0
    return root, state


def _spec_text(
    *,
    sections: tuple[str, ...] = ("Goal", "In scope", "Out of scope", "Acceptance criteria"),
    ac_lines: tuple[str, ...] = ("- [ ] AC-1: the thing works",),
) -> str:
    body = {
        "Goal": "Ship it.",
        "In scope": "- x",
        "Out of scope": "- y",
        "Acceptance criteria": "\n".join(ac_lines),
    }
    parts = ["# Spec", ""]
    for name in sections:
        parts += [f"## {name}", "", body.get(name, "content"), ""]
    return "\n".join(parts) + "\n"


def _design_text(
    sections: tuple[str, ...] = (
        "Components",
        "Interfaces",
        "Integration surfaces",
        "Epic deliverable",
    ),
) -> str:
    body = {
        "Components": "- core",
        "Interfaces": "- core: consumes nothing, produces lib",
        "Integration surfaces": "- `src/core.py` — owned by: core task",
        "Epic deliverable": "The lib imports.",
    }
    parts = ["# Design", ""]
    for name in sections:
        parts += [f"## {name}", "", body.get(name, "content"), ""]
    return "\n".join(parts) + "\n"


def _walk_intake(state: str, wdd: Path, approver: str = "t") -> None:
    """Canonical ladder walk (plan Task 2): spec -> research skip -> design.

    Added once per test file per the plan's convention; later phase-6a tasks
    in this same file reuse it to build init->ratify->(ladder) fixtures
    without repeating the raw CLI sequence.
    """
    (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
    assert _cli(state, "intake", "spec", "--approved-by", approver)[0] == 0
    assert _cli(
        state, "intake", "research", "--skip", "--by", approver,
        "--reason", "no external contracts",
    )[0] == 0
    (wdd / "design.md").write_text(_design_text(), encoding="utf-8")
    assert _cli(
        state, "intake", "design", "--approved-by", approver,
        "--deliverable-command", "true",
    )[0] == 0


def _inject_scope_with_approval(state_path: str, *, sha256: str = "sha256:preexisting") -> None:
    """Simulate a plan-applied scope with a recorded composite approval.

    Task 3 (not yet landed) is what would normally produce scope.approval via
    `plan apply --approved-by`; the cascade/drift tests need a state with it
    already present, so this hand-builds it directly via StateStore, per the
    plan's explicit instruction for this task.
    """
    store = StateStore(Path(state_path))
    state = deepcopy(store.read())
    state["scope"] = {
        "id": "SCOPE-x",
        "baseRef": "wdd/scope-x",
        "maxConcurrent": None,
        "reviewPolicy": "risk_based",
        "approval": {"by": "t", "at": "2026-08-01T00:00:00Z", "sha256": sha256},
    }
    store.write(state)


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


class ArtifactSha256Test(unittest.TestCase):
    def test_returns_sha256_prefixed_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.txt"
            path.write_text("hello\n", encoding="utf-8")
            digest = artifact_sha256(path)
            self.assertTrue(digest.startswith("sha256:"))
            self.assertEqual(len(digest), len("sha256:") + 64)

    def test_different_bytes_differ(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.txt"
            b = Path(tmp) / "b.txt"
            a.write_text("a\n", encoding="utf-8")
            b.write_text("b\n", encoding="utf-8")
            self.assertNotEqual(artifact_sha256(a), artifact_sha256(b))


class IntakeSpecVerbTest(unittest.TestCase):
    def test_happy_path_records_criteria_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-1: a", "- [ ] AC-2: b", "- [ ] AC-3: c")),
                encoding="utf-8",
            )
            code, out = _cli(state, "intake", "spec", "--approved-by", "t")
            self.assertEqual(code, 0, out)
            payload = json.loads(out)
            self.assertEqual(payload["criteria"], 3)
            recorded = StateStore(Path(state)).read()["intake"]["spec"]
            self.assertEqual(recorded["by"], "t")
            self.assertEqual(recorded["criteria"], 3)
            self.assertEqual(recorded["sha256"], artifact_sha256(wdd / "spec.md"))

    def test_refuses_when_spec_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _root, state = _ratified_repo(tmp)
            code, _out = _cli(state, "intake", "spec", "--approved-by", "t")
            self.assertNotEqual(code, 0)

    def test_refuses_when_spec_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text("   \n", encoding="utf-8")
            code, _out = _cli(state, "intake", "spec", "--approved-by", "t")
            self.assertNotEqual(code, 0)

    def test_refuses_missing_required_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text(
                _spec_text(sections=("Goal", "In scope", "Acceptance criteria")),
                encoding="utf-8",
            )
            code, _out = _cli(state, "intake", "spec", "--approved-by", "t")
            self.assertNotEqual(code, 0)

    def test_refuses_unnumbered_checklist_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-1: a", "- [ ] do the other thing")),
                encoding="utf-8",
            )
            code, _out = _cli(state, "intake", "spec", "--approved-by", "t")
            self.assertNotEqual(code, 0)

    def test_refuses_duplicate_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-1: a", "- [ ] AC-1: b")),
                encoding="utf-8",
            )
            code, _out = _cli(state, "intake", "spec", "--approved-by", "t")
            self.assertNotEqual(code, 0)

    def test_refuses_non_contiguous_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-1: a", "- [ ] AC-3: b")),
                encoding="utf-8",
            )
            code, _out = _cli(state, "intake", "spec", "--approved-by", "t")
            self.assertNotEqual(code, 0)

    def test_refuses_numbers_not_starting_at_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-2: a", "- [ ] AC-3: b")),
                encoding="utf-8",
            )
            code, _out = _cli(state, "intake", "spec", "--approved-by", "t")
            self.assertNotEqual(code, 0)

    def test_refuses_pre_ratification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            wdd = root / ".wdd"
            state = str(wdd / "state.json")
            assert _cli(state, "init", "--repo", str(root))[0] == 0
            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            code, _out = _cli(state, "intake", "spec", "--approved-by", "t")
            self.assertNotEqual(code, 0)

    def test_refuses_approved_by_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text(_spec_text(), encoding="utf-8")
            code, _out = _cli(state, "intake", "spec", "--approved-by", "")
            self.assertNotEqual(code, 0)

    def test_refuses_on_legacy_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text(_spec_text(), encoding="utf-8")
            store = StateStore(Path(state))
            legacy_state = deepcopy(store.read())
            legacy_state["intake"] = {"legacy": True}
            store.write(legacy_state)
            with self.assertRaises(IllegalTransition):
                record_spec(store, root / ".wdd", approved_by="t")

    def test_refuses_in_delivered_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text(_spec_text(), encoding="utf-8")
            store = StateStore(Path(state))
            delivered_state = deepcopy(store.read())
            delivered_state["scope"] = {
                "id": "SCOPE-x", "baseRef": "wdd/x", "maxConcurrent": None,
                "reviewPolicy": "risk_based",
            }
            delivered_state["tasks"] = {
                "T1": {
                    "id": "T1", "title": "T1", "specPath": "tasks/T1.md", "status": "done",
                    "risk": "normal", "dependsOn": [], "conflictDomains": [], "branch": None,
                    "context": [], "model": None, "reviewModel": None,
                    "worktree": None, "headSha": None, "pr": None, "review": None,
                    "verification": None, "freshness": None, "merge": None, "blocker": None,
                }
            }
            delivered_state["finalize"] = {"delivered": {"at": "2026-08-01T00:00:00Z", "by": "t", "headSha": "abc"}}
            store.write(delivered_state)
            with self.assertRaises(IllegalTransition):
                record_spec(store, root / ".wdd", approved_by="t")


class IntakeResearchVerbTest(unittest.TestCase):
    def test_happy_done_records_artifacts_with_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            (wdd / "shared-context").mkdir(exist_ok=True)
            artifact = wdd / "shared-context" / "contract-inventory.md"
            artifact.write_text("| op | method | ref |\n", encoding="utf-8")
            code, out = _cli(
                state, "intake", "research", "--done", "--by", "t",
                "--artifacts", "shared-context/contract-inventory.md",
            )
            self.assertEqual(code, 0, out)
            recorded = StateStore(Path(state)).read()["intake"]["research"]
            self.assertTrue(recorded["done"])
            self.assertEqual(len(recorded["artifacts"]), 1)
            self.assertEqual(
                recorded["artifacts"][0]["path"], "shared-context/contract-inventory.md"
            )
            self.assertEqual(recorded["artifacts"][0]["sha256"], artifact_sha256(artifact))

    def test_happy_skip_records_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            code, out = _cli(
                state, "intake", "research", "--skip", "--by", "t", "--reason", "no contracts"
            )
            self.assertEqual(code, 0, out)
            recorded = StateStore(Path(state)).read()["intake"]["research"]
            self.assertTrue(recorded["skipped"])
            self.assertEqual(recorded["reason"], "no contracts")

    def test_mode_exclusivity_neither_refused_by_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            code, _out = _cli(state, "intake", "research", "--by", "t")
            self.assertNotEqual(code, 0)

    def test_mode_exclusivity_both_refused_by_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            code, _out = _cli(
                state, "intake", "research", "--done", "--skip", "--by", "t",
                "--artifacts", "x", "--reason", "y",
            )
            self.assertNotEqual(code, 0)

    def test_refuses_before_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _root, state = _ratified_repo(tmp)
            code, _out = _cli(
                state, "intake", "research", "--skip", "--by", "t", "--reason", "no contracts"
            )
            self.assertNotEqual(code, 0)

    def test_refuses_artifact_outside_wdd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            (root / "outside.md").write_text("x\n", encoding="utf-8")
            code, _out = _cli(
                state, "intake", "research", "--done", "--by", "t",
                "--artifacts", "../outside.md",
            )
            self.assertNotEqual(code, 0)

    def test_refuses_artifact_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            code, _out = _cli(
                state, "intake", "research", "--done", "--by", "t",
                "--artifacts", "shared-context/does-not-exist.md",
            )
            self.assertNotEqual(code, 0)

    def test_refuses_artifact_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            (wdd / "shared-context").mkdir(exist_ok=True)
            (wdd / "shared-context" / "empty.md").write_text("", encoding="utf-8")
            code, _out = _cli(
                state, "intake", "research", "--done", "--by", "t",
                "--artifacts", "shared-context/empty.md",
            )
            self.assertNotEqual(code, 0)

    def test_refuses_skip_with_empty_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            code, _out = _cli(state, "intake", "research", "--skip", "--by", "t", "--reason", "")
            self.assertNotEqual(code, 0)


class IntakeDesignVerbTest(unittest.TestCase):
    def test_happy_path_records_deliverable_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            assert _cli(
                state, "intake", "research", "--skip", "--by", "t", "--reason", "no contracts"
            )[0] == 0
            (wdd / "design.md").write_text(_design_text(), encoding="utf-8")
            code, out = _cli(
                state, "intake", "design", "--approved-by", "t",
                "--deliverable-command", "pytest -q",
            )
            self.assertEqual(code, 0, out)
            recorded = StateStore(Path(state)).read()["intake"]["design"]
            self.assertEqual(recorded["deliverableCommand"], "pytest -q")
            self.assertEqual(recorded["sha256"], artifact_sha256(wdd / "design.md"))

    def test_refuses_deliverable_command_omission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            assert _cli(
                state, "intake", "research", "--skip", "--by", "t", "--reason", "no contracts"
            )[0] == 0
            (wdd / "design.md").write_text(_design_text(), encoding="utf-8")
            code, _out = _cli(state, "intake", "design", "--approved-by", "t")
            self.assertNotEqual(code, 0)

    def test_refuses_deliverable_command_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            assert _cli(
                state, "intake", "research", "--skip", "--by", "t", "--reason", "no contracts"
            )[0] == 0
            (wdd / "design.md").write_text(_design_text(), encoding="utf-8")
            code, _out = _cli(
                state, "intake", "design", "--approved-by", "t", "--deliverable-command", "   "
            )
            self.assertNotEqual(code, 0)

    def test_refuses_before_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            (wdd / "design.md").write_text(_design_text(), encoding="utf-8")
            code, _out = _cli(
                state, "intake", "design", "--approved-by", "t", "--deliverable-command", "true"
            )
            self.assertNotEqual(code, 0)

    def test_refuses_missing_design_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            assert _cli(
                state, "intake", "research", "--skip", "--by", "t", "--reason", "no contracts"
            )[0] == 0
            code, _out = _cli(
                state, "intake", "design", "--approved-by", "t", "--deliverable-command", "true"
            )
            self.assertNotEqual(code, 0)

    def test_refuses_missing_design_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            assert _cli(
                state, "intake", "research", "--skip", "--by", "t", "--reason", "no contracts"
            )[0] == 0
            (wdd / "design.md").write_text(
                _design_text(sections=("Components", "Interfaces", "Epic deliverable")),
                encoding="utf-8",
            )
            code, _out = _cli(
                state, "intake", "design", "--approved-by", "t", "--deliverable-command", "true"
            )
            self.assertNotEqual(code, 0)


class IntakeCascadeTest(unittest.TestCase):
    """Full clearing-set matrix: each rung's re-approval clears everything
    downstream of it, including scope.approval once a plan has been applied
    (Task 3 territory, hand-built here per the plan's explicit instruction).
    """

    def test_spec_reapproval_clears_research_design_and_scope_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _walk_intake(state, wdd)
            _inject_scope_with_approval(state)
            before = StateStore(Path(state)).read()
            self.assertIn("approval", before["scope"])
            self.assertIsNotNone(before["intake"]["research"])
            self.assertIsNotNone(before["intake"]["design"])

            (wdd / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-1: a", "- [ ] AC-2: b")), encoding="utf-8"
            )
            code, out = _cli(state, "intake", "spec", "--approved-by", "t")
            self.assertEqual(code, 0, out)

            after = StateStore(Path(state)).read()
            self.assertNotIn("research", after["intake"])
            self.assertNotIn("design", after["intake"])
            self.assertNotIn("approval", after["scope"])
            self.assertEqual(after["intake"]["spec"]["criteria"], 2)

    def test_research_reapproval_clears_design_and_scope_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _walk_intake(state, wdd)
            _inject_scope_with_approval(state)
            before = StateStore(Path(state)).read()
            self.assertIn("approval", before["scope"])
            self.assertIsNotNone(before["intake"]["design"])

            code, out = _cli(
                state, "intake", "research", "--skip", "--by", "t",
                "--reason", "changed my mind about scope",
            )
            self.assertEqual(code, 0, out)

            after = StateStore(Path(state)).read()
            self.assertNotIn("design", after["intake"])
            self.assertNotIn("approval", after["scope"])
            self.assertEqual(
                after["intake"]["research"]["reason"], "changed my mind about scope"
            )
            # spec is untouched by a research re-approval.
            self.assertEqual(after["intake"]["spec"], before["intake"]["spec"])

    def test_design_reapproval_clears_scope_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _walk_intake(state, wdd)
            _inject_scope_with_approval(state)
            before = StateStore(Path(state)).read()
            self.assertIn("approval", before["scope"])

            code, out = _cli(
                state, "intake", "design", "--approved-by", "t",
                "--deliverable-command", "pytest -q",
            )
            self.assertEqual(code, 0, out)

            after = StateStore(Path(state)).read()
            self.assertNotIn("approval", after["scope"])
            self.assertEqual(after["intake"]["design"]["deliverableCommand"], "pytest -q")
            # spec/research untouched by a design re-approval.
            self.assertEqual(after["intake"]["spec"], before["intake"]["spec"])
            self.assertEqual(after["intake"]["research"], before["intake"]["research"])


class IntakeDriftTest(unittest.TestCase):
    def test_none_when_no_records_yet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            current = StateStore(Path(state)).read()
            self.assertIsNone(intake_drift(current, root / ".wdd"))

    def test_none_for_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            store = StateStore(Path(state))
            legacy_state = deepcopy(store.read())
            legacy_state["intake"] = {"legacy": True}
            self.assertIsNone(intake_drift(legacy_state, root / ".wdd"))

    def test_none_when_ladder_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _walk_intake(state, wdd)
            current = StateStore(Path(state)).read()
            self.assertIsNone(intake_drift(current, wdd))

    def test_spec_edited_after_approval_is_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _walk_intake(state, wdd)
            (wdd / "spec.md").write_text(_spec_text(ac_lines=("- [ ] AC-1: changed",)), encoding="utf-8")
            current = StateStore(Path(state)).read()
            drift = intake_drift(current, wdd)
            self.assertEqual(drift["rung"], "spec")

    def test_spec_deleted_after_approval_is_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _walk_intake(state, wdd)
            (wdd / "spec.md").unlink()
            current = StateStore(Path(state)).read()
            drift = intake_drift(current, wdd)
            self.assertEqual(drift["rung"], "spec")
            self.assertEqual(drift["actual"], "missing:spec.md")

    def test_research_artifact_edited_after_approval_is_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            (wdd / "shared-context").mkdir(exist_ok=True)
            artifact = wdd / "shared-context" / "contract-inventory.md"
            artifact.write_text("row one\n", encoding="utf-8")
            assert _cli(
                state, "intake", "research", "--done", "--by", "t",
                "--artifacts", "shared-context/contract-inventory.md",
            )[0] == 0
            artifact.write_text("row one, edited\n", encoding="utf-8")
            current = StateStore(Path(state)).read()
            drift = intake_drift(current, wdd)
            self.assertEqual(drift["rung"], "research")

    def test_research_artifact_deleted_after_approval_is_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            (wdd / "shared-context").mkdir(exist_ok=True)
            artifact = wdd / "shared-context" / "contract-inventory.md"
            artifact.write_text("row one\n", encoding="utf-8")
            assert _cli(
                state, "intake", "research", "--done", "--by", "t",
                "--artifacts", "shared-context/contract-inventory.md",
            )[0] == 0
            artifact.unlink()
            current = StateStore(Path(state)).read()
            drift = intake_drift(current, wdd)
            self.assertEqual(drift["rung"], "research")
            self.assertEqual(drift["actual"], "missing:shared-context/contract-inventory.md")

    def test_design_edited_after_approval_is_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _walk_intake(state, wdd)
            (wdd / "design.md").write_text(
                _design_text() + "\nextra content\n", encoding="utf-8"
            )
            current = StateStore(Path(state)).read()
            drift = intake_drift(current, wdd)
            self.assertEqual(drift["rung"], "design")

    def test_design_deleted_after_approval_is_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _walk_intake(state, wdd)
            (wdd / "design.md").unlink()
            current = StateStore(Path(state)).read()
            drift = intake_drift(current, wdd)
            self.assertEqual(drift["rung"], "design")
            self.assertEqual(drift["actual"], "missing:design.md")


class IntakeStatusTest(unittest.TestCase):
    def test_reports_next_rung_through_the_ladder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            self.assertEqual(intake_status(StateStore(Path(state)).read())["nextRung"], "spec")
            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            self.assertEqual(
                intake_status(StateStore(Path(state)).read())["nextRung"], "research"
            )
            assert _cli(
                state, "intake", "research", "--skip", "--by", "t", "--reason", "n/a"
            )[0] == 0
            self.assertEqual(intake_status(StateStore(Path(state)).read())["nextRung"], "design")
            (wdd / "design.md").write_text(_design_text(), encoding="utf-8")
            assert _cli(
                state, "intake", "design", "--approved-by", "t", "--deliverable-command", "true"
            )[0] == 0
            self.assertIsNone(intake_status(StateStore(Path(state)).read())["nextRung"])

            code, out = _cli(state, "intake", "status")
            self.assertEqual(code, 0, out)
            self.assertIsNone(json.loads(out)["nextRung"])

    def test_legacy_next_rung_is_none(self) -> None:
        state = new_state("SCOPE-x")
        state["intake"] = {"legacy": True}
        self.assertIsNone(intake_status(state)["nextRung"])


class IntakeFunctionLevelRefusalTest(unittest.TestCase):
    """A few refusals exercised directly against the Python API (not just the
    CLI), pinning the exception types the plan's error-message contract implies."""

    def test_record_research_requires_exactly_one_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            store = StateStore(Path(state))
            with self.assertRaises(ValidationError):
                record_research(store, wdd, by="t")
            with self.assertRaises(ValidationError):
                record_research(
                    store, wdd, by="t", done_artifacts=["x"], skip_reason="y"
                )

    def test_record_design_requires_nonempty_deliverable_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _cli(state, "intake", "spec", "--approved-by", "t")
            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            assert _cli(
                state, "intake", "research", "--skip", "--by", "t", "--reason", "n/a"
            )[0] == 0
            (wdd / "design.md").write_text(_design_text(), encoding="utf-8")
            store = StateStore(Path(state))
            with self.assertRaises(ValidationError):
                record_design(store, wdd, approved_by="t", deliverable_command="")
            with self.assertRaises(ValidationError):
                record_design(store, wdd, approved_by="t", deliverable_command="   ")


class SetupLadderNextTest(unittest.TestCase):
    """Task 3: `next` walks agree_spec -> research -> agree_design -> plan,
    one rung at a time, and a drifted rung is re-emitted with `stale: true`."""

    def test_next_walks_the_ladder_then_plan_then_apply_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"

            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            action = json.loads(out)["actions"][0]
            self.assertEqual(action["action"], "agree_spec")
            self.assertIn("recordWith", action)
            self.assertNotIn("stale", action)

            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0

            code, out = _cli(state, "next")
            self.assertEqual(json.loads(out)["actions"][0]["action"], "research")

            assert _cli(
                state, "intake", "research", "--skip", "--by", "t", "--reason", "n/a"
            )[0] == 0

            code, out = _cli(state, "next")
            self.assertEqual(json.loads(out)["actions"][0]["action"], "agree_design")

            (wdd / "design.md").write_text(_design_text(), encoding="utf-8")
            assert _cli(
                state, "intake", "design", "--approved-by", "t", "--deliverable-command", "true"
            )[0] == 0

            code, out = _cli(state, "next")
            self.assertEqual(json.loads(out)["actions"][0]["action"], "plan")

            (wdd / "tasks").mkdir(exist_ok=True)
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan_document(["T1"])), encoding="utf-8")
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, out)

    def test_drifted_rung_is_reemitted_with_stale_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _walk_intake(state, wdd)
            (wdd / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-1: changed",)), encoding="utf-8"
            )
            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            action = json.loads(out)["actions"][0]
            self.assertEqual(action["action"], "agree_spec")
            self.assertTrue(action["stale"])


class PlanApplyGateTest(unittest.TestCase):
    """Task 3: the store-missing bootstrap is gone; non-legacy apply refuses
    on an incomplete or drifted ladder, and on an unapproved nonempty diff."""

    def test_refuses_when_no_state_naming_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _git_repo(tmp)
            state = str(root / ".wdd" / "state.json")
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan_document(["T1"])), encoding="utf-8")
            code, _out, err = _cli_full(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root)
            )
            self.assertNotEqual(code, 0)
            self.assertIn("wddctl init", err)

    def test_refuses_while_intake_ladder_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "tasks").mkdir(exist_ok=True)
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan_document(["T1"])), encoding="utf-8")
            code, _out, err = _cli_full(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertNotEqual(code, 0)
            self.assertIn("intake ladder is incomplete", err)
            self.assertIsNone(StateStore(Path(state)).read()["scope"])

    def test_refuses_on_drifted_rung(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _walk_intake(state, wdd)
            (wdd / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-1: changed",)), encoding="utf-8"
            )
            (wdd / "tasks").mkdir(exist_ok=True)
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan_document(["T1"])), encoding="utf-8")
            code, _out, err = _cli_full(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertNotEqual(code, 0)
            self.assertIn("drifted", err)

    def test_refuses_a_changed_plan_without_approved_by(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _walk_intake(state, wdd)
            (wdd / "tasks").mkdir(exist_ok=True)
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan_document(["T1"])), encoding="utf-8")
            code, _out, err = _cli_full(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root)
            )
            self.assertNotEqual(code, 0)
            self.assertIn("--approved-by", err)
            self.assertIsNone(StateStore(Path(state)).read()["scope"])


class PlanApprovalCompositeTest(unittest.TestCase):
    """Task 3: plan_composite covers the normalized plan + every brief +
    every context file; the recorded composite changes when those bytes do."""

    def _ready(self, tmp: str) -> tuple[Path, Path, str]:
        root, state = _ratified_repo(tmp)
        wdd = root / ".wdd"
        _walk_intake(state, wdd)
        (wdd / "tasks").mkdir(exist_ok=True)
        (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief v1.\n", encoding="utf-8")
        return root, wdd, state

    def test_composite_is_recorded_on_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, _wdd, state = self._ready(tmp)
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan_document(["T1"])), encoding="utf-8")
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, out)
            approval = StateStore(Path(state)).read()["scope"]["approval"]
            self.assertEqual(approval["by"], "t")
            self.assertTrue(approval["sha256"].startswith("sha256:"))

    def test_editing_a_brief_changes_the_composite_on_restamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state = self._ready(tmp)
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan_document(["T1"])), encoding="utf-8")
            assert _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )[0] == 0
            first = StateStore(Path(state)).read()["scope"]["approval"]["sha256"]

            # The brief's bytes change but no MUTABLE_TASK_FIELD does, so
            # _diff_plan alone would call this "unchanged" -- the re-stamp
            # (same --approved-by, same plan file) is exactly how the
            # operator signs off on the edit and moves the composite.
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief v2, edited.\n", encoding="utf-8")
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, out)
            second = StateStore(Path(state)).read()["scope"]["approval"]["sha256"]
            self.assertNotEqual(first, second)

    def test_context_file_bytes_are_covered_by_the_composite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, state = self._ready(tmp)
            (wdd / "shared-context").mkdir(exist_ok=True)
            context_file = wdd / "shared-context" / "notes.md"
            context_file.write_text("v1\n", encoding="utf-8")
            plan = _plan_document(["T1"])
            plan["tasks"][0]["context"] = ["shared-context/notes.md#x"]
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            assert _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )[0] == 0
            first = StateStore(Path(state)).read()["scope"]["approval"]["sha256"]

            context_file.write_text("v2, edited\n", encoding="utf-8")
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, out)
            second = StateStore(Path(state)).read()["scope"]["approval"]["sha256"]
            self.assertNotEqual(first, second)

    def test_context_ref_escaping_wdd_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, _wdd, state = self._ready(tmp)
            (root / "outside.md").write_text("x\n", encoding="utf-8")
            plan = _plan_document(["T1"])
            plan["tasks"][0]["context"] = ["../outside.md"]
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, _out, _err = _cli_full(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertNotEqual(code, 0)

    def test_context_ref_to_a_missing_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, _wdd, state = self._ready(tmp)
            plan = _plan_document(["T1"])
            plan["tasks"][0]["context"] = ["shared-context/does-not-exist.md"]
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, _out, _err = _cli_full(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertNotEqual(code, 0)

    def test_unchanged_reapply_without_flag_preserves_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, _wdd, state = self._ready(tmp)
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan_document(["T1"])), encoding="utf-8")
            assert _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )[0] == 0
            before = StateStore(Path(state)).read()["scope"]["approval"]

            code, out = _cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
            self.assertEqual(code, 0, out)
            after = StateStore(Path(state)).read()["scope"]["approval"]
            self.assertEqual(before, after)


class PlanTaskHandoverFieldsTest(unittest.TestCase):
    """Task 3: context/model/reviewModel are validated at apply and
    persisted into task state (MUTABLE_TASK_FIELDS), so a later edit is a
    detectable diff and the composite is reconstructable from state alone."""

    def test_context_model_reviewmodel_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _walk_intake(state, wdd)
            (wdd / "tasks").mkdir(exist_ok=True)
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
            (wdd / "shared-context").mkdir(exist_ok=True)
            (wdd / "shared-context" / "notes.md").write_text("row\n", encoding="utf-8")
            plan = _plan_document(["T1"])
            plan["tasks"][0]["context"] = ["shared-context/notes.md#x", "spec.md#AC-1"]
            plan["tasks"][0]["model"] = "qwen-local"
            plan["tasks"][0]["reviewModel"] = "opus"
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, out)
            task = StateStore(Path(state)).read()["tasks"]["T1"]
            self.assertEqual(task["context"], ["shared-context/notes.md#x", "spec.md#AC-1"])
            self.assertEqual(task["model"], "qwen-local")
            self.assertEqual(task["reviewModel"], "opus")

    def test_changing_model_on_reapply_is_a_diff_requiring_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _walk_intake(state, wdd)
            (wdd / "tasks").mkdir(exist_ok=True)
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
            plan = _plan_document(["T1"])
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            assert _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )[0] == 0

            plan["tasks"][0]["model"] = "qwen-local"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            code, _out, _err = _cli_full(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root)
            )
            self.assertNotEqual(code, 0)

            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, out)
            self.assertEqual(StateStore(Path(state)).read()["tasks"]["T1"]["model"], "qwen-local")


class LegacyPlanApplyRegressionTest(unittest.TestCase):
    """Task 3 pin: a migrated (legacy) scope's plan apply is bit-for-bit
    unaffected -- no ladder, no composite, no approval requirement."""

    def test_legacy_scope_applies_without_ladder_brief_or_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            store = StateStore(Path(state))
            legacy_state = deepcopy(store.read())
            legacy_state["intake"] = {"legacy": True}
            store.write(legacy_state)

            # No spec.md/design.md, no ladder walked, no brief file on disk,
            # no --approved-by: exactly the pre-v5 apply_plan contract.
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan_document(["T1"])), encoding="utf-8")
            code, out = _cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
            self.assertEqual(code, 0, out)
            after = StateStore(Path(state)).read()
            self.assertIsNotNone(after["scope"])
            self.assertIn("T1", after["tasks"])
            self.assertIsNone(after["scope"].get("approval"))

    def test_legacy_next_never_shows_ladder_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            store = StateStore(Path(state))
            legacy_state = deepcopy(store.read())
            legacy_state["intake"] = {"legacy": True}
            store.write(legacy_state)

            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)["actions"][0]["action"], "plan")


if __name__ == "__main__":
    unittest.main()
