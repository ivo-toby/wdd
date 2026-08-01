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
from wave_delivery.plan import intake_gate_status, plan_composite, require_fresh_intake
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
    task_ids: tuple[str, ...] | list[str],
    *,
    scope_id: str = "SCOPE-x",
    base_ref: str = "wdd/scope-x",
    review_policy: str | None = None,
) -> dict:
    scope: dict = {"id": scope_id, "baseRef": base_ref}
    if review_policy is not None:
        scope["reviewPolicy"] = review_policy
    return {
        "schemaVersion": 1,
        "kind": "wdd_plan",
        "scope": scope,
        "tasks": [{"id": task_id, "specPath": f"tasks/{task_id}.md"} for task_id in task_ids],
    }


def _passed_results(*commands: str) -> str:
    """JSON --results payload marking every given command passed, in order --
    the v5 multi-command `finalize verify record` contract (Task 5)."""
    return json.dumps([{"command": command, "status": "passed"} for command in commands])


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


def _apply_ladder_and_plan(
    state: str, wdd: Path, root: Path, *, review_policy: str = "risk_based", approver: str = "t"
) -> None:
    """`_walk_intake` + a one-task, composite-approved `plan apply` (Task 4).

    The execute/finalize-phase drift tests below need a REAL (non-legacy)
    applied-and-approved scope to exercise `require_fresh_intake` against --
    unlike test_finalize.py's `_mark_legacy` shortcut, which deliberately
    exempts fixtures that aren't exercising the ladder.
    """
    _walk_intake(state, wdd, approver)
    (wdd / "tasks").mkdir(exist_ok=True)
    (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
    plan_file = root / "plan.json"
    plan_file.write_text(
        json.dumps(_plan_document(["T1"], review_policy=review_policy)), encoding="utf-8"
    )
    code, out = _cli(
        state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
        "--approved-by", approver,
    )
    assert code == 0, out


def _start_and_commit(state: str, root: Path, task_id: str = "T1", message: str = "do work") -> None:
    code, out = _cli(state, "start", "--task", task_id, "--repo", str(root))
    assert code == 0, out
    worktree = Path(json.loads(out)["worktree"])
    (worktree / "change.txt").write_text(f"{message}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=worktree, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "commit", "-qm", message],
        cwd=worktree, check=True,
    )


def _run_to_finalize(tmp: str) -> tuple[Path, str]:
    """Drive a non-legacy, composite-approved scope through one local task
    to the finalize phase, for Task 4's finalize-governed-verb drift pin."""
    root, state = _ratified_repo(tmp)
    bare = Path(tmp) / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=root, check=True)
    wdd = root / ".wdd"
    _apply_ladder_and_plan(state, wdd, root, review_policy="always")
    _start_and_commit(state, root)
    assert _cli(state, "submit", "--task", "T1", "--repo", str(root))[0] == 0
    assert _cli(
        state, "review", "record", "--task", "T1", "--reviewer", "t", "--findings", "[]"
    )[0] == 0
    assert _cli(state, "verify", "record", "--task", "T1", "--status", "passed")[0] == 0
    assert _cli(state, "freshness", "record", "--task", "T1", "--repo", str(root))[0] == 0
    assert _cli(state, "merge", "--task", "T1", "--repo", str(root))[0] == 0

    from wave_delivery.schema import derived_phase

    scope_state = StateStore(Path(state)).read()
    assert derived_phase(scope_state) == "finalize", scope_state
    return root, state


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

    def test_composite_is_task_order_invariant(self) -> None:
        """Finding 1 (review of 34c8eed): json.dumps(sort_keys=True) only
        orders each dict's keys, not the `tasks` array -- the same plan with
        tasks listed in a different order must still hash identically,
        since _apply_plan_to_state inserts tasks id-sorted regardless of
        plan-file order (a mismatched sort here would cause permanent
        false-positive plan drift once Task 4 recomputes the composite from
        state)."""
        with tempfile.TemporaryDirectory() as tmp:
            root, wdd, _state = self._ready(tmp)
            (wdd / "tasks" / "T2.md").write_text("# T2\n\nBrief.\n", encoding="utf-8")
            plan_forward = _plan_document(["T1", "T2"])
            plan_reversed = _plan_document(["T2", "T1"])
            composite_forward = plan_composite(plan_forward, wdd)
            composite_reversed = plan_composite(plan_reversed, wdd)
            self.assertEqual(composite_forward, composite_reversed)


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

    def test_legacy_changed_plan_without_approved_by_carries_prior_approval_forward(
        self,
    ) -> None:
        """Finding 2 (review of 34c8eed): the exact pre-doctrine contract on a
        legacy scope -- a prior scope.approval recorded (the v4 migrated
        shape, which predates the sha256 composite field and so never has
        one), then a CHANGED plan re-applied WITHOUT --approved-by must still
        succeed and must leave the old approval dict untouched (silent
        carry-forward is the documented legacy behavior, not a regression).
        This also pins that `validate_state` tolerates `scope.approval`
        lacking `sha256` -- required for a migrated v4 state, whose approval
        shape predates this task's composite field, to remain valid."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"

            # Mark legacy before the first apply -- a non-legacy scope would
            # refuse this apply outright (ladder incomplete). This is the
            # architecture-note-sanctioned way to hand-build a "migrated"
            # scope in a test: set state["intake"] explicitly, never mint it
            # via a constructor.
            store = StateStore(Path(state))
            legacy_state = deepcopy(store.read())
            legacy_state["intake"] = {"legacy": True}
            store.write(legacy_state)

            (wdd / "tasks").mkdir(exist_ok=True)
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan_document(["T1"])), encoding="utf-8")
            # A legacy apply's composite is always None (apply_plan), so
            # --approved-by on a legacy scope stamps {by, at} with NO
            # sha256 -- exactly the v4-migrated approval shape that predates
            # this task's composite field. This also exercises
            # validate_state's tolerance for a sha256-less scope.approval.
            assert _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "old",
            )[0] == 0
            legacy_approval = StateStore(Path(state)).read()["scope"]["approval"]
            self.assertNotIn("sha256", legacy_approval)

            (wdd / "tasks" / "T2.md").write_text("# T2\n\nBrief.\n", encoding="utf-8")
            changed_plan = _plan_document(["T1", "T2"])
            plan_file.write_text(json.dumps(changed_plan), encoding="utf-8")
            code, out = _cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
            self.assertEqual(code, 0, out)

            after = StateStore(Path(state)).read()
            self.assertIn("T2", after["tasks"])
            self.assertEqual(after["scope"]["approval"], legacy_approval)


class ExecutionGateIntakeDriftTest(unittest.TestCase):
    """Task 4: `require_fresh_intake` extends the execution gate -- a spec.md
    edit after `plan apply` refuses every governed verb, not just planning,
    until the full remedy walk (re-approve the drifted rung, then re-stamp
    `plan apply --approved-by`) restores it."""

    def test_post_apply_spec_edit_blocks_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_ladder_and_plan(state, wdd, root)

            (wdd / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-1: changed",)), encoding="utf-8"
            )
            code, _out, err = _cli_full(state, "start", "--task", "T1", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("intake drift", err)
            self.assertIn("spec", err)

    def test_execute_phase_next_shows_intake_drift_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_ladder_and_plan(state, wdd, root)

            (wdd / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-1: changed",)), encoding="utf-8"
            )
            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["actions"], [])
            blocker = result["blockers"][0]
            self.assertEqual(blocker["code"], "intake_drift")
            self.assertEqual(blocker["rung"], "spec")

    def test_full_remedy_walk_restores_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_ladder_and_plan(state, wdd, root)

            (wdd / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-1: changed",)), encoding="utf-8"
            )
            self.assertNotEqual(
                _cli(state, "start", "--task", "T1", "--repo", str(root))[0], 0
            )

            # Cascade: re-approving spec clears research + design (recorded
            # by _apply_ladder_and_plan's _walk_intake), so both must be
            # re-recorded before the ladder is complete again.
            assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
            assert _cli(
                state, "intake", "research", "--skip", "--by", "t",
                "--reason", "no external contracts",
            )[0] == 0
            assert _cli(
                state, "intake", "design", "--approved-by", "t",
                "--deliverable-command", "true",
            )[0] == 0

            # The re-approval cascade also cleared scope.approval; an
            # unchanged plan file re-stamps it (empty-diff --approved-by,
            # Task 3's behavior) without needing a re-plan.
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan_document(["T1"])), encoding="utf-8")
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, out)

            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)


class ExecutionGatePlanDriftTest(unittest.TestCase):
    """Task 4: a brief edit after composite approval is plan drift -- caught
    by the same gate, remedied by a flagged, otherwise-empty-diff re-apply
    (the brief edit is invisible to `_diff_plan`, so `--approved-by` on the
    unchanged plan file is what moves the composite, per Task 3)."""

    def test_brief_edit_blocks_start_with_plan_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_ladder_and_plan(state, wdd, root)

            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief, edited.\n", encoding="utf-8")
            code, _out, err = _cli_full(state, "start", "--task", "T1", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("plan drift", err)

    def test_execute_phase_next_shows_plan_drift_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_ladder_and_plan(state, wdd, root)

            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief, edited.\n", encoding="utf-8")
            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["actions"], [])
            self.assertEqual(result["blockers"][0]["code"], "plan_drift")

    def test_reapply_approved_by_with_empty_diff_restores_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_ladder_and_plan(state, wdd, root)

            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief, edited.\n", encoding="utf-8")
            self.assertNotEqual(
                _cli(state, "start", "--task", "T1", "--repo", str(root))[0], 0
            )

            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan_document(["T1"])), encoding="utf-8")
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, out)
            self.assertTrue(json.loads(out)["unchanged"])

            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)


class ExecutionGateLegacyPinTest(unittest.TestCase):
    """Task 4 pin: a migrated legacy scope is wholesale exempt from the new
    gate, exactly like it is from the ladder and the composite (spec Sec7)."""

    def test_legacy_scope_start_unaffected_by_spec_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            store = StateStore(Path(state))
            legacy_state = deepcopy(store.read())
            legacy_state["intake"] = {"legacy": True}
            store.write(legacy_state)

            (wdd / "tasks").mkdir(exist_ok=True)
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(_plan_document(["T1"])), encoding="utf-8")
            assert _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root)
            )[0] == 0

            # No spec.md ever existed for this legacy scope, and its
            # scope.approval carries no sha256 (the v4-migrated shape) --
            # both would be "drift" for a non-legacy scope, but the gate
            # must no-op wholesale for legacy ones.
            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)


class ExecutionGateFinalizeDriftTest(unittest.TestCase):
    """Task 4: the finalize-phase drift gate isn't limited to execute-phase
    verbs -- a rung re-approval or a spec edit during finalize clears/drifts
    scope.approval, and finalize verbs (already governed) refuse until the
    plan is re-stamped."""

    def test_spec_edit_during_finalize_blocks_finalize_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_finalize(tmp)
            wdd = root / ".wdd"

            (wdd / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-1: changed",)), encoding="utf-8"
            )
            code, _out, err = _cli_full(
                state, "finalize", "verify", "record",
                "--results", _passed_results("true", "true"), "--repo", str(root),
            )
            self.assertNotEqual(code, 0)
            self.assertIn("intake drift", err)

    def test_finalize_phase_next_shows_intake_drift_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_finalize(tmp)
            wdd = root / ".wdd"

            (wdd / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-1: changed",)), encoding="utf-8"
            )
            code, out = _cli(state, "next", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["actions"], [])
            self.assertEqual(result["blockers"][0]["code"], "intake_drift")

    def test_rung_reapproval_during_finalize_blocks_finalize_until_replan(self) -> None:
        """Task-2 review's design note: a rung re-approval during finalize
        clears scope.approval via the intake cascade, so finalize verbs
        refuse (plan_drift, no composite approval) until a fresh `plan apply
        --approved-by` re-stamps -- even though nothing about the rung's own
        bytes drifted (a legitimate re-approval, not an edit-behind-its-back).

        Re-approves `design` (the ladder's last rung) specifically: its
        cascade only clears `scope.approval` (nothing downstream to clear),
        so the ladder stays complete and only the plan-composite gate fires
        -- isolating this case from the incomplete-ladder refusal a spec/
        research re-approval's wider cascade would also trigger.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_finalize(tmp)

            assert _cli(
                state, "intake", "design", "--approved-by", "t",
                "--deliverable-command", "true",
            )[0] == 0
            code, _out, err = _cli_full(
                state, "finalize", "verify", "record",
                "--results", _passed_results("true", "true"), "--repo", str(root),
            )
            self.assertNotEqual(code, 0)
            self.assertIn("plan drift", err)

            plan_file = root / "plan.json"
            plan_file.write_text(
                json.dumps(_plan_document(["T1"], review_policy="always")), encoding="utf-8"
            )
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, out)

            code, out, err = _cli_full(
                state, "finalize", "verify", "record",
                "--results", _passed_results("true", "true"), "--repo", str(root),
            )
            self.assertEqual(code, 0, out + err)


