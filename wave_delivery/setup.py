"""wddctl init: deterministic scaffolding of a fresh .wdd directory.

Setup used to be prose-choreographed — the agent improvised from a template
and no state existed until `plan apply`. Init moves that choreography into
the controller: everything mechanical is created here, and `next` drives the
rest (resolve open questions -> ratify -> plan).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import config_path, constitution_path, default_config, load_config, save_config
from .constitution import probe_repository
from .errors import ValidationError
from .schema import new_setup_state
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


def _open_questions(probed_commands: list[str]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = [
        {
            "path": "merge.surface",
            "question": (
                "Review/merge surface: 'pr' pushes task branches and mirrors review "
                "findings to pull-request comments; 'local' keeps the whole loop "
                "offline in state.json. Which should this repository use?"
            ),
            "options": ["pr", "local"],
        }
    ]
    if not probed_commands:
        questions.append(
            {
                "path": "verification.commands",
                "question": (
                    "No verification command could be detected. What command(s) prove "
                    'a change works here? (JSON list, e.g. ["pytest -q"])'
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
