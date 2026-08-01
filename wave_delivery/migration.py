"""In-place conversion of older controller state (v2 or v3) to the current schema.

Schema v2 was only reachable by running `wddctl init` directly — no documented
workflow produced it — but state that exists must not become unreadable. This
converts it rather than stranding it. Schema v3 has no scope-optional state
(see schema.py), so v3 -> v4 is a pure version bump once validated.

The conversion is dry-run first and writes a backup beside the state file
before touching anything.
"""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .schema import SCHEMA_VERSION, validate_state
from .store import StateStore, atomic_write_text


SUPPORTED_SOURCE_VERSIONS = {2, 3, 4}
_V4_SCHEMA_VERSION = 4


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


def convert(state: dict[str, Any], *, review_policy: str = "always") -> dict[str, Any]:
    """Return the current-schema (v5) equivalent of a v2, v3, or v4 state.

    Conversion always lands on an intermediate v4-shaped dict first (the
    v2/v3 field remap is untouched from before schema v5 existed), then the
    v4 -> v5 step is a pure bump plus `intake: {"legacy": True}` — migration
    is the only producer of that exemption (see schema.py's
    `_validate_intake`); constructors never mint it.
    """
    v4 = _convert_to_v4(state, review_policy=review_policy)
    migrated = deepcopy(v4)
    migrated["schemaVersion"] = SCHEMA_VERSION
    migrated["intake"] = {"legacy": True}
    validate_state(migrated)
    return migrated


def _convert_to_v4(state: dict[str, Any], *, review_policy: str) -> dict[str, Any]:
    version = state.get("schemaVersion")
    if version == _V4_SCHEMA_VERSION:
        return deepcopy(state)

    if version == 3:
        # Every valid v3 state is already a valid v4 state (schema.py's
        # scope-optional relaxation only adds a case v3 never produced), so
        # the conversion is a pure version bump rather than a field remap.
        migrated = deepcopy(state)
        migrated["schemaVersion"] = _V4_SCHEMA_VERSION
        return migrated

    scope = dict(state.get("scope") or {})
    migrated: dict[str, Any] = {
        "schemaVersion": _V4_SCHEMA_VERSION,
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

    # Not validated here: this dict is v4-shaped and validate_state only
    # accepts the current SCHEMA_VERSION (v5). The caller (convert) takes it
    # through the v4 -> v5 step and validates the final result.
    return migrated


def plan_migration(path: Path | str, *, review_policy: str = "always") -> dict[str, Any]:
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
            f"reviewPolicy is {review_policy!r}"
            + (
                " — schema v2 required review for every task, so this preserves that"
                " obligation; pass --review-policy risk_based to loosen it deliberately"
                if review_policy == "always"
                else " — chosen explicitly; schema v2 required review for every task"
            ),
            "recorded worktree paths are cleared; the location is derived per checkout",
        ],
    }


def apply_migration(path: Path | str, *, review_policy: str = "always") -> dict[str, Any]:
    path = Path(path)
    # Serialized against another migrate: read, backup and write must be one
    # step, or a second migration can copy the already-converted v3 file over
    # the v2 backup and leave no copy of the original anywhere. Normal v3
    # commands cannot race this, since they reject v2 state on read.
    with StateStore(path).locked():
        plan = plan_migration(path, review_policy=review_policy)
        migrated = convert(read_source(path), review_policy=review_policy)
        backup = Path(plan["backup"])
        shutil.copy2(path, backup)
        atomic_write_text(path, json.dumps(migrated, indent=2, sort_keys=True) + "\n")
    return {**plan, "applied": True}