class IntakeGateStatusUnitTest(unittest.TestCase):
    """Task 4: `intake_gate_status`/`require_fresh_intake` unit-level, for
    the shape that can't arise through the CLI's own apply path -- a
    non-legacy scope whose approval was never composite-stamped at all
    (missing, or present without a sha256). A non-legacy `plan apply`
    always requires `--approved-by` on a nonempty diff (Task 3), so this
    state could only be produced by direct state surgery -- exactly what it
    is here -- never by the CLI itself; the gate still must not treat it as
    a legitimate steady state."""

    def _ready_applied_state(self, tmp: str) -> tuple[dict, Path]:
        root, state = _ratified_repo(tmp)
        wdd = root / ".wdd"
        _apply_ladder_and_plan(state, wdd, root)
        current = StateStore(Path(state)).read()
        return current, wdd

    def test_missing_approval_is_plan_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current, wdd = self._ready_applied_state(tmp)
            current = deepcopy(current)
            del current["scope"]["approval"]
            gate = intake_gate_status(current, wdd)
            self.assertIsNotNone(gate)
            code, _detail = gate
            self.assertEqual(code, "plan_drift")
            with self.assertRaises(IllegalTransition) as ctx:
                require_fresh_intake(current, wdd)
            self.assertIn("plan drift", str(ctx.exception))
            self.assertIn("never composite-approved", str(ctx.exception))

    def test_approval_without_sha256_is_plan_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current, wdd = self._ready_applied_state(tmp)
            current = deepcopy(current)
            current["scope"]["approval"] = {"by": "t", "at": "2026-08-01T00:00:00Z"}
            gate = intake_gate_status(current, wdd)
            self.assertEqual(gate[0], "plan_drift")

    def test_matching_composite_is_no_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current, wdd = self._ready_applied_state(tmp)
            self.assertIsNone(intake_gate_status(current, wdd))
            require_fresh_intake(current, wdd)  # must not raise

    def test_legacy_state_is_never_gated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current, wdd = self._ready_applied_state(tmp)
            current = deepcopy(current)
            current["intake"] = {"legacy": True}
            del current["scope"]["approval"]
            self.assertIsNone(intake_gate_status(current, wdd))
            require_fresh_intake(current, wdd)  # must not raise


