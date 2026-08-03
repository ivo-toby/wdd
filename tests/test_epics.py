"""Epic-scoped-state plan (2026-08-03).

Task 1: the typed path resolver. Task 2: the layered config overlay,
digests, projections, and derive_effective.

Local helpers only (no cross-file imports between test modules, per the
phase-6a/6b test conventions carried forward -- see Global Constraints in
docs/superpowers/plans/2026-08-03-epic-scoped-state.md). Later tasks in the
same plan add classes to this same file.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wave_delivery.cli import main
from wave_delivery.config import (
    OVERLAY_ALLOWED_LEAVES,
    default_config,
    derive_effective,
    effective_config_digest,
    epic_config_drift,
    get_value,
    governance_fingerprint,
    load_config,
    load_layers,
    load_overlay,
    project,
    resolve_config_source,
    save_config,
    save_overlay,
    set_overlay_value,
    validate_overlay,
)
from wave_delivery.doctor import inspect_capabilities
from wave_delivery.errors import IllegalTransition, RevisionConflict, ValidationError
from wave_delivery.finalize import (
    _canonical_record_bytes,
    _record_sha256,
    archive_scope,
    generate_archive_record,
    recover_archive_transaction,
)
from wave_delivery.intake import artifact_sha256, resolve_within_wdd
from wave_delivery.migration import (
    SUPPORTED_SOURCE_VERSIONS,
    _slugify_scope_id,
    apply_migration,
    convert_v5_to_v6,
    plan_migration,
    read_source,
)
from wave_delivery.paths import resolve_artifact
from wave_delivery.schema import (
    SCHEMA_VERSION,
    TASK_STATUSES,
    new_setup_state,
    new_state,
    task_state,
    validate_state,
)
from wave_delivery.setup import create_epic, epic_orphans, setup_next_actions
from wave_delivery.store import StateStore

# Task 5 (configure gate, drift, evidence binding) additions live below the
# Task 1-4 classes already in this file; they reuse the module-level helpers
# above (_git_repo/_cli/_cli_full/_ratified_repo/_epic_repo/_spec_text/
# _design_text) plus a few local-only ones of their own, per this file's
# no-cross-file-imports convention.


class ResolveArtifactNamespaceTest(unittest.TestCase):
    """Each lexical namespace maps to the right root (spec Sec1)."""

    def test_shared_context_is_always_global(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            resolved = resolve_artifact("shared-context/notes.md", wdd_dir=wdd, epic=None)
            self.assertEqual(resolved, (wdd / "shared-context" / "notes.md").resolve())

    def test_shared_context_ignores_epic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            resolved = resolve_artifact(
                "shared-context/notes.md", wdd_dir=wdd, epic="my-epic"
            )
            # Global, regardless of an active epic -- shared-context/ never
            # lives under epics/<slug>/.
            self.assertEqual(resolved, (wdd / "shared-context" / "notes.md").resolve())

    def test_tasks_namespace_resolves_flat_when_epic_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            resolved = resolve_artifact("tasks/T1.md", wdd_dir=wdd, epic=None)
            self.assertEqual(resolved, (wdd / "tasks" / "T1.md").resolve())

    def test_tasks_namespace_resolves_under_epic_when_epic_given(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            resolved = resolve_artifact("tasks/T1.md", wdd_dir=wdd, epic="my-epic")
            self.assertEqual(
                resolved, (wdd / "epics" / "my-epic" / "tasks" / "T1.md").resolve()
            )

    def test_research_namespace_resolves_under_epic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            resolved = resolve_artifact(
                "research/inventory.md", wdd_dir=wdd, epic="my-epic"
            )
            self.assertEqual(
                resolved,
                (wdd / "epics" / "my-epic" / "research" / "inventory.md").resolve(),
            )

    def test_spec_design_plan_bare_names_resolve_under_epic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            for name in ("spec.md", "design.md", "plan.json"):
                resolved = resolve_artifact(name, wdd_dir=wdd, epic="my-epic")
                self.assertEqual(resolved, (wdd / "epics" / "my-epic" / name).resolve())

    def test_spec_design_plan_bare_names_resolve_flat_when_epic_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            for name in ("spec.md", "design.md", "plan.json"):
                resolved = resolve_artifact(name, wdd_dir=wdd, epic=None)
                self.assertEqual(resolved, (wdd / name).resolve())


class ResolveArtifactAnchorTest(unittest.TestCase):
    """Anchors (`#...`) are stripped before resolution (spec Sec1); unchanged
    from `resolve_within_wdd`'s prior behavior."""

    def test_anchor_is_stripped_for_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            with_anchor = resolve_artifact("tasks/T1.md#AC-1", wdd_dir=wdd, epic=None)
            without_anchor = resolve_artifact("tasks/T1.md", wdd_dir=wdd, epic=None)
            self.assertEqual(with_anchor, without_anchor)

    def test_shared_context_anchor_is_stripped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            with_anchor = resolve_artifact(
                "shared-context/notes.md#orders", wdd_dir=wdd, epic=None
            )
            without_anchor = resolve_artifact(
                "shared-context/notes.md", wdd_dir=wdd, epic=None
            )
            self.assertEqual(with_anchor, without_anchor)

    def test_empty_path_before_anchor_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            with self.assertRaises(ValidationError) as ctx:
                resolve_artifact("#AC-1", wdd_dir=wdd, epic=None)
            self.assertIn("#AC-1", str(ctx.exception))


class ResolveArtifactRejectionTest(unittest.TestCase):
    """Every rejection class refuses, naming the offending ref (spec Sec1)."""

    def test_rejects_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            with self.assertRaises(ValidationError) as ctx:
                resolve_artifact("/etc/passwd", wdd_dir=wdd, epic=None)
            self.assertIn("/etc/passwd", str(ctx.exception))

    def test_rejects_dotdot_segment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            with self.assertRaises(ValidationError) as ctx:
                resolve_artifact("tasks/../../outside.md", wdd_dir=wdd, epic=None)
            self.assertIn("tasks/../../outside.md", str(ctx.exception))

    def test_rejects_leading_dotdot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            with self.assertRaises(ValidationError) as ctx:
                resolve_artifact("../outside.md", wdd_dir=wdd, epic=None)
            self.assertIn("../outside.md", str(ctx.exception))

    def test_rejects_epics_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            with self.assertRaises(ValidationError) as ctx:
                resolve_artifact("epics/other-epic/spec.md", wdd_dir=wdd, epic=None)
            self.assertIn("epics/other-epic/spec.md", str(ctx.exception))

    def test_rejects_archive_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            with self.assertRaises(ValidationError) as ctx:
                resolve_artifact("archive/old-epic/record.json", wdd_dir=wdd, epic=None)
            self.assertIn("archive/old-epic/record.json", str(ctx.exception))

    def test_rejects_dispatch_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            with self.assertRaises(ValidationError) as ctx:
                resolve_artifact("dispatch/T1-1/brief.md", wdd_dir=wdd, epic=None)
            self.assertIn("dispatch/T1-1/brief.md", str(ctx.exception))

    def test_rejects_reserved_record_json_bare(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            with self.assertRaises(ValidationError) as ctx:
                resolve_artifact("record.json", wdd_dir=wdd, epic=None)
            self.assertIn("record.json", str(ctx.exception))

    def test_rejects_reserved_record_json_nested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            with self.assertRaises(ValidationError) as ctx:
                resolve_artifact("research/record.json", wdd_dir=wdd, epic=None)
            self.assertIn("research/record.json", str(ctx.exception))

    def test_rejects_unrecognized_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            with self.assertRaises(ValidationError) as ctx:
                resolve_artifact("notes.md", wdd_dir=wdd, epic=None)
            self.assertIn("notes.md", str(ctx.exception))

    def test_rejects_empty_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            with self.assertRaises(ValidationError):
                resolve_artifact("", wdd_dir=wdd, epic=None)

    def test_symlink_escape_inside_namespace_is_refused(self) -> None:
        # Lexical '..'-rejection alone cannot catch a symlink planted INSIDE
        # a legal namespace that points back out -- the same defense
        # resolve_within_wdd applied before this resolver existed.
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / "wdd"
            wdd.mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "secret.md").write_text("x\n", encoding="utf-8")
            (wdd / "shared-context").symlink_to(outside)
            with self.assertRaises(ValidationError):
                resolve_artifact("shared-context/secret.md", wdd_dir=wdd, epic=None)


class ResolveArtifactFlatFallbackRegressionTest(unittest.TestCase):
    """`epic=None` must be byte-compatible with pre-epic resolution: the
    same ref, the same resolved path, as `resolve_within_wdd` produced
    before this task (regression-pinned)."""

    def test_flat_fallback_matches_manual_join_for_every_known_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            (wdd / "tasks").mkdir()
            (wdd / "research").mkdir()
            (wdd / "shared-context").mkdir()
            refs = [
                "tasks/T1.md",
                "research/inventory.md",
                "shared-context/contract.md",
                "spec.md",
                "design.md",
                "plan.json",
            ]
            for ref in refs:
                resolved = resolve_artifact(ref, wdd_dir=wdd, epic=None)
                self.assertEqual(resolved, (wdd / ref).resolve())

    def test_flat_fallback_matches_resolve_within_wdd(self) -> None:
        """`intake.resolve_within_wdd` is the label-preserving wrapper
        rewired in this task to call `resolve_artifact` -- both must agree
        on the resolved path for the same ref."""
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            via_wrapper = resolve_within_wdd(wdd, "tasks/T1.md")
            via_resolver = resolve_artifact("tasks/T1.md", wdd_dir=wdd, epic=None)
            self.assertEqual(via_wrapper, via_resolver)


class ResolveWithinWddLabelTest(unittest.TestCase):
    """`intake.resolve_within_wdd`'s label-preserving wrapper (rewired onto
    `resolve_artifact` in this task) still names the caller's ref kind in
    refusal messages -- the pinned wording `tests/test_intake.py` and
    `tests/test_wave_delivery.py` (or their equivalents) check for."""

    def test_default_label_is_research_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            with self.assertRaises(ValidationError) as ctx:
                resolve_within_wdd(wdd, "../outside.md")
            self.assertIn("research artifact", str(ctx.exception))

    def test_custom_label_replaces_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp)
            with self.assertRaises(ValidationError) as ctx:
                resolve_within_wdd(wdd, "../outside.md", label="context ref")
            message = str(ctx.exception)
            self.assertIn("context ref", message)
            self.assertNotIn("research artifact", message)


def _wdd_with_config(tmp: str, config: dict | None = None) -> Path:
    wdd = Path(tmp) / ".wdd"
    save_config(wdd, config if config is not None else default_config())
    return wdd


