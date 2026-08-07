"""In-place conversion of older controller state (v2-v5) to the current schema.

Schema v2 was only reachable by running `wddctl init` directly -- no documented
workflow produced it -- but state that exists must not become unreadable. This
converts it rather than stranding it. Schema v3 has no scope-optional state
(see schema.py), so v3 -> v4 is a pure version bump once validated, and v4 ->
v5 is a pure version bump plus `intake: {"legacy": True}`.

v5 -> v6 (epic-scoped-state plan, Task 3, spec Sec4's per-field migration
table) is a different kind of step: unlike every earlier bump, it also moves
files on disk (spec.md/design.md/plan.json/tasks/ + recorded research
artifacts into `epics/<slug>/`) and stamps existing evidence with
migration-time config digests. `convert()` composes whichever earlier steps
the source needs with this one, so every source version still lands on the
CURRENT schema in one call -- mirroring the pre-existing v2/v3/v4 -> v5
composition below.

The conversion is dry-run first and writes a backup beside the state file
before touching anything.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import (
    config_path,
    default_config,
    effective_config_digest,
    load_layers,
    project,
    save_overlay,
)
from .errors import ValidationError
from .finalize import _final_verification_projection_digest
from .handover import _sanitize_task_id_for_filename
from .schema import SCHEMA_VERSION, EPIC_SLUG_PATTERN, validate_state
from .store import StateStore, atomic_write_text


SUPPORTED_SOURCE_VERSIONS = {2, 3, 4, 5, 6}
_V4_SCHEMA_VERSION = 4
_V5_SCHEMA_VERSION = 5
_V6_SCHEMA_VERSION = 6

# The reserved filename the typed resolver (paths.py) and the future archive
# transaction (Task 6) both refuse anywhere in an artifact ref -- migration
# must not silently move a v5 artifact onto it either (spec Sec4).
_RESERVED_BASENAME = "record.json"

_MANIFEST_NAME = "manifest.json"
_ATTEMPT_DIR_MODE = 0o700
_ATTEMPT_FILE_MODE = 0o400


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


def convert(
    state: dict[str, Any], *, review_policy: str = "always", wdd_dir: Path | str
) -> dict[str, Any]:
    """Return the current-schema (v7) equivalent of a v2..v6 state.

    Conversion always lands on an intermediate v6-shaped dict first: a v6
    SOURCE skips straight to it (no artifact files to move -- they already
    live under `epics/<slug>/`); a v5 source runs the v5 -> v6 step
    (`convert_v5_to_v6`, moving epic-owned artifact files under `wdd_dir`
    and stamping migration-time evidence -- migration is the only producer
    of the v6 `intake.configure` exemption, see schema.py's
    `_validate_configure`); anything older (v2/v3/v4) is remapped up to v5
    first, unchanged from before schema v6 existed. The final v6 -> v7 step
    (`convert_v6_to_v7`) is a pure version bump plus `parked: {}` (spec
    "Schema v7") -- no file moves, applied uniformly regardless of which
    branch produced the v6-shaped intermediate.
    """
    if state.get("schemaVersion") == _V6_SCHEMA_VERSION:
        v6 = deepcopy(state)
    elif state.get("schemaVersion") == _V5_SCHEMA_VERSION:
        v6 = convert_v5_to_v6(deepcopy(state), wdd_dir=wdd_dir)
    else:
        v4 = _convert_to_v4(state, review_policy=review_policy)
        v5 = _convert_v4_to_v5(v4)
        v6 = convert_v5_to_v6(v5, wdd_dir=wdd_dir)
    return convert_v6_to_v7(v6)


def _convert_v4_to_v5(v4: dict[str, Any]) -> dict[str, Any]:
    """The v4 -> v5 step (unchanged from before schema v6 existed): a pure
    bump plus `intake: {"legacy": True}` -- migration is the only producer
    of that exemption (see schema.py's `_validate_intake`); constructors
    never mint it.

    Not validated here: `validate_state` only accepts the CURRENT
    SCHEMA_VERSION (v6), so this intermediate v5-shaped dict is validated
    only once the v5 -> v6 step (`convert_v5_to_v6`) has finished.
    """
    migrated = deepcopy(v4)
    migrated["schemaVersion"] = _V5_SCHEMA_VERSION
    migrated["intake"] = {"legacy": True}
    # Handover fields (phase-6a, spec Sec3) postdate every migratable source
    # version, including a source that was ALREADY v4 (the passthrough
    # branch of _convert_to_v4 above, which does not touch task fields at
    # all) -- applied here so every path to v5 gets the same defaults
    # task_state() gives a freshly planned task.
    for task in migrated["tasks"].values():
        task.setdefault("context", [])
        task.setdefault("model", None)
        task.setdefault("reviewModel", None)
        # Handover fields (phase-6b Task 2, spec Sec3) postdate every
        # migratable source version the same way context/model/reviewModel
        # (phase-6a) did -- same setdefault treatment, same reasoning.
        task.setdefault("snapshot", None)
        task.setdefault("inputs", [])
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
    # accepts the current SCHEMA_VERSION (v6). The caller (convert) takes it
    # through the v5 -> v6 step and validates the final result.
    return migrated


# ---------------------------------------------------------------------------
# v5 -> v6 (epic-scoped-state plan, Task 3, spec Sec4's per-field table).
# ---------------------------------------------------------------------------

_SLUG_INVALID = re.compile(r"[^a-z0-9-]+")
_SLUG_REPEAT_DASH = re.compile(r"-{2,}")


def _slugify_scope_id(scope_id: str | None) -> str:
    """Best-effort epic slug derived from a v5 scope id (spec Sec4: "slug
    from the active scope id, or 'legacy' when no scope"). The spec does
    not define the exact derivation algorithm for an id that is not already
    slug-shaped -- this strips a conventional `SCOPE-` prefix, lowercases,
    replaces anything outside `[a-z0-9-]` with `-`, and collapses/trims
    dashes; an id that still does not satisfy `EPIC_SLUG_PATTERN` afterwards
    (too short, empty, all-punctuation) falls back to `"legacy"` rather than
    ever writing an invalid `state.epic`. Flagged in the Task 3 report as a
    judgment call, not a normative spec reading.
    """
    if not scope_id:
        return "legacy"
    candidate = scope_id.strip().lower()
    if candidate.startswith("scope-"):
        candidate = candidate[len("scope-"):]
    candidate = _SLUG_INVALID.sub("-", candidate).strip("-")
    candidate = _SLUG_REPEAT_DASH.sub("-", candidate)
    if len(candidate) > 64:
        candidate = candidate[:64].rstrip("-")
    if not candidate or not EPIC_SLUG_PATTERN.match(candidate):
        return "legacy"
    return candidate


def _epic_slug_for_migration(state: dict[str, Any]) -> str:
    scope = state.get("scope")
    scope_id = scope.get("id") if isinstance(scope, dict) else None
    return _slugify_scope_id(scope_id)


def _research_artifact_paths(state: dict[str, Any]) -> list[str]:
    research = (state.get("intake") or {}).get("research") or {}
    paths = []
    for artifact in research.get("artifacts") or []:
        path = artifact.get("path")
        if isinstance(path, str) and path:
            paths.append(path.split("#", 1)[0])
    return paths


def plan_v6_file_moves(
    wdd_dir: Path, slug: str, state: dict[str, Any]
) -> list[tuple[Path, Path]]:
    """[(source, destination), ...] for every epic-owned artifact a v5 state
    names, under the OLD flat layout, that must move into `epics/<slug>/`
    (spec Sec4): `spec.md`/`design.md`/`plan.json`, the whole `tasks/`
    directory, and research artifacts recorded in
    `intake.research.artifacts[].path` -- NOT a blind scan of `research/`,
    since the spec names only the RECORDED artifacts as migratable.
    `shared-context/` is never included (spec: "stays put"). Only entries
    whose source actually exists on disk are planned -- a source fixture
    that never wrote the file (e.g. a synthetic v2/v3/v4 unit-test state
    with no artifacts on disk at all) has nothing to move, and a second
    `migrate` run over an already-moved tree finds nothing left to plan
    either.
    """
    wdd_dir = Path(wdd_dir)
    epic_dir = wdd_dir / "epics" / slug
    moves: list[tuple[Path, Path]] = []

    for name in ("spec.md", "design.md", "plan.json"):
        source = wdd_dir / name
        if source.exists() and source.is_file():
            moves.append((source, epic_dir / name))

    tasks_dir = wdd_dir / "tasks"
    if tasks_dir.is_dir():
        for path in sorted(tasks_dir.rglob("*")):
            if path.is_file():
                relative = path.relative_to(wdd_dir)
                moves.append((path, epic_dir / relative))

    seen_research = {source for source, _ in moves}
    for relative_str in _research_artifact_paths(state):
        source = wdd_dir / relative_str
        if source in seen_research:
            continue
        if source.exists() and source.is_file():
            moves.append((source, epic_dir / relative_str))
            seen_research.add(source)

    return moves


def _refuse_reserved_collisions(moves: list[tuple[Path, Path]]) -> None:
    """Reserved-name refusal (spec Sec4): "v5 permitted artifacts literally
    named record.json. Migration refuses to move a file onto the reserved
    ... path, naming the file and a rename remedy." Checked against the
    resolver's own any-depth rule (paths.py's `_RESERVED_BASENAMES`), not
    only the bare top-level case, so nothing migration moves ever becomes
    permanently unreferenceable through `resolve_artifact` afterwards.
    """
    for source, destination in moves:
        if destination.name == _RESERVED_BASENAME:
            raise ValidationError(
                f"migration cannot move {source} to {destination}: "
                f"{_RESERVED_BASENAME!r} is a reserved filename under an epic directory; "
                f"rename {source.name} to something else and re-run migrate"
            )


def _execute_file_moves(moves: list[tuple[Path, Path]]) -> None:
    for source, destination in moves:
        if not source.exists():
            # Idempotent re-run: an earlier migrate attempt already moved
            # this file (or it never existed in this fixture).
            continue
        if destination.exists():
            raise ValidationError(
                f"migration destination already exists: {destination} (from {source})"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))


def _write_attempt_manifests(wdd_dir: Path, state: dict[str, Any]) -> None:
    """Write `manifest.json` naming the brief file into every EXISTING
    attempt snapshot dir (spec Sec4). Snapshots are immutable and left in
    place; the manifest lets `runner.py`'s dispatch assembler identify the
    brief without matching by lexicographic guessing. Idempotent: a
    manifest already present (an earlier migrate, or a re-run) is left
    untouched rather than rewritten.
    """
    wdd_dir = Path(wdd_dir)
    dispatch_root = wdd_dir / "dispatch"
    for task_id, task in state.get("tasks", {}).items():
        brief = str(task["specPath"]).split("#", 1)[0]
        # Every attempt dir for the task, not just the current `snapshot`
        # pointer (spec Sec4 "each existing attempt dir"): superseded
        # attempts from rebinds/re-dispatches share the sanitized-id
        # prefix with a numeric suffix.
        candidates: list[Path] = []
        snapshot = task.get("snapshot")
        if snapshot and (wdd_dir / snapshot).is_dir():
            candidates.append(wdd_dir / snapshot)
        sanitized = _sanitize_task_id_for_filename(task_id)
        if dispatch_root.is_dir():
            for entry in sorted(dispatch_root.glob(f"{sanitized}-*")):
                if entry.is_dir() and entry not in candidates:
                    suffix = entry.name[len(sanitized) + 1 :]
                    if suffix.isdigit():
                        candidates.append(entry)
        for snapshot_dir in candidates:
            manifest_path = snapshot_dir / _MANIFEST_NAME
            if manifest_path.exists():
                continue
            os.chmod(snapshot_dir, _ATTEMPT_DIR_MODE)
            atomic_write_text(
                manifest_path, json.dumps({"brief": brief}, indent=2, sort_keys=True) + "\n"
            )
            os.chmod(manifest_path, _ATTEMPT_FILE_MODE)


def _load_layers_for_migration(wdd_dir: Path, slug: str) -> dict[str, Any]:
    """`load_layers`, tolerant of a repo that never adopted `config.json`
    (a v2/v3/v4-origin source predating phase-6c's governance split, still
    reachable by `convert()`'s composed chain). Mirrors `merge_settings`'s
    documented "config=None models a legacy repo" doctrine: effective is
    just the built-in default, since there is no global config or overlay
    to layer over it.
    """
    if not config_path(wdd_dir).exists():
        defaults = default_config()
        return {"defaults": defaults, "global": defaults, "overlay": {}, "effective": defaults}
    return load_layers(wdd_dir, slug)


def _resolve_review_model_for_stamp(
    task: dict[str, Any], effective_config: dict[str, Any]
) -> str | None:
    """The reviewModel a task review is stamped with at migration time:
    task override -> risk-tiered `models.review` -> None. Mirrors
    `engine._resolve_model`/`runner._resolve_dispatch_model`'s identical
    precedence for the reviewer role (their own cross-reference comments
    ask to keep the two in step; this is a third hand-synced copy for the
    one-shot migration case, since neither of those two is reachable
    without an `action`/dispatch context).
    """
    override = task.get("reviewModel")
    if isinstance(override, str) and override:
        return override
    review = (effective_config.get("models") or {}).get("review")
    tier = "highRisk" if task.get("risk") == "high" else "default"
    value = review.get(tier) if isinstance(review, dict) else review
    return value if isinstance(value, str) and value else None


def convert_v5_to_v6(state: dict[str, Any], *, wdd_dir: Path | str) -> dict[str, Any]:
    """v5 -> v6 (spec Sec4's per-field migration table). PURE: computes and
    fully validates the migrated state -- including checking that no planned
    file move would land on the reserved `record.json` name -- but never
    touches the filesystem for writes (only reads, to check what exists and
    to compute the migration-time config digest). This is what makes
    `plan_migration`/`--dry-run` safe to call: no side effects survive a
    preview. `apply_migration` is the only caller that goes on to actually
    execute the file moves and manifest writes this function only PLANS,
    once the whole conversion has validated in memory.
    """
    if state.get("schemaVersion") != _V5_SCHEMA_VERSION:
        raise ValidationError(
            f"convert_v5_to_v6 requires a v{_V5_SCHEMA_VERSION}-shaped state, got "
            f"{state.get('schemaVersion')!r}"
        )
    wdd_dir = Path(wdd_dir)
    migrated = deepcopy(state)
    migrated["schemaVersion"] = _V6_SCHEMA_VERSION

    slug = _epic_slug_for_migration(migrated)
    migrated["epic"] = slug
    migrated.setdefault("archivePending", None)
    migrated.setdefault("archiveBlocked", None)

    moves = plan_v6_file_moves(wdd_dir, slug, migrated)
    _refuse_reserved_collisions(moves)

    layers = _load_layers_for_migration(wdd_dir, slug)
    effective = layers["effective"]
    full_digest = effective_config_digest(effective)

    # Exemption shape (spec Sec4): BOTH a non-legacy scope (which keeps its
    # real spec/research/design records) and a legacy scope (which keeps
    # `intake.legacy`) gain the identical `configure: {"legacy": true,
    # "sha256": ...}` stamp -- the exemption covers only the missing human
    # attribution; drift is still guarded ordinarily from here on. Migration
    # is the sole producer of this shape; constructors never mint it
    # (schema.py's `_validate_configure`).
    intake = dict(migrated.get("intake") or {})
    intake["configure"] = {"legacy": True, "sha256": full_digest}
    migrated["intake"] = intake

    task_review_digest = effective_config_digest(project(effective, "taskReview"))
    task_verification_digest = effective_config_digest(project(effective, "taskVerification"))
    for task in migrated.get("tasks", {}).values():
        review = task.get("review")
        if isinstance(review, dict):
            review["resolvedRisk"] = task.get("risk", "normal")
            review["reviewModel"] = _resolve_review_model_for_stamp(task, effective)
            review["configSha256"] = task_review_digest
        verification = task.get("verification")
        if isinstance(verification, dict):
            verification["configSha256"] = task_verification_digest

    finalize = migrated.get("finalize")
    if isinstance(finalize, dict):
        final_review = finalize.get("review")
        if isinstance(final_review, dict):
            review_model = (effective.get("models") or {}).get("review")
            final_review["reviewModel"] = (
                review_model if isinstance(review_model, str) and review_model else None
            )
            final_review["configSha256"] = effective_config_digest(project(effective, "finalReview"))
        final_verification = finalize.get("verification")
        if isinstance(final_verification, dict):
            # Deliverable-command-inclusive digest (Task 5, spec Sec2:
            # "verification.*, plus the deliverable command for final") --
            # `_final_verification_projection_digest` is the ONE
            # implementation finalize.py's evidence recording/gates also use,
            # imported here rather than reimplemented (config.project() has
            # no state access, so it cannot fold this in itself).
            deliverable_command = ((migrated.get("intake") or {}).get("design") or {}).get(
                "deliverableCommand"
            )
            final_verification["configSha256"] = _final_verification_projection_digest(
                effective, deliverable_command
            )

    # `migrated` is intentionally v6-shaped here (an intermediate this
    # function's own callers may inspect before the v6 -> v7 bump runs --
    # see `MigrateComposedFromEarlierVersionsTest`), but `validate_state`
    # only ever accepts the CURRENT schema version. Validate the shape by
    # checking it against the exact dict `convert_v6_to_v7` would produce
    # from it (current version + the pure-bump `parked: {}`) without
    # mutating or returning that bumped copy -- everything BUT the version
    # bump and `parked` is checked for real.
    validate_state({**migrated, "schemaVersion": SCHEMA_VERSION, "parked": {}})
    return migrated


def convert_v6_to_v7(state: dict[str, Any]) -> dict[str, Any]:
    """v6 -> v7 (spec "Schema v7"): a pure version bump plus `parked: {}` --
    no file moves, mirroring the v3 -> v4 and v4 -> v5 pure-bump steps
    above. Constructors never mint parked entries (schema.py); migration's
    only contribution here is the empty map every v6 source predates.
    """
    if state.get("schemaVersion") != _V6_SCHEMA_VERSION:
        raise ValidationError(
            f"convert_v6_to_v7 requires a v{_V6_SCHEMA_VERSION}-shaped state, got "
            f"{state.get('schemaVersion')!r}"
        )
    migrated = deepcopy(state)
    migrated["schemaVersion"] = SCHEMA_VERSION
    migrated.setdefault("parked", {})
    validate_state(migrated)
    return migrated


def _migration_notes(
    migrated: dict[str, Any], *, review_policy: str, source_version: int | None = None
) -> list[str]:
    if source_version == _V6_SCHEMA_VERSION:
        # v6 -> v7 is a pure version bump (spec "Schema v7"): no waves/risk/
        # worktree/artifact remap ever applied to a source this recent, and
        # no files move -- the only real note is the schema bump itself.
        return ["schemaVersion bumped to 7 with an empty parked map; no files moved"]
    return [
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
        f"epic-owned artifacts move into epics/{migrated['epic']}/ (spec Sec4)",
    ]


def plan_migration(path: Path | str, *, review_policy: str = "always") -> dict[str, Any]:
    """Dry-run preview: reads and converts (`convert()` is pure -- see its
    docstring and `convert_v5_to_v6`'s), never writes state.json or moves a
    single file. `wddctl migrate --dry-run` calls this alone.
    """
    path = Path(path)
    source = read_source(path)
    migrated = convert(source, review_policy=review_policy, wdd_dir=path.parent)
    return {
        "state": str(path),
        "from": source.get("schemaVersion"),
        "to": SCHEMA_VERSION,
        "tasks": sorted(migrated["tasks"]),
        "backup": str(path.with_suffix(path.suffix + ".v2.bak")),
        "notes": _migration_notes(
            migrated, review_policy=review_policy, source_version=source.get("schemaVersion")
        ),
    }


def apply_migration(path: Path | str, *, review_policy: str = "always") -> dict[str, Any]:
    """Convert once (pure, fully validated in memory), THEN -- and only
    then -- perform the v5 -> v6 physical side effects (file moves +
    attempt-snapshot manifests) this same conversion already planned and
    reserved-name-checked, and finally back up and atomically overwrite
    state.json. `convert()` itself is never called twice: unlike the pure
    v2/v3/v4 -> v5 steps, the v5 -> v6 step's move-planning reads the
    filesystem, so calling it twice would plan (and, worse, execute) against
    a tree that already moved once.
    """
    path = Path(path)
    wdd_dir = path.parent
    # Serialized against another migrate: read, move files, back up, and
    # write must be one step, or a second migration can race the first.
    # Normal commands cannot race this, since they reject a non-current
    # schema state on read.
    with StateStore(path).locked():
        source = read_source(path)
        source_version = source.get("schemaVersion")
        migrated = convert(source, review_policy=review_policy, wdd_dir=wdd_dir)

        # The v5 -> v6 physical side effects (file moves, attempt-snapshot
        # manifests, a fresh empty overlay) apply ONLY to a source that
        # actually predates the epics/<slug>/ layout (v2..v5) -- a v6
        # SOURCE already has its artifacts under epics/<slug>/ and a real
        # overlay (possibly non-empty) that a blind `save_overlay(..., {})`
        # here would silently destroy. The v6 -> v7 step is a pure version
        # bump (spec "Schema v7": "no file moves"), so a v6 source takes
        # none of these side effects.
        if source_version != _V6_SCHEMA_VERSION:
            moves = plan_v6_file_moves(wdd_dir, migrated["epic"], migrated)
            _refuse_reserved_collisions(moves)
            _execute_file_moves(moves)
            _write_attempt_manifests(wdd_dir, migrated)
            # `create_epic` always mints an empty overlay (`config.json`, `{}`)
            # alongside a fresh `epics/<slug>/` -- a migrated epic dir had none
            # (v5 predates the overlay split), so write the identical empty
            # shape here too, or a migrated epic's directory disagrees with
            # every other epic's shape for no reason. Semantically inert: a
            # missing overlay file already reads as `{}` (`load_overlay`'s own
            # doctrine), which is exactly what `_load_layers_for_migration`
            # used above to compute `full_digest` -- writing the same `{}` to
            # disk now changes no digest already stamped into `migrated`.
            save_overlay(wdd_dir, migrated["epic"], {})

        backup = path.with_suffix(path.suffix + ".v2.bak")
        shutil.copy2(path, backup)
        atomic_write_text(path, json.dumps(migrated, indent=2, sort_keys=True) + "\n")
    return {
        "state": str(path),
        "from": source.get("schemaVersion"),
        "to": SCHEMA_VERSION,
        "tasks": sorted(migrated["tasks"]),
        "backup": str(backup),
        "notes": _migration_notes(
            migrated, review_policy=review_policy, source_version=source_version
        ),
        "applied": True,
    }
