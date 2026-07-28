"""The single planning input: one plan file in, one governed scope out."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .domains import domains_overlap
from .engine import apply_mutation
from .errors import IllegalTransition, ValidationError
from .git import branch_exists, require_repository, resolve_ref, run_git, validate_ref_name
from .schema import (
    REVIEW_POLICIES,
    RISK_LEVELS,
    copied_state,
    detect_dependency_cycle,
    new_state,
    task_state,
)
from .store import StateStore


PLAN_KIND = "wdd_plan"
MUTABLE_TASK_FIELDS = ("title", "specPath", "risk", "dependsOn", "conflictDomains")


def read_plan(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValidationError(f"plan file does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"plan file is not valid JSON: {path}: {error}") from error
    return validate_plan(plan)


def validate_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValidationError("plan must be a JSON object")
    if plan.get("kind") != PLAN_KIND:
        raise ValidationError(f'plan requires "kind": "{PLAN_KIND}"')
    if plan.get("schemaVersion") != 1:
        raise ValidationError('plan requires "schemaVersion": 1')

    scope = plan.get("scope")
    if not isinstance(scope, dict):
        raise ValidationError("plan.scope must be an object")
    scope_id = scope.get("id")
    if not isinstance(scope_id, str) or not scope_id:
        raise ValidationError("plan.scope.id must be a non-empty string")
    base_ref = scope.get("baseRef")
    if base_ref is not None:
        validate_ref_name(base_ref, what="plan.scope.baseRef")
    policy = scope.get("reviewPolicy", "risk_based")
    if policy not in REVIEW_POLICIES:
        raise ValidationError(f"plan.scope.reviewPolicy must be one of {sorted(REVIEW_POLICIES)}")
    limit = scope.get("maxConcurrent")
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        raise ValidationError("plan.scope.maxConcurrent must be a positive integer or null")
    every = scope.get("reconcileEveryNMerges", 3)
    if every is not None and (not isinstance(every, int) or every < 1):
        raise ValidationError("plan.scope.reconcileEveryNMerges must be a positive integer or null")

    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValidationError("plan.tasks must be a non-empty list")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for entry in tasks:
        if not isinstance(entry, dict):
            raise ValidationError("each plan task must be an object")
        task_id = entry.get("id")
        if not isinstance(task_id, str) or not task_id:
            raise ValidationError("each plan task requires a non-empty id")
        if task_id in seen:
            raise ValidationError(f"duplicate task id in plan: {task_id}")
        seen.add(task_id)
        risk = entry.get("risk", "normal")
        if risk not in RISK_LEVELS:
            raise ValidationError(f"task {task_id} risk must be 'normal' or 'high'")
        for field in ("dependsOn", "conflictDomains"):
            value = entry.get(field, [])
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item for item in value
            ):
                raise ValidationError(f"task {task_id} {field} must be a string list")
        title = entry.get("title", task_id)
        if not isinstance(title, str) or not title:
            raise ValidationError(f"task {task_id} title must be a non-empty string")
        spec_path = entry.get("specPath", f"tasks/{task_id}.md")
        if not isinstance(spec_path, str) or not spec_path:
            raise ValidationError(f"task {task_id} specPath must be a non-empty string")
        normalized.append(
            {
                "id": task_id,
                "title": title,
                "specPath": spec_path,
                "risk": risk,
                "dependsOn": list(entry.get("dependsOn", [])),
                "conflictDomains": list(entry.get("conflictDomains", [])),
            }
        )

    for entry in normalized:
        for dependency in entry["dependsOn"]:
            if dependency not in seen:
                raise ValidationError(
                    f"task {entry['id']} depends on unknown task {dependency}"
                )
        if entry["id"] in entry["dependsOn"]:
            raise ValidationError(f"task {entry['id']} cannot depend on itself")
    detect_dependency_cycle({entry["id"]: entry for entry in normalized})

    return {
        "schemaVersion": 1,
        "kind": PLAN_KIND,
        "scope": {
            "id": scope_id,
            "baseRef": base_ref,
            "maxConcurrent": limit,
            "reviewPolicy": policy,
            "reconcileEveryNMerges": every,
        },
        "tasks": normalized,
    }


def apply_config_defaults(
    plan_dict: dict[str, Any], raw_scope: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """Overlay config.json's defaults onto scope fields the plan file omitted.

    validate_plan() fills reviewPolicy/reconcileEveryNMerges with hardcoded
    literals so it stays a pure, file-local function with no config
    dependency. Config.json is the actual ratified source of truth for what
    "default" means, so cli.py calls this afterward, at apply time, passing
    the plan's raw (pre-validation) scope object: a field only gets
    overridden here when the operator's plan file never mentioned it at
    all — an explicit value in the plan always wins over config.
    """
    scope = dict(plan_dict["scope"])
    if "reviewPolicy" not in raw_scope:
        scope["reviewPolicy"] = config["review"]["policy"]
    if "reconcileEveryNMerges" not in raw_scope:
        scope["reconcileEveryNMerges"] = config["merge"]["reconcileEveryNMerges"]
    if "maxConcurrent" not in raw_scope:
        scope["maxConcurrent"] = config["concurrency"]["maxConcurrent"]
    return {**plan_dict, "scope": scope}


def apply_risk_rules(plan_dict: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Derive each task's risk from config riskRules; upward only.

    Overlap semantics come from domains.py deliberately: where a rule and a
    domain might cover the same file, the answer is "they overlap" — a task
    wrongly reviewed costs a review, a task wrongly unreviewed costs a merge.
    """
    high_patterns = [
        rule["pattern"] for rule in config.get("riskRules", []) if rule["risk"] == "high"
    ]
    if not high_patterns:
        return plan_dict
    tasks = []
    for entry in plan_dict["tasks"]:
        derived = entry["risk"]
        if derived != "high" and any(
            domains_overlap(pattern, domain)
            for pattern in high_patterns
            for domain in entry["conflictDomains"]
        ):
            derived = "high"
        tasks.append({**entry, "risk": derived})
    return {**plan_dict, "tasks": tasks}


