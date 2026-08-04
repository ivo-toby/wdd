"""CLI version identity (spec Sec3).

`VERSION` is the single source of truth, living at repo root in a source
checkout and copied next to the installed package by
scripts/install_wave_delivery.py. Either way it ends up one directory above
this module's package directory:

  repo root/VERSION                  repo root/wave_delivery/version.py
  <prefix>/lib/VERSION                <prefix>/lib/wave_delivery/version.py

so a single `parents[1]` lookup from `__file__` covers both layouts -- no
installed/source branching needed.
"""

from __future__ import annotations

from pathlib import Path

_FALLBACK = "0.0.0+unknown"


def wddctl_version() -> str:
    """Return the CLI's semver string, or a marked fallback if unreadable."""
    version_file = Path(__file__).resolve().parents[1] / "VERSION"
    try:
        content = version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return _FALLBACK
    return content or _FALLBACK
