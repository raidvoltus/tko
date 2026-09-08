"""Path helpers for portable / installed modes."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def state_dir() -> Path:
    if is_frozen() and (app_root() / ".portable").exists():
        d = app_root() / "state"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        d = base / "TKO" / "state"
    else:
        d = Path.home() / ".tko" / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir() -> Path:
    d = state_dir().parent / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d
