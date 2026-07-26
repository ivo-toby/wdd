"""Small, dependency-free Git helpers used by controller adapters."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import ValidationError


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


def wdd_root(repo: Path) -> Path:
    """Managed worktrees live beside the repository, never inside its working tree."""
    return repo.parent / f"{repo.name}.wdd"


def task_worktree_path(repo: Path, scope_id: str, task_id: str) -> Path:
    return wdd_root(repo) / "worktrees" / scope_id / task_id


def integration_worktree_path(repo: Path, scope_id: str) -> Path:
    return wdd_root(repo) / "integration" / scope_id


def ensure_worktree(repo: Path, path: Path, branch: str, *, base_ref: str | None) -> str:
    """Create or verify one worktree checked out on ``branch``. Returns the action taken."""
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
