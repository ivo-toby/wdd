"""SHA-bound review and verification evidence.

Evidence is pinned to the exact commit it was produced against. The controller
already knows the base and head SHAs, so callers never supply them: recording
evidence takes findings or a status, nothing more.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .config import effective_config_digest, project
from .engine import apply_event
from .errors import IllegalTransition, ValidationError
from .git import is_ancestor, require_repository, resolve_ref, run_git
from .store import StateStore, atomic_write_text


REVIEW_RESULT_KIND = "wddctl_review_result"
VERIFICATION_RESULT_KIND = "wddctl_verification_result"
SEVERITIES = {"P1", "P2", "P3"}


def _read_result(path: Path | str) -> dict[str, Any]:
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValidationError(f"result file does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"result file is not valid JSON: {path}: {error}") from error
    if not isinstance(result, dict):
        raise ValidationError("result file must contain a JSON object")
    return result


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{name} must be a non-empty string")
    return value


def validate_findings(findings: Any) -> list[dict[str, Any]]:
    if not isinstance(findings, list):
        raise ValidationError("findings must be a list")
    validated: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValidationError("each finding must be an object")
        severity = finding.get("severity")
        if severity not in SEVERITIES:
            raise ValidationError(
                f"each finding requires severity P1, P2, or P3 (got {severity!r})"
            )
        _required_string(finding.get("summary"), "finding summary")
        validated.append(finding)
    return validated


def evidence_shas(
    state: dict[str, Any], task_id: str, *, repo: Path | str | None = None
) -> tuple[str, str]:
    """Return the (baseSha, headSha) any evidence for this task must be pinned to."""
    try:
        task = state["tasks"][task_id]
    except KeyError as error:
        raise ValidationError(f"unknown task: {task_id}") from error
    head_sha = _required_string(task.get("headSha"), f"task {task_id} headSha")
    review = task.get("review")
    if isinstance(review, dict) and review.get("headSha") == head_sha:
        # Keep verification pinned to the same base the review used.
        return review["baseSha"], head_sha
    base_ref = state["scope"].get("baseRef")
    if repo is not None and base_ref:
        repository = require_repository(repo)
        merge_base = run_git(
            repository, "merge-base", base_ref, head_sha, check=False
        ).stdout.strip()
        if merge_base:
            if merge_base == head_sha:
                # The head is already contained in the base — typically it was
                # merged outside wddctl. The derived range is empty, so review
                # and verification would describe no changes at all, which is
                # how a mandatory review got bypassed.
                raise IllegalTransition(
                    f"{task_id} describes an empty range against {base_ref}: its head "
                    f"{head_sha} is already contained in the base, so there is nothing "
                    "to review or verify"
                )
            return merge_base, head_sha
    lease = (state.get("leases") or {}).get(task_id) or {}
    base_sha = lease.get("baseSha")
    if not base_sha:
        raise IllegalTransition(
            f"cannot determine the base SHA for {task_id}; pass --repo so it can be computed"
        )
    return base_sha, head_sha


def resolved_review_model(task: dict[str, Any], models: dict[str, Any] | None) -> str | None:
    """The review model a task's evidence binds to (spec Sec2: "the selected
    review model it actually ran under"). Hand-synced twin of
    `engine._resolve_model`'s run_review branch / `runner._resolve_dispatch_model`
    / `migration._resolve_review_model_for_stamp` -- all four implement the
    identical precedence (task override -> risk-tiered `models.review` ->
    None); keep them in step if it ever changes. A task-level `reviewModel`
    override always wins; otherwise `models.review`, tiered by the task's own
    risk when given in object form (a plain string means both tiers).
    """
    override = task.get("reviewModel")
    if isinstance(override, str) and override:
        return override
    if not models:
        return None
    review = models.get("review")
    if isinstance(review, dict):
        tier = "highRisk" if task.get("risk") == "high" else "default"
        value = review.get(tier)
    else:
        value = review
    return value if isinstance(value, str) and value else None


def _evidence_binding(task: dict[str, Any], layers: dict[str, Any] | None) -> dict[str, Any]:
    """The resolved-decision + projection-digest fields a fresh task review
    record binds to (spec Sec2), or `{}` with no config available at all
    (legacy repos predating config.json -- optional per schema.py)."""
    if layers is None:
        return {}
    return {
        "resolvedRisk": task.get("risk"),
        "reviewModel": resolved_review_model(task, layers["effective"].get("models")),
        "configSha256": effective_config_digest(project(layers["effective"], "taskReview")),
    }


def _verification_evidence_binding(layers: dict[str, Any] | None) -> dict[str, Any]:
    if layers is None:
        return {}
    return {
        "configSha256": effective_config_digest(project(layers["effective"], "taskVerification")),
    }


def review_evidence_stale(task: dict[str, Any], layers: dict[str, Any]) -> str | None:
    """None if a task's recorded review evidence still matches what the
    CURRENT effective config + task state would produce; else a message
    naming the mismatch (spec Sec2: "Config projections alone are not
    sufficient for review evidence... review records additionally bind their
    resolved decision values"). Consulted by merge's gate (Task 5) in
    addition to `engine.task_gate`'s own dynamic `review_required` check --
    the two are complementary: a policy that already required review
    (`always`) does not newly demand one when risk rises, so only comparing
    the BOUND resolvedRisk/reviewModel/digest against fresh values catches a
    review that ran under decisions that no longer hold.
    """
    review = task.get("review")
    if not isinstance(review, dict):
        return None
    current_risk = task.get("risk")
    if "resolvedRisk" in review and review["resolvedRisk"] != current_risk:
        return (
            f"recorded review resolvedRisk {review['resolvedRisk']!r} no longer matches "
            f"the task's current risk {current_risk!r}"
        )
    if "reviewModel" in review:
        current_model = resolved_review_model(task, layers["effective"].get("models"))
        if review["reviewModel"] != current_model:
            return (
                f"recorded review reviewModel {review['reviewModel']!r} no longer matches "
                f"the currently resolved model {current_model!r}"
            )
    if "configSha256" in review:
        current_digest = effective_config_digest(project(layers["effective"], "taskReview"))
        if review["configSha256"] != current_digest:
            return "recorded review's config projection no longer matches the current config"
    return None


def verification_evidence_stale(task: dict[str, Any], layers: dict[str, Any]) -> str | None:
    """`review_evidence_stale`'s verification-evidence counterpart (Task 5,
    spec Sec2): compares the recorded `taskVerification` projection digest
    against a freshly recomputed one."""
    verification = task.get("verification")
    if not isinstance(verification, dict):
        return None
    if "configSha256" in verification:
        current_digest = effective_config_digest(project(layers["effective"], "taskVerification"))
        if verification["configSha256"] != current_digest:
            return (
                "recorded verification's config projection no longer matches the current config"
            )
    return None


def record_review(
    store: StateStore,
    *,
    task_id: str,
    findings: list[dict[str, Any]],
    reviewer: str,
    repo: Path | str | None = None,
    layers: dict[str, Any] | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    state = store.read()
    base_sha, head_sha = evidence_shas(state, task_id, repo=repo)
    data = {
        "baseSha": base_sha,
        "headSha": head_sha,
        "findings": validate_findings(findings),
        "reviewer": _required_string(reviewer, "reviewer"),
    }
    data.update(_evidence_binding(state["tasks"][task_id], layers))
    return apply_event(
        store,
        event_type="review.recorded",
        task_id=task_id,
        data=data,
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
    )


def record_verification(
    store: StateStore,
    *,
    task_id: str,
    status: str,
    command: str | None = None,
    repo: Path | str | None = None,
    layers: dict[str, Any] | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    if status not in {"passed", "failed", "unavailable"}:
        raise ValidationError("verification status must be passed, failed, or unavailable")
    state = store.read()
    base_sha, head_sha = evidence_shas(state, task_id, repo=repo)
    data = {
        "baseSha": base_sha,
        "headSha": head_sha,
        "status": status,
        "command": command,
    }
    data.update(_verification_evidence_binding(layers))
    return apply_event(
        store,
        event_type="verification.recorded",
        task_id=task_id,
        data=data,
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
    )


def normalize_review_results(paths: list[Path | str], *, task_id: str) -> dict[str, Any]:
    """Merge one or more external reviewer result files into a single record.

    Each file must be a JSON object shaped like::

        {
          "schemaVersion": 1,
          "kind": "wddctl_review_result",
          "task": "TASK-001",
          "baseSha": "<sha>",
          "headSha": "<sha>",
          "reviewer": "name",
          "findings": [{"severity": "P1", "summary": "...", "file": "...", "line": 1}]
        }
    """
    if not paths:
        raise ValidationError("at least one review result is required")
    results = [_read_result(path) for path in paths]
    base_sha: str | None = None
    head_sha: str | None = None
    reviewers: list[str] = []
    findings: list[dict[str, Any]] = []
    for result in results:
        if result.get("schemaVersion") != 1 or result.get("kind") != REVIEW_RESULT_KIND:
            raise ValidationError(
                f'review result must set "schemaVersion": 1 and "kind": "{REVIEW_RESULT_KIND}"'
            )
        if result.get("task") not in {None, task_id}:
            raise ValidationError(f"review result belongs to {result.get('task')}, not {task_id}")
        result_base = _required_string(result.get("baseSha"), "review result baseSha")
        result_head = _required_string(result.get("headSha"), "review result headSha")
        if base_sha is not None and result_base != base_sha:
            raise ValidationError("all review results must use the same baseSha")
        if head_sha is not None and result_head != head_sha:
            raise ValidationError("all review results must use the same headSha")
        base_sha, head_sha = result_base, result_head
        reviewers.append(_required_string(result.get("reviewer"), "review result reviewer"))
        findings.extend(validate_findings(result.get("findings", [])))
    return {
        "baseSha": base_sha,
        "headSha": head_sha,
        "reviewer": ", ".join(sorted(set(reviewers))),
        "findings": findings,
    }


def normalize_verification_result(path: Path | str, *, task_id: str) -> dict[str, Any]:
    """Read one external verification result file.

    The file must be a JSON object shaped like::

        {
          "schemaVersion": 1,
          "kind": "wddctl_verification_result",
          "task": "TASK-001",
          "baseSha": "<sha>",
          "headSha": "<sha>",
          "status": "passed",
          "command": "pytest -q"
        }
    """
    result = _read_result(path)
    if result.get("schemaVersion") != 1 or result.get("kind") != VERIFICATION_RESULT_KIND:
        raise ValidationError(
            f'verification result must set "schemaVersion": 1 and "kind": "{VERIFICATION_RESULT_KIND}"'
        )
    if result.get("task") not in {None, task_id}:
        raise ValidationError(f"verification result belongs to {result.get('task')}, not {task_id}")
    status = result.get("status")
    if status not in {"passed", "failed", "unavailable"}:
        raise ValidationError("verification result status must be passed, failed, or unavailable")
    return {
        "baseSha": _required_string(result.get("baseSha"), "verification result baseSha"),
        "headSha": _required_string(result.get("headSha"), "verification result headSha"),
        "status": status,
        "command": result.get("command"),
    }


def run_review(
    store: StateStore,
    *,
    repo: Path | str,
    task_id: str,
    command: list[str],
    output: Path | str,
    base_sha: str | None = None,
) -> dict[str, Any]:
    """Run one configured reviewer command against frozen base/head SHAs."""
    if not command or not all(isinstance(item, str) and item for item in command):
        raise ValidationError("review command must be a non-empty JSON string array")
    state = store.read()
    if state["constitution"]["status"] != "ratified":
        raise IllegalTransition("review execution requires an explicitly ratified constitution")
    try:
        task = state["tasks"][task_id]
    except KeyError as error:
        raise ValidationError(f"unknown task: {task_id}") from error
    if task["status"] != "review":
        raise IllegalTransition("review execution requires a task in the review gate")
    derived_base, head_sha = evidence_shas(state, task_id, repo=repo)
    base_sha = base_sha or derived_base
    environment = os.environ.copy()
    environment.update(
        {
            "WDDCTL_REVIEW_TASK": task_id,
            "WDDCTL_REVIEW_BASE_SHA": base_sha,
            "WDDCTL_REVIEW_HEAD_SHA": head_sha,
        }
    )
    result = subprocess.run(
        command,
        cwd=repo,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise ValidationError(f"review command failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        review_result = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValidationError("review command did not emit a JSON result") from error
    temporary = Path(output)
    atomic_write_text(temporary, json.dumps(review_result, indent=2, sort_keys=True) + "\n")
    normalized = normalize_review_results([temporary], task_id=task_id)
    if normalized["baseSha"] != base_sha or normalized["headSha"] != head_sha:
        raise ValidationError("review command result does not match the frozen base/head SHA")
    return {"output": str(temporary), **normalized}


def verify_external_shas(
    state: dict[str, Any], task_id: str, result: dict[str, Any], repo: Path | str | None
) -> None:
    """Bind externally supplied evidence to real commits.

    The transition already rejects a headSha that is not the task's current
    head. The base was trusted, so a result naming a nonexistent base SHA was
    accepted and the task still reached merge. Check the base is a real commit
    and actually an ancestor of the head it claims to describe.
    """
    task = state["tasks"][task_id]
    base_sha, head_sha = result["baseSha"], result["headSha"]
    if head_sha != task.get("headSha"):
        raise IllegalTransition(
            f"evidence head {head_sha} does not match the current head of {task_id}"
        )
    if repo is None:
        raise IllegalTransition(
            "collecting external evidence requires --repo so its SHAs can be verified"
        )
    repository = require_repository(repo)
    for label, sha in (("baseSha", base_sha), ("headSha", head_sha)):
        try:
            resolve_ref(repository, sha)
        except ValidationError as error:
            raise IllegalTransition(
                f"evidence {label} {sha} is not a commit in this repository"
            ) from error
    # "Any ancestor" is too weak: baseSha == headSha describes an empty range,
    # so a review of nothing was accepted. The base must be the exact one the
    # controller derives for this task.
    expected_base, expected_head = evidence_shas(state, task_id, repo=repository)
    if base_sha != expected_base or head_sha != expected_head:
        raise IllegalTransition(
            f"evidence for {task_id} must describe {expected_base}..{expected_head}; "
            f"got {base_sha}..{head_sha}"
        )


def collect_review(
    store: StateStore,
    *,
    task_id: str,
    result_paths: list[Path | str],
    repo: Path | str | None = None,
    layers: dict[str, Any] | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    state = store.read()
    result = normalize_review_results(result_paths, task_id=task_id)
    verify_external_shas(state, task_id, result, repo)
    result.update(_evidence_binding(state["tasks"][task_id], layers))
    return apply_event(
        store,
        event_type="review.recorded",
        task_id=task_id,
        data=result,
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
    )


def collect_verification(
    store: StateStore,
    *,
    task_id: str,
    result_path: Path | str,
    repo: Path | str | None = None,
    layers: dict[str, Any] | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    result = normalize_verification_result(result_path, task_id=task_id)
    verify_external_shas(store.read(), task_id, result, repo)
    result.update(_verification_evidence_binding(layers))
    return apply_event(
        store,
        event_type="verification.recorded",
        task_id=task_id,
        data=result,
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
    )
