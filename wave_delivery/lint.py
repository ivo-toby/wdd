"""Deterministic plan-quality checks.

Every check here exists because an agent-authored plan exhibited the failure
in the wild: a fully serialized dependency chain, every task marked high
risk, and per-file conflict-domain lists so exhaustive they were pure
ceremony. Lint warns; it never blocks unless the caller passes --strict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import admission_schedule
from .plan import state_from_plan


def _check_serialization(plan_dict: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = plan_dict["tasks"]
    if len(tasks) < 3:
        return []
    rounds = admission_schedule(state_from_plan(plan_dict))
    fully_serial = all(len(round_["tasks"]) == 1 for round_ in rounds)
    near_serial = len(tasks) >= 4 and len(rounds) > 0.75 * len(tasks)
    if not (fully_serial or near_serial):
        return []
    return [
        {
            "code": "serialized_plan",
            "severity": "warning",
            "message": (
                f"{len(tasks)} tasks admit in {len(rounds)} rounds — the plan is "
                "effectively serialized. Check dependsOn for vague sequencing and "
                "conflictDomains for accidental overlap; maxConcurrent buys nothing here."
            ),
        }
    ]


def _check_risk_distribution(plan_dict: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = plan_dict["tasks"]
    if len(tasks) < 4:
        return []
    risks = {entry["risk"] for entry in tasks}
    if len(risks) != 1:
        return []
    direction = (
        "risk_based review degenerates to review-everything"
        if risks == {"high"}
        else "risk_based review will review nothing — confirm no task touches a high-risk area"
    )
    return [
        {
            "code": "uniform_risk",
            "severity": "warning",
            "message": f"every task is risk={next(iter(risks))!r}: {direction}.",
        }
    ]


def lint_plan(
    plan_dict: dict[str, Any], wdd_dir: Path | str | None = None
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    findings.extend(_check_serialization(plan_dict))
    findings.extend(_check_risk_distribution(plan_dict))
    return findings
