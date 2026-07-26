"""Conflict-domain semantics.

Both admission control and freshness classification reason about conflict
domains. They used to do it differently — freshness glob-matched while
admission compared raw strings — so a task holding ``src/auth/**`` did not
block one declaring ``src/auth/token.py`` and both were admitted onto the same
files. The semantics live here so the two call sites cannot drift again.

Where overlap is undecidable the answer is "they overlap". Blocking two tasks
that would not actually have collided costs some parallelism; failing to block
two that would costs a silently lost diff.
"""

from __future__ import annotations

import fnmatch


WILDCARDS = "*?["


def matches_domain(path: str, domain: str) -> bool:
    """Does one concrete path fall inside one conflict domain?"""
    if domain.endswith("/**"):
        root = domain[:-3]
        return path == root or path.startswith(root + "/")
    return fnmatch.fnmatch(path, domain) or path == domain


def literal_prefix(domain: str) -> str:
    """The leading path segments of a domain that contain no wildcard.

    ``src/auth/**`` -> ``src/auth``; ``src/*.ts`` -> ``src``; ``*.py`` -> ``""``.
    An empty prefix means the pattern can match at any depth.
    """
    segments: list[str] = []
    for segment in domain.split("/"):
        if any(wildcard in segment for wildcard in WILDCARDS):
            break
        segments.append(segment)
    return "/".join(segments)


def _covers(prefix: str, other: str) -> bool:
    """Is ``prefix`` a path-prefix of ``other``, comparing whole segments?"""
    if prefix == "":
        return True
    return other == prefix or other.startswith(prefix + "/")


def domains_overlap(first: str, second: str) -> bool:
    """Could two conflict domains ever cover the same file?"""
    if first == second:
        return True
    if fnmatch.fnmatch(first, second) or fnmatch.fnmatch(second, first):
        return True
    if matches_domain(first, second) or matches_domain(second, first):
        return True
    first_prefix, second_prefix = literal_prefix(first), literal_prefix(second)
    return _covers(first_prefix, second_prefix) or _covers(second_prefix, first_prefix)


def overlapping_domains(wanted: list[str], held: dict[str, str]) -> dict[str, str]:
    """Map each wanted domain that collides to the task holding the collision."""
    collisions: dict[str, str] = {}
    for domain in wanted:
        for held_domain, holder in held.items():
            if domains_overlap(domain, held_domain):
                collisions.setdefault(domain, holder)
                break
    return collisions
