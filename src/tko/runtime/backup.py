"""State backup, rotation, corruption detection, and fail-closed recovery helpers.

Policy (Stage 9)
----------------
* Exchange is SSOT for balances/orders/fills that can be reconciled.
* Local backup is NOT authority for inventing fills, balances, or PnL.
* Corrupt primary state → empty/safe defaults + HALTED/RECONCILING on next startup
  (startup barrier + Stage 6 recon required before READY).
* Credentials are never backed up.
"""

from __future__ import annotations

import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SKIP_NAMES = frozenset({"tko.lock", "KILL"})
_SKIP_SUBSTRINGS = ("credential", "secret", "keyring", ".pem", ".key")
DEFAULT_KEEP = 10


def backup_state(state_dir: Path, backups_dir: Path, *, keep: int = DEFAULT_KEEP) -> Path:
    """Zip state_dir into backups_dir (no credentials). Rotates old backups."""
    state_dir = state_dir.resolve()
    backups_dir = Path(backups_dir)
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
                posix = p.as_posix().lower()
                if any(s in posix for s in _SKIP_SUBSTRINGS):
                    continue
                arc = p.relative_to(state_dir).as_posix()
                zf.write(p, arcname=arc)
    logger.info("event=backup_created path=%s", out)
    rotate_backups(backups_dir, keep=keep)
    return out


def rotate_backups(backups_dir: Path, *, keep: int = DEFAULT_KEEP) -> int:
    """Delete oldest state_*.zip beyond *keep*. Returns number removed."""
    keep = max(1, int(keep))
    backups_dir = Path(backups_dir)
    if not backups_dir.exists():
        return 0
    files = sorted(backups_dir.glob("state_*.zip"), key=lambda p: p.stat().st_mtime)
    removed = 0
    while len(files) > keep:
        old = files.pop(0)
        try:
            old.unlink()
            removed += 1
            logger.info("event=backup_rotated removed=%s", old)
        except OSError as exc:
            logger.warning("event=backup_rotate_failed path=%s err=%s", old, exc)
    return removed


def validate_json_file(path: Path) -> tuple[bool, str, Any | None]:
    """Return (ok, reason, parsed). Fail-closed on missing/empty/truncated/invalid."""
    path = Path(path)
    if not path.exists():
        return False, "missing", None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"unreadable:{exc}", None
    if not raw.strip():
        return False, "empty", None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, f"invalid_json:{exc}", None
    return True, "ok", data


def is_structurally_valid_positions(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if "positions" in data:
        return isinstance(data["positions"], (list, dict))
    return all(isinstance(v, dict) for v in data.values()) if data else True


def is_structurally_valid_intents(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    intents = data.get("intents")
    if intents is None:
        return True
    return isinstance(intents, list)


def list_backup_zips(backups_dir: Path) -> list[Path]:
    backups_dir = Path(backups_dir)
    if not backups_dir.exists():
        return []
    return sorted(backups_dir.glob("state_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)


def extract_file_from_backup(backup_zip: Path, arcname: str, dest: Path) -> tuple[bool, str]:
    """Extract a single arcname from backup zip to dest (atomic replace).

    Does NOT authorize trading. Caller must run startup barrier + reconciliation.
    """
    backup_zip = Path(backup_zip)
    dest = Path(dest)
    if not backup_zip.exists():
        return False, "backup_missing"
    try:
        with zipfile.ZipFile(backup_zip, "r") as zf:
            if arcname not in zf.namelist():
                return False, "arc_missing"
            data = zf.read(arcname)
            if arcname.endswith(".json"):
                try:
                    json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    return False, f"backup_corrupt:{exc}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(dest)
            return True, "restored"
    except (OSError, zipfile.BadZipFile) as exc:
        return False, f"extract_failed:{exc}"


def recover_json_state(
    primary: Path,
    backups_dir: Path,
    *,
    arcname: str,
    structural_ok=None,
) -> tuple[str, Any | None]:
    """Try primary then newest valid backup. Returns (source, data_or_None).

    source in: primary | backup | none
    Never invents trading state — None means empty/safe default at caller.
    """
    ok, reason, data = validate_json_file(primary)
    if ok and (structural_ok is None or structural_ok(data)):
        return "primary", data
    logger.warning("event=state_corrupt path=%s reason=%s", primary, reason)
    for z in list_backup_zips(backups_dir):
        tmp_out = primary.with_suffix(".recover_tmp")
        success, msg = extract_file_from_backup(z, arcname, tmp_out)
        if not success:
            continue
        ok2, reason2, data2 = validate_json_file(tmp_out)
        if ok2 and (structural_ok is None or structural_ok(data2)):
            try:
                tmp_out.replace(primary)
            except OSError:
                try:
                    tmp_out.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            logger.warning("event=state_recovered_from_backup path=%s backup=%s", primary, z)
            return "backup", data2
        try:
            tmp_out.unlink(missing_ok=True)
        except OSError:
            pass
    logger.error("event=state_recovery_failed path=%s — using empty safe default", primary)
    return "none", None
