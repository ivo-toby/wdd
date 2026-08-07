"""wddctl init: deterministic scaffolding of a fresh .wdd directory.

Setup used to be prose-choreographed — the agent improvised from a template
and no state existed until `plan apply`. Init moves that choreography into
the controller: everything mechanical is created here, and `next` drives the
rest (resolve open questions -> ratify -> plan).
"""

from __future__ import annotations

import json
import re
import shlex
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import (
    config_path,
    constitution_path,
    default_config,
    load_config,
    load_overlay,
    save_config,
    save_overlay,
)
from .constitution import probe_repository
from .engine import apply_mutation, utc_now
from .errors import IllegalTransition, ValidationError
from .git import (
    ensure_worktrees_gitignore,
    require_repository,
    run_git,
    worktree_at,
    worktree_for,
)
from .handover import ensure_dispatch_gitignore
from .intake import intake_drift, intake_status
from .schema import EPIC_SLUG_PATTERN, copied_state, new_setup_state
from .store import StateStore, atomic_write_text


CONSTITUTION_TEMPLATE = """\
---
id: WDD-CONSTITUTION
kind: constitution
version: 2.0.0
---

# Project Constitution

This document is prose for humans and agents exercising judgment. Every
machine-consumed setting lives in `config.json` next to this file and is
edited with `wddctl config set`.

## Intent

Describe in a few sentences what this project is and what a successful
change to it looks like. Until this section is written for the project,
reviewers fall back to the task briefs alone.

## What reviewers should weigh most

Reviewers block on correctness, security, and unmet objectives (P1), and on
real problems short of that (P2). Beyond those defaults, name what deserves
extra scrutiny in this repository and why — the areas where a bad merge is
expensive to unwind.

## Workflow norms

Work is decomposed into tasks with explicit dependencies and conflict
domains. Workers implement test-first and stay inside their declared
domains. The controller merges only tasks whose review and verification
evidence is current. The final merge to the target branch is made by a
human, never by the controller.

## Behavioral contract

These are testable obligations, not vibes — each one names a concrete,
checkable rule rather than a persona statement.

- Dissent is a duty, not rudeness. If you believe a human or agent
  decision is wrong, say so once — with the reason and what evidence
  would change your mind — before executing, not after.
- Disagree-then-commit. After the human decides with your objection on
  the table, execute their call. Pushback is one round, not obstruction;
  the human stays the authority.
- Claim/observation discipline. "I did X and verified it by Y" and "I
  believe X" are different sentences; never use the first without the Y.
- Challenge conflicts with recorded doctrine. An instruction that
  contradicts this constitution, the spec, or an approved record gets
  surfaced, not silently obeyed — and not silently ignored either.
- No agreement theater. Don't open by validating a premise you are about
  to refute; in incidents and reviews, your first job is the hole in the
  theory.
- Evidence names who did the work. Never record a review, verification,
  or approval that did not happen; never put a name on evidence for work
  that person or model did not do.
"""


_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)


def _legacy_models(constitution_text: str) -> dict[str, Any] | None:
    for match in _JSON_BLOCK.finditer(constitution_text):
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        models = parsed.get("models") if isinstance(parsed, dict) else None
        if isinstance(models, dict):
            return models
    return None


def _open_questions(probed_commands: list[str]) -> list[dict[str, Any]]:
    """Setup decisions the agent must relay to the user.

    ``question`` is written for the HUMAN: a plain decision, no config paths,
    no JSON syntax — the agent asks it as-is (or better) and translates the
    answer into ``wddctl config set`` itself using ``path``.
    """
    questions: list[dict[str, Any]] = [
        {
            "path": "merge.surface",
            "question": (
                "Should each task ship as a real GitHub pull request, or stay "
                "fully local? Pull requests give you the familiar review surface "
                "(branches pushed, findings mirrored as PR comments); local keeps "
                "the whole loop offline with no pushes — good for solo or "
                "offline work."
            ),
            "options": ["pr", "local"],
        },
        {
            "path": "models",
            "question": (
                "Which models should do the work? Three roles matter: everyday "
                "implementation, a stronger model for high-risk tasks, and review "
                "(usually your strongest — it guards the merges). Name models "
                "your agent harness understands, or say the harness defaults are "
                "fine."
            ),
        },
    ]
    if not probed_commands:
        questions.append(
            {
                "path": "verification.commands",
                "question": (
                    "I couldn't detect a test or verification command in this "
                    "repository. What command should prove a change works — for "
                    "example 'npm test' or 'pytest -q'? If nothing runnable "
                    "exists yet, say so and verification will be recorded as "
                    "unavailable with your justification."
                ),
            }
        )
    return questions


