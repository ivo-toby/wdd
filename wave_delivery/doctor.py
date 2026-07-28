"""Capability diagnostics for optional controller integrations."""

from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from .config import config_path, governance_drift, load_config
from .errors import ValidationError


def _inspect_governance(
    wdd_dir: Path | str | None, state: dict[str, Any] | None
) -> dict[str, Any]:
    governance: dict[str, Any] = {
        "configPresent": False,
        "configValid": False,
        "drift": None,
    }
    if wdd_dir is None or not config_path(wdd_dir).exists():
        return governance
    governance["configPresent"] = True
    try:
        load_config(wdd_dir)
    except ValidationError as error:
        governance["configValid"] = False
        governance["error"] = str(error)
        return governance
    governance["configValid"] = True
    if state is not None:
        governance["drift"] = governance_drift(state, wdd_dir)
    return governance


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
    return {
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
        "governance": _inspect_governance(wdd_dir, state),
    }
