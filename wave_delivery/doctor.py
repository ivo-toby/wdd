"""Capability diagnostics for optional controller integrations."""

from __future__ import annotations

import platform
import shutil
import sys
from typing import Any


def inspect_capabilities() -> dict[str, Any]:
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
    }
