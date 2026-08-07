"""Capability diagnostics for optional controller integrations."""

from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from .config import config_path, governance_drift, load_config
from .errors import ValidationError
from .setup import epic_orphans
from .version import wddctl_version


# A runner command's argv[0] naming one of these is a malformed registration
# -- placeholders belong in arguments (worktree/prompt/logfile), never in the
# binary position -- reported as unresolvable rather than fed to
# shutil.which (which would just, and misleadingly, say "not found").
_RUNNER_PLACEHOLDERS = {"{worktree}", "{prompt}", "{logfile}"}


def _inspect_governance(
    wdd_dir: Path | str | None, state: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    governance: dict[str, Any] = {
        "configPresent": False,
        "configValid": False,
        "drift": None,
    }
    if wdd_dir is None or not config_path(wdd_dir).exists():
        return governance, None
    governance["configPresent"] = True
    try:
        config = load_config(wdd_dir)
    except ValidationError as error:
        governance["configValid"] = False
        governance["error"] = str(error)
        return governance, None
    governance["configValid"] = True
    if state is not None:
        governance["drift"] = governance_drift(state, wdd_dir)
    return governance, config


def _inspect_runners(config: dict[str, Any] | None) -> dict[str, Any]:
    """Per configured runner, whether its command's argv[0] is on PATH.

    Same shutil.which idiom as the git/gh/acli/codex/claude probes above --
    observational only, never a functional probe (that's `wddctl dispatch
    --probe`, which actually execs the command). Absent entirely when there
    is no config, or its `runners` map is empty: the key only appears when
    there is something to report.
    """
    runners = (config or {}).get("runners")
    if not isinstance(runners, dict) or not runners:
        return {}
    report: dict[str, Any] = {}
    for name, entry in runners.items():
        command = (entry or {}).get("command") if isinstance(entry, dict) else None
        argv0 = command[0] if isinstance(command, list) and command else None
        if not isinstance(argv0, str) or not argv0 or argv0 in _RUNNER_PLACEHOLDERS:
            report[name] = {"argv0": argv0, "available": False, "unresolvable": True}
        else:
            report[name] = {"argv0": argv0, "available": shutil.which(argv0) is not None}
    return report


def inspect_capabilities(
    wdd_dir: Path | str | None = None, state: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Report optional controller integrations, plus whether governance is sound.

    doctor never refuses: a missing/invalid config or reported drift are
    facts in the payload, not a nonzero exit — that gate belongs to the
    governed verbs (see require_fresh_governance), not to diagnostics.
    """
    available = {
        command: shutil.which(command) is not None
        for command in ("git", "gh", "acli", "codex", "claude")
    }
    governance, config = _inspect_governance(wdd_dir, state)
    payload: dict[str, Any] = {
        "version": wddctl_version(),
        "python": {
            "version": platform.python_version(),
            "minimumVersion": "3.10",
            "supported": sys.version_info >= (3, 10),
        },
        "capabilities": {
            "coreController": True,
            "gitIntegration": available["git"],
            "githubAdapter": available["gh"],
            "atlassianAdapter": available["acli"],
            "codexAutomation": available["codex"],
            "claudeAutomation": available["claude"],
        },
        "commands": available,
        "governance": governance,
    }
    runners = _inspect_runners(config)
    if runners:
        payload["runners"] = runners
    # Orphan epic directories (Task 4, spec Sec1): a dir under epics/ that
    # is not state.epic -- a crash between `epic new`'s mkdir and its
    # state.epic adoption, or (Task 6) a crashed archive transaction. A
    # PARKED epic's directory is excluded from this report too (see
    # `setup.epic_orphans`'s own docstring) -- doctor never refuses; both
    # this and the parked report below are reported, never gated on.
    if wdd_dir is not None:
        payload["epicOrphans"] = epic_orphans(wdd_dir, state)
    # Parked epics (epic park/resume spec, "doctor reports parked epics
    # (slug, parked-at, task counts) so they are never invisible"): sorted
    # by slug for deterministic output, one entry per `state.parked` key.
    if state is not None and state.get("parked"):
        payload["parked"] = [
            {
                "slug": slug,
                "at": bundle.get("at"),
                "taskCount": len(bundle.get("tasks") or {}),
            }
            for slug, bundle in sorted((state.get("parked") or {}).items())
        ]
    return payload