class CompositeHashesEffectivePlanTest(unittest.TestCase):
    """Task 4 fix round CRITICAL: apply_plan's recorded composite must be
    computed from the EFFECTIVE (post-apply) plan -- via the same
    `_reconstruct_plan_from_state` the gate uses -- not the raw submitted
    plan dict. baseRef/mergeSurface/mergeMode are "omission means keep":
    a legitimate re-apply that omits them must not manufacture a false,
    unrecoverable plan_drift that locks every governed verb.
    """

    def _first_apply_with_overrides(self, state: str, wdd: Path, root: Path) -> None:
        _walk_intake(state, wdd)
        (wdd / "tasks").mkdir(exist_ok=True)
        (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
        plan = {
            "schemaVersion": 1,
            "kind": "wdd_plan",
            "scope": {
                "id": "SCOPE-x",
                "baseRef": "wdd/scope-x",
                "mergeSurface": "local",
                "mergeMode": "controller",
            },
            "tasks": [{"id": "T1", "specPath": "tasks/T1.md"}],
        }
        plan_file = root / "plan.json"
        plan_file.write_text(json.dumps(plan), encoding="utf-8")
        code, out = _cli(
            state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
            "--approved-by", "t",
        )
        assert code == 0, out

    def test_reapply_with_task_change_omitting_overrides_is_not_drift(self) -> None:
        """Nonempty-diff re-apply path (the `mutator` closure): a task field
        change forces a real apply (not the unchanged-diff shortcut), while
        the scope omits baseRef/mergeSurface/mergeMode entirely."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            self._first_apply_with_overrides(state, wdd, root)

            plan2 = {
                "schemaVersion": 1,
                "kind": "wdd_plan",
                "scope": {"id": "SCOPE-x"},
                "tasks": [{"id": "T1", "specPath": "tasks/T1.md", "title": "T1 renamed"}],
            }
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan2), encoding="utf-8")
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, out)

            current = StateStore(Path(state)).read()
            self.assertIsNone(intake_gate_status(current, wdd))

            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)

    def test_unchanged_reapply_omitting_overrides_restamp_is_not_drift(self) -> None:
        """Unchanged-diff + --approved-by re-stamp path (`approval_mutator`):
        an otherwise identical plan that omits baseRef/mergeSurface/
        mergeMode must still re-stamp a composite matching the kept
        (effective) scope, not "absent" overrides."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            self._first_apply_with_overrides(state, wdd, root)

            plan2 = {
                "schemaVersion": 1,
                "kind": "wdd_plan",
                "scope": {"id": "SCOPE-x"},
                "tasks": [{"id": "T1", "specPath": "tasks/T1.md"}],
            }
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan2), encoding="utf-8")
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, out)
            self.assertTrue(json.loads(out)["unchanged"])

            current = StateStore(Path(state)).read()
            self.assertIsNone(intake_gate_status(current, wdd))

            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)

    def test_reviewer_repro_omit_baseref_on_second_apply_is_not_drift(self) -> None:
        """Regression pin for the reviewer's exact live repro: a second apply
        that omits ONLY baseRef (mergeSurface/mergeMode repeated verbatim)
        must not drift."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            self._first_apply_with_overrides(state, wdd, root)

            plan2 = {
                "schemaVersion": 1,
                "kind": "wdd_plan",
                "scope": {
                    "id": "SCOPE-x",
                    "mergeSurface": "local",
                    "mergeMode": "controller",
                },
                "tasks": [{"id": "T1", "specPath": "tasks/T1.md"}],
            }
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps(plan2), encoding="utf-8")
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, out)

            current = StateStore(Path(state)).read()
            self.assertIsNone(intake_gate_status(current, wdd))
            require_fresh_intake(current, wdd)  # must not raise

            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)


class BothDriftBlockerOrderTest(unittest.TestCase):
    """Task 4 fix round IMPORTANT: when both governance and intake/plan
    drift are present, `next`'s blockers[0] must be governance_drift --
    matching the chokepoint's raise order (`require_fresh_governance` then
    `require_fresh_intake` in `main()`), not whichever gate happened to
    insert last."""

    def test_both_drift_blocker_zero_is_governance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _apply_ladder_and_plan(state, wdd, root)

            # Intake/plan drift: edit the spec after the ladder + plan were
            # approved.
            (wdd / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-1: changed",)), encoding="utf-8"
            )
            # Governance drift: constitution ratified against a config that
            # has since been amended without re-ratifying.
            assert _cli(state, "config", "set", "review.blockingSeverities", '["P1"]')[0] == 0

            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["actions"], [])
            codes = [b["code"] for b in result["blockers"]]
            self.assertIn("governance_drift", codes)
            self.assertIn("intake_drift", codes)
            self.assertEqual(result["blockers"][0]["code"], "governance_drift")


def _run_to_finalize_legacy(tmp: str) -> tuple[Path, str]:
    """Drive a legacy (`intake.legacy`) scope through one local task to the
    finalize phase -- Task 5's backward-compat fixture for the single-command
    `finalize verify record` contract, which legacy scopes keep untouched.
    Mirrors test_finalize.py's own legacy-marking pattern (mark legacy right
    after ratify, apply the plan without walking the ladder)."""
    root, state = _ratified_repo(tmp)
    bare = Path(tmp) / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=root, check=True)
    wdd = root / ".wdd"
    store = StateStore(Path(state))
    current = deepcopy(store.read())
    current["intake"] = {"legacy": True}
    store.write(current)
    (wdd / "tasks").mkdir(exist_ok=True)
    (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
    plan_file = root / "plan.json"
    plan_file.write_text(
        json.dumps(_plan_document(["T1"], review_policy="always")), encoding="utf-8"
    )
    code, out = _cli(state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root))
    assert code == 0, out
    _start_and_commit(state, root)
    assert _cli(state, "submit", "--task", "T1", "--repo", str(root))[0] == 0
    assert _cli(
        state, "review", "record", "--task", "T1", "--reviewer", "t", "--findings", "[]"
    )[0] == 0
    assert _cli(state, "verify", "record", "--task", "T1", "--status", "passed")[0] == 0
    assert _cli(state, "freshness", "record", "--task", "T1", "--repo", str(root))[0] == 0
    assert _cli(state, "merge", "--task", "T1", "--repo", str(root))[0] == 0

    from wave_delivery.schema import derived_phase

    scope_state = StateStore(Path(state)).read()
    assert derived_phase(scope_state) == "finalize", scope_state
    return root, state


class FinalVerificationMultiCommandTest(unittest.TestCase):
    """Task 5, spec Sec5: v5 non-legacy scopes record final verification as
    an ordered `[{command, status}]` list, in ONE atomic `--results` call,
    validated for exact completeness against the required list (global
    verification.commands then the scope's deliverable command)."""

    def test_all_passed_results_in_overall_passed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_finalize(tmp)
            assert _cli(
                state, "finalize", "review", "record", "--reviewer", "t", "--findings", "[]",
                "--repo", str(root),
            )[0] == 0
            code, out = _cli(
                state, "finalize", "verify", "record",
                "--results", _passed_results("true", "true"), "--repo", str(root),
            )
            self.assertEqual(code, 0, out)
            verification = StateStore(Path(state)).read()["finalize"]["verification"]
            self.assertEqual(verification["status"], "passed")
            self.assertEqual(
                verification["commands"],
                [{"command": "true", "status": "passed"}, {"command": "true", "status": "passed"}],
            )

    def test_any_failed_entry_makes_overall_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_finalize(tmp)
            results = json.dumps(
                [{"command": "true", "status": "passed"}, {"command": "true", "status": "failed"}]
            )
            code, out = _cli(
                state, "finalize", "verify", "record", "--results", results, "--repo", str(root)
            )
            self.assertEqual(code, 0, out)
            verification = StateStore(Path(state)).read()["finalize"]["verification"]
            self.assertEqual(verification["status"], "failed")

    def test_missing_entry_is_refused_and_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_finalize(tmp)
            results = json.dumps([{"command": "true", "status": "passed"}])
            code, _out, err = _cli_full(
                state, "finalize", "verify", "record", "--results", results, "--repo", str(root)
            )
            self.assertNotEqual(code, 0)
            self.assertIn("missing", err)

    def test_extra_entry_is_refused_and_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_finalize(tmp)
            results = json.dumps(
                [
                    {"command": "true", "status": "passed"},
                    {"command": "true", "status": "passed"},
                    {"command": "echo extra", "status": "passed"},
                ]
            )
            code, _out, err = _cli_full(
                state, "finalize", "verify", "record", "--results", results, "--repo", str(root)
            )
            self.assertNotEqual(code, 0)
            self.assertIn("extra", err)

    def test_reordered_entries_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_finalize(tmp)
            # A required list of two distinct commands so reordering is
            # observable: override the deliverable command via a fresh
            # design re-approval (only clears scope.approval), then re-stamp.
            (root / ".wdd" / "design.md").write_text(_design_text(), encoding="utf-8")
            assert _cli(
                state, "intake", "design", "--approved-by", "t",
                "--deliverable-command", "echo deliverable",
            )[0] == 0
            plan_file = root / "plan.json"
            plan_file.write_text(
                json.dumps(_plan_document(["T1"], review_policy="always")), encoding="utf-8"
            )
            assert _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )[0] == 0
            results = json.dumps(
                [
                    {"command": "echo deliverable", "status": "passed"},
                    {"command": "true", "status": "passed"},
                ]
            )
            code, _out, err = _cli_full(
                state, "finalize", "verify", "record", "--results", results, "--repo", str(root)
            )
            self.assertNotEqual(code, 0)
            self.assertIn("expected order", err)

    def test_legacy_status_command_args_refused_on_v5_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_finalize(tmp)
            code, _out, err = _cli_full(
                state, "finalize", "verify", "record", "--status", "passed",
                "--command", "true", "--repo", str(root),
            )
            self.assertNotEqual(code, 0)
            self.assertIn("v5 scope", err)

    def test_v5_scope_without_results_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_finalize(tmp)
            code, _out, err = _cli_full(
                state, "finalize", "verify", "record", "--repo", str(root)
            )
            self.assertNotEqual(code, 0)
            self.assertIn("requires --results", err)