def init_repository(wdd_dir: Path | str, repo: Path | str) -> dict[str, Any]:
    wdd_dir = Path(wdd_dir)
    repo = Path(repo)
    store = StateStore(wdd_dir / "state.json")
    if store.exists():
        return {
            "alreadyInitialized": True,
            "created": [],
            "openQuestions": [],
            "hint": "state exists; run 'wddctl next' for what to do",
        }

    proposal = probe_repository(repo)
    probed_commands = proposal["evidence"]["verificationCommands"]

    # Load existing config if present, otherwise create fresh one
    config_file = config_path(wdd_dir)
    if config_file.exists():
        config = load_config(wdd_dir)
    else:
        config = default_config()
        config["branching"]["targetBranch"] = proposal["decisions"]["targetBranch"]
        config["verification"]["commands"] = probed_commands
        config["openQuestions"] = _open_questions(probed_commands)

    created: list[str] = []
    if not config_file.exists():
        save_config(wdd_dir, config)
        created.append(str(config_file))

    if not constitution_path(wdd_dir).exists():
        atomic_write_text(constitution_path(wdd_dir), CONSTITUTION_TEMPLATE)
        created.append(str(constitution_path(wdd_dir)))

    for directory in ("tasks", "shared-context"):
        (wdd_dir / directory).mkdir(parents=True, exist_ok=True)
        created.append(str(wdd_dir / directory))

    # .wdd/dispatch/ is transient scratch (Global Constraints, phase-6b Task
    # 2): gitignored from the start so a fresh init never risks a worker's
    # dispatch log or attempt snapshot getting committed.
    if ensure_dispatch_gitignore(wdd_dir):
        created.append(str(wdd_dir / ".gitignore"))

    # An in-repo worktrees.root (default ".worktrees") must never be
    # committed either; scaffolded at the REPO ROOT (not .wdd/), and a no-op
    # for an absolute root outside the repo (nothing there to gitignore).
    worktrees_root = (config.get("worktrees") or {}).get("root")
    if ensure_worktrees_gitignore(repo, worktrees_root):
        created.append(str(repo / ".gitignore"))

    # Wrap state write in lock with re-check for race condition safety
    with store.locked():
        if store.exists():
            return {
                "alreadyInitialized": True,
                "created": [],
                "openQuestions": [],
                "hint": "state exists; run 'wddctl next' for what to do",
            }
        store.write(new_setup_state())
    created.append(str(store.path))

    return {
        "alreadyInitialized": False,
        "created": created,
        "openQuestions": config["openQuestions"],
        "hint": "run 'wddctl next' and follow it",
    }


# ---------------------------------------------------------------------------
# Epic lifecycle: `wddctl epic new` (epic-scoped-state plan Task 4, spec Sec1
# "the slug is born at the top of the ladder"). `epic new` is the SOLE
# creator of `epics/<slug>/` -- `config.save_overlay`'s own mkdir(parents=True)
# side effect must never be mistaken for a second creation path. Every OTHER
# writer of an epic overlay (`config set --epic`) only ever runs once
# state.epic already names an active epic THIS function created, so it can
# never independently originate a new epic directory.
# ---------------------------------------------------------------------------

_EPIC_RECORD_NAME = "record.json"
_EPIC_OVERLAY_NAME = "config.json"


