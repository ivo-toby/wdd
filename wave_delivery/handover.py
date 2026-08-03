"""Immutable attempt snapshots and recorded input digests (front-half spec Sec3).

"Handover itself is immutable": at `start` the task's brief and every
context-ref file are copied into a read-only attempt snapshot under
`.wdd/dispatch/<task>-<attempt>/`, and the digests of the SOURCE files are
recorded on the task. Workers (and, later, reviewers) receive snapshot
paths, never live controller files -- a file edited (or edited-and-restored)
after validation cannot reach a worker. `.wdd/dispatch/` is transient
scratch: gitignored, and never a place durable state lives.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from .engine import apply_mutation, utc_now
from .errors import IllegalTransition, ValidationError
from .intake import artifact_sha256, resolve_within_wdd
from .paths import resolve_artifact
from .schema import copied_state
from .store import StateStore, atomic_write_text


# Same character class as finalize.py's _sanitize_scope_id_for_filename (the
# archive idiom, per Global Constraints): task ids double as filesystem path
# components under .wdd/dispatch/, so anything outside [A-Za-z0-9._-] is
# replaced rather than trusted verbatim.
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")

_DISPATCH_GITIGNORE_ENTRY = "dispatch/"

_ATTEMPT_DIR_MODE = 0o700
_ATTEMPT_FILE_MODE = 0o400

# Bound on attempt-number collision retries (see materialize_attempt): a
# generous ceiling for a scenario that should be rare (a stray leftover dir,
# a concurrent racer), just large enough that it never bites a legitimate
# caller while still guaranteeing termination.
_MAX_ATTEMPT_DIR_RETRIES = 100


def _sanitize_task_id_for_filename(task_id: str) -> str:
    sanitized = _UNSAFE_FILENAME_CHARS.sub("_", task_id)
    if not sanitized:
        raise ValidationError(f"task id sanitizes to an empty dispatch dirname: {task_id!r}")
    return sanitized


def ensure_dispatch_gitignore(wdd_dir: Path | str) -> bool:
    """Ensure `.wdd/.gitignore` ignores the transient `dispatch/` scratch dir.

    Idempotent and content-preserving: called by both `init` and
    `migrate --governance` (Global Constraints), neither of which should
    duplicate the entry or clobber a `.gitignore` a user already edited.
    Returns True when the file was created or the entry was appended.
    """
    wdd_dir = Path(wdd_dir)
    gitignore = wdd_dir / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if _DISPATCH_GITIGNORE_ENTRY in existing.splitlines():
        return False
    content = existing
    if content and not content.endswith("\n"):
        content += "\n"
    content += _DISPATCH_GITIGNORE_ENTRY + "\n"
    atomic_write_text(gitignore, content)
    return True


def _next_attempt_number(dispatch_dir: Path, sanitized_task_id: str) -> int:
    """1 + the count of existing attempt dirs for this task (plan's algorithm).

    A literal count, not a max()+1 scan: attempt dirs are never removed, so
    the two agree in practice, but the plan specifies the count.
    """
    prefix = f"{sanitized_task_id}-"
    existing = [
        entry for entry in dispatch_dir.glob(f"{prefix}*")
        if entry.is_dir() and entry.name[len(prefix):].isdigit()
    ]
    return 1 + len(existing)


def _task_input_sources(
    state: dict[str, Any], wdd_dir: Path, task_id: str
) -> list[Path]:
    """Resolve a task's brief + context-ref files to absolute paths, deduped.

    Shared by `materialize_attempt` (copies them into a snapshot) and
    `rebind_attempt` (Task 3: re-hashes them in place against current bytes)
    -- both need the identical source-file resolution, so a re-approval that
    adds or removes a context ref is picked up the same way by either path.
    Anchors (`#...`) are stripped for file resolution -- centrally, inside
    the one typed resolver (`wave_delivery/paths.py`'s `resolve_artifact`,
    spec Sec1, Global Constraints), reached here via
    `intake.resolve_within_wdd`'s label-preserving wrapper. A file
    referenced twice (brief == a context ref, or a duplicate context ref)
    appears once, in first-seen order (brief first, then context refs in
    plan order).
    """
    try:
        task = state["tasks"][task_id]
    except KeyError as error:
        raise ValidationError(f"unknown task: {task_id}") from error

    sources: list[Path] = []
    seen: set[Path] = set()

    def _add(raw_path: str, *, label: str) -> None:
        resolved = resolve_within_wdd(wdd_dir, raw_path, label=label)
        if not resolved.exists() or not resolved.is_file():
            raise ValidationError(f"{label} does not exist or is not a regular file: {raw_path}")
        if resolved not in seen:
            seen.add(resolved)
            sources.append(resolved)

    _add(task["specPath"], label="task brief")
    for ref in task.get("context") or []:
        _add(ref, label="context ref")
    return sources


def materialize_attempt(
    state: dict[str, Any], wdd_dir: Path | str, task_id: str
) -> dict[str, Any]:
    """Copy a task's brief + context-ref files into a fresh, read-only attempt dir.

    Returns ``{"snapshot": <.wdd-relative dir path>, "inputs": [{"path", "sha256"}, ...]}``.
    ``inputs`` digests are of the SOURCE files (the live `.wdd` copies), not
    the snapshot copies, and paths are `.wdd`-relative -- the same doctrine
    `plan_composite`/research artifacts already use. Anchors (`#...`) are
    stripped for file resolution; a file referenced twice (brief == a context
    ref, or a duplicate context ref) copies once and appears once in `inputs`.
    """
    wdd_dir = Path(wdd_dir)
    wdd_resolved = wdd_dir.resolve()
    sources = _task_input_sources(state, wdd_dir, task_id)

    # Built from wdd_resolved (not the possibly-relative wdd_dir): every
    # downstream path (attempt_dir, per-file destinations) must be absolute
    # so the final `attempt_dir.relative_to(wdd_resolved)` below is a
    # valid comparison regardless of whether the caller passed a relative
    # or absolute wdd_dir -- the CLI's own default `--state` resolves to
    # the relative `.wdd/state.json`, so this is the common case, not an
    # edge case.
    dispatch_dir = wdd_resolved / "dispatch"
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(dispatch_dir, _ATTEMPT_DIR_MODE)
    # An existing install that predates the dispatch/ scratch dir (created
    # via `wddctl migrate`'s schema path, never through `migrate_governance`)
    # never gets this entry otherwise: migrate_governance early-returns
    # before it once config.json already exists. Ensuring it here, at the
    # point dispatch/ actually starts existing, is idempotent and
    # content-preserving either way.
    ensure_dispatch_gitignore(wdd_resolved)

    sanitized = _sanitize_task_id_for_filename(task_id)
    attempt = _next_attempt_number(dispatch_dir, sanitized)
    # mkdir(exist_ok=False) is the collision check, but the glob count above
    # is not atomic with it -- a stray leftover dir or a concurrent racer can
    # already occupy the counted number. Retry the next number on collision
    # (bounded, so a pathological all-taken run fails cleanly instead of
    # looping forever) rather than let a raw FileExistsError escape.
    attempt_dir: Path | None = None
    for _ in range(_MAX_ATTEMPT_DIR_RETRIES):
        candidate = dispatch_dir / f"{sanitized}-{attempt}"
        try:
            candidate.mkdir(parents=False, exist_ok=False)
        except FileExistsError:
            attempt += 1
            continue
        attempt_dir = candidate
        break
    if attempt_dir is None:
        raise ValidationError(
            f"could not allocate an attempt dir for task {task_id!r} after "
            f"{_MAX_ATTEMPT_DIR_RETRIES} collision retries"
        )
    os.chmod(attempt_dir, _ATTEMPT_DIR_MODE)

    inputs: list[dict[str, str]] = []
    for resolved in sources:
        relative = resolved.relative_to(wdd_resolved)
        destination = attempt_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resolved, destination)
        os.chmod(destination, _ATTEMPT_FILE_MODE)
        inputs.append({"path": str(relative), "sha256": artifact_sha256(resolved)})

    snapshot = str(attempt_dir.relative_to(wdd_resolved))
    return {"snapshot": snapshot, "inputs": inputs}


def record_attempt(
    store: StateStore,
    *,
    task_id: str,
    snapshot: str,
    inputs: list[dict[str, str]],
    idempotency_key: str | None = None,
    expected_revision: int | None = None,
) -> tuple[dict[str, Any], bool]:
    """Record a materialized attempt's snapshot path (+ inputs) on the task.

    One `apply_mutation` (`task.dispatched`), so the recording is atomic --
    called only after `materialize_attempt` succeeds, itself only ever
    called after a successful `start` (never before a failed one).

    Legacy scopes (`intake.legacy`) are exempt from input-version binding
    (spec Sec3's doctrine postdates them): the snapshot still lands, useful
    and harmless, but `inputs` is recorded empty rather than binding
    evidence to a doctrine the scope never opted into. This does not use
    `transition()` -- `task.dispatched` isn't a status-machine event, the
    same way `governance.migrated`'s direct mutator is not.
    """

    def mutator(state: dict[str, Any]) -> dict[str, Any]:
        if task_id not in state["tasks"]:
            raise ValidationError(f"unknown task: {task_id}")
        updated = copied_state(state)
        legacy = (state.get("intake") or {}).get("legacy") is True
        task = updated["tasks"][task_id]
        task["snapshot"] = snapshot
        task["inputs"] = [] if legacy else list(inputs)
        return updated

    return apply_mutation(
        store,
        event_type="task.dispatched",
        task_id=task_id,
        data={"snapshot": snapshot},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )


def inputs_status(
    state: dict[str, Any], wdd_dir: Path | str, task_id: str
) -> dict[str, str] | None:
    """The task's input-version-binding status (spec Sec3).

    None when the task has no recorded ``inputs`` (a legacy scope, a pre-6b
    task, or one that has never started -- `record_attempt` is what first
    populates it) or when every recorded digest still matches the CURRENT
    bytes at that path. Otherwise, the FIRST mismatch in recorded order, as
    ``{"path", "recorded", "actual"}``; a since-deleted file reports
    ``"actual": "missing:<path>"`` rather than raising.

    Deliberately compares the RECORDED (path, sha256) pairs as-is, not a
    freshly re-derived source set (`_task_input_sources`): a plan
    re-approval that only touched some OTHER task's brief or context must
    not perturb this task's own gate. The composite gates the plan; these
    per-task digests gate the task -- keeping that boundary exact is the
    point of testing against what was actually recorded, not what the task's
    fields currently say to resolve.

    Load-bearing invariant this relies on: comparing the recorded pairs
    as-is is only sound because `plan.py`'s `_apply_plan_to_state` refuses
    to change a task's `specPath`/`context` (both in `MUTABLE_TASK_FIELDS`)
    once the task has left `todo` -- so the RECORDED paths and the task's
    CURRENT `specPath`/`context` fields can never diverge for a
    started-or-later task. If that refusal were ever relaxed, a task could
    be re-pointed at different source files after `start` while this
    function keeps checking bytes at the OLD paths, silently no longer
    covering the files the task actually now names -- an evidence hole, not
    a mismatch this function would ever surface.
    """
    wdd_dir = Path(wdd_dir)
    try:
        task = state["tasks"][task_id]
    except KeyError as error:
        raise ValidationError(f"unknown task: {task_id}") from error

    recorded = task.get("inputs") or []
    if not recorded:
        return None

    # Cross-reference: recorded input paths are namespace-relative refs and
    # MUST resolve through the one typed resolver (wave_delivery/paths.py,
    # spec Sec1) — a raw join here reads flat paths and false-flags every
    # started task after the v6 migration moves artifacts under
    # epics/<slug>/ (caught empirically in Task 3 review).
    epic = state.get("epic")
    for entry in recorded:
        path = entry["path"]
        recorded_sha = entry["sha256"]
        try:
            candidate = resolve_artifact(path, wdd_dir=wdd_dir, epic=epic)
        except ValidationError:
            return {"path": path, "recorded": recorded_sha, "actual": f"missing:{path}"}
        if not candidate.exists() or not candidate.is_file():
            return {"path": path, "recorded": recorded_sha, "actual": f"missing:{path}"}
        actual_sha = artifact_sha256(candidate)
        if actual_sha != recorded_sha:
            return {"path": path, "recorded": recorded_sha, "actual": actual_sha}
    return None


def rebind_attempt(
    store: StateStore,
    *,
    task_id: str,
    wdd_dir: Path | str,
    by: str,
    idempotency_key: str | None = None,
    expected_revision: int | None = None,
) -> tuple[dict[str, Any], bool]:
    """Re-record a task's input digests against CURRENT bytes.

    The recorded human decision (spec Sec3) that a task's in-flight attempt
    and its unmerged evidence still stand, despite the recorded digests no
    longer matching -- the escape hatch alongside re-dispatch (a fresh
    `start`). Refuses ("nothing to rebind") when `inputs_status` is already
    None: a legacy/pre-6b task, one that never started, and one whose
    recorded digests already match current bytes all have nothing to
    re-bind, and are refused the same way.

    Re-resolves the task's CURRENT source set (`_task_input_sources`) rather
    than only re-hashing the previously recorded paths, so a re-approval
    that also added or removed a context ref is picked up too -- the same
    resolution `materialize_attempt` uses, just re-hashing in place instead
    of copying into a new snapshot (rebind does not mint a new attempt dir;
    the existing snapshot and worktree are exactly what the human is
    vouching still stands).
    """
    if not by:
        raise ValidationError("rebind requires --by")
    wdd_dir = Path(wdd_dir)

    def mutator(state: dict[str, Any]) -> dict[str, Any]:
        if inputs_status(state, wdd_dir, task_id) is None:
            raise IllegalTransition(
                f"nothing to rebind for task {task_id}: it has no recorded inputs, or its "
                "recorded digests already match the current bytes"
            )
        sources = _task_input_sources(state, wdd_dir, task_id)
        wdd_resolved = wdd_dir.resolve()
        new_inputs = [
            {"path": str(source.relative_to(wdd_resolved)), "sha256": artifact_sha256(source)}
            for source in sources
        ]
        updated = copied_state(state)
        updated["tasks"][task_id]["inputs"] = new_inputs
        # The event log's `data` never lands in state.json (engine.py's
        # apply_mutation records only revision/type/task/idempotencyKey/at --
        # `by` feeds the idempotency key derivation and nothing else), so the
        # human attribution `rebind` requires (--by) must be persisted here,
        # on the task itself, the same {by, at} idiom plan.py's
        # `_stamp_approval` uses for plan-approval.
        updated["tasks"][task_id]["rebound"] = {"by": by, "at": utc_now()}
        return updated

    return apply_mutation(
        store,
        event_type="task.rebound",
        task_id=task_id,
        data={"by": by, "at": utc_now()},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )
