#!/usr/bin/env python3
"""Scan source/dist for embedded credentials. Exit 1 on hit."""
from __future__ import annotations

import re
import sys
from pathlib import Path

PATS = [
    re.compile(r"(?i)(api[_-]?secret|secret[_-]?key|private[_-]?key)\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*"),
]
SKIP = {".git", ".venv", "venv", "__pycache__", "dist", "build", ".pytest_cache"}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    hits = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP for part in p.parts):
            continue
        if p.suffix.lower() not in {".py", ".md", ".txt", ".json", ".toml", ".yml", ".yaml", ".ps1", ".spec"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat in PATS:
            if pat.search(text):
                if "changeme" in text.lower() or "placeholder" in text.lower() or "too short" in text.lower():
                    continue
                hits.append(str(p.relative_to(root)))
                break
    if hits:
        print("SECRET SCAN FAIL:")
        for h in hits:
            print(" ", h)
        return 1
    print("SECRET SCAN PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