def _epic_dir_shape(wdd_dir: Path, slug: str) -> str:
    """Classify an `epics/<slug>/` directory for `create_epic`'s uniqueness
    and crash-orphan-adoption logic (spec Sec1):

    - "absent": the directory does not exist -- ordinary fresh creation.
    - "has_record": contains the reserved `record.json` -- an in-flight or
      completed archive transaction, or a hand-placed collision; refused
      unconditionally regardless of anything else in the directory.
    - "crash_orphan": contains ONLY an empty overlay `config.json` (`{}`) --
      a prior `epic new` that mkdir'd + wrote the overlay but crashed before
      adopting `state.epic`. The identical command re-run adopts it (no
      error), per the Task 4 interface's crash-shape doctrine.
    - "occupied": anything else -- a real, non-adoptable collision.
    """
    epic_dir = wdd_dir / "epics" / slug
    if not epic_dir.is_dir():
        return "absent"
    entries = list(epic_dir.iterdir())
    if any(entry.name == _EPIC_RECORD_NAME for entry in entries):
        return "has_record"
    if len(entries) == 1 and entries[0].name == _EPIC_OVERLAY_NAME and entries[0].is_file():
        try:
            overlay = load_overlay(wdd_dir, slug)
        except ValidationError:
            return "occupied"
        if overlay == {}:
            return "crash_orphan"
    return "occupied"


def epic_orphans(wdd_dir: Path | str, state: dict[str, Any] | None) -> list[str]:
    """Epic directories under `epics/` that are not `state.epic` (`doctor`'s
    orphan report, Task 4 test contract). A directory left behind by a
    crashed `epic new` (never adopted) or a crashed archive transaction
    (Task 6) both surface here -- `doctor` never refuses, it only reports.

    A PARKED epic's directory is excluded too (epic park/resume, spec
    "doctor... epic_orphans() excludes parked slugs"): its state lives in
    `state.parked[<slug>]`, not on disk, but `epics/<slug>/` staying in
    place is exactly what park promises ("no epic-directory moves") -- an
    accounted-for suspension, not a crash candidate. Without this exclusion
    every parked epic would misreport as an orphan the moment it parked.
    """
    epics_root = Path(wdd_dir) / "epics"
    if not epics_root.is_dir():
        return []
    active = (state or {}).get("epic")
    parked = set((state or {}).get("parked") or {})
    return sorted(
        entry.name
        for entry in epics_root.iterdir()
        if entry.is_dir() and entry.name != active and entry.name not in parked
    )


