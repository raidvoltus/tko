#!/usr/bin/env python3
"""Write dist/BUILD_MANIFEST.json (no secrets)."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit(root: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except Exception:
        return "unknown"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    dist = root / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for name in ("TKO-Core.exe", "TKO.exe", "TKO-GUI.exe", "TKO-Core", "TKO"):
        for p in dist.rglob(name):
            if p.is_file():
                artifacts.append({
                    "path": str(p.relative_to(root).as_posix()),
                    "sha256": sha256_file(p),
                    "size": p.stat().st_size,
                })
    try:
        import PyInstaller
        pi_ver = PyInstaller.__version__
    except Exception:
        pi_ver = "unknown"
    from tko import __version__
    manifest = {
        "version": __version__,
        "commit": git_commit(root),
        "python": platform.python_version(),
        "pyinstaller": pi_ver,
        "platform": platform.platform(),
        "system": platform.system(),
        "build_time_utc": datetime.now(timezone.utc).isoformat(),
        "packaging": "onedir",
        "artifacts": artifacts,
        "notes": "Credentials are never bundled. State/logs under LOCALAPPDATA/TKO or portable data/.",
    }
    out = dist / "BUILD_MANIFEST.json"
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
