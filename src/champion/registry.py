"""Champion registry + challenger tracking. Deterministic rollback."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.champion.manifest import ChampionManifest, ChallengerManifest
from src.champion.states import ALLOWED_TRANSITIONS, ChallengerState

logger = logging.getLogger(__name__)


class ChampionRegistry:
    def __init__(self, store_dir: Optional[str] = None):
        self.store_dir = Path(store_dir) if store_dir else None
        self.champion: Optional[ChampionManifest] = None
        self.previous: Optional[ChampionManifest] = None
        self.challengers: Dict[str, ChallengerManifest] = {}
        self.history: List[Dict[str, Any]] = []

    def set_champion(self, manifest: ChampionManifest, reason: str = "init") -> None:
        if self.champion is not None:
            self.previous = self.champion
        self.champion = manifest
        self.history.append(
            {
                "event": "SET_CHAMPION",
                "champion_id": manifest.champion_id,
                "identity": manifest.identity_hash(),
                "reason": reason,
                "ts": time.time(),
            }
        )
        self._persist()
        logger.info("Champion set: %s (%s)", manifest.champion_id, reason)

    def register_challenger(self, m: ChallengerManifest) -> None:
        self.challengers[m.challenger_id] = m
        self.history.append(
            {"event": "REGISTER_CHALLENGER", "id": m.challenger_id, "type": m.challenger_type, "ts": time.time()}
        )
        self._persist()

    def transition(self, challenger_id: str, new_state: ChallengerState, reason: str = "") -> bool:
        ch = self.challengers.get(challenger_id)
        if not ch:
            return False
        try:
            cur = ChallengerState(ch.state)
        except ValueError:
            return False
        allowed = ALLOWED_TRANSITIONS.get(cur, set())
        if new_state not in allowed:
            logger.warning(
                "Illegal transition %s: %s -> %s", challenger_id, cur, new_state
            )
            return False
        ch.state = new_state.value
        self.history.append(
            {
                "event": "TRANSITION",
                "id": challenger_id,
                "from": cur.value,
                "to": new_state.value,
                "reason": reason,
                "ts": time.time(),
            }
        )
        self._persist()
        return True

    def promote(self, challenger_id: str, new_champion: ChampionManifest) -> bool:
        ch = self.challengers.get(challenger_id)
        if not ch or ch.state != ChallengerState.ELIGIBLE.value:
            logger.error("Promote denied: challenger not ELIGIBLE")
            return False
        if not self.transition(challenger_id, ChallengerState.PROMOTED, "promotion"):
            return False
        self.set_champion(new_champion, reason=f"promoted_from:{challenger_id}")
        return True

    def rollback(self) -> bool:
        if self.previous is None:
            logger.error("Rollback denied: no previous champion")
            return False
        self.champion, self.previous = self.previous, self.champion
        self.history.append(
            {
                "event": "ROLLBACK",
                "champion_id": self.champion.champion_id if self.champion else None,
                "ts": time.time(),
            }
        )
        self._persist()
        logger.warning("Champion rolled back to %s", self.champion.champion_id if self.champion else None)
        return True

    def status(self) -> Dict[str, Any]:
        return {
            "champion_status": self.champion.champion_id if self.champion else None,
            "champion_identity": self.champion.identity_hash() if self.champion else None,
            "rollback_available": self.previous is not None,
            "challengers": {
                k: {"state": v.state, "type": v.challenger_type} for k, v in self.challengers.items()
            },
            "promotion_eligible": any(
                v.state == ChallengerState.ELIGIBLE.value for v in self.challengers.values()
            ),
        }

    def _persist(self) -> None:
        if not self.store_dir:
            return
        self.store_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "champion": self.champion.to_dict() if self.champion else None,
            "previous": self.previous.to_dict() if self.previous else None,
            "challengers": {k: v.to_dict() for k, v in self.challengers.items()},
            "history": self.history[-200:],
        }
        path = self.store_dir / "champion_registry.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
