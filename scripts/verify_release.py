#!/usr/bin/env python3
"""Post-build release verification (no live orders, no secrets)."""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

SECRET_PATTERNS = [
    re.compile(r"API_SECRET\s*=\s*['\"][^'\"]{8,}"),
    re.compile(r"SECRET_KEY\s*=\s*['\"][^'\"]{8,}"),
    re.compile(r"PRIVATE_KEY\s*=\s*['\"][^'\"]{8,}"),
    re.compile(r"api_secret['\"]?\s*[:=]\s*['\"][A-Za-z0-9+/=]{16,}"),
]
BAD_NAMES = re.compile(r"(\.env$|credentials\.json|api_secret|secrets\.json)", re.I)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    dist = root / "dist"
    if not dist.exists():
        print("FAIL: dist/ missing")
        return 1
    bins = [
        p for p in dist.rglob("*")
        if p.is_file() and p.name in ("TKO-Core", "TKO", "TKO-Core.exe", "TKO.exe", "TKO-GUI.exe")
    ]
    if not bins:
        print("FAIL: no TKO executable under dist/")
        return 1
    print("Found artifacts:")
    sums = []
    for p in bins:
        digest = sha256_file(p)
        print(f"  {p.relative_to(root)}  sha256={digest}  size={p.stat().st_size}")
        sums.append(f"{digest}  {p.relative_to(root).as_posix()}")
    bad = [
        p for p in dist.rglob("*")
        if p.is_file() and BAD_NAMES.search(p.name)
        and "certifi" not in p.parts and p.name.lower() != "cacert.pem"
    ]
    if bad:
        print("FAIL: suspicious filenames:")
        for p in bad:
            print(" ", p)
        return 1
    for p in dist.rglob("*"):
        if not p.is_file() or p.suffix.lower() in {".exe", ".dll", ".pyd", ".so", ".pyc"}:
            continue
        if p.stat().st_size > 2_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                print(f"FAIL: possible secret pattern in {p}")
                return 1
    sums_path = dist / "SHA256SUMS.txt"
    sums_path.write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(f"Wrote {sums_path}")
    print("STATIC VERIFY PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
