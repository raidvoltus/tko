"""
Tokocrypto transaction friction model (research / evaluation only).

Aligned with documented Indonesian crypto fiscal structure (PMK-style
spot costs) for break-even analysis. Not order authority.

Round-trip IDR spot (conservative retail taker):
  buy:  taker 0.20% + CFX 0.0222% ≈ 0.2222%
  sell: taker 0.20% + PPh 0.21% + CFX 0.0222% ≈ 0.4322%
  total ≈ 0.6544% before spread/slippage

USDT spot (approx from research table):
  taker 0.15% each side + fiscal/clearing components ≈ 0.4044% one-way
  round-trip modeled conservatively.

References: research design (Tokocrypto MBX / PFAK cost structure).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class TokocryptoFriction:
    """Deterministic friction assumptions for backtest/evaluation."""

    market: str = "IDR_SPOT"  # IDR_SPOT | USDT_SPOT
    # IDR spot components (percent)
    taker_buy_pct: float = 0.20
    taker_sell_pct: float = 0.20
    cfx_buy_pct: float = 0.0222
    cfx_sell_pct: float = 0.0222
    pph_sell_pct: float = 0.21  # fiat off-ramp style drag on sell leg
    # USDT spot
    usdt_taker_pct: float = 0.15
    usdt_fiscal_one_way_pct: float = 0.0522  # residual fiscal/clearing approx
    # execution
    slip_pct: float = 0.10  # 0.1% default liquid names
    slip_mid_cap_pct: float = 0.30

    def entry_pct(self) -> float:
        if self.market == "USDT_SPOT":
            return self.usdt_taker_pct + self.usdt_fiscal_one_way_pct + self.slip_pct
        return self.taker_buy_pct + self.cfx_buy_pct + self.slip_pct

    def exit_pct(self) -> float:
        if self.market == "USDT_SPOT":
            return self.usdt_taker_pct + self.usdt_fiscal_one_way_pct + self.slip_pct
        return self.taker_sell_pct + self.pph_sell_pct + self.cfx_sell_pct + self.slip_pct

    def round_trip_pct(self) -> float:
        return self.entry_pct() + self.exit_pct()

    def break_even_move_pct(self) -> float:
        """Minimum favorable move to cover one round-trip (no edge)."""
        return self.round_trip_pct()

    def as_dict(self) -> Dict[str, float]:
        return {
            "market": 0.0 if self.market == "IDR_SPOT" else 1.0,
            "entry_pct": self.entry_pct(),
            "exit_pct": self.exit_pct(),
            "round_trip_pct": self.round_trip_pct(),
            "break_even_move_pct": self.break_even_move_pct(),
            "slip_pct": self.slip_pct,
        }


# Paper Table 1 conservative defaults
IDR_SPOT_FRICTION = TokocryptoFriction(market="IDR_SPOT")
USDT_SPOT_FRICTION = TokocryptoFriction(market="USDT_SPOT", slip_pct=0.10)
