"""Frozen-aware install / data paths for TKO-Core and TKO-GUI."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

# Minimal fail-safe config if no template is bundled
_MINIMAL_CONFIG_YAML = b"""# Auto-generated default - edit ProgramData/TKO/config/config.yaml
mode: PAPER
cycle_interval_sec: 30
exchange:
  name: tokocrypto
  base_url: "https://www.tokocrypto.com"
symbols:
  whitelist: []
  blacklist: []
  prefer_quote: ["USDT", "IDR"]
  max_symbols_scan: 40
  fallback: ["BTC_USDT", "ETH_USDT", "BNB_USDT", "SOL_USDT"]
paper:
  wallet_usdt: 0
risk:
  max_order_value_usdt: 100
  max_exposure_usdt: 1000
  min_balance_usdt: 10
"""


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def meipass() -> Optional[Path]:
    mp = getattr(sys, "_MEIPASS", None)
    return Path(mp) if mp else None


def install_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    try:
        return Path(__file__).resolve().parents[2]
    except IndexError:
        return Path(__file__).resolve().parent


def program_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA") or os.environ.get("ALLUSERSPROFILE")
        if base:
            return Path(base) / "TKO"
        return Path.home() / "AppData" / "Local" / "TKO"
    return Path.home() / ".tko"


def config_candidates() -> List[Path]:
    env = os.environ.get("TKO_CONFIG", "").strip()
    out: List[Path] = []
    if env:
        out.append(Path(env))
    out.append(install_dir() / "config" / "config.yaml")
    out.append(program_data_dir() / "config" / "config.yaml")
    mp = meipass()
    if mp is not None:
        out.append(mp / "config" / "config.yaml")
        out.append(mp / "_internal" / "config" / "config.yaml")
    # onedir sibling
    out.append(install_dir() / "_internal" / "config" / "config.yaml")
    return out


def resolve_config_path() -> Path:
    for c in config_candidates():
        if c.is_file():
            return c
    return program_data_dir() / "config" / "config.yaml"


def ensure_default_config(target: Optional[Path] = None) -> Path:
    """Always ensure a readable config.yaml exists (ProgramData preferred)."""
    for c in config_candidates():
        if c.is_file():
            return c

    dest = target or (program_data_dir() / "config" / "config.yaml")
    dest.parent.mkdir(parents=True, exist_ok=True)
    template: Optional[Path] = None
    mp = meipass()
    for cand in (
        (mp / "config" / "config.yaml") if mp else None,
        (mp / "_internal" / "config" / "config.yaml") if mp else None,
        install_dir() / "config" / "config.yaml",
        install_dir() / "_internal" / "config" / "config.yaml",
    ):
        if cand is not None and cand.is_file():
            template = cand
            break
    if not dest.exists():
        if template is not None:
            dest.write_bytes(template.read_bytes())
        else:
            dest.write_bytes(_MINIMAL_CONFIG_YAML)
    return dest


def state_root() -> Path:
    d = program_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "audit").mkdir(parents=True, exist_ok=True)
    return d


def describe_config_search() -> str:
    lines = []
    for c in config_candidates():
        lines.append(f"{c} exists={c.is_file()}")
    return " | ".join(lines)
