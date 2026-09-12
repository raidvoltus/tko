"""S1-04: both startup paths must resolve to the same main()."""

from __future__ import annotations

import importlib
from pathlib import Path


def test_console_script_target_is_entrypoint_main():
    from tko.runtime.entrypoint import main as entry_main

    mod = importlib.import_module("tko.runtime.entrypoint")
    assert mod.main is entry_main


def test_dunder_main_delegates_to_entrypoint():
    """__main__.py must only re-export entrypoint.main — no parallel lifecycle."""
    import tko.__main__ as dunder
    from tko.runtime.entrypoint import main as entry_main

    assert dunder.main is entry_main


def test_dunder_main_source_has_no_parallel_cli():
    root = Path(__file__).resolve().parents[1]
    text = (root / "src" / "tko" / "__main__.py").read_text(encoding="utf-8")
    assert "from tko.runtime.entrypoint import main" in text
    assert "TradingBot" not in text
    assert "ArgumentParser" not in text
