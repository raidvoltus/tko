"""Artifact integrity: checksums and atomic publication."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> str:
    """Atomic write JSON; returns sha256 of content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    digest = sha256_bytes(raw)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        Path(tmp_name).replace(path)
    except Exception:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return digest


def verify_checksum(path: Path, expected_sha256: str) -> bool:
    if not path.exists():
        return False
    return sha256_file(path) == expected_sha256


def publish_model_bundle(
    dest_dir: Path,
    *,
    model_bytes: bytes,
    metadata: dict[str, Any],
    metrics: dict[str, Any],
    calibration: dict[str, Any],
    feature_schema: dict[str, Any],
) -> dict[str, str]:
    """Atomically publish model.joblib + sidecar JSON files with checksums.

    Incomplete publish → no active pointer updated (caller responsibility).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    checksums: dict[str, str] = {}
    model_path = dest_dir / "model.joblib"
    fd, tmp_name = tempfile.mkstemp(dir=str(dest_dir), prefix=".tmp_", suffix=".joblib")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(model_bytes)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        Path(tmp_name).replace(model_path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    checksums["model.joblib"] = sha256_bytes(model_bytes)
    checksums["metadata.json"] = write_json_atomic(dest_dir / "metadata.json", metadata)
    checksums["metrics.json"] = write_json_atomic(dest_dir / "metrics.json", metrics)
    checksums["calibration.json"] = write_json_atomic(dest_dir / "calibration.json", calibration)
    checksums["feature_schema.json"] = write_json_atomic(dest_dir / "feature_schema.json", feature_schema)
    write_json_atomic(dest_dir / "checksums.json", checksums)
    return checksums


def load_and_verify_bundle(dest_dir: Path) -> tuple[bool, str]:
    """Return (ok, reason). Fail-closed on any mismatch/missing file."""
    dest_dir = Path(dest_dir)
    check_path = dest_dir / "checksums.json"
    if not check_path.exists():
        return False, "missing_checksums"
    try:
        expected = json.loads(check_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"checksums_unreadable:{exc}"
    for name, digest in expected.items():
        p = dest_dir / name
        if name == "checksums.json":
            continue
        if not p.exists():
            return False, f"missing:{name}"
        if sha256_file(p) != digest:
            return False, f"checksum_mismatch:{name}"
    return True, "ok"
