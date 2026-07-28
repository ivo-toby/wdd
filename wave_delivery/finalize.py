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

from pathlib import Path
from typing import Any

from .config import config_path, load_config, merge_settings
from .engine import apply_mutation, utc_now
from .errors import IllegalTransition, ValidationError
from .git import is_ancestor, require_repository, resolve_ref, run_git
from .github import create_pr, push_branch
from .merge import _fetched_base_refs
from .review import validate_findings
from .schema import copied_state, derived_phase
from .store import StateStore


FINALIZE_PHASES = {"finalize", "delivered"}


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


def _require_target_branch(wdd_dir: Path) -> tuple[str, dict[str, Any]]:
    if not config_path(wdd_dir).exists():
        raise IllegalTransition(
            "this scope predates config.json, so it has no configured "
            "branching.targetBranch; run 'wddctl migrate --governance' to adopt "
            "config.json (preserving any legacy model settings), then re-run this command"
        )
    config = load_config(wdd_dir)
    return config["branching"]["targetBranch"], config


def _blocking_severities(wdd_dir: Path) -> set[str]:
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
    blocking = _blocking_severities(store.path.parent)

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


def record_final_verification(
    store: StateStore,
    *,
    status: str,
    command: str | None = None,
    justification: str | None = None,
    repo: Path | str,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Record the full verification run against the current epic branch head.

    Mirrors ``review.record_verification``'s status vocabulary. "unavailable"
    additionally requires a justification -- either passed explicitly or
    falling back to the configured ``verification.unavailableJustification``
    -- since there is no task worker left to explain why the command could
    not run once the scope has reached finalize.
    """
    if status not in {"passed", "failed", "unavailable"}:
        raise ValidationError("verification status must be passed, failed, or unavailable")
    wdd_dir = store.path.parent
    if status == "unavailable":
        if not justification and config_path(wdd_dir).exists():
            justification = load_config(wdd_dir)["verification"].get("unavailableJustification")
        if not justification:
            raise ValidationError(
                "verification status 'unavailable' requires --justification (or a "
                "configured verification.unavailableJustification)"
            )
    repo_path = require_repository(repo)

    # Fail fast, before any side effects run -- same two-stage pattern as
    # record_final_review/prepare_handoff/record_delivered.
    state = store.read()
    _require_finalize_phase(state)
    _require_not_delivered(state, what="verify")

    def mutator(state: dict[str, Any]) -> dict[str, Any]:
        _require_finalize_phase(state)
        _require_not_delivered(state, what="verify")
        base_sha = resolve_ref(repo_path, _base_ref(state))
        updated = copied_state(state)
        updated.setdefault("finalize", {})
        updated["finalize"]["verification"] = {
            "headSha": base_sha,
            "status": status,
            "command": command,
            "justification": justification,
            "at": utc_now(),
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
    lines = [f"wdd scope {scope['id']}", "", "Tasks:"]
    for task_id, task in sorted(state["tasks"].items()):
        lines.append(f"- {task_id}: {task.get('title', task_id)} ({task['status']})")
    lines += [
        "",
        f"Final review: {review.get('outcome')} by {review.get('reviewer')}",
        f"Final verification: {verification.get('status')} ({verification.get('command') or 'n/a'})",
    ]
    return "\n".join(lines)


def _require_current_finalize_evidence(state: dict[str, Any], base_sha: str) -> None:
    """Handoff's precondition: clean review + passed verification, both fresh.

    Named after what to redo, not just that evidence is missing or stale --
    the plan requires stale-evidence refusals to name the fix.
    """
    finalize = state.get("finalize") or {}
    review = finalize.get("review")
    if not isinstance(review, dict) or review.get("outcome") != "passed":
        raise IllegalTransition(
            "handoff requires a clean final review; run 'wddctl finalize review record "
            "--reviewer NAME --findings [] --repo .'"
        )
    if review.get("headSha") != base_sha:
        raise IllegalTransition(
            f"final review evidence is stale (pinned to {review.get('headSha')}, base is now "
            f"at {base_sha}); re-run 'wddctl finalize review record --reviewer NAME "
            "--findings [] --repo .'"
        )
    verification = finalize.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "passed":
        raise IllegalTransition(
            "handoff requires passed final verification; run 'wddctl finalize verify record "
            "--status passed --command CMD --repo .'"
        )
    if verification.get("headSha") != base_sha:
        raise IllegalTransition(
            f"final verification evidence is stale (pinned to {verification.get('headSha')}, "
            f"base is now at {base_sha}); re-run 'wddctl finalize verify record --status "
            "passed --command CMD --repo .'"
        )


def prepare_handoff(
    store: StateStore,
    *,
    repo: Path | str,
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
    _require_current_finalize_evidence(state, base_sha)
    target_branch, config = _require_target_branch(wdd_dir)
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
        _require_current_finalize_evidence(current, base_sha)
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
    """
    if not isinstance(by, str) or not by:
        raise ValidationError("by must be a non-empty string")
    repo_path = require_repository(repo)
    wdd_dir = store.path.parent
    state = store.read()
    _require_finalize_phase(state)
    _require_not_delivered(state, what="deliver")
    base_ref = _base_ref(state)
    target_branch, _config = _require_target_branch(wdd_dir)

    run_git(repo_path, "fetch", "origin", target_branch, check=False)

    outcome: dict[str, Any] = {}

    def mutator(current: dict[str, Any]) -> dict[str, Any]:
        # Re-derived inside the lock: the fetch and the outer read happened
        # before it, so nothing here may be trusted stale.
        _require_finalize_phase(current)
        _require_not_delivered(current, what="deliver")
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
