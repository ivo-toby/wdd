#!/usr/bin/env python3
"""Run the in-repository wdctl command without installation."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wave_delivery.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
