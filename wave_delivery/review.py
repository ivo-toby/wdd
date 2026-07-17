"""Normalized SHA-bound review and verification result handling."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .engine import apply_event
from .errors import IllegalTransition, ValidationError
from .store import StateStore, atomic_write_text


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


def normalize_review_results(paths: list[Path | str], *, task_id: str) -> dict[str, Any]:
    if not paths:
        raise ValidationError("at least one review result is required")
    results = [_read_result(path) for path in paths]
    base_sha: str | None = None
    head_sha: str | None = None
    reviewers: list[str] = []
    findings: list[dict[str, Any]] = []
    for result in results:
        if result.get("schemaVersion") != 1 or result.get("kind") != "wddctl_review_result":
            raise ValidationError("review result must use schemaVersion 1 and kind wddctl_review_result")
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
        result_findings = result.get("findings", [])
        if not isinstance(result_findings, list):
            raise ValidationError("review result findings must be a list")
        for finding in result_findings:
            if not isinstance(finding, dict) or finding.get("severity") not in {"P1", "P2", "P3"}:
                raise ValidationError("each review finding requires severity P1, P2, or P3")
            _required_string(finding.get("summary"), "review finding summary")
            findings.append(finding)
    return {
        "baseSha": base_sha,
        "headSha": head_sha,
        "reviewer": ", ".join(sorted(set(reviewers))),
        "findings": findings,
    }


def normalize_verification_result(path: Path | str, *, task_id: str) -> dict[str, Any]:
    result = _read_result(path)
    if result.get("schemaVersion") != 1 or result.get("kind") != "wddctl_verification_result":
        raise ValidationError(
            "verification result must use schemaVersion 1 and kind wddctl_verification_result"
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
    head_sha = _required_string(task.get("headSha"), "task headSha")
    lease = (state.get("leases") or {}).get(task_id) or {}
    base_sha = base_sha or lease.get("baseSha")
    base_sha = _required_string(base_sha, "review baseSha")
    environment = os.environ.copy()
    environment.update(
        {
            "WDCTL_REVIEW_TASK": task_id,
            "WDCTL_REVIEW_BASE_SHA": base_sha,
            "WDCTL_REVIEW_HEAD_SHA": head_sha,
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


def collect_review(
    store: StateStore,
    *,
    task_id: str,
    result_paths: list[Path | str],
    idempotency_key: str,
    expected_revision: int,
) -> tuple[dict[str, Any], bool]:
    result = normalize_review_results(result_paths, task_id=task_id)
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
    idempotency_key: str,
    expected_revision: int,
) -> tuple[dict[str, Any], bool]:
    result = normalize_verification_result(result_path, task_id=task_id)
    return apply_event(
        store,
        event_type="verification.recorded",
        task_id=task_id,
        data=result,
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
    )
