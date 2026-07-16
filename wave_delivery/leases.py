"""Idempotent Git branch/worktree leases for repository-writing workers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .engine import utc_now
from .errors import IllegalTransition, RevisionConflict, ValidationError
from .git import branch_exists, require_repository, resolve_ref, run_git, worktree_at
from .schema import copied_state
from .store import StateStore


def _require_ratified(state: dict[str, Any]) -> None:
    if state["constitution"]["status"] != "ratified":
        raise IllegalTransition("leases require an explicitly ratified constitution")


def _task(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    try:
        return state["tasks"][task_id]
    except KeyError as error:
        raise ValidationError(f"unknown task: {task_id}") from error


def _default_worktree(repo: Path, scope_id: str, task_id: str) -> Path:
    return repo.parent / f"{repo.name}.wdctl-worktrees" / scope_id / task_id


def _record_event(
    state: dict[str, Any], *, event_type: str, task_id: str, idempotency_key: str
) -> dict[str, Any]:
    updated = copied_state(state)
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
    return updated


def ensure_lease(
    store: StateStore,
    *,
    repo: Path | str,
    task_id: str,
    branch: str | None,
    worktree: Path | str | None,
    base_ref: str | None,
    idempotency_key: str,
    expected_revision: int,
    dry_run: bool = False,
) -> tuple[dict[str, Any], bool]:
    repo = require_repository(repo)
    if not idempotency_key:
        raise ValidationError("idempotency key must not be empty")
    with store.locked():
        state = store.read()
        if idempotency_key in state["appliedIdempotencyKeys"]:
            lease = (state.get("leases") or {}).get(task_id) or {}
            return {
                "task": task_id,
                "branch": lease.get("branch"),
                "worktree": lease.get("worktree"),
                "revision": state["revision"],
            }, True
        if state["revision"] != expected_revision:
            raise RevisionConflict(f"expected revision {expected_revision}, found {state['revision']}")
        _require_ratified(state)
        task = _task(state, task_id)
        lease = (state.get("leases") or {}).get(task_id)
        lease_branch = branch or task.get("branch") or (lease or {}).get("branch") or f"task/{task_id}"
        lease_worktree = Path(
            worktree
            or task.get("worktree")
            or (lease or {}).get("worktree")
            or _default_worktree(repo, state["scope"]["id"], task_id)
        ).resolve()
        lease_base = base_ref or (lease or {}).get("baseRef") or "HEAD"
        base_sha = resolve_ref(repo, lease_base)
        existing = worktree_at(repo, lease_worktree)
        if existing:
            expected_branch = f"refs/heads/{lease_branch}"
            if existing.get("branch") not in {expected_branch, None}:
                raise ValidationError(
                    f"worktree {lease_worktree} is checked out on {existing.get('branch')}, not {expected_branch}"
                )
            action = "reuse"
        elif lease_worktree.exists():
            raise ValidationError(f"worktree path exists but is not managed by Git: {lease_worktree}")
        elif branch_exists(repo, lease_branch):
            action = "attach_existing_branch"
        else:
            action = "create_branch_and_worktree"
        preview = {
            "task": task_id,
            "branch": lease_branch,
            "worktree": str(lease_worktree),
            "baseRef": lease_base,
            "baseSha": base_sha,
            "action": action,
            "dryRun": dry_run,
        }
        if dry_run:
            return preview, False
        if action == "attach_existing_branch":
            run_git(repo, "worktree", "add", str(lease_worktree), lease_branch)
        elif action == "create_branch_and_worktree":
            run_git(repo, "worktree", "add", "-b", lease_branch, str(lease_worktree), lease_base)
        head_sha = resolve_ref(repo, lease_branch)
        updated = _record_event(
            state, event_type="lease.ensured", task_id=task_id, idempotency_key=idempotency_key
        )
        updated.setdefault("leases", {})[task_id] = {
            "status": "active",
            "branch": lease_branch,
            "worktree": str(lease_worktree),
            "baseRef": lease_base,
            "baseSha": base_sha,
            "headSha": head_sha,
            "acquiredAt": utc_now(),
        }
        updated["tasks"][task_id]["branch"] = lease_branch
        updated["tasks"][task_id]["worktree"] = str(lease_worktree)
        store.write(updated)
        return {**preview, "revision": updated["revision"], "headSha": head_sha}, False


def release_lease(
    store: StateStore,
    *,
    repo: Path | str,
    task_id: str,
    idempotency_key: str,
    expected_revision: int,
    keep_worktree: bool = False,
) -> tuple[dict[str, Any], bool]:
    repo = require_repository(repo)
    if not idempotency_key:
        raise ValidationError("idempotency key must not be empty")
    with store.locked():
        state = store.read()
        if idempotency_key in state["appliedIdempotencyKeys"]:
            lease = (state.get("leases") or {}).get(task_id) or {}
            return {
                "task": task_id,
                "worktree": lease.get("worktree"),
                "cleanup": lease.get("cleanup"),
                "revision": state["revision"],
            }, True
        if state["revision"] != expected_revision:
            raise RevisionConflict(f"expected revision {expected_revision}, found {state['revision']}")
        _require_ratified(state)
        _task(state, task_id)
        lease = (state.get("leases") or {}).get(task_id)
        if not isinstance(lease, dict) or lease.get("status") != "active":
            raise IllegalTransition(f"task {task_id} has no active lease")
        worktree = Path(lease["worktree"])
        if not keep_worktree:
            entry = worktree_at(repo, worktree)
            if not entry:
                raise ValidationError(f"leased worktree is not registered with Git: {worktree}")
            status = run_git(worktree, "status", "--porcelain").stdout.strip()
            if status:
                raise IllegalTransition(
                    f"refusing to remove worktree with uncommitted changes: {worktree}"
                )
            run_git(repo, "worktree", "remove", str(worktree))
            run_git(repo, "worktree", "prune")
        updated = _record_event(
            state, event_type="lease.released", task_id=task_id, idempotency_key=idempotency_key
        )
        updated_lease = deepcopy(lease)
        updated_lease["status"] = "released"
        updated_lease["releasedAt"] = utc_now()
        updated_lease["cleanup"] = "retained" if keep_worktree else "cleaned_up"
        updated.setdefault("leases", {})[task_id] = updated_lease
        if not keep_worktree:
            updated["tasks"][task_id]["worktree"] = None
        store.write(updated)
        return {
            "task": task_id,
            "worktree": str(worktree),
            "cleanup": updated_lease["cleanup"],
            "revision": updated["revision"],
        }, False
