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
import subprocess
import tempfile
import unittest
from pathlib import Path

from wave_delivery.cli import main
from wave_delivery.config import (
    OVERLAY_ALLOWED_LEAVES,
    default_config,
    derive_effective,
    effective_config_digest,
    get_value,
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
from wave_delivery.errors import IllegalTransition, ValidationError
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
            self.assertEqual(
                migrated["finalize"]["verification"]["configSha256"],
                effective_config_digest(project(layers["effective"], "finalVerification")),
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

    def test_next_emits_agree_spec_immediately_after_epic_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = _epic_repo(tmp)
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


if __name__ == "__main__":
    unittest.main()
