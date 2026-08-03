"""Deterministic plan-quality checks.

Every check here exists because an agent-authored plan exhibited the failure
in the wild: a fully serialized dependency chain, every task marked high
risk, and per-file conflict-domain lists so exhaustive they were pure
ceremony. Lint warns; it never blocks unless the caller passes --strict.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .domains import WILDCARDS, domains_overlap, matches_domain
from .engine import admission_schedule
from .errors import ValidationError
from .paths import resolve_artifact
from .plan import state_from_plan


def _resolve_brief(wdd_dir: Path | str, spec_path: str) -> Path | None:
    """A task's brief file, resolved through the one typed resolver.

    Cross-reference: `wave_delivery/paths.py`'s `resolve_artifact` (spec
    Sec1, Global Constraints "one resolver"); `epic=None` is Task 1's
    transition-mode fallback (unchanged flat `.wdd/` resolution until Task
    4). Returns None rather than raising for a ref the resolver rejects
    (absolute, traversal, wrong namespace, ...) -- lint is advisory and
    must never crash on a malformed plan.json (module docstring: "lint
    warns; it never blocks"); callers already treat a brief that isn't
    there as the ordinary `missing_brief` finding, so folding "rejected
    ref" into that same "not there" path costs nothing.
    """
    try:
        return resolve_artifact(spec_path, wdd_dir=wdd_dir, epic=None)
    except ValidationError:
        return None


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
        brief = _resolve_brief(wdd_dir, entry["specPath"])
        brief_is_file = brief is not None and brief.is_file()
        content_lines = 0
        if brief_is_file:
            # Non-blank lines, not raw line count: a file of only blank
            # lines has content-free "length" and must still be flagged.
            content_lines = sum(
                1 for line in brief.read_text(encoding="utf-8").splitlines() if line.strip()
            )
        if content_lines < 2:
            reason = "does not exist" if not brief_is_file else "is effectively empty"
            findings.append(
                {
                    "code": "missing_brief",
                    "severity": "warning",
                    "task": entry["id"],
                    "message": f"{entry['id']}: brief {entry['specPath']} {reason} — a worker dispatched on it will improvise.",
                }
            )
            continue
        # Briefs are prose for the worker; a JSON/YAML-shaped blob means the
        # author confused the brief with plan.json (seen with small models).
        stripped = brief.read_text(encoding="utf-8").lstrip()
        if stripped[:1] in "{[":
            findings.append(
                {
                    "code": "nonprose_brief",
                    "severity": "warning",
                    "task": entry["id"],
                    "message": (
                        f"{entry['id']}: brief {entry['specPath']} reads as JSON/data, "
                        "not prose — a worker needs sentences (objective, scope, "
                        "verification), not a second plan.json."
                    ),
                }
            )
    return findings


def _check_spec(wdd_dir: Path | str) -> list[dict[str, Any]]:
    # Cross-reference: wave_delivery/paths.py's `resolve_artifact` (spec
    # Sec1, Global Constraints "one resolver"); "spec.md" is a fixed
    # epic-namespace literal, so this can never raise. `epic=None` is Task
    # 1's transition-mode fallback (unchanged flat `.wdd/` resolution).
    spec = resolve_artifact("spec.md", wdd_dir=wdd_dir, epic=None)
    content_lines = 0
    if spec.is_file():
        content_lines = sum(
            1 for line in spec.read_text(encoding="utf-8").splitlines() if line.strip()
        )
    if content_lines >= 2:
        return []
    reason = "does not exist" if not spec.is_file() else "is effectively empty"
    return [
        {
            "code": "missing_spec",
            "severity": "warning",
            "message": (
                f".wdd/spec.md {reason} — the finalize phase reviews the epic "
                "branch against it, and without it there is no agreed record of "
                "what this scope delivers. Run the intake (wdd-intake) first."
            ),
        }
    ]


_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
# The spec's Integration surfaces convention: `- `path/glob` — owned by: ...`.
# Only the backtick-quoted path is load-bearing; the "owned by" prose is a
# named responsibility for humans, not something lint resolves.
_SURFACE_LINE_RE = re.compile(r"^-\s+`([^`]+)`")
# A context ref counts as an acceptance-criteria mapping only when it is
# EXACTLY `spec.md#AC-<digits>` -- prefix junk like `spec.md#AC-garbage`
# does not match `\d+$` and so does not count (spec Sec3).
_AC_REF_RE = re.compile(r"^spec\.md#AC-\d+$")


def _sections(text: str) -> dict[str, list[str]]:
    """Map lowercased `## ` heading name -> its body lines (to the next heading).

    Tolerant by construction: a file with no headings at all yields `{}`,
    never an error -- callers treat an absent section the same as an empty
    one. Shared by the brief (Deliverable/Interfaces) and design.md
    (Integration surfaces) parsers below.
    """
    lines = text.splitlines()
    headings = [
        (match.group(1).strip().lower(), index)
        for index, line in enumerate(lines)
        if (match := _HEADING_RE.match(line))
    ]
    sections: dict[str, list[str]] = {}
    for position, (name, start) in enumerate(headings):
        end = headings[position + 1][1] if position + 1 < len(headings) else len(lines)
        sections.setdefault(name, []).extend(lines[start + 1 : end])
    return sections


def _section_nonempty(sections: dict[str, list[str]], name: str) -> bool:
    return any(line.strip() for line in sections.get(name, []))


def _check_deliverable_and_interfaces(
    plan_dict: dict[str, Any], wdd_dir: Path | str
) -> list[dict[str, Any]]:
    """Brief template's two required, linted sections (spec Sec3).

    A brief that `_check_briefs` already flagged as missing/empty is skipped
    here -- one finding (`missing_brief`) for a nonexistent file, not three.
    """
    findings: list[dict[str, Any]] = []
    for entry in plan_dict["tasks"]:
        brief = _resolve_brief(wdd_dir, entry["specPath"])
        if brief is None or not brief.is_file():
            continue
        sections = _sections(brief.read_text(encoding="utf-8"))
        if not _section_nonempty(sections, "deliverable"):
            findings.append(
                {
                    "code": "missing_deliverable",
                    "severity": "warning",
                    "task": entry["id"],
                    "message": (
                        f"{entry['id']}: brief {entry['specPath']} has no non-empty "
                        "'## Deliverable' section — the reviewer's first question is "
                        "whether the diff produces it."
                    ),
                }
            )
        if not _section_nonempty(sections, "interfaces"):
            findings.append(
                {
                    "code": "missing_interfaces",
                    "severity": "warning",
                    "task": entry["id"],
                    "message": (
                        f"{entry['id']}: brief {entry['specPath']} has no non-empty "
                        "'## Interfaces' section — Consumes/Produces should be consistent "
                        "with design.md."
                    ),
                }
            )
    return findings


def _check_missing_criteria(plan_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """Advisory: a task with no `spec.md#AC-<n>` ref discharges no acceptance
    criterion (spec Sec3) -- genuinely internal tasks exist, so this never
    blocks, only surfaces for a human to confirm."""
    findings: list[dict[str, Any]] = []
    for entry in plan_dict["tasks"]:
        refs = entry.get("context") or []
        if any(_AC_REF_RE.match(ref) for ref in refs):
            continue
        findings.append(
            {
                "code": "missing_criteria",
                "severity": "warning",
                "task": entry["id"],
                "message": (
                    f"{entry['id']}: no context ref maps to a numbered acceptance "
                    "criterion (spec.md#AC-<n>) — confirm this task is genuinely internal."
                ),
            }
        )
    return findings


def _intake_recorded(state: dict[str, Any] | None) -> bool:
    """True once at least one intake rung (spec/research/design) is recorded.

    Legacy scopes are wholesale-exempt from the ladder (schema Sec7) and
    never carry these keys, so they fall out of this check for free.
    """
    if not state:
        return False
    intake = state.get("intake") or {}
    if intake.get("legacy") is True:
        return False
    return any(intake.get(key) is not None for key in ("spec", "research", "design"))


def _check_missing_context(
    plan_dict: dict[str, Any], state: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """A scope with recorded intake artifacts but a task carrying no
    `context` refs is losing the machine-carried handover those artifacts
    exist to provide (spec Sec3)."""
    if not _intake_recorded(state):
        return []
    findings: list[dict[str, Any]] = []
    for entry in plan_dict["tasks"]:
        if entry.get("context"):
            continue
        findings.append(
            {
                "code": "missing_context",
                "severity": "warning",
                "task": entry["id"],
                "message": (
                    f"{entry['id']}: intake artifacts are recorded but this task carries "
                    "no 'context' refs — handover will rely on memory, not machine-carried "
                    "evidence."
                ),
            }
        )
    return findings


def _integration_surfaces(wdd_dir: Path | str) -> list[str]:
    """Paths listed under design.md's '## Integration surfaces' section.

    Tolerant of an absent file or absent/empty section (both return `[]`,
    never raise) -- design.md is optional at lint time, unlike at intake
    `design` approval where its presence is enforced.
    """
    # Cross-reference: wave_delivery/paths.py's `resolve_artifact` (spec
    # Sec1, Global Constraints "one resolver"); "design.md" is a fixed
    # epic-namespace literal, so this can never raise. `epic=None` is Task
    # 1's transition-mode fallback (unchanged flat `.wdd/` resolution).
    design = resolve_artifact("design.md", wdd_dir=wdd_dir, epic=None)
    if not design.is_file():
        return []
    sections = _sections(design.read_text(encoding="utf-8"))
    lines = sections.get("integration surfaces", [])
    surfaces = []
    for line in lines:
        match = _SURFACE_LINE_RE.match(line.strip())
        if match:
            surfaces.append(match.group(1))
    return surfaces


def _check_unowned_surfaces(
    plan_dict: dict[str, Any], wdd_dir: Path | str
) -> list[dict[str, Any]]:
    """design.md's Integration surfaces vs. the plan's conflictDomains (spec Sec2).

    A surface with producers and no task's conflictDomains covering it is a
    design error caught mechanically: nobody owns writing to it, so multiple
    tasks (or none) will improvise there.
    """
    surfaces = _integration_surfaces(wdd_dir)
    if not surfaces:
        return []
    all_domains = [
        domain for entry in plan_dict["tasks"] for domain in entry.get("conflictDomains", [])
    ]
    findings: list[dict[str, Any]] = []
    for path in surfaces:
        if any(matches_domain(path, domain) for domain in all_domains):
            continue
        findings.append(
            {
                "code": "unowned_surface",
                "severity": "warning",
                "message": (
                    f"design.md lists integration surface `{path}` but no task's "
                    "conflictDomains cover it — a surface with producers and no owning "
                    "task is a design error."
                ),
            }
        )
    return findings


def lint_plan(
    plan_dict: dict[str, Any],
    wdd_dir: Path | str | None = None,
    state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    findings.extend(_check_serialization(plan_dict))
    findings.extend(_check_risk_distribution(plan_dict))
    findings.extend(_check_enumerated_domains(plan_dict))
    findings.extend(_check_coarse_domains(plan_dict))
    findings.extend(_check_missing_criteria(plan_dict))
    findings.extend(_check_missing_context(plan_dict, state))
    if wdd_dir is not None:
        findings.extend(_check_spec(wdd_dir))
        findings.extend(_check_briefs(plan_dict, wdd_dir))
        findings.extend(_check_deliverable_and_interfaces(plan_dict, wdd_dir))
        findings.extend(_check_unowned_surfaces(plan_dict, wdd_dir))
    return findings
