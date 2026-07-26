"""Small, dependency-free Git helpers used by controller adapters."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .errors import ValidationError


_REF_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def validate_ref_name(name: Any, *, what: str = "ref") -> str:
    """Reject anything Git could read as an option or a malformed ref.

    Ref names reach `git branch` and `git worktree add` as positional
    arguments. A value like ``-D`` is read as a flag: passing it as a base ref
    ran `git branch -D <start>` and deleted that branch before any later
    validation could fail. Validate before Git ever sees the value.
    """
    if not isinstance(name, str) or not name:
        raise ValidationError(f"{what} must be a non-empty string")
    if len(name) > 255:
        raise ValidationError(f"{what} is too long: {name[:40]}...")
    if not _REF_NAME.match(name):
        raise ValidationError(
            f"{what} {name!r} is not a valid Git ref name; it must start with a letter or "
            "digit and contain only letters, digits, '.', '_', '/' and '-'"
        )
    for forbidden in ("..", "//", "@{", ".lock/"):
        if forbidden in name:
            raise ValidationError(f"{what} {name!r} must not contain {forbidden!r}")
    if name.endswith((".lock", "/", ".")):
        raise ValidationError(f"{what} {name!r} has an invalid ending")
    return name


def run_git(repo: Path | str, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git failure"
        raise ValidationError(f"git {' '.join(arguments)}: {detail}")
    return result


def require_repository(repo: Path | str) -> Path:
    repo = Path(repo).resolve()
    result = run_git(repo, "rev-parse", "--show-toplevel")
    return Path(result.stdout.strip())


def resolve_ref(repo: Path | str, ref: str) -> str:
    return run_git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def branch_exists(repo: Path | str, branch: str) -> bool:
    return run_git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0


def is_ancestor(repo: Path | str, ancestor: str, descendant: str) -> bool:
    return run_git(
        repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False
    ).returncode == 0


def worktree_entries(repo: Path | str) -> list[dict[str, str]]:
    output = run_git(repo, "worktree", "list", "--porcelain").stdout
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        entries.append(current)
    return entries


def worktree_at(repo: Path | str, path: Path | str) -> dict[str, str] | None:
    resolved = str(Path(path).resolve())
    for entry in worktree_entries(repo):
        if entry.get("worktree") == resolved:
            return entry
    return None


def worktree_branch(path: Path | str) -> str | None:
    """The branch a worktree has checked out, or None when detached."""
    result = run_git(path, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return result.stdout.strip() or None


def wdd_root(repo: Path) -> Path:
    """Managed worktrees live beside the repository, never inside its working tree."""
    return repo.parent / f"{repo.name}.wdd"


def worktree_override(repo: Path, path: Path | str, scope_id: str, task_id: str) -> str | None:
    """Return a storable override, or None when the location is the default.

    The default location is a pure function of (repo, scope, task), so storing
    it would bake in the directory name of whichever checkout created it — a
    clone into a differently-named directory would then resolve back to the
    original machine's worktree. Only a caller-chosen path is worth recording,
    and that is stored relative to the repository.
    """
    resolved = Path(path).resolve()
    if resolved == task_worktree_path(repo, scope_id, task_id).resolve():
        return None
    try:
        return os.path.relpath(resolved, repo.resolve())
    except ValueError:
        # Different drive on Windows; nothing relative can express it.
        return str(resolved)


def worktree_for(
    repo: Path | str, scope_id: str, task_id: str, override: str | Path | None = None
) -> Path:
    """Where this task's worktree lives, for this checkout.

    The repository path is canonicalized so callers that pass an alias (on
    macOS, /var vs /private/var) derive the same location as ``start``.
    """
    repo = Path(repo).resolve()
    if override:
        path = Path(override)
        return path if path.is_absolute() else (repo / path).resolve()
    return task_worktree_path(repo, scope_id, task_id)


def task_worktree_path(repo: Path, scope_id: str, task_id: str) -> Path:
    return wdd_root(repo) / "worktrees" / scope_id / task_id


def integration_worktree_path(repo: Path, scope_id: str) -> Path:
    return wdd_root(repo) / "integration" / scope_id


def ensure_worktree(repo: Path, path: Path, branch: str, *, base_ref: str | None) -> str:
    """Create or verify one worktree checked out on ``branch``. Returns the action taken."""
    validate_ref_name(branch, what="branch")
    if base_ref is not None:
        validate_ref_name(base_ref, what="base ref")
    existing = worktree_at(repo, path)
    if existing:
        expected = f"refs/heads/{branch}"
        if existing.get("branch") != expected:
            raise ValidationError(
                f"worktree {path} is checked out on {existing.get('branch')}, not {expected}"
            )
        if resolve_ref(path, "HEAD") != resolve_ref(repo, branch):
            raise ValidationError(f"worktree {path} HEAD does not match branch {branch}")
        return "reuse"
    if path.exists():
        raise ValidationError(f"worktree path exists but is not managed by Git: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if branch_exists(repo, branch):
        run_git(repo, "worktree", "add", str(path), branch)
        return "attach_existing_branch"
    if not base_ref:
        raise ValidationError(f"cannot create branch {branch} without a base ref")
    run_git(repo, "worktree", "add", "-b", branch, str(path), base_ref)
    return "create_branch_and_worktree"
