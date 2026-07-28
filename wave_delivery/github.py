"""The PR execution surface: push a task branch and drive `gh` for the rest.

Every network-shaped operation for the "pr" merge surface goes through this
module so tests can stub a fake `gh` found on PATH and a local bare-repo
`origin` remote -- no live GitHub anywhere, ever.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import IllegalTransition
from .git import run_git


def push_branch(repo: Path | str, branch: str) -> None:
    """Push a task branch to origin, creating or advancing its remote ref.

    Run from the repository itself, not the task's worktree. The branch is a
    ref in the shared repo's object store -- reachable from ``repo`` alone --
    so this works whether or not the task's worktree is still attached, and
    it avoids assuming the worktree exists at submit time at all.
    """
    result = run_git(repo, "push", "-u", "origin", f"{branch}:{branch}", check=False)
    if result.returncode != 0:
        raise IllegalTransition(f"git push origin {branch} failed: {_stderr_excerpt(result)}")


def create_pr(repo: Path | str, branch: str, base: str, title: str, body: str) -> str:
    """Open a PR via `gh pr create`, returning its URL.

    Real `gh pr create` writes the created PR's URL to stdout as its result;
    the fake fixture mirrors exactly that contract.
    """
    result = _run_gh(
        repo,
        "pr", "create",
        "--head", branch,
        "--base", base,
        "--title", title,
        "--body", body,
    )
    url = result.stdout.strip().splitlines()[-1].strip() if result.stdout.strip() else ""
    if not url:
        raise IllegalTransition(f"gh pr create for {branch} produced no URL")
    return url


def comment_pr(repo: Path | str, pr_ref: str, body: str) -> None:
    """Post one comment to an existing PR (accepts a PR number, branch, or URL)."""
    _run_gh(repo, "pr", "comment", pr_ref, "--body", body)


def _run_gh(repo: Path | str, *arguments: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["gh", *arguments],
        cwd=str(repo),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise IllegalTransition(f"gh {' '.join(arguments)} failed: {_stderr_excerpt(result)}")
    return result


def _stderr_excerpt(result: subprocess.CompletedProcess[str], limit: int = 400) -> str:
    detail = (result.stderr or result.stdout or "unknown failure").strip()
    return detail[:limit]
