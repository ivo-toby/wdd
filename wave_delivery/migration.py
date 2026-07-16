"""Dry-run-first migration from WDD schema-v1 JSON to controller schema v2."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .schema import TASK_STATUSES, new_state, task_state, validate_state
from .store import atomic_write_text


KANBAN_STATUSES = {"todo", "in-progress", "review", "done", "blocked", "cancelled"}


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValidationError(f"migration source does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"migration source is not valid JSON: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValidationError("migration source must be a JSON object")
    return value


def _safe_relative_path(value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{name} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValidationError(f"{name} must not escape the scope directory")
    return path


def _stable_task_path(source: Path, *, micro_wave: bool) -> Path:
    if micro_wave:
        return source
    if source.parent.name in KANBAN_STATUSES:
        return source.parent.parent / "tasks" / source.name
    return source.parent / "tasks" / source.name


def _v2_status(value: Any) -> str:
    if value == "in-progress":
        return "in_progress"
    if isinstance(value, str) and value in TASK_STATUSES:
        return value
    return "todo"


def _legacy_tasks(source: dict[str, Any], *, micro_wave: bool) -> list[dict[str, Any]]:
    if micro_wave:
        tasks = source.get("tasks")
        if not isinstance(tasks, list):
            raise ValidationError("schema-v1 micro-wave state requires a tasks list")
        return tasks
    tasks: list[dict[str, Any]] = []
    waves = source.get("waves")
    if not isinstance(waves, list):
        raise ValidationError("schema-v1 orchestration state requires a waves list")
    for wave in waves:
        if not isinstance(wave, dict):
            raise ValidationError("each schema-v1 wave must be an object")
        entries = wave.get("tasks")
        if not isinstance(entries, list):
            raise ValidationError("each schema-v1 wave requires a tasks list")
        tasks.extend(entries)
    return tasks


def _scope_from_v1(source: dict[str, Any]) -> tuple[str, str, bool]:
    if source.get("kind") == "micro_wave_state" or "work" in source:
        work = source.get("work")
        if not isinstance(work, dict) or not isinstance(work.get("id"), str):
            raise ValidationError("schema-v1 micro-wave state requires work.id")
        return work["id"], "micro_wave", True
    epic = source.get("epic")
    if not isinstance(epic, dict) or not isinstance(epic.get("id"), str):
        raise ValidationError("schema-v1 orchestration state requires epic.id")
    return epic["id"], "epic", False


def _migration_id(state_path: Path, source_text: str) -> str:
    digest = hashlib.sha256((str(state_path.resolve()) + source_text).encode("utf-8")).hexdigest()
    return f"v1-to-v2-{digest[:12]}"


def build_migration_plan(state_path: Path | str) -> dict[str, Any]:
    state_path = Path(state_path).resolve()
    try:
        source_text = state_path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ValidationError(f"migration source does not exist: {state_path}") from error
    source = _read_json(state_path)
    if source.get("schemaVersion") == 2:
        raise ValidationError("state file is already schema version 2")
    if source.get("schemaVersion") != 1:
        raise ValidationError("only schemaVersion 1 can be migrated")
    scope_id, scope_kind, micro_wave = _scope_from_v1(source)
    target = new_state(scope_id, scope_kind)
    source_tasks = _legacy_tasks(source, micro_wave=micro_wave)
    moves: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_task in source_tasks:
        if not isinstance(raw_task, dict):
            raise ValidationError("schema-v1 task entries must be objects")
        task_id = raw_task.get("id")
        if not isinstance(task_id, str) or not task_id:
            raise ValidationError("schema-v1 task requires id")
        if task_id in seen:
            raise ValidationError(f"schema-v1 task appears more than once: {task_id}")
        seen.add(task_id)
        old_path = _safe_relative_path(raw_task.get("path"), f"task {task_id}.path")
        stable_path = _stable_task_path(old_path, micro_wave=micro_wave)
        task = task_state(
            task_id,
            depends_on=list(raw_task.get("dependsOn") or []),
            conflict_domains=list(raw_task.get("conflictDomains") or []),
            spec_path=str(stable_path),
        )
        task["status"] = _v2_status(raw_task.get("status"))
        task["branch"] = raw_task.get("branch")
        task["worktree"] = raw_task.get("workerWorktree") or raw_task.get("worktree")
        task["headSha"] = raw_task.get("latestCommit") or raw_task.get("headSha")
        task["pr"] = raw_task.get("pr")
        task["verification"] = raw_task.get("verification")
        feedback = raw_task.get("blockingFeedback") or []
        if feedback:
            task["blocker"] = "Migrated blocking feedback requires reconciliation."
        target["tasks"][task_id] = task
        if old_path != stable_path:
            moves.append({"task": task_id, "source": str(old_path), "target": str(stable_path)})

    if micro_wave:
        target["waves"] = {}
    else:
        target["waves"] = {
            wave["id"]: {
                "id": wave["id"],
                "status": wave.get("status", "planned"),
                "tasks": [task["id"] for task in wave.get("tasks", [])],
                "strategy": wave.get("strategy"),
            }
            for wave in source.get("waves", [])
            if isinstance(wave, dict) and isinstance(wave.get("id"), str)
        }
    if isinstance(source.get("monitoring"), dict):
        target["monitoring"] = source["monitoring"]
    target["events"] = [
        {
            "revision": 0,
            "type": "migration.v1_to_v2",
            "task": None,
            "idempotencyKey": None,
            "at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
    ]
    validate_state(target)
    migration_id = _migration_id(state_path, source_text)
    migration_key = f"migration:{migration_id}"
    target["events"][0]["idempotencyKey"] = migration_key
    target["appliedIdempotencyKeys"] = [migration_key]
    return {
        "schemaVersion": 1,
        "kind": "wdctl_migration_plan",
        "id": migration_id,
        "sourceState": str(state_path),
        "sourceStateFingerprint": _sha256_file(state_path),
        "backupDirectory": str(state_path.parent / ".wdctl-migrations" / migration_id),
        "scope": target["scope"],
        "moves": moves,
        "targetState": target,
    }


def _backup_manifest(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "kind": "wdctl_migration_backup",
        "id": plan["id"],
        "sourceState": plan["sourceState"],
        "sourceStateFingerprint": _sha256_file(Path(plan["sourceState"])),
        "targetStateFingerprint": _sha256_bytes(
            (json.dumps(plan["targetState"], indent=2, sort_keys=True) + "\n").encode("utf-8")
        ),
        "moves": plan["moves"],
        "phase": "backed_up",
        "completedMoves": [],
    }


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def apply_migration(plan: dict[str, Any]) -> dict[str, Any]:
    state_path = Path(plan["sourceState"])
    backup_directory = Path(plan["backupDirectory"])
    expected_state_fingerprint = plan.get("sourceStateFingerprint")
    if not isinstance(expected_state_fingerprint, str) or not expected_state_fingerprint:
        raise ValidationError("migration plan is missing its source state fingerprint")
    if _sha256_file(state_path) != expected_state_fingerprint:
        raise ValidationError("refusing migration because source state changed after planning")
    if backup_directory.exists():
        raise ValidationError(f"migration backup already exists: {backup_directory}")
    for move in plan["moves"]:
        source = state_path.parent / move["source"]
        target = state_path.parent / move["target"]
        if not source.is_file():
            raise ValidationError(f"migration task file does not exist: {source}")
        if target.exists():
            raise ValidationError(f"migration target already exists: {target}")
    backup_directory.mkdir(parents=True)
    state_backup = backup_directory / "state-v1.json"
    shutil.copy2(state_path, state_backup)
    manifest = _backup_manifest(plan)
    for index, move in enumerate(plan["moves"]):
        source = state_path.parent / move["source"]
        copy_path = backup_directory / "tasks" / f"{index:03d}-{source.name}"
        copy_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, copy_path)
        move["backup"] = str(copy_path.relative_to(backup_directory))
        move["fingerprint"] = _sha256_file(source)
        manifest["moves"][index]["backup"] = move["backup"]
        manifest["moves"][index]["fingerprint"] = move["fingerprint"]
    manifest_path = backup_directory / "manifest.json"
    _write_manifest(manifest_path, manifest)
    try:
        for index, move in enumerate(plan["moves"]):
            source = state_path.parent / move["source"]
            target = state_path.parent / move["target"]
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            manifest["completedMoves"].append(index)
            manifest["phase"] = "moving_tasks"
            _write_manifest(manifest_path, manifest)
        if _sha256_file(state_path) != expected_state_fingerprint:
            raise ValidationError("refusing migration because source state changed while applying")
        atomic_write_text(state_path, json.dumps(plan["targetState"], indent=2, sort_keys=True) + "\n")
        manifest["phase"] = "completed"
        _write_manifest(manifest_path, manifest)
    except Exception:
        manifest["phase"] = "failed"
        _write_manifest(manifest_path, manifest)
        raise
    return {
        "migrated": str(state_path),
        "backupDirectory": str(backup_directory),
        "movedTasks": len(plan["moves"]),
        "scope": plan["scope"],
    }


def rollback_migration(backup_directory: Path | str) -> dict[str, Any]:
    backup_directory = Path(backup_directory)
    manifest_path = backup_directory / "manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("kind") != "wdctl_migration_backup":
        raise ValidationError("backup manifest is not a wdctl migration backup")
    state_path = Path(manifest["sourceState"])
    current_state_fingerprint = _sha256_file(state_path)
    allowed_state_fingerprints = {
        manifest.get("sourceStateFingerprint"),
        manifest.get("targetStateFingerprint"),
    }
    if current_state_fingerprint not in allowed_state_fingerprints:
        raise ValidationError("refusing rollback because controller state changed after migration")
    for move in reversed(manifest.get("moves", [])):
        source = state_path.parent / move["source"]
        target = state_path.parent / move["target"]
        backup = backup_directory / move["backup"]
        if target.exists():
            if _sha256_file(target) != move.get("fingerprint"):
                raise ValidationError(
                    f"refusing rollback because migrated task changed: {target}"
                )
            target.unlink()
        if source.exists() and _sha256_file(source) != move.get("fingerprint"):
            raise ValidationError(
                f"refusing rollback because original task path changed: {source}"
            )
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, source)
    atomic_write_text(state_path, (backup_directory / "state-v1.json").read_text(encoding="utf-8"))
    manifest["phase"] = "rolled_back"
    _write_manifest(manifest_path, manifest)
    return {"restored": str(state_path), "backupDirectory": str(backup_directory)}
