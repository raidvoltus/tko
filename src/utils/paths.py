"""Frozen-aware install / data paths for TKO-Core and TKO-GUI.

When PyInstaller freezes entry.py, ``Path(__file__).parents[2]`` is NOT the
install directory (often resolves under Program Files incorrectly). Always
prefer ``sys.executable`` parent for onedir builds.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def meipass() -> Optional[Path]:
    mp = getattr(sys, "_MEIPASS", None)
    return Path(mp) if mp else None


def install_dir() -> Path:
    """Directory containing the running EXE (onedir) or project root (dev)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # src/utils/paths.py -> parents[2] = repo root
    return Path(__file__).resolve().parents[2]


def resource_dir() -> Path:
    """Bundled read-only resources (config template inside onedir/_internal)."""
    mp = meipass()
    if mp is not None:
        return mp
    return install_dir()


def program_data_dir() -> Path:
    """Writable state/config root (Windows ProgramData, else ~/.tko)."""
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA") or os.environ.get("ALLUSERSPROFILE")
        if base:
            return Path(base) / "TKO"
        return Path.home() / "AppData" / "Local" / "TKO"
    return Path.home() / ".tko"


def resolve_config_path() -> Path:
    """
    Search order (first existing wins for load):
      1. $TKO_CONFIG env
      2. <install_dir>/config/config.yaml
      3. <ProgramData>/TKO/config/config.yaml
      4. <_MEIPASS>/config/config.yaml (bundled template)

    If none exist, preferred create path is ProgramData then install_dir.
    """
    env = os.environ.get("TKO_CONFIG", "").strip()
    if env:
        return Path(env)

    candidates = [
        install_dir() / "config" / "config.yaml",
        program_data_dir() / "config" / "config.yaml",
    ]
    mp = meipass()
    if mp is not None:
        candidates.append(mp / "config" / "config.yaml")
    # dev fallback
    candidates.append(install_dir() / "config" / "config.yaml")

    for c in candidates:
        if c.is_file():
            return c

    # Prefer writable ProgramData for first-run materialization
    return program_data_dir() / "config" / "config.yaml"


def ensure_default_config(target: Optional[Path] = None) -> Path:
    """
    If no user config exists, copy bundled template to ProgramData (or target).
    Returns path to config that should be used.
    """
    existing = None
    env = os.environ.get("TKO_CONFIG", "").strip()
    if env and Path(env).is_file():
        return Path(env)
    for c in (
        install_dir() / "config" / "config.yaml",
        program_data_dir() / "config" / "config.yaml",
    ):
        if c.is_file():
            existing = c
            break
    if existing is not None:
        return existing

    dest = target or (program_data_dir() / "config" / "config.yaml")
    dest.parent.mkdir(parents=True, exist_ok=True)
    template = None
    mp = meipass()
    for cand in (
        (mp / "config" / "config.yaml") if mp else None,
        install_dir() / "config" / "config.yaml",
        install_dir() / "_internal" / "config" / "config.yaml",
    ):
        if cand is not None and cand.is_file():
            template = cand
            break
    if template is not None and not dest.exists():
        dest.write_bytes(template.read_bytes())
    return dest


def state_root() -> Path:
    d = program_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "audit").mkdir(parents=True, exist_ok=True)
    return d
