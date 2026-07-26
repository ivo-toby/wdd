"""Cheap, bounded Git observation for active controller tasks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .engine import utc_now
from .git import (
    branch_exists,
    require_repository,
    resolve_ref,
    worktree_for,
    run_git,
    worktree_at,
)
from .schema import copied_state
from .store import StateStore


def _observations(state: dict[str, Any], repo: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
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
        observations[task_id] = item
    return observations, actions


def monitor_once(store: StateStore, *, repo: Path | str, dry_run: bool = False) -> dict[str, Any]:
    repo = require_repository(repo)
    with store.locked():
        state = store.read()
        observations, actions = _observations(state, repo)
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
