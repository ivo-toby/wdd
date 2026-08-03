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
from wave_delivery.errors import ValidationError
from wave_delivery.intake import resolve_within_wdd
from wave_delivery.paths import resolve_artifact
from wave_delivery.schema import new_setup_state
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


if __name__ == "__main__":
    unittest.main()
