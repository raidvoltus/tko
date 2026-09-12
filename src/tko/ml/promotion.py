"""Promotion state machine — atomic, reversible, fail-closed."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from tko.ml.artifacts import load_and_verify_bundle
from tko.ml.challenger import ChallengerRegistry
from tko.ml.champion import ChampionRecord, ChampionRegistry


class PromoState(str, Enum):
    TRAINED = "TRAINED"
    GATE_EVALUATION = "GATE_EVALUATION"
    REJECTED = "REJECTED"
    SHADOW_ELIGIBLE = "SHADOW_ELIGIBLE"
    SHADOW_ACTIVE = "SHADOW_ACTIVE"
    CHALLENGER_VALIDATED = "CHALLENGER_VALIDATED"
    PROMOTION_ELIGIBLE = "PROMOTION_ELIGIBLE"
    PROMOTED = "PROMOTED"
    SAFE_FAIL = "SAFE_FAIL"


_ALLOWED: dict[PromoState, set[PromoState]] = {
    PromoState.TRAINED: {PromoState.GATE_EVALUATION, PromoState.REJECTED, PromoState.SAFE_FAIL},
    PromoState.GATE_EVALUATION: {
        PromoState.REJECTED,
        PromoState.SHADOW_ELIGIBLE,
        PromoState.SAFE_FAIL,
    },
    PromoState.SHADOW_ELIGIBLE: {
        PromoState.SHADOW_ACTIVE,
        PromoState.REJECTED,
        PromoState.SAFE_FAIL,
    },
    PromoState.SHADOW_ACTIVE: {
        PromoState.CHALLENGER_VALIDATED,
        PromoState.REJECTED,
        PromoState.SAFE_FAIL,
    },
    PromoState.CHALLENGER_VALIDATED: {
        PromoState.PROMOTION_ELIGIBLE,
        PromoState.REJECTED,
        PromoState.SAFE_FAIL,
    },
    PromoState.PROMOTION_ELIGIBLE: {
        PromoState.PROMOTED,
        PromoState.REJECTED,
        PromoState.SAFE_FAIL,
    },
    PromoState.PROMOTED: {PromoState.REJECTED, PromoState.SAFE_FAIL},
    PromoState.REJECTED: set(),
    PromoState.SAFE_FAIL: set(),
}


@dataclass
class PromotionLock:
    """Prevents duplicate promotion attempts."""

    path: Path

    def acquire(self, model_id: str) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                cur = json.loads(self.path.read_text(encoding="utf-8"))
                if cur.get("model_id") == model_id and cur.get("state") == "IN_PROGRESS":
                    return False
            except Exception:  # noqa: BLE001
                return False
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"model_id": model_id, "state": "IN_PROGRESS", "ts": time.time()}),
            encoding="utf-8",
        )
        tmp.replace(self.path)
        return True

    def release(self, *, final: str = "DONE") -> None:
        if self.path.exists():
            try:
                cur = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                cur = {}
            cur["state"] = final
            cur["ts"] = time.time()
            self.path.write_text(json.dumps(cur), encoding="utf-8")


class PromotionEngine:
    def __init__(
        self,
        *,
        champion_reg: ChampionRegistry,
        challenger_reg: ChallengerRegistry,
        lock_path: Path,
        state_path: Path,
    ) -> None:
        self.champions = champion_reg
        self.challengers = challenger_reg
        self.lock = PromotionLock(lock_path)
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def _write_state(self, model_id: str, state: PromoState, reason: str = "") -> None:
        payload = {"model_id": model_id, "state": state.value, "reason": reason, "ts": time.time()}
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def transition(self, model_id: str, new_state: PromoState, *, reason: str = "") -> PromoState:
        cur_raw = None
        if self.state_path.exists():
            try:
                cur_raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self._write_state(model_id, PromoState.SAFE_FAIL, "corrupt_state")
                return PromoState.SAFE_FAIL
        cur = PromoState(cur_raw["state"]) if cur_raw and cur_raw.get("model_id") == model_id else PromoState.TRAINED
        if new_state not in _ALLOWED.get(cur, set()) and not (
            cur == PromoState.TRAINED and new_state == PromoState.GATE_EVALUATION
        ):
            # allow bootstrap
            if cur_raw is None and new_state == PromoState.TRAINED:
                self._write_state(model_id, new_state, reason)
                return new_state
            if new_state not in _ALLOWED.get(cur, set()):
                self._write_state(model_id, PromoState.SAFE_FAIL, f"illegal:{cur.value}->{new_state.value}")
                return PromoState.SAFE_FAIL
        self._write_state(model_id, new_state, reason)
        return new_state

    def promote_challenger(self, model_id: str) -> dict[str, Any]:
        """Atomic promotion: challenger → champion. Requires PROMOTION_ELIGIBLE."""
        if not self.lock.acquire(model_id):
            return {"ok": False, "reason": "duplicate_promotion_lock"}
        try:
            chall = self.challengers.latest(model_id)
            if chall is None:
                return {"ok": False, "reason": "challenger_missing"}
            if chall.status not in ("PROMOTION_ELIGIBLE", "CHALLENGER_VALIDATED", "SHADOW_ACTIVE"):
                return {"ok": False, "reason": f"status_not_eligible:{chall.status}"}
            ok, reason = load_and_verify_bundle(Path(chall.artifact_dir)) if chall.artifact_dir else (False, "no_artifact")
            if chall.artifact_dir and not ok:
                self.challengers.update_status(model_id, "REJECTED")
                self.transition(model_id, PromoState.REJECTED, reason=reason)
                return {"ok": False, "reason": f"artifact:{reason}"}

            st = self.transition(model_id, PromoState.PROMOTED, reason="atomic_promote")
            if st != PromoState.PROMOTED:
                return {"ok": False, "reason": f"state:{st.value}"}

            champ = ChampionRecord(
                model_id=chall.model_id,
                version=chall.version,
                artifact_hash=chall.artifact_hash,
                artifact_dir=chall.artifact_dir,
                dataset_version=chall.dataset_version,
                feature_version=chall.feature_version,
                training_ts=chall.created_ts,
                evaluation_window=chall.validation_window,
                gate_results=dict(chall.gate_results),
                promotion_ts=time.time(),
                status="CHAMPION_ACTIVE",
            )
            self.champions.set_champion(champ)
            self.challengers.update_status(model_id, "PROMOTED")
            self.lock.release(final="PROMOTED")
            return {"ok": True, "champion": champ.to_dict()}
        except Exception as exc:  # noqa: BLE001
            self.transition(model_id, PromoState.SAFE_FAIL, reason=str(exc))
            self.lock.release(final="SAFE_FAIL")
            return {"ok": False, "reason": f"exception:{exc}"}

    def rollback_to_previous(self, *, reason: str = "degradation") -> dict[str, Any]:
        hist = self.champions.history()
        retired = [h for h in hist if h.status == "CHAMPION_RETIRED"]
        active = self.champions.get_active()
        if active:
            self.champions._append(
                ChampionRecord(
                    **{**active.to_dict(), "status": "CHAMPION_RETIRED"}
                )
            )
        if not retired:
            self.champions.clear()
            return {"ok": True, "ml_off": True, "reason": "no_previous_champion"}
        prev = retired[-1]
        restored = ChampionRecord(
            **{**prev.to_dict(), "status": "CHAMPION_ACTIVE", "promotion_ts": time.time()}
        )
        if restored.artifact_dir:
            ok, why = load_and_verify_bundle(Path(restored.artifact_dir))
            if not ok:
                self.champions.clear()
                return {"ok": True, "ml_off": True, "reason": f"prev_corrupt:{why}"}
        self.champions.set_champion(restored)
        return {"ok": True, "ml_off": False, "champion": restored.to_dict(), "reason": reason}
