"""Risk-aware branch freshness classification without an LLM."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .domains import matches_domain
from .engine import apply_event
from .errors import IllegalTransition, ValidationError
from .git import require_repository, resolve_ref, run_git
from .store import StateStore


FRESHNESS_CLASSIFICATIONS = {
    "current",
    "nonmaterially_stale",
    "materially_stale",
    "conflicted",
}


def _changed_paths(repo: Path, start: str, end: str) -> list[str]:
    output = run_git(repo, "diff", "--name-only", f"{start}..{end}").stdout
    return sorted(path for path in output.splitlines() if path)


_matches_domain = matches_domain


def check_freshness(
    repo: Path | str,
    *,
    base_ref: str,
    head_ref: str,
    conflict_domains: list[str] | None = None,
) -> dict[str, Any]:
    repo = require_repository(repo)
    base_sha = resolve_ref(repo, base_ref)
    head_sha = resolve_ref(repo, head_ref)
    domains = sorted(set(conflict_domains or []))
    if run_git(repo, "merge-base", "--is-ancestor", base_sha, head_sha, check=False).returncode == 0:
        return {
            "classification": "current",
            "baseRef": base_ref,
            "headRef": head_ref,
            "baseSha": base_sha,
            "headSha": head_sha,
            "baseChangedPaths": [],
            "headChangedPaths": [],
            "overlappingPaths": [],
            "conflictDomains": domains,
        }

    merge_base = run_git(repo, "merge-base", base_sha, head_sha).stdout.strip()
    merge = run_git(repo, "merge-tree", "--write-tree", base_sha, head_sha, check=False)
    if merge.returncode not in {0, 1}:
        detail = merge.stderr.strip() or merge.stdout.strip()
        raise ValidationError(f"git merge-tree failed: {detail}")
    base_changed = _changed_paths(repo, merge_base, base_sha)
    head_changed = _changed_paths(repo, merge_base, head_sha)
    overlap = sorted(set(base_changed) & set(head_changed))
    touches_domain = sorted(
        path
        for path in base_changed
        if any(_matches_domain(path, domain) for domain in domains)
    )
    if merge.returncode == 1:
        classification = "conflicted"
    elif overlap or touches_domain:
        classification = "materially_stale"
    else:
        classification = "nonmaterially_stale"
    return {
        "classification": classification,
        "baseRef": base_ref,
        "headRef": head_ref,
        "baseSha": base_sha,
        "headSha": head_sha,
        "mergeBase": merge_base,
        "baseChangedPaths": base_changed,
        "headChangedPaths": head_changed,
        "overlappingPaths": overlap,
        "conflictDomainPaths": touches_domain,
        "conflictDomains": domains,
    }


def record_freshness(
    store: StateStore,
    *,
    repo: Path | str,
    task_id: str,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool, dict[str, Any]]:
    """Classify a task branch against the scope base and record the result."""
    repo = require_repository(repo)
    state = store.read()
    try:
        task = state["tasks"][task_id]
    except KeyError as error:
        raise ValidationError(f"unknown task: {task_id}") from error
    base_ref = state["scope"].get("baseRef")
    if not isinstance(base_ref, str) or not base_ref:
        raise IllegalTransition("freshness recording requires a configured scope base ref")
    head_sha = task.get("headSha")
    if not isinstance(head_sha, str) or not head_sha:
        raise IllegalTransition("freshness recording requires a task head SHA")
    result = check_freshness(
        repo,
        base_ref=base_ref,
        head_ref=head_sha,
        conflict_domains=task["conflictDomains"],
    )
    state, duplicate = apply_event(
        store,
        event_type="freshness.recorded",
        task_id=task_id,
        data=result,
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
    )
    return state, duplicate, result
