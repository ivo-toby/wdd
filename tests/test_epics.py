"""Epic-scoped-state plan (2026-08-03), Task 1: the typed path resolver.

Local helpers only (no cross-file imports between test modules, per the
phase-6a/6b test conventions carried forward -- see Global Constraints in
docs/superpowers/plans/2026-08-03-epic-scoped-state.md). Later tasks in the
same plan add classes to this same file.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wave_delivery.errors import ValidationError
from wave_delivery.intake import resolve_within_wdd
from wave_delivery.paths import resolve_artifact


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


if __name__ == "__main__":
    unittest.main()
