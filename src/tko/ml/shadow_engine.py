"""Autonomous shadow engine — predictions only, LIVE_DISABLED forever here."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tko.ml.observation import ObservationConfig, ObservationEvent, ObservationWindow
from tko.ml.paper import PaperConfig, PaperLedger, PaperMode, simulate_round_trip
from tko.ml.shadow import ShadowLogger, shadow_decision

logger = logging.getLogger(__name__)


@dataclass
class ShadowInput:
    symbol: str
    primary_signal: str  # BUY|SELL|HOLD
    market_ts: float
    signal_ts: float
    entry_price: float
    exit_price: float | None = None
    champion_conf: float = 0.0
    challenger_conf: float = 0.0
    champion_id: str = ""
    challenger_id: str = ""
    feature_version: str = "v1"
    regime: str = ""
    min_confidence: float = 0.55


class ShadowEngine:
    """Parallel Champion/Challenger shadow evaluation.

    Explicitly LIVE_DISABLED — never places exchange orders.
    """

    MODE = PaperMode.LIVE_DISABLED

    def __init__(
        self,
        *,
        paper_path: Path,
        observation_path: Path,
        shadow_log_path: Path,
        paper_cfg: PaperConfig | None = None,
        obs_cfg: ObservationConfig | None = None,
    ) -> None:
        self.paper = PaperLedger(paper_path)
        self.paper._mode = PaperMode.SHADOW
        self.obs = ObservationWindow(observation_path, obs_cfg)
        self.shadow_log = ShadowLogger(shadow_log_path)
        self.paper_cfg = paper_cfg or PaperConfig()

    def evaluate(self, inp: ShadowInput) -> dict[str, Any]:
        if self.MODE.value == "LIVE":
            raise RuntimeError("shadow_engine_cannot_go_live")
        # fail-closed on stale: caller must pass market_ts; we only check presence
        if not inp.market_ts:
            return {"ok": False, "reason": "stale_or_missing_market_ts", "mode": self.MODE.value}

        champ = shadow_decision(
            primary_signal=inp.primary_signal,
            confidence=inp.champion_conf,
            min_confidence=inp.min_confidence,
            model_version=inp.champion_id or "champion",
            logger=self.shadow_log,
        )
        chall = shadow_decision(
            primary_signal=inp.primary_signal,
            confidence=inp.challenger_conf,
            min_confidence=inp.min_confidence,
            model_version=inp.challenger_id or "challenger",
            logger=self.shadow_log,
        )
        disagreement = bool(champ.get("would_filter") != chall.get("would_filter"))
        self.obs.log(
            ObservationEvent(
                ts=time.time(),
                symbol=inp.symbol,
                primary_signal=inp.primary_signal,
                champion_pred=int(champ.get("prediction", 0)),
                challenger_pred=int(chall.get("prediction", 0)),
                champion_conf=float(inp.champion_conf),
                challenger_conf=float(inp.challenger_conf),
                disagreement=disagreement,
                regime=inp.regime,
                market_ts=float(inp.market_ts),
            )
        )

        # hypothetical paper trades only when primary is BUY and model would allow
        results: dict[str, Any] = {
            "mode": PaperMode.SHADOW.value,
            "champion": champ,
            "challenger": chall,
            "disagreement": disagreement,
            "paper_trades": [],
        }
        if inp.primary_signal.upper() == "BUY" and inp.exit_price:
            for role, mid, conf, filt in (
                ("champion", inp.champion_id, inp.champion_conf, champ),
                ("challenger", inp.challenger_id, inp.challenger_conf, chall),
            ):
                if filt.get("would_filter"):
                    continue
                trade = simulate_round_trip(
                    symbol=inp.symbol,
                    side="BUY",
                    entry_price=inp.entry_price,
                    exit_price=float(inp.exit_price),
                    signal_ts=inp.signal_ts,
                    market_ts=inp.market_ts,
                    model_id=mid or role,
                    model_role=role,
                    feature_version=inp.feature_version,
                    regime=inp.regime,
                    cfg=self.paper_cfg,
                )
                self.paper.record(trade)
                results["paper_trades"].append(trade.to_dict())
        results["observation"] = self.obs.status()
        return results