class LoadLayersTest(unittest.TestCase):
    """load_layers returns {defaults, global, overlay, effective}; per-key
    fallback is epic overlay -> global -> default (spec Sec2)."""

    def test_no_epic_yields_empty_overlay_and_effective_equals_global(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            layers = load_layers(wdd, None)
            self.assertEqual(layers["overlay"], {})
            self.assertEqual(layers["effective"], layers["global"])
            self.assertEqual(layers["defaults"], default_config())

    def test_missing_overlay_file_is_empty_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            layers = load_layers(wdd, "my-epic")
            self.assertEqual(layers["overlay"], {})
            self.assertEqual(layers["effective"], layers["global"])

    def test_overlay_leaf_overrides_global_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            save_overlay(wdd, "my-epic", {"merge": {"surface": "local"}})
            layers = load_layers(wdd, "my-epic")
            self.assertEqual(layers["global"]["merge"]["surface"], "pr")
            self.assertEqual(layers["effective"]["merge"]["surface"], "local")

    def test_unoverridden_leaf_falls_through_to_global(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            save_overlay(wdd, "my-epic", {"merge": {"surface": "local"}})
            layers = load_layers(wdd, "my-epic")
            # models.planning wasn't touched by the overlay -- falls through.
            self.assertEqual(layers["effective"]["models"]["planning"], None)

    def test_per_key_fallback_is_independent_per_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = default_config()
            config["models"]["planning"] = "global-planner"
            config["models"]["review"] = "global-reviewer"
            wdd = _wdd_with_config(tmp, config)
            save_overlay(wdd, "my-epic", {"models": {"review": "epic-reviewer"}})
            layers = load_layers(wdd, "my-epic")
            self.assertEqual(layers["effective"]["models"]["planning"], "global-planner")
            self.assertEqual(layers["effective"]["models"]["review"], "epic-reviewer")


class OverlayAllowlistTest(unittest.TestCase):
    """The overlay allowlist is enforced BY NAME at every entry point:
    overlay load, `config set --epic` (exercised via set_overlay_value +
    derive_effective, the same path the CLI handler uses), and a direct
    derive_effective call (standing in for configure approval until
    Task 5)."""

    def test_all_documented_leaves_are_individually_accepted(self) -> None:
        samples = {
            "models.planning": "gpt-planner",
            "models.implementation": {"default": "gpt-impl", "highRisk": "gpt-impl-hi"},
            "models.review": "gpt-review",
            "verification.commands": ["pytest -q"],
            "verification.unavailableJustification": None,
            "merge.surface": "local",
            "riskRules": [{"pattern": "src/**", "risk": "high"}],
            "review.policy": "always",
        }
        for dotted, value in samples.items():
            overlay = set_overlay_value({}, dotted, value)
            validate_overlay(overlay)  # must not raise

    def test_rejects_unknown_top_level_key_by_name_at_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            (wdd / "epics" / "my-epic").mkdir(parents=True)
            (wdd / "epics" / "my-epic" / "config.json").write_text(
                json.dumps({"runners": {"codex": {"command": ["codex"]}}}), encoding="utf-8"
            )
            with self.assertRaises(ValidationError) as ctx:
                load_overlay(wdd, "my-epic")
            self.assertIn("runners", str(ctx.exception))

    def test_rejects_worktrees_root_by_name(self) -> None:
        overlay = {"worktrees": {"root": "custom"}}
        with self.assertRaises(ValidationError) as ctx:
            validate_overlay(overlay)
        self.assertIn("worktrees.root", str(ctx.exception))

    def test_rejects_disallowed_sibling_within_allowed_container(self) -> None:
        # merge.surface is allowed; merge.mode is not -- rejected by name,
        # not just by the top-level 'merge' container being touched.
        overlay = {"merge": {"surface": "local", "mode": "human"}}
        with self.assertRaises(ValidationError) as ctx:
            validate_overlay(overlay)
        self.assertIn("merge.mode", str(ctx.exception))

    def test_rejects_review_blocking_severities(self) -> None:
        overlay = {"review": {"blockingSeverities": ["P1"]}}
        with self.assertRaises(ValidationError) as ctx:
            validate_overlay(overlay)
        self.assertIn("review.blockingSeverities", str(ctx.exception))

    def test_set_overlay_value_then_derive_effective_rejects_forbidden_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            layers = load_layers(wdd, "my-epic")
            patch = set_overlay_value(layers["overlay"], "branching.targetBranch", "develop")
            with self.assertRaises(ValidationError) as ctx:
                derive_effective(layers, patch)
            self.assertIn("branching.targetBranch", str(ctx.exception))

    def test_derive_effective_rejects_forbidden_key_directly(self) -> None:
        # Stands in for configure approval until Task 5 wires the verb.
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            layers = load_layers(wdd, "my-epic")
            with self.assertRaises(ValidationError) as ctx:
                derive_effective(layers, {"taskProvider": {"type": "jira"}})
            self.assertIn("taskProvider", str(ctx.exception))

    def test_overlay_value_shape_is_validated_like_global_config(self) -> None:
        with self.assertRaises(ValidationError):
            validate_overlay({"merge": {"surface": "carrier-pigeon"}})
        with self.assertRaises(ValidationError):
            validate_overlay({"review": {"policy": "yolo"}})
        with self.assertRaises(ValidationError):
            validate_overlay({"riskRules": [{"pattern": "x", "risk": "extreme"}]})


class DeriveEffectiveMaskingTest(unittest.TestCase):
    """The layered-snapshot pin: an overlay leaf masks the global value;
    dropping it from a later patch reveals the retained global value."""

    def test_overlay_masks_global_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            layers = load_layers(wdd, "my-epic")
            self.assertEqual(layers["effective"]["merge"]["surface"], "pr")
            derived = derive_effective(layers, {"merge": {"surface": "local"}})
            self.assertEqual(derived["effective"]["merge"]["surface"], "local")
            self.assertEqual(derived["global"]["merge"]["surface"], "pr")

    def test_removal_reveals_retained_global_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            layers = load_layers(wdd, "my-epic")
            masked = derive_effective(layers, {"merge": {"surface": "local"}})
            self.assertEqual(masked["effective"]["merge"]["surface"], "local")
            # Empty patch == "--use-defaults": drop the override.
            revealed = derive_effective(masked, {})
            self.assertEqual(revealed["effective"]["merge"]["surface"], "pr")
            self.assertEqual(revealed["overlay"], {})

    def test_removal_of_one_leaf_does_not_disturb_another(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            layers = load_layers(wdd, "my-epic")
            both = derive_effective(
                layers, {"merge": {"surface": "local"}, "review": {"policy": "always"}}
            )
            only_review = derive_effective(both, {"review": {"policy": "always"}})
            self.assertEqual(only_review["effective"]["merge"]["surface"], "pr")
            self.assertEqual(only_review["effective"]["review"]["policy"], "always")


class DeriveEffectiveRejectsWhatLoadingRejectsTest(unittest.TestCase):
    def test_same_forbidden_key_rejected_both_ways(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            (wdd / "epics" / "my-epic").mkdir(parents=True)
            (wdd / "epics" / "my-epic" / "config.json").write_text(
                json.dumps({"branching": {"targetBranch": "develop"}}), encoding="utf-8"
            )
            with self.assertRaises(ValidationError):
                load_overlay(wdd, "my-epic")
            layers = load_layers(wdd, None)
            with self.assertRaises(ValidationError):
                derive_effective(layers, {"branching": {"targetBranch": "develop"}})

    def test_same_bad_value_rejected_both_ways(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            (wdd / "epics" / "my-epic").mkdir(parents=True)
            (wdd / "epics" / "my-epic" / "config.json").write_text(
                json.dumps({"merge": {"surface": "carrier-pigeon"}}), encoding="utf-8"
            )
            with self.assertRaises(ValidationError):
                load_overlay(wdd, "my-epic")
            layers = load_layers(wdd, None)
            with self.assertRaises(ValidationError):
                derive_effective(layers, {"merge": {"surface": "carrier-pigeon"}})


class EffectiveConfigDigestTest(unittest.TestCase):
    """One digest function, byte-precise (spec Sec2)."""

    def test_key_order_does_not_affect_digest(self) -> None:
        view = {"b": 1, "a": 2, "nested": {"z": 1, "y": 2}}
        reordered = {"a": 2, "nested": {"y": 2, "z": 1}, "b": 1}
        self.assertEqual(effective_config_digest(view), effective_config_digest(reordered))

    def test_array_order_is_preserved(self) -> None:
        first = {"commands": ["a", "b"]}
        second = {"commands": ["b", "a"]}
        self.assertNotEqual(effective_config_digest(first), effective_config_digest(second))

    def test_unicode_is_stable_and_distinguishing(self) -> None:
        ascii_view = {"justification": "cafe"}
        unicode_view = {"justification": "café"}
        # Same digest function must be deterministic and distinguish these.
        self.assertEqual(
            effective_config_digest(ascii_view), effective_config_digest(dict(ascii_view))
        )
        self.assertNotEqual(
            effective_config_digest(ascii_view), effective_config_digest(unicode_view)
        )

    def test_empty_overlay_and_missing_key_are_not_confused(self) -> None:
        # An empty overlay's effective view (pure global) must not collide
        # with a view that merely omits an unrelated key.
        with_key = {"riskRules": []}
        without_key = {}
        self.assertNotEqual(effective_config_digest(with_key), effective_config_digest(without_key))

    def test_digest_is_stable_for_equal_views(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            layers = load_layers(wdd, None)
            self.assertEqual(
                effective_config_digest(layers["effective"]),
                effective_config_digest(layers["effective"]),
            )

    def test_digest_changes_when_effective_view_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            layers = load_layers(wdd, "my-epic")
            before = effective_config_digest(layers["effective"])
            derived = derive_effective(layers, {"merge": {"surface": "local"}})
            after = effective_config_digest(derived["effective"])
            self.assertNotEqual(before, after)

    def test_rejects_non_finite_number(self) -> None:
        with self.assertRaises(ValidationError):
            effective_config_digest({"weight": float("nan")})
        with self.assertRaises(ValidationError):
            effective_config_digest({"weight": float("inf")})

    def test_overlay_json_with_duplicate_keys_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            (wdd / "epics" / "my-epic").mkdir(parents=True)
            (wdd / "epics" / "my-epic" / "config.json").write_text(
                '{"merge": {"surface": "local"}, "merge": {"surface": "pr"}}', encoding="utf-8"
            )
            with self.assertRaises(ValidationError):
                load_overlay(wdd, "my-epic")

    def test_overlay_json_with_non_finite_literal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            (wdd / "epics" / "my-epic").mkdir(parents=True)
            (wdd / "epics" / "my-epic" / "config.json").write_text(
                '{"riskRules": [{"pattern": "x", "risk": "high", "weight": NaN}]}',
                encoding="utf-8",
            )
            with self.assertRaises(ValidationError):
                load_overlay(wdd, "my-epic")


class ProjectionPartitioningTest(unittest.TestCase):
    """Gates compare like with like: an edit to one config area must not
    stale evidence recorded under an unrelated projection (spec Sec2)."""

    def test_models_planning_edit_changes_no_evidence_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            layers = load_layers(wdd, "my-epic")
            before = {
                purpose: effective_config_digest(project(layers["effective"], purpose))
                for purpose in ("taskReview", "finalReview", "taskVerification", "finalVerification")
            }
            derived = derive_effective(layers, {"models": {"planning": "new-planner"}})
            after = {
                purpose: effective_config_digest(project(derived["effective"], purpose))
                for purpose in ("taskReview", "finalReview", "taskVerification", "finalVerification")
            }
            self.assertEqual(before, after)
            # The plan projection DOES include models -- sanity check it moved.
            plan_before = effective_config_digest(project(layers["effective"], "plan"))
            plan_after = effective_config_digest(project(derived["effective"], "plan"))
            self.assertNotEqual(plan_before, plan_after)

    def test_verification_commands_edit_changes_exactly_the_two_verification_projections(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            layers = load_layers(wdd, "my-epic")
            purposes = ("plan", "taskReview", "finalReview", "taskVerification", "finalVerification")
            before = {
                purpose: effective_config_digest(project(layers["effective"], purpose))
                for purpose in purposes
            }
            derived = derive_effective(layers, {"verification": {"commands": ["pytest -q"]}})
            after = {
                purpose: effective_config_digest(project(derived["effective"], purpose))
                for purpose in purposes
            }
            changed = {purpose for purpose in purposes if before[purpose] != after[purpose]}
            self.assertEqual(changed, {"taskVerification", "finalVerification"})

    def test_unknown_purpose_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            project(default_config(), "codeReview")


class ConfigEpicCliTest(unittest.TestCase):
    """`config get`/`set` gain `--epic` (spec Sec2)."""

    def _run(self, tmp: str, *argv: str) -> tuple[int, str]:
        state = str(Path(tmp) / ".wdd" / "state.json")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["--state", state, *argv])
        return code, stdout.getvalue()

    def test_set_epic_refuses_when_no_active_epic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _wdd_with_config(tmp)
            code, out = self._run(tmp, "config", "set", "--epic", "merge.surface", "local")
            self.assertNotEqual(code, 0)

    def test_set_epic_refusal_names_epic_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _wdd_with_config(tmp)
            stderr = io.StringIO()
            state = str(Path(tmp) / ".wdd" / "state.json")
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    ["--state", state, "config", "set", "--epic", "merge.surface", "local"]
                )
            self.assertNotEqual(code, 0)
            self.assertIn("wddctl epic new", stderr.getvalue())

    def test_get_epic_without_active_epic_reports_global_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _wdd_with_config(tmp)
            code, out = self._run(tmp, "config", "get", "--epic", "merge.surface")
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(payload["value"], "pr")
            self.assertEqual(payload["source"], "global")

    def _write_state_with_epic(self, tmp: str, epic: str) -> None:
        wdd = Path(tmp) / ".wdd"
        state_store = StateStore(wdd / "state.json")
        state = new_setup_state()
        state["epic"] = epic
        state_store.write(state)

    def test_set_epic_writes_overlay_when_epic_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _wdd_with_config(tmp)
            self._write_state_with_epic(tmp, "my-epic")
            code, out = self._run(tmp, "config", "set", "--epic", "merge.surface", "local")
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(payload["value"], "local")
            self.assertEqual(payload["epic"], "my-epic")
            wdd = Path(tmp) / ".wdd"
            overlay = load_overlay(wdd, "my-epic")
            self.assertEqual(overlay, {"merge": {"surface": "local"}})

    def test_get_epic_reports_epic_source_after_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _wdd_with_config(tmp)
            self._write_state_with_epic(tmp, "my-epic")
            code, _ = self._run(tmp, "config", "set", "--epic", "merge.surface", "local")
            self.assertEqual(code, 0)
            code, out = self._run(tmp, "config", "get", "--epic", "merge.surface")
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(payload["value"], "local")
            self.assertEqual(payload["source"], "epic")

    def test_get_epic_deeper_path_under_overridden_leaf_reports_epic_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _wdd_with_config(tmp)
            self._write_state_with_epic(tmp, "my-epic")
            wdd = Path(tmp) / ".wdd"
            save_overlay(wdd, "my-epic", {"models": {"implementation": {"default": "gpt-x"}}})
            code, out = self._run(
                tmp, "config", "get", "--epic", "models.implementation.default"
            )
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(payload["value"], "gpt-x")
            self.assertEqual(payload["source"], "epic")

    def test_plain_get_set_unaffected_by_epic_flag_absence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _wdd_with_config(tmp)
            code, out = self._run(tmp, "config", "get", "merge.surface")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out), "pr")


class ResolveConfigSourceTest(unittest.TestCase):
    def test_unknown_path_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            layers = load_layers(wdd, "my-epic")
            with self.assertRaises(ValidationError):
                resolve_config_source(layers, "models.nonexistent")

    def test_overlay_allowed_leaves_constant_matches_documented_set(self) -> None:
        self.assertEqual(
            set(OVERLAY_ALLOWED_LEAVES),
            {
                "models.planning",
                "models.implementation",
                "models.review",
                "verification.commands",
                "verification.unavailableJustification",
                "merge.surface",
                "riskRules",
                "review.policy",
            },
        )


class LegacyConfigDefaultHydrationTest(unittest.TestCase):
    """A legacy config.json predating `runners`/`worktrees` (both optional
    per validate_config, spec Sec6) must not crash `config get --epic`:
    `effective` needs a concrete value for these keys even though `global`
    legitimately lacks them -- otherwise `resolve_config_source` names the
    'default' tier and then crashes trying to read the value out of
    `effective` (fix-round F1)."""

    def _legacy_wdd(self, tmp: str) -> Path:
        config = default_config()
        del config["runners"]
        del config["worktrees"]
        return _wdd_with_config(tmp, config)

    def test_runners_resolves_to_default_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = self._legacy_wdd(tmp)
            layers = load_layers(wdd, None)
            value, source = resolve_config_source(layers, "runners")
            self.assertEqual(value, {})
            self.assertEqual(source, "default")
            self.assertEqual(get_value(layers["effective"], "runners"), {})

    def test_worktrees_root_resolves_to_default_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = self._legacy_wdd(tmp)
            layers = load_layers(wdd, None)
            value, source = resolve_config_source(layers, "worktrees.root")
            self.assertEqual(value, ".worktrees")
            self.assertEqual(source, "default")

    def test_global_layer_itself_stays_unhydrated(self) -> None:
        # 'global' must stay the raw, un-hydrated config -- only 'effective'
        # is hydrated. If 'global' were hydrated too, resolve_config_source
        # could never report source='default' for these keys (it would find
        # them in 'global' first).
        with tempfile.TemporaryDirectory() as tmp:
            wdd = self._legacy_wdd(tmp)
            layers = load_layers(wdd, None)
            self.assertNotIn("runners", layers["global"])
            self.assertNotIn("worktrees", layers["global"])

    def test_digest_of_effective_view_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = self._legacy_wdd(tmp)
            layers = load_layers(wdd, None)
            first = effective_config_digest(layers["effective"])
            second = effective_config_digest(layers["effective"])
            self.assertEqual(first, second)


class SetOverlayValueSubLeafSeedingTest(unittest.TestCase):
    """`set_overlay_value` seeds a new overlay leaf from the CURRENT
    EFFECTIVE leaf value when `dotted` targets a path BELOW an allowlisted
    leaf (e.g. `models.implementation.default`, below the
    `models.implementation` leaf), so siblings the caller didn't touch (e.g.
    `highRisk`) survive the later leaf-atomic apply instead of being
    silently nulled by a freshly-created `{sub_key: value}` object
    (fix-round F2)."""

    def test_sub_leaf_set_preserves_sibling_default_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = default_config()
            config["models"]["implementation"] = {"default": "a", "highRisk": "b"}
            wdd = _wdd_with_config(tmp, config)
            layers = load_layers(wdd, "my-epic")
            patch = set_overlay_value(
                layers["overlay"],
                "models.implementation.default",
                "X",
                effective=layers["effective"],
            )
            self.assertEqual(
                patch["models"]["implementation"], {"default": "X", "highRisk": "b"}
            )
            derived = derive_effective(layers, patch)
            self.assertEqual(
                derived["effective"]["models"]["implementation"],
                {"default": "X", "highRisk": "b"},
            )

    def test_sub_leaf_set_without_effective_still_creates_sparse_leaf(self) -> None:
        # Backward-compat: callers that only ever touch exact-leaf paths
        # (never below-leaf) don't need to pass `effective`; a below-leaf
        # call made without it falls back to the pre-fix sparse behavior
        # rather than crashing on a missing seed source.
        overlay = set_overlay_value({}, "models.implementation.default", "X")
        self.assertEqual(overlay, {"models": {"implementation": {"default": "X"}}})

    def test_second_sub_leaf_set_does_not_reseed_over_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = default_config()
            config["models"]["implementation"] = {"default": "a", "highRisk": "b"}
            wdd = _wdd_with_config(tmp, config)
            layers = load_layers(wdd, "my-epic")
            first_patch = set_overlay_value(
                layers["overlay"],
                "models.implementation.default",
                "X",
                effective=layers["effective"],
            )
            derived = derive_effective(layers, first_patch)
            second_patch = set_overlay_value(
                derived["overlay"],
                "models.implementation.highRisk",
                "Y",
                effective=derived["effective"],
            )
            self.assertEqual(
                second_patch["models"]["implementation"],
                {"default": "X", "highRisk": "Y"},
            )


class ConfigSetEpicSubLeafCliTest(unittest.TestCase):
    """`config set --epic` on a sub-leaf path preserves untouched siblings
    end to end through the CLI (fix-round F2)."""

    def _run(self, tmp: str, *argv: str) -> tuple[int, str]:
        state = str(Path(tmp) / ".wdd" / "state.json")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["--state", state, *argv])
        return code, stdout.getvalue()

    def _write_state_with_epic(self, tmp: str, epic: str) -> None:
        wdd = Path(tmp) / ".wdd"
        state_store = StateStore(wdd / "state.json")
        state = new_setup_state()
        state["epic"] = epic
        state_store.write(state)

    def test_set_epic_sub_leaf_preserves_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = default_config()
            config["models"]["implementation"] = {"default": "a", "highRisk": "b"}
            _wdd_with_config(tmp, config)
            self._write_state_with_epic(tmp, "my-epic")
            code, _ = self._run(
                tmp, "config", "set", "--epic", "models.implementation.default", '"X"'
            )
            self.assertEqual(code, 0)
            code, out = self._run(
                tmp, "config", "get", "--epic", "models.implementation.highRisk"
            )
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(payload["value"], "b")
            self.assertEqual(payload["source"], "epic")


class GlobalConfigParsePrecisionTest(unittest.TestCase):
    """`load_config` gets the same byte-precision parsing `load_overlay`
    already had: duplicate object keys and non-finite number literals
    (NaN/Infinity/-Infinity) are rejected rather than silently accepted by
    Python's `json` module -- both now go through one shared
    `_parse_strict_json` helper (fix-round F3)."""

    def test_duplicate_key_in_config_json_is_rejected_naming_file_and_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir(parents=True)
            path = wdd / "config.json"
            path.write_text('{"schemaVersion": 1, "schemaVersion": 1}', encoding="utf-8")
            with self.assertRaises(ValidationError) as ctx:
                load_config(wdd)
            message = str(ctx.exception)
            self.assertIn(str(path), message)
            self.assertIn("schemaVersion", message)

    def test_non_finite_literal_in_config_json_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir(parents=True)
            path = wdd / "config.json"
            path.write_text('{"weight": NaN}', encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_config(wdd)

    def test_ordinary_valid_config_still_loads(self) -> None:
        # Regression guard: the new parse path must not reject well-formed
        # configs with no duplicates or non-finite literals.
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_with_config(tmp)
            self.assertEqual(load_config(wdd), default_config())


class OverlayAllowlistLaxnessInsideObjectLeafTest(unittest.TestCase):
    """Key laxness INSIDE an object-shaped allowed leaf mirrors global
    `validate_config`'s own documented tolerance for `models.review`'s
    object form (unknown extra keys are not rejected there either) --
    pinned here so a future tightening of either validator is a conscious,
    visible change rather than an accidental behavior drift (fix-round
    Minor)."""

    def test_bogus_key_inside_models_review_object_leaf_is_currently_accepted(self) -> None:
        overlay = {"models": {"review": {"default": "x", "bogus": "y"}}}
        validate_overlay(overlay)  # must not raise (documented tolerance)


# ---------------------------------------------------------------------------
# Task 3: schema v6 (epic identity, intake.configure, archivePending/
# archiveBlocked, evidence-binding extras) and the v5 -> v6 migration table
# (spec Sec4).
# ---------------------------------------------------------------------------


class SchemaV6EpicFieldTest(unittest.TestCase):
    """`state.epic`: slug-or-null (spec Sec1)."""

    def test_null_epic_is_valid(self) -> None:
        state = new_state("SCOPE-x")
        state["epic"] = None
        validate_state(state)

    def test_valid_slug_is_accepted(self) -> None:
        state = new_state("SCOPE-x")
        state["epic"] = "checkout-flow"
        validate_state(state)

    def test_single_character_slug_is_rejected(self) -> None:
        # [a-z0-9][a-z0-9-]{1,63} requires at least 2 characters total.
        state = new_state("SCOPE-x")
        state["epic"] = "a"
        with self.assertRaises(ValidationError):
            validate_state(state)

    def test_uppercase_slug_is_rejected(self) -> None:
        state = new_state("SCOPE-x")
        state["epic"] = "Checkout"
        with self.assertRaises(ValidationError):
            validate_state(state)

    def test_slug_with_underscore_is_rejected(self) -> None:
        state = new_state("SCOPE-x")
        state["epic"] = "checkout_flow"
        with self.assertRaises(ValidationError):
            validate_state(state)

    def test_new_state_defaults_epic_to_none(self) -> None:
        self.assertIsNone(new_state("SCOPE-x")["epic"])
        self.assertIsNone(new_setup_state()["epic"])


class SchemaV6ArchiveFieldsTest(unittest.TestCase):
    """`archivePending`/`archiveBlocked`: nullable, shape-checked when
    present (spec Sec1); constructors never mint either (Task 6 is the
    sole writer)."""

    def test_both_default_to_none(self) -> None:
        state = new_state("SCOPE-x")
        self.assertIsNone(state["archivePending"])
        self.assertIsNone(state["archiveBlocked"])

    def test_valid_archive_pending_is_accepted(self) -> None:
        state = new_state("SCOPE-x")
        state["archivePending"] = {
            "slug": "checkout",
            "sourceRevision": 4,
            "archivedAt": "2026-01-01T00:00:00Z",
            "recordSha256": "sha256:" + "a" * 64,
        }
        validate_state(state)

    def test_archive_pending_rejects_negative_revision(self) -> None:
        state = new_state("SCOPE-x")
        state["archivePending"] = {
            "slug": "checkout",
            "sourceRevision": -1,
            "archivedAt": "2026-01-01T00:00:00Z",
            "recordSha256": "sha256:" + "a" * 64,
        }
        with self.assertRaises(ValidationError):
            validate_state(state)

    def test_archive_pending_requires_all_fields(self) -> None:
        state = new_state("SCOPE-x")
        state["archivePending"] = {"slug": "checkout"}
        with self.assertRaises(ValidationError):
            validate_state(state)

    def test_valid_archive_blocked_is_accepted(self) -> None:
        state = new_state("SCOPE-x")
        state["archiveBlocked"] = {
            "slug": "checkout",
            "collidingPath": "archive/checkout",
            "at": "2026-01-01T00:00:00Z",
        }
        validate_state(state)

    def test_archive_blocked_requires_all_fields(self) -> None:
        state = new_state("SCOPE-x")
        state["archiveBlocked"] = {"slug": "checkout"}
        with self.assertRaises(ValidationError):
            validate_state(state)


class SchemaV6IntakeConfigureTest(unittest.TestCase):
    """`intake.configure`: the real attributed shape `{by, at, sha256}`
    (Task 5's verb) or migration's exemption shape `{"legacy": true,
    "sha256": ...}` (spec Sec4) -- under EITHER an already-legacy
    (`intake.legacy`) or a non-legacy intake section."""

    def test_attributed_shape_is_accepted_on_non_legacy_intake(self) -> None:
        state = new_state("SCOPE-x")
        state["intake"] = {
            "configure": {"by": "alice", "at": "2026-01-01T00:00:00Z", "sha256": "sha256:" + "a" * 64}
        }
        validate_state(state)

    def test_exemption_shape_is_accepted_on_non_legacy_intake(self) -> None:
        state = new_state("SCOPE-x")
        state["intake"] = {"configure": {"legacy": True, "sha256": "sha256:" + "a" * 64}}
        validate_state(state)

    def test_legacy_scope_may_carry_configure_alongside_legacy(self) -> None:
        state = new_state("SCOPE-x")
        state["intake"] = {
            "legacy": True,
            "configure": {"legacy": True, "sha256": "sha256:" + "a" * 64},
        }
        validate_state(state)

    def test_legacy_scope_without_configure_is_still_valid(self) -> None:
        state = new_state("SCOPE-x")
        state["intake"] = {"legacy": True}
        validate_state(state)

    def test_configure_exemption_rejects_unknown_keys(self) -> None:
        state = new_state("SCOPE-x")
        state["intake"] = {
            "configure": {"legacy": True, "sha256": "sha256:" + "a" * 64, "by": "alice"}
        }
        with self.assertRaises(ValidationError):
            validate_state(state)

    def test_configure_missing_sha256_is_rejected(self) -> None:
        state = new_state("SCOPE-x")
        state["intake"] = {"configure": {"by": "alice", "at": "2026-01-01T00:00:00Z"}}
        with self.assertRaises(ValidationError):
            validate_state(state)

    def test_legacy_true_with_extra_unknown_key_besides_configure_is_rejected(self) -> None:
        state = new_state("SCOPE-x")
        state["intake"] = {"legacy": True, "bogus": True}
        with self.assertRaises(ValidationError):
            validate_state(state)


class SchemaV6ReviewEvidenceExtrasTest(unittest.TestCase):
    """resolvedRisk/reviewModel/configSha256: permissive when absent
    (legacy-stamped/pre-Task-5 records), shape-checked when present (spec
    Sec2)."""

    def _task_with_review(self, review: dict | None) -> dict:
        task = task_state("T1")
        task["review"] = review
        state = new_state("SCOPE-x")
        state["tasks"] = {"T1": task}
        return state

    def test_review_without_any_extras_is_still_valid(self) -> None:
        validate_state(self._task_with_review({"baseSha": "a", "headSha": "b", "findings": [], "reviewer": "x"}))

    def test_review_with_valid_extras_is_accepted(self) -> None:
        review = {
            "baseSha": "a", "headSha": "b", "findings": [], "reviewer": "x",
            "resolvedRisk": "high", "reviewModel": "gpt-review", "configSha256": "sha256:" + "a" * 64,
        }
        validate_state(self._task_with_review(review))

    def test_review_with_invalid_resolved_risk_is_rejected(self) -> None:
        review = {"baseSha": "a", "headSha": "b", "findings": [], "reviewer": "x", "resolvedRisk": "extreme"}
        with self.assertRaises(ValidationError):
            validate_state(self._task_with_review(review))

    def test_review_with_empty_config_sha_is_rejected(self) -> None:
        review = {"baseSha": "a", "headSha": "b", "findings": [], "reviewer": "x", "configSha256": ""}
        with self.assertRaises(ValidationError):
            validate_state(self._task_with_review(review))

    def test_final_review_rejects_resolved_risk(self) -> None:
        # finalReview evidence binds a model but no per-task risk tier
        # (spec Sec2: "final review records its selected model").
        state = new_state("SCOPE-x")
        state["finalize"] = {
            "review": {
                "headSha": "a", "outcome": "passed", "findings": [], "reviewer": "x",
                "at": "t", "resolvedRisk": "high",
            }
        }
        with self.assertRaises(ValidationError):
            validate_state(state)

    def test_final_review_accepts_review_model_and_config_sha(self) -> None:
        state = new_state("SCOPE-x")
        state["finalize"] = {
            "review": {
                "headSha": "a", "outcome": "passed", "findings": [], "reviewer": "x", "at": "t",
                "reviewModel": "gpt-review", "configSha256": "sha256:" + "a" * 64,
            }
        }
        validate_state(state)

    def test_task_verification_config_sha_is_shape_checked(self) -> None:
        task = task_state("T1")
        task["verification"] = {"baseSha": "a", "headSha": "b", "status": "passed", "configSha256": ""}
        state = new_state("SCOPE-x")
        state["tasks"] = {"T1": task}
        with self.assertRaises(ValidationError):
            validate_state(state)


# --- v5 -> v6 migration (spec Sec4) ----------------------------------------


def _wdd_root(tmp: str) -> Path:
    wdd = Path(tmp) / ".wdd"
    save_config(wdd, default_config())
    return wdd


def _write_text(path: Path, content: str = "content\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _v5_state(*, scope_id: str | None = "SCOPE-demo", legacy: bool = True, tasks: dict | None = None) -> dict:
    """A hand-built v5-shaped state (schemaVersion 5, no epic/archivePending/
    archiveBlocked -- those postdate v5). `task_state()` itself is reused for
    per-task shape since it is unaffected by the v6 bump (only the top-level
    state shape changed)."""
    scope = None
    if scope_id is not None:
        scope = {
            "id": scope_id, "baseRef": None, "maxConcurrent": None, "reviewPolicy": "risk_based",
        }
    if legacy:
        intake: dict = {"legacy": True}
    else:
        intake = {
            "spec": {
                "by": "tester", "at": "2026-01-01T00:00:00Z",
                "sha256": "sha256:" + "a" * 64, "criteria": 1,
            },
            "research": {"by": "tester", "at": "2026-01-01T00:00:00Z", "skipped": True, "reason": "n/a"},
            "design": {
                "by": "tester", "at": "2026-01-01T00:00:00Z",
                "sha256": "sha256:" + "b" * 64, "deliverableCommand": "pytest -q",
            },
        }
    return {
        "schemaVersion": 5,
        "revision": 0,
        "scope": scope,
        "constitution": {
            "status": "ratified",
            "ratification": {"by": "tester", "decisionFingerprint": "sha256:c", "at": "2026-01-01T00:00:00Z"},
        },
        "tasks": tasks if tasks is not None else {},
        "reconcile": {
            "everyNMerges": 3, "mergesSinceCheckpoint": 0, "lastCheckpointAt": None, "pendingNotes": [],
        },
        "monitoring": {
            "mode": "manual", "status": "inactive", "lastCheckedAt": None,
            "nextCheckDueAt": None, "observations": {},
        },
        "events": [],
        "appliedIdempotencyKeys": [],
        "telemetry": {"eventApplications": 0, "renderCount": 0},
        "intake": intake,
    }


def _write_v5_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class MigrateSlugDerivationTest(unittest.TestCase):
    """slug from the active scope id, or 'legacy' when no scope (spec
    Sec4); a scope id that cannot be turned into a valid slug also falls
    back to 'legacy' rather than ever writing an invalid `state.epic`
    (documented judgment call in migration.py's `_slugify_scope_id`)."""

    def test_scope_prefix_convention_id(self) -> None:
        self.assertEqual(_slugify_scope_id("SCOPE-checkout"), "checkout")

    def test_no_scope_id_falls_back_to_legacy(self) -> None:
        self.assertEqual(_slugify_scope_id(None), "legacy")
        self.assertEqual(_slugify_scope_id(""), "legacy")

    def test_unusable_id_falls_back_to_legacy(self) -> None:
        self.assertEqual(_slugify_scope_id("---"), "legacy")

    def test_id_without_scope_prefix_is_lowercased_and_sanitized(self) -> None:
        self.assertEqual(_slugify_scope_id("Checkout_Flow"), "checkout-flow")


class MigrateV5ToV6FileMovesTest(unittest.TestCase):
    """spec.md/design.md/plan.json/tasks/ + recorded research artifacts
    move into epics/<slug>/; shared-context/ never moves (spec Sec4)."""

    def test_spec_design_plan_and_tasks_move_into_epic_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            _write_text(wdd / "spec.md", "spec\n")
            _write_text(wdd / "design.md", "design\n")
            _write_text(wdd / "plan.json", "{}\n")
            _write_text(wdd / "tasks" / "TASK-001.md", "brief\n")
            state = _v5_state(legacy=False, tasks={"TASK-001": task_state("TASK-001")})
            path = wdd / "state.json"
            _write_v5_state(path, state)

            result = apply_migration(path)
            self.assertEqual(result["to"], 6)

            epic_dir = wdd / "epics" / "demo"
            self.assertEqual((epic_dir / "spec.md").read_text(), "spec\n")
            self.assertEqual((epic_dir / "design.md").read_text(), "design\n")
            self.assertEqual((epic_dir / "plan.json").read_text(), "{}\n")
            self.assertEqual((epic_dir / "tasks" / "TASK-001.md").read_text(), "brief\n")
            self.assertFalse((wdd / "spec.md").exists())
            self.assertFalse((wdd / "design.md").exists())
            self.assertFalse((wdd / "plan.json").exists())
            self.assertFalse((wdd / "tasks" / "TASK-001.md").exists())

    def test_shared_context_is_never_moved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            _write_text(wdd / "shared-context" / "notes.md", "notes\n")
            _write_text(wdd / "tasks" / "TASK-001.md", "brief\n")
            task = task_state("TASK-001", context=["shared-context/notes.md"])
            state = _v5_state(legacy=False, tasks={"TASK-001": task})
            path = wdd / "state.json"
            _write_v5_state(path, state)

            apply_migration(path)
            self.assertEqual((wdd / "shared-context" / "notes.md").read_text(), "notes\n")

    def test_recorded_research_artifacts_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            artifact_path = wdd / "research" / "inventory.md"
            _write_text(artifact_path, "inventory\n")
            state = _v5_state(legacy=False, tasks={})
            state["intake"]["research"] = {
                "by": "tester", "at": "2026-01-01T00:00:00Z", "done": True,
                "artifacts": [{"path": "research/inventory.md", "sha256": artifact_sha256(artifact_path)}],
            }
            path = wdd / "state.json"
            _write_v5_state(path, state)

            apply_migration(path)
            self.assertTrue((wdd / "epics" / "demo" / "research" / "inventory.md").exists())
            self.assertFalse(artifact_path.exists())

    def test_unrecorded_research_directory_contents_are_left_alone(self) -> None:
        # Only artifacts RECORDED in intake.research.artifacts[] move (spec
        # Sec4) -- a stray file in the flat research/ dir that was never
        # recorded is not part of the migration table.
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            stray = wdd / "research" / "scratch.md"
            _write_text(stray, "scratch\n")
            state = _v5_state(legacy=True, tasks={})
            path = wdd / "state.json"
            _write_v5_state(path, state)

            apply_migration(path)
            self.assertTrue(stray.exists())


class MigrateReservedNameRefusalTest(unittest.TestCase):
    """Migration refuses to move a file onto the reserved 'record.json'
    name, naming the file and a rename remedy (spec Sec4); nothing is
    moved and state.json is untouched -- the refusal happens entirely in
    memory before any physical side effect."""

    def test_refuses_moving_a_file_named_record_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            _write_text(wdd / "tasks" / "record.json", "{}\n")
            task = task_state("TASK-001", spec_path="tasks/record.json")
            state = _v5_state(legacy=True, tasks={"TASK-001": task})
            path = wdd / "state.json"
            _write_v5_state(path, state)
            original = path.read_text()

            with self.assertRaises(ValidationError) as ctx:
                apply_migration(path)
            message = str(ctx.exception)
            self.assertIn("record.json", message)
            self.assertIn("rename", message)

            self.assertTrue((wdd / "tasks" / "record.json").exists())
            self.assertEqual(path.read_text(), original)
            self.assertFalse((wdd / "epics").exists())


class MigrateConfigureStampTest(unittest.TestCase):
    """intake.configure gains migration's exemption stamp -- the full
    effective-config digest at migration time -- on both non-legacy and
    legacy scopes (spec Sec4); drift is still guarded at the digest level
    from then on."""

    def test_non_legacy_scope_gains_configure_exemption_with_full_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            state = _v5_state(legacy=False, tasks={})
            path = wdd / "state.json"
            _write_v5_state(path, state)

            apply_migration(path)
            migrated = json.loads(path.read_text())
            configure = migrated["intake"]["configure"]
            self.assertIs(configure["legacy"], True)
            layers = load_layers(wdd, migrated["epic"])
            self.assertEqual(configure["sha256"], effective_config_digest(layers["effective"]))
            # Real intake records are untouched by the exemption.
            self.assertIn("spec", migrated["intake"])
            self.assertIn("design", migrated["intake"])

    def test_legacy_scope_keeps_legacy_and_gains_configure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            state = _v5_state(legacy=True, tasks={})
            path = wdd / "state.json"
            _write_v5_state(path, state)

            apply_migration(path)
            migrated = json.loads(path.read_text())
            self.assertIs(migrated["intake"]["legacy"], True)
            self.assertIs(migrated["intake"]["configure"]["legacy"], True)

    def test_configure_stamp_drifts_at_the_digest_level_after_a_global_config_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            state = _v5_state(legacy=False, tasks={})
            path = wdd / "state.json"
            _write_v5_state(path, state)
            apply_migration(path)
            migrated = json.loads(path.read_text())
            stamped = migrated["intake"]["configure"]["sha256"]

            config = load_config(wdd)
            config["merge"]["surface"] = "local"
            save_config(wdd, config)
            layers = load_layers(wdd, migrated["epic"])
            current = effective_config_digest(layers["effective"])
            self.assertNotEqual(stamped, current)


def _migrated_ratified_wdd(tmp: str, *, legacy: bool) -> tuple[Path, str]:
    """A migrated (v5->v6) scope whose ratification fingerprint is REAL
    (computed over an actual config.json + constitution.md), so governance
    drift never fires on its own -- isolating epic_config_drift for these
    tests from the (already-covered, ChokepointPrecedenceTest) interplay
    where a global-config edit trips both gates at once."""
    wdd = _wdd_root(tmp)
    # Matches _ratified_repo's convention: default_config()'s merge.surface
    # is already "pr", so an overlay/global edit TO "pr" below is only a
    # genuine change (and therefore genuine drift) starting from "local".
    save_config(wdd, {**load_config(wdd), "merge": {**load_config(wdd)["merge"], "surface": "local"}})
    (wdd / "constitution.md").write_text("# Constitution\n\nBe good.\n", encoding="utf-8")
    fingerprint = governance_fingerprint(wdd)
    state = _v5_state(legacy=legacy, tasks={})
    state["constitution"]["ratification"]["decisionFingerprint"] = fingerprint
    path = wdd / "state.json"
    _write_v5_state(path, state)
    apply_migration(path)
    return wdd, str(path)


class EpicConfigDriftAfterMigrationTest(unittest.TestCase):
    """F1 regression: migration's exemption stamp (`configure: {"legacy":
    true, "sha256": ...}`) covers only the missing human attribution --
    drift is still guarded ordinarily from there (spec Sec4). Before the
    fix, `epic_config_drift` returned None unconditionally for ANY scope
    whose `configure` carried `legacy: true`, and for ANY scope with
    `intake.legacy` at all -- silently disabling drift detection for every
    migrated scope. Covers both a migrated non-legacy scope (overlay edit)
    and a wholesale-legacy scope (global config edit, per Task 3's identical
    stamp shape on both)."""

    def test_migrated_non_legacy_scope_overlay_edit_trips_drift_and_is_remedied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd, state = _migrated_ratified_wdd(tmp, legacy=False)
            migrated = json.loads(Path(state).read_text())
            self.assertIsNone(epic_config_drift(migrated, wdd))

            assert _cli(state, "config", "set", "--epic", "merge.surface", "pr")[0] == 0
            edited = StateStore(Path(state)).read()
            self.assertIsNotNone(epic_config_drift(edited, wdd))

            # Chokepoint refuses a governed verb, naming the remedy.
            code, _out, err = _cli_full(state, "reconcile", "done")
            self.assertNotEqual(code, 0)
            self.assertIn("epic config drift", err)
            self.assertIn("intake configure", err)

            # `next` surfaces the same blocker.
            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["blockers"][0]["code"], "epic_config_drift")

            # A real, attributed re-approval clears the drift and replaces
            # the migration-stamped exemption with a real record.
            assert _cli(state, "intake", "configure", "--approved-by", "carol")[0] == 0
            reapproved = StateStore(Path(state)).read()
            self.assertIsNone(epic_config_drift(reapproved, wdd))
            configure = reapproved["intake"]["configure"]
            self.assertEqual(configure["by"], "carol")
            self.assertNotIn("legacy", configure)

    def test_wholesale_legacy_scope_global_config_edit_trips_drift_and_is_remedied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd, state = _migrated_ratified_wdd(tmp, legacy=True)
            migrated = json.loads(Path(state).read_text())
            self.assertIsNone(epic_config_drift(migrated, wdd))

            config = load_config(wdd)
            config["merge"]["surface"] = "pr"
            save_config(wdd, config)
            edited = StateStore(Path(state)).read()
            self.assertIsNotNone(epic_config_drift(edited, wdd))

            # A global-config edit also trips governance drift (it feeds
            # governance_fingerprint too); re-sign governance first so the
            # chokepoint's epic-config gate -- the one this test targets --
            # is what actually fires, isolating it from the (separately
            # covered) governance-outranks-epic-config precedence case.
            assert _cli(state, "constitution", "amend", "--by", "t2")[0] == 0
            reamended = StateStore(Path(state)).read()
            self.assertIsNotNone(epic_config_drift(reamended, wdd))

            code, _out, err = _cli_full(state, "reconcile", "done")
            self.assertNotEqual(code, 0)
            self.assertIn("epic config drift", err)
            self.assertIn("intake configure", err)

            # The remedy must be reachable for a wholesale-legacy scope too:
            # `intake configure` is not part of the ladder it is exempt
            # from, so it stays legal here even though spec/research/design
            # remain refused.
            assert _cli(state, "intake", "configure", "--approved-by", "carol")[0] == 0
            reapproved = StateStore(Path(state)).read()
            self.assertIsNone(epic_config_drift(reapproved, wdd))
            self.assertIs(reapproved["intake"]["legacy"], True)
            self.assertEqual(reapproved["intake"]["configure"]["by"], "carol")


class MigrateEvidenceStampTest(unittest.TestCase):
    """Existing review/verification records (task- and finalize-level)
    are stamped with migration-time projected digests, and task review
    additionally gains resolvedRisk/reviewModel from current state (spec
    Sec2/Sec4)."""

    def test_task_review_and_verification_gain_projection_digests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            task = task_state("TASK-001", risk="high")
            task["review"] = {"baseSha": "a" * 40, "headSha": "b" * 40, "findings": [], "reviewer": "alice"}
            task["verification"] = {
                "baseSha": "a" * 40, "headSha": "b" * 40, "status": "passed", "command": "pytest -q",
            }
            state = _v5_state(legacy=True, tasks={"TASK-001": task})
            path = wdd / "state.json"
            _write_v5_state(path, state)

            apply_migration(path)
            migrated = json.loads(path.read_text())
            review = migrated["tasks"]["TASK-001"]["review"]
            verification = migrated["tasks"]["TASK-001"]["verification"]
            self.assertEqual(review["resolvedRisk"], "high")

            layers = load_layers(wdd, migrated["epic"])
            self.assertEqual(
                review["configSha256"],
                effective_config_digest(project(layers["effective"], "taskReview")),
            )
            self.assertEqual(
                verification["configSha256"],
                effective_config_digest(project(layers["effective"], "taskVerification")),
            )

    def test_task_review_uses_task_override_review_model_when_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            task = task_state("TASK-001", review_model="gpt-review-override")
            task["review"] = {"baseSha": "a" * 40, "headSha": "b" * 40, "findings": [], "reviewer": "alice"}
            state = _v5_state(legacy=True, tasks={"TASK-001": task})
            path = wdd / "state.json"
            _write_v5_state(path, state)

            apply_migration(path)
            migrated = json.loads(path.read_text())
            self.assertEqual(
                migrated["tasks"]["TASK-001"]["review"]["reviewModel"], "gpt-review-override"
            )

    def test_tasks_without_review_or_verification_are_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            task = task_state("TASK-001")
            state = _v5_state(legacy=True, tasks={"TASK-001": task})
            path = wdd / "state.json"
            _write_v5_state(path, state)

            apply_migration(path)
            migrated = json.loads(path.read_text())
            self.assertIsNone(migrated["tasks"]["TASK-001"]["review"])
            self.assertIsNone(migrated["tasks"]["TASK-001"]["verification"])

    def test_finalize_review_and_verification_gain_digests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            state = _v5_state(legacy=True, tasks={})
            state["finalize"] = {
                "review": {
                    "headSha": "a" * 40, "outcome": "passed", "findings": [],
                    "reviewer": "alice", "at": "2026-01-01T00:00:00Z",
                },
                "verification": {
                    "headSha": "a" * 40, "status": "passed", "command": "pytest -q",
                    "justification": None, "at": "2026-01-01T00:00:00Z",
                },
            }
            path = wdd / "state.json"
            _write_v5_state(path, state)

            apply_migration(path)
            migrated = json.loads(path.read_text())
            layers = load_layers(wdd, migrated["epic"])
            self.assertEqual(
                migrated["finalize"]["review"]["configSha256"],
                effective_config_digest(project(layers["effective"], "finalReview")),
            )
            # Task 5: finalVerification's digest additionally covers the epic
            # deliverable command (spec Sec2) -- None here, since this is a
            # legacy scope with no recorded intake.design.
            from wave_delivery.finalize import _final_verification_projection_digest

            self.assertEqual(
                migrated["finalize"]["verification"]["configSha256"],
                _final_verification_projection_digest(layers["effective"], None),
            )
            self.assertNotIn("resolvedRisk", migrated["finalize"]["review"])


class MigrateAttemptManifestTest(unittest.TestCase):
    """manifest.json is written into every existing attempt snapshot dir,
    naming the brief file (spec Sec4); runner.py's dispatch assembler
    prefers it."""

    def test_manifest_written_naming_the_brief(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            _write_text(wdd / "tasks" / "TASK-001.md", "brief\n")
            snapshot_dir = wdd / "dispatch" / "TASK-001-1"
            _write_text(snapshot_dir / "tasks" / "TASK-001.md", "brief\n")
            task = task_state("TASK-001")
            task["snapshot"] = "dispatch/TASK-001-1"
            task["inputs"] = [{"path": "tasks/TASK-001.md", "sha256": "sha256:" + "a" * 64}]
            state = _v5_state(legacy=True, tasks={"TASK-001": task})
            path = wdd / "state.json"
            _write_v5_state(path, state)

            apply_migration(path)
            manifest_path = snapshot_dir / "manifest.json"
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["brief"], "tasks/TASK-001.md")

    def test_no_snapshot_means_no_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            _write_text(wdd / "tasks" / "TASK-001.md", "brief\n")
            state = _v5_state(legacy=True, tasks={"TASK-001": task_state("TASK-001")})
            path = wdd / "state.json"
            _write_v5_state(path, state)

            apply_migration(path)
            self.assertFalse((wdd / "dispatch").exists())

    def test_dispatch_prefers_manifest_over_stale_relative_match(self) -> None:
        # Exercises runner.py's _snapshot_files directly: once a manifest
        # names the brief, it wins even when a DIFFERENT file happens to sit
        # at the exact namespace-relative path the (now-stale) specPath names.
        from wave_delivery.runner import _snapshot_files

        with tempfile.TemporaryDirectory() as tmp:
            snapshot_dir = Path(tmp) / "snap"
            _write_text(snapshot_dir / "tasks" / "TASK-001.md", "STALE\n")
            _write_text(snapshot_dir / "the-real-brief.md", "REAL\n")
            (snapshot_dir / "manifest.json").write_text(
                json.dumps({"brief": "the-real-brief.md"}), encoding="utf-8"
            )
            brief_path, context_paths = _snapshot_files(snapshot_dir, Path("tasks/TASK-001.md"))
            self.assertEqual(brief_path.read_text(), "REAL\n")
            # manifest.json itself is never handed to the worker as context.
            self.assertNotIn(snapshot_dir / "manifest.json", context_paths)

    def test_dispatch_falls_back_to_basename_matching_without_a_manifest(self) -> None:
        # Pre-manifest snapshot dir: no manifest.json at all. The brief file
        # sits at a DIFFERENT relative path than specPath currently names
        # (as a migrated-but-never-remanifested snapshot might), so the
        # exact-relative-path match misses -- basename matching finds it by
        # filename instead of ever guessing lexicographically.
        from wave_delivery.runner import _snapshot_files

        with tempfile.TemporaryDirectory() as tmp:
            snapshot_dir = Path(tmp) / "snap"
            _write_text(snapshot_dir / "epics" / "demo" / "tasks" / "TASK-001.md", "REAL\n")
            _write_text(snapshot_dir / "context.md", "context\n")
            brief_path, context_paths = _snapshot_files(snapshot_dir, Path("tasks/TASK-001.md"))
            self.assertEqual(brief_path.name, "TASK-001.md")
            self.assertEqual(brief_path.read_text(), "REAL\n")
            self.assertEqual([p.name for p in context_paths], ["context.md"])


class MigrateIdempotencyTest(unittest.TestCase):
    """Re-running migrate on an already-v6 state is refused, not silently
    re-applied -- the no-op contract (spec Sec4: "migrate is idempotent")."""

    def test_rerunning_migrate_on_a_v6_state_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            state = _v5_state(legacy=True, tasks={})
            path = wdd / "state.json"
            _write_v5_state(path, state)

            apply_migration(path)
            with self.assertRaises(ValidationError) as ctx:
                apply_migration(path)
            self.assertIn(f"already schema v{SCHEMA_VERSION}", str(ctx.exception))


class MigratePlanIsPureTest(unittest.TestCase):
    """`plan_migration`/`--dry-run` never writes state.json or moves a
    single file -- the v5 -> v6 step's move-planning only reads the
    filesystem (Global Constraints: dry-run first)."""

    def test_plan_migration_does_not_touch_the_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            _write_text(wdd / "tasks" / "TASK-001.md", "brief\n")
            state = _v5_state(legacy=True, tasks={"TASK-001": task_state("TASK-001")})
            path = wdd / "state.json"
            _write_v5_state(path, state)
            original = path.read_text()

            result = plan_migration(path)
            self.assertEqual(result["to"], 6)
            self.assertEqual(path.read_text(), original)
            self.assertTrue((wdd / "tasks" / "TASK-001.md").exists())
            self.assertFalse((wdd / "epics").exists())


class MigrateInputGateGreenTest(unittest.TestCase):
    """Post-migration, a recorded input digest still matches the (moved)
    file's bytes at its new, epic-namespaced location -- input binding
    stays green across migration (spec Sec4)."""

    def test_recorded_input_digest_still_resolves_and_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            brief = wdd / "tasks" / "TASK-001.md"
            _write_text(brief, "brief body\n")
            digest = artifact_sha256(brief)
            task = task_state("TASK-001")
            task["inputs"] = [{"path": "tasks/TASK-001.md", "sha256": digest}]
            state = _v5_state(legacy=False, tasks={"TASK-001": task})
            path = wdd / "state.json"
            _write_v5_state(path, state)

            apply_migration(path)
            migrated = json.loads(path.read_text())
            slug = migrated["epic"]
            recorded = migrated["tasks"]["TASK-001"]["inputs"][0]
            resolved = resolve_artifact(recorded["path"], wdd_dir=wdd, epic=slug)
            self.assertTrue(resolved.exists())
            self.assertEqual(artifact_sha256(resolved), recorded["sha256"])
            # The REAL gate, not a hand-rolled equivalent: inputs_status must
            # come back clean post-migration (Task 3 review caught the
            # hand-rolled check passing while the production gate failed).
            from wave_delivery.handover import inputs_status

            self.assertIsNone(inputs_status(migrated, wdd, "TASK-001"))


class MigrateArchivedRecordsUntouchedTest(unittest.TestCase):
    """Archived v5 records stay readable where they are -- migration never
    scans or touches archive/ (spec Sec4)."""

    def test_flat_archive_dir_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            archived_record = wdd / "archive" / "old-epic" / "record.json"
            _write_text(archived_record, '{"kind": "old"}\n')
            state = _v5_state(legacy=True, tasks={})
            path = wdd / "state.json"
            _write_v5_state(path, state)

            apply_migration(path)
            self.assertEqual(archived_record.read_text(), '{"kind": "old"}\n')


class MigrateTaskStatusMatrixTest(unittest.TestCase):
    """Spec Sec4's test contract: every task status x legacy/non-legacy
    intake x with/without an attempt snapshot migrates cleanly to a
    v6-valid state."""

    def test_full_matrix(self) -> None:
        for status in sorted(TASK_STATUSES):
            for legacy in (True, False):
                for with_snapshot in (True, False):
                    with self.subTest(status=status, legacy=legacy, snapshot=with_snapshot):
                        with tempfile.TemporaryDirectory() as tmp:
                            wdd = _wdd_root(tmp)
                            _write_text(wdd / "tasks" / "TASK-001.md", "brief\n")
                            task = task_state("TASK-001")
                            task["status"] = status
                            task["review"] = {
                                "baseSha": "a" * 40, "headSha": "b" * 40,
                                "findings": [], "reviewer": "alice",
                            }
                            task["verification"] = {
                                "baseSha": "a" * 40, "headSha": "b" * 40,
                                "status": "passed", "command": "pytest -q",
                            }
                            if with_snapshot:
                                snapshot_dir = wdd / "dispatch" / "TASK-001-1"
                                _write_text(snapshot_dir / "tasks" / "TASK-001.md", "brief\n")
                                task["snapshot"] = "dispatch/TASK-001-1"
                                task["inputs"] = [
                                    {"path": "tasks/TASK-001.md", "sha256": "sha256:" + "a" * 64}
                                ]
                            state = _v5_state(legacy=legacy, tasks={"TASK-001": task})
                            path = wdd / "state.json"
                            _write_v5_state(path, state)

                            apply_migration(path)
                            migrated = StateStore(path).read()  # validates v6 shape

                            self.assertEqual(migrated["tasks"]["TASK-001"]["status"], status)
                            self.assertTrue(
                                (wdd / "epics" / migrated["epic"] / "tasks" / "TASK-001.md").exists()
                            )
                            if with_snapshot:
                                self.assertTrue(
                                    (wdd / "dispatch" / "TASK-001-1" / "manifest.json").exists()
                                )


class MigrateComposedFromEarlierVersionsTest(unittest.TestCase):
    """v2/v3/v4 sources still chain all the way through to v6 in one
    `migrate` call (Global Constraints: composing the whole chain, mirrored
    from the pre-existing v4 -> v5 composition in migration.py)."""

    def test_supported_source_versions_include_v5(self) -> None:
        self.assertEqual(SUPPORTED_SOURCE_VERSIONS, {2, 3, 4, 5})

    def test_v5_source_reaches_v6_directly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = _wdd_root(tmp)
            state = _v5_state(legacy=True, tasks={})
            path = wdd / "state.json"
            _write_v5_state(path, state)
            source = read_source(path)
            migrated = convert_v5_to_v6(source, wdd_dir=wdd)
            self.assertEqual(migrated["schemaVersion"], 6)
            validate_state(migrated)

    def test_v5_to_v6_conversion_refuses_a_non_v5_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValidationError):
                convert_v5_to_v6(new_state("SCOPE-x"), wdd_dir=Path(tmp))


# --- Task 4: epic lifecycle -- `epic new`, ladder wiring, flat-path -------
# retirement (spec Sec1 "the slug is born at the top of the ladder").
# Local helpers below copy the scratch-repo / `_cli` pattern from
# tests/test_intake.py verbatim (no cross-file imports between test
# modules, per the plan's Global Constraints).


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
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(["--state", state, *argv])
    return code, stdout.getvalue(), stderr.getvalue()


def _ratified_repo(tmp: str) -> tuple[Path, str]:
    """A fresh git repo with .wdd/ initialized and the constitution ratified
    -- no epic, no scope. Mirrors test_intake.py's fixture of the same name."""
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


def _epic_repo(tmp: str, slug: str = "demo") -> tuple[Path, str]:
    """`_ratified_repo` plus one adopted epic -- the fixture most Task 4
    tests build on."""
    root, state = _ratified_repo(tmp)
    assert _cli(state, "epic", "new", "--slug", slug)[0] == 0
    return root, state


def _ratified_repo_pr_surface(tmp: str) -> tuple[Path, str]:
    """`_ratified_repo`, but keeps `merge.surface` at its built-in default
    ("pr") instead of overriding it to "local" -- Task 5 fix-round F2's
    "an epic overlay's merge.surface=local overrides a global pr config"
    regressions need a global value to actually override."""
    root = _git_repo(tmp)
    wdd = root / ".wdd"
    state = str(wdd / "state.json")
    assert _cli(state, "init", "--repo", str(root))[0] == 0
    # Resolves the merge.surface open question WITHOUT changing its value
    # (the whole point of this fixture): default_config()'s "pr" set back
    # to itself.
    assert _cli(state, "config", "set", "merge.surface", "pr")[0] == 0
    assert _cli(
        state, "config", "set", "models",
        '{"planning": null, "implementation": {"default": null, "highRisk": null}, "review": null}',
    )[0] == 0
    config = load_config(wdd)
    if any(q["path"] == "verification.commands" for q in config["openQuestions"]):
        assert _cli(state, "config", "set", "verification.commands", '["true"]')[0] == 0
    assert _cli(state, "constitution", "ratify", "--by", "t")[0] == 0
    return root, state


def _spec_text(ac_lines: tuple[str, ...] = ("- [ ] AC-1: the thing works",)) -> str:
    return (
        "# Spec\n\n## Goal\n\nShip it.\n\n## In scope\n\n- x\n\n"
        "## Out of scope\n\n- y\n\n## Acceptance criteria\n\n" + "\n".join(ac_lines) + "\n"
    )


def _design_text() -> str:
    return (
        "# Design\n\n## Components\n\n- core\n\n## Interfaces\n\n"
        "- core: consumes nothing, produces lib\n\n## Integration surfaces\n\n"
        "- `src/core.py` — owned by: core\n\n## Epic deliverable\n\nThe lib imports.\n"
    )


def _plan_document(
    task_ids: tuple[str, ...] | list[str], *, scope_id: str, base_ref: str | None = None
) -> dict:
    scope: dict = {"id": scope_id}
    if base_ref is not None:
        scope["baseRef"] = base_ref
    return {
        "schemaVersion": 1,
        "kind": "wdd_plan",
        "scope": scope,
        "tasks": [{"id": task_id, "specPath": f"tasks/{task_id}.md"} for task_id in task_ids],
    }


class EpicNewLadderOrderTest(unittest.TestCase):
    """Test contract: create_epic is emitted post-ratify, pre-agree_spec."""

    def test_next_emits_create_epic_before_any_intake_rung(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            action = json.loads(out)["actions"][0]
            self.assertEqual(action["action"], "create_epic")
            self.assertIn("epic new", action["command"])

    def test_next_emits_configure_epic_immediately_after_epic_new(self) -> None:
        # Task 5 (spec Sec2): configure_epic is the middle rung, between
        # create_epic and agree_spec.
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp)
            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            action = json.loads(out)["actions"][0]
            self.assertEqual(action["action"], "configure_epic")
            self.assertIn("intake configure", action["recordWith"])

    def test_next_emits_agree_spec_immediately_after_configure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp)
            assert _cli(state, "intake", "configure", "--use-defaults", "--by", "t")[0] == 0
            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)["actions"][0]["action"], "agree_spec")

    def test_epic_new_sets_state_epic_and_creates_sparse_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            code, out = _cli(state, "epic", "new", "--slug", "demo")
            self.assertEqual(code, 0, out)
            payload = json.loads(out)
            self.assertEqual(payload["epic"], "demo")
            written = StateStore(Path(state)).read()
            self.assertEqual(written["epic"], "demo")
            wdd = root / ".wdd"
            self.assertEqual(load_overlay(wdd, "demo"), {})
            self.assertTrue((wdd / "epics" / "demo" / "config.json").is_file())


