"""Legal controller transitions, concise status, next actions, and projections."""

from __future__ import annotations

import datetime as dt
from copy import deepcopy
from typing import Any

from .errors import IllegalTransition, RevisionConflict, ValidationError
from .schema import TASK_STATUSES, copied_state
from .store import StateStore, atomic_write_text


TERMINAL_STATUSES = {"done", "blocked", "cancelled"}
ACTIVE_STATUSES = {"in_progress", "review", "merge_ready"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _task(state: dict[str, Any], task_id: str | None) -> dict[str, Any]:
    if not task_id:
        raise ValidationError("this event requires --task")
    try:
        return state["tasks"][task_id]
    except KeyError as error:
        raise ValidationError(f"unknown task: {task_id}") from error


def _require_ratified(state: dict[str, Any]) -> None:
    if state["constitution"]["status"] != "ratified":
        raise IllegalTransition(
            "execution is blocked until the constitution is explicitly ratified"
        )


def _head_matches(evidence: Any, head_sha: str | None) -> bool:
    return bool(
        isinstance(evidence, dict)
        and head_sha
        and evidence.get("headSha") == head_sha
    )


def has_blocking_findings(task: dict[str, Any]) -> bool:
    review = task.get("review")
    if not isinstance(review, dict):
        return False
    return any(
        isinstance(finding, dict) and finding.get("severity") in {"P1", "P2"}
        for finding in review.get("findings", [])
    )


def task_gate(task: dict[str, Any]) -> str:
    status = task["status"]
    if status == "todo":
        return "not_started"
    if status == "review":
        return "reviewing"
    if status == "merge_ready":
        return "merge_ready"
    if status in TERMINAL_STATUSES:
        return status
    if has_blocking_findings(task):
        return "needs_fixes"
    if not task.get("pr"):
        return "no_pr"
    review = task.get("review")
    if not _head_matches(review, task.get("headSha")) or review.get("outcome") != "passed":
        return "needs_review"
    verification = task.get("verification")
    if not _head_matches(verification, task.get("headSha")) or verification.get("status") != "passed":
        return "needs_verification"
    return "ready_to_merge"


def _require_status(task: dict[str, Any], allowed: set[str], event_type: str) -> None:
    if task["status"] not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise IllegalTransition(
            f"{event_type} is not legal from {task['status']}; expected one of {allowed_text}"
        )


def _require_head(data: dict[str, Any], event_type: str) -> str:
    head_sha = data.get("headSha")
    if not isinstance(head_sha, str) or not head_sha:
        raise ValidationError(f"{event_type} requires data.headSha")
    return head_sha


def transition(
    state: dict[str, Any], event_type: str, task_id: str | None, data: dict[str, Any]
) -> dict[str, Any]:
    """Apply one validated event without mutating the supplied state."""
    state = copied_state(state)
    if event_type == "constitution.ratified":
        if state["constitution"]["status"] == "ratified":
            raise IllegalTransition("constitution is already ratified")
        actor = data.get("by")
        fingerprint = data.get("decisionFingerprint")
        if not isinstance(actor, str) or not actor:
            raise ValidationError("constitution.ratified requires data.by")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValidationError(
                "constitution.ratified requires data.decisionFingerprint"
            )
        state["constitution"] = {
            "status": "ratified",
            "ratification": {
                "by": actor,
                "decisionFingerprint": fingerprint,
                "at": utc_now(),
            },
        }
        return state

    _require_ratified(state)
    task = _task(state, task_id)
    if event_type == "task.started":
        _require_status(task, {"todo"}, event_type)
        task["status"] = "in_progress"
        task["blocker"] = None
    elif event_type == "task.pr_recorded":
        _require_status(task, {"in_progress"}, event_type)
        pr = data.get("pr")
        if not isinstance(pr, str) or not pr:
            raise ValidationError("task.pr_recorded requires data.pr")
        task["pr"] = pr
        task["headSha"] = _require_head(data, event_type)
        task["review"] = None
        task["verification"] = None
        task["status"] = "review"
    elif event_type == "review.recorded":
        _require_status(task, {"review"}, event_type)
        head_sha = _require_head(data, event_type)
        if head_sha != task.get("headSha"):
            raise IllegalTransition("review evidence must match the task head SHA")
        findings = data.get("findings", [])
        if not isinstance(findings, list):
            raise ValidationError("review.recorded requires data.findings to be a list")
        for finding in findings:
            if not isinstance(finding, dict) or finding.get("severity") not in {"P1", "P2", "P3"}:
                raise ValidationError("each review finding requires severity P1, P2, or P3")
        outcome = "blocking" if any(
            finding.get("severity") in {"P1", "P2"} for finding in findings
        ) else "passed"
        task["review"] = {
            "headSha": head_sha,
            "outcome": outcome,
            "findings": findings,
            "reviewer": data.get("reviewer"),
        }
        task["status"] = "in_progress"
    elif event_type == "verification.recorded":
        _require_status(task, {"in_progress"}, event_type)
        head_sha = _require_head(data, event_type)
        if head_sha != task.get("headSha"):
            raise IllegalTransition("verification evidence must match the task head SHA")
        result = data.get("status")
        if result not in {"passed", "failed", "unavailable"}:
            raise ValidationError(
                "verification.recorded requires data.status: passed, failed, or unavailable"
            )
        task["verification"] = {
            "headSha": head_sha,
            "status": result,
            "command": data.get("command"),
        }
        if result == "passed" and task_gate(task) == "ready_to_merge":
            task["status"] = "merge_ready"
    elif event_type == "task.head_updated":
        _require_status(task, {"in_progress", "review", "merge_ready"}, event_type)
        task["headSha"] = _require_head(data, event_type)
        task["review"] = None
        task["verification"] = None
        task["status"] = "in_progress"
    elif event_type == "task.merged":
        _require_status(task, {"merge_ready"}, event_type)
        task["status"] = "done"
    elif event_type == "task.blocked":
        _require_status(task, TASK_STATUSES - TERMINAL_STATUSES, event_type)
        reason = data.get("reason")
        if not isinstance(reason, str) or not reason:
            raise ValidationError("task.blocked requires data.reason")
        task["status"] = "blocked"
        task["blocker"] = reason
    elif event_type == "task.cancelled":
        _require_status(task, TASK_STATUSES - TERMINAL_STATUSES, event_type)
        task["status"] = "cancelled"
    else:
        raise ValidationError(f"unknown event type: {event_type}")
    return state


def apply_event(
    store: StateStore,
    *,
    event_type: str,
    task_id: str | None,
    data: dict[str, Any],
    idempotency_key: str,
    expected_revision: int,
) -> tuple[dict[str, Any], bool]:
    if not idempotency_key:
        raise ValidationError("idempotency key must not be empty")
    with store.locked():
        state = store.read()
        if idempotency_key in state["appliedIdempotencyKeys"]:
            return state, True
        if state["revision"] != expected_revision:
            raise RevisionConflict(
                f"expected revision {expected_revision}, found {state['revision']}"
            )
        updated = transition(state, event_type, task_id, data)
        updated["revision"] = state["revision"] + 1
        updated["events"].append(
            {
                "revision": updated["revision"],
                "type": event_type,
                "task": task_id,
                "idempotencyKey": idempotency_key,
                "at": utc_now(),
            }
        )
        updated["appliedIdempotencyKeys"].append(idempotency_key)
        updated["telemetry"]["eventApplications"] += 1
        store.write(updated)
        return updated, False


def status_summary(state: dict[str, Any]) -> dict[str, Any]:
    counts = {status: 0 for status in sorted({task["status"] for task in state["tasks"].values()} | {"todo"})}
    active: list[dict[str, str]] = []
    for task_id, task in sorted(state["tasks"].items()):
        counts[task["status"]] = counts.get(task["status"], 0) + 1
        if task["status"] not in TERMINAL_STATUSES:
            active.append({"id": task_id, "gate": task_gate(task), "status": task["status"]})
    return {
        "scope": state["scope"],
        "revision": state["revision"],
        "constitution": state["constitution"]["status"],
        "taskCounts": counts,
        "activeTasks": active,
        "monitoring": state["monitoring"],
    }


def _dependency_blockers(state: dict[str, Any], task: dict[str, Any]) -> list[str]:
    return [
        dependency
        for dependency in task["dependsOn"]
        if state["tasks"][dependency]["status"] != "done"
    ]


def next_actions(state: dict[str, Any], *, max_actions: int = 8) -> dict[str, Any]:
    """Return a bounded, machine-readable action queue without mutating state."""
    blockers: list[dict[str, Any]] = []
    actions: list[dict[str, str]] = []
    if state["constitution"]["status"] != "ratified":
        blockers.append(
            {
                "code": "constitution_unratified",
                "message": "Run wdctl constitution ratify before execution.",
            }
        )
    else:
        occupied_domains: set[str] = set()
        for task in state["tasks"].values():
            if task["status"] in ACTIVE_STATUSES:
                occupied_domains.update(task["conflictDomains"])
        for task_id, task in sorted(state["tasks"].items()):
            if len(actions) >= max_actions:
                break
            if task["status"] == "blocked":
                blockers.append({"task": task_id, "code": "blocked", "message": task["blocker"] or "blocked"})
                continue
            if task["status"] in {"done", "cancelled"}:
                continue
            dependencies = _dependency_blockers(state, task)
            if dependencies:
                blockers.append({"task": task_id, "code": "dependencies", "dependsOn": dependencies})
                continue
            if task["status"] == "todo":
                overlap = sorted(set(task["conflictDomains"]) & occupied_domains)
                if overlap:
                    blockers.append({"task": task_id, "code": "conflict_domains", "domains": overlap})
                    continue
                actions.append({"task": task_id, "action": "start_task"})
                occupied_domains.update(task["conflictDomains"])
                continue
            gate = task_gate(task)
            action = {
                "no_pr": "await_worker",
                "reviewing": "collect_review",
                "needs_fixes": "assign_fix_writer",
                "needs_review": "run_review",
                "needs_verification": "run_verification",
                "ready_to_merge": "mark_merge_ready",
                "merge_ready": "merge_or_request_human_merge",
            }.get(gate)
            if action:
                actions.append({"task": task_id, "action": action})
    return {
        "scope": state["scope"]["id"],
        "revision": state["revision"],
        "actions": actions,
        "blockers": blockers,
    }


def bounded_next_actions(state: dict[str, Any], *, max_bytes: int = 2048) -> dict[str, Any]:
    """Keep default next-action output small enough for an agent prompt."""
    import json

    limit = 8
    full_result = next_actions(state, max_actions=8)
    while limit >= 0:
        result = next_actions(state, max_actions=limit)
        result["blockers"] = full_result["blockers"][:limit]
        result["truncated"] = (
            len(result["actions"]) < len(full_result["actions"])
            or len(result["blockers"]) < len(full_result["blockers"])
        )
        if len(json.dumps(result, separators=(",", ":")).encode("utf-8")) <= max_bytes:
            return result
        limit -= 1
    raise ValidationError("next action output cannot fit within the requested byte limit")


def render_controller_state(state: dict[str, Any]) -> str:
    summary = status_summary(state)
    lines = [
        "<!-- Generated by wdctl from canonical schema-v2 state. Do not edit. -->",
        "",
        f"# Controller State: {summary['scope']['id']}",
        "",
        f"- Revision: {summary['revision']}",
        f"- Constitution: {summary['constitution']}",
        f"- Monitoring: {summary['monitoring']['mode']} ({summary['monitoring']['status']})",
        "",
        "## Active Task Gates",
        "",
        "| Task | Status | Gate |",
        "|---|---|---|",
    ]
    for task in summary["activeTasks"]:
        lines.append(f"| {task['id']} | {task['status']} | {task['gate']} |")
    if not summary["activeTasks"]:
        lines.append("| None | - | - |")
    next_step = bounded_next_actions(state)
    lines.extend(["", "## Next Actions", ""])
    for action in next_step["actions"]:
        lines.append(f"- `{action['action']}` — {action['task']}")
    for blocker in next_step["blockers"]:
        label = blocker.get("task", "scope")
        lines.append(f"- Blocked: {label} ({blocker['code']})")
    if not next_step["actions"] and not next_step["blockers"]:
        lines.append("- No pending action.")
    return "\n".join(lines) + "\n"


def render_to_path(state: dict[str, Any], output: str) -> None:
    atomic_write_text(output, render_controller_state(deepcopy(state)))
