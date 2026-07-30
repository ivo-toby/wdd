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

from .config import config_path, constitution_path, default_config, load_config, save_config
from .constitution import probe_repository
from .engine import apply_mutation
from .schema import copied_state, new_setup_state
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
    elif state.get("scope") is None:
        actions.append(
            {
                "task": "-",
                "action": "plan",
                "command": f"{prefix} plan apply --plan plan.json --repo .",
                "judgment": "decompose the work per the wdd-plan skill, write task briefs, then apply",
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
