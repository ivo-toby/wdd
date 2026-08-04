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

    Precedence: `!` on the header is major; otherwise a BREAKING CHANGE/
    BREAKING-CHANGE footer anywhere in the body is major regardless of
    whether the header is a well-formed conventional header (a plain-
    subject commit with a BREAKING CHANGE footer still escalates -- the
    footer scan is not gated on the header having matched); otherwise a
    malformed/non-conventional header is 'patch'; otherwise `feat` is
    minor, everything else patch. An explicit `Release-Bump:` trailer is
    then applied, but only if it would RAISE that base classification --
    spec: "acts as an explicit override for the exceptional case" is a
    ratchet, not a downgrade path, so it can never pull a `feat!:` or a
    BREAKING CHANGE footer back down.
    """
    if not message or not message.strip():
        return "patch"
    header = message.splitlines()[0].strip()
    match = _HEADER_RE.match(header)
    if match and match.group("bang"):
        base = "major"
    elif _BREAKING_FOOTER_RE.search(message):
        base = "major"
    elif match and match.group("type").lower() == "feat":
        base = "minor"
    else:
        base = "patch"

    override = _RELEASE_BUMP_RE.search(message)
    if override:
        candidate = override.group(1)
        if _BUMP_ORDER[candidate] > _BUMP_ORDER[base]:
            return candidate
    return base


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

    A genuinely absent tag makes `ls-remote` exit 0 with empty output --
    that is the only case that means "no tag" here. A transport/auth
    failure raises `GitError` and is deliberately NOT caught: swallowing
    it and returning None would make a network blip look identical to "tag
    doesn't exist", and the CAS loop would then push as if it had verified
    that -- fail closed by letting the caller see the real error instead.
    """
    output = run_git(["ls-remote", "--tags", "origin", f"refs/tags/{tag}"])
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

        write_version(version)
        run_git(["add", "VERSION"])
        # Bootstrap (no prior tag) can compute a version identical to what
        # VERSION already reads -- nothing staged, so `git commit` would
        # exit 1 and raise. `git diff --cached --quiet` exits 0 (no
        # exception) when nothing is staged, non-zero (GitError) when
        # there is a real diff; skip straight to tagging the current head
        # in the former case -- v0.1.0 still gets tagged, just without a
        # release commit to carry it.
        try:
            run_git(["diff", "--cached", "--quiet"])
            release_commit = head
        except GitError:
            run_git(["commit", "-m", f"chore(release): v{version} [skip ci]"])
            release_commit = run_git(["rev-parse", "HEAD"])

        # Triage against the commit the tag would actually be created at
        # (release_commit), not the pre-bump `head` -- comparing against
        # `head` made the idempotent arm here unreachable in the normal
        # (non-bootstrap) case, since the tag is never meant to point at
        # the parent commit.
        tag_commit = _tag_commit(run_git, tag)
        action = decide_action(tag_commit, release_commit)
        if action == "idempotent":
            print(f"{tag} already at {release_commit}; idempotent success")
            return 0
        if action == "foreign":
            # Spec: "main moved -> recompute and retry" -- a concurrent,
            # legitimate release can land between this attempt's fetch and
            # this check; `ls-remote` above talks to origin live, so it can
            # see that push before we do. Re-checking origin/main here
            # (rather than trusting the `head` this attempt started with)
            # tells the two cases apart: main really did move under us ->
            # loop and recompute from scratch; main is exactly where we
            # left it -> a truly foreign/unreachable tag, fail closed.
            # `origin/main` is a local tracking ref -- re-fetch before
            # re-reading it, or this would just echo back the same `head`
            # this attempt already fetched and never detect the move.
            run_git(["fetch", "--tags", "origin", "main"])
            refreshed_head = run_git(["rev-parse", "origin/main"])
            if refreshed_head != head:
                print(
                    f"wddctl release: main moved during attempt {attempt} "
                    f"({head} -> {refreshed_head}); recomputing"
                )
                continue
            print(
                f"wddctl release: refusing -- {tag} exists at {tag_commit}, "
                f"expected release commit {release_commit}",
                file=sys.stderr,
            )
            return 1

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