class EpicNewUniquenessTest(unittest.TestCase):
    """Test contract: uniqueness incl. archived slugs."""

    def test_refuses_when_an_epic_is_already_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp, "demo")
            code, out, err = _cli_full(state, "epic", "new", "--slug", "second")
            self.assertNotEqual(code, 0)
            self.assertIn("already active", err)
            self.assertEqual(StateStore(Path(state)).read()["epic"], "demo")

    def test_refuses_slug_matching_an_archived_epic_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "archive" / "demo").mkdir(parents=True)
            (wdd / "archive" / "demo" / "record.json").write_text("{}\n", encoding="utf-8")
            code, out, err = _cli_full(state, "epic", "new", "--slug", "demo")
            self.assertNotEqual(code, 0)
            self.assertIn("archived", err)
            self.assertIsNone(StateStore(Path(state)).read()["epic"])

    def test_refuses_target_dir_containing_record_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "epics" / "demo").mkdir(parents=True)
            (wdd / "epics" / "demo" / "record.json").write_text("{}\n", encoding="utf-8")
            code, out, err = _cli_full(state, "epic", "new", "--slug", "demo")
            self.assertNotEqual(code, 0)
            self.assertIn("record.json", err)
            self.assertIsNone(StateStore(Path(state)).read()["epic"])

    def test_refuses_occupied_directory_that_is_not_the_crash_orphan_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "epics" / "demo" / "tasks").mkdir(parents=True)
            (wdd / "epics" / "demo" / "tasks" / "T1.md").write_text("# T1\n", encoding="utf-8")
            code, out, err = _cli_full(state, "epic", "new", "--slug", "demo")
            self.assertNotEqual(code, 0)
            self.assertIn("demo", err)
            self.assertIsNone(StateStore(Path(state)).read()["epic"])


