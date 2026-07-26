"""Task dispatch: admission, isolated worktree, and start recorded as one step."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .engine import (
    ACTIVE_STATUSES,
    admission_blocker,
    apply_mutation,
    describe_blocker,
    NoChange,
    require_ratified,
    transition,
    utc_now,
)
from .errors import IllegalTransition, ValidationError
from .git import (
    branch_exists,
    ensure_worktree,
    worktree_override,
    require_repository,
    resolve_ref,
    worktree_for,
    run_git,
    task_worktree_path,
    worktree_at,
    worktree_branch,
)
from .schema import copied_state
from .store import StateStore


def _task(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    try:
        return state["tasks"][task_id]
    except KeyError as error:
        raise ValidationError(f"unknown task: {task_id}") from error


def _reattach(
    state: dict[str, Any], repo: Path, task_id: str, outcome: dict[str, Any]
) -> dict[str, Any]:
    """Recreate the worktree for an already-started task.

    This is the local-agent-to-cloud-agent handoff: the committed state says a
    task is in progress, its branch exists, but the worktree that lived beside
    the old checkout was never part of the clone.
    """
    task = state["tasks"][task_id]
    branch = task.get("branch")
    if not branch:
        raise IllegalTransition(f"task {task_id} is {task['status']} but has no branch recorded")
    if not branch_exists(repo, branch):
        raise IllegalTransition(
            f"task {task_id} is {task['status']} but branch {branch} is missing from this "
            "repository; fetch it before re-attaching"
        )
    target = worktree_for(repo, state["scope"]["id"], task_id, task.get("worktree"))
    action = ensure_worktree(repo, target, branch, base_ref=state["scope"].get("baseRef"))

    updated = copied_state(state)
    updated["tasks"][task_id]["worktree"] = worktree_override(
        repo, target, state["scope"]["id"], task_id
    )
    lease = dict((updated.get("leases") or {}).get(task_id) or {})
    lease.update(
        {
            "status": "active",
            "branch": branch,
            "worktree": str(target),
            "baseRef": state["scope"].get("baseRef"),
            "headSha": resolve_ref(repo, branch),
            "reattachedAt": utc_now(),
        }
    )
    updated.setdefault("leases", {})[task_id] = lease
    outcome.update(
        {
            "task": task_id,
            "action": f"reattach:{action}",
            "branch": branch,
            "worktree": str(target),
            "status": task["status"],
            "specPath": task["specPath"],
        }
    )
    return updated


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
    # A re-attach must not derive the same idempotency key as the original
    # start, or it would be swallowed as a duplicate and never run.
    reattaching = _task(store.read(), task_id)["status"] in ACTIVE_STATUSES
    event_type = "lease.reattached" if reattaching else "task.started"

    def mutator(state: dict[str, Any]) -> dict[str, Any]:
        task = _task(state, task_id)
        if task["status"] in ACTIVE_STATUSES:
            return _reattach(state, repo, task_id, outcome)
        if task["status"] != "todo":
            raise IllegalTransition(
                f"task {task_id} is {task['status']}, not todo; nothing to start"
            )
        # Ratification is checked by the task.started transition, but that runs
        # after ensure_worktree below. Unratified starts created a branch and a
        # worktree and only then raised, leaving Git ahead of state. Checked
        # before admission, matching the order transition() applies them in.
        require_ratified(state)
        blocker = admission_blocker(state, task_id)
        if blocker is not None:
            raise IllegalTransition(
                f"task {task_id} is not admissible yet: {describe_blocker(blocker)}"
            )
        base_ref = state["scope"].get("baseRef")
        if not base_ref:
            raise IllegalTransition("this scope has no configured base ref")
        task_branch = branch or task.get("branch") or f"task/{task_id}"
        task_worktree = Path(
            worktree
            or worktree_for(repo, state["scope"]["id"], task_id, task.get("worktree"))
        ).resolve()
        action = ensure_worktree(repo, task_worktree, task_branch, base_ref=base_ref)
        head_sha = resolve_ref(repo, task_branch)

        updated = transition(state, "task.started", task_id, {})
        updated["tasks"][task_id]["branch"] = task_branch
        override = worktree_override(repo, task_worktree, state["scope"]["id"], task_id)
        updated["tasks"][task_id]["worktree"] = override
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
        event_type=event_type,
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
    outcome: dict[str, Any] = {}

    def chosen_event(state: dict[str, Any]) -> str:
        # Keyed on the deliverable, not on headSha: refresh also populates
        # headSha, so keying on it meant a task refreshed before its first
        # submission never recorded a PR and stayed at the no_pr gate forever.
        return "task.pr_recorded" if _task(state, task_id).get("pr") is None else "task.head_updated"

    def mutator(state: dict[str, Any]) -> dict[str, Any]:
        # Every Git read below happens under the store lock. Resolving the head
        # outside it let a concurrent refresh commit a newer head and then be
        # silently reverted by this command's stale SHA, leaving recorded state
        # pointing at a commit that was no longer the branch tip.
        task = _task(state, task_id)
        branch = task.get("branch")
        if not branch:
            raise IllegalTransition(f"task {task_id} has no branch; run 'wddctl start' first")
        resolved_worktree = worktree_for(
            repo, state["scope"]["id"], task_id, task.get("worktree")
        )
        if resolved_worktree.exists():
            # Clean is not enough: a worktree switched to another branch would let
            # submit record the stale task-branch SHA and silently drop the work
            # that was actually committed.
            checked_out = worktree_branch(resolved_worktree)
            if checked_out is None:
                raise IllegalTransition(
                    f"task {task_id} worktree {resolved_worktree} has a detached HEAD; "
                    f"check out {branch} before submitting"
                )
            if checked_out != branch:
                raise IllegalTransition(
                    f"task {task_id} worktree {resolved_worktree} is on {checked_out}, not {branch}; "
                    "submit would record the wrong commit"
                )
            dirty = run_git(resolved_worktree, "status", "--porcelain").stdout.strip()
            if dirty:
                raise IllegalTransition(
                    f"task {task_id} has uncommitted changes in {resolved_worktree}; "
                    "commit them before submitting"
                )
        head_sha = resolve_ref(repo, branch)
        # Compare against the base this task actually started from. Comparing with
        # the *current* base let an untouched branch look like work as soon as
        # another task advanced the base.
        lease = (state.get("leases") or {}).get(task_id) or {}
        origin = lease.get("baseSha") or (
            resolve_ref(repo, state["scope"]["baseRef"]) if state["scope"].get("baseRef") else None
        )
        if origin:
            own_commits = run_git(
                repo, "rev-list", "--count", f"{origin}..{head_sha}", check=False
            ).stdout.strip()
            if own_commits in {"", "0"}:
                raise IllegalTransition(
                    f"task {task_id} has no commits of its own on {branch} since it started; "
                    "there is nothing to submit"
                )
        event = chosen_event(state)
        if event == "task.head_updated" and task.get("headSha") == head_sha:
            # Nothing moved. task.head_updated invalidates review, verification
            # and freshness by design, so applying it here would let an
            # innocent retry discard passed evidence and demote the task.
            raise NoChange(
                {
                    "task": task_id,
                    "event": "none",
                    "action": "already_recorded",
                    "branch": branch,
                    "pr": task.get("pr"),
                    "headSha": head_sha,
                    "status": task["status"],
                    "revision": state["revision"],
                }
            )
        reference = pr or f"branch:{branch}@{head_sha[:12]}"
        data = (
            {"pr": reference, "headSha": head_sha}
            if event == "task.pr_recorded"
            else {"headSha": head_sha}
        )
        outcome.update({"task": task_id, "event": event, "branch": branch, "headSha": head_sha})
        return transition(state, event, task_id, data)

    try:
        state, duplicate = apply_mutation(
            store,
            event_type=chosen_event,
            task_id=task_id,
            data={},
            idempotency_key=idempotency_key,
            expected_revision=expected_revision,
            mutator=mutator,
        )
    except NoChange as unchanged:
        return unchanged.result, False
    if duplicate:
        task = state["tasks"][task_id]
        return {
            "task": task_id,
            "event": "duplicate",
            "branch": task.get("branch"),
            "pr": task.get("pr"),
            "headSha": task.get("headSha"),
            "status": task["status"],
            "revision": state["revision"],
        }, True
    return {
        **outcome,
        "pr": state["tasks"][task_id]["pr"],
        "status": state["tasks"][task_id]["status"],
        "revision": state["revision"],
    }, False


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
        path = worktree_for(repo, state["scope"]["id"], task_id, task.get("worktree"))
        cleanup = "retained"
        if not keep_worktree:
            entry = worktree_at(repo, path)
            if not entry and not path.exists():
                # A crash between 'git worktree remove' and the state write
                # leaves the lease active with nothing on disk. Every other
                # git-then-state command has an already-done path; without one
                # here, release could never complete and the only escape
                # (--keep-worktree) recorded "retained" for a worktree that was
                # gone.
                cleanup = "already_removed"
            else:
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
                cleanup = "cleaned_up"
            run_git(repo, "worktree", "prune")

        updated = copied_state(state)
        released = deepcopy(lease)
        released["status"] = "released"
        released["releasedAt"] = utc_now()
        released["cleanup"] = cleanup
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
