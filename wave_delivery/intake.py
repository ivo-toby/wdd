"""The intake ladder: fingerprint-bound spec, research, and design records.

Spec Sec1/Sec2 (front-half redesign): between `ratify` and `plan apply`,
`next` walks new rungs, one at a time -- ``agree_spec -> research ->
agree_design -> plan``. Evidence lives in ``state["intake"]``, written by the
three rung verbs below. Every approval binds to the approved bytes (the same
doctrine as ``config.governance_fingerprint``): each record carries a
SHA-256 of the artifact at approval time, so an edit after approval is
detectable drift, never silently accepted.

The ladder is ordered and cascades: re-approving a rung invalidates every
record after it (spec.py's ``intake.spec_approved`` clears research+design;
research clears design; all three clear ``scope.approval``, since a plan was
approved against upstream bytes that just changed underneath it). Clearing
happens INSIDE the ``apply_mutation`` mutator, on the locked state -- the
same "handwritten mutator, apply_mutation supplies the revisioned/idempotent/
locked envelope" pattern ``finalize.py`` already uses for scope-level
records that don't fit ``engine.transition``'s per-task vocabulary.

Every rung verb refuses: before ratification, on legacy scopes
(``intake.legacy``), and once the scope reaches the delivered phase. They
stay legal in setup AND execute phases on purpose -- execute-phase intake
verbs are how Task 4's drift gate gets remedied (re-approve, then re-stamp
`plan apply --approved-by`).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .config import derive_effective, effective_config_digest, load_layers, save_overlay
from .engine import apply_mutation, utc_now
from .errors import IllegalTransition, ValidationError
from .paths import resolve_artifact
from .schema import copied_state, derived_phase


_REQUIRED_SPEC_SECTIONS = ("goal", "in scope", "out of scope", "acceptance criteria")
_REQUIRED_DESIGN_SECTIONS = (
    "components",
    "interfaces",
    "integration surfaces",
    "epic deliverable",
)
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
# The Acceptance criteria section must be WHOLLY numbered: every checklist
# line matches this exact shape, unique and contiguous from 1. A checklist
# line that does not match (wrong prefix, missing colon, non-numeric) is
# "unnumbered" and refuses the whole record -- final review must be able to
# walk 1..N with nothing outside the numbering.
_AC_LINE_RE = re.compile(r"^- \[ \] AC-(\d+):\s*\S")
# Any markdown checklist bullet, numbered or not -- used to detect the
# unnumbered-line case above.
_CHECKLIST_LINE_RE = re.compile(r"^- \[[ xX]\]")


def artifact_sha256(path: Path | str) -> str:
    """SHA-256 of an artifact's bytes, in the `governance_fingerprint` idiom."""
    data = Path(path).read_bytes()
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _require_nonempty_file(path: Path, *, label: str) -> str:
    if not path.exists() or not path.is_file():
        raise ValidationError(f"{label} does not exist; write it before recording this rung")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValidationError(f"{label} is empty")
    return text


def _headings(text: str) -> list[tuple[str, int]]:
    """(heading text, line index) for every top-level `## ` heading."""
    headings = []
    for index, line in enumerate(text.splitlines()):
        match = _HEADING_RE.match(line)
        if match:
            headings.append((match.group(1), index))
    return headings


def _require_sections(text: str, required: tuple[str, ...], *, label: str) -> list[tuple[str, int]]:
    headings = _headings(text)
    present = {name.lower() for name, _ in headings}
    missing = [name for name in required if name not in present]
    if missing:
        raise ValidationError(
            f"{label} is missing required section(s): {', '.join(missing)}"
        )
    return headings


def _section_lines(text: str, headings: list[tuple[str, int]], name: str) -> list[str]:
    lines = text.splitlines()
    names_lower = [heading.lower() for heading, _ in headings]
    index = names_lower.index(name)
    start = headings[index][1] + 1
    end = headings[index + 1][1] if index + 1 < len(headings) else len(lines)
    return lines[start:end]


