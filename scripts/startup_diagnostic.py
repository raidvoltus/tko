#!/usr/bin/env python3
"""Startup diagnostic for Windows 7 environment."""
from __future__ import annotations

import platform
import sys
import importlib


def check(name, min_version=None):
    try:
        m = importlib.import_module(name)
        ver = getattr(m, "__version__", "?")
        print(f"  [OK] {name} {ver}")
        return True
    except Exception as e:
        print(f"  [FAIL] {name}: {e}")
        return False


def main():
    print("=" * 60)
    print("TOKOCRYPTO BOT — STARTUP DIAGNOSTIC")
    print("=" * 60)
    print(f"Python: {sys.version}")
    print(f"Platform: {platform.platform()}")
    print(f"Machine: {platform.machine()}")
    print(f"Windows: {'Windows' in platform.system()}")
    print()

    if sys.version_info[:2] != (3, 8):
        print("[WARN] Recommended Python is 3.8.x for Windows 7")
    else:
        print("[OK] Python 3.8.x detected")

    print("\nCore dependencies:")
    ok = True
    for pkg in [
        "requests",
        "urllib3",
        "websocket",
        "numpy",
        "pandas",
        "sklearn",
        "cryptography",
        "yaml",
    ]:
        if not check(pkg):
            ok = False

    print("\nOptional ML:")
    check("xgboost")

    print("\nStdlib:")
    for pkg in ["tkinter", "sqlite3", "hashlib", "hmac", "json"]:
        check(pkg)

    print()
    if ok:
        print("RESULT: Core dependencies present. Bot can start.")
        return 0
    print("RESULT: Missing dependencies. Install requirements-win7.txt")
    return 1


if __name__ == "__main__":
    sys.exit(main())
