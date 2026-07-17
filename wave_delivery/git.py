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