def _validate_spec_text(text: str) -> int:
    """Validate spec.md's four sections and wholly-numbered acceptance criteria.

    Returns the criteria count N. Every checklist line in the "Acceptance
    criteria" section must match `- [ ] AC-<n>: ...`; the numbers must be
    unique and contiguous from 1; no unnumbered checklist line may appear in
    the section. Spec Sec2/Sec1.
    """
    headings = _require_sections(text, _REQUIRED_SPEC_SECTIONS, label="spec.md")
    section = _section_lines(text, headings, "acceptance criteria")
    numbers: list[int] = []
    for raw_line in section:
        line = raw_line.rstrip()
        if not line:
            continue
        if not _CHECKLIST_LINE_RE.match(line):
            continue
        match = _AC_LINE_RE.match(line)
        if not match:
            raise ValidationError(
                "spec.md Acceptance criteria section has an unnumbered checklist line "
                f"({line!r}); every line must match '- [ ] AC-<n>: ...'"
            )
        numbers.append(int(match.group(1)))
    if not numbers:
        raise ValidationError(
            "spec.md Acceptance criteria section has no numbered checklist items"
        )
    if len(set(numbers)) != len(numbers):
        raise ValidationError("spec.md Acceptance criteria numbers must be unique")
    if sorted(numbers) != list(range(1, len(numbers) + 1)):
        raise ValidationError(
            "spec.md Acceptance criteria numbers must be contiguous from 1 "
            f"(found {sorted(numbers)})"
        )
    return len(numbers)


def _validate_design_text(text: str) -> None:
    _require_sections(text, _REQUIRED_DESIGN_SECTIONS, label="design.md")


def resolve_within_wdd(
    wdd_dir: Path, raw_path: str, *, label: str = "research artifact", epic: str | None = None
) -> Path:
    """Resolve a `.wdd`-relative artifact ref, labeled for the caller's ref kind.

    Cross-reference: `wave_delivery/paths.py`'s `resolve_artifact` is the
    ONE typed resolver (spec Sec1, Global Constraints) -- every path/
    namespace/containment decision lives there. This is a thin,
    label-preserving wrapper kept for the call sites (and their pinned
    tests) that need the refusal message to name their own ref kind
    ("research artifact", "context ref", "task brief", ...) rather than
    `resolve_artifact`'s generic wording. `epic` defaults to `None` (Task
    1's transition-mode fallback, still flat) but every call site in THIS
    module now threads the caller's `state.get("epic")` explicitly (Task 4,
    spec Sec1: "every resolve_artifact/resolve_within_wdd call site threads
    epic=state.epic") -- the default only covers a caller with no state at
    all to read epic from.
    """
    try:
        return resolve_artifact(raw_path, wdd_dir=wdd_dir, epic=epic)
    except ValidationError as error:
        raise ValidationError(f"{label}: {error}") from error


def _require_ladder_legal(state: dict[str, Any]) -> None:
    """Shared refusal set for all three rung verbs: spec Sec1's three gates.

    Ratification first (there is nothing to approve pre-governance), then
    legacy (the migration exemption is wholesale, per schema.py), then
    delivered (the ladder is done and archived, not re-opened -- Task 5's
    `scope archive` is the only path back to a fresh ladder).
    """
    if state["constitution"]["status"] != "ratified":
        raise IllegalTransition(
            "intake verbs require the constitution to be ratified first; "
            "run 'wddctl constitution ratify --by NAME'"
        )
    # The slug is born at the top of the ladder (spec Sec1): without an
    # active epic, rung artifacts would resolve flat and plan apply's
    # SCOPE-<slug> derivation would silently never fire (Task 4 review,
    # Important). Legacy scopes are exempt below, wholesale.
    if state.get("epic") is None and (state.get("intake") or {}).get("legacy") is not True:
        raise IllegalTransition(
            "no active epic: the ladder starts with "
            "'wddctl epic new --slug SLUG' (spec: the slug is born at the "
            "top of the ladder)"
        )
    if (state.get("intake") or {}).get("legacy") is True:
        raise IllegalTransition(
            "this scope is a migrated legacy scope, exempt wholesale from the intake ladder"
        )
    if derived_phase(state) == "delivered":
        raise IllegalTransition(
            "this scope is already delivered; the intake ladder is closed. "
            "run 'wddctl scope archive --repo .' to start a fresh scope"
        )


def _clear_scope_approval(state: dict[str, Any]) -> None:
    scope = state.get("scope")
    if isinstance(scope, dict) and "approval" in scope:
        state["scope"] = {**scope}
        del state["scope"]["approval"]


def _require_configured(state: dict[str, Any]) -> None:
    """`agree_spec` refuses until `intake configure` is recorded (epic-
    scoped-state plan Task 5, spec Sec2). Checked only by `record_spec`:
    research/design already require spec first, so gating the first rung is
    sufficient -- there is no path to research/design without spec.
    """
    if (state.get("intake") or {}).get("configure") is None:
        raise IllegalTransition(
            "intake spec requires the epic to be configured first; run 'wddctl "
            "intake configure --approved-by NAME' (or --use-defaults --by NAME)"
        )


