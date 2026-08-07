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
from .engine import apply_mutation
from .errors import IllegalTransition, ValidationError
from .git import ensure_worktrees_gitignore
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
    """
    epics_root = Path(wdd_dir) / "epics"
    if not epics_root.is_dir():
        return []
    active = (state or {}).get("epic")
    return sorted(
        entry.name for entry in epics_root.iterdir() if entry.is_dir() and entry.name != active
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
        actions.append(
            {
                "task": "-",
                "action": "create_epic",
                "command": f"{prefix} epic new --slug SLUG",
                "judgment": (
                    "name the work with the user in ONE short round (a lowercase-"
                    "dash slug, and optionally a display title) per the wdd-intake "
                    "skill, then create the epic. Slugs are immutable -- there is "
                    "no rename verb; retiring one means archiving it."
                ),
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