class FinalVerificationLegacyContractTest(unittest.TestCase):
    """Task 5: legacy scopes keep the pre-existing single-command
    `--status`/`--command` contract untouched -- a regression pin -- and
    `--results` is refused there."""

    def test_legacy_single_command_contract_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_finalize_legacy(tmp)
            code, out = _cli(
                state, "finalize", "verify", "record", "--status", "passed",
                "--command", "pytest -q", "--repo", str(root),
            )
            self.assertEqual(code, 0, out)
            verification = StateStore(Path(state)).read()["finalize"]["verification"]
            self.assertEqual(verification["status"], "passed")
            self.assertEqual(verification["command"], "pytest -q")
            self.assertNotIn("commands", verification)

    def test_results_arg_refused_on_legacy_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_finalize_legacy(tmp)
            code, _out, err = _cli_full(
                state, "finalize", "verify", "record",
                "--results", _passed_results("pytest -q"), "--repo", str(root),
            )
            self.assertNotEqual(code, 0)
            self.assertIn("legacy scope", err)


class VerificationCommandsReadSiteTest(unittest.TestCase):
    """Task 5: `verification_commands` normalizes both evidence shapes to the
    one-entry-list-or-more view every read site (handoff summary, `finalize
    status`) shares, so legacy records read as the one-entry list they
    always were."""

    def test_legacy_shape_normalizes_to_one_entry(self) -> None:
        from wave_delivery.finalize import verification_commands

        legacy = {"headSha": "abc", "status": "passed", "command": "pytest -q", "at": "t"}
        self.assertEqual(
            verification_commands(legacy), [{"command": "pytest -q", "status": "passed"}]
        )

    def test_v5_shape_passes_through(self) -> None:
        from wave_delivery.finalize import verification_commands

        v5 = {
            "headSha": "abc",
            "status": "passed",
            "commands": [{"command": "true", "status": "passed"}],
            "at": "t",
        }
        self.assertEqual(verification_commands(v5), v5["commands"])

    def test_empty_or_none_normalizes_to_empty_list(self) -> None:
        from wave_delivery.finalize import verification_commands

        self.assertEqual(verification_commands(None), [])
        self.assertEqual(verification_commands({}), [])