class EpicNewRefusalCasesTest(unittest.TestCase):
    """Test contract: refusal cases (active epic, record.json present, bad slug)."""

    def test_refuses_uppercase_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            code, out, err = _cli_full(state, "epic", "new", "--slug", "Demo")
            self.assertNotEqual(code, 0)
            self.assertIn("slug", err)

    def test_refuses_single_character_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            code, out, err = _cli_full(state, "epic", "new", "--slug", "d")
            self.assertNotEqual(code, 0)

    def test_refuses_slug_with_illegal_characters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            code, out, err = _cli_full(state, "epic", "new", "--slug", "demo_epic!")
            self.assertNotEqual(code, 0)

    def test_valid_slug_boundary_two_chars_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            code, out = _cli(state, "epic", "new", "--slug", "d1")
            self.assertEqual(code, 0, out)


class EpicNewCrashOrphanAdoptionTest(unittest.TestCase):
    """Test contract: crash-shape adoption idempotency."""

    def test_adopts_a_directory_holding_only_an_empty_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            # Simulate a crash between mkdir+overlay-write and state.epic
            # adoption: the directory holds ONLY an empty overlay.
            save_overlay(wdd, "demo", {})
            self.assertIsNone(StateStore(Path(state)).read()["epic"])

            code, out = _cli(state, "epic", "new", "--slug", "demo")
            self.assertEqual(code, 0, out)
            self.assertEqual(StateStore(Path(state)).read()["epic"], "demo")

    def test_adoption_is_re_runnable(self) -> None:
        """Running `epic new` on an already-adopted epic a second time (the
        exact command, before archiving) is refused as "already active" --
        adoption only covers the UNADOPTED crash-orphan shape, not a
        re-run once state.epic already names it."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp, "demo")
            code, out, err = _cli_full(state, "epic", "new", "--slug", "demo")
            self.assertNotEqual(code, 0)
            self.assertIn("already active", err)


class EpicScopeIdDerivationTest(unittest.TestCase):
    """Test contract: scope-id derivation + mismatch rejection."""

    def _walk_to_plan(self, state: str, epic_dir: Path) -> None:
        assert _cli(state, "intake", "configure", "--use-defaults", "--by", "t")[0] == 0
        (epic_dir / "spec.md").write_text(_spec_text(), encoding="utf-8")
        assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
        assert _cli(
            state, "intake", "research", "--skip", "--by", "t", "--reason", "n/a"
        )[0] == 0
        (epic_dir / "design.md").write_text(_design_text(), encoding="utf-8")
        assert _cli(
            state, "intake", "design", "--approved-by", "t", "--deliverable-command", "true"
        )[0] == 0

    def test_intake_rungs_refuse_without_an_active_epic(self) -> None:
        # Task 4 review, Important: the ladder was walkable flat (no epic
        # new), silently defeating the SCOPE-<slug> invariant. The rung
        # verbs themselves must refuse and name the remedy.
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            (root / ".wdd" / "spec.md").write_text(_spec_text(), encoding="utf-8")
            code, _out, err = _cli_full(state, "intake", "spec", "--approved-by", "t")
            self.assertNotEqual(code, 0)
            self.assertIn("wddctl epic new", err)

    def test_plan_apply_rejects_a_scope_id_other_than_scope_dash_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp, "demo")
            wdd = root / ".wdd"
            epic_dir = wdd / "epics" / "demo"
            self._walk_to_plan(state, epic_dir)
            (epic_dir / "tasks").mkdir(parents=True, exist_ok=True)
            (epic_dir / "tasks" / "T1.md").write_text("# T1\n", encoding="utf-8")
            plan_file = root / "plan.json"
            plan_file.write_text(
                json.dumps(_plan_document(["T1"], scope_id="SCOPE-wrong", base_ref="wdd/demo")),
                encoding="utf-8",
            )
            code, out, err = _cli_full(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertNotEqual(code, 0)
            self.assertIn("SCOPE-demo", err)
            self.assertIn("SCOPE-wrong", err)
            self.assertIsNone(StateStore(Path(state)).read()["scope"])

    def test_plan_apply_accepts_the_epic_derived_scope_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp, "demo")
            wdd = root / ".wdd"
            epic_dir = wdd / "epics" / "demo"
            self._walk_to_plan(state, epic_dir)
            (epic_dir / "tasks").mkdir(parents=True, exist_ok=True)
            (epic_dir / "tasks" / "T1.md").write_text("# T1\n", encoding="utf-8")
            plan_file = root / "plan.json"
            plan_file.write_text(
                json.dumps(_plan_document(["T1"], scope_id="SCOPE-demo", base_ref="wdd/demo")),
                encoding="utf-8",
            )
            code, out, err = _cli_full(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, err)
            self.assertEqual(StateStore(Path(state)).read()["scope"]["id"], "SCOPE-demo")


class EpicV6NoFlatFallbackRegressionTest(unittest.TestCase):
    """Test contract regression: flat tasks/T.md present but
    epics/<slug>/tasks/T.md absent -> refusal naming the epic path, no
    silent fallback (spec Sec1)."""

    def test_resolve_artifact_never_falls_back_to_flat_for_an_active_epic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            (wdd / "tasks").mkdir(parents=True)
            (wdd / "tasks" / "T1.md").write_text("# flat, decoy\n", encoding="utf-8")
            # No epics/demo/tasks/T1.md written -- the epic-namespaced path
            # is absent. Resolution must point AT that (still-missing) path,
            # never quietly return the flat decoy above.
            resolved = resolve_artifact("tasks/T1.md", wdd_dir=wdd, epic="demo")
            self.assertEqual(resolved, (wdd / "epics" / "demo" / "tasks" / "T1.md").resolve())
            self.assertFalse(resolved.exists())

    def test_intake_spec_refuses_naming_the_epic_path_when_only_flat_spec_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp, "demo")
            assert _cli(state, "intake", "configure", "--use-defaults", "--by", "t")[0] == 0
            wdd = root / ".wdd"
            # Only the flat decoy exists; epics/demo/spec.md does not -- the
            # verb must refuse rather than silently reading the flat file.
            (wdd / "spec.md").write_text(_spec_text(), encoding="utf-8")
            code, out, err = _cli_full(state, "intake", "spec", "--approved-by", "t")
            self.assertNotEqual(code, 0)
            self.assertIn("spec.md does not exist", err)
            # Once written to the correctly epic-scoped path, the SAME verb
            # succeeds -- proving the earlier refusal was resolution, not a
            # coincidental content/validation failure.
            (wdd / "epics" / "demo" / "spec.md").write_text(_spec_text(), encoding="utf-8")
            code, out, err = _cli_full(state, "intake", "spec", "--approved-by", "t")
            self.assertEqual(code, 0, err)

    def test_handover_input_sources_refuse_flat_fallback_for_an_active_epic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp, "demo")
            wdd = root / ".wdd"
            epic_dir = wdd / "epics" / "demo"
            self._walk_and_plan(state, root, epic_dir)
            # Delete the epic-scoped brief and leave only a flat decoy --
            # `start` must refuse (missing brief), not silently pick up the
            # flat file.
            (epic_dir / "tasks" / "T1.md").unlink()
            (wdd / "tasks").mkdir(parents=True, exist_ok=True)
            (wdd / "tasks" / "T1.md").write_text("# flat decoy\n", encoding="utf-8")
            code, out, err = _cli_full(state, "start", "--task", "T1", "--repo", str(root))
            self.assertNotEqual(code, 0)

    def _walk_and_plan(self, state: str, root: Path, epic_dir: Path) -> None:
        assert _cli(state, "intake", "configure", "--use-defaults", "--by", "t")[0] == 0
        (epic_dir / "spec.md").write_text(_spec_text(), encoding="utf-8")
        assert _cli(state, "intake", "spec", "--approved-by", "t")[0] == 0
        assert _cli(
            state, "intake", "research", "--skip", "--by", "t", "--reason", "n/a"
        )[0] == 0
        (epic_dir / "design.md").write_text(_design_text(), encoding="utf-8")
        assert _cli(
            state, "intake", "design", "--approved-by", "t", "--deliverable-command", "true"
        )[0] == 0
        (epic_dir / "tasks").mkdir(parents=True, exist_ok=True)
        (epic_dir / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
        plan_file = root / "plan.json"
        plan_file.write_text(
            json.dumps(_plan_document(["T1"], scope_id="SCOPE-demo", base_ref="wdd/demo")),
            encoding="utf-8",
        )
        assert _cli(
            state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
            "--approved-by", "t",
        )[0] == 0


class DoctorEpicOrphanTest(unittest.TestCase):
    """Test contract: doctor reports orphan epic dirs (exists, not
    state.epic, not archived)."""

    def test_no_orphans_reported_for_a_single_active_epic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp, "demo")
            report = inspect_capabilities(root / ".wdd", StateStore(Path(state)).read())
            self.assertEqual(report["epicOrphans"], [])

    def test_reports_a_directory_that_is_not_the_active_epic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp, "demo")
            wdd = root / ".wdd"
            # A leftover directory -- e.g. a crashed archive transaction
            # (Task 6) or an unadopted `epic new` -- left behind under
            # epics/ while a DIFFERENT epic is active.
            (wdd / "epics" / "orphaned").mkdir(parents=True)
            report = inspect_capabilities(wdd, StateStore(Path(state)).read())
            self.assertEqual(report["epicOrphans"], ["orphaned"])

    def test_reports_every_epic_dir_when_no_epic_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            (wdd / "epics" / "stray").mkdir(parents=True)
            report = inspect_capabilities(wdd, StateStore(Path(state)).read())
            self.assertEqual(report["epicOrphans"], ["stray"])

    def test_doctor_never_refuses_on_an_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp, "demo")
            wdd = root / ".wdd"
            (wdd / "epics" / "orphaned").mkdir(parents=True)
            code, out = _cli(state, "doctor")
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)["epicOrphans"], ["orphaned"])


class EpicFullLadderAndPlanApplyE2ETest(unittest.TestCase):
    """Test contract: existing intake/plan flows work end-to-end against
    epics/<slug>/ paths (walk a full ladder + plan apply in one test)."""

    def test_full_ladder_then_plan_apply_lands_under_epics_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"

            code, out = _cli(state, "next")
            self.assertEqual(json.loads(out)["actions"][0]["action"], "create_epic")
            assert _cli(state, "epic", "new", "--slug", "checkout-v2")[0] == 0
            epic_dir = wdd / "epics" / "checkout-v2"

            code, out = _cli(state, "next")
            self.assertEqual(json.loads(out)["actions"][0]["action"], "configure_epic")
            assert _cli(state, "intake", "configure", "--use-defaults", "--by", "t")[0] == 0

            code, out = _cli(state, "next")
            self.assertEqual(json.loads(out)["actions"][0]["action"], "agree_spec")
            (epic_dir / "spec.md").write_text(
                _spec_text(ac_lines=("- [ ] AC-1: checkout completes",)), encoding="utf-8"
            )
            code, out = _cli(state, "intake", "spec", "--approved-by", "t")
            self.assertEqual(code, 0, out)

            code, out = _cli(state, "next")
            self.assertEqual(json.loads(out)["actions"][0]["action"], "research")
            assert _cli(
                state, "intake", "research", "--skip", "--by", "t", "--reason", "n/a"
            )[0] == 0

            code, out = _cli(state, "next")
            self.assertEqual(json.loads(out)["actions"][0]["action"], "agree_design")
            (epic_dir / "design.md").write_text(_design_text(), encoding="utf-8")
            assert _cli(
                state, "intake", "design", "--approved-by", "t",
                "--deliverable-command", "true",
            )[0] == 0

            code, out = _cli(state, "next")
            self.assertEqual(json.loads(out)["actions"][0]["action"], "plan")

            (epic_dir / "tasks").mkdir(parents=True, exist_ok=True)
            (epic_dir / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
            plan_file = root / "plan.json"
            plan_file.write_text(
                json.dumps(
                    _plan_document(["T1"], scope_id="SCOPE-checkout-v2", base_ref="wdd/checkout-v2")
                ),
                encoding="utf-8",
            )
            code, out = _cli(
                state, "plan", "apply", "--plan", str(plan_file), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, out)
            applied = StateStore(Path(state)).read()
            self.assertEqual(applied["scope"]["id"], "SCOPE-checkout-v2")
            self.assertEqual(applied["epic"], "checkout-v2")

            # lint sees the epic-scoped spec/brief/design without complaint.
            code, out = _cli(state, "plan", "lint", "--plan", str(plan_file))
            self.assertEqual(code, 0, out)
            findings = json.loads(out)["findings"]
            self.assertFalse(
                [f for f in findings if f["code"] in {"missing_spec", "missing_brief"}]
            )

            # next now proposes starting the task.
            code, out = _cli(state, "next", "--repo", str(root))
            actions = [action["action"] for action in json.loads(out)["actions"]]
            self.assertIn("start_task", actions)


# ---------------------------------------------------------------------------
# Task 5: configure gate, drift, and evidence binding (spec Sec2).
# ---------------------------------------------------------------------------


def _epic_ladder_to_plan(
    state: str,
    wdd: Path,
    root: Path,
    *,
    slug: str = "demo",
    approver: str = "t",
    task_domains: list[str] | None = None,
    review_policy: str = "risk_based",
    overlay: dict[str, str] | None = None,
) -> None:
    """epic new -> configure (--use-defaults) -> spec/research-skip/design ->
    a one-task, composite-approved plan apply. The Task 5 counterpart of
    test_intake.py's `_apply_ladder_and_plan`, written locally per this
    file's no-cross-file-imports convention. `task_domains` lets a caller
    steer T1 into a riskRule's pattern for the risk re-derivation scenarios.
    `overlay` (dotted path -> JSON-encoded value string) sets epic overlay
    leaves BEFORE `intake configure` runs -- Task 5 fix-round F2's "epic
    override reaches merge/dispatch" regressions need it approved, not left
    to trip epic_config_drift (F1's concern, not this one). Given a nonempty
    overlay, `configure` uses `--approved-by` (the "as currently written"
    form) instead of `--use-defaults`, which would reset the overlay to {}.
    """
    assert _cli(state, "epic", "new", "--slug", slug)[0] == 0
    for path, value in (overlay or {}).items():
        assert _cli(state, "config", "set", "--epic", path, value)[0] == 0
    if overlay:
        assert _cli(state, "intake", "configure", "--approved-by", approver)[0] == 0
    else:
        assert _cli(state, "intake", "configure", "--use-defaults", "--by", approver)[0] == 0
    epic_dir = wdd / "epics" / slug
    (epic_dir / "spec.md").write_text(_spec_text(), encoding="utf-8")
    assert _cli(state, "intake", "spec", "--approved-by", approver)[0] == 0
    assert _cli(
        state, "intake", "research", "--skip", "--by", approver, "--reason", "n/a"
    )[0] == 0
    (epic_dir / "design.md").write_text(_design_text(), encoding="utf-8")
    assert _cli(
        state, "intake", "design", "--approved-by", approver, "--deliverable-command", "true"
    )[0] == 0
    (epic_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (epic_dir / "tasks" / "T1.md").write_text("# T1\n\nBrief.\n", encoding="utf-8")
    plan = {
        "schemaVersion": 1,
        "kind": "wdd_plan",
        "scope": {
            "id": f"SCOPE-{slug}",
            "baseRef": f"wdd/{slug}",
            "reviewPolicy": review_policy,
        },
        "tasks": [
            {
                "id": "T1",
                "specPath": "tasks/T1.md",
                "conflictDomains": task_domains or [],
            }
        ],
    }
    plan_file = root / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
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


class IntakeConfigureVerbTest(unittest.TestCase):
    """Test contract: the two legal `intake configure` forms, sha256 over the
    derived post-mutation full view, and `agree_spec` refusing without it."""

    def test_agree_spec_refuses_until_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp, "demo")
            (root / ".wdd" / "epics" / "demo" / "spec.md").write_text(
                _spec_text(), encoding="utf-8"
            )
            code, out, err = _cli_full(state, "intake", "spec", "--approved-by", "t")
            self.assertNotEqual(code, 0)
            self.assertIn("intake configure", err)

    def test_approved_by_records_digest_over_current_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp, "demo")
            wdd = root / ".wdd"
            assert _cli(state, "config", "set", "--epic", "merge.surface", "pr")[0] == 0
            code, out = _cli(state, "intake", "configure", "--approved-by", "t")
            self.assertEqual(code, 0, out)
            recorded = StateStore(Path(state)).read()["intake"]["configure"]
            self.assertEqual(recorded["by"], "t")
            layers = load_layers(wdd, "demo")
            self.assertEqual(recorded["sha256"], effective_config_digest(layers["effective"]))
            # The overlay as written is what got approved, untouched by the
            # approval itself.
            self.assertEqual(load_overlay(wdd, "demo"), {"merge": {"surface": "pr"}})

    def test_use_defaults_records_digest_over_empty_overlay_and_resets_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp, "demo")
            wdd = root / ".wdd"
            assert _cli(state, "config", "set", "--epic", "merge.surface", "pr")[0] == 0
            code, out = _cli(state, "intake", "configure", "--use-defaults", "--by", "t")
            self.assertEqual(code, 0, out)
            # The explicit "inherit everything" decision resets the overlay
            # on disk -- a nonempty, unapproved overlay must never sit behind
            # an approval that claims defaults.
            self.assertEqual(load_overlay(wdd, "demo"), {})
            recorded = StateStore(Path(state)).read()["intake"]["configure"]
            layers = load_layers(wdd, "demo")
            derived = derive_effective(layers, {})
            self.assertEqual(recorded["sha256"], effective_config_digest(derived["effective"]))

    def test_exactly_one_form_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp, "demo")
            code, _out, err = _cli_full(state, "intake", "configure")
            self.assertNotEqual(code, 0)
            self.assertIn("exactly one", err)
            code, _out, err = _cli_full(
                state, "intake", "configure", "--approved-by", "t", "--use-defaults", "--by", "t"
            )
            self.assertNotEqual(code, 0)
            code, _out, err = _cli_full(state, "intake", "configure", "--use-defaults")
            self.assertNotEqual(code, 0)
            self.assertIn("--by", err)

    def test_reconfigure_clears_scope_approval_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _epic_ladder_to_plan(state, wdd, root)
            before = StateStore(Path(state)).read()
            self.assertIn("approval", before["scope"])
            spec_before = before["intake"]["spec"]
            design_before = before["intake"]["design"]

            code, out = _cli(state, "intake", "configure", "--approved-by", "t2")
            self.assertEqual(code, 0, out)
            after = StateStore(Path(state)).read()
            self.assertNotIn("approval", after["scope"])
            self.assertEqual(after["intake"]["spec"], spec_before)
            self.assertEqual(after["intake"]["design"], design_before)


class EpicConfigDriftTest(unittest.TestCase):
    """Test contract: overlay edit -> epic_config_drift blocker in `next`
    (actions emptied) and chokepoint refusal for governed verbs; an edit to
    an unrelated key stales nothing."""

    def test_overlay_edit_after_configure_trips_drift_blocker_in_next(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _epic_ladder_to_plan(state, wdd, root)
            assert _cli(state, "config", "set", "--epic", "merge.surface", "pr")[0] == 0
            code, out = _cli(state, "next", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["actions"], [])
            self.assertEqual(result["blockers"][0]["code"], "epic_config_drift")

    def test_overlay_edit_after_configure_refuses_governed_verb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _epic_ladder_to_plan(state, wdd, root)
            assert _cli(state, "config", "set", "--epic", "merge.surface", "pr")[0] == 0
            code, _out, err = _cli_full(state, "start", "--task", "T1", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("epic config drift", err)
            self.assertIn("intake configure", err)

    def test_reapproving_configure_clears_the_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _epic_ladder_to_plan(state, wdd, root)
            assert _cli(state, "config", "set", "--epic", "merge.surface", "pr")[0] == 0
            code, _out, err = _cli_full(state, "start", "--task", "T1", "--repo", str(root))
            self.assertNotEqual(code, 0)
            assert _cli(state, "intake", "configure", "--approved-by", "t")[0] == 0
            # Re-recording configure clears scope.approval too (spec Sec2) --
            # a plan re-stamp is required before any governed verb resumes.
            code, _out, err = _cli_full(state, "start", "--task", "T1", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("plan drift", err)
            assert _cli(
                state, "plan", "apply", "--plan", str(root / "plan.json"), "--repo", str(root),
                "--approved-by", "t",
            )[0] == 0
            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)

    def test_unrelated_epic_overlay_key_edit_does_not_trip_review_evidence(self) -> None:
        # Task 5, spec Sec2: "an edit to an unrelated key (models.planning)
        # stales nothing" -- recorded task review evidence must survive an
        # overlay edit to a projection-disjoint key once the drift itself is
        # re-approved (models.planning is outside taskReview's projection).
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _epic_ladder_to_plan(state, wdd, root, review_policy="always")
            _start_and_commit(state, root)
            assert _cli(state, "submit", "--task", "T1", "--repo", str(root))[0] == 0
            assert _cli(
                state, "review", "record", "--task", "T1", "--reviewer", "t", "--findings", "[]"
            )[0] == 0
            recorded_review = StateStore(Path(state)).read()["tasks"]["T1"]["review"]
            self.assertIn("configSha256", recorded_review)

            assert _cli(state, "config", "set", "--epic", "models.planning", '"gpt-x"')[0] == 0
            assert _cli(state, "intake", "configure", "--approved-by", "t")[0] == 0
            assert _cli(
                state, "plan", "apply", "--plan", str(root / "plan.json"), "--repo", str(root),
                "--approved-by", "t",
            )[0] == 0

            assert _cli(state, "verify", "record", "--task", "T1", "--status", "passed")[0] == 0
            assert _cli(state, "freshness", "record", "--task", "T1", "--repo", str(root))[0] == 0
            code, out = _cli(state, "merge", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)


class ChokepointPrecedenceTest(unittest.TestCase):
    """Test contract: precedence order pinned -- governance -> epic config
    -> intake artifacts -> plan composite; one blocker at a time."""

    def test_governance_drift_outranks_epic_config_drift_in_next(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _epic_ladder_to_plan(state, wdd, root)
            # A GLOBAL config edit changes the effective view intake.configure
            # signed too (by construction, spec Sec2), so it trips governance
            # drift AND epic config drift simultaneously.
            assert _cli(state, "config", "set", "verification.commands", '["false"]')[0] == 0
            code, out = _cli(state, "next", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)
            self.assertEqual(result["blockers"][0]["code"], "governance_drift")

    def test_governance_drift_outranks_epic_config_drift_at_chokepoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _epic_ladder_to_plan(state, wdd, root)
            assert _cli(state, "config", "set", "verification.commands", '["false"]')[0] == 0
            code, _out, err = _cli_full(state, "start", "--task", "T1", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("governance drift", err)

    def test_full_remedy_chain_in_precedence_order(self) -> None:
        """Amend fixes governance; the epic-config gate then surfaces next
        (still stale, since the effective view it signed changed too); a
        reconfigure fixes that but clears scope.approval, so plan drift
        surfaces last; a plan re-stamp finally clears everything."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _epic_ladder_to_plan(state, wdd, root)
            assert _cli(state, "config", "set", "verification.commands", '["false"]')[0] == 0

            code, _out, err = _cli_full(state, "start", "--task", "T1", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("governance drift", err)

            assert _cli(state, "constitution", "amend", "--by", "t")[0] == 0
            code, _out, err = _cli_full(state, "start", "--task", "T1", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("epic config drift", err)

            assert _cli(state, "intake", "configure", "--approved-by", "t")[0] == 0
            code, _out, err = _cli_full(state, "start", "--task", "T1", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("plan drift", err)

            assert _cli(
                state, "plan", "apply", "--plan", str(root / "plan.json"), "--repo", str(root),
                "--approved-by", "t",
            )[0] == 0
            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)


class SolScenarioRegressionTest(unittest.TestCase):
    """The three round-1 P1 scenarios from Sol's review, each pinned as a
    named regression test (plan Task 5's Files/Test contract)."""

    def test_scenario_a_global_config_change_needs_full_remedy_chain_to_resume(self) -> None:
        """(a) global config change + amend + unchanged overlay cannot resume
        execution without a plan re-stamp (amend clears scope.approval)."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _epic_ladder_to_plan(state, wdd, root)
            self.assertIn("approval", StateStore(Path(state)).read()["scope"])

            assert _cli(state, "config", "set", "verification.commands", '["false"]')[0] == 0
            assert _cli(state, "constitution", "amend", "--by", "t")[0] == 0
            # amend alone cleared scope.approval; the overlay is unchanged,
            # but the effective view intake.configure signed still moved
            # (the changed global config feeds it) -- neither a governed verb
            # nor a bare re-approval of the SAME plan bytes may resume
            # execution without walking BOTH remedies.
            self.assertNotIn("approval", StateStore(Path(state)).read()["scope"])
            code, _out, err = _cli_full(state, "start", "--task", "T1", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("epic config drift", err)

            assert _cli(state, "intake", "configure", "--approved-by", "t")[0] == 0
            code, _out, err = _cli_full(state, "start", "--task", "T1", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("plan drift", err)

            code, out = _cli(
                state, "plan", "apply", "--plan", str(root / "plan.json"), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, out)
            code, out = _cli(state, "start", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)

    def test_scenario_b_started_normal_risk_task_new_high_rule_merge_needs_review(self) -> None:
        """(b) started normal-risk task + new high riskRule + configure/plan
        re-approval -> merge refuses without review."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            _epic_ladder_to_plan(
                state, wdd, root, task_domains=["src/auth/**"], review_policy="risk_based"
            )
            self.assertEqual(StateStore(Path(state)).read()["tasks"]["T1"]["risk"], "normal")
            _start_and_commit(state, root)
            assert _cli(state, "submit", "--task", "T1", "--repo", str(root))[0] == 0
            # risk_based + normal risk: no review required yet.
            assert _cli(state, "verify", "record", "--task", "T1", "--status", "passed")[0] == 0
            assert _cli(state, "freshness", "record", "--task", "T1", "--repo", str(root))[0] == 0
            before = StateStore(Path(state)).read()["tasks"]["T1"]
            self.assertEqual(before["status"], "merge_ready")
            self.assertIsNone(before["review"])

            # riskRules is an epic-overlay-allowed leaf (config.py's
            # OVERLAY_ALLOWED_LEAVES); overriding it per-epic (rather than
            # globally) keeps this scenario about the configure/plan re-
            # approval chain alone, with no governance drift entangled.
            assert _cli(
                state, "config", "set", "--epic", "riskRules",
                '[{"pattern": "src/auth/**", "risk": "high"}]',
            )[0] == 0
            assert _cli(state, "intake", "configure", "--approved-by", "t")[0] == 0
            code, out = _cli(
                state, "plan", "apply", "--plan", str(root / "plan.json"), "--repo", str(root),
                "--approved-by", "t",
            )
            self.assertEqual(code, 0, out)
            after = StateStore(Path(state)).read()["tasks"]["T1"]
            self.assertEqual(after["risk"], "high")

            code, _out, err = _cli_full(state, "merge", "--task", "T1", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("needs_review", err)

            assert _cli(
                state, "review", "record", "--task", "T1", "--reviewer", "t", "--findings", "[]"
            )[0] == 0
            code, out = _cli(state, "merge", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)

    def test_scenario_c_stale_final_verification_config_projection_refuses_handoff(self) -> None:
        """(c) verification evidence recorded under old verification.commands
        + config change -> handoff refuses, evidence stale."""
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            bare = Path(tmp) / "origin.git"
            subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
            subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=root, check=True)
            _epic_ladder_to_plan(state, wdd, root, review_policy="always")
            _start_and_commit(state, root)
            assert _cli(state, "submit", "--task", "T1", "--repo", str(root))[0] == 0
            assert _cli(
                state, "review", "record", "--task", "T1", "--reviewer", "t", "--findings", "[]"
            )[0] == 0
            assert _cli(state, "verify", "record", "--task", "T1", "--status", "passed")[0] == 0
            assert _cli(state, "freshness", "record", "--task", "T1", "--repo", str(root))[0] == 0
            assert _cli(state, "merge", "--task", "T1", "--repo", str(root))[0] == 0

            assert _cli(
                state, "finalize", "review", "record", "--reviewer", "t", "--findings", "[]",
                "--repo", str(root),
            )[0] == 0
            results = json.dumps([{"command": "true", "status": "passed"}, {"command": "true", "status": "passed"}])
            assert _cli(
                state, "finalize", "verify", "record", "--results", results, "--repo", str(root)
            )[0] == 0
            final_verification = StateStore(Path(state)).read()["finalize"]["verification"]
            self.assertIn("configSha256", final_verification)

            # Handoff succeeds before any config change -- proves the later
            # refusal is genuinely about staleness, not some other precondition.
            code, out = _cli(state, "finalize", "handoff", "--repo", str(root))
            self.assertEqual(code, 0, out)

            # A verification.commands change also trips governance drift
            # (config.json is covered by governance_fingerprint wholesale);
            # amend + a bare plan re-stamp clear THAT without re-proving the
            # verification work itself -- isolating the config-projection
            # staleness this scenario is actually about.
            assert _cli(state, "config", "set", "verification.commands", '["true", "false"]')[0] == 0
            assert _cli(state, "constitution", "amend", "--by", "t")[0] == 0
            assert _cli(state, "intake", "configure", "--approved-by", "t")[0] == 0
            assert _cli(
                state, "plan", "apply", "--plan", str(root / "plan.json"), "--repo", str(root),
                "--approved-by", "t",
            )[0] == 0

            code, _out, err = _cli_full(state, "finalize", "handoff", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn("final verification evidence is stale", err)
            self.assertIn("finalize verify record", err)


_FAKE_RUNNER = str(Path(__file__).resolve().parent / "fixtures" / "fake-runner" / "fake-runner")


def _fake_runner_command() -> list[str]:
    """The fake-runner fixture idiom (test_handover.py's `_runner_command`),
    duplicated locally per this file's no-cross-file-imports convention."""
    return [_FAKE_RUNNER, "--prompt", "{prompt}", "--worktree", "{worktree}", "--logfile", "{logfile}"]


class EpicOverrideReachesMergeAndDispatchTest(unittest.TestCase):
    """F2 regression: an active epic's overlay overrides used to be inert at
    the surfaces that actually act on them -- `merge_settings` (submit's and
    merge's PR-vs-local branching) and dispatch's worker model resolution
    were all fed a bare, un-overlaid `load_config` read instead of the
    admission snapshot's `effective` view, silently dropping any epic
    override at the one point each surface actually consults it."""

    def test_epic_overlay_local_surface_overrides_a_global_pr_config_at_submit_and_merge(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo_pr_surface(tmp)
            wdd = root / ".wdd"
            _epic_ladder_to_plan(state, wdd, root, overlay={"merge.surface": '"local"'})
            _start_and_commit(state, root)

            # If the epic override were inert, submit would see the GLOBAL
            # "pr" surface and attempt a real `git push origin` against a
            # repo with no configured remote -- an unconditional failure
            # (push is not wrapped in try/except: cli.py's submit handler
            # pushes before any PR attempt, on purpose) -- instead of the
            # clean, PR-machinery-free local-surface submit this overlay
            # asks for.
            code, out, err = _cli_full(state, "submit", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, err)
            payload = json.loads(out)
            self.assertNotIn("warning", payload)

            assert _cli(
                state, "review", "record", "--task", "T1", "--reviewer", "t", "--findings", "[]"
            )[0] == 0
            assert _cli(state, "verify", "record", "--task", "T1", "--status", "passed")[0] == 0
            assert _cli(state, "freshness", "record", "--task", "T1", "--repo", str(root))[0] == 0

            # Same signal at merge: a wrongly-resolved "pr" surface pushes
            # the advanced base to origin post-merge, which -- with no
            # remote configured -- degrades into a "warning" key on the
            # result (cli.py's merge handler) instead of the warning-free
            # result the correctly-resolved "local" surface produces.
            code, out = _cli(state, "merge", "--task", "T1", "--repo", str(root))
            self.assertEqual(code, 0, out)
            merge_payload = json.loads(out)
            self.assertNotIn("warning", merge_payload)

    def test_epic_overlay_model_override_reaches_worker_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo_pr_surface(tmp)
            wdd = root / ".wdd"
            command = _fake_runner_command()
            runners_payload = json.dumps({"epic-model": {"command": command}})
            assert _cli(state, "config", "set", "runners", runners_payload)[0] == 0
            # Registering a runner edits config.json; re-sign so dispatch
            # below is not blocked by governance drift.
            assert _cli(state, "constitution", "amend", "--by", "t2")[0] == 0
            assert _cli(state, "dispatch", "--probe", "epic-model", "--repo", str(root))[0] == 0

            _epic_ladder_to_plan(
                state, wdd, root,
                overlay={
                    "models.implementation": '{"default": "epic-model", "highRisk": "epic-model"}'
                },
            )
            assert _cli(state, "start", "--task", "T1", "--repo", str(root))[0] == 0

            # The GLOBAL models.implementation is still {default: null,
            # highRisk: null} (set by _ratified_repo_pr_surface) -- if the
            # epic override were inert here, dispatch would resolve
            # model=None and refuse "not a configured runner" instead of
            # actually exec'ing the fake runner under the overridden model.
            code, out, err = _cli_full(
                state, "dispatch", "--task", "T1", "--role", "worker", "--repo", str(root)
            )
            self.assertEqual(code, 0, err)
            result = json.loads(out)
            self.assertEqual(result["model"], "epic-model")
            self.assertEqual(result["exitCode"], 0)
            self.assertEqual(result["statusToken"], "DONE")


class NoBareLoadConfigInGovernedMergeSettingsSitesTest(unittest.TestCase):
    """F2 audit: every `merge_settings`-feeding site in cli.py, plus
    dispatch --task's model resolution, must read the admission snapshot's
    `effective` view via `_governed_config`, never a second bare
    `load_config` (spec Sec2 resolve-once, extended by this fix-round).
    A textual check, not a functional one -- the functional regressions
    above are what actually prove the behavior; this pins the count so a
    future edit cannot silently reintroduce a bare read at one of these six
    sites without also updating this test."""

    def test_six_governed_call_sites_use_the_layered_snapshot_helper(self) -> None:
        import wave_delivery.cli as cli_module

        source = Path(cli_module.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count("config = _governed_config(admission_layers)"), 6)


# ---------------------------------------------------------------------------
# Task 6: transactional archive -- the four-step move, the deterministic
# record, and the exhaustive crash-recovery matrix (spec Sec1).
# ---------------------------------------------------------------------------


def _epic_ladder_to_delivered(
    state: str, wdd: Path, root: Path, *, slug: str = "demo", approver: str = "t"
) -> None:
    """`_epic_ladder_to_plan` walked the rest of the way to `delivered` --
    Task 6's shared fixture for archive-transaction tests. Local-surface
    task execution (start/commit/submit/verify/freshness/merge) plus the
    finalize ladder (review/verify/handoff/delivered) via a real git merge,
    mirroring test_intake.py's `_run_to_delivered` (no cross-file imports,
    per this file's convention). Default reviewPolicy (risk_based) + default
    risk (normal) skips task-level review, same as test_intake.py's
    FullLifecycleE2ETest.
    """
    _epic_ladder_to_plan(state, wdd, root, slug=slug, approver=approver)
    _start_and_commit(state, root)
    assert _cli(state, "submit", "--task", "T1", "--repo", str(root))[0] == 0
    assert _cli(state, "verify", "record", "--task", "T1", "--status", "passed")[0] == 0
    assert _cli(state, "freshness", "record", "--task", "T1", "--repo", str(root))[0] == 0
    assert _cli(state, "merge", "--task", "T1", "--repo", str(root))[0] == 0

    assert _cli(
        state, "finalize", "review", "record", "--reviewer", approver, "--findings", "[]",
        "--repo", str(root),
    )[0] == 0
    results = json.dumps(
        [{"command": "true", "status": "passed"}, {"command": "true", "status": "passed"}]
    )
    code, out = _cli(
        state, "finalize", "verify", "record", "--results", results, "--repo", str(root)
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
    code, out = _cli(state, "finalize", "delivered", "--by", approver, "--repo", str(root))
    assert code == 0, out


def _delivered_repo(tmp: str, *, slug: str = "demo") -> tuple[Path, str, Path]:
    """A delivered, epic-scoped scope ready for `scope archive` -- the
    shared starting point for every crash-recovery-matrix test below."""
    root, state = _ratified_repo(tmp)
    wdd = root / ".wdd"
    bare = Path(tmp) / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=root, check=True)
    _epic_ladder_to_delivered(state, wdd, root, slug=slug)
    return root, state, wdd


class GenerateArchiveRecordDeterminismTest(unittest.TestCase):
    """`generate_archive_record`: a pure function of `state` plus the one
    nondeterministic input (`archivedAt`) -- spec Sec1's "deterministic
    function... nothing about the record is unreconstructable"."""

    def test_same_inputs_yield_byte_identical_output(self) -> None:
        state = new_state("SCOPE-demo", base_ref="wdd/demo")
        state["tasks"] = {"T1": task_state("T1")}
        first = generate_archive_record(state, "2026-08-01T00:00:00Z")
        second = generate_archive_record(state, "2026-08-01T00:00:00Z")
        self.assertEqual(first, second)
        self.assertEqual(_canonical_record_bytes(first), _canonical_record_bytes(second))

    def test_archived_at_is_the_only_varying_input(self) -> None:
        state = new_state("SCOPE-demo", base_ref="wdd/demo")
        a = generate_archive_record(state, "2026-08-01T00:00:00Z")
        b = generate_archive_record(state, "2026-08-02T00:00:00Z")
        self.assertNotEqual(a["archivedAt"], b["archivedAt"])
        a_rest = {k: v for k, v in a.items() if k != "archivedAt"}
        b_rest = {k: v for k, v in b.items() if k != "archivedAt"}
        self.assertEqual(a_rest, b_rest)

    def test_excludes_the_archive_pending_journal_event_from_event_count(self) -> None:
        """The exclusion rule that makes recovery's regeneration byte-
        identical to what step 1 originally wrote: a state that has NOT yet
        journaled `archivePending` must produce the identical record to the
        SAME state plus `archivePending` set and one more trailing event
        (the journal event itself, always the last one appended)."""
        base_events = [
            {
                "revision": 1, "type": "scope.delivered", "task": None,
                "idempotencyKey": "k1", "at": "2026-08-01T00:00:00Z",
            }
        ]
        pre = new_state("SCOPE-demo", base_ref="wdd/demo")
        pre["events"] = list(base_events)
        pre_record = generate_archive_record(pre, "2026-08-03T00:00:00Z")
        self.assertEqual(pre_record["eventCount"], 1)

        post = new_state("SCOPE-demo", base_ref="wdd/demo")
        post["events"] = list(base_events) + [
            {
                "revision": 2, "type": "scope.archive_pending", "task": None,
                "idempotencyKey": "k2", "at": "2026-08-03T00:00:00Z",
            }
        ]
        post["archivePending"] = {
            "slug": "demo", "sourceRevision": 1,
            "archivedAt": "2026-08-03T00:00:00Z", "recordSha256": "sha256:x",
        }
        post_record = generate_archive_record(post, "2026-08-03T00:00:00Z")
        self.assertEqual(pre_record, post_record)


class ArchiveScopeTransactionalMoveTest(unittest.TestCase):
    """archive_scope's four steps, uncrashed, end to end: the whole
    `epics/<slug>/` directory moves to `archive/<slug>/`, and state resets
    total-no-leak (scope/epic/archivePending/archiveBlocked all null)."""

    def test_archive_moves_epic_dir_and_resets_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _delivered_repo(tmp)

            code, out = _cli(state, "scope", "archive", "--repo", str(root))
            self.assertEqual(code, 0, out)
            result = json.loads(out)

            self.assertFalse((wdd / "epics" / "demo").exists())
            archive_dir = wdd / "archive" / "demo"
            self.assertTrue(archive_dir.is_dir())
            record_path = archive_dir / "record.json"
            self.assertEqual(Path(result["archived"]), record_path)
            self.assertTrue((archive_dir / "spec.md").exists())
            self.assertTrue((archive_dir / "design.md").exists())
            self.assertTrue((archive_dir / "config.json").exists())

            payload = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertIn("T1", payload["tasks"])
            self.assertEqual(payload["finalize"]["verification"]["status"], "passed")

            after = StateStore(Path(state)).read()
            self.assertIsNone(after["scope"])
            self.assertIsNone(after["epic"])
            self.assertIsNone(after["archivePending"])
            self.assertIsNone(after["archiveBlocked"])
            self.assertEqual(after["tasks"], {})
            self.assertNotIn("finalize", after)
            self.assertEqual(after["intake"], {})
            # Governance and the audit trail survive.
            self.assertEqual(after["constitution"]["status"], "ratified")
            self.assertGreater(len(after["events"]), 0)


class ArchiveRecoveryRowOneTest(unittest.TestCase):
    """Recovery row 1: journal set, source (`epics/<slug>/`) present,
    destination absent -- crash between step 2 (journal) and step 3
    (rename). A fresh command (`load_recovered`, what `next`/`status`/
    `doctor` all use) verifies/regenerates the record and completes the
    transaction."""

    def _crash_after_journal(self, store: StateStore, root: Path) -> dict:
        with mock.patch("wave_delivery.finalize.os.rename", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                archive_scope(store, repo=root)
        return store.read()

    def test_completes_via_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _delivered_repo(tmp)
            store = StateStore(Path(state))
            crashed = self._crash_after_journal(store, root)

            self.assertIsNotNone(crashed["archivePending"])
            self.assertTrue((wdd / "epics" / "demo").exists())
            self.assertFalse((wdd / "archive" / "demo").exists())
            expected_sha = crashed["archivePending"]["recordSha256"]
            record_bytes = (wdd / "epics" / "demo" / "record.json").read_bytes()
            self.assertEqual(_record_sha256(record_bytes), expected_sha)

            recovered = store.load_recovered()
            self.assertIsNone(recovered["epic"])
            self.assertIsNone(recovered["archivePending"])
            self.assertFalse((wdd / "epics" / "demo").exists())
            record_path = wdd / "archive" / "demo" / "record.json"
            self.assertTrue(record_path.exists())
            self.assertEqual(_record_sha256(record_path.read_bytes()), expected_sha)

    def test_missing_record_is_regenerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _delivered_repo(tmp)
            store = StateStore(Path(state))
            crashed = self._crash_after_journal(store, root)
            expected_sha = crashed["archivePending"]["recordSha256"]
            (wdd / "epics" / "demo" / "record.json").unlink()

            recovered = store.load_recovered()
            record_path = wdd / "archive" / "demo" / "record.json"
            self.assertEqual(_record_sha256(record_path.read_bytes()), expected_sha)
            self.assertIsNone(recovered["archivePending"])

    def test_corrupted_record_is_regenerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _delivered_repo(tmp)
            store = StateStore(Path(state))
            crashed = self._crash_after_journal(store, root)
            expected_sha = crashed["archivePending"]["recordSha256"]
            (wdd / "epics" / "demo" / "record.json").write_text(
                '{"tampered": true}\n', encoding="utf-8"
            )

            recovered = store.load_recovered()
            record_path = wdd / "archive" / "demo" / "record.json"
            self.assertEqual(_record_sha256(record_path.read_bytes()), expected_sha)
            self.assertIsNone(recovered["archivePending"])


class ArchiveRecoveryRowTwoTest(unittest.TestCase):
    """Recovery row 2: journal set, destination present, source absent --
    crash between step 3 (rename) and step 4 (reset). The reset has not
    happened, so the (pre-reset) state is still authoritative for
    verifying/regenerating the ARCHIVED record.json."""

    def _crash_after_rename(self, store: StateStore, root: Path) -> dict:
        """Let step 2's journal write through, then fail step 4's reset
        write -- the rename (step 3) lands in between, unpatched, so it
        genuinely succeeds. Row 2's shape needs exactly this: journaled,
        destination present, source gone, reset never happened."""
        original_write = StateStore.write
        calls = {"n": 0}

        def flaky_write(self_store, s):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("crash")
            return original_write(self_store, s)

        with mock.patch.object(StateStore, "write", flaky_write):
            with self.assertRaises(OSError):
                archive_scope(store, repo=root)
        return store.read()

    def test_completes_via_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _delivered_repo(tmp)
            store = StateStore(Path(state))
            crashed = self._crash_after_rename(store, root)

            self.assertFalse((wdd / "epics" / "demo").exists())
            self.assertTrue((wdd / "archive" / "demo").is_dir())
            self.assertIsNotNone(crashed["archivePending"])
            expected_sha = crashed["archivePending"]["recordSha256"]

            recovered = store.load_recovered()
            self.assertIsNone(recovered["epic"])
            self.assertIsNone(recovered["archivePending"])
            record_path = wdd / "archive" / "demo" / "record.json"
            self.assertEqual(_record_sha256(record_path.read_bytes()), expected_sha)

    def test_corrupted_archived_record_is_regenerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _delivered_repo(tmp)
            store = StateStore(Path(state))
            crashed = self._crash_after_rename(store, root)
            expected_sha = crashed["archivePending"]["recordSha256"]
            (wdd / "archive" / "demo" / "record.json").write_text(
                '{"tampered": true}\n', encoding="utf-8"
            )

            store.load_recovered()
            record_path = wdd / "archive" / "demo" / "record.json"
            self.assertEqual(_record_sha256(record_path.read_bytes()), expected_sha)


class ArchiveRecoveryHardErrorTest(unittest.TestCase):
    """Recovery row 4: journal set, neither path present -- pathological
    total loss. Nothing is guessed; recovery fails loudly, naming the slug
    and the on-disk facts."""

    def test_journal_set_neither_path_present_is_a_hard_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _delivered_repo(tmp)
            store = StateStore(Path(state))
            with mock.patch("wave_delivery.finalize.os.rename", side_effect=OSError("crash")):
                with self.assertRaises(OSError):
                    archive_scope(store, repo=root)
            shutil.rmtree(wdd / "epics" / "demo")

            with self.assertRaises(ValidationError) as ctx:
                store.load_recovered()
            self.assertIn("demo", str(ctx.exception))


class ArchiveCollisionBlockedResolutionE2ETest(unittest.TestCase):
    """Recovery row 3 (external collision -> durable `archiveBlocked`) and
    row 5 (the legal, idempotent resting state), end to end: `next` surfaces
    the blocker, `scope archive` refuses while unresolved, and re-running it
    once the collision is gone clears the block and archives fresh."""

    def test_collision_blocks_next_surfaces_it_then_resolve_and_re_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, wdd = _delivered_repo(tmp)
            store = StateStore(Path(state))

            with mock.patch("wave_delivery.finalize.os.rename", side_effect=OSError("crash")):
                with self.assertRaises(OSError):
                    archive_scope(store, repo=root)

            # External collision: archive/demo/ appears while the journal is
            # still pending and the rename never happened.
            colliding = wdd / "archive" / "demo"
            colliding.mkdir(parents=True)
            (colliding / "unrelated.txt").write_text("not ours\n", encoding="utf-8")

            recovered = store.load_recovered()
            self.assertIsNone(recovered["archivePending"])
            self.assertIsNotNone(recovered["archiveBlocked"])
            self.assertEqual(recovered["archiveBlocked"]["slug"], "demo")
            self.assertEqual(recovered["archiveBlocked"]["collidingPath"], str(colliding))
            # The generated record is removed; the epic's real content and
            # the external collision's own content are both untouched --
            # recovery never resets on an unresolved collision.
            self.assertFalse((wdd / "epics" / "demo" / "record.json").exists())
            self.assertTrue((wdd / "epics" / "demo" / "spec.md").exists())
            self.assertTrue((colliding / "unrelated.txt").exists())
            self.assertEqual(recovered["epic"], "demo")
            self.assertIsNotNone(recovered["scope"])

            # Row 5: the resting state is idempotent.
            again = store.load_recovered()
            self.assertEqual(again["archiveBlocked"], recovered["archiveBlocked"])

            # `next` surfaces the durable blocker and empties actions.
            code, out = _cli(state, "next")
            self.assertEqual(code, 0, out)
            next_result = json.loads(out)
            self.assertEqual(next_result["actions"], [])
            self.assertEqual(next_result["blockers"][0]["code"], "archive_blocked")
            self.assertEqual(next_result["blockers"][0]["collidingPath"], str(colliding))

            # Re-running `scope archive` while unresolved refuses, naming it.
            code, _out, err = _cli_full(state, "scope", "archive", "--repo", str(root))
            self.assertNotEqual(code, 0)
            self.assertIn(str(colliding), err)

            # Resolve the collision, then re-run: clears archiveBlocked and
            # starts a fresh transaction from the still-intact epic dir.
            shutil.rmtree(colliding)
            code, out = _cli(state, "scope", "archive", "--repo", str(root))
            self.assertEqual(code, 0, out)
            self.assertFalse((wdd / "epics" / "demo").exists())
            self.assertTrue((wdd / "archive" / "demo" / "record.json").exists())
            final = StateStore(Path(state)).read()
            self.assertIsNone(final["archiveBlocked"])
            self.assertIsNone(final["archivePending"])
            self.assertIsNone(final["epic"])


class ArchiveRecoveryStrayRecordCleanupTest(unittest.TestCase):
    """Recovery row 6: a step-1 crash (record.json written into the active
    epic's own directory before the journal was ever set) is cleaned up
    transparently, scoped to `state.epic` only. Triggered here via an
    ORDINARY governed mutation (`intake configure`, not `scope archive`
    itself) specifically to prove `apply_mutation`'s own `recover_locked()`
    call cannot self-deadlock: the state lock is not reentrant, so a nested
    acquisition would hang (or time out into `LockUnavailable`) rather than
    let this command complete.
    """

    def test_stray_record_removed_and_governed_mutation_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            assert _cli(state, "epic", "new", "--slug", "demo")[0] == 0
            stray = wdd / "epics" / "demo" / "record.json"
            stray.write_text('{"kind": "stray"}\n', encoding="utf-8")

            code, out = _cli(state, "intake", "configure", "--use-defaults", "--by", "t")
            self.assertEqual(code, 0, out)
            self.assertFalse(stray.exists())
            self.assertTrue((wdd / "epics" / "demo" / "config.json").exists())
            state_after = StateStore(Path(state)).read()
            self.assertEqual(state_after["epic"], "demo")

    def test_unrelated_epic_content_is_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            assert _cli(state, "epic", "new", "--slug", "demo")[0] == 0
            (wdd / "epics" / "demo" / "spec.md").write_text("keep me\n", encoding="utf-8")
            (wdd / "epics" / "demo" / "record.json").write_text("{}\n", encoding="utf-8")

            store = StateStore(Path(state))
            recovered = store.load_recovered()
            self.assertFalse((wdd / "epics" / "demo" / "record.json").exists())
            self.assertEqual(
                (wdd / "epics" / "demo" / "spec.md").read_text(encoding="utf-8"), "keep me\n"
            )
            self.assertEqual(recovered["epic"], "demo")


class RecoveryNeverReadsArchiveTest(unittest.TestCase):
    """spec Sec1: recovery never scans, reads, or otherwise touches anything
    under `archive/` beyond the exact path named by `archivePending.slug` /
    `archiveBlocked.slug`. Proven by permission-denying `archive/` (and,
    more precisely, an unrelated already-archived epic's own directory)
    entirely and running recovery paths that have no legitimate reason to
    look inside it."""

    def setUp(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root bypasses directory permission checks")

    def test_clean_state_recovery_does_not_need_archive_dir_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            assert _cli(state, "epic", "new", "--slug", "demo")[0] == 0
            archive_dir = wdd / "archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_dir.chmod(0o000)
            try:
                store = StateStore(Path(state))
                recovered = store.load_recovered()
                self.assertEqual(recovered["epic"], "demo")
            finally:
                archive_dir.chmod(0o755)

    def test_other_archived_epic_directory_is_never_touched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _ratified_repo(tmp)
            wdd = root / ".wdd"
            assert _cli(state, "epic", "new", "--slug", "demo")[0] == 0
            other = wdd / "archive" / "other-epic"
            other.mkdir(parents=True)
            (other / "record.json").write_text("{}\n", encoding="utf-8")
            other.chmod(0o000)
            try:
                (wdd / "epics" / "demo" / "record.json").write_text("{}\n", encoding="utf-8")
                store = StateStore(Path(state))
                recovered = store.load_recovered()
                self.assertFalse((wdd / "epics" / "demo" / "record.json").exists())
                self.assertEqual(recovered["epic"], "demo")
            finally:
                other.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
