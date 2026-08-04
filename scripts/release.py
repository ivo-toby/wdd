#!/usr/bin/env python3
"""Semver automation on `main` (spec Sec3).

Bump classification is a full-message Conventional Commits parser
(`parse_commit`, `compute_bump`); version arithmetic is `next_version`;
the rest is a bounded compare-and-swap loop (`main`) that builds a release
commit bumping VERSION, tags it, and pushes commit+tag atomically. All git
interaction goes through `run_git` so the CAS loop is unit-testable with a
fake -- no network, no real tags, in tests.

No prior tag bootstraps v0.1.0 regardless of commit content (spec: "No
prior tag -> bootstrap v0.1.0").
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATH = ROOT / "VERSION"

RunGit = Callable[[list[str]], str]

_BUMP_ORDER = {"patch": 0, "minor": 1, "major": 2}

# Header: `type(scope)!: description`. Scope and `!` are both optional.
_HEADER_RE = re.compile(r"^(?P<type>[A-Za-z]+)(\((?P<scope>[^)]+)\))?(?P<bang>!)?:\s*\S")
_BREAKING_FOOTER_RE = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
_RELEASE_BUMP_RE = re.compile(r"^Release-Bump:\s*(minor|major)\s*$", re.MULTILINE)
# This repo's merge-commit subjects (real merges, not squashes -- squash
# titles are themselves conventional commits per house style and are
# classified normally by parse_commit).
_MERGE_SUBJECT_RE = re.compile(r"^Merge (pull request|branch|remote-tracking branch)\b")

_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


class GitError(RuntimeError):
    """A `git` invocation exited non-zero (or otherwise failed to run)."""


def parse_commit(message: str) -> str:
    """Classify one full commit message: 'major', 'minor', or 'patch'.

    Precedence: an explicit `Release-Bump:` trailer wins outright (spec:
    "acts as an explicit override for the exceptional case"), then a
    malformed/non-conventional header falls back to 'patch', then `!` on
    the header or a BREAKING CHANGE/BREAKING-CHANGE footer anywhere in the
    body is major, then `feat` is minor, everything else patch.
    """
    if not message or not message.strip():
        return "patch"
    override = _RELEASE_BUMP_RE.search(message)
    if override:
        return override.group(1)
    header = message.splitlines()[0].strip()
    match = _HEADER_RE.match(header)
    if not match:
        return "patch"
    if match.group("bang"):
        return "major"
    if _BREAKING_FOOTER_RE.search(message):
        return "major"
    if match.group("type").lower() == "feat":
        return "minor"
    return "patch"


def _is_merge_subject(message: str) -> bool:
    first_line = message.splitlines()[0].strip() if message else ""
    return bool(_MERGE_SUBJECT_RE.match(first_line))


def compute_bump(messages: list[str]) -> str:
    """Aggregate a commit range to the single highest bump it implies.

    Real merge-commit subjects are excluded (their constituents, present
    as their own entries in `messages`, are still classified); an empty
    or all-excluded list is 'patch' (no signal -> no escalation).
    """
    bump = "patch"
    for message in messages:
        if _is_merge_subject(message):
            continue
        candidate = parse_commit(message)
        if _BUMP_ORDER[candidate] > _BUMP_ORDER[bump]:
            bump = candidate
    return bump


def next_version(last_tag: str | None, bump: str) -> str:
    """Next `X.Y.Z` (no `v` prefix) given the last release tag and a bump.

    No prior tag bootstraps 0.1.0 unconditionally -- bump is not consulted.
    """
    if last_tag is None:
        return "0.1.0"
    match = _TAG_RE.match(last_tag)
    if not match:
        raise ValueError(f"malformed release tag: {last_tag!r}")
    major, minor, patch = (int(part) for part in match.groups())
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown bump: {bump!r}")


def decide_action(tag_commit: str | None, target_commit: str) -> str:
    """CAS triage (spec Sec3), isolated as a pure decision.

    'push': the intended tag does not exist yet -- proceed.
    'idempotent': it exists and already points at the intended commit --
      a prior run (or this one, re-triggered) already finished; exit green.
    'foreign': it exists at some OTHER commit -- never auto-skip a version;
      fail closed and name both commits.
    """
    if tag_commit is None:
        return "push"
    if tag_commit == target_commit:
        return "idempotent"
    return "foreign"


def real_run_git(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        stderr = getattr(error, "stderr", "") or ""
        raise GitError(f"git {' '.join(args)} failed: {stderr}".strip()) from error
    return result.stdout.strip()


def _last_tag(run_git: RunGit) -> str | None:
    try:
        return run_git(["describe", "--tags", "--abbrev=0", "--match", "v*", "origin/main"])
    except GitError:
        return None


_RECORD_SEP = "\x1e"


def _commit_messages(run_git: RunGit, last_tag: str | None, target_commit: str) -> list[str]:
    range_spec = f"{last_tag}..{target_commit}" if last_tag else target_commit
    output = run_git(["log", f"--format=%B{_RECORD_SEP}", range_spec])
    return [record.strip() for record in output.split(_RECORD_SEP) if record.strip()]


def _tag_commit(run_git: RunGit, tag: str) -> str | None:
    """The commit `tag` points to on `origin`, queried authoritatively.

    Goes straight to the remote via `ls-remote` rather than local
    `refs/tags/*` -- a local tag ref left over from an earlier CAS attempt
    in this same run (created but never pushed, because that push was the
    one that got rejected) would otherwise be misread as an existing
    collision on the next attempt.
    """
    try:
        output = run_git(["ls-remote", "--tags", "origin", f"refs/tags/{tag}"])
    except GitError:
        return None
    if not output:
        return None
    return output.split()[0]


def _write_version(version: str) -> None:
    VERSION_PATH.write_text(f"{version}\n", encoding="utf-8")


def main(
    argv: list[str] | None = None,
    *,
    run_git: RunGit | None = None,
    write_version: Callable[[str], None] = _write_version,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = 5,
) -> int:
    del argv  # no flags; the workflow invokes this with no arguments
    run_git = run_git or real_run_git

    for attempt in range(1, max_attempts + 1):
        run_git(["fetch", "--tags", "origin", "main"])
        run_git(["switch", "-C", "main", "origin/main"])
        head = run_git(["rev-parse", "origin/main"])
        last_tag = _last_tag(run_git)

        # Already tagged at HEAD: nothing to release, whether from a prior
        # successful run of this workflow or a manual re-trigger.
        try:
            at_head = run_git(["describe", "--tags", "--exact-match", "origin/main"])
        except GitError:
            at_head = None
        if at_head is not None and _TAG_RE.match(at_head):
            print(f"{at_head} already at {head}; idempotent success")
            return 0

        messages = _commit_messages(run_git, last_tag, head)
        bump = compute_bump(messages) if messages else "patch"
        version = next_version(last_tag, bump)
        tag = f"v{version}"

        tag_commit = _tag_commit(run_git, tag)
        action = decide_action(tag_commit, head)
        if action == "idempotent":
            print(f"{tag} already at {head}; idempotent success")
            return 0
        if action == "foreign":
            print(
                f"wddctl release: refusing -- {tag} exists at {tag_commit}, "
                f"expected release commit {head}",
                file=sys.stderr,
            )
            return 1

        write_version(version)
        run_git(["add", "VERSION"])
        run_git(["commit", "-m", f"chore(release): v{version} [skip ci]"])
        release_commit = run_git(["rev-parse", "HEAD"])
        run_git(["tag", "-f", tag, release_commit])

        try:
            run_git(["push", "--atomic", "origin", "main", tag])
        except GitError as error:
            if attempt == max_attempts:
                print(f"wddctl release: CAS loop exhausted retries: {error}", file=sys.stderr)
                return 1
            backoff = 2 ** (attempt - 1)
            print(f"wddctl release: push rejected (attempt {attempt}), retrying in {backoff}s")
            sleep(backoff)
            continue

        print(f"pushed {tag} ({release_commit})")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
