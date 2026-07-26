"""Integration actions the controller performs itself: refresh and merge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import apply_event, task_gate
from .errors import IllegalTransition, ValidationError
from .freshness import check_freshness
from .git import (
    ensure_worktree,
    worktree_for,
    integration_worktree_path,
    is_ancestor,
    require_repository,
    resolve_ref,
    run_git,
)
from .store import StateStore


BLOCKING_FRESHNESS = {"materially_stale", "conflicted"}


def _task(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    try:
        return state["tasks"][task_id]
    except KeyError as error:
        raise ValidationError(f"unknown task: {task_id}") from error


def _base_ref(state: dict[str, Any]) -> str:
    base_ref = state["scope"].get("baseRef")
    if not isinstance(base_ref, str) or not base_ref:
        raise IllegalTransition("this scope has no configured base ref")
    return base_ref


def _integration_dir(repo: Path, state: dict[str, Any], base_ref: str) -> Path:
    """Merge inside the controller checkout when it is already on the base branch."""
    current = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if current == base_ref:
        if run_git(repo, "status", "--porcelain").stdout.strip():
            raise IllegalTransition(
                f"{repo} is on {base_ref} but has uncommitted changes; commit or stash before merging"
            )
        return repo
    path = integration_worktree_path(repo, state["scope"]["id"])
    ensure_worktree(repo, path, base_ref, base_ref=None)
    return path


def refresh_task(
    store: StateStore,
    *,
    repo: Path | str,
    task_id: str,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Merge the scope base into a task branch and record the new head."""
    repo = require_repository(repo)
    state = store.read()
    task = _task(state, task_id)
    base_ref = _base_ref(state)
    branch = task.get("branch")
    if not branch:
        raise IllegalTransition(f"task {task_id} has no branch; run 'wddctl start' first")
    worktree_path = worktree_for(
        repo, state["scope"]["id"], task_id, task.get("worktree")
    )
    if not worktree_path.exists():
        raise IllegalTransition(
            f"task worktree is missing: {worktree_path}; "
            f"run 'wddctl start --task {task_id} --repo .' to re-attach it"
        )
    if run_git(worktree_path, "status", "--porcelain").stdout.strip():
        raise IllegalTransition(
            f"refusing to refresh {task_id}: worktree has uncommitted changes ({worktree_path})"
        )

    before = resolve_ref(repo, branch)
    base_sha = resolve_ref(repo, base_ref)
    if is_ancestor(repo, base_sha, before):
        recorded = task.get("headSha")
        if not recorded or before == recorded:
            # Nothing submitted yet, or state already matches the branch.
            return {"task": task_id, "action": "already_current", "headSha": before}
        # The branch moved without wddctl seeing it — typically a conflict
        # resolved by hand in the worktree. Record the head so the task is not
        # left reporting stale freshness against a commit that no longer exists.
        state, duplicate = apply_event(
            store,
            event_type="task.head_updated",
            task_id=task_id,
            data={"headSha": before},
            idempotency_key=idempotency_key,
            expected_revision=expected_revision,
        )
        return {
            "task": task_id,
            "action": "adopted_external_merge",
            "previousHeadSha": task.get("headSha"),
            "headSha": before,
            "baseSha": base_sha,
            "revision": state["revision"],
            "duplicate": duplicate,
            "note": "branch was already current; recorded its head and invalidated evidence",
        }

    merge = run_git(
        worktree_path,
        "merge",
        "--no-edit",
        "-m",
        f"wdd: refresh {task_id} from {base_ref}",
        base_sha,
        check=False,
    )
    if merge.returncode != 0:
        conflicts = run_git(
            worktree_path, "diff", "--name-only", "--diff-filter=U", check=False
        ).stdout.split()
        run_git(worktree_path, "merge", "--abort", check=False)
        raise IllegalTransition(
            f"refreshing {task_id} from {base_ref} conflicts in: {', '.join(conflicts) or 'unknown files'}; "
            "resolve it in the task worktree, then re-run"
        )

    after = resolve_ref(repo, branch)
    state, duplicate = apply_event(
        store,
        event_type="task.head_updated",
        task_id=task_id,
        data={"headSha": after},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
    )
    return {
        "task": task_id,
        "action": "refreshed",
        "previousHeadSha": before,
        "headSha": after,
        "baseSha": base_sha,
        "revision": state["revision"],
        "duplicate": duplicate,
        "note": "review and verification evidence was invalidated by the new head",
    }


def merge_task(
    store: StateStore,
    *,
    repo: Path | str,
    task_id: str,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Perform the merge into the scope base, then record it as Git-verified."""
    repo = require_repository(repo)
    state = store.read()
    task = _task(state, task_id)
    base_ref = _base_ref(state)

    gate = task_gate(state, task)
    if gate != "merge_ready":
        raise IllegalTransition(
            f"task {task_id} is at gate '{gate}', not 'merge_ready'; run 'wddctl next' for the required step"
        )
    head_sha = task.get("headSha")
    if not isinstance(head_sha, str) or not head_sha:
        raise IllegalTransition(f"task {task_id} has no recorded head SHA")

    branch = task.get("branch") or head_sha
    live = check_freshness(
        repo,
        base_ref=base_ref,
        head_ref=head_sha,
        conflict_domains=task["conflictDomains"],
    )
    if live["classification"] in BLOCKING_FRESHNESS:
        raise IllegalTransition(
            f"task {task_id} is {live['classification']} against {base_ref}; "
            f"run 'wddctl refresh --task {task_id}' first"
        )

    base_sha_before = resolve_ref(repo, base_ref)
    if is_ancestor(repo, head_sha, base_sha_before):
        action = "already_merged"
    else:
        directory = _integration_dir(repo, state, base_ref)
        merge = run_git(
            directory,
            "merge",
            "--no-ff",
            "-m",
            f"wdd: merge {task_id} into {base_ref}",
            head_sha,
            check=False,
        )
        if merge.returncode != 0:
            conflicts = run_git(
                directory, "diff", "--name-only", "--diff-filter=U", check=False
            ).stdout.split()
            run_git(directory, "merge", "--abort", check=False)
            raise IllegalTransition(
                f"merging {task_id} into {base_ref} conflicts in: {', '.join(conflicts) or 'unknown files'}; "
                f"run 'wddctl refresh --task {task_id}' and resolve it on the task branch"
            )
        action = "merged"

    base_sha = resolve_ref(repo, base_ref)
    if not is_ancestor(repo, head_sha, base_sha):
        raise IllegalTransition(
            f"task head {head_sha} is still not contained in {base_ref} ({base_sha}) after merging"
        )

    state, duplicate = apply_event(
        store,
        event_type="task.merged",
        task_id=task_id,
        data={
            "mergeVerified": True,
            "baseRef": base_ref,
            "baseSha": base_sha,
            "headSha": head_sha,
        },
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
    )
    return {
        "task": task_id,
        "action": action,
        "branch": branch,
        "baseRef": base_ref,
        "baseSha": base_sha,
        "headSha": head_sha,
        "revision": state["revision"],
        "duplicate": duplicate,
    }
