"""Path helpers for portable / installed / frozen modes.

Writable data NEVER under Program Files or PyInstaller _MEIPASS.
Application directory (EXE location) ≠ current working directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def meipass_dir() -> Path | None:
    """PyInstaller extraction dir (read-only bundle resources)."""
    p = getattr(sys, "_MEIPASS", None)
    return Path(p) if p else None


def app_root() -> Path:
    """Directory containing the executable (frozen) or repo root (dev)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # src/tko/runtime/paths.py → parents[3] = repo root
    return Path(__file__).resolve().parents[3]


def resource_dir() -> Path:
    """Read-only bundled resources (MEIPASS when frozen, else app_root)."""
    m = meipass_dir()
    if m is not None:
        return m
    return app_root()


def data_root() -> Path:
    """Writable application data root (config/state/logs/models/backups)."""
    if is_frozen() and (app_root() / ".portable").exists():
        d = app_root() / "data"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        d = base / "TKO"
    else:
        d = Path.home() / ".tko"
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_dir() -> Path:
    d = data_root() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir() -> Path:
    d = data_root() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_dir() -> Path:
    d = data_root() / "config"
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_dir() -> Path:
    d = data_root() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def backups_dir() -> Path:
    d = data_root() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d
