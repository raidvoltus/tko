#!/usr/bin/env python3
"""Startup diagnostic — Windows 10+ / Linux."""
from __future__ import annotations

import platform
import sys
import importlib


def check(name):
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
    print("TOKOCRYPTO AUTOPILOT — STARTUP DIAGNOSTIC")
    print("=" * 60)
    print(f"Python: {sys.version}")
    print(f"Platform: {platform.platform()}")
    print(f"Machine: {platform.machine()}")
    print()

    if sys.version_info < (3, 10):
        print("[WARN] Python 3.10+ recommended (3.11 preferred)")
    else:
        print("[OK] Python >= 3.10")

    print("\nCore dependencies:")
    ok = True
    for pkg in [
        "requests", "urllib3", "websocket", "numpy", "pandas",
        "sklearn", "cryptography", "yaml",
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
    print("RESULT: Missing dependencies. Run: pip install -r requirements.txt")
    return 1


if __name__ == "__main__":
    sys.exit(main())
