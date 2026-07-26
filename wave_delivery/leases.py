"""Task dispatch: admission, isolated worktree, and start recorded as one step."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .engine import admission_blocker, apply_event, apply_mutation, transition, utc_now
from .errors import IllegalTransition, ValidationError
from .git import (
    ensure_worktree,
    require_repository,
    resolve_ref,
    run_git,
    task_worktree_path,
    worktree_at,
)
from .schema import copied_state
from .store import StateStore


def _task(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    try:
        return state["tasks"][task_id]
    except KeyError as error:
        raise ValidationError(f"unknown task: {task_id}") from error


def start_task(
    store: StateStore,
    *,
    repo: Path | str,
    task_id: str,
    branch: str | None = None,
    worktree: Path | str | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Admit a task, give it an isolated worktree, and mark it in progress.

    Admission is enforced here and again inside the ``task.started`` transition,
    so a caller that bypasses ``wddctl next`` still cannot start two tasks that
    share a conflict domain.
    """
    repo = require_repository(repo)
    outcome: dict[str, Any] = {}

    def mutator(state: dict[str, Any]) -> dict[str, Any]:
        task = _task(state, task_id)
        if task["status"] != "todo":
            raise IllegalTransition(
                f"task {task_id} is {task['status']}, not todo; nothing to start"
            )
        blocker = admission_blocker(state, task_id)
        if blocker is not None:
            raise IllegalTransition(
                f"task {task_id} is not admissible yet: {blocker['code']} "
                f"({', '.join(blocker.get('domains') or blocker.get('dependsOn') or [str(blocker.get('limit'))])})"
            )
        base_ref = state["scope"].get("baseRef")
        if not base_ref:
            raise IllegalTransition("this scope has no configured base ref")
        task_branch = branch or task.get("branch") or f"task/{task_id}"
        task_worktree = Path(
            worktree or task.get("worktree") or task_worktree_path(repo, state["scope"]["id"], task_id)
        ).resolve()
        action = ensure_worktree(repo, task_worktree, task_branch, base_ref=base_ref)
        head_sha = resolve_ref(repo, task_branch)

        updated = transition(state, "task.started", task_id, {})
        updated["tasks"][task_id]["branch"] = task_branch
        updated["tasks"][task_id]["worktree"] = str(task_worktree)
        updated.setdefault("leases", {})[task_id] = {
            "status": "active",
            "branch": task_branch,
            "worktree": str(task_worktree),
            "baseRef": base_ref,
            "baseSha": resolve_ref(repo, base_ref),
            "headSha": head_sha,
            "acquiredAt": utc_now(),
        }
        outcome.update(
            {
                "task": task_id,
                "action": action,
                "branch": task_branch,
                "worktree": str(task_worktree),
                "baseRef": base_ref,
                "headSha": head_sha,
                "specPath": updated["tasks"][task_id]["specPath"],
            }
        )
        return updated

    state, duplicate = apply_mutation(
        store,
        event_type="task.started",
        task_id=task_id,
        data={},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )
    if duplicate:
        task = state["tasks"][task_id]
        return {
            "task": task_id,
            "action": "duplicate",
            "branch": task.get("branch"),
            "worktree": task.get("worktree"),
            "revision": state["revision"],
        }, True
    return {**outcome, "revision": state["revision"]}, False


def submit_task(
    store: StateStore,
    *,
    repo: Path | str,
    task_id: str,
    pr: str | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Record a task's deliverable, reading the head SHA from its branch."""
    repo = require_repository(repo)
    state = store.read()
    task = _task(state, task_id)
    branch = task.get("branch")
    if not branch:
        raise IllegalTransition(f"task {task_id} has no branch; run 'wddctl start' first")
    worktree = task.get("worktree")
    if worktree and Path(worktree).exists():
        dirty = run_git(worktree, "status", "--porcelain").stdout.strip()
        if dirty:
            raise IllegalTransition(
                f"task {task_id} has uncommitted changes in {worktree}; commit them before submitting"
            )
    head_sha = resolve_ref(repo, branch)
    base_ref = state["scope"].get("baseRef")
    if base_ref and head_sha == resolve_ref(repo, base_ref):
        raise IllegalTransition(
            f"task {task_id} has no commits of its own on {branch}; there is nothing to submit"
        )
    reference = pr or f"branch:{branch}@{head_sha[:12]}"
    event = "task.pr_recorded" if task.get("headSha") is None else "task.head_updated"
    data = {"pr": reference, "headSha": head_sha} if event == "task.pr_recorded" else {"headSha": head_sha}
    state, duplicate = apply_event(
        store,
        event_type=event,
        task_id=task_id,
        data=data,
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
    )
    return {
        "task": task_id,
        "event": event,
        "branch": branch,
        "pr": state["tasks"][task_id]["pr"],
        "headSha": head_sha,
        "status": state["tasks"][task_id]["status"],
        "revision": state["revision"],
    }, duplicate


def release_task(
    store: StateStore,
    *,
    repo: Path | str,
    task_id: str,
    keep_worktree: bool = False,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Remove a finished task's worktree, refusing to discard unsaved work."""
    repo = require_repository(repo)
    outcome: dict[str, Any] = {}

    def mutator(state: dict[str, Any]) -> dict[str, Any]:
        task = _task(state, task_id)
        if task["status"] not in {"done", "cancelled", "blocked"}:
            raise IllegalTransition(
                f"task {task_id} is {task['status']}; release its worktree only when it is "
                "done, cancelled, or blocked"
            )
        lease = (state.get("leases") or {}).get(task_id)
        if not isinstance(lease, dict) or lease.get("status") != "active":
            raise IllegalTransition(f"task {task_id} has no active worktree")
        path = Path(lease["worktree"])
        if not keep_worktree:
            entry = worktree_at(repo, path)
            if not entry:
                raise ValidationError(f"worktree is not registered with Git: {path}")
            expected_branch = f"refs/heads/{lease['branch']}"
            if entry.get("branch") != expected_branch:
                raise ValidationError(
                    f"refusing to remove worktree on {entry.get('branch')}; expected {expected_branch}"
                )
            if run_git(path, "status", "--porcelain").stdout.strip():
                raise IllegalTransition(
                    f"refusing to remove worktree with uncommitted changes: {path}"
                )
            run_git(repo, "worktree", "remove", str(path))
            run_git(repo, "worktree", "prune")

        updated = copied_state(state)
        released = deepcopy(lease)
        released["status"] = "released"
        released["releasedAt"] = utc_now()
        released["cleanup"] = "retained" if keep_worktree else "cleaned_up"
        updated.setdefault("leases", {})[task_id] = released
        if not keep_worktree:
            updated["tasks"][task_id]["worktree"] = None
        outcome.update({"task": task_id, "worktree": str(path), "cleanup": released["cleanup"]})
        return updated

    state, duplicate = apply_mutation(
        store,
        event_type="lease.released",
        task_id=task_id,
        data={"keepWorktree": keep_worktree},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )
    if duplicate:
        return {"task": task_id, "action": "duplicate", "revision": state["revision"]}, True
    return {**outcome, "revision": state["revision"]}, False
