"""Deterministic plan-quality checks.

Every check here exists because an agent-authored plan exhibited the failure
in the wild: a fully serialized dependency chain, every task marked high
risk, and per-file conflict-domain lists so exhaustive they were pure
ceremony. Lint warns; it never blocks unless the caller passes --strict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .domains import WILDCARDS, domains_overlap
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
                "effectively serialized. Check dependsOn fan-out, conflictDomains "
                "overlap, and whether scope.maxConcurrent (currently "
                f"{plan_dict['scope']['maxConcurrent']}) is the limiter."
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


def _check_enumerated_domains(plan_dict: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for entry in plan_dict["tasks"]:
        by_dir: dict[str, int] = {}
        for domain in entry["conflictDomains"]:
            if any(wildcard in domain for wildcard in WILDCARDS):
                continue
            directory, _, _ = domain.rpartition("/")
            if directory:
                by_dir[directory] = by_dir.get(directory, 0) + 1
        for directory, count in sorted(by_dir.items()):
            if count >= 4:
                findings.append(
                    {
                        "code": "enumerated_domains",
                        "severity": "warning",
                        "task": entry["id"],
                        "message": (
                            f"{entry['id']} lists {count} individual files under "
                            f"{directory}/ — consider the glob {directory}/** unless "
                            "another task must write there concurrently."
                        ),
                    }
                )
    return findings


def _check_coarse_domains(plan_dict: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    tasks = plan_dict["tasks"]
    for entry in tasks:
        for domain in entry["conflictDomains"]:
            others = sum(
                1
                for other in tasks
                if other["id"] != entry["id"]
                and any(domains_overlap(domain, other_domain)
                        for other_domain in other["conflictDomains"])
            )
            if others >= 3:
                findings.append(
                    {
                        "code": "coarse_domain",
                        "severity": "warning",
                        "task": entry["id"],
                        "message": (
                            f"domain {domain!r} on {entry['id']} overlaps {others} other "
                            "tasks — it will serialize them all; narrow it to what the "
                            "task actually writes."
                        ),
                    }
                )
    return findings


def _check_briefs(plan_dict: dict[str, Any], wdd_dir: Path | str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for entry in plan_dict["tasks"]:
        brief = Path(wdd_dir) / entry["specPath"]
        content_lines = 0
        if brief.is_file():
            # Non-blank lines, not raw line count: a file of only blank
            # lines has content-free "length" and must still be flagged.
            content_lines = sum(
                1 for line in brief.read_text(encoding="utf-8").splitlines() if line.strip()
            )
        if content_lines < 2:
            reason = "does not exist" if not brief.is_file() else "is effectively empty"
            findings.append(
                {
                    "code": "missing_brief",
                    "severity": "warning",
                    "task": entry["id"],
                    "message": f"{entry['id']}: brief {entry['specPath']} {reason} — a worker dispatched on it will improvise.",
                }
            )
    return findings


def lint_plan(
    plan_dict: dict[str, Any], wdd_dir: Path | str | None = None
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    findings.extend(_check_serialization(plan_dict))
    findings.extend(_check_risk_distribution(plan_dict))
    findings.extend(_check_enumerated_domains(plan_dict))
    findings.extend(_check_coarse_domains(plan_dict))
    if wdd_dir is not None:
        findings.extend(_check_briefs(plan_dict, wdd_dir))
    return findings
