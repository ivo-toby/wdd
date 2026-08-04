#!/usr/bin/env python3
"""Install the dependency-free wddctl package with POSIX and Windows launchers."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SOURCE = ROOT / "wave_delivery"
VERSION_SOURCE = ROOT / "VERSION"

POSIX_LAUNCHER = """#!/bin/sh
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONPATH="$SCRIPT_DIR/../lib${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m wave_delivery "$@"
"""

WINDOWS_LAUNCHER = """@echo off
set "WDDCTL_ROOT=%~dp0.."
set "PYTHONPATH=%WDDCTL_ROOT%\\lib;%PYTHONPATH%"
python -m wave_delivery %*
"""


def install(prefix: Path) -> dict[str, str]:
    prefix = prefix.resolve()
    library = prefix / "lib" / "wave_delivery"
    bin_directory = prefix / "bin"
    if library.exists():
        shutil.rmtree(library)
    library.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PACKAGE_SOURCE, library, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # Sibling of the installed package dir, mirroring the repo-root/wave_delivery
    # layout of a source checkout -- wave_delivery/version.py finds VERSION at
    # `parents[1]` regardless of which layout it's running from.
    shutil.copyfile(VERSION_SOURCE, library.parent / "VERSION")
    bin_directory.mkdir(parents=True, exist_ok=True)
    for legacy_launcher in (bin_directory / "wdctl", bin_directory / "wdctl.cmd"):
        if legacy_launcher.exists():
            legacy_launcher.unlink()
    posix_launcher = bin_directory / "wddctl"
    posix_launcher.write_text(POSIX_LAUNCHER, encoding="utf-8")
    posix_launcher.chmod(0o755)
    windows_launcher = bin_directory / "wddctl.cmd"
    windows_launcher.write_text(WINDOWS_LAUNCHER, encoding="utf-8", newline="\r\n")
    return {
        "prefix": str(prefix),
        "pythonModule": "python -m wave_delivery",
        "posixLauncher": str(posix_launcher),
        "windowsLauncher": str(windows_launcher),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(install(args.prefix), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
