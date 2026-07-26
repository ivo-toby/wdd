"""Legal controller transitions, admission control, next actions, and projections."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from copy import deepcopy
from typing import Any, Callable

from .errors import IllegalTransition, RevisionConflict, ValidationError
from .schema import TASK_STATUSES, copied_state
from .store import StateStore, atomic_write_text


TERMINAL_STATUSES = {"done", "blocked", "cancelled"}
ACTIVE_STATUSES = {"in_progress", "review", "merge_ready"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def event_id(
    revision: int, event_type: str, task_id: str | None, data: dict[str, Any]
) -> str:
    """Mint a unique id for one applied event.

    This deliberately does NOT dedupe by payload. Deduping on payload alone
    cannot tell a retry from a legitimately repeated event, and getting that
    wrong silently wedges a scope: `reconcile done` takes no arguments, so a
    payload-derived key made it a one-shot per scope, and restarting a task
    after `unblock` collided with its original start.

    Retries are safe by construction instead: every transition is either
    guarded by the status it requires, or writes evidence by overwriting it.
    Callers that genuinely need at-most-once semantics pass an explicit
    ``--idempotency-key``, which is honoured exactly.
    """
    payload = json.dumps(
        {"event": event_type, "task": task_id, "data": data},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"auto:{revision}:{digest}"


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


def review_required(state: dict[str, Any], task: dict[str, Any]) -> bool:
    policy = state["scope"]["reviewPolicy"]
    if policy == "none":
        return False
    if policy == "always":
        return True
    return task.get("risk") == "high"


def has_blocking_findings(task: dict[str, Any]) -> bool:
    review = task.get("review")
    if not isinstance(review, dict):
        return False
    return any(
        isinstance(finding, dict) and finding.get("severity") in {"P1", "P2"}
        for finding in review.get("findings", [])
    )


def admission_blocker(state: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    """Return the reason a todo task may not start yet, or None if it may.

    This is the single source of truth for admission. It is enforced by the
    ``task.started`` transition, not merely advertised by ``next``.
    """
    task = _task(state, task_id)
    unmet = [
        dependency
        for dependency in task["dependsOn"]
        if state["tasks"][dependency]["status"] != "done"
    ]
    if unmet:
        return {"code": "dependencies", "dependsOn": sorted(unmet)}

    holder_by_domain: dict[str, str] = {}
    active_count = 0
    for other_id, other in sorted(state["tasks"].items()):
        if other_id == task_id or other["status"] not in ACTIVE_STATUSES:
            continue
        active_count += 1
        for domain in other["conflictDomains"]:
            holder_by_domain.setdefault(domain, other_id)

    overlap = sorted(set(task["conflictDomains"]) & set(holder_by_domain))
    if overlap:
        return {
            "code": "conflict_domains",
            "domains": overlap,
            "heldBy": sorted({holder_by_domain[domain] for domain in overlap}),
        }

    limit = state["scope"].get("maxConcurrent")
    if isinstance(limit, int) and active_count >= limit:
        return {"code": "max_concurrent", "limit": limit, "active": active_count}
    return None


def task_gate(state: dict[str, Any], task: dict[str, Any]) -> str:
    status = task["status"]
    if status == "todo":
        return "not_started"
    if status == "review":
        return "reviewing"
    if status == "merge_ready":
        freshness = task.get("freshness")
        if (
            isinstance(freshness, dict)
            and freshness.get("headSha") == task.get("headSha")
            and freshness.get("classification") in {"current", "nonmaterially_stale"}
        ):
            return "merge_ready"
        return "needs_freshness"
    if status in TERMINAL_STATUSES:
        return status
    if has_blocking_findings(task):
        return "needs_fixes"
    if not task.get("pr"):
        return "no_pr"
    if review_required(state, task):
        review = task.get("review")
        if not _head_matches(review, task.get("headSha")) or review.get("outcome") != "passed":
            return "needs_review"
    verification = task.get("verification")
    if not _head_matches(verification, task.get("headSha")) or verification.get("status") != "passed":
        return "needs_verification"
    return "ready_to_merge"


def reconciliation_due(state: dict[str, Any]) -> dict[str, Any] | None:
    reconcile = state["reconcile"]
    if reconcile["pendingNotes"]:
        return {"code": "pending_notes", "notes": len(reconcile["pendingNotes"])}
    every = reconcile.get("everyNMerges")
    if isinstance(every, int) and reconcile["mergesSinceCheckpoint"] >= every:
        return {"code": "merge_count", "merges": reconcile["mergesSinceCheckpoint"]}
    return None


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


def _require_data_string(data: dict[str, Any], key: str, event_type: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{event_type} requires data.{key}")
    return value


def transition(
    state: dict[str, Any], event_type: str, task_id: str | None, data: dict[str, Any]
) -> dict[str, Any]:
    """Apply one validated event without mutating the supplied state."""
    state = copied_state(state)
    if event_type in {"constitution.ratified", "constitution.amended"}:
        already = state["constitution"]["status"] == "ratified"
        if event_type == "constitution.ratified" and already:
            raise IllegalTransition(
                "constitution is already ratified; use 'wddctl constitution amend' to change it"
            )
        if event_type == "constitution.amended" and not already:
            raise IllegalTransition("nothing to amend; ratify the constitution first")
        actor = data.get("by")
        fingerprint = data.get("decisionFingerprint")
        if not isinstance(actor, str) or not actor:
            raise ValidationError(f"{event_type} requires data.by")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValidationError(f"{event_type} requires data.decisionFingerprint")
        previous = state["constitution"].get("ratification")
        if event_type == "constitution.amended" and isinstance(previous, dict):
            if previous.get("decisionFingerprint") == fingerprint:
                raise IllegalTransition(
                    "amendment fingerprint matches the ratified one; nothing changed"
                )
        state["constitution"] = {
            "status": "ratified",
            "ratification": {
                "by": actor,
                "decisionFingerprint": fingerprint,
                "at": utc_now(),
                **(
                    {"amendedFrom": previous.get("decisionFingerprint")}
                    if event_type == "constitution.amended" and isinstance(previous, dict)
                    else {}
                ),
            },
        }
        return state

    _require_ratified(state)

    if event_type == "reconcile.completed":
        state["reconcile"]["mergesSinceCheckpoint"] = 0
        state["reconcile"]["pendingNotes"] = []
        state["reconcile"]["lastCheckpointAt"] = utc_now()
        return state

    if event_type == "note.added":
        note = _require_data_string(data, "note", event_type)
        state["reconcile"]["pendingNotes"].append(
            {"task": task_id, "note": note, "at": utc_now()}
        )
        return state

    task = _task(state, task_id)
    if event_type == "task.started":
        _require_status(task, {"todo"}, event_type)
        blocker = admission_blocker(state, task_id)
        if blocker is not None:
            raise IllegalTransition(
                f"task {task_id} is not admissible: {json.dumps(blocker, sort_keys=True)}"
            )
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
        task["freshness"] = None
        task["merge"] = None
        task["status"] = "review" if review_required(state, task) else "in_progress"
    elif event_type == "review.recorded":
        # in_progress is accepted so that raising reviewPolicy mid-flight, or
        # reviewing a task the policy did not require, cannot wedge the gate.
        _require_status(task, {"review", "in_progress"}, event_type)
        if not task.get("pr"):
            raise IllegalTransition("review.recorded requires a submitted task")
        base_sha = _require_data_string(data, "baseSha", event_type)
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
            "baseSha": base_sha,
            "headSha": head_sha,
            "outcome": outcome,
            "findings": findings,
            "reviewer": data.get("reviewer"),
        }
        task["status"] = "in_progress"
    elif event_type == "verification.recorded":
        _require_status(task, {"in_progress"}, event_type)
        base_sha = _require_data_string(data, "baseSha", event_type)
        head_sha = _require_head(data, event_type)
        if head_sha != task.get("headSha"):
            raise IllegalTransition("verification evidence must match the task head SHA")
        review = task.get("review")
        if isinstance(review, dict) and review.get("baseSha") != base_sha:
            raise IllegalTransition("verification evidence must use the review base SHA")
        result = data.get("status")
        if result not in {"passed", "failed", "unavailable"}:
            raise ValidationError(
                "verification.recorded requires data.status: passed, failed, or unavailable"
            )
        task["verification"] = {
            "baseSha": base_sha,
            "headSha": head_sha,
            "status": result,
            "command": data.get("command"),
        }
        if result == "passed" and task_gate(state, task) == "ready_to_merge":
            task["status"] = "merge_ready"
    elif event_type == "task.head_updated":
        _require_status(task, {"in_progress", "review", "merge_ready"}, event_type)
        task["headSha"] = _require_head(data, event_type)
        task["review"] = None
        task["verification"] = None
        task["freshness"] = None
        task["merge"] = None
        task["status"] = "review" if (task.get("pr") and review_required(state, task)) else "in_progress"
    elif event_type == "freshness.recorded":
        _require_status(task, {"merge_ready"}, event_type)
        head_sha = _require_head(data, event_type)
        if head_sha != task.get("headSha"):
            raise IllegalTransition("freshness evidence must match the task head SHA")
        scope_base_ref = state["scope"].get("baseRef")
        if not scope_base_ref:
            raise IllegalTransition("freshness evidence requires a configured scope base ref")
        base_ref = _require_data_string(data, "baseRef", event_type)
        if base_ref != scope_base_ref:
            raise IllegalTransition(
                f"freshness evidence base {base_ref} does not match scope base {scope_base_ref}"
            )
        base_sha = _require_data_string(data, "baseSha", event_type)
        classification = data.get("classification")
        if classification not in {
            "current",
            "nonmaterially_stale",
            "materially_stale",
            "conflicted",
        }:
            raise ValidationError("freshness.recorded has an invalid classification")
        task["freshness"] = {
            "classification": classification,
            "baseSha": base_sha,
            "headSha": head_sha,
            "baseRef": base_ref,
            "headRef": data.get("headRef"),
        }
    elif event_type == "task.merged":
        _require_status(task, {"merge_ready"}, event_type)
        if task_gate(state, task) != "merge_ready":
            raise IllegalTransition("task.merged requires current or nonmaterially stale freshness evidence")
        if data.get("mergeVerified") is not True:
            raise IllegalTransition("task.merged requires live Git merge verification")
        head_sha = _require_head(data, event_type)
        if head_sha != task.get("headSha"):
            raise IllegalTransition("merge evidence must match the task head SHA")
        base_ref = _require_data_string(data, "baseRef", event_type)
        if base_ref != state["scope"].get("baseRef"):
            raise IllegalTransition("merge evidence must use the configured scope base ref")
        base_sha = _require_data_string(data, "baseSha", event_type)
        task["merge"] = {
            "baseRef": base_ref,
            "baseSha": base_sha,
            "headSha": head_sha,
            "verifiedAt": utc_now(),
        }
        task["status"] = "done"
        state["reconcile"]["mergesSinceCheckpoint"] += 1
    elif event_type == "task.blocked":
        _require_status(task, TASK_STATUSES - TERMINAL_STATUSES, event_type)
        reason = data.get("reason")
        if not isinstance(reason, str) or not reason:
            raise ValidationError("task.blocked requires data.reason")
        task["status"] = "blocked"
        task["blocker"] = reason
    elif event_type == "task.unblocked":
        _require_status(task, {"blocked"}, event_type)
        task["status"] = "todo" if not task.get("pr") else "in_progress"
        task["blocker"] = None
    elif event_type == "task.cancelled":
        _require_status(task, TASK_STATUSES - TERMINAL_STATUSES, event_type)
        task["status"] = "cancelled"
    else:
        raise ValidationError(f"unknown event type: {event_type}")
    return state


def apply_mutation(
    store: StateStore,
    *,
    event_type: str,
    task_id: str | None,
    data: dict[str, Any],
    idempotency_key: str | None,
    expected_revision: int | None,
    mutator: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    """Apply one atomic, revisioned, idempotent state mutation.

    ``expected_revision`` and ``idempotency_key`` are optional. When omitted the
    current revision is read under the same exclusive lock that guards the
    write, and the event gets a unique id rather than a payload-derived one —
    see :func:`event_id` for why deduping on payload is wrong.
    """
    with store.locked():
        state = store.read()
        key = idempotency_key or event_id(
            state["revision"] + 1, event_type, task_id, data
        )
        if idempotency_key and key in state["appliedIdempotencyKeys"]:
            return state, True
        if expected_revision is not None and state["revision"] != expected_revision:
            raise RevisionConflict(
                f"expected revision {expected_revision}, found {state['revision']}"
            )
        updated = mutator(state)
        updated["revision"] = state["revision"] + 1
        updated["events"].append(
            {
                "revision": updated["revision"],
                "type": event_type,
                "task": task_id,
                "idempotencyKey": key,
                "at": utc_now(),
            }
        )
        updated["appliedIdempotencyKeys"].append(key)
        updated["telemetry"]["eventApplications"] += 1
        store.write(updated)
        return updated, False


def apply_event(
    store: StateStore,
    *,
    event_type: str,
    task_id: str | None,
    data: dict[str, Any],
    idempotency_key: str | None = None,
    expected_revision: int | None = None,
) -> tuple[dict[str, Any], bool]:
    return apply_mutation(
        store,
        event_type=event_type,
        task_id=task_id,
        data=data,
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=lambda state: transition(state, event_type, task_id, data),
    )


def status_summary(state: dict[str, Any]) -> dict[str, Any]:
    counts = {status: 0 for status in sorted({task["status"] for task in state["tasks"].values()} | {"todo"})}
    active: list[dict[str, str]] = []
    for task_id, task in sorted(state["tasks"].items()):
        counts[task["status"]] = counts.get(task["status"], 0) + 1
        if task["status"] not in TERMINAL_STATUSES:
            active.append(
                {"id": task_id, "gate": task_gate(state, task), "status": task["status"]}
            )
    return {
        "scope": state["scope"],
        "revision": state["revision"],
        "constitution": state["constitution"]["status"],
        "taskCounts": counts,
        "activeTasks": active,
        "reconcile": {
            "due": reconciliation_due(state),
            "mergesSinceCheckpoint": state["reconcile"]["mergesSinceCheckpoint"],
        },
        "monitoring": state["monitoring"],
    }


GATE_ACTIONS = {
    "no_pr": "await_worker",
    "reviewing": "run_review",
    "needs_fixes": "assign_fix_writer",
    "needs_review": "run_review",
    "needs_verification": "run_verification",
    "needs_freshness": "check_branch_freshness",
    "ready_to_merge": "mark_merge_ready",
    "merge_ready": "merge_task",
}

# Per action: (command to run right now, command that records the result of the
# judgment work). Emitting these removes the translation step between what
# `next` reports and what the caller must type.
ACTION_COMMANDS: dict[str, tuple[str | None, str | None]] = {
    "start_task": ("start --task {task} --repo {repo}", None),
    "await_worker": (None, "submit --task {task} --repo {repo}"),
    "run_review": (None, "review record --task {task} --reviewer NAME --findings '[]'"),
    "assign_fix_writer": (None, "submit --task {task} --repo {repo}"),
    "run_verification": (
        None,
        "verify record --task {task} --status passed --command '<verification command>'",
    ),
    "check_branch_freshness": ("freshness record --task {task} --repo {repo}", None),
    "mark_merge_ready": (None, "verify record --task {task} --status passed"),
    "merge_task": ("merge --task {task} --repo {repo}", None),
    "run_reconciliation": (None, "reconcile done"),
}


def decorate_actions(
    result: dict[str, Any], *, state_path: str | None = None, repo: str = "."
) -> dict[str, Any]:
    """Attach the literal command for each action.

    ``command`` runs now. ``recordWith`` records the outcome once the judgment
    work (implementing, reviewing, verifying) is done.
    """
    prefix = "wddctl" + (f" --state {state_path}" if state_path else "")
    for action in result["actions"]:
        run_now, record_with = ACTION_COMMANDS.get(action["action"], (None, None))
        fields = {"task": action["task"], "repo": repo}
        if run_now:
            action["command"] = f"{prefix} {run_now.format(**fields)}"
        if record_with:
            action["recordWith"] = f"{prefix} {record_with.format(**fields)}"
    return result


def next_actions(
    state: dict[str, Any], *, max_actions: int | None = 8
) -> dict[str, Any]:
    """Return a bounded, machine-readable action queue without mutating state."""
    blockers: list[dict[str, Any]] = []
    actions: list[dict[str, str]] = []
    if state["constitution"]["status"] != "ratified":
        blockers.append(
            {
                "code": "constitution_unratified",
                "message": "Run wddctl constitution ratify before execution.",
            }
        )
        return {
            "scope": state["scope"]["id"],
            "revision": state["revision"],
            "actions": actions,
            "blockers": blockers,
        }

    due = reconciliation_due(state)
    if due is not None:
        actions.append({"task": "-", "action": "run_reconciliation", **due})

    # Simulate admission so a single pass never proposes two conflicting starts.
    projected = deepcopy(state)
    for task_id, task in sorted(state["tasks"].items()):
        if max_actions is not None and len(actions) >= max_actions:
            break
        if task["status"] == "blocked":
            blockers.append(
                {"task": task_id, "code": "blocked", "message": task["blocker"] or "blocked"}
            )
            continue
        if task["status"] in {"done", "cancelled"}:
            continue
        if task["status"] == "todo":
            blocker = admission_blocker(projected, task_id)
            if blocker is not None:
                blockers.append({"task": task_id, **blocker})
                continue
            actions.append({"task": task_id, "action": "start_task"})
            projected["tasks"][task_id]["status"] = "in_progress"
            continue
        action = GATE_ACTIONS.get(task_gate(state, task))
        if action:
            actions.append({"task": task_id, "action": action})
    return {
        "scope": state["scope"]["id"],
        "revision": state["revision"],
        "actions": actions,
        "blockers": blockers,
    }


def bounded_next_actions(
    state: dict[str, Any],
    *,
    max_bytes: int = 4096,
    state_path: str | None = None,
    repo: str = ".",
) -> dict[str, Any]:
    """Keep default next-action output small enough for an agent prompt.

    Commands are attached before the budget is measured, so the limit stays
    honest rather than being blown by decoration afterwards.
    """
    limit = 8
    full_result = next_actions(state, max_actions=None)
    while limit >= 0:
        result = next_actions(state, max_actions=limit)
        result["blockers"] = full_result["blockers"][:limit]
        result["truncated"] = (
            len(result["actions"]) < len(full_result["actions"])
            or len(result["blockers"]) < len(full_result["blockers"])
        )
        decorate_actions(result, state_path=state_path, repo=repo)
        rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if len(rendered.encode("utf-8")) <= max_bytes:
            return result
        limit -= 1
    raise ValidationError("next action output cannot fit within the requested byte limit")


def admission_schedule(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Project the order tasks would be admitted in, grouped into rounds.

    This is a preview for humans. It is not a gate: nothing waits for a round
    to finish, and the real controller admits each task the moment its own
    dependencies and conflict domains clear.
    """
    projected = deepcopy(state)
    for task in projected["tasks"].values():
        if task["status"] not in {"done", "cancelled"}:
            task["status"] = "todo"
    rounds: list[dict[str, Any]] = []
    remaining = {
        task_id
        for task_id, task in projected["tasks"].items()
        if task["status"] == "todo"
    }
    while remaining:
        admitted: list[str] = []
        for task_id in sorted(remaining):
            if admission_blocker(projected, task_id) is None:
                projected["tasks"][task_id]["status"] = "in_progress"
                admitted.append(task_id)
        if not admitted:
            rounds.append({"round": len(rounds) + 1, "tasks": [], "stalled": sorted(remaining)})
            break
        rounds.append({"round": len(rounds) + 1, "tasks": admitted})
        for task_id in admitted:
            projected["tasks"][task_id]["status"] = "done"
            remaining.discard(task_id)
    return rounds