def state_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    scope = plan["scope"]
    state = new_state(
        scope["id"],
        base_ref=scope["baseRef"],
        max_concurrent=scope["maxConcurrent"],
        review_policy=scope["reviewPolicy"],
        reconcile_every_n_merges=scope["reconcileEveryNMerges"],
    )
    for entry in plan["tasks"]:
        state["tasks"][entry["id"]] = task_state(
            entry["id"],
            title=entry["title"],
            spec_path=entry["specPath"],
            risk=entry["risk"],
            depends_on=entry["dependsOn"],
            conflict_domains=entry["conflictDomains"],
        )
    return state


def _diff_plan(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    if state["scope"] is None:
        scope_changes = dict(plan["scope"])
        return {
            "added": sorted(entry["id"] for entry in plan["tasks"]),
            "removed": [],
            "updated": [],
            "scope": scope_changes,
        }
    planned = {entry["id"]: entry for entry in plan["tasks"]}
    existing = state["tasks"]
    added = sorted(set(planned) - set(existing))
    removed = sorted(set(existing) - set(planned))
    updated: list[dict[str, Any]] = []
    for task_id in sorted(set(planned) & set(existing)):
        changes = {
            field: planned[task_id][field]
            for field in MUTABLE_TASK_FIELDS
            if existing[task_id].get(field) != planned[task_id][field]
        }
        if changes:
            updated.append({"task": task_id, "changes": sorted(changes)})
    scope_changes = {
        key: plan["scope"][key]
        for key in ("maxConcurrent", "reviewPolicy")
        if state["scope"].get(key) != plan["scope"][key]
    }
    # An omitted baseRef means "keep the configured one", not "change to null".
    if plan["scope"]["baseRef"] and plan["scope"]["baseRef"] != state["scope"].get("baseRef"):
        scope_changes["baseRef"] = plan["scope"]["baseRef"]
    if state["reconcile"].get("everyNMerges") != plan["scope"]["reconcileEveryNMerges"]:
        scope_changes["reconcileEveryNMerges"] = plan["scope"]["reconcileEveryNMerges"]
    return {"added": added, "removed": removed, "updated": updated, "scope": scope_changes}


def _apply_plan_to_state(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    state = copied_state(state)
    planned = {entry["id"]: entry for entry in plan["tasks"]}

    if state["scope"] is None:
        # An init-created state has no scope yet; the first plan apply adopts
        # it while keeping ratification and event history.
        state["scope"] = {
            "id": plan["scope"]["id"],
            "baseRef": plan["scope"]["baseRef"],
            "maxConcurrent": plan["scope"]["maxConcurrent"],
            "reviewPolicy": plan["scope"]["reviewPolicy"],
        }
        state["reconcile"]["everyNMerges"] = plan["scope"]["reconcileEveryNMerges"]

    if plan["scope"]["id"] != state["scope"]["id"]:
        raise IllegalTransition(
            f"plan scope {plan['scope']['id']} does not match state scope {state['scope']['id']}"
        )

    for task_id in sorted(set(state["tasks"]) - set(planned)):
        if state["tasks"][task_id]["status"] != "todo":
            raise IllegalTransition(
                f"cannot remove {task_id} from the plan; it is {state['tasks'][task_id]['status']}, not todo"
            )
        del state["tasks"][task_id]

    for task_id, entry in sorted(planned.items()):
        if task_id not in state["tasks"]:
            state["tasks"][task_id] = task_state(
                task_id,
                title=entry["title"],
                spec_path=entry["specPath"],
                risk=entry["risk"],
                depends_on=entry["dependsOn"],
                conflict_domains=entry["conflictDomains"],
            )
            continue
        task = state["tasks"][task_id]
        changed = [field for field in MUTABLE_TASK_FIELDS if task.get(field) != entry[field]]
        if not changed:
            continue
        if task["status"] != "todo":
            raise IllegalTransition(
                f"cannot change {', '.join(changed)} on {task_id}; it is {task['status']}, not todo"
            )
        for field in MUTABLE_TASK_FIELDS:
            task[field] = entry[field]

    scope = plan["scope"]
    if state["scope"]["baseRef"] and scope["baseRef"] != state["scope"]["baseRef"]:
        active = [
            task_id
            for task_id, task in state["tasks"].items()
            if task["status"] not in {"todo", "cancelled"}
        ]
        if active:
            raise IllegalTransition(
                "cannot change scope.baseRef while tasks have started: "
                + ", ".join(sorted(active))
            )
    state["scope"]["baseRef"] = scope["baseRef"] or state["scope"]["baseRef"]
    state["scope"]["maxConcurrent"] = scope["maxConcurrent"]
    state["scope"]["reviewPolicy"] = scope["reviewPolicy"]
    state["reconcile"]["everyNMerges"] = scope["reconcileEveryNMerges"]
    return state


def ensure_base_branch(repo: Path | str, base_ref: str, *, from_ref: str | None) -> dict[str, Any]:
    """Create the scope base branch if it does not exist yet."""
    repo = require_repository(repo)
    validate_ref_name(base_ref, what="base ref")
    if from_ref is not None:
        validate_ref_name(from_ref, what="--from-ref")
    if branch_exists(repo, base_ref):
        return {"baseRef": base_ref, "action": "verified", "baseSha": resolve_ref(repo, base_ref)}
    start = from_ref or "HEAD"
    run_git(repo, "branch", base_ref, start)
    return {
        "baseRef": base_ref,
        "action": "created",
        "from": start,
        "baseSha": resolve_ref(repo, base_ref),
    }


def apply_plan(
    store: StateStore,
    plan: dict[str, Any],
    *,
    repo: Path | str | None = None,
    from_ref: str | None = None,
    dry_run: bool = False,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not store.exists():
        state = state_from_plan(plan)
        diff = {
            "added": sorted(state["tasks"]),
            "removed": [],
            "updated": [],
            "scope": plan["scope"],
        }
        result: dict[str, Any] = {
            "scope": plan["scope"]["id"],
            "created": True,
            "dryRun": dry_run,
            "diff": diff,
        }
        if dry_run:
            return result
        base = None
        # Creation is a check-then-act: without the lock two controllers both
        # saw no state and the loser's plan was silently discarded. The
        # existence check is repeated inside so the second one loses cleanly.
        with store.locked():
            if store.exists():
                raise IllegalTransition(
                    f"scope state already exists at {store.path}; it was created concurrently — "
                    "re-run to apply this plan as a diff"
                )
            if repo is not None and plan["scope"]["baseRef"]:
                base = ensure_base_branch(repo, plan["scope"]["baseRef"], from_ref=from_ref)
            store.write(state)
        return {**result, "revision": 0, "base": base}

    current = store.read()
    diff = _diff_plan(current, plan)
    unchanged = not (diff["added"] or diff["removed"] or diff["updated"] or diff["scope"])
    result = {
        "scope": plan["scope"]["id"],
        "created": False,
        "dryRun": dry_run,
        "diff": diff,
        "revision": current["revision"],
    }
    if dry_run or unchanged:
        return {**result, "unchanged": unchanged}

    base: dict[str, Any] | None = None

    def mutator(state: dict[str, Any]) -> dict[str, Any]:
        # Validate before creating the branch, and do both inside the store
        # lock. Every refusal below -- scope-id mismatch, editing a task that
        # has started, changing baseRef while tasks are active, a stale
        # --expected-revision -- used to fire only after the base branch had
        # already been created, leaving a ref the operator never asked for
        # that a later corrected apply would silently adopt as its base.
        nonlocal base
        updated = _apply_plan_to_state(state, plan)
        if repo is not None and plan["scope"]["baseRef"]:
            base = ensure_base_branch(repo, plan["scope"]["baseRef"], from_ref=from_ref)
        return updated

    state, duplicate = apply_mutation(
        store,
        event_type="plan.applied",
        task_id=None,
        data={"diff": diff},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )
    return {**result, "revision": state["revision"], "duplicate": duplicate, "base": base}