def record_configure(
    store: Any,
    wdd_dir: Path | str,
    *,
    approved_by: str | None = None,
    use_defaults: bool = False,
    by: str | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """`wddctl intake configure`: the configure step (spec Sec2), one legal
    outcome per invocation:

    - `--approved-by NAME`: approves the epic overlay AS CURRENTLY WRITTEN
      (built up beforehand via `config set --epic`).
    - `--use-defaults --by NAME`: the explicit decision to inherit
      everything -- silence is not an option. Also WRITES the empty
      overlay to disk (never leaves a stale, unapproved overlay behind).

    Exactly one form is legal; the CLI enforces this by argument shape.
    `sha256` is `effective_config_digest` of the DERIVED POST-MUTATION full
    view: layers are resolved from disk exactly ONCE (resolve-once, spec
    Sec2 "Commands that change the config they run under ... do not re-read
    disk mid-command"), then `derive_effective` recomputes `effective` from
    the retained global/defaults layers -- the same pure function `config
    set --epic` already uses, never a second implementation. Re-recording
    clears `scope.approval` ONLY -- spec/research/design do not depend on
    config, so their records are untouched.
    """
    if (approved_by is not None) == bool(use_defaults):
        raise ValidationError(
            "intake configure requires exactly one of --approved-by NAME or "
            "--use-defaults --by NAME"
        )
    if use_defaults:
        if not isinstance(by, str) or not by:
            raise ValidationError("--use-defaults requires --by NAME")
        actor = by
    else:
        if not isinstance(approved_by, str) or not approved_by:
            raise ValidationError("--approved-by requires a non-empty name")
        actor = approved_by

    wdd_dir = Path(wdd_dir)
    state = store.read()
    _require_ladder_legal(state)
    epic = state.get("epic")

    # Resolved exactly ONCE for this whole invocation (spec Sec2): every
    # byte the recorded digest binds to comes from this single snapshot, not
    # a second read taken inside the lock below.
    layers = load_layers(wdd_dir, epic)
    patch = {} if use_defaults else layers["overlay"]
    derived = derive_effective(layers, patch)
    sha256 = effective_config_digest(derived["effective"])

    def mutator(current: dict[str, Any]) -> dict[str, Any]:
        _require_ladder_legal(current)
        if current.get("epic") != epic:
            raise IllegalTransition(
                "the active epic changed since this command began; re-run "
                "'wddctl intake configure ...'"
            )
        updated = copied_state(current)
        if use_defaults:
            # The explicit decision to inherit everything is written to
            # disk too -- never leaves a nonempty, unapproved overlay
            # sitting behind an approval that claims defaults.
            save_overlay(wdd_dir, epic, {})
        updated["intake"] = dict(updated.get("intake") or {})
        updated["intake"]["configure"] = {"by": actor, "at": utc_now(), "sha256": sha256}
        _clear_scope_approval(updated)
        return updated

    return apply_mutation(
        store,
        event_type="intake.configured",
        task_id=None,
        data={},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )


def record_spec(
    store: Any,
    wdd_dir: Path | str,
    *,
    approved_by: str,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Record `.wdd/spec.md` approval; cascades clear research, design, scope.approval."""
    if not isinstance(approved_by, str) or not approved_by:
        raise ValidationError("--approved-by requires a non-empty name")
    wdd_dir = Path(wdd_dir)
    # state must be read before the first resolution: spec.md's epic-namespace
    # location depends on state.epic (Task 4, spec Sec1) -- unlike Task 1's
    # transition-mode `epic=None`, this is no longer path-independent of state.
    state = store.read()
    _require_ladder_legal(state)
    _require_configured(state)

    def _snapshot(epic: str | None) -> tuple[int, str]:
        spec_path = resolve_within_wdd(wdd_dir, "spec.md", label="spec", epic=epic)
        text = _require_nonempty_file(spec_path, label="spec.md")
        return _validate_spec_text(text), artifact_sha256(spec_path)

    # Fail fast, before the lock -- mirrors finalize.py's two-stage guard.
    _snapshot(state.get("epic"))

    def mutator(current: dict[str, Any]) -> dict[str, Any]:
        _require_ladder_legal(current)
        _require_configured(current)
        # Re-derived under the lock: an edit to spec.md between the pre-lock
        # read and now must not approve stale bytes -- an approval of text
        # that has since changed approves nothing.
        criteria, sha256 = _snapshot(current.get("epic"))
        updated = copied_state(current)
        updated["intake"] = dict(updated.get("intake") or {})
        updated["intake"]["spec"] = {
            "by": approved_by,
            "at": utc_now(),
            "criteria": criteria,
            "sha256": sha256,
        }
        updated["intake"].pop("research", None)
        updated["intake"].pop("design", None)
        _clear_scope_approval(updated)
        return updated

    return apply_mutation(
        store,
        event_type="intake.spec_approved",
        task_id=None,
        data={},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )


def record_research(
    store: Any,
    wdd_dir: Path | str,
    *,
    by: str,
    done_artifacts: list[str] | None = None,
    skip_reason: str | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Record research done (with hashed artifacts) or an attributed skip.

    Exactly one of `done_artifacts`/`skip_reason` is required -- silence is
    not an option, and neither form is anonymous (spec Sec1). Cascades clear
    design and scope.approval.
    """
    if not isinstance(by, str) or not by:
        raise ValidationError("--by requires a non-empty name")
    if (done_artifacts is not None) == (skip_reason is not None):
        raise ValidationError(
            "intake research requires exactly one of --done (with --artifacts) "
            "or --skip (with --reason)"
        )
    if skip_reason is not None and not skip_reason.strip():
        raise ValidationError("--reason requires a non-empty string")
    wdd_dir = Path(wdd_dir)
    # state read up front, mirroring record_spec: artifact resolution below
    # depends on state.epic (Task 4).
    state = store.read()
    _require_ladder_legal(state)
    if (state.get("intake") or {}).get("spec") is None:
        raise IllegalTransition(
            "intake research requires a recorded spec first; "
            "run 'wddctl intake spec --approved-by NAME'"
        )

    def _snapshot_artifacts(epic: str | None) -> list[dict[str, str]] | None:
        if done_artifacts is None:
            return None
        if not done_artifacts:
            raise ValidationError("--done requires at least one --artifacts path")
        records = []
        for raw_path in done_artifacts:
            resolved = resolve_within_wdd(wdd_dir, raw_path, epic=epic)
            if not resolved.exists() or not resolved.is_file():
                raise ValidationError(
                    f"research artifact does not exist or is not a regular file: {raw_path}"
                )
            if resolved.stat().st_size == 0:
                raise ValidationError(f"research artifact is empty: {raw_path}")
            # Recorded as the namespace-relative REF itself (anchor stripped),
            # never a path derived from the resolved physical location: once
            # epic is real, `resolved` lives under `epics/<epic>/...`, and a
            # "path" of that shape would be rejected by `resolve_artifact` the
            # next time it is read back (it refuses any ref beginning
            # `epics/`). This mirrors the fix ce8e2e9 made to
            # handover.inputs_status's READ side; here it is the WRITE side.
            records.append(
                {"path": raw_path.split("#", 1)[0], "sha256": artifact_sha256(resolved)}
            )
        return records

    # Fail fast, before the lock.
    _snapshot_artifacts(state.get("epic"))

    def mutator(current: dict[str, Any]) -> dict[str, Any]:
        _require_ladder_legal(current)
        if (current.get("intake") or {}).get("spec") is None:
            raise IllegalTransition(
                "intake research requires a recorded spec first; "
                "run 'wddctl intake spec --approved-by NAME'"
            )
        artifacts = _snapshot_artifacts(current.get("epic"))
        updated = copied_state(current)
        updated["intake"] = dict(updated.get("intake") or {})
        if artifacts is not None:
            updated["intake"]["research"] = {
                "by": by,
                "at": utc_now(),
                "done": True,
                "artifacts": artifacts,
            }
        else:
            updated["intake"]["research"] = {
                "by": by,
                "at": utc_now(),
                "skipped": True,
                "reason": skip_reason,
            }
        updated["intake"].pop("design", None)
        _clear_scope_approval(updated)
        return updated

    return apply_mutation(
        store,
        event_type="intake.research_recorded",
        task_id=None,
        data={},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )


def record_design(
    store: Any,
    wdd_dir: Path | str,
    *,
    approved_by: str,
    deliverable_command: str,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Record `.wdd/design.md` approval plus the epic deliverable command.

    `--deliverable-command` is required and non-empty -- the epic
    deliverable's proof is not optional (spec Sec2). Cascades clear
    scope.approval.
    """
    if not isinstance(approved_by, str) or not approved_by:
        raise ValidationError("--approved-by requires a non-empty name")
    if not isinstance(deliverable_command, str) or not deliverable_command.strip():
        raise ValidationError(
            "--deliverable-command is required and must be a non-empty string: "
            "the epic deliverable's proof is not optional"
        )
    wdd_dir = Path(wdd_dir)
    state = store.read()
    _require_ladder_legal(state)
    if (state.get("intake") or {}).get("research") is None:
        raise IllegalTransition(
            "intake design requires recorded research first; run 'wddctl intake research "
            "--done --by NAME --artifacts PATH...' or '--skip --by NAME --reason \"...\"'"
        )

    def _snapshot(epic: str | None) -> str:
        design_path = resolve_within_wdd(wdd_dir, "design.md", label="design", epic=epic)
        text = _require_nonempty_file(design_path, label="design.md")
        _validate_design_text(text)
        return artifact_sha256(design_path)

    # Fail fast, before the lock.
    _snapshot(state.get("epic"))

    def mutator(current: dict[str, Any]) -> dict[str, Any]:
        _require_ladder_legal(current)
        if (current.get("intake") or {}).get("research") is None:
            raise IllegalTransition(
                "intake design requires recorded research first; run 'wddctl intake research "
                "--done --by NAME --artifacts PATH...' or '--skip --by NAME --reason \"...\"'"
            )
        sha256 = _snapshot(current.get("epic"))
        updated = copied_state(current)
        updated["intake"] = dict(updated.get("intake") or {})
        updated["intake"]["design"] = {
            "by": approved_by,
            "at": utc_now(),
            "sha256": sha256,
            "deliverableCommand": deliverable_command,
        }
        _clear_scope_approval(updated)
        return updated

    return apply_mutation(
        store,
        event_type="intake.design_approved",
        task_id=None,
        data={},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )


def intake_status(state: dict[str, Any]) -> dict[str, Any]:
    """The intake section plus which rung is next (None once complete/legacy)."""
    intake = state.get("intake") or {}
    if intake.get("legacy") is True:
        next_rung = None
    elif intake.get("spec") is None:
        next_rung = "spec"
    elif intake.get("research") is None:
        next_rung = "research"
    elif intake.get("design") is None:
        next_rung = "design"
    else:
        next_rung = None
    return {"intake": intake, "nextRung": next_rung}


def intake_drift(state: dict[str, Any], wdd_dir: Path | str) -> dict[str, Any] | None:
    """The first mismatched rung (spec -> research artifacts -> design), or None.

    None covers three no-op cases: legacy (wholesale exempt), and any rung
    that has no record yet (nothing approved there to drift from -- ladder
    incompleteness is `intake_complete`'s concern, not this one's). A missing
    file counts as drift, reported as `actual: "missing:<path>"` (the
    `governance_drift`/phase-4 idiom for a deleted ratified artifact).
    """
    wdd_dir = Path(wdd_dir)
    intake = state.get("intake") or {}
    if intake.get("legacy") is True:
        return None
    # Cross-reference: every resolution below threads state.epic (Task 4,
    # spec Sec1) -- a v6 state with an active epic never falls back to a
    # flat read, even for a rung recorded before this scope had one.
    epic = state.get("epic")

    spec = intake.get("spec")
    if spec is None:
        return None
    spec_path = resolve_within_wdd(wdd_dir, "spec.md", label="spec", epic=epic)
    if not spec_path.exists():
        return {"rung": "spec", "recorded": spec["sha256"], "actual": "missing:spec.md"}
    actual = artifact_sha256(spec_path)
    if actual != spec["sha256"]:
        return {"rung": "spec", "recorded": spec["sha256"], "actual": actual}

    research = intake.get("research")
    if research is None:
        return None
    if research.get("done") is True:
        for artifact in research.get("artifacts", []):
            artifact_path = resolve_within_wdd(wdd_dir, artifact["path"], epic=epic)
            if not artifact_path.exists():
                return {
                    "rung": "research",
                    "recorded": artifact["sha256"],
                    "actual": f"missing:{artifact['path']}",
                }
            actual = artifact_sha256(artifact_path)
            if actual != artifact["sha256"]:
                return {"rung": "research", "recorded": artifact["sha256"], "actual": actual}
    # A skipped research rung has no artifact bytes to drift-check.

    design = intake.get("design")
    if design is None:
        return None
    design_path = resolve_within_wdd(wdd_dir, "design.md", label="design", epic=epic)
    if not design_path.exists():
        return {"rung": "design", "recorded": design["sha256"], "actual": "missing:design.md"}
    actual = artifact_sha256(design_path)
    if actual != design["sha256"]:
        return {"rung": "design", "recorded": design["sha256"], "actual": actual}

    return None
