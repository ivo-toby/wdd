"""Schema-v2 model validation with no runtime dependencies."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .errors import ValidationError


SCHEMA_VERSION = 2
TASK_STATUSES = {
    "todo",
    "in_progress",
    "review",
    "merge_ready",
    "done",
    "blocked",
    "cancelled",
}
CONSTITUTION_STATUSES = {"draft", "ratified"}


def task_state(
    task_id: str,
    *,
    depends_on: list[str] | None = None,
    conflict_domains: list[str] | None = None,
    spec_path: str | None = None,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "specPath": spec_path or f"tasks/{task_id}.md",
        "status": "todo",
        "dependsOn": list(depends_on or []),
        "conflictDomains": list(conflict_domains or []),
        "branch": None,
        "worktree": None,
        "headSha": None,
        "pr": None,
        "review": None,
        "verification": None,
        "freshness": None,
        "blocker": None,
    }


def new_state(scope_id: str, scope_kind: str = "epic") -> dict[str, Any]:
    if not scope_id:
        raise ValidationError("scope id must not be empty")
    if scope_kind not in {"epic", "micro_wave"}:
        raise ValidationError("scope kind must be 'epic' or 'micro_wave'")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "revision": 0,
        "scope": {"id": scope_id, "kind": scope_kind},
        "constitution": {"status": "draft", "ratification": None},
        "tasks": {},
        "waves": {},
        "monitoring": {
            "mode": "manual",
            "status": "inactive",
            "lastCheckedAt": None,
            "nextCheckDueAt": None,
            "observations": {},
        },
        "events": [],
        "appliedIdempotencyKeys": [],
        "telemetry": {"eventApplications": 0, "renderCount": 0},
    }


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{name} must be an object")
    return value


def _require_string(value: Any, name: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{name} must be a non-empty string")


def validate_state(state: dict[str, Any]) -> None:
    if not isinstance(state, dict):
        raise ValidationError("controller state must be an object")
    if state.get("schemaVersion") != SCHEMA_VERSION:
        raise ValidationError(
            f"unsupported schemaVersion {state.get('schemaVersion')!r}; expected {SCHEMA_VERSION}"
        )
    revision = state.get("revision")
    if not isinstance(revision, int) or revision < 0:
        raise ValidationError("revision must be a non-negative integer")

    scope = _require_mapping(state.get("scope"), "scope")
    _require_string(scope.get("id"), "scope.id")
    if scope.get("kind") not in {"epic", "micro_wave"}:
        raise ValidationError("scope.kind must be 'epic' or 'micro_wave'")

    constitution = _require_mapping(state.get("constitution"), "constitution")
    constitution_status = constitution.get("status")
    if constitution_status not in CONSTITUTION_STATUSES:
        raise ValidationError("constitution.status must be 'draft' or 'ratified'")
    ratification = constitution.get("ratification")
    if constitution_status == "ratified":
        ratification = _require_mapping(ratification, "constitution.ratification")
        _require_string(ratification.get("by"), "constitution.ratification.by")
        _require_string(
            ratification.get("decisionFingerprint"),
            "constitution.ratification.decisionFingerprint",
        )

    tasks = _require_mapping(state.get("tasks"), "tasks")
    for task_id, task in tasks.items():
        _require_string(task_id, "tasks key")
        task = _require_mapping(task, f"tasks.{task_id}")
        if task.get("id") != task_id:
            raise ValidationError(f"tasks.{task_id}.id must match its object key")
        _require_string(task.get("specPath"), f"tasks.{task_id}.specPath")
        if task.get("status") not in TASK_STATUSES:
            raise ValidationError(f"tasks.{task_id}.status is invalid")
        for field in ("dependsOn", "conflictDomains"):
            value = task.get(field)
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item for item in value
            ):
                raise ValidationError(f"tasks.{task_id}.{field} must be a string list")
        if task_id in task["dependsOn"]:
            raise ValidationError(f"tasks.{task_id} cannot depend on itself")
        for dependency in task["dependsOn"]:
            if dependency not in tasks:
                raise ValidationError(
                    f"tasks.{task_id}.dependsOn references unknown task {dependency}"
                )
        for field in ("branch", "worktree", "headSha", "pr", "blocker"):
            _require_string(task.get(field), f"tasks.{task_id}.{field}", nullable=True)
        for field in ("review", "verification"):
            value = task.get(field)
            if value is not None and not isinstance(value, dict):
                raise ValidationError(f"tasks.{task_id}.{field} must be an object or null")
        freshness = task.get("freshness")
        if freshness is not None and not isinstance(freshness, dict):
            raise ValidationError(f"tasks.{task_id}.freshness must be an object or null")

    for field in ("waves", "monitoring", "telemetry"):
        _require_mapping(state.get(field), field)
    leases = state.get("leases")
    if leases is not None and not isinstance(leases, dict):
        raise ValidationError("leases must be an object when present")
    events = state.get("events")
    if not isinstance(events, list):
        raise ValidationError("events must be a list")
    keys = state.get("appliedIdempotencyKeys")
    if not isinstance(keys, list) or not all(isinstance(key, str) and key for key in keys):
        raise ValidationError("appliedIdempotencyKeys must be a non-empty-string list")


def copied_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy after validating the input state."""
    validate_state(state)
    return deepcopy(state)
