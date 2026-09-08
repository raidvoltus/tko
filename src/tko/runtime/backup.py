"""State folder backup to zip (no credentials)."""

from __future__ import annotations

import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_SKIP_NAMES = {"tko.lock"}


def backup_state(state_dir: Path, backups_dir: Path) -> Path:
    state_dir = state_dir.resolve()
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = backups_dir / f"state_{stamp}.zip"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if state_dir.exists():
            for p in state_dir.rglob("*"):
                if not p.is_file():
                    continue
                if p.name in _SKIP_NAMES:
                    continue
                if "credential" in p.as_posix().lower():
                    continue
                arc = p.relative_to(state_dir).as_posix()
                zf.write(p, arcname=arc)
    logger.info("event=backup_created path=%s", out)
    return out
