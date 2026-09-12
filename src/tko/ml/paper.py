"""Isolated paper execution — zero production side effects.

Never touches PositionStore, IntentStore, production PnL, or exchange.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class PaperMode(str, Enum):
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    LIVE_DISABLED = "LIVE_DISABLED"


@dataclass
class PaperTrade:
    trade_id: str
    signal_ts: float
    decision_ts: float
    market_ts: float
    symbol: str
    side: str
    entry_price: float
    exit_price: float | None
    quantity: float
    notional: float
    fee_rate: float
    slippage_bps: float
    gross_pnl: float
    net_pnl: float
    holding_bars: int
    outcome: str  # win|loss|open|flat
    model_id: str
    model_role: str  # champion|challenger
    feature_version: str
    regime: str
    mode: str = PaperMode.PAPER.value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PaperConfig:
    fee_rate: float = 0.001
    slippage_bps: float = 5.0
    default_notional: float = 1_000_000.0  # quote units (e.g. IDR)


class PaperLedger:
    """Append-only isolated paper ledger. No production stores."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._mode = PaperMode.PAPER

    @property
    def mode(self) -> PaperMode:
        return self._mode

    def record(self, trade: PaperTrade) -> None:
        if self._mode == PaperMode.LIVE_DISABLED:
            return
        trade.mode = self._mode.value
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(trade.to_dict()) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def metrics(self, *, model_id: str | None = None) -> dict[str, float]:
        rows = self.read_all()
        if model_id:
            rows = [r for r in rows if r.get("model_id") == model_id]
        pnls = [float(r.get("net_pnl", 0.0)) for r in rows if r.get("outcome") != "open"]
        if not pnls:
            return {
                "n_trades": 0.0,
                "net_pnl": 0.0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "expectancy": 0.0,
            }
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gp = sum(wins)
        gl = abs(sum(losses))
        pf = (gp / gl) if gl > 1e-12 else (10.0 if gp > 0 else 0.0)
        return {
            "n_trades": float(len(pnls)),
            "net_pnl": float(sum(pnls)),
            "win_rate": float(len(wins) / len(pnls)),
            "profit_factor": float(pf),
            "expectancy": float(sum(pnls) / len(pnls)),
        }


def simulate_round_trip(
    *,
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    signal_ts: float,
    market_ts: float,
    model_id: str,
    model_role: str,
    feature_version: str = "v1",
    regime: str = "",
    holding_bars: int = 1,
    cfg: PaperConfig | None = None,
) -> PaperTrade:
    """Deterministic hypothetical fill. No exchange calls."""
    cfg = cfg or PaperConfig()
    if entry_price <= 0 or exit_price <= 0:
        raise ValueError("invalid_price")
    slip = cfg.slippage_bps / 10_000.0
    if side.upper() == "BUY":
        ep = entry_price * (1.0 + slip)
        xp = exit_price * (1.0 - slip)
        qty = cfg.default_notional / ep
        gross = (xp - ep) * qty
    else:
        ep = entry_price * (1.0 - slip)
        xp = exit_price * (1.0 + slip)
        qty = cfg.default_notional / ep
        gross = (ep - xp) * qty
    fee = cfg.fee_rate * cfg.default_notional * 2.0
    net = gross - fee
    outcome = "win" if net > 0 else ("loss" if net < 0 else "flat")
    return PaperTrade(
        trade_id=uuid.uuid4().hex[:12],
        signal_ts=float(signal_ts),
        decision_ts=time.time(),
        market_ts=float(market_ts),
        symbol=symbol,
        side=side.upper(),
        entry_price=float(ep),
        exit_price=float(xp),
        quantity=float(qty),
        notional=float(cfg.default_notional),
        fee_rate=float(cfg.fee_rate),
        slippage_bps=float(cfg.slippage_bps),
        gross_pnl=float(gross),
        net_pnl=float(net),
        holding_bars=int(holding_bars),
        outcome=outcome,
        model_id=model_id,
        model_role=model_role,
        feature_version=feature_version,
        regime=regime,
        mode=PaperMode.PAPER.value,
    )
