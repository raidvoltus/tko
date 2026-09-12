"""Automatic ML rollback to deterministic baseline."""

from __future__ import annotations

import logging
from pathlib import Path

from tko.ml.artifacts import load_and_verify_bundle
from tko.ml.registry import ModelRegistry, ModelState

logger = logging.getLogger(__name__)


def rollback_to_baseline(registry: ModelRegistry, *, reason: str = "auto_rollback") -> dict:
    """Deactivate ML: clear active pointer, mark ACTIVE as REJECTED/RETIRED.

    Never loads corrupt artifacts. Fail-closed → ML OFF.
    """
    active = registry.get_active()
    result = {"cleared": False, "model_id": None, "reason": reason}
    if not active:
        registry.clear_active()
        result["cleared"] = True
        result["reason"] = "no_active"
        return result
    mid = active.get("model_id")
    result["model_id"] = mid
    if mid:
        rec = registry.latest(mid)
        if rec and rec.state == ModelState.ACTIVE.value:
            try:
                registry.transition(mid, ModelState.RETIRED, notes=reason)
            except ValueError:
                try:
                    registry.transition(mid, ModelState.REJECTED, notes=reason)
                except ValueError:
                    pass
    registry.clear_active()
    result["cleared"] = True
    logger.warning("event=ml_rollback reason=%s model_id=%s", reason, mid)
    return result


def safe_load_active_artifact(registry: ModelRegistry) -> tuple[bool, str, Path | None]:
    """Load active model only if checksum valid. Else clear active."""
    active = registry.get_active()
    if not active:
        return False, "no_active", None
    art = Path(active.get("artifact_dir") or "")
    ok, reason = load_and_verify_bundle(art)
    if not ok:
        registry.clear_active()
        mid = active.get("model_id")
        if mid:
            try:
                registry.transition(str(mid), ModelState.CORRUPTED, notes=reason)
            except ValueError:
                pass
        return False, reason, None
    return True, "ok", art