class FinalReviewJudgmentTest(unittest.TestCase):
    """Task 5, spec Sec5's finalize tie-in: the `final_review` judgment names
    AC-1..AC-N (from `intake.spec.criteria`) and design.md's epic deliverable
    for non-legacy scopes; legacy scopes keep the original prose (pin)."""

    def test_non_legacy_judgment_names_criteria_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_finalize(tmp)
            code, out = _cli(state, "next", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            action = result["actions"][0]
            self.assertEqual(action["action"], "final_review")
            criteria = StateStore(Path(state)).read()["intake"]["spec"]["criteria"]
            self.assertEqual(criteria, 1)
            self.assertIn("AC-1", action["judgment"])
            self.assertIn("design.md", action["judgment"])
            self.assertIn("epic deliverable", action["judgment"])

    def test_legacy_judgment_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_finalize_legacy(tmp)
            code, out = _cli(state, "next", "--repo", str(root))
            self.assertEqual(code, 0, out)
            action = json.loads(out)["actions"][0]
            self.assertEqual(action["action"], "final_review")
            self.assertNotIn("AC-1", action["judgment"])
            self.assertEqual(
                action["judgment"],
                "dispatch a reviewer against the whole epic branch diff, per wdd-review's "
                "final-review contract, checked against .wdd/spec.md",
            )


def _run_to_delivered(tmp: str) -> tuple[Path, str]:
    """`_run_to_finalize` walked the rest of the way to `delivered`, via the
    v5 multi-command `--results` contract -- the scope-archive tests' shared
    fixture."""
    root, state = _run_to_finalize(tmp)
    assert _cli(
        state, "finalize", "review", "record", "--reviewer", "t", "--findings", "[]",
        "--repo", str(root),
    )[0] == 0
    code, out = _cli(
        state, "finalize", "verify", "record",
        "--results", _passed_results("true", "true"), "--repo", str(root),
    )
    assert code == 0, out
    code, out = _cli(state, "finalize", "handoff", "--repo", str(root))
    assert code == 0, out
    base_ref = StateStore(Path(state)).read()["scope"]["baseRef"]
    subprocess.run(["git", "checkout", "-q", "main"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
         "merge", "--no-ff", "-m", "final merge", base_ref],
        cwd=root, check=True,
    )
    code, out = _cli(state, "finalize", "delivered", "--by", "bob", "--repo", str(root))
    assert code == 0, out

    from wave_delivery.schema import derived_phase

    assert derived_phase(StateStore(Path(state)).read()) == "delivered"
    return root, state


class ScopeArchiveTest(unittest.TestCase):
    """Task 5, spec Sec1's rollover: `wddctl scope archive` is delivered-only,
    writes `.wdd/archive/<scope-id>.json`, and resets every scope-carrying
    section of state to a fresh setup-phase shape -- total no-leak, governance
    untouched -- so `next` walks a brand-new intake ladder for the next
    scope."""

    def test_refuses_pre_delivered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_finalize(tmp)
            code, _out, err = _cli_full(state, "scope", "archive", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("delivered", err)

    def test_archive_writes_file_and_resets_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_delivered(tmp)
            # Seed a pending reconcile note before archiving so the no-leak
            # assertion below has something concrete to prove gone.
            assert _cli(state, "note", "--note", "a durable discovery")[0] == 0
            before = StateStore(Path(state)).read()
            self.assertEqual(len(before["reconcile"]["pendingNotes"]), 1)
            scope_id = before["scope"]["id"]

            code, out = _cli(state, "scope", "archive", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)

            archive_path = Path(result["archived"])
            self.assertTrue(archive_path.exists())
            self.assertEqual(archive_path, root / ".wdd" / "archive" / f"{scope_id}.json")
            payload = json.loads(archive_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["scope"]["id"], scope_id)
            self.assertIn("T1", payload["tasks"])
            self.assertIn("spec", payload["intake"])
            self.assertIn("verification", payload["finalize"])
            self.assertEqual(payload["reconcile"]["pendingNotes"][0]["note"], "a durable discovery")
            self.assertIsInstance(payload["eventCount"], int)
            self.assertGreater(payload["eventCount"], 0)
            self.assertIn("archivedAt", payload)

            after = StateStore(Path(state)).read()
            self.assertIsNone(after["scope"])
            self.assertEqual(after["tasks"], {})
            self.assertNotIn("finalize", after)
            self.assertEqual(after["intake"], {})
            self.assertEqual(after["reconcile"]["pendingNotes"], [])
            self.assertEqual(after["reconcile"]["mergesSinceCheckpoint"], 0)
            self.assertEqual(after["monitoring"]["observations"], {})
            # Governance survives the reset.
            self.assertEqual(after["constitution"]["status"], "ratified")

    def test_archive_includes_and_disposes_leases(self) -> None:
        """Finding: `state["leases"]` was silently dropped at archive -- not
        archived, not explicitly cleared (`new_setup_state()` just has no
        `leases` key, so it vanished undisclosed). `_run_to_delivered` drives
        a real task through `start`/`submit`/`merge`, which populates and
        then releases a T1 lease (worktree/branch/timestamps), so this fixture
        already has per-task lease history to prove archived and disposed."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_delivered(tmp)
            before = StateStore(Path(state)).read()
            self.assertIn("T1", before.get("leases") or {})
            lease_before = before["leases"]["T1"]
            self.assertIn("branch", lease_before)
            self.assertIn("worktree", lease_before)

            code, out = _cli(state, "scope", "archive", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)

            payload = json.loads(Path(result["archived"]).read_text(encoding="utf-8"))
            self.assertIn("T1", payload["leases"])
            self.assertEqual(payload["leases"]["T1"], lease_before)

            after = StateStore(Path(state)).read()
            self.assertNotIn("leases", after)

    def test_next_says_agree_spec_after_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_delivered(tmp)
            assert _cli(state, "scope", "archive", "--repo", str(root))[0] == 0
            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["actions"][0]["action"], "agree_spec")

    def test_archived_deliverable_command_absent_from_next_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _run_to_delivered(tmp)
            before = StateStore(Path(state)).read()
            self.assertEqual(before["intake"]["design"]["deliverableCommand"], "true")

            assert _cli(state, "scope", "archive", "--repo", str(root))[0] == 0
            after = StateStore(Path(state)).read()
            self.assertEqual(after["intake"], {})
            self.assertNotIn("design", after["intake"])


class FullLifecycleE2ETest(unittest.TestCase):
    """Task 7: the new canonical lifecycle, start to finish, in one journey --

    init -> questions -> ratify -> the intake ladder (spec approve, research
    skip, design approve with a deliverable command) -> plan apply
    --approved-by (composite approval) -> one local-surface task through the
    full execute loop (start/commit/submit/verify/freshness/merge/release --
    the plan's default reviewPolicy risk_based + risk normal skips the
    task-level review gate, per test_execution_surfaces.py's identical
    fixture) -> the finalize ladder (a clean `finalize review record`, then
    verification via the NEW v5 atomic `--results` contract naming both the
    ratified global verification command and the epic deliverable command)
    -> `finalize handoff` on the local surface -> a real (non-simulated)
    `git merge` of the epic branch into the target, proved by `finalize
    delivered` -> `scope archive` -> `next` says `agree_spec` again, proving
    full rollover to a fresh ladder for the next scope.

    Deliberately sequenced through the CLI directly with a checkpoint
    assertion after every stage (rather than delegating to the composed
    `_run_to_finalize`/`_run_to_delivered` fixtures those other classes use)
    so this test reads as the lifecycle's own narrative and each named stage
    is independently provable, while still reusing every granular helper in
    this file (`_ratified_repo`, `_spec_text`, `_design_text`,
    `_plan_document`, `_start_and_commit`, `_passed_results`). Individual
    mechanisms already have dedicated unit/integration coverage above, and
    in test_setup_config/test_plan_quality/test_execution_surfaces/
    test_finalize -- this is the single end-to-end regression pin for the
    whole ladder+governance lifecycle wired together.
    """

    def test_full_lifecycle_init_through_archive_to_fresh_ladder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # --- init -> questions -> ratify ---------------------------------
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            bare = Path(tmp) / "origin.git"
            subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
            subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=root, check=True)

            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)["actions"][0]["action"], "agree_spec")

            # --- ladder rung 1: spec approve ---------------------------------
            (wdd / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-1: greets the caller by name",)),
                encoding="utf-8",
            )
            code, out = _cli(state, "intake", "spec", "--approved-by", "t")
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)["criteria"], 1)
            code, out = _cli(state, "next")
            self.assertEqual(json.loads(out)["actions"][0]["action"], "research")

            # --- ladder rung 2: research skip (attributed reason) ------------
            code, out = _cli(
                state, "intake", "research", "--skip", "--by", "t",
                "--reason", "no external contracts to survey",
            )
            self.assertEqual(code, 0, out)
            code, out = _cli(state, "next")
            self.assertEqual(json.loads(out)["actions"][0]["action"], "agree_design")

            # --- ladder rung 3: design approve with deliverable command ------
            (wdd / "design.md").write_text(_design_text(), encoding="utf-8")
            code, out = _cli(
                state, "intake", "design", "--approved-by", "t",
                "--deliverable-command", "true",
            )
            self.assertEqual(code, 0, out)
            self.assertEqual(
                StateStore(Path(state)).read()["intake"]["design"]["deliverableCommand"], "true"
            )
            code, out = _cli(state, "next")
            self.assertEqual(json.loads(out)["actions"][0]["action"], "plan")

            # --- plan apply --approved-by: the composite approval ------------
            (wdd / "tasks").mkdir(exist_ok=True)
            (wdd / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
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

            # --- one local-surface task through the full execute loop --------
            # Default reviewPolicy (risk_based) + default risk (normal) skips
            # task-level review, so the loop is exactly start/commit/submit/
            # verify/freshness/merge/release -- no `review record` call.
            _start_and_commit(state, root)
            code, out = _cli(state, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            code, out = _cli(state, "verify", "record", "--task", "T1", "--status", "passed")
            self.assertEqual(code, 0, out)
            code, out = _cli(state, "freshness", "record", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            code, out = _cli(state, "merge", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            code, out = _cli(state, "release", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)

            from wave_delivery.schema import derived_phase

            after_execute = StateStore(Path(state)).read()
            self.assertEqual(after_execute["tasks"]["T1"]["status"], "done")
            self.assertEqual(derived_phase(after_execute), "finalize")

            # --- finalize ladder: clean review, v5 multi-command verify ------
            code, out = _cli(
                state, "finalize", "review", "record", "--reviewer", "t", "--findings", "[]",
                "--repo", str(root),
            )
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)["outcome"], "passed")

            code, out = _cli(
                state, "finalize", "verify", "record",
                "--results", _passed_results("true", "true"), "--repo", str(root),
            )
            self.assertEqual(code, 0, out)
            verification = StateStore(Path(state)).read()["finalize"]["verification"]
            self.assertEqual(verification["status"], "passed")
            self.assertEqual(
                verification["commands"],
                [{"command": "true", "status": "passed"}, {"command": "true", "status": "passed"}],
            )

            # --- handoff (local surface) then a real git merge into target ---
            code, out = _cli(state, "finalize", "handoff", "--repo", str(root))
            self.assertEqual(code, 0, out)
            self.assertIsNone(json.loads(out)["pr"])

            base_ref = StateStore(Path(state)).read()["scope"]["baseRef"]
            subprocess.run(["git", "checkout", "-q", "main"], cwd=root, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "-c", "commit.gpgsign=false", "merge", "--no-ff", "-m", "final merge", base_ref],
                cwd=root, check=True,
            )
            code, out = _cli(state, "finalize", "delivered", "--by", "t", "--repo", str(root))
            self.assertEqual(code, 0, out)
            delivered_state = StateStore(Path(state)).read()
            self.assertEqual(derived_phase(delivered_state), "delivered")
            self.assertEqual(delivered_state["finalize"]["delivered"]["by"], "t")

            # --- scope archive: the ladder's rollover -------------------------
            code, out = _cli(state, "scope", "archive", "--repo", str(root))
            self.assertEqual(code, 0, out)
            archive_path = Path(json.loads(out)["archived"])
            self.assertTrue(archive_path.exists())
            payload = json.loads(archive_path.read_text(encoding="utf-8"))
            self.assertIn("spec", payload["intake"])
            self.assertIn("T1", payload["tasks"])
            self.assertEqual(payload["finalize"]["verification"]["status"], "passed")

            after_archive = StateStore(Path(state)).read()
            self.assertIsNone(after_archive["scope"])
            self.assertEqual(after_archive["intake"], {})
            self.assertEqual(after_archive["tasks"], {})
            self.assertNotIn("finalize", after_archive)
            # Governance survives the reset.
            self.assertEqual(after_archive["constitution"]["status"], "ratified")

            # --- next says agree_spec again: a fresh ladder for scope #2 -----
            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)["actions"][0]["action"], "agree_spec")


if __name__ == "__main__":
    unittest.main()
