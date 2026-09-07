#!/usr/bin/env python3
"""Tokocrypto Trading Bot - entry point (Windows 7 compatible)."""
from __future__ import annotations

import logging
import os
import sys

# ensure src on path
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    from src.core.app import AppController
    from src.gui.main_window import MainWindow

    ctrl = AppController()
    # load existing config into GUI fields
    cfg = ctrl.config.load()
    win = MainWindow(ctrl)
    win.load_config_into_form(cfg)
    win.run()


if __name__ == "__main__":
    main()
