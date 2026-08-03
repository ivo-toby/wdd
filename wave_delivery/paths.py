"""Typed artifact-path namespaces, one resolver (spec Sec1 "Typed path
namespaces, one resolver").

Every site that turns a serialized ref (plan.json's `specPath`/`context`,
intake's research artifacts, handover's input-source resolution, lint's
brief/spec/design reads) into a real filesystem path calls
`resolve_artifact` -- no consumer resolves paths its own way (Global
Constraints, epic-scoped-state plan). A ref's meaning is fixed LEXICALLY,
by its literal text, never by existence probing: what you get back never
depends on what happens to be on disk today.

Namespace table (spec Sec1):

- `shared-context/...`            -> always global: `<wdd_dir>/shared-context/...`
- `tasks/...`, `research/...`,
  `spec.md`, `design.md`, `plan.json` -> the active epic's namespace
- absolute paths, `..` segments, and refs beginning `epics/`, `archive/`,
  or `dispatch/`, plus the reserved filename `record.json`            -> rejected outright

Anything else (a ref naming no recognized namespace) is refused too: the
table above is closed, not a partial list with a permissive fallback --
that is what makes this a *typed* resolver rather than a generic
containment check.

`epic=None` is this branch's transition mode (Task 1 of the
epic-scoped-state plan): every call site rewired in this task still calls
with `epic=None`, so epic-namespaced refs resolve FLAT against `.wdd/`,
byte-identical to pre-epic resolution -- `epics/<slug>/` directories do
not exist yet on this branch. Task 4 is what starts passing a real epic
slug and flips epic-namespaced refs over to `.wdd/epics/<epic>/...`.
"""

from __future__ import annotations

from pathlib import Path

from .errors import ValidationError


# First path segment (or, for the three bare filenames, the whole ref)
# decides the namespace. Closed set: nothing outside this table resolves.
_GLOBAL_SEGMENTS = frozenset({"shared-context"})
_EPIC_SEGMENTS = frozenset({"tasks", "research"})
_EPIC_EXACT_REFS = frozenset({"spec.md", "design.md", "plan.json"})
_REJECTED_SEGMENTS = frozenset({"epics", "archive", "dispatch"})
_RESERVED_BASENAMES = frozenset({"record.json"})


def resolve_artifact(ref: str, *, wdd_dir: Path | str, epic: str | None) -> Path:
    """Resolve a namespace-relative artifact ref to an absolute filesystem path.

    `ref` is `.wdd`-relative artifact-reference syntax, `<path>[#anchor]`
    (spec Sec3) -- the anchor is reading guidance, stripped here before
    resolution (existing doctrine every prior call site enforced itself
    with `ref.split("#", 1)[0]`; centralized here so no consumer repeats
    it). Raises `ValidationError` naming the offending `ref` for: an empty
    ref or an empty path before `#`, an absolute path, any `..` segment, a
    ref beginning `epics/`, `archive/`, or `dispatch/`, the reserved
    filename `record.json` anywhere in the ref, or a ref that names no
    recognized namespace at all.

    `epic=None` resolves `tasks/`, `research/`, `spec.md`, `design.md`,
    `plan.json` refs flat against `wdd_dir` itself -- the Task 1
    transition-mode fallback described in this module's docstring. Once an
    `epic` slug is given (Task 4), those same refs resolve under
    `wdd_dir/epics/<epic>/...` instead; `shared-context/` refs are
    ALWAYS global regardless of `epic`.

    Does not check existence -- callers check `resolved.exists()`
    themselves (the existing doctrine; existence and containment are
    orthogonal concerns).
    """
    if not isinstance(ref, str) or not ref:
        raise ValidationError(f"artifact ref must be a non-empty string: {ref!r}")

    path_part = ref.split("#", 1)[0]
    if not path_part:
        raise ValidationError(f"artifact ref has no path before '#': {ref!r}")

    if path_part.startswith("/") or Path(path_part).is_absolute():
        raise ValidationError(f"artifact ref must not be an absolute path: {ref!r}")

    segments = path_part.split("/")
    if ".." in segments:
        raise ValidationError(f"artifact ref must not contain '..' segments: {ref!r}")

    first = segments[0]
    if first in _REJECTED_SEGMENTS:
        raise ValidationError(
            f"artifact ref must not begin with the reserved {first!r} namespace: {ref!r}"
        )
    if segments[-1] in _RESERVED_BASENAMES:
        raise ValidationError(
            f"artifact ref names the reserved filename 'record.json': {ref!r}"
        )

    wdd_dir = Path(wdd_dir)
    if first in _GLOBAL_SEGMENTS:
        root = wdd_dir
    elif first in _EPIC_SEGMENTS or path_part in _EPIC_EXACT_REFS:
        root = wdd_dir if epic is None else wdd_dir / "epics" / epic
    else:
        raise ValidationError(
            "artifact ref is not in a recognized namespace (shared-context/, "
            f"tasks/, research/, spec.md, design.md, plan.json): {ref!r}"
        )

    candidate = root / path_part
    resolved = candidate.resolve()
    root_resolved = root.resolve()
    # Lexical '..'-rejection above already forbids climbing out of `root`
    # textually; this guards the remaining case -- a symlink somewhere
    # under `root` pointing outside it -- the same defense
    # `intake.resolve_within_wdd` applied before this resolver existed.
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValidationError(
            f"artifact ref escapes its namespace root: {ref!r} (resolved to {resolved})"
        )
    return resolved