def create_epic(
    store: StateStore,
    wdd_dir: Path | str,
    *,
    slug: str,
    title: str | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """`wddctl epic new --slug SLUG [--title T]` (spec Sec1): the first
    action of every epic. Refuses when an epic is already active (one at a
    time), when the slug is malformed, when it collides with `archive/`
    (slugs are immutable -- there is no rename verb; retiring one means
    archiving it) or with a non-orphan `epics/<slug>/`, and unconditionally
    when that directory holds the reserved `record.json`. A directory
    holding ONLY an empty, unadopted overlay is the crash-orphan shape: the
    identical command adopts it instead of refusing.

    mkdir + empty overlay + `state.epic` all land in ONE `apply_mutation`
    (event `epic.created`): the directory write happens INSIDE the mutator,
    under the state lock, so a crash between "directory exists" and
    "state.epic recorded" can only ever produce the crash-orphan shape
    above -- never a directory whose existence and state.epic disagree in
    any other way. `title`, when given, is recorded only in the event's
    `data` (state.epic itself is schema v6's plain slug-or-null field, spec
    Sec1 -- there is no queryable title field to persist it into).
    """
    if not isinstance(slug, str) or not EPIC_SLUG_PATTERN.match(slug):
        raise ValidationError(
            f"epic slug must match [a-z0-9][a-z0-9-]{{1,63}}: {slug!r}"
        )
    wdd_dir = Path(wdd_dir)
    archive_dir = wdd_dir / "archive" / slug

    def _check_available(current: dict[str, Any]) -> None:
        if current.get("epic") is not None:
            raise IllegalTransition(
                f"an epic is already active ({current['epic']!r}); archive it with "
                "'wddctl scope archive --repo .' before starting a new one"
            )
        if archive_dir.exists():
            raise ValidationError(
                f"epic slug {slug!r} is already used by an archived epic "
                f"({archive_dir}); slugs are immutable and unique across epics/ "
                "and archive/ -- choose a different slug"
            )
        if slug in (current.get("parked") or {}):
            # Belt-and-braces (epic park/resume spec, "Interactions, pinned":
            # "Add state.parked keys to the check anyway... for a
            # hand-deleted directory"): `epics/<slug>/` staying in place is
            # what park promises, so `_epic_dir_shape` below would normally
            # already refuse via "occupied" -- but an operator who manually
            # deleted that directory out from under a parked epic must not
            # be able to silently mint a FRESH epic under the same slug,
            # only reachable in this repo's parked map. Named as its own
            # branch (not folded into the shape checks) since state, not
            # the filesystem, is the source of truth for this one.
            raise ValidationError(
                f"epic slug {slug!r} is a parked epic; slugs are immutable and unique "
                "across epics/, archive/, and state.parked -- resume it with 'wddctl "
                f"epic resume --slug {slug}' or choose a different slug"
            )
        shape = _epic_dir_shape(wdd_dir, slug)
        if shape == "has_record":
            raise ValidationError(
                f"epics/{slug}/ contains a reserved record.json; refusing to adopt "
                "or overwrite it -- this looks like an in-flight or completed "
                "archive transaction"
            )
        if shape == "occupied":
            raise ValidationError(
                f"epics/{slug}/ already exists with content that is not an empty, "
                "unadopted overlay; slugs are immutable and unique across epics/ "
                "and archive/ -- choose a different slug"
            )

    # Fail fast, before the lock -- mirrors intake.py's two-stage guard.
    if store.exists():
        _check_available(store.read())

    def mutator(current: dict[str, Any]) -> dict[str, Any]:
        _check_available(current)
        updated = copied_state(current)
        # The directory write happens HERE, inside the locked mutator, not
        # before it -- "directory creation happens inside the locked
        # mutation path" (Task 4 interface). save_overlay's own
        # mkdir(parents=True, exist_ok=True) is what actually creates
        # epics/<slug>/; this is the ONLY call site in the whole codebase
        # that may call it with no active epic already naming that slug.
        save_overlay(wdd_dir, slug, {})
        updated["epic"] = slug
        return updated

    data: dict[str, Any] = {"slug": slug}
    if title:
        data["title"] = title
    return apply_mutation(
        store,
        event_type="epic.created",
        task_id=None,
        data=data,
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )


# ---------------------------------------------------------------------------
# `wddctl epic park` / `wddctl epic resume --slug S` (epic park/resume spec,
# 2026-08-07): suspension, not concurrency -- see the spec's "Design
# principle". Park moves the scope-carrying sections into
# `state.parked[<slug>]` and resets them to a fresh setup shape, exactly the
# same swap `finalize._reset_to_post_ratification` performs for archive
# (governance/events/telemetry/probes untouched, `appliedIdempotencyKeys`
# preserved) -- except park keeps the sections instead of writing them to an
# archive record, and additionally carries `leases`/`monitoring` (archive
# resets monitoring; park's own reason is identical: it is scope-specific).
# Resume moves them back verbatim. No epic-directory move happens for
# either verb (spec: "epics/<slug>/ stays in place").
# ---------------------------------------------------------------------------

_PARKED_BUNDLE_SECTIONS = ("scope", "tasks", "intake", "reconcile", "monitoring")


def park_epic(
    store: StateStore,
    *,
    repo: Path | str,
    worktrees_root: str | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """`wddctl epic park` (spec "epic park"): suspend the active epic.

    Refuses when there is no active epic, when an archive transaction is
    pending, or when one is durably blocked (finish or resolve it first --
    park is not a way to route around an in-flight archive). Legal in ANY
    phase from post-configure through delivered, including a scope that has
    not reached `plan apply` yet (`scope` is `None`, `tasks` is `{}`): the
    only precondition is an active epic.

    Worktrees are released BEFORE the state swap, all-or-nothing: every
    active-lease task's worktree is checked for uncommitted changes FIRST
    (before any worktree is actually removed or any state mutated) -- one
    dirty worktree refuses the WHOLE park, naming the path, with nothing
    released and nothing swapped. Branches are left alone; resume's own
    `start` reattach path re-creates worktrees from them.
    """
    repo = require_repository(repo)

    def mutator(state: dict[str, Any]) -> dict[str, Any]:
        slug = state.get("epic")
        if slug is None:
            raise IllegalTransition("epic park requires an active epic (state.epic is unset)")
        if state.get("archivePending") is not None:
            raise IllegalTransition(
                "epic park: an archive transaction is pending for this epic; finish it "
                "with 'wddctl scope archive --repo .' before parking"
            )
        archive_blocked = state.get("archiveBlocked")
        if archive_blocked is not None:
            raise IllegalTransition(
                f"epic park: archive is blocked on {archive_blocked['collidingPath']!r}; "
                "resolve the collision and re-run 'wddctl scope archive --repo .' before parking"
            )

        # --- Step 1: plan every worktree release, refusing on ANY dirty
        # worktree BEFORE removing anything (all-or-nothing) -------------
        leases = state.get("leases") or {}
        scope_id = (state.get("scope") or {}).get("id")
        to_release: list[tuple[str, Path]] = []
        for task_id in sorted(leases):
            lease = leases[task_id]
            if not isinstance(lease, dict) or lease.get("status") != "active":
                continue
            task = state.get("tasks", {}).get(task_id, {})
            path = worktree_for(
                repo, scope_id, task_id, task.get("worktree"), worktrees_root=worktrees_root
            )
            entry = worktree_at(repo, path)
            if entry is None:
                if path.exists():
                    raise ValidationError(f"worktree is not registered with Git: {path}")
                # Already gone (a crash between a prior removal and its state
                # write) -- nothing to check or remove for this task.
                continue
            expected_branch = f"refs/heads/{lease.get('branch')}"
            if entry.get("branch") != expected_branch:
                raise ValidationError(
                    f"refusing to park: worktree {path} is on {entry.get('branch')}, "
                    f"expected {expected_branch}"
                )
            if run_git(path, "status", "--porcelain").stdout.strip():
                raise IllegalTransition(
                    f"epic park refuses: task {task_id} has uncommitted changes in "
                    f"{path}; commit or stash them before parking (branches are kept -- "
                    "only worktrees are released)"
                )
            to_release.append((task_id, path))

        # --- Step 2: only now, remove the worktrees checked above --------
        for _task_id, path in to_release:
            run_git(repo, "worktree", "remove", str(path))
        if to_release:
            run_git(repo, "worktree", "prune")

        # --- Step 3: build the parked bundle and swap in a fresh setup ---
        updated = copied_state(state)
        for task_id, _path in to_release:
            released = deepcopy(updated["leases"][task_id])
            released["status"] = "released"
            released["releasedAt"] = utc_now()
            released["cleanup"] = "cleaned_up"
            updated["leases"][task_id] = released
            updated["tasks"][task_id]["worktree"] = None

        bundle: dict[str, Any] = {"at": utc_now()}
        for section in _PARKED_BUNDLE_SECTIONS:
            bundle[section] = updated.get(section)
        if updated.get("finalize") is not None:
            bundle["finalize"] = updated["finalize"]
        if updated.get("leases") is not None:
            bundle["leases"] = updated["leases"]

        parked = dict(updated.get("parked") or {})
        parked[slug] = bundle

        fresh = new_setup_state()
        fresh["constitution"] = updated["constitution"]
        fresh["events"] = updated["events"]
        fresh["appliedIdempotencyKeys"] = updated["appliedIdempotencyKeys"]
        fresh["telemetry"] = updated["telemetry"]
        fresh["revision"] = updated["revision"]
        # Probes are machine observations keyed by runner-command digest, not
        # scope state (archive's identical precedent, finalize.py's
        # `_reset_to_post_ratification`): they survive the reset.
        if updated.get("probes"):
            fresh["probes"] = updated["probes"]
        fresh["parked"] = parked
        return fresh

    slug_hint = store.read().get("epic") if store.exists() else None
    return apply_mutation(
        store,
        event_type="epic.parked",
        task_id=None,
        data={"slug": slug_hint} if slug_hint else {},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )


def resume_epic(
    store: StateStore,
    *,
    slug: str,
    wdd_dir: Path | str,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """`wddctl epic resume --slug S` (spec "epic resume"): reactivate a
    parked epic.

    Refuses when an epic is already active (park or archive it first), when
    `slug` is not in `state.parked`, or when `epics/<slug>/` is missing on
    disk (a hard error naming the path -- never guessed or silently
    recreated).

    Resume and the chokepoint, pinned (spec "Resume and the chokepoint,
    pinned"): this verb is governed like any mutating verb, but its OWN
    admission question is answered entirely by `require_fresh_governance`
    against the CURRENT (pre-swap) state -- constitution/config still
    ratified is right and sufficient. The epic-level gates
    (`require_fresh_epic_config`/`require_fresh_intake`) run at the same CLI
    chokepoint as every governed verb, but evaluate the pre-swap state,
    where `state.epic`/`intake.configure`/`scope` are all null/absent --
    structurally no-ops. This is deliberate, not accidental: staleness is
    the EXISTING gates' job, re-evaluated on the NEXT governed verb against
    the just-restored POST-resume state, which is the only state where
    "has this epic's config/intake/plan drifted" is a meaningful question.
    No re-validation of the restored sections happens here beyond schema
    validation of the swapped-in state (`StateStore.write`'s own check).
    """
    wdd_dir = Path(wdd_dir)

    def mutator(state: dict[str, Any]) -> dict[str, Any]:
        if state.get("epic") is not None:
            raise IllegalTransition(
                f"epic resume requires no active epic (currently {state['epic']!r}); "
                "park or archive it first"
            )
        parked = state.get("parked") or {}
        if slug not in parked:
            raise ValidationError(
                f"no parked epic named {slug!r}; parked slugs: {sorted(parked)}"
            )
        epic_dir = wdd_dir / "epics" / slug
        if not epic_dir.is_dir():
            raise ValidationError(
                f"epics/{slug}/ does not exist on disk; cannot resume a parked epic whose "
                "directory is missing -- restore it (from version control or backup) before "
                "retrying"
            )

        bundle = parked[slug]
        updated = copied_state(state)
        updated["epic"] = slug
        for section in _PARKED_BUNDLE_SECTIONS:
            updated[section] = bundle.get(section)
        if "finalize" in bundle:
            updated["finalize"] = bundle["finalize"]
        else:
            updated.pop("finalize", None)
        if "leases" in bundle:
            updated["leases"] = bundle["leases"]
        else:
            updated.pop("leases", None)
        remaining = dict(parked)
        del remaining[slug]
        updated["parked"] = remaining
        return updated

    return apply_mutation(
        store,
        event_type="epic.resumed",
        task_id=None,
        data={"slug": slug},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )


def _intake_ladder_action(
    state: dict[str, Any], wdd_dir: Path, prefix: str
) -> dict[str, Any] | None:
    """The one intake rung to walk next, or None once the ladder is done.

    Front-half spec Sec1: between ratify and plan apply, `next` walks
    ``agree_spec -> research -> agree_design`` one rung at a time. Drift
    takes priority over "what's unset": a rung that was already recorded but
    whose artifact bytes no longer match is re-emitted with ``stale: true``
    (spec's "before apply, next re-hashes" clause) even though every rung
    technically has *a* record -- an approval of edited-since bytes approves
    nothing. Legacy scopes are wholesale exempt (schema.py's migration-only
    doctrine), so this returns None immediately for them.
    """
    intake = state.get("intake") or {}
    if intake.get("legacy") is True:
        return None
    drift = intake_drift(state, wdd_dir)
    rung = drift["rung"] if drift is not None else intake_status(state)["nextRung"]
    if rung is None:
        return None
    action: dict[str, Any]
    if rung == "spec":
        action = {
            "task": "-",
            "action": "agree_spec",
            "recordWith": f"{prefix} intake spec --approved-by NAME",
            "judgment": (
                "agree spec.md with the user (goal, in/out of scope, numbered "
                "acceptance criteria) per the wdd-intake skill's spec stage, then record it"
            ),
        }
    elif rung == "research":
        action = {
            "task": "-",
            "action": "research",
            "recordWith": (
                f"{prefix} intake research --done --by NAME --artifacts PATH... "
                "(or --skip --by NAME --reason '...')"
            ),
            "judgment": (
                "read the named reference implementation and build the contract "
                "inventory per the wdd-intake skill's research stage, or record an "
                "explicit, attributed skip when no external contract applies"
            ),
        }
    else:  # rung == "design"
        action = {
            "task": "-",
            "action": "agree_design",
            "recordWith": (
                f"{prefix} intake design --approved-by NAME --deliverable-command '...'"
            ),
            "judgment": (
                "agree design.md (components, interfaces, integration surfaces, "
                "epic deliverable) with the user per the wdd-intake skill's design stage, "
                "then record it with the command that proves the epic deliverable"
            ),
        }
    if drift is not None:
        action["stale"] = True
    return action


def setup_next_actions(
    state: dict[str, Any], wdd_dir: Path | str, *, state_path: str | None = None
) -> dict[str, Any]:
    """The setup-phase counterpart of engine.next_actions.

    Same output shape, one action at a time: setup is sequential, and a
    single unambiguous action is what keeps an agent from improvising.
    """
    wdd_dir = Path(wdd_dir)
    if not config_path(wdd_dir).exists():
        return {
            "scope": None,
            "revision": state["revision"],
            "phase": "setup",
            "actions": [
                {
                    "task": "-",
                    "action": "repair_config",
                    "judgment": (
                        "state.json exists but config.json is missing. Restore "
                        ".wdd/config.json from version control if it was committed; "
                        "otherwise delete .wdd/state.json and re-run 'wddctl init' "
                        "to regenerate both."
                    ),
                }
            ],
            "blockers": [],
        }
    prefix = "wddctl" + (f" --state {shlex.quote(state_path)}" if state_path else "")
    actions: list[dict[str, Any]] = []
    config = load_config(wdd_dir)
    if config["openQuestions"]:
        actions.append(
            {
                "task": "-",
                "action": "resolve_config",
                "questions": config["openQuestions"],
                "command": f"{prefix} config set <path> <value>",
                "judgment": "relay every listed question to the user in ONE round, in plain language (never show config paths or JSON syntax), then translate the answers into config set yourself",
            }
        )
    elif state["constitution"]["status"] != "ratified":
        actions.append(
            {
                "task": "-",
                "action": "ratify",
                "command": f"{prefix} constitution ratify --by NAME",
                "judgment": "show the user config.json and constitution.md; ratify only after explicit sign-off",
            }
        )
    elif (
        state.get("scope") is None
        and state.get("epic") is None
        and (state.get("intake") or {}).get("legacy") is not True
        and (state.get("intake") or {}).get("spec") is None
    ):
        # The slug is born at the top of the ladder (Task 4, spec Sec1):
        # `create_epic` is emitted before any intake rung, right after
        # ratify. The ladder is
        # create_epic -> configure_epic -> agree_spec -> research ->
        # agree_design -> plan; `configure_epic` (spec Sec2) is wired in the
        # branch just below this one. The extra `intake.spec is None` guard
        # keeps this rung 0 of the SAME first-unset-thing chain
        # `_intake_ladder_action` walks below: once any ladder progress
        # already exists (a scope that recorded spec without ever creating
        # an epic -- unreachable through this ladder going forward, but a
        # legal pre-Task-4 shape), there is nothing to gain by retroactively
        # demanding epic creation; the ladder continues from wherever the
        # recorded intake data says it is.
        judgment = (
            "name the work with the user in ONE short round (a lowercase-"
            "dash slug, and optionally a display title) per the wdd-intake "
            "skill, then create the epic. Slugs are immutable -- there is "
            "no rename verb; retiring one means archiving it."
        )
        parked = state.get("parked") or {}
        if parked:
            # Named, not acted on (epic park/resume spec, "status and
            # setup-phase next name parked epics in judgment text without
            # emitting actions for them"): a new epic here might actually be
            # a pivot BACK to suspended work, and the operator should be
            # told that option exists before creating a genuinely new slug.
            judgment += (
                f" Parked epic(s) exist ({', '.join(sorted(parked))}) -- if the new work "
                "is actually one of these resuming, use 'wddctl epic resume --slug SLUG' "
                "instead of creating a new epic."
            )
        actions.append(
            {
                "task": "-",
                "action": "create_epic",
                "command": f"{prefix} epic new --slug SLUG",
                "judgment": judgment,
            }
        )
    elif (
        state.get("scope") is None
        and state.get("epic") is not None
        and (state.get("intake") or {}).get("legacy") is not True
        and (state.get("intake") or {}).get("configure") is None
    ):
        # configure_epic (Task 5, spec Sec2): the middle step of the ladder,
        # between create_epic and agree_spec. `record_spec` itself also
        # refuses without a recorded `configure` (belt and braces -- this is
        # only the surfaced NEXT-ACTION half of that gate).
        actions.append(
            {
                "task": "-",
                "action": "configure_epic",
                "recordWith": (
                    f"{prefix} intake configure --approved-by NAME "
                    "(or --use-defaults --by NAME)"
                ),
                "judgment": (
                    "walk the user through the epic-overridable keys in ONE compact "
                    "round, in their own terms -- which merge surface, which models, "
                    "what proves this epic works -- per wdd-intake, translating their "
                    "answers into 'wddctl config set --epic PATH VALUE' calls (or none "
                    "at all if they want every default), then record the decision "
                    "with recordWith. Silence is not an option: an explicit "
                    "--use-defaults is required to inherit everything."
                ),
            }
        )
    elif state.get("scope") is None:
        ladder_action = _intake_ladder_action(state, wdd_dir, prefix)
        if ladder_action is not None:
            actions.append(ladder_action)
        else:
            actions.append(
                {
                    "task": "-",
                    "action": "plan",
                    # --approved-by is not optional in practice: the first
                    # apply onto a null scope is always a nonempty diff (every
                    # task is newly "added"), and apply_plan refuses a
                    # nonempty diff on a non-legacy scope without it. Naming
                    # it here (NAME is a placeholder for the approving
                    # human, same convention as ratify's --by NAME) keeps
                    # this command runnable as emitted instead of failing on
                    # first use.
                    "command": f"{prefix} plan apply --plan plan.json --repo . --approved-by NAME",
                    "judgment": (
                        "decompose the work per the wdd-plan skill, write task briefs, show the "
                        "user the diff for explicit approval, then apply with the approving "
                        "human's name"
                    ),
                }
            )
    return {
        "scope": (state.get("scope") or {}).get("id") if state.get("scope") else None,
        "revision": state["revision"],
        "phase": "setup",
        "actions": actions,
        "blockers": [],
    }


def migrate_governance(wdd_dir: Path | str) -> dict[str, Any]:
    """One-time conversion of a pre-split .wdd to config.json + prose constitution.

    Ratification is deliberately invalidated: the fingerprint's meaning
    changed (it now signs config.json too), so the user must see and approve
    the new split once.
    """
    wdd_dir = Path(wdd_dir)
    if config_path(wdd_dir).exists():
        return {"migrated": False, "reason": "config.json already exists"}
    constitution_file = constitution_path(wdd_dir)
    old_text = (
        constitution_file.read_text(encoding="utf-8") if constitution_file.exists() else ""
    )

    config = default_config()
    legacy = _legacy_models(old_text)
    if legacy:
        for key in ("planning", "review"):
            value = legacy.get(key)
            if isinstance(value, str) and value:
                config["models"][key] = value
        implementation = legacy.get("implementation")
        if isinstance(implementation, str) and implementation:
            config["models"]["implementation"]["default"] = implementation
    config["openQuestions"] = _open_questions(config["verification"]["commands"])
    save_config(wdd_dir, config)
    # A pre-split .wdd predates the dispatch/ scratch dir too; ensure the
    # gitignore entry the same way a fresh init would.
    ensure_dispatch_gitignore(wdd_dir)
    # Same for worktrees.root: `.wdd` lives directly at the repo root by
    # convention throughout this codebase (never relocated independently of
    # it), so wdd_dir.parent is the repo root here too.
    ensure_worktrees_gitignore(wdd_dir.parent, config["worktrees"]["root"])

    if old_text:
        atomic_write_text(wdd_dir / "constitution.md.pre-config", old_text)
    atomic_write_text(constitution_file, CONSTITUTION_TEMPLATE)

    store = StateStore(wdd_dir / "state.json")
    invalidated = False
    if store.exists():
        if store.read()["constitution"]["status"] == "ratified":

            def _invalidate(state: dict[str, Any]) -> dict[str, Any]:
                state = copied_state(state)
                if state["constitution"]["status"] != "ratified":
                    return state
                state["constitution"] = {"status": "draft", "ratification": None}
                return state

            apply_mutation(
                store,
                event_type="governance.migrated",
                task_id=None,
                data={},
                idempotency_key=None,
                expected_revision=None,
                mutator=_invalidate,
            )
            invalidated = True
    return {
        "migrated": True,
        "ratificationInvalidated": invalidated,
        "modelsExtracted": bool(legacy),
        "backup": str(wdd_dir / "constitution.md.pre-config") if old_text else None,
    }
