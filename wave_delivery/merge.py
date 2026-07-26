"""Integration actions the controller performs itself: refresh and merge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import (
    NoChange,
    apply_mutation,
    require_ratified,
    task_gate,
    transition,
)
from .errors import IllegalTransition, RevisionConflict, ValidationError
from .freshness import check_freshness
from .git import (
    ensure_worktree,
    worktree_for,
    integration_worktree_path,
    is_ancestor,
    require_repository,
    resolve_ref,
    run_git,
    worktree_branch,
)
from .store import StateStore


BLOCKING_FRESHNESS = {"materially_stale", "conflicted"}
# task.head_updated is only legal from these, so refuse before mutating Git.
REFRESHABLE_STATUSES = {"in_progress", "review", "merge_ready"}


def _task(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    try:
        return state["tasks"][task_id]
    except KeyError as error:
        raise ValidationError(f"unknown task: {task_id}") from error


def _preflight(
    state: dict[str, Any], expected_revision: int | None, idempotency_key: str | None
) -> bool:
    """Fail before touching Git, not after; return True for a known duplicate.

    Both merge and refresh mutate Git and only then apply an event. Validating
    the revision inside that later apply left the branch already moved while
    controller state was unchanged.

    The idempotency key is honoured first, matching ``apply_mutation``. A retry
    that carries both an explicit key and the now-stale revision it was first
    issued with is the case explicit keys exist for; rejecting it as a revision
    conflict told the caller "no Git state was changed" when the first attempt
    had in fact already merged.
    """
    if idempotency_key and idempotency_key in state["appliedIdempotencyKeys"]:
        return True
    if expected_revision is not None and state["revision"] != expected_revision:
        raise RevisionConflict(
            f"expected revision {expected_revision}, found {state['revision']}; "
            "no Git state was changed"
        )
    return False


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
    if _preflight(state, expected_revision, idempotency_key):
        return {"task": task_id, "action": "duplicate", "revision": state["revision"], "duplicate": True}
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
    # Existence and cleanliness do not prove identity: a worktree switched to
    # another branch would have the base merged into that branch instead, while
    # the task branch stayed put and the command still reported success.
    checked_out = worktree_branch(worktree_path)
    if checked_out is None:
        raise IllegalTransition(
            f"refusing to refresh {task_id}: worktree {worktree_path} has a detached HEAD; "
            f"check out {branch} first"
        )
    if checked_out != branch:
        raise IllegalTransition(
            f"refusing to refresh {task_id}: worktree {worktree_path} is on {checked_out}, "
            f"not {branch}"
        )
    if run_git(worktree_path, "status", "--porcelain").stdout.strip():
        raise IllegalTransition(
            f"refusing to refresh {task_id}: worktree has uncommitted changes ({worktree_path})"
        )

    outcome: dict[str, Any] = {}

    def mutator(current: dict[str, Any]) -> dict[str, Any]:
        # Everything from here runs inside the store lock, so no other
        # controller can advance the revision between the Git mutation and the
        # state commit. Previously the branch moved and the commit then failed.
        #
        # Legality is checked before any Git work for the same reason: the
        # transition validates status, and refreshing e.g. a blocked task moved
        # its branch and only then raised, leaving state pointing at the old
        # commit.
        require_ratified(current)
        status = current["tasks"][task_id]["status"]
        if status not in REFRESHABLE_STATUSES:
            raise IllegalTransition(
                f"cannot refresh {task_id}: it is {status}; expected one of "
                f"{', '.join(sorted(REFRESHABLE_STATUSES))}"
            )
        before = resolve_ref(repo, branch)
        base_sha = resolve_ref(repo, base_ref)
        recorded = current["tasks"][task_id].get("headSha")
        if is_ancestor(repo, base_sha, before):
            if not recorded or before == recorded:
                raise NoChange({"task": task_id, "action": "already_current", "headSha": before})
            after = before
            action = "adopted_external_merge"
            note = "branch was already current; recorded its head and invalidated evidence"
        else:
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
                    f"refreshing {task_id} from {base_ref} conflicts in: "
                    f"{', '.join(conflicts) or 'unknown files'}; "
                    "resolve it in the task worktree, then re-run"
                )
            after = resolve_ref(repo, branch)
            action = "refreshed"
            note = "review and verification evidence was invalidated by the new head"
        outcome.update(
            {
                "task": task_id,
                "action": action,
                "previousHeadSha": recorded if action == "adopted_external_merge" else before,
                "headSha": after,
                "baseSha": base_sha,
                "note": note,
            }
        )
        return transition(current, "task.head_updated", task_id, {"headSha": after})

    try:
        state, duplicate = apply_mutation(
            store,
            event_type="task.head_updated",
            task_id=task_id,
            data={},
            idempotency_key=idempotency_key,
            expected_revision=expected_revision,
            mutator=mutator,
        )
    except NoChange as unchanged:
        return unchanged.result
    return {**outcome, "revision": state["revision"], "duplicate": duplicate}


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
    if _preflight(state, expected_revision, idempotency_key):
        return {"task": task_id, "action": "duplicate", "revision": state["revision"], "duplicate": True}
    task = _task(state, task_id)
    base_ref = _base_ref(state)

    outcome: dict[str, Any] = {}

    def mutator(current: dict[str, Any]) -> dict[str, Any]:
        # Serialized with the state commit: the base branch must never move
        # while another controller advances the revision underneath us.
        require_ratified(current)
        task = _task(current, task_id)
        gate = task_gate(current, task)
        if gate != "merge_ready":
            raise IllegalTransition(
                f"task {task_id} is at gate '{gate}', not 'merge_ready'; "
                "run 'wddctl next' for the required step"
            )
        head_sha = task.get("headSha")
        if not isinstance(head_sha, str) or not head_sha:
            raise IllegalTransition(f"task {task_id} has no recorded head SHA")

        live = check_freshness(
            repo, base_ref=base_ref, head_ref=head_sha, conflict_domains=task["conflictDomains"]
        )
        if live["classification"] in BLOCKING_FRESHNESS:
            raise IllegalTransition(
                f"task {task_id} is {live['classification']} against {base_ref}; "
                f"run 'wddctl refresh --task {task_id}' first"
            )

        if is_ancestor(repo, head_sha, resolve_ref(repo, base_ref)):
            action = "already_merged"
        else:
            directory = _integration_dir(repo, current, base_ref)
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
                    f"merging {task_id} into {base_ref} conflicts in: "
                    f"{', '.join(conflicts) or 'unknown files'}; "
                    f"run 'wddctl refresh --task {task_id}' and resolve it on the task branch"
                )
            action = "merged"

        base_sha = resolve_ref(repo, base_ref)
        if not is_ancestor(repo, head_sha, base_sha):
            raise IllegalTransition(
                f"task head {head_sha} is still not contained in {base_ref} ({base_sha}) after merging"
            )
        outcome.update(
            {
                "task": task_id,
                "action": action,
                "branch": task.get("branch") or head_sha,
                "baseRef": base_ref,
                "baseSha": base_sha,
                "headSha": head_sha,
            }
        )
        return transition(
            current,
            "task.merged",
            task_id,
            {
                "mergeVerified": True,
                "baseRef": base_ref,
                "baseSha": base_sha,
                "headSha": head_sha,
            },
        )

    state, duplicate = apply_mutation(
        store,
        event_type="task.merged",
        task_id=task_id,
        data={},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )
    return {**outcome, "revision": state["revision"], "duplicate": duplicate}
