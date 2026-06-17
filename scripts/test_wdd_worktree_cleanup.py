#!/usr/bin/env python3
"""Regression checks for WDD worker worktree cleanup guidance."""

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def read_tracked(path: str) -> str:
    working_path = ROOT / path
    if working_path.exists():
        return working_path.read_text(encoding="utf-8")
    return subprocess.check_output(
        ["git", "show", f"HEAD:{path}"],
        cwd=ROOT,
        text=True,
    )


def assert_contains(path: str, *needles: str) -> None:
    content = read_tracked(path)
    missing = [needle for needle in needles if needle not in content]
    if missing:
        formatted = "\n".join(f"  - {needle}" for needle in missing)
        raise AssertionError(f"{path} is missing required cleanup guidance:\n{formatted}")


def test_epic_wave_cleanup_guidance() -> None:
    assert_contains(
        "skills/subagent-pr-orchestration/SKILL.md",
        "git worktree remove",
        "Do not remove a worktree that has uncommitted changes",
        "cleaned_up",
    )
    assert_contains(
        "skills/wdd-reconcile-wave/SKILL.md",
        "Remove each completed task or bundle worktree",
        "git worktree remove",
        "worktreeStatus",
    )


def test_micro_wave_cleanup_guidance() -> None:
    assert_contains(
        "skills/wdd-run-work/SKILL.md",
        "worktree cleanup state",
    )
    assert_contains(
        "skills/wdd-finish-work/SKILL.md",
        "Remove completed micro-wave task or bundle worktrees",
        "git worktree remove",
        "Do not remove a worktree that has uncommitted changes",
    )


def test_artifact_schema_records_cleanup_state() -> None:
    assert_contains(
        "docs/artifact-schema.md",
        '"cleanup": null',
        '"worktreeStatus": "cleaned_up"',
        "Worktree cleanup is part of wave and micro-wave closure",
    )


def test_templates_seed_cleanup_state() -> None:
    for path in (
        "skills/wdd-plan-epic/templates/orchestration.json",
        "skills/wdd-init-project/templates/orchestration.json",
        "skills/wdd-plan-work/templates/state.json",
    ):
        assert_contains(
            path,
            '"worktreeStatus": "unassigned"',
            '"cleanup": null',
        )


if __name__ == "__main__":
    tests = [
        test_epic_wave_cleanup_guidance,
        test_micro_wave_cleanup_guidance,
        test_artifact_schema_records_cleanup_state,
        test_templates_seed_cleanup_state,
    ]
    for test in tests:
        test()
    print(f"{len(tests)} WDD worktree cleanup checks passed")
