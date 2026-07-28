"""Cheap, bounded Git observation for active controller tasks."""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import Any

from .engine import utc_now
from .git import (
    branch_exists,
    is_ancestor,
    require_repository,
    resolve_ref,
    worktree_for,
    run_git,
    worktree_at,
)
from .schema import copied_state
from .store import StateStore


def _observations(
    state: dict[str, Any], repo: Path, *, state_path: str | None = None, repo_arg: str = "."
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    observations: dict[str, Any] = {}
    actions: list[dict[str, str]] = []
    for task_id, task in sorted(state["tasks"].items()):
        item: dict[str, Any] = {"branch": task.get("branch")}
        branch = task.get("branch")
        if branch and branch_exists(repo, branch):
            head_sha = resolve_ref(repo, branch)
            item["branchHead"] = head_sha
            if task.get("headSha") and task["headSha"] != head_sha:
                actions.append({"task": task_id, "action": "record_head_change"})
        elif branch:
            item["branchHead"] = None
            actions.append({"task": task_id, "action": "resolve_missing_branch"})
        if (state.get("leases") or {}).get(task_id, {}).get("status") == "active":
            resolved = worktree_for(repo, state["scope"]["id"], task_id, task.get("worktree"))
            entry = worktree_at(repo, resolved)
            if entry:
                status = run_git(resolved, "status", "--porcelain").stdout.strip()
                item["worktreeStatus"] = "dirty" if status else "clean"
                if status:
                    actions.append({"task": task_id, "action": "resolve_dirty_worktree"})
            else:
                item["worktreeStatus"] = "missing"
                actions.append({"task": task_id, "action": "resolve_missing_worktree"})

        # Monitor for human merges: check if a merge_ready task has been merged into base_ref
        if task["status"] == "merge_ready":
            head_sha = task.get("headSha")
            base_ref = state["scope"].get("baseRef")
            if head_sha and base_ref and is_ancestor(repo, head_sha, base_ref):
                # Build the command string following the same pattern as
                # engine.decorate_actions: --state is only echoed back when
                # the caller didn't use the default (mirrors cli.py's
                # _state_option), and --repo reflects the actual argument
                # monitor ran with, not a hardcoded ".".
                prefix = "wddctl" + (f" --state {shlex.quote(state_path)}" if state_path else "")
                fields = {"task": shlex.quote(task_id), "repo": shlex.quote(repo_arg)}
                command = f"{prefix} merge --task {fields['task']} --repo {fields['repo']} --observed"
                actions.append({
                    "task": task_id,
                    "action": "record_human_merge",
                    "command": command,
                })

        observations[task_id] = item
    return observations, actions


def monitor_once(
    store: StateStore, *, repo: Path | str, dry_run: bool = False, state_path: str | None = None
) -> dict[str, Any]:
    repo_arg = str(repo)
    repo = require_repository(repo)
    with store.locked():
        state = store.read()
        observations, actions = _observations(
            state, repo, state_path=state_path, repo_arg=repo_arg
        )
        prior = (state["monitoring"].get("observations") or {})
        changed = observations != prior
        result = {
            "scope": state["scope"]["id"],
            "revision": state["revision"],
            "changed": changed,
            "actions": actions,
            "observations": observations,
            "dryRun": dry_run,
        }
        if not changed or dry_run:
            return result
        updated = copied_state(state)
        updated["revision"] = state["revision"] + 1
        updated["monitoring"]["observations"] = observations
        updated["monitoring"]["lastCheckedAt"] = utc_now()
        digest = hashlib.sha256(
            json.dumps(observations, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        key = f"monitor:{updated['revision']}:{digest}"
        updated["events"].append(
            {
                "revision": updated["revision"],
                "type": "monitor.observed",
                "task": None,
                "idempotencyKey": key,
                "at": utc_now(),
            }
        )
        updated["appliedIdempotencyKeys"].append(key)
        updated["telemetry"]["eventApplications"] += 1
        store.write(updated)
        result["revision"] = updated["revision"]
        return result