def render_controller_state(
    state: dict[str, Any], *, state_path: str | None = None, repo: str = "."
) -> str:
    summary = status_summary(state)
    lines = [
        "<!-- Generated by wddctl. Do not edit; edit plan.json or run a wddctl command. -->",
        "",
        f"# Controller State: {summary['scope']['id']}",
        "",
        f"- Revision: {summary['revision']}",
        f"- Constitution: {summary['constitution']}",
        f"- Review policy: {summary['scope']['reviewPolicy']}",
        f"- Max concurrent: {summary['scope']['maxConcurrent'] or 'unlimited'}",
        f"- Merges since checkpoint: {summary['reconcile']['mergesSinceCheckpoint']}",
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
    next_step = bounded_next_actions(state, state_path=state_path, repo=repo)
    lines.extend(["", "## Next Actions", ""])
    for action in next_step["actions"]:
        lines.append(f"- `{action['action']}` — {action['task']}")
        if action.get("command"):
            lines.append(f"  - run: `{action['command']}`")
        if action.get("recordWith"):
            lines.append(f"  - then record: `{action['recordWith']}`")
    for blocker in next_step["blockers"]:
        label = blocker.get("task", "scope")
        lines.append(f"- Blocked: {label} ({blocker['code']})")
    if not next_step["actions"] and not next_step["blockers"]:
        lines.append("- No pending action.")
    return "\n".join(lines) + "\n"


def render_to_path(
    state: dict[str, Any], output: str, *, state_path: str | None = None, repo: str = "."
) -> None:
    atomic_write_text(
        output,
        render_controller_state(deepcopy(state), state_path=state_path, repo=repo),
    )
