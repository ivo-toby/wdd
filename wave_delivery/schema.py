"""Controller state model with no runtime dependencies."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .errors import ValidationError


SCHEMA_VERSION = 5
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
REVIEW_POLICIES = {"always", "risk_based", "none"}
RISK_LEVELS = {"normal", "high"}
# Duplicated from config.py (not imported) to avoid a circular import: config.py
# already imports REVIEW_POLICIES/RISK_LEVELS from this module.
MERGE_SURFACES = {"pr", "local"}
MERGE_MODES = {"controller", "human"}


def task_state(
    task_id: str,
    *,
    title: str | None = None,
    depends_on: list[str] | None = None,
    conflict_domains: list[str] | None = None,
    spec_path: str | None = None,
    risk: str = "normal",
    context: list[str] | None = None,
    model: str | None = None,
    review_model: str | None = None,
) -> dict[str, Any]:
    if risk not in RISK_LEVELS:
        raise ValidationError(f"task risk must be one of {sorted(RISK_LEVELS)}")
    return {
        "id": task_id,
        "title": title or task_id,
        "specPath": spec_path or f"tasks/{task_id}.md",
        "status": "todo",
        "risk": risk,
        "dependsOn": list(depends_on or []),
        "conflictDomains": list(conflict_domains or []),
        # Handover fields (front-half spec Sec3): validated at plan apply
        # (path/format for context, non-empty for model/reviewModel) and
        # persisted here so the plan-approval composite and Task 4's drift
        # gate can be reconstructed from state alone.
        "context": list(context or []),
        "model": model,
        "reviewModel": review_model,
        "branch": None,
        "worktree": None,
        "headSha": None,
        "pr": None,
        "review": None,
        "verification": None,
        "freshness": None,
        "merge": None,
        "blocker": None,
    }


def new_state(
    scope_id: str,
    *,
    base_ref: str | None = None,
    max_concurrent: int | None = None,
    review_policy: str = "risk_based",
    reconcile_every_n_merges: int | None = 3,
) -> dict[str, Any]:
    if not scope_id:
        raise ValidationError("scope id must not be empty")
    if review_policy not in REVIEW_POLICIES:
        raise ValidationError(f"review policy must be one of {sorted(REVIEW_POLICIES)}")
    if max_concurrent is not None and (not isinstance(max_concurrent, int) or max_concurrent < 1):
        raise ValidationError("maxConcurrent must be a positive integer or null")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "revision": 0,
        "scope": {
            "id": scope_id,
            "baseRef": base_ref,
            "maxConcurrent": max_concurrent,
            "reviewPolicy": review_policy,
        },
        "constitution": {"status": "draft", "ratification": None},
        "tasks": {},
        "reconcile": {
            "everyNMerges": reconcile_every_n_merges,
            "mergesSinceCheckpoint": 0,
            "lastCheckpointAt": None,
            "pendingNotes": [],
        },
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
        "intake": {},
    }


def new_setup_state() -> dict[str, Any]:
    """State for an initialized repository that has no scope yet.

    Created by `wddctl init` so `next` can drive setup; `plan apply` adopts
    the scope into this state later.
    """
    state = new_state("__setup__")
    state["scope"] = None
    return state


def derived_phase(state: dict[str, Any]) -> str:
    """Phase is computed, never stored: setup until a scope exists."""
    if state.get("scope") is None or state["constitution"]["status"] != "ratified":
        return "setup"

    # When scope present + ratified, check if all tasks are terminal.
    tasks = state.get("tasks", {})
    if not tasks:
        # Empty task list: stay in execute (plan validation forbids this anyway).
        return "execute"

    # Check if all tasks are in terminal states (done or cancelled).
    terminal_statuses = {"done", "cancelled"}
    if all(task.get("status") in terminal_statuses for task in tasks.values()):
        # All tasks terminal: finalize phase, or delivered if marker exists.
        finalize_section = state.get("finalize", {})
        if finalize_section.get("delivered"):
            return "delivered"
        return "finalize"

    # At least one task is not terminal.
    return "execute"


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
        found = state.get("schemaVersion")
        hint = (
            " run 'wddctl --state <path> migrate --dry-run' to convert it"
            if found in {2, 3, 4}
            else ""
        )
        raise ValidationError(
            f"unsupported schemaVersion {found!r}; expected {SCHEMA_VERSION}.{hint}"
        )
    revision = state.get("revision")
    if not isinstance(revision, int) or revision < 0:
        raise ValidationError("revision must be a non-negative integer")

    scope = state.get("scope")
    if scope is None:
        if state.get("tasks"):
            raise ValidationError("scope must exist before tasks do; run 'wddctl plan apply'")
    else:
        scope = _require_mapping(scope, "scope")
        _require_string(scope.get("id"), "scope.id")
        _require_string(scope.get("baseRef"), "scope.baseRef", nullable=True)
        if scope.get("reviewPolicy") not in REVIEW_POLICIES:
            raise ValidationError(f"scope.reviewPolicy must be one of {sorted(REVIEW_POLICIES)}")
        max_concurrent = scope.get("maxConcurrent")
        if max_concurrent is not None and (
            not isinstance(max_concurrent, int) or max_concurrent < 1
        ):
            raise ValidationError("scope.maxConcurrent must be a positive integer or null")
        approval = scope.get("approval")
        if approval is not None:
            approval = _require_mapping(approval, "scope.approval")
            _require_string(approval.get("by"), "scope.approval.by")
            _require_string(approval.get("at"), "scope.approval.at")
        merge_surface = scope.get("mergeSurface")
        if merge_surface is not None and merge_surface not in MERGE_SURFACES:
            raise ValidationError(f"scope.mergeSurface must be one of {sorted(MERGE_SURFACES)}")
        merge_mode = scope.get("mergeMode")
        if merge_mode is not None and merge_mode not in MERGE_MODES:
            raise ValidationError(f"scope.mergeMode must be one of {sorted(MERGE_MODES)}")

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
        _require_string(task.get("title"), f"tasks.{task_id}.title")
        if task.get("status") not in TASK_STATUSES:
            raise ValidationError(f"tasks.{task_id}.status is invalid")
        if task.get("risk") not in RISK_LEVELS:
            raise ValidationError(f"tasks.{task_id}.risk must be 'normal' or 'high'")
        for field in ("dependsOn", "conflictDomains", "context"):
            value = task.get(field)
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item for item in value
            ):
                raise ValidationError(f"tasks.{task_id}.{field} must be a string list")
        for field in ("model", "reviewModel"):
            _require_string(task.get(field), f"tasks.{task_id}.{field}", nullable=True)
        if task_id in task["dependsOn"]:
            raise ValidationError(f"tasks.{task_id} cannot depend on itself")
        for dependency in task["dependsOn"]:
            if dependency not in tasks:
                raise ValidationError(
                    f"tasks.{task_id}.dependsOn references unknown task {dependency}"
                )
        for field in ("branch", "worktree", "headSha", "pr", "blocker"):
            _require_string(task.get(field), f"tasks.{task_id}.{field}", nullable=True)
        for field in ("review", "verification", "merge"):
            value = task.get(field)
            if value is not None and not isinstance(value, dict):
                raise ValidationError(f"tasks.{task_id}.{field} must be an object or null")
        freshness = task.get("freshness")
        if freshness is not None and not isinstance(freshness, dict):
            raise ValidationError(f"tasks.{task_id}.freshness must be an object or null")

    detect_dependency_cycle(tasks)

    reconcile = _require_mapping(state.get("reconcile"), "reconcile")
    every = reconcile.get("everyNMerges")
    if every is not None and (not isinstance(every, int) or every < 1):
        raise ValidationError("reconcile.everyNMerges must be a positive integer or null")
    if not isinstance(reconcile.get("mergesSinceCheckpoint"), int):
        raise ValidationError("reconcile.mergesSinceCheckpoint must be an integer")
    if not isinstance(reconcile.get("pendingNotes"), list):
        raise ValidationError("reconcile.pendingNotes must be a list")

    for field in ("monitoring", "telemetry"):
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

    # Validate optional finalize section.
    finalize = state.get("finalize")
    if finalize is not None:
        finalize = _require_mapping(finalize, "finalize")
        # Optional keys: review, verification, handoff (each must be dict or absent).
        for field in ("review", "verification", "handoff"):
            value = finalize.get(field)
            if value is not None and not isinstance(value, dict):
                raise ValidationError(f"finalize.{field} must be an object or null")
        # Optional key: delivered (dict with non-empty-string at, by, headSha when present).
        delivered = finalize.get("delivered")
        if delivered is not None:
            delivered = _require_mapping(delivered, "finalize.delivered")
            for field in ("at", "by", "headSha"):
                _require_string(delivered.get(field), f"finalize.delivered.{field}")

    _validate_intake(_require_mapping(state.get("intake"), "intake"))


_INTAKE_RECORD_KEYS = {"spec", "research", "design"}


def _validate_intake(intake: dict[str, Any]) -> None:
    """Validate the required `intake` section (schema v5).

    Valid shapes: `{"legacy": True}` (migration-only exemption, see
    migration.py) or any subset of `{spec, research, design}` records, each
    bound to the artifact bytes that were approved (governance_fingerprint
    idiom).
    """
    if "legacy" in intake:
        if intake.get("legacy") is not True or intake.keys() != {"legacy"}:
            raise ValidationError("intake.legacy must be the sole key, set to true")
        return

    unknown = set(intake) - _INTAKE_RECORD_KEYS
    if unknown:
        raise ValidationError(f"intake has unknown keys: {sorted(unknown)}")

    spec = intake.get("spec")
    if spec is not None:
        spec = _require_mapping(spec, "intake.spec")
        _require_string(spec.get("by"), "intake.spec.by")
        _require_string(spec.get("at"), "intake.spec.at")
        _require_string(spec.get("sha256"), "intake.spec.sha256")
        criteria = spec.get("criteria")
        if not isinstance(criteria, int) or isinstance(criteria, bool) or criteria < 1:
            raise ValidationError("intake.spec.criteria must be a positive integer")

    research = intake.get("research")
    if research is not None:
        research = _require_mapping(research, "intake.research")
        _require_string(research.get("by"), "intake.research.by")
        _require_string(research.get("at"), "intake.research.at")
        done = research.get("done")
        skipped = research.get("skipped")
        if (done is True) == (skipped is True):
            raise ValidationError(
                "intake.research must be exactly one of done=true or skipped=true"
            )
        if done is True:
            artifacts = research.get("artifacts")
            if not isinstance(artifacts, list):
                raise ValidationError("intake.research.artifacts must be a list")
            for artifact in artifacts:
                artifact = _require_mapping(artifact, "intake.research.artifacts[]")
                _require_string(artifact.get("path"), "intake.research.artifacts[].path")
                _require_string(artifact.get("sha256"), "intake.research.artifacts[].sha256")
        else:
            _require_string(research.get("reason"), "intake.research.reason")

    design = intake.get("design")
    if design is not None:
        design = _require_mapping(design, "intake.design")
        _require_string(design.get("by"), "intake.design.by")
        _require_string(design.get("at"), "intake.design.at")
        _require_string(design.get("sha256"), "intake.design.sha256")
        _require_string(design.get("deliverableCommand"), "intake.design.deliverableCommand")


def intake_complete(state: dict[str, Any]) -> bool:
    """True once the ladder is done: legacy scopes are exempt wholesale."""
    intake = state.get("intake") or {}
    if intake.get("legacy") is True:
        return True
    return all(intake.get(key) is not None for key in ("spec", "research", "design"))


def detect_dependency_cycle(tasks: dict[str, Any]) -> None:
    """Raise if the dependency graph is not acyclic."""
    unvisited, visiting, done = 0, 1, 2
    marks: dict[str, int] = {task_id: unvisited for task_id in tasks}

    def walk(task_id: str, trail: list[str]) -> None:
        if marks[task_id] == done:
            return
        if marks[task_id] == visiting:
            cycle = " -> ".join([*trail[trail.index(task_id):], task_id])
            raise ValidationError(f"dependency cycle: {cycle}")
        marks[task_id] = visiting
        for dependency in tasks[task_id].get("dependsOn", []):
            if dependency in tasks:
                walk(dependency, [*trail, task_id])
        marks[task_id] = done

    for task_id in sorted(tasks):
        walk(task_id, [])


def copied_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy after validating the input state."""
    validate_state(state)
    return deepcopy(state)
