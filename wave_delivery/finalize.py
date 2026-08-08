"""Scope-level finalize verbs: review, verify, handoff, delivered.

Once every task in a scope reaches a terminal status, wddctl stops managing
individual tasks and starts choreographing the scope itself, per Spec §6: a
final review of the whole epic branch against ``.wdd/spec.md``, a full
verification run, a handoff PR to the human who performs the actual merge,
and an observed-delivery record once that merge has genuinely happened.

Evidence here mirrors review.py's task-level contract (validate findings,
pin evidence to a SHA, invalidate it once that SHA moves) but at scope
granularity: there is no task head left to pin against once every task is
terminal, so review and verification are pinned to the scope's CURRENT
base-branch head SHA instead -- the whole epic branch is what final review
and verification examine, and any new commit on it invalidates the recorded
evidence, same doctrine as task evidence.

The four event types (final.review_recorded, final.verification_recorded,
handoff.prepared, scope.delivered) are scope-level, not task-level, so their
mutators write directly into ``state["finalize"]`` rather than going through
``engine.transition`` (which only knows the per-task and constitution/
reconcile vocabulary). This is the same "handwritten mutator, apply_mutation
supplies the revisioned/idempotent/locked envelope" pattern
``setup.migrate_governance`` and ``merge.observe_merge`` already use.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from collections import Counter
from pathlib import Path
from typing import Any

from .config import config_path, effective_config_digest, load_config, merge_settings, project
from .engine import apply_mutation, event_id, utc_now
from .errors import IllegalTransition, RevisionConflict, ValidationError
from .git import (
    integration_worktree_path,
    is_ancestor,
    require_repository,
    resolve_ref,
    run_git,
    worktree_at,
)
from .github import create_pr, push_branch
from .merge import _fetched_base_refs
from .review import validate_findings
from .schema import copied_state, derived_phase, new_setup_state
from .store import StateStore, atomic_write_text


FINALIZE_PHASES = {"finalize", "delivered"}

# `epics/<slug>/` -> `archive/<slug>/` (spec Sec1): the slug -- not
# scope.id -- has governed archive's on-disk path since Task 6. Slugs are
# validated against EPIC_SLUG_PATTERN at `epic new` time (setup.create_epic),
# so no traversal/sanitization concern exists here the way it did for the
# pre-Task-6 flat `archive/<scope-id>.json` layout (scope.id, by contrast,
# carries no character restriction at all -- it only ever appears as JSON
# payload data below, never as a path component).


def _final_review_model(effective: dict[str, Any]) -> str | None:
    """The model finalize review's evidence binds to (spec Sec2: "final
    review records its selected model"). No per-task risk tier exists at
    scope granularity, so unlike task review this does not tier by risk --
    matching `finalize_next_actions`'s existing (pre-Task-5) model-decoration
    behavior exactly: `models.review` is used only when it is a plain,
    non-empty string; a tiered object form yields no model at either site.
    """
    review_model = (effective.get("models") or {}).get("review")
    return review_model if isinstance(review_model, str) and review_model else None


def _final_review_evidence_binding(layers: dict[str, Any] | None) -> dict[str, Any]:
    if layers is None:
        return {}
    effective = layers["effective"]
    return {
        "reviewModel": _final_review_model(effective),
        "configSha256": effective_config_digest(project(effective, "finalReview")),
    }


def _final_verification_projection_digest(
    effective: dict[str, Any], deliverable_command: str | None
) -> str:
    """finalVerification's projection digest additionally covers the epic
    deliverable command (spec Sec2: "verification.*, plus the deliverable
    command for final"). `config.project()` has no access to state, so it
    cannot include this itself (see its own docstring, which explicitly
    defers this to Task 5/finalize.py). Folds the command in as an extra key
    alongside the projection before hashing with the same
    `effective_config_digest` -- still the one fingerprint implementation,
    just fed a slightly larger view for this one purpose. `migration.py`'s
    v5->v6 stamp uses this same helper so the migration-time digest and a
    freshly recomputed one are never two different implementations.
    """
    projection = project(effective, "finalVerification")
    return effective_config_digest({**projection, "deliverableCommand": deliverable_command})


def _final_verification_evidence_binding(
    layers: dict[str, Any] | None, state: dict[str, Any]
) -> dict[str, Any]:
    if layers is None:
        return {}
    deliverable_command = ((state.get("intake") or {}).get("design") or {}).get(
        "deliverableCommand"
    )
    return {
        "configSha256": _final_verification_projection_digest(
            layers["effective"], deliverable_command
        ),
    }


def _require_finalize_phase(state: dict[str, Any]) -> None:
    phase = derived_phase(state)
    if phase not in FINALIZE_PHASES:
        raise IllegalTransition(
            "finalize verbs require the scope to be in the finalize or delivered phase "
            f"(it is {phase}); every task must reach done or cancelled first"
        )


def _require_not_delivered(state: dict[str, Any], *, what: str) -> None:
    """Refuse a mutating finalize verb once the scope is already delivered.

    ``_require_finalize_phase`` alone is not enough: it deliberately treats
    "delivered" as a legal phase (so ``finalize status`` keeps working), but
    once ``finalize.delivered`` is recorded the human's final merge has
    already happened -- re-recording review/verification is meaningless, and
    re-running handoff would re-push an already-merged branch and (on the pr
    surface) open a second, genuinely duplicate PR against a landed branch.
    """
    if (state.get("finalize") or {}).get("delivered"):
        raise IllegalTransition(
            f"this scope is already delivered; there is nothing left to {what}"
        )


def _base_ref(state: dict[str, Any]) -> str:
    base_ref = (state.get("scope") or {}).get("baseRef")
    if not isinstance(base_ref, str) or not base_ref:
        raise IllegalTransition("this scope has no configured base ref")
    return base_ref


def _require_something_to_deliver(repo_path: Path, base_sha: str, target_branch: str) -> None:
    """Refuse when the epic branch has no commits beyond its merge-base with the target.

    ``is_ancestor`` treats a commit as its own ancestor, so this single check
    catches two vacuous-ancestry shapes the human-merge guarantee must not
    self-certify against:

    - scope.baseRef == branching.targetBranch (base_sha IS the target head).
    - A scope whose epic branch never received any merged work (e.g. every
      task cancelled): base_sha sits exactly at the merge-base with target,
      so it is already an ancestor of the target head with no human merge
      having happened.

    This runs at handoff time, BEFORE the human's final merge can possibly
    have landed -- a genuine handoff for real, unmerged work is always false
    here (the epic branch has diverged from target with real commits), so
    the normal flow (real commits -> handoff -> human merges -> delivered)
    is unaffected. Plan-time validation (cli.py's plan apply/lint) already
    refuses case one before any task ever starts; this is defense in depth
    plus the only place that can catch case two, which plan time cannot see.
    """
    if is_ancestor(repo_path, base_sha, target_branch):
        raise IllegalTransition(
            "nothing to deliver: the epic branch has no commits beyond its merge-base "
            f"with {target_branch}"
        )


def _require_handoff_recorded(state: dict[str, Any]) -> None:
    """`finalize delivered`'s other precondition: a handoff must exist first.

    Without this, a scope that never went through handoff (e.g. the
    all-cancelled walk finding 1's fix in ``_require_something_to_deliver``
    now blocks at handoff time) could still reach ``record_delivered``
    directly and self-certify the moment its base head happens to already be
    an ancestor of target -- exactly the vacuous case handoff itself now
    refuses. Requiring a recorded handoff means that refusal always fires
    first, for every path to delivered.
    """
    handoff = (state.get("finalize") or {}).get("handoff")
    if not isinstance(handoff, dict):
        raise IllegalTransition(
            "finalize delivered requires a recorded handoff; run 'wddctl finalize handoff "
            "--repo .' first"
        )


def _require_target_branch(
    wdd_dir: Path, layers: dict[str, Any] | None = None
) -> tuple[str, dict[str, Any]]:
    """`branching.targetBranch` plus the config view callers pass on to
    `merge_settings` for the handoff surface. `layers`, when given, is the
    caller's already-resolved admission snapshot (spec Sec2 resolve-once,
    fix-round F2): its `effective` view is used instead of a second bare
    `load_config` read, so an active epic's `merge.surface` override
    (`branching.targetBranch` itself is not epic-overlay-allowed, so it is
    identical either way) actually reaches the handoff/delivered surface
    computation. Callers with no snapshot fall back to a fresh global read.
    """
    if not config_path(wdd_dir).exists():
        raise IllegalTransition(
            "this scope predates config.json, so it has no configured "
            "branching.targetBranch; run 'wddctl migrate --governance' to adopt "
            "config.json (preserving any legacy model settings), then re-run this command"
        )
    config = layers["effective"] if layers is not None else load_config(wdd_dir)
    return config["branching"]["targetBranch"], config


def _blocking_severities(wdd_dir: Path, effective: dict[str, Any] | None = None) -> set[str]:
    """`review.blockingSeverities`. `effective`, when given, is an already-
    resolved admission snapshot's merged view (spec Sec2 resolve-once) --
    this key is NOT epic-overlay-allowed (config.py's OVERLAY_ALLOWED_LEAVES),
    so passing it only avoids a second config.json read within the same
    command, not a missed override. Callers with no snapshot (`next`'s
    read-only rendering) still get a fresh global read.
    """
    if effective is not None:
        return set(effective["review"]["blockingSeverities"])
    if not config_path(wdd_dir).exists():
        return {"P1", "P2"}
    return set(load_config(wdd_dir)["review"]["blockingSeverities"])


def finalize_status(state: dict[str, Any]) -> dict[str, Any]:
    return {"phase": derived_phase(state), "finalize": state.get("finalize") or {}}


def record_final_review(
    store: StateStore,
    *,
    findings: list[dict[str, Any]],
    reviewer: str,
    repo: Path | str,
    layers: dict[str, Any] | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Record the whole-epic-branch review Spec §6 calls for.

    Findings use the same P1/P2/P3 vocabulary as task review; the outcome is
    "blocked" the moment any finding's severity is in the configured
    ``review.blockingSeverities`` (default P1/P2).
    """
    if not isinstance(reviewer, str) or not reviewer:
        raise ValidationError("reviewer must be a non-empty string")
    validated = validate_findings(findings)
    repo_path = require_repository(repo)
    blocking = _blocking_severities(store.path.parent, layers["effective"] if layers else None)

    # Fail fast, before any (currently cheap, but not guaranteed to stay so)
    # work below runs -- mirrors the pre-lock check prepare_handoff and
    # record_delivered already do ahead of their own side effects.
    state = store.read()
    _require_finalize_phase(state)
    _require_not_delivered(state, what="review")

    def mutator(state: dict[str, Any]) -> dict[str, Any]:
        _require_finalize_phase(state)
        _require_not_delivered(state, what="review")
        base_sha = resolve_ref(repo_path, _base_ref(state))
        outcome = "blocked" if any(f["severity"] in blocking for f in validated) else "passed"
        updated = copied_state(state)
        updated.setdefault("finalize", {})
        updated["finalize"]["review"] = {
            "headSha": base_sha,
            "outcome": outcome,
            "findings": validated,
            "reviewer": reviewer,
            "at": utc_now(),
            **_final_review_evidence_binding(layers),
        }
        return updated

    return apply_mutation(
        store,
        event_type="final.review_recorded",
        task_id=None,
        data={},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )


def _is_legacy_intake(state: dict[str, Any]) -> bool:
    return (state.get("intake") or {}).get("legacy") is True


def _required_verification_commands(
    state: dict[str, Any], wdd_dir: Path, effective: dict[str, Any] | None = None
) -> list[str]:
    """The v5 non-legacy required command list, in order (spec Sec2/Sec5):
    the epic-effective ``verification.commands`` then the scope's epic
    deliverable command (``intake.design.deliverableCommand`` -- always
    present on a v5 non-legacy scope that reached finalize, since the intake
    ladder must be complete before `plan apply` and the ladder never clears
    without re-recording design).

    ``verification.commands`` IS epic-overlay-allowed (config.py's
    OVERLAY_ALLOWED_LEAVES): `effective`, when given, is an already-resolved
    admission snapshot's merged view (spec Sec2 resolve-once) and MUST be
    consulted instead of the bare global config, or an epic's own override
    would be silently ignored here while still feeding the
    finalVerification digest this same evidence recording stamps. Callers
    with no snapshot (`next`'s read-only rendering) fall back to a fresh
    global-only read, matching this function's behavior before Task 5.
    """
    if effective is not None:
        commands = list(effective["verification"]["commands"])
    elif config_path(wdd_dir).exists():
        commands = list(load_config(wdd_dir)["verification"]["commands"])
    else:
        commands = []
    design = (state.get("intake") or {}).get("design") or {}
    deliverable = design.get("deliverableCommand")
    if deliverable:
        commands.append(deliverable)
    return commands


def _validate_verification_results(
    results: Any, required_commands: list[str]
) -> list[dict[str, str]]:
    """Validate `--results` against the exact, ordered required command list.

    Missing, extra, or reordered entries all refuse, naming the mismatch --
    there is no partial-evidence state (spec Sec5: "append semantics are
    forbidden") and no room for the caller to skip or reorder a required
    command.
    """
    if not isinstance(results, list) or not results:
        raise ValidationError(
            "--results must be a non-empty JSON array of {command, status} objects"
        )
    entries: list[dict[str, str]] = []
    for item in results:
        if not isinstance(item, dict):
            raise ValidationError("each --results entry must be an object with command/status")
        command = item.get("command")
        status = item.get("status")
        if not isinstance(command, str) or not command:
            raise ValidationError("each --results entry requires a non-empty 'command'")
        if status not in {"passed", "failed", "unavailable"}:
            raise ValidationError(
                "each --results entry's status must be passed, failed, or unavailable"
            )
        entries.append({"command": command, "status": status})
    actual_commands = [entry["command"] for entry in entries]
    if actual_commands != required_commands:
        # Multiset diff (not plain membership): the required/deliverable
        # commands can repeat verbatim (e.g. the same smoke command used
        # globally and as the deliverable), so a plain `in` check would miss
        # a missing/extra *count* of an otherwise-present command.
        required_counts = Counter(required_commands)
        actual_counts = Counter(actual_commands)
        missing = list((required_counts - actual_counts).elements())
        extra = list((actual_counts - required_counts).elements())
        detail_parts = []
        if missing:
            detail_parts.append(f"missing: {missing}")
        if extra:
            detail_parts.append(f"extra: {extra}")
        if not missing and not extra:
            detail_parts.append(f"expected order {required_commands}, got {actual_commands}")
        raise ValidationError(
            "finalize verify record --results must name exactly the required commands, in "
            "order (the ratified global verification.commands then the scope's deliverable "
            "command): " + "; ".join(detail_parts)
        )
    return entries


def _overall_verification_status(entries: list[dict[str, str]]) -> str:
    """Overall status is `passed` only when every entry passed (spec Sec5);
    a failure outranks an unavailable entry when both are present, since
    "failed" is the stronger, more actionable signal for `next` to surface."""
    statuses = {entry["status"] for entry in entries}
    if statuses == {"passed"}:
        return "passed"
    if "failed" in statuses:
        return "failed"
    return "unavailable"


def verification_commands(verification: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Normalize `finalize.verification` evidence to its command list.

    v5 non-legacy records already carry ``commands``: ``[{command, status}, ...]``.
    Legacy (pre-phase-6a) records carry a single ``command``/``status`` pair at
    the top level; read sites see that as the one-entry list it always was,
    rather than special-casing the older shape at every call site.
    """
    if not verification:
        return []
    if "commands" in verification:
        return list(verification["commands"])
    if "results" in verification:
        # `--run`-fed evidence (`record_final_verification_run`): the rich
        # per-command executor shape, keyed `results` -- deliberately NOT
        # `commands`, since that key's validator (schema.py
        # `_validate_finalize_verification`) predates `--run` and only
        # accepts the reported two-key `{command, status}` entries (its
        # status vocabulary excludes "skipped", which AC-4 requires here
        # too); `results` reuses the already-`--run`-shaped validation
        # `_validate_verification_evidence_extras` gives task-level
        # evidence, task T3's own field name for the identical shape.
        # Normalized to the same two-key view this read site already
        # presents for the other shapes -- the executor-only fields
        # (exitCode/durationMs/outputSha256/tail) have no place in a
        # cosmetic command listing.
        return [
            {"command": entry.get("command"), "status": entry.get("status")}
            for entry in verification["results"]
        ]
    command = verification.get("command")
    if command is None:
        return []
    return [{"command": command, "status": verification.get("status")}]


def record_final_verification(
    store: StateStore,
    *,
    status: str | None = None,
    command: str | None = None,
    justification: str | None = None,
    results: list[dict[str, Any]] | None = None,
    repo: Path | str,
    layers: dict[str, Any] | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Record the full verification run against the current epic branch head.

    Two contracts, chosen by the scope's own legacy-ness (never by which
    arguments happen to be passed):

    - Legacy scopes (``intake.legacy``): the original single-command shape,
      unchanged bit-for-bit. Mirrors ``review.record_verification``'s status
      vocabulary; "unavailable" requires a justification (explicit or the
      configured ``verification.unavailableJustification`` fallback).
    - v5 non-legacy scopes: the spec Sec5 multi-command shape, recorded in
      ONE atomic ``--results`` invocation (append semantics are forbidden --
      partial evidence must never exist in state). Completeness is validated
      at record time: the entries must equal, exactly and in order, the
      required list (global ``verification.commands`` then the scope's
      deliverable command). Overall ``status`` is `passed` iff every entry
      passed.
    """
    wdd_dir = store.path.parent
    state = store.read()
    legacy = _is_legacy_intake(state)

    if legacy:
        if results is not None:
            raise ValidationError(
                "this is a legacy scope; record evidence with --status/--command "
                "(--results is the v5 multi-command contract)"
            )
        if status not in {"passed", "failed", "unavailable"}:
            raise ValidationError("verification status must be passed, failed, or unavailable")
        if status == "unavailable":
            if not justification:
                # verification.unavailableJustification IS epic-overlay-
                # allowed: prefer the already-resolved admission snapshot
                # (spec Sec2 resolve-once) over a second, non-epic-aware
                # config.json read.
                if layers is not None:
                    justification = layers["effective"]["verification"].get(
                        "unavailableJustification"
                    )
                elif config_path(wdd_dir).exists():
                    justification = load_config(wdd_dir)["verification"].get(
                        "unavailableJustification"
                    )
            if not justification:
                raise ValidationError(
                    "verification status 'unavailable' requires --justification (or a "
                    "configured verification.unavailableJustification)"
                )
    else:
        if status is not None or command is not None:
            raise ValidationError(
                "this is a v5 scope; record multi-command evidence with --results "
                "'[{\"command\":..., \"status\":...}, ...]' (--status/--command is the "
                "legacy contract)"
            )
        if results is None:
            raise ValidationError(
                "finalize verify record requires --results for this scope: the ratified "
                "global verification.commands plus the scope's deliverable command"
            )
        # Validated pre-lock so a bad --results refuses before any side effects;
        # re-validated inside the mutator below against the locked state, same
        # two-stage pattern as every other finalize verb in this module.
        _validate_verification_results(
            results,
            _required_verification_commands(state, wdd_dir, layers["effective"] if layers else None),
        )

    repo_path = require_repository(repo)

    # Fail fast, before any side effects run -- same two-stage pattern as
    # record_final_review/prepare_handoff/record_delivered.
    _require_finalize_phase(state)
    _require_not_delivered(state, what="verify")

    def mutator(state: dict[str, Any]) -> dict[str, Any]:
        _require_finalize_phase(state)
        _require_not_delivered(state, what="verify")
        base_sha = resolve_ref(repo_path, _base_ref(state))
        updated = copied_state(state)
        updated.setdefault("finalize", {})
        binding = _final_verification_evidence_binding(layers, state)
        if legacy:
            updated["finalize"]["verification"] = {
                "headSha": base_sha,
                "status": status,
                "command": command,
                "justification": justification,
                "at": utc_now(),
                **binding,
            }
        else:
            entries = _validate_verification_results(
                results,
                _required_verification_commands(
                    state, wdd_dir, layers["effective"] if layers else None
                ),
            )
            updated["finalize"]["verification"] = {
                "headSha": base_sha,
                "commands": entries,
                "status": _overall_verification_status(entries),
                "at": utc_now(),
                **binding,
            }
        return updated

    return apply_mutation(
        store,
        event_type="final.verification_recorded",
        task_id=None,
        data={},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )


def _finalize_integration_worktree(repo: Path, state: dict[str, Any], epic_head_sha: str) -> Path:
    """Where `finalize verify record --run` executes (spec AC-6): a
    dedicated integration worktree, DETACHED at the resolved epic head SHA
    -- never `repo` itself (the operator's own checkout), and never
    attached to `base_ref` by branch name the way `merge.py`'s
    `_integration_dir` reuses the operator checkout when it is already on
    that branch (exactly the checkout this must avoid: the operator's
    checkout commonly IS on the epic branch during finalize, and Git
    refuses to check the same branch out twice). Checking out the resolved
    SHA detached sidesteps that one-checkout-per-branch rule entirely --
    the same commit can be checked out on `base_ref` in the operator's
    checkout and, detached, in this worktree at the same time.

    Idempotent by force-recreation rather than reuse-and-verify: an
    existing worktree at this path is removed and rebuilt fresh on every
    call, so a prior `--run`'s untracked marker/output files never linger
    into the next one, and the post-checkout `resolve_ref` assertion below
    (reconciliation addendum 3) always confirms a checkout this function
    just performed, not a stale one from a previous invocation.
    """
    path = integration_worktree_path(repo, state["scope"]["id"])
    if worktree_at(repo, path) is not None:
        run_git(repo, "worktree", "remove", "--force", str(path))
    elif path.exists():
        raise ValidationError(
            f"integration worktree path exists but is not managed by Git: {path}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    run_git(repo, "worktree", "add", "--detach", str(path), epic_head_sha)
    actual = resolve_ref(path, "HEAD")
    if actual != epic_head_sha:
        raise IllegalTransition(
            f"integration worktree {path} is checked out at {actual}, not the "
            f"resolved epic head {epic_head_sha}"
        )
    return path


def record_final_verification_run(
    store: StateStore,
    *,
    run_result: dict[str, Any],
    duration_ms: int,
    repo: Path | str,
    layers: dict[str, Any] | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """The `--run`-fed counterpart of `record_final_verification` (spec
    AC-6), mirroring `review.record_verification_run`'s task-level pattern
    at scope granularity: `run_result` is `verify_run.execute`'s own return
    shape (`{"results": [...], "logSha256": ...}`), recorded verbatim (as
    `results`, not `commands` -- see `verification_commands`'s docstring
    note for why) since `cli.py`'s `_run_final_verification` is the sole
    caller and already built the exact required, ordered command list
    (global `verification.commands` then the deliverable command) before
    executing it; there is nothing left to re-validate here.

    `execution: "wddctl"` is written here, unconditionally, and ONLY here
    -- the sole code path that ever produces that literal on a final
    verification record. Legal only for non-legacy (v5) scopes; the legacy
    refusal is repeated here even though `_run_final_verification` already
    refuses it before ever executing a command, same defense-in-depth
    reasoning as the finalize-phase/not-delivered checks below (both
    checked before AND inside the locked mutator).
    """
    state = store.read()
    if _is_legacy_intake(state):
        raise ValidationError(
            "finalize verify record --run is not available for a legacy scope: legacy "
            "scopes keep the reported-only single-command contract (wddctl finalize "
            "verify record --status ... --command ...)"
        )
    repo_path = require_repository(repo)
    _require_finalize_phase(state)
    _require_not_delivered(state, what="verify")

    results = run_result["results"]
    overall_status = "failed" if any(entry["status"] == "failed" for entry in results) else "passed"

    def mutator(state: dict[str, Any]) -> dict[str, Any]:
        _require_finalize_phase(state)
        _require_not_delivered(state, what="verify")
        base_sha = resolve_ref(repo_path, _base_ref(state))
        updated = copied_state(state)
        updated.setdefault("finalize", {})
        binding = _final_verification_evidence_binding(layers, state)
        updated["finalize"]["verification"] = {
            "headSha": base_sha,
            "results": results,
            "status": overall_status,
            "logSha256": run_result["logSha256"],
            "execution": "wddctl",
            "telemetry": {"durationMs": duration_ms},
            "at": utc_now(),
            **binding,
        }
        return updated

    return apply_mutation(
        store,
        event_type="final.verification_recorded",
        task_id=None,
        data={},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )


def _handoff_summary(state: dict[str, Any]) -> str:
    scope = state["scope"]
    finalize = state.get("finalize") or {}
    review = finalize.get("review") or {}
    verification = finalize.get("verification") or {}
    commands = verification_commands(verification)
    commands_desc = ", ".join(c["command"] for c in commands if c.get("command")) or "n/a"
    lines = [f"wdd scope {scope['id']}", "", "Tasks:"]
    for task_id, task in sorted(state["tasks"].items()):
        lines.append(f"- {task_id}: {task.get('title', task_id)} ({task['status']})")
    lines += [
        "",
        f"Final review: {review.get('outcome')} by {review.get('reviewer')}",
        f"Final verification: {verification.get('status')} ({commands_desc})",
    ]
    return "\n".join(lines)


def _require_current_finalize_evidence(
    state: dict[str, Any], base_sha: str, layers: dict[str, Any] | None = None
) -> None:
    """Handoff's precondition: clean review + passed verification, both fresh.

    Named after what to redo, not just that evidence is missing or stale --
    the plan requires stale-evidence refusals to name the fix. The verify
    hint is branched on the scope's own legacy-ness: a v5 non-legacy scope's
    `finalize verify record` refuses the legacy `--status/--command`
    invocation outright (it wants `--results`), so naming the wrong contract
    here would send the operator down a dead end.

    `layers`, when given, additionally re-derives the finalReview/
    finalVerification config projections (+ finalReview's bound model) and
    refuses if either no longer matches what the recorded evidence bound to
    (spec Sec2, epic-scoped-state plan Task 5): a config edit after the
    review/verification ran stales it exactly like a moved headSha does,
    even though headSha itself did not move.
    """
    legacy = _is_legacy_intake(state)
    verify_hint = (
        "wddctl finalize verify record --status passed --command CMD --repo ."
        if legacy
        else "wddctl finalize verify record --results '[{\"command\":...,\"status\":...}, ...]' --repo ."
    )
    review_hint = "wddctl finalize review record --reviewer NAME --findings '[]' --repo ."
    finalize = state.get("finalize") or {}
    review = finalize.get("review")
    if not isinstance(review, dict) or review.get("outcome") != "passed":
        raise IllegalTransition(
            f"handoff requires a clean final review; run '{review_hint}'"
        )
    if review.get("headSha") != base_sha:
        raise IllegalTransition(
            f"final review evidence is stale (pinned to {review.get('headSha')}, base is now "
            f"at {base_sha}); re-run '{review_hint}'"
        )
    verification = finalize.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "passed":
        raise IllegalTransition(f"handoff requires passed final verification; run '{verify_hint}'")
    if verification.get("headSha") != base_sha:
        raise IllegalTransition(
            f"final verification evidence is stale (pinned to {verification.get('headSha')}, "
            f"base is now at {base_sha}); re-run '{verify_hint}'"
        )
    if layers is not None:
        expected_review = _final_review_evidence_binding(layers)
        if "reviewModel" in review and review.get("reviewModel") != expected_review["reviewModel"]:
            raise IllegalTransition(
                f"final review evidence is stale (recorded reviewModel "
                f"{review.get('reviewModel')!r} no longer matches the currently resolved "
                f"{expected_review['reviewModel']!r}); re-run '{review_hint}'"
            )
        if "configSha256" in review and review.get("configSha256") != expected_review["configSha256"]:
            raise IllegalTransition(
                "final review evidence is stale (its config projection no longer matches "
                f"the current config); re-run '{review_hint}'"
            )
        expected_verification = _final_verification_evidence_binding(layers, state)
        if (
            "configSha256" in verification
            and verification.get("configSha256") != expected_verification["configSha256"]
        ):
            raise IllegalTransition(
                "final verification evidence is stale (its config projection -- including "
                f"the epic deliverable command -- no longer matches the current config); "
                f"re-run '{verify_hint}'"
            )


def prepare_handoff(
    store: StateStore,
    *,
    repo: Path | str,
    layers: dict[str, Any] | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Push the epic branch and open (pr surface) or instruct (local surface) the handoff.

    wddctl never merges the epic branch into the target -- that is Spec §6's
    human-owned final merge; there is deliberately no code path for it
    anywhere in this module. This only prepares the handoff: on the "pr"
    surface it pushes the base branch and opens the epic->target PR via
    github.py; on "local" it records the handoff and returns instructions,
    leaving the push and PR to the operator.
    """
    repo_path = require_repository(repo)
    wdd_dir = store.path.parent
    state = store.read()
    _require_finalize_phase(state)
    _require_not_delivered(state, what="hand off")
    base_ref = _base_ref(state)
    base_sha = resolve_ref(repo_path, base_ref)
    target_branch, config = _require_target_branch(wdd_dir, layers)
    # Checked before the review/verification evidence gate: there is no
    # point asking for a clean review of a branch that has nothing to
    # deliver, and this is the guard that closes the vacuous-ancestry
    # self-certification (finding 1) -- it must be the first thing a scope
    # with no merged work hits, not something masked by a missing-evidence
    # message that would send the operator down the wrong path.
    _require_something_to_deliver(repo_path, base_sha, target_branch)
    _require_current_finalize_evidence(state, base_sha, layers)
    surface = merge_settings(state, config)["surface"]

    pr_url: str | None = None
    instructions: str | None = None
    if surface == "pr":
        # Side effects happen before the lock, same ordering leases.submit_task
        # uses for its own push/create_pr: a network failure here must not
        # record a handoff that never actually reached GitHub.
        push_branch(repo_path, base_ref)
        pr_url = create_pr(
            repo_path,
            base_ref,
            target_branch,
            title=f"WDD scope {state['scope']['id']}",
            body=_handoff_summary(state),
        )
    else:
        instructions = (
            f"push {base_ref} to your remote (e.g. 'git push origin {base_ref}') and open a "
            f"pull request into {target_branch} yourself; wddctl does not perform this on the "
            "local surface. Once the human merge lands, run 'wddctl finalize delivered "
            "--by NAME --repo .' to record it."
        )

    outcome: dict[str, Any] = {}

    def mutator(current: dict[str, Any]) -> dict[str, Any]:
        # Re-validated under the lock: the push/PR above ran outside it, so
        # the base branch (and the evidence pinned to it) could have moved --
        # including a delivered record that landed in the meantime (a
        # concurrent 'finalize delivered' racing this handoff).
        _require_finalize_phase(current)
        _require_not_delivered(current, what="hand off")
        current_base_sha = resolve_ref(repo_path, base_ref)
        if current_base_sha != base_sha:
            raise IllegalTransition(
                f"base branch {base_ref} moved since handoff began (was {base_sha}, now "
                f"{current_base_sha}); re-run 'wddctl finalize handoff --repo .'"
            )
        _require_something_to_deliver(repo_path, base_sha, target_branch)
        _require_current_finalize_evidence(current, base_sha, layers)
        updated = copied_state(current)
        updated.setdefault("finalize", {})
        updated["finalize"]["handoff"] = {
            "pr": pr_url,
            "headSha": base_sha,
            "targetBranch": target_branch,
            "at": utc_now(),
        }
        outcome.update({"pr": pr_url, "headSha": base_sha, "targetBranch": target_branch})
        return updated

    state, duplicate = apply_mutation(
        store,
        event_type="handoff.prepared",
        task_id=None,
        data={},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )
    result = {**outcome, "revision": state["revision"], "duplicate": duplicate}
    if instructions is not None:
        result["instructions"] = instructions
    return result


def record_delivered(
    store: StateStore,
    *,
    by: str,
    repo: Path | str,
    layers: dict[str, Any] | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Record the observed human merge of the epic branch into the target.

    Reuses phase 4's either-ref ancestry proof (``merge._fetched_base_refs``):
    a human merge is provable evidence the moment it lands on either the
    local target branch or ``origin/<target>``, and neither is authoritative
    over the other (see that function's docstring). wddctl performing this
    merge itself is deliberately not implemented anywhere: this only ever
    observes Git state a human already created.

    Re-running this once delivery is already recorded is refused, not
    silently re-verified: ``by``/``at`` name who observed the merge and
    when, and letting a second caller overwrite that with their own name
    would misattribute the record. Nothing about a genuine re-run is more
    correct than the first one -- the ancestry proof cannot change once it
    has succeeded -- so there is nothing legitimate for a retry to add. A
    caller that actually needs the current record has ``finalize status``.

    Also requires a recorded handoff (``_require_handoff_recorded``): without
    it, a scope could reach delivered by ancestry proof alone, without ever
    going through the handoff-time vacuous-ancestry guard
    (``_require_something_to_deliver``) -- see that function's docstring for
    the self-certification this closes.
    """
    if not isinstance(by, str) or not by:
        raise ValidationError("by must be a non-empty string")
    repo_path = require_repository(repo)
    wdd_dir = store.path.parent
    state = store.read()
    _require_finalize_phase(state)
    _require_not_delivered(state, what="deliver")
    _require_handoff_recorded(state)
    base_ref = _base_ref(state)
    target_branch, _config = _require_target_branch(wdd_dir, layers)

    run_git(repo_path, "fetch", "origin", target_branch, check=False)

    outcome: dict[str, Any] = {}

    def mutator(current: dict[str, Any]) -> dict[str, Any]:
        # Re-derived inside the lock: the fetch and the outer read happened
        # before it, so nothing here may be trusted stale.
        _require_finalize_phase(current)
        _require_not_delivered(current, what="deliver")
        _require_handoff_recorded(current)
        base_sha = resolve_ref(repo_path, base_ref)
        candidates = _fetched_base_refs(repo_path, target_branch)
        proven = next(
            (sha for _, sha in candidates if is_ancestor(repo_path, base_sha, sha)), None
        )
        if proven is None:
            refs_desc = " nor ".join(f"{name} ({sha})" for name, sha in candidates)
            raise IllegalTransition(
                f"scope base {base_ref} head {base_sha} is reachable from neither "
                f"{refs_desc}; the final merge has not happened"
            )
        updated = copied_state(current)
        updated.setdefault("finalize", {})
        updated["finalize"]["delivered"] = {"at": utc_now(), "by": by, "headSha": base_sha}
        outcome.update({"headSha": base_sha, "by": by, "targetBranch": target_branch})
        return updated

    state, duplicate = apply_mutation(
        store,
        event_type="scope.delivered",
        task_id=None,
        data={},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )
    return {**outcome, "revision": state["revision"], "duplicate": duplicate}


def _final_review_judgment(state: dict[str, Any]) -> str:
    """The `final_review` action's judgment text (spec Sec5's finalize tie-in).

    Legacy scopes keep the original prose verbatim (a regression pin -- they
    have no numbered criteria or design.md deliverable to name). Non-legacy
    v5 scopes additionally name the acceptance criteria to walk by number
    (AC-1..AC-N, from ``intake.spec.criteria``) and design.md's epic
    deliverable statement, alongside the existing spec.md instruction.
    """
    base = (
        "dispatch a reviewer against the whole epic branch diff, per wdd-review's "
        "final-review contract, checked against spec.md"
    )
    if _is_legacy_intake(state):
        return base
    criteria = ((state.get("intake") or {}).get("spec") or {}).get("criteria")
    if not criteria:
        return base
    return (
        f"{base}; walk spec.md's acceptance criteria AC-1..AC-{criteria} in order and "
        "confirm design.md's epic deliverable statement is observably true"
    )


def _delivered_judgment(state: dict[str, Any]) -> str:
    """The `archive` action's judgment text (spec Sec3): names the epic
    retrospective step alongside the archive command. The knowledge file
    lives at `shared-context/knowledge/<slug>.md`, keyed by the epic slug
    when one exists (`state.epic`, cleared only once archiving itself
    resets state) -- a legacy or pre-epic scope has no slug, so its own
    scope id names the file instead, the closest stable identifier such a
    scope has.

    This is standing guidance, not a gate (spec Non-goals: "No retrospective
    hard gate -- `scope archive` never refuses for a missing retrospective,
    and running it directly (skipping the offer) is legal operator
    behavior"): the judgment names the step, but `archive`'s own `command`
    below runs unconditionally either way.
    """
    slug = state.get("epic") or (state.get("scope") or {}).get("id") or "SLUG"
    return (
        "before archiving, offer the epic retrospective per wdd-intake: distill the "
        f"decisions, root causes, and quirks captured during the epic into "
        f"shared-context/knowledge/{slug}.md, get the human's sign-off recorded in the "
        "file, and commit it -- then run 'scope archive'. This is standing guidance, not "
        "a gate: running scope archive directly, skipping the offer, is legal operator "
        "behavior, and the living draft survives either way."
    )


def finalize_next_actions(
    state: dict[str, Any],
    wdd_dir: Path | str,
    repo: Path | str,
    *,
    state_path: str | None = None,
) -> dict[str, Any]:
    """The finalize-phase counterpart of setup.setup_next_actions.

    Same output shape and the same one-action-at-a-time discipline: once a
    scope reaches finalize, exactly one rung of Spec Sec6's ladder is "the"
    next thing to do, in priority order --

      1. final_review     -- review absent, or stale against the current
                              base head (a fresh-but-blocked review is NOT
                              this rung; see assign_final_fixes below. New
                              commits landed to address blocking findings
                              re-stale the review and land back here
                              naturally, so this rung also covers the
                              "review fixes went in" loop-back).
      2. assign_final_fixes -- review present, fresh, and blocked: no
                              command exists for this (the fix is new
                              commits on the base branch, which is exactly
                              what re-stales the review above).
      3. final_verification -- review passed+fresh; verification absent,
                              not passed, or stale.
      4. prepare_handoff   -- review and verification both passed+fresh;
                              no handoff recorded, or the handoff itself is
                              stale (handoff.headSha != current base head).
                              In practice a new base commit re-stales review
                              and verification at the same time (they are
                              all three pinned to the same SHA), so rung 1
                              or 3 will already have fired before this
                              staleness is ever independently observable --
                              the check exists anyway for defense in depth
                              and because Task 3's own handoff precondition
                              (_require_current_finalize_evidence) already
                              treats "the base branch moved since I read it"
                              as a first-class failure mode.
      5. await_delivery    -- handoff recorded and fresh: nothing left to
                              run, only the human-owned final merge to wait
                              on (Spec Sec6: wddctl never performs it).
      6. archive           -- delivered: the human-owned merge already
                              landed. `next`'s judgment names the epic
                              retrospective step alongside the archive
                              command (spec Sec3) -- an offer, not a gate.

    Unlike engine.py, this module already reads config.json directly
    (record_final_review, prepare_handoff, ...) -- it lives outside
    engine.py specifically so scope-level choreography can do that, the
    same way setup_next_actions reads config for its own open-questions
    gate. ``models.review`` is attached to final_review the same way
    engine.decorate_actions attaches it to a task-level run_review action.
    """
    wdd_dir = Path(wdd_dir)
    scope_id = (state.get("scope") or {}).get("id")
    phase = derived_phase(state)
    prefix = "wddctl" + (f" --state {shlex.quote(state_path)}" if state_path else "")
    repo_arg = shlex.quote(str(repo))
    if phase == "delivered":
        return {
            "scope": scope_id,
            "revision": state["revision"],
            "phase": "delivered",
            "actions": [
                {
                    "task": "-",
                    "action": "archive",
                    "command": f"{prefix} scope archive --repo {repo_arg}",
                    "judgment": _delivered_judgment(state),
                }
            ],
            "blockers": [],
        }

    repo_path = require_repository(repo)
    base_sha = resolve_ref(repo_path, _base_ref(state))

    finalize = state.get("finalize") or {}
    review = finalize.get("review")
    verification = finalize.get("verification")
    handoff = finalize.get("handoff")

    review_fresh = isinstance(review, dict) and review.get("headSha") == base_sha
    verification_fresh = (
        isinstance(verification, dict) and verification.get("headSha") == base_sha
    )
    handoff_fresh = isinstance(handoff, dict) and handoff.get("headSha") == base_sha

    action: dict[str, Any]
    if review is None or not review_fresh:
        action = {
            "task": "-",
            "action": "final_review",
            "recordWith": (
                f"{prefix} finalize review record --reviewer NAME --findings '[]' "
                f"--repo {repo_arg}"
            ),
            "judgment": _final_review_judgment(state),
        }
        config = load_config(wdd_dir) if config_path(wdd_dir).exists() else None
        model = (config["models"].get("review") if config else None)
        if isinstance(model, str) and model:
            action["model"] = model
    elif review.get("outcome") == "blocked":
        blocking = _blocking_severities(wdd_dir)
        findings = [f for f in review.get("findings", []) if f.get("severity") in blocking]
        named = "; ".join(f"{f['severity']}: {f['summary']}" for f in findings)
        action = {
            "task": "-",
            "action": "assign_final_fixes",
            "judgment": (
                f"assign fixes for the blocking findings from the final review ({named}); "
                "new commits on the base branch re-stale the review, which brings the scope "
                "back to final_review automatically"
            ),
            "findings": findings,
        }
    elif (
        verification is None
        or not verification_fresh
        or verification.get("status") != "passed"
    ):
        if _is_legacy_intake(state):
            record_with = (
                f"{prefix} finalize verify record --status passed "
                f"--command '<verification command>' --repo {repo_arg}"
            )
        else:
            required = _required_verification_commands(state, wdd_dir)
            results_hint = json.dumps(
                [{"command": cmd, "status": "passed"} for cmd in required]
            )
            record_with = (
                f"{prefix} finalize verify record --results {shlex.quote(results_hint)} "
                f"--repo {repo_arg}"
            )
        action = {
            "task": "-",
            "action": "final_verification",
            "recordWith": record_with,
            "judgment": "run full verification against the current epic branch head and record the result",
        }
    elif handoff is None or not handoff_fresh:
        action = {
            "task": "-",
            "action": "prepare_handoff",
            "command": f"{prefix} finalize handoff --repo {repo_arg}",
            "judgment": (
                "push the epic branch and open the handoff to the human who performs the "
                "final merge (pr surface), or record local handoff instructions (local surface)"
            ),
        }
    else:
        target_branch = handoff.get("targetBranch")
        pr = handoff.get("pr")
        if pr:
            reference = f"the handoff PR {pr}"
        else:
            base_ref = _base_ref(state)
            reference = (
                f"the local handoff: push {base_ref} to your remote and open a pull request "
                f"into {target_branch} yourself; wddctl does not perform this on the local surface"
            )
        action = {
            "task": "-",
            "action": "await_delivery",
            "recordWith": f"{prefix} finalize delivered --by NAME --repo {repo_arg}",
            "judgment": (
                f"wait for the human-owned final merge via {reference}; once it lands, record "
                "it with recordWith so live Git can prove it happened"
            ),
        }

    return {
        "scope": scope_id,
        "revision": state["revision"],
        "phase": "finalize",
        "actions": [action],
        "blockers": [],
    }


# ---------------------------------------------------------------------------
# `wddctl scope archive`: spec Sec1's four-step recoverable transaction, plus
# the exhaustive recovery matrix that makes crashing at any step safe. Every
# state write below happens while `store.locked()` is held for the whole
# transaction (never released and re-acquired mid-way -- `apply_mutation`
# cannot be called from in here: its own `store.locked()` would deadlock
# against the lock this code already holds, since it is not reentrant), so
# the manual envelope-writing in `_apply_locked_event` mirrors
# `apply_mutation`'s own bookkeeping (revision bump, one event, idempotency
# key, telemetry) by hand -- the same "handwritten mutator, apply_mutation's
# envelope semantics without apply_mutation itself" idiom `setup.init_repository`
# already uses for its own locked-but-not-apply_mutation write.
# ---------------------------------------------------------------------------


def generate_archive_record(state: dict[str, Any], archived_at: str) -> dict[str, Any]:
    """The archive record's contents: a pure, deterministic function of
    `state` plus the one nondeterministic input, `archived_at` (spec Sec1
    "record generation is a deterministic function of the state at
    sourceRevision -- excluding the archivePending journal event itself --
    plus the journal's archivedAt"). Same shape as the pre-Task-6 flat
    archive payload (scope/tasks/intake/finalize/reconcile/leases/
    eventCount/archivedAt); only WHERE it is written changed (inside
    `epics/<slug>/`, then moved wholesale by the step-3 rename).

    `state["archivePending"]`, when present, means the journal event (step
    2) has already been appended and revision already bumped once beyond
    what generated the ORIGINAL record -- so `eventCount` here excludes that
    one trailing event, making a post-step-2 (or later) regeneration from
    live state byte-identical to what step 1 wrote before that event ever
    existed. This is what makes `recordSha256` always reproducible: nothing
    about the record is unreconstructable.
    """
    events = state.get("events") or []
    if state.get("archivePending") is not None:
        events = events[:-1]
    return {
        "scope": state.get("scope"),
        "tasks": state.get("tasks"),
        "intake": state.get("intake"),
        "finalize": state.get("finalize"),
        "reconcile": state.get("reconcile"),
        "leases": state.get("leases") or {},
        "eventCount": len(events),
        "archivedAt": archived_at,
    }


def _canonical_record_bytes(record: dict[str, Any]) -> bytes:
    """Byte-stable serialization shared by the on-disk `record.json` write
    and the `recordSha256` hash, so "the bytes we hashed" and "the bytes we
    wrote" can never drift apart. Pretty-printed (indent=2) like every other
    `.wdd/` JSON file; `sort_keys=True` recurses into every nested object, so
    key order never affects the digest -- `generate_archive_record` twice
    from the same inputs always yields identical bytes here.
    """
    return (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _record_sha256(record_bytes: bytes) -> str:
    return f"sha256:{hashlib.sha256(record_bytes).hexdigest()}"


def _verify_or_regenerate_record(
    state: dict[str, Any], record_path: Path, archive_pending: dict[str, Any]
) -> None:
    """Recovery's "verify record.json against recordSha256 (regenerate from
    the still-live state if missing/corrupt)" step, shared by recovery rows
    A and B -- both verify/regenerate the SAME way, only the path (still
    inside `epics/<slug>/`, or already moved to `archive/<slug>/`) differs.
    The state itself is authoritative in both rows (the reset has not
    happened yet either way), so `generate_archive_record` is called
    against it unconditionally when regeneration is needed.
    """
    expected_sha = archive_pending["recordSha256"]
    valid = False
    if record_path.exists():
        try:
            valid = _record_sha256(record_path.read_bytes()) == expected_sha
        except OSError:
            valid = False
    if valid:
        return
    record = generate_archive_record(state, archive_pending["archivedAt"])
    record_bytes = _canonical_record_bytes(record)
    if _record_sha256(record_bytes) != expected_sha:
        raise ValidationError(
            f"regenerated archive record for slug {archive_pending['slug']!r} does not "
            f"match the journaled recordSha256 ({expected_sha}); the live state no longer "
            "reproduces the record it originally journaled -- this should be unreachable "
            "(archivePending is only ever journaled from the exact state that generated the "
            "record) and needs manual inspection of .wdd/state.json"
        )
    atomic_write_text(record_path, record_bytes.decode("utf-8"))


def _reset_to_post_ratification(current: dict[str, Any]) -> dict[str, Any]:
    """Step 4's reset target (spec Sec1): every scope-carrying section wiped
    to `new_setup_state()`'s fresh shape (scope null, tasks empty, `finalize`
    absent, intake reset to `{}` -- a fresh ladder, NOT re-marked legacy --
    reconcile and monitoring fresh, `epic`/`archivePending`/`archiveBlocked`
    all null/absent), governance and the audit trail (constitution, events,
    appliedIdempotencyKeys, telemetry) carried over untouched. `leases` is
    archived into the record above, then dropped entirely -- `new_setup_state`
    produces no `leases` key at all, not a zeroed `{}`. Shared by the
    ordinary (uncrashed) step 4 and recovery rows A/B's completion, which
    reset to this exact same shape; `revision` is left as `current`'s own
    (unbumped) so the caller's own `_apply_locked_event` bump lands on top.
    """
    fresh = new_setup_state()
    fresh["constitution"] = current["constitution"]
    fresh["events"] = current["events"]
    fresh["appliedIdempotencyKeys"] = current["appliedIdempotencyKeys"]
    fresh["telemetry"] = current["telemetry"]
    fresh["revision"] = current["revision"]
    # Probes are machine observations keyed by runner-command digest, not
    # scope state (spec Sec1: "nothing scope-specific leaks forward" -- and
    # probes are exactly NOT scope-specific): they survive the reset, or the
    # next epic re-probes commands whose evidence never expired (Task 6
    # review finding; the v5 flat archive shared this bug).
    if current.get("probes"):
        fresh["probes"] = current["probes"]
    # Parked epics (epic park/resume spec): a DIFFERENT epic's `state.parked`
    # entries are not this epic's scope-carrying state -- archiving (or
    # crash-recovering) the ACTIVE epic must never wipe out an unrelated
    # epic parked earlier. `new_setup_state()` already defaults `parked` to
    # `{}`; only override it when there is something to carry forward.
    if current.get("parked"):
        fresh["parked"] = current["parked"]
    return fresh


def _apply_locked_event(
    state: dict[str, Any],
    event_type: str,
    data: dict[str, Any],
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Hand-roll `apply_mutation`'s write envelope (revision bump, one
    event, idempotency-key bookkeeping, telemetry) for a state mutation that
    runs OUTSIDE `apply_mutation` itself. `archive_scope`'s steps 2 and 4,
    plus recovery's row completions, all need this: none of them may call
    `apply_mutation` (it acquires `store.locked()`, and the lock this code
    already runs under is not reentrant -- nesting it would deadlock).
    """
    updated = copied_state(state)
    updated["revision"] = state["revision"] + 1
    key = idempotency_key or event_id(updated["revision"], event_type, None, data)
    updated["events"].append(
        {
            "revision": updated["revision"],
            "type": event_type,
            "task": None,
            "idempotencyKey": key,
            "at": utc_now(),
        }
    )
    updated["appliedIdempotencyKeys"].append(key)
    updated["telemetry"]["eventApplications"] += 1
    return updated


def recover_archive_transaction(state: dict[str, Any], wdd_dir: Path | str) -> dict[str, Any]:
    """The archive-transaction recovery matrix (spec Sec1), run under the
    state lock -- assumed already held by the caller (`StateStore.
    recover_locked`, itself called by `apply_mutation` and `load_recovered`).
    Returns `state` completely unchanged (the SAME object, checked by
    identity) when nothing needs recovering, so the caller knows not to
    persist anything; otherwise returns a freshly built state the caller
    must write.

    NEVER scans, reads, or otherwise touches anything under `archive/`
    beyond the exact path named by `archivePending.slug` or `archiveBlocked.
    slug` -- a completed archive's own `record.json` with no journal is the
    *success* state, not recovery's business, and pre-v6 archive files (or
    any other epic's archived directory) are never looked at.

    Six recoverable/terminal rows, exhaustively, any other on-disk
    combination is a hard `ValidationError` naming the observed facts:

    1. journal set, source (`epics/<slug>/`) present, destination
       (`archive/<slug>/`) absent -> verify/regenerate `record.json`, retry
       the rename, complete the reset.
    2. journal set, destination present, source absent -> verify/regenerate
       the ARCHIVED `record.json` (the reset has not happened -- state is
       still authoritative), complete the reset only.
    3. journal set, BOTH present (external collision) -> remove the
       generated record.json, clear the journal, write a durable
       `archiveBlocked` (event `scope.archive_blocked`).
    4. journal set, neither present -> hard error (nothing guessed).
    5. no journal, `archiveBlocked` set, both directories present -> the
       legal post-rollback resting state; nothing is touched (`next`
       surfaces the blocker from the durable field).
    6. no journal, no block: a stray `record.json` inside the ACTIVE epic's
       own directory (`epics/<state.epic>/`) is a step-1 crash (the
       transaction never reached step 2) -- remove it and proceed. Scoped to
       `state.epic` only, never any other directory under `epics/`.
    """
    wdd_dir = Path(wdd_dir)
    archive_pending = state.get("archivePending")

    if archive_pending is not None:
        slug = archive_pending["slug"]
        epic_dir = wdd_dir / "epics" / slug
        archive_dir = wdd_dir / "archive" / slug
        source_exists = epic_dir.is_dir()
        dest_exists = archive_dir.exists()

        if source_exists and not dest_exists:
            # Row 1.
            _verify_or_regenerate_record(state, epic_dir / "record.json", archive_pending)
            # os.rename requires the destination's PARENT to already exist;
            # `archive/` itself is never otherwise created (unlike the old
            # flat-file layout, whose atomic_write_text implicitly mkdir'd
            # it) since nothing under it is ever written directly.
            archive_dir.parent.mkdir(parents=True, exist_ok=True)
            os.rename(epic_dir, archive_dir)
            return _apply_locked_event(
                _reset_to_post_ratification(state), "scope.archived", {"slug": slug}
            )

        if dest_exists and not source_exists:
            # Row 2.
            _verify_or_regenerate_record(state, archive_dir / "record.json", archive_pending)
            return _apply_locked_event(
                _reset_to_post_ratification(state), "scope.archived", {"slug": slug}
            )

        if source_exists and dest_exists:
            # Row 3: external collision -- do not retry forever.
            generated_record = epic_dir / "record.json"
            if generated_record.exists():
                generated_record.unlink()
            blocked = copied_state(state)
            blocked["archivePending"] = None
            blocked["archiveBlocked"] = {
                "slug": slug,
                "collidingPath": str(archive_dir),
                "at": utc_now(),
            }
            return _apply_locked_event(blocked, "scope.archive_blocked", {"slug": slug})

        # Row 4: neither path exists -- nothing to verify, nothing to guess.
        raise ValidationError(
            f"scope archive is journaled (slug {slug!r}, recorded at "
            f"{archive_pending.get('archivedAt')!r}) but neither epics/{slug}/ nor "
            f"archive/{slug}/ exists on disk; this cannot be recovered automatically -- "
            "inspect .wdd/ by hand"
        )

    archive_blocked = state.get("archiveBlocked")
    if archive_blocked is not None:
        # Row 5: the legal resting state -- untouched regardless of whether
        # the collision is still there or has since been resolved (a human
        # can remove `collidingPath` at any time; recovery itself does not
        # act on that -- `scope archive`'s own re-run logic is what clears
        # `archiveBlocked` and starts fresh, per spec Sec1). The one
        # invariant recovery actually cares about is that the epic's own
        # content (the transaction's SOURCE) is still there to resume from.
        slug = archive_blocked["slug"]
        epic_dir = wdd_dir / "epics" / slug
        if epic_dir.is_dir():
            return state
        colliding = archive_blocked["collidingPath"]
        raise ValidationError(
            f"state.archiveBlocked names slug {slug!r}, but epics/{slug}/ no longer exists "
            f"on disk (recorded colliding path: {colliding}); this cannot be recovered "
            "automatically -- inspect .wdd/ by hand"
        )

    # Row 6: no journal, no durable block -- the only remaining recoverable
    # shape is a step-1 crash, scoped to the ACTIVE epic only.
    active_epic = state.get("epic")
    if active_epic is not None:
        stray_record = wdd_dir / "epics" / active_epic / "record.json"
        if stray_record.exists():
            stray_record.unlink()
    return state


def archive_scope(
    store: StateStore,
    *,
    repo: Path | str,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """`wddctl scope archive`: the ladder's final transition (spec Sec1's
    rollover), now a recoverable transaction under the state lock:

    1. Write `record.json` (deterministic, `generate_archive_record`)
       INSIDE `epics/<slug>/` -- the reserved filename `epic new` and the
       typed resolver both already refuse.
    2. Journal `state.archivePending = {slug, sourceRevision, archivedAt,
       recordSha256}`.
    3. One atomic `os.rename(epics/<slug>, archive/<slug>)`.
    4. Reset state to `_reset_to_post_ratification`'s fresh setup shape,
       clearing `archivePending` (and `epic`) along with everything else
       scope-carrying. `next` emits `agree_spec` (by way of `create_epic`)
       immediately afterward, same as any other ratified-but-scope-null
       state -- no bespoke rollover logic is needed in setup.py.

    The whole transaction runs inside ONE `store.locked()` block (never
    released between steps) -- crash-recovery for every step is
    `recover_archive_transaction`'s job, invoked here too (via
    `store.recover_locked()`) so a call that finds a PRIOR crash first heals
    it transparently before doing anything else. A durable `archiveBlocked`
    left by a prior collision (recovery row 3) is resolved here, not by
    recovery itself: if the colliding path is gone, the block is lifted and
    a fresh transaction starts in the same call; if it is still there, this
    refuses, naming it.
    """
    wdd_dir = store.path.parent
    require_repository(repo)
    with store.locked():
        state = store.recover_locked()

        clear_archive_blocked = False
        archive_blocked = state.get("archiveBlocked")
        if archive_blocked is not None:
            colliding_path = Path(archive_blocked["collidingPath"])
            if colliding_path.exists():
                raise IllegalTransition(
                    f"scope archive is blocked: {colliding_path} already exists, colliding "
                    f"with epic slug {archive_blocked['slug']!r}; move or remove it, then "
                    "re-run 'wddctl scope archive --repo .' to start a fresh transaction"
                )
            clear_archive_blocked = True

        phase = derived_phase(state)
        if phase != "delivered":
            raise IllegalTransition(
                f"scope archive requires the scope to be delivered (it is {phase}); finish "
                "the finalize ladder through 'wddctl finalize delivered --by NAME --repo .' "
                "first"
            )

        if idempotency_key and idempotency_key in state["appliedIdempotencyKeys"]:
            return {"revision": state["revision"], "duplicate": True}
        if expected_revision is not None and state["revision"] != expected_revision:
            raise RevisionConflict(
                f"expected revision {expected_revision}, found {state['revision']}"
            )

        slug = state.get("epic")
        if slug is None:
            raise IllegalTransition(
                "scope archive requires an active epic (state.epic is unset); this scope "
                "predates the epic-scoped-state migration and has no epics/<slug>/ directory "
                "to archive"
            )
        scope_id = state["scope"]["id"]
        epic_dir = wdd_dir / "epics" / slug
        archive_dir = wdd_dir / "archive" / slug
        if not epic_dir.is_dir():
            raise ValidationError(f"epics/{slug}/ does not exist; cannot archive")
        if archive_dir.exists():
            raise ValidationError(
                f"archive/{slug}/ already exists; slugs are unique across epics/ and "
                "archive/ -- this should be unreachable"
            )

        archived_at = utc_now()
        record = generate_archive_record(state, archived_at)
        record_bytes = _canonical_record_bytes(record)
        record_sha256 = _record_sha256(record_bytes)
        atomic_write_text(epic_dir / "record.json", record_bytes.decode("utf-8"))  # Step 1

        source_revision = state["revision"]
        pending_state = copied_state(state)
        pending_state["archivePending"] = {
            "slug": slug,
            "sourceRevision": source_revision,
            "archivedAt": archived_at,
            "recordSha256": record_sha256,
        }
        if clear_archive_blocked:
            pending_state["archiveBlocked"] = None
        pending_state = _apply_locked_event(
            pending_state, "scope.archive_pending", {"slug": slug}, idempotency_key=idempotency_key
        )
        store.write(pending_state)  # Step 2

        archive_dir.parent.mkdir(parents=True, exist_ok=True)
        os.rename(epic_dir, archive_dir)  # Step 3

        final_state = _apply_locked_event(
            _reset_to_post_ratification(pending_state), "scope.archived", {"slug": slug}
        )
        store.write(final_state)  # Step 4

    return {
        "archived": str(archive_dir / "record.json"),
        "scope": scope_id,
        "revision": final_state["revision"],
        "duplicate": False,
    }
