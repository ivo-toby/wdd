"""The single planning input: one plan file in, one governed scope out."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import MERGE_MODES, MERGE_SURFACES
from .domains import domains_overlap
from .engine import apply_mutation, utc_now
from .errors import IllegalTransition, ValidationError
from .git import branch_exists, require_repository, resolve_ref, run_git, validate_ref_name
from .intake import artifact_sha256, intake_drift, resolve_within_wdd
from .schema import (
    REVIEW_POLICIES,
    RISK_LEVELS,
    copied_state,
    detect_dependency_cycle,
    intake_complete,
    new_state,
    task_state,
)
from .store import StateStore


PLAN_KIND = "wdd_plan"
MUTABLE_TASK_FIELDS = (
    "title",
    "specPath",
    "risk",
    "dependsOn",
    "conflictDomains",
    "context",
    "model",
    "reviewModel",
)


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
    merge_surface = scope.get("mergeSurface")
    if merge_surface is not None and merge_surface not in MERGE_SURFACES:
        raise ValidationError(f"plan.scope.mergeSurface must be one of {sorted(MERGE_SURFACES)}")
    merge_mode = scope.get("mergeMode")
    if merge_mode is not None and merge_mode not in MERGE_MODES:
        raise ValidationError(f"plan.scope.mergeMode must be one of {sorted(MERGE_MODES)}")

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
        context = entry.get("context", [])
        if not isinstance(context, list) or not all(
            isinstance(item, str) and item for item in context
        ):
            raise ValidationError(f"task {task_id} context must be a string list")
        for ref in context:
            path_part, _, _anchor = ref.partition("#")
            if not path_part:
                raise ValidationError(
                    f"task {task_id} context ref has no path before '#': {ref!r}"
                )
        model = entry.get("model")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ValidationError(f"task {task_id} model must be a non-empty string")
        review_model = entry.get("reviewModel")
        if review_model is not None and (
            not isinstance(review_model, str) or not review_model.strip()
        ):
            raise ValidationError(f"task {task_id} reviewModel must be a non-empty string")
        normalized.append(
            {
                "id": task_id,
                "title": title,
                "specPath": spec_path,
                "risk": risk,
                "dependsOn": list(entry.get("dependsOn", [])),
                "conflictDomains": list(entry.get("conflictDomains", [])),
                "context": list(context),
                "model": model,
                "reviewModel": review_model,
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

    normalized_scope: dict[str, Any] = {
        "id": scope_id,
        "baseRef": base_ref,
        "maxConcurrent": limit,
        "reviewPolicy": policy,
        "reconcileEveryNMerges": every,
    }
    # Omitted means absent from the normalized scope (and later, state), not
    # null: merge_settings() distinguishes "no override" from "override to
    # a falsy value" -- neither field has one, but keeping the same absent-
    # key convention as everything else here avoids a special case later.
    if merge_surface is not None:
        normalized_scope["mergeSurface"] = merge_surface
    if merge_mode is not None:
        normalized_scope["mergeMode"] = merge_mode

    return {
        "schemaVersion": 1,
        "kind": PLAN_KIND,
        "scope": normalized_scope,
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


def apply_risk_rules(
    plan_dict: dict[str, Any], config: dict[str, Any], state: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Derive each task's risk from config riskRules; upward only.

    Overlap semantics come from domains.py deliberately: where a rule and a
    domain might cover the same file, the answer is "they overlap" — a task
    wrongly reviewed costs a review, a task wrongly unreviewed costs a merge.

    A newly ratified riskRule can match a task that already left "todo".
    Re-deriving its risk would make `_apply_plan_to_state`'s immutability
    check refuse the (identical, otherwise-unrelated) re-apply of the plan
    file forever after, since risk is a MUTABLE_TASK_FIELD only while a task
    is still todo. When `state` is given, tasks it already tracks with a
    non-todo status keep their stored risk instead of the freshly derived
    one — derivation still applies in full to todo tasks and to tasks new
    to the plan.
    """
    high_patterns = [
        rule["pattern"] for rule in config.get("riskRules", []) if rule["risk"] == "high"
    ]
    if not high_patterns:
        return plan_dict
    state_tasks = state["tasks"] if state is not None else {}
    tasks = []
    for entry in plan_dict["tasks"]:
        existing = state_tasks.get(entry["id"])
        if existing is not None and existing["status"] != "todo":
            tasks.append({**entry, "risk": existing["risk"]})
            continue
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
    # new_state() has no mergeSurface/mergeMode parameters (its signature stays
    # stable); carry them here from the plan's normalized scope, same absent-
    # key-when-omitted convention as validate_plan and _apply_plan_to_state.
    for field in ("mergeSurface", "mergeMode"):
        if field in scope:
            state["scope"][field] = scope[field]
    for entry in plan["tasks"]:
        state["tasks"][entry["id"]] = task_state(
            entry["id"],
            title=entry["title"],
            spec_path=entry["specPath"],
            risk=entry["risk"],
            depends_on=entry["dependsOn"],
            conflict_domains=entry["conflictDomains"],
            context=entry.get("context"),
            model=entry.get("model"),
            review_model=entry.get("reviewModel"),
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
    # mergeSurface/mergeMode follow the baseRef "omitted means keep" pattern,
    # not maxConcurrent's "always overwrite" one: a plan that never mentions
    # the field leaves whatever is already stored alone rather than clearing
    # it, since the field is only present in the plan scope at all when the
    # operator explicitly set it (validate_plan's absent-key convention).
    for field in ("mergeSurface", "mergeMode"):
        if field in plan["scope"] and plan["scope"][field] != state["scope"].get(field):
            scope_changes[field] = plan["scope"][field]
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
        for field in ("mergeSurface", "mergeMode"):
            if field in plan["scope"]:
                state["scope"][field] = plan["scope"][field]

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
                context=entry.get("context"),
                model=entry.get("model"),
                review_model=entry.get("reviewModel"),
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
    # Adoptable like reviewPolicy (a scope change, allowed any time -- these
    # only gate future actions) but, like baseRef, omission means "leave the
    # stored value alone" rather than "clear it": a re-apply of a plan file
    # that never mentions the field must not silently drop an override that
    # a previous apply recorded.
    for field in ("mergeSurface", "mergeMode"):
        if field in scope:
            state["scope"][field] = scope[field]
    return state


def _validate_context_refs(tasks: list[dict[str, Any]], wdd_dir: Path) -> None:
    """Every task `context` ref must resolve to a regular file inside `.wdd/`.

    Ref syntax is `<path>[#anchor]` (spec Sec3); the anchor is advisory
    reading guidance, never resolved mechanically. Mirrors intake.py's
    research-artifact containment doctrine (`resolve_within_wdd`).
    """
    for entry in tasks:
        for ref in entry.get("context") or []:
            path_part = ref.split("#", 1)[0]
            resolved = resolve_within_wdd(wdd_dir, path_part)
            if not resolved.exists() or not resolved.is_file():
                raise ValidationError(
                    f"task {entry['id']} context ref does not resolve to a file "
                    f"inside .wdd/: {ref!r}"
                )


def plan_composite(plan_dict: dict[str, Any], wdd_dir: Path | str) -> str:
    """SHA-256 composite binding a plan approval to the bytes it was shown.

    Covers the canonical normalized plan (key-sorted JSON, so task/field
    order never changes the digest) plus every task's brief file and every
    `context`-ref file, combined as sorted (path, sha256) pairs so duplicate
    paths across tasks are hashed once and iteration order never matters.
    Task 4 reconstructs a comparable plan dict from applied state (mirroring
    `_diff_plan`'s reconstruction) and recomputes this same function to
    detect post-apply drift -- the reason every field this composite must
    see (context/model/reviewModel) is also a MUTABLE_TASK_FIELD persisted
    into task state.
    """
    wdd_dir = Path(wdd_dir)
    # sort_keys=True only orders each dict's keys, not the tasks list itself;
    # _apply_plan_to_state inserts tasks id-sorted regardless of plan-file
    # order, so the composite must normalize task order the same way or two
    # byte-identical plans (tasks merely listed differently) would hash
    # differently -- a false-positive "changed plan" forever.
    normalized = {**plan_dict, "tasks": sorted(plan_dict["tasks"], key=lambda entry: entry["id"])}
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    paths: set[str] = set()
    for entry in plan_dict["tasks"]:
        paths.add(entry["specPath"])
        for ref in entry.get("context") or []:
            paths.add(ref.split("#", 1)[0])

    def _hash_or_refuse(path: str) -> str:
        resolved = wdd_dir / path
        try:
            return artifact_sha256(resolved)
        except FileNotFoundError as error:
            raise ValidationError(
                f"plan apply --approved-by requires every brief and context file to exist: "
                f"{path} does not exist at {resolved}"
            ) from error

    pairs = sorted((path, _hash_or_refuse(path)) for path in paths)
    digest = hashlib.sha256()
    digest.update(canonical.encode("utf-8"))
    for path, file_hash in pairs:
        digest.update(path.encode("utf-8"))
        digest.update(file_hash.encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


def _is_legacy_intake(state: dict[str, Any]) -> bool:
    return (state.get("intake") or {}).get("legacy") is True


def _reconstruct_plan_from_state(state: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a normalized plan dict from applied state alone (Task 4).

    `require_fresh_intake` has no plan file to re-read at execute time -- the
    only source of truth post-apply is the state a prior `plan apply` wrote.
    This mirrors `_diff_plan`'s own reconstruction of "what the plan looked
    like" from state: scope fields `_apply_plan_to_state` persists
    (`mergeSurface`/`mergeMode` included only when the key is present in
    `state["scope"]`, the same "omission means keep, not clear" convention
    `_diff_plan`/`_apply_plan_to_state` use elsewhere), and per task exactly
    `MUTABLE_TASK_FIELDS` -- `_apply_plan_to_state`'s own mutable-field list,
    also what `_diff_plan` diffs and what `plan_composite` folds into its
    digest, so nothing this composite must see can go stale by omission here.
    Task iteration/list order doesn't matter: `plan_composite` re-sorts tasks
    by id internally before hashing (matching `_apply_plan_to_state`'s
    id-sorted insertion), so a byte-identical scope produces a byte-identical
    composite regardless of dict order below.
    """
    scope = state["scope"]
    normalized_scope: dict[str, Any] = {
        "id": scope["id"],
        "baseRef": scope.get("baseRef"),
        "maxConcurrent": scope.get("maxConcurrent"),
        "reviewPolicy": scope.get("reviewPolicy"),
        "reconcileEveryNMerges": state["reconcile"].get("everyNMerges"),
    }
    for field in ("mergeSurface", "mergeMode"):
        if field in scope:
            normalized_scope[field] = scope[field]
    tasks = []
    for task_id, task in state["tasks"].items():
        entry: dict[str, Any] = {"id": task_id}
        for field in MUTABLE_TASK_FIELDS:
            entry[field] = task.get(field)
        tasks.append(entry)
    return {
        "schemaVersion": 1,
        "kind": PLAN_KIND,
        "scope": normalized_scope,
        "tasks": tasks,
    }


def intake_gate_status(
    state: dict[str, Any], wdd_dir: Path | str
) -> tuple[str, dict[str, Any]] | None:
    """(code, detail) for the first drift `require_fresh_intake` would refuse
    on, or None -- the non-raising counterpart `next` uses to build a
    blocker, mirroring `config.governance_drift`'s relationship to
    `require_fresh_governance`.

    None covers the no-op cases: legacy scopes (wholesale exempt, spec Sec7)
    and no scope yet (nothing applied to gate). Checked in ladder order: an
    intake rung drift (spec Sec1's three-gate doctrine, re-hashed here via
    `intake_drift`) is reported before a plan-composite drift, since a stale
    intake rung is the more upstream problem and re-approving it will also
    require the plan re-stamp that would otherwise show up here separately.
    A non-legacy scope with no composite approval at all (missing, or present
    without a `sha256`) is treated as plan drift too: a non-legacy `plan
    apply` always requires `--approved-by` on a nonempty diff (Task 3), so a
    non-legacy applied scope with no composite approval could only mean the
    approval was never stamped -- there is no legitimate "never approved"
    steady state to exempt. A brief/context file deleted since approval
    makes `plan_composite` refuse (`_hash_or_refuse`); that refusal is caught
    here and reported as drift too, rather than propagating as an unrelated
    ValidationError out of a read path like `next`.
    """
    if _is_legacy_intake(state) or state.get("scope") is None:
        return None
    wdd_dir = Path(wdd_dir)
    drift = intake_drift(state, wdd_dir)
    if drift is not None:
        return "intake_drift", drift
    approval = state["scope"].get("approval")
    if not isinstance(approval, dict) or not approval.get("sha256"):
        return "plan_drift", {"recorded": None, "actual": "never composite-approved"}
    try:
        recomputed = plan_composite(_reconstruct_plan_from_state(state), wdd_dir)
    except ValidationError as error:
        return "plan_drift", {"recorded": approval["sha256"], "actual": f"missing file: {error}"}
    if approval["sha256"] != recomputed:
        return "plan_drift", {"recorded": approval["sha256"], "actual": recomputed}
    return None


def require_fresh_intake(state: dict[str, Any], wdd_dir: Path | str) -> None:
    """Extend the execution gate (`config.require_fresh_governance`'s
    sibling, spec Sec1 "after apply" enforcement) with intake/plan drift.

    Wired at the same CLI chokepoint, for every governed verb: a non-legacy
    scope whose recorded intake fingerprints or plan-approval composite no
    longer match the current bytes refuses admission, merges, and every
    other governed verb -- not just planning. Intake verbs and `plan apply
    --approved-by` are deliberately NOT governed verbs (cli.py's
    GOVERNED_VERBS): they are the remedy this gate demands, and must stay
    legal precisely when this raises.
    """
    gate = intake_gate_status(state, wdd_dir)
    if gate is None:
        return
    code, detail = gate
    if code == "intake_drift":
        raise IllegalTransition(
            f"intake drift: intake {detail['rung']} has drifted since approval "
            f"(recorded {detail['recorded']}, actual {detail['actual']}); re-run "
            f"'wddctl intake {detail['rung']} ...' to re-approve, then "
            "'wddctl plan apply --approved-by NAME' to re-stamp the plan before "
            "resuming execution"
        )
    if detail.get("recorded") is None:
        raise IllegalTransition(
            "plan drift: this scope's plan was never composite-approved; run "
            "'wddctl plan apply --approved-by NAME' to stamp the currently applied plan"
        )
    raise IllegalTransition(
        f"plan drift: recorded plan approval {detail['recorded']} no longer matches "
        f"the applied plan's current bytes ({detail['actual']}); a brief or context "
        "file changed since approval. Run 'wddctl plan apply --approved-by NAME' "
        "(an unchanged plan file is a pure re-stamp) to re-approve before resuming "
        "execution"
    )


def _stamp_approval(
    state: dict[str, Any], approved_by: str | None, composite: str | None = None
) -> dict[str, Any]:
    if approved_by:
        approval = {"by": approved_by, "at": utc_now()}
        if composite is not None:
            approval["sha256"] = composite
        state["scope"]["approval"] = approval
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
    approved_by: str | None = None,
) -> dict[str, Any]:
    """Apply a validated, overlaid plan dict to the scope's state.

    The legacy no-state bootstrap (creating state.json out of thin air) is
    gone -- Sec1/Sec7 of the front-half spec: `wddctl init` is the only way
    state comes to exist. Every apply onto a non-legacy scope also refuses
    while the intake ladder is incomplete or drifted, and a nonempty diff
    requires `--approved-by` (the plan-approval composite doctrine, Sec3).
    Legacy scopes (`intake.legacy`) are wholesale exempt from all three: they
    have no approved-bytes baseline to gate against, so their apply behavior
    stays bit-for-bit what it was before schema v5.
    """
    if not store.exists():
        raise ValidationError(
            f"no scope state at {store.path}; run 'wddctl init --repo .' first"
        )

    wdd_dir = store.path.parent
    current = store.read()
    legacy = _is_legacy_intake(current)

    _validate_context_refs(plan["tasks"], wdd_dir)

    if not legacy:
        if not intake_complete(current):
            raise IllegalTransition(
                "plan apply refuses: the intake ladder is incomplete; finish "
                "'wddctl intake spec/research/design' before applying a plan"
            )
        drift = intake_drift(current, wdd_dir)
        if drift is not None:
            raise IllegalTransition(
                f"plan apply refuses: intake {drift['rung']} has drifted "
                f"(recorded {drift['recorded']}, actual {drift['actual']}); re-run "
                f"'wddctl intake {drift['rung']} ...' to re-approve before applying"
            )

    diff = _diff_plan(current, plan)
    unchanged = not (diff["added"] or diff["removed"] or diff["updated"] or diff["scope"])
    created = current["scope"] is None

    if not legacy and not unchanged and not approved_by:
        raise ValidationError(
            "plan apply refuses: this plan changes the scope; re-run with "
            "--approved-by NAME once the user has reviewed the diff"
        )

    result = {
        "scope": plan["scope"]["id"],
        "created": created,
        "dryRun": dry_run,
        "diff": diff,
        "revision": current["revision"],
    }

    # Handle dry-run: echo approvedBy but don't write
    if dry_run:
        if approved_by:
            result["approvedBy"] = approved_by
        return {**result, "unchanged": unchanged}

    # Handle unchanged plan without approval: return early
    if unchanged and not approved_by:
        return {**result, "unchanged": unchanged}

    # Handle unchanged plan with approval: stamp approval via apply_mutation
    if unchanged and approved_by:
        def approval_mutator(state: dict[str, Any]) -> dict[str, Any]:
            state_copy = copied_state(state)
            # The composite must hash the EFFECTIVE (post-apply) plan, not the
            # raw submitted one: baseRef/mergeSurface/mergeMode have
            # "omission means keep" semantics, so a legitimate re-apply that
            # omits them would otherwise hash "absent" here while the gate's
            # _reconstruct_plan_from_state (what require_fresh_intake compares
            # against) sees the real, kept value -- an unrecoverable false
            # plan_drift no re-apply of the same minimal file could heal.
            # Reconstructing from state_copy after the (no-op, diff-empty)
            # apply keeps both sides symmetric by construction.
            composite = (
                None if legacy else plan_composite(_reconstruct_plan_from_state(state_copy), wdd_dir)
            )
            _stamp_approval(state_copy, approved_by, composite)
            return state_copy

        state, duplicate = apply_mutation(
            store,
            event_type="plan.approved",
            task_id=None,
            data={"by": approved_by},
            idempotency_key=idempotency_key,
            expected_revision=expected_revision,
            mutator=approval_mutator,
        )
        result_dict = {**result, "revision": state["revision"], "unchanged": True}
        if not duplicate:
            result_dict["approvedBy"] = approved_by
        return result_dict

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
        # Same symmetry as the unchanged-plan re-stamp path above: hash the
        # EFFECTIVE state _apply_plan_to_state just produced (via the same
        # _reconstruct_plan_from_state the gate uses), not the raw plan dict
        # -- covers the scope-adoption path too (scope adopted from null),
        # since `updated` already reflects the post-adoption scope.
        composite = (
            None
            if legacy or not approved_by
            else plan_composite(_reconstruct_plan_from_state(updated), wdd_dir)
        )
        _stamp_approval(updated, approved_by, composite)
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
    result_dict = {**result, "revision": state["revision"], "duplicate": duplicate, "base": base}
    if approved_by and not duplicate:
        result_dict["approvedBy"] = approved_by
    return result_dict
