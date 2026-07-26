"""In-place conversion of schema-v2 controller state to v3.

Schema v2 was only reachable by running `wddctl init` directly — no documented
workflow produced it — but state that exists must not become unreadable. This
converts it rather than stranding it.

The conversion is dry-run first and writes a backup beside the state file
before touching anything.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .schema import SCHEMA_VERSION, validate_state
from .store import atomic_write_text


SUPPORTED_SOURCE_VERSIONS = {2}


def read_source(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValidationError(f"state file does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"state file is not valid JSON: {path}: {error}") from error
    if not isinstance(state, dict):
        raise ValidationError("state file must contain a JSON object")
    version = state.get("schemaVersion")
    if version == SCHEMA_VERSION:
        raise ValidationError(f"{path} is already schema v{SCHEMA_VERSION}; nothing to migrate")
    if version not in SUPPORTED_SOURCE_VERSIONS:
        raise ValidationError(
            f"cannot migrate schemaVersion {version!r}; supported sources: "
            f"{sorted(SUPPORTED_SOURCE_VERSIONS)}"
        )
    return state


def convert(state: dict[str, Any], *, review_policy: str = "risk_based") -> dict[str, Any]:
    """Return the v3 equivalent of a v2 state."""
    scope = dict(state.get("scope") or {})
    migrated: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "revision": state.get("revision", 0),
        "scope": {
            "id": scope.get("id"),
            "baseRef": scope.get("baseRef"),
            "maxConcurrent": None,
            "reviewPolicy": review_policy,
        },
        "constitution": state.get("constitution") or {"status": "draft", "ratification": None},
        "tasks": {},
        "reconcile": {
            "everyNMerges": 3,
            "mergesSinceCheckpoint": 0,
            "lastCheckpointAt": None,
            "pendingNotes": [],
        },
        "monitoring": state.get("monitoring")
        or {
            "mode": "manual",
            "status": "inactive",
            "lastCheckedAt": None,
            "nextCheckDueAt": None,
            "observations": {},
        },
        "events": list(state.get("events") or []),
        "appliedIdempotencyKeys": list(state.get("appliedIdempotencyKeys") or []),
        "telemetry": state.get("telemetry") or {"eventApplications": 0, "renderCount": 0},
    }
    if state.get("leases"):
        migrated["leases"] = state["leases"]

    for task_id, task in (state.get("tasks") or {}).items():
        converted = dict(task)
        converted.setdefault("title", task_id)
        converted.setdefault("risk", "normal")
        # v2 recorded absolute worktree paths; v3 derives the default location.
        converted["worktree"] = None
        migrated["tasks"][task_id] = converted

    validate_state(migrated)
    return migrated


def plan_migration(path: Path | str, *, review_policy: str = "risk_based") -> dict[str, Any]:
    path = Path(path)
    source = read_source(path)
    migrated = convert(source, review_policy=review_policy)
    return {
        "state": str(path),
        "from": source.get("schemaVersion"),
        "to": SCHEMA_VERSION,
        "tasks": sorted(migrated["tasks"]),
        "backup": str(path.with_suffix(path.suffix + ".v2.bak")),
        "notes": [
            "waves are dropped; scheduling is derived from dependencies and conflict domains",
            "every task defaults to risk 'normal' — mark high-risk tasks in plan.json",
            f"reviewPolicy defaults to {review_policy!r}",
            "recorded worktree paths are cleared; the location is derived per checkout",
        ],
    }


def apply_migration(path: Path | str, *, review_policy: str = "risk_based") -> dict[str, Any]:
    path = Path(path)
    plan = plan_migration(path, review_policy=review_policy)
    migrated = convert(read_source(path), review_policy=review_policy)
    backup = Path(plan["backup"])
    shutil.copy2(path, backup)
    atomic_write_text(path, json.dumps(migrated, indent=2, sort_keys=True) + "\n")
    return {**plan, "applied": True}
