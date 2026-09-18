"""FULL AUTOPILOT Portfolio Rotation Engine.

AssetScanner → OpportunityScorer → RouteOptimizer → RebalancePlanner

net_opportunity =
  expected_return
  - trading_fee
  - spread_cost
  - estimated_slippage
  - market_impact
  - execution_risk_penalty
  - volatility_risk_penalty
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AssetBalance:
    asset: str
    free: float
    locked: float
    total: float
    price_usdt: float = 0.0
    value_usdt: float = 0.0


@dataclass
class Opportunity:
    from_asset: str
    to_asset: str
    symbol: str                 # trading pair used
    route: List[str]            # e.g. ["BTC", "USDT", "ETH"] or ["BTC", "ETH"]
    expected_return_pct: float
    fee_pct: float
    spread_pct: float
    slippage_pct: float
    impact_pct: float
    exec_risk_pct: float
    vol_risk_pct: float
    net_opportunity_pct: float
    score: float
    quantity: float = 0.0
    notional_usdt: float = 0.0
    reason: str = ""


@dataclass
class PortfolioSnapshot:
    balances: Dict[str, AssetBalance] = field(default_factory=dict)
    total_value_usdt: float = 0.0
    available_usdt: float = 0.0
    ts: float = field(default_factory=time.time)

    def value_of(self, asset: str) -> float:
        b = self.balances.get(asset)
        return b.value_usdt if b else 0.0


@dataclass
class RotationPlan:
    cycle_id: str
    opportunities: List[Opportunity]
    selected: Optional[Opportunity]
    hold: bool
    reason: str
    ts: float = field(default_factory=time.time)


class AssetScanner:
    """Scan tradeable symbols from exchange info + filter by liquidity."""

    def __init__(
        self,
        min_quote_volume: float = 5000.0,
        prefer_quote: Optional[List[str]] = None,
        whitelist: Optional[List[str]] = None,
        blacklist: Optional[List[str]] = None,
        max_symbols: int = 40,
    ):
        self.min_quote_volume = min_quote_volume
        self.prefer_quote = prefer_quote or ["USDT", "IDR", "USDC"]
        self.whitelist = set(whitelist or [])
        self.blacklist = set(blacklist or [])
        self.max_symbols = max_symbols

    @staticmethod
    def extract_symbol_list(exchange_info: Dict[str, Any]) -> List[Any]:
        """Normalize Tokocrypto / Binance-like exchangeInfo shapes to a list of symbol dicts."""
        if not isinstance(exchange_info, dict):
            return []
        # direct list
        for key in ("symbols", "list"):
            v = exchange_info.get(key)
            if isinstance(v, list):
                return v
        data = exchange_info.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("list", "symbols", "symbolList", "items"):
                v = data.get(key)
                if isinstance(v, list):
                    return v
        return []

    def scan(self, exchange_info: Dict[str, Any], tickers: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Return list of tradeable symbol dicts bounded by max_symbols."""
        raw = self.extract_symbol_list(exchange_info)
        if not isinstance(raw, list) or not raw:
            logger.warning(
                "symbol scan: empty list (top_keys=%s data_type=%s)",
                list(exchange_info.keys())[:12] if isinstance(exchange_info, dict) else type(exchange_info),
                type(exchange_info.get("data")) if isinstance(exchange_info, dict) else None,
            )
            return []
        out: List[Dict[str, Any]] = []
        for s in raw:
            if not isinstance(s, dict):
                continue
            sym = str(s.get("symbol") or s.get("symbolName") or "").strip()
            if not sym:
                continue
            # normalize BTCUSDT -> BTC_USDT when assets known
            status = str(s.get("status", s.get("symbolStatus", "TRADING"))).upper()
            if status not in ("TRADING", "1", "ACTIVE", "ENABLED", ""):
                continue
            quote = str(s.get("quoteAsset") or s.get("quote") or "").upper()
            base = str(s.get("baseAsset") or s.get("base") or "").upper()
            if "_" not in sym and base and quote and sym.upper() == f"{base}{quote}":
                sym = f"{base}_{quote}"
            if self.whitelist and sym not in self.whitelist and sym.replace("_", "") not in {w.replace("_", "") for w in self.whitelist}:
                continue
            if sym in self.blacklist or sym.replace("_", "") in {b.replace("_", "") for b in self.blacklist}:
                continue
            if self.prefer_quote and quote not in self.prefer_quote:
                # still allow if high volume later; for now soft prefer
                pass
            # volume filter if ticker available
            vol = 0.0
            if tickers and sym in tickers:
                try:
                    vol = float(tickers[sym].get("quoteVolume") or tickers[sym].get("v") or 0)
                except Exception:
                    vol = 0.0
            if tickers and vol < self.min_quote_volume and vol > 0:
                continue
            out.append({
                "symbol": sym,
                "base": base,
                "quote": quote,
                "raw": s,
                "quote_volume": vol,
            })
        # prefer USDT pairs, then by volume
        def key(x):
            q = x["quote"]
            pref = 0 if q == "USDT" else (1 if q == "IDR" else 2)
            return (pref, -x.get("quote_volume", 0))
        out.sort(key=key)
        return out[: self.max_symbols]


class OpportunityScorer:
    """Score net opportunity for rotating from_asset → to_asset."""

    FEE_PCT = 0.20          # taker-ish default %
    DEFAULT_SPREAD = 0.05
    DEFAULT_SLIPPAGE = 0.10
    DEFAULT_IMPACT = 0.05
    DEFAULT_EXEC_RISK = 0.10
    DEFAULT_VOL_RISK = 0.15

    def score(
        self,
        from_asset: str,
        to_asset: str,
        expected_return_pct: float,
        spread_pct: Optional[float] = None,
        volatility_pct: Optional[float] = None,
        liquidity_score: float = 1.0,
        fee_pct: Optional[float] = None,
    ) -> Opportunity:
        fee = fee_pct if fee_pct is not None else self.FEE_PCT
        spread = spread_pct if spread_pct is not None else self.DEFAULT_SPREAD
        slip = self.DEFAULT_SLIPPAGE / max(liquidity_score, 0.1)
        impact = self.DEFAULT_IMPACT / max(liquidity_score, 0.1)
        exec_r = self.DEFAULT_EXEC_RISK
        vol_r = (volatility_pct or 1.0) * 0.1
        net = (
            expected_return_pct
            - fee
            - spread
            - slip
            - impact
            - exec_r
            - vol_r
        )
        return Opportunity(
            from_asset=from_asset,
            to_asset=to_asset,
            symbol="",
            route=[from_asset, to_asset],
            expected_return_pct=expected_return_pct,
            fee_pct=fee,
            spread_pct=spread,
            slippage_pct=slip,
            impact_pct=impact,
            exec_risk_pct=exec_r,
            vol_risk_pct=vol_r,
            net_opportunity_pct=net,
            score=net,
        )


class RouteOptimizer:
    """Choose A→B direct or A→USDT→B / A→IDR→B based on pair availability + cost."""

    INTERMEDIATES = ["USDT", "IDR", "USDC", "BTC"]

    def __init__(self, available_pairs: Optional[Dict[str, str]] = None):
        # map (base, quote) -> symbol
        self.pairs = available_pairs or {}

    def set_pairs(self, symbols: List[Dict[str, Any]]) -> None:
        self.pairs = {}
        for s in symbols:
            base = s.get("base") or ""
            quote = s.get("quote") or ""
            sym = s.get("symbol") or ""
            if base and quote and sym:
                self.pairs[(base.upper(), quote.upper())] = sym
                self.pairs[(quote.upper(), base.upper())] = sym  # allow reverse lookup

    def has_pair(self, a: str, b: str) -> Optional[str]:
        return self.pairs.get((a.upper(), b.upper()))

    def find_route(self, from_asset: str, to_asset: str) -> List[str]:
        fa, ta = from_asset.upper(), to_asset.upper()
        if fa == ta:
            return [fa]
        # direct
        if self.has_pair(fa, ta):
            return [fa, ta]
        # via intermediate
        best: Optional[List[str]] = None
        for mid in self.INTERMEDIATES:
            if mid == fa or mid == ta:
                continue
            if self.has_pair(fa, mid) and self.has_pair(mid, ta):
                route = [fa, mid, ta]
                if best is None or len(route) < len(best):
                    best = route
        return best or [fa, ta]  # fallback (may fail at execution)


class PortfolioRotationEngine:
    """Orchestrates one rotation cycle under resource limits."""

    def __init__(
        self,
        scanner: Optional[AssetScanner] = None,
        scorer: Optional[OpportunityScorer] = None,
        router: Optional[RouteOptimizer] = None,
        min_edge_pct: float = 0.35,
        min_holding_period_sec: float = 300,
        cooldown_sec: float = 60,
        max_daily_turnover_pct: float = 50,
    ):
        self.scanner = scanner or AssetScanner()
        self.scorer = scorer or OpportunityScorer()
        self.router = router or RouteOptimizer()
        self.min_edge_pct = min_edge_pct
        self.min_holding_period_sec = min_holding_period_sec
        self.cooldown_sec = cooldown_sec
        self.max_daily_turnover_pct = max_daily_turnover_pct
        self._last_rotation_ts = 0.0
        self._hold_since: Dict[str, float] = {}
        self._daily_turnover_usdt = 0.0
        self._day_start = time.time()

    def build_snapshot(
        self,
        balances_raw: Dict[str, Dict[str, float]],
        prices_usdt: Dict[str, float],
    ) -> PortfolioSnapshot:
        snap = PortfolioSnapshot()
        total = 0.0
        for asset, bal in balances_raw.items():
            free = float(bal.get("free", 0) or 0)
            locked = float(bal.get("locked", 0) or 0)
            px = float(prices_usdt.get(asset, 0) or 0)
            if asset.upper() in ("USDT", "USDC", "BUSD"):
                px = 1.0
            val = (free + locked) * px
            snap.balances[asset.upper()] = AssetBalance(
                asset=asset.upper(), free=free, locked=locked, total=free + locked,
                price_usdt=px, value_usdt=val,
            )
            total += val
        snap.total_value_usdt = total
        usdt = snap.balances.get("USDT")
        snap.available_usdt = usdt.free if usdt else 0.0
        return snap

    def plan_cycle(
        self,
        snapshot: PortfolioSnapshot,
        expected_returns: Dict[str, float],
        spreads: Optional[Dict[str, float]] = None,
        symbols: Optional[List[Dict[str, Any]]] = None,
    ) -> RotationPlan:
        cycle_id = uuid.uuid4().hex[:16]
        now = time.time()

        # reset daily turnover
        if now - self._day_start > 86400:
            self._daily_turnover_usdt = 0.0
            self._day_start = now

        if now - self._last_rotation_ts < self.cooldown_sec:
            return RotationPlan(cycle_id, [], None, True, "cooldown")

        if symbols:
            self.router.set_pairs(symbols)

        # candidates: assets we hold with value > dust, and target assets with positive expected return
        holdings = [
            a for a, b in snapshot.balances.items()
            if b.value_usdt >= 1.0 and b.free > 0
        ]
        targets = list(expected_returns.keys())
        if not targets:
            targets = ["USDT", "BTC", "ETH"]

        opps: List[Opportunity] = []
        spreads = spreads or {}

        for from_a in holdings:
            for to_a in targets:
                if from_a == to_a:
                    continue
                # holding period check
                held = self._hold_since.get(from_a, 0)
                if held and (now - held) < self.min_holding_period_sec:
                    continue
                exp = float(expected_returns.get(to_a, 0)) - float(expected_returns.get(from_a, 0))
                route = self.router.find_route(from_a, to_a)
                spread = spreads.get(f"{from_a}_{to_a}", spreads.get(to_a, 0.05))
                opp = self.scorer.score(from_a, to_a, exp, spread_pct=spread)
                opp.route = route
                # attach symbol for first leg
                if len(route) >= 2:
                    sym = self.router.has_pair(route[0], route[1])
                    opp.symbol = sym or f"{route[0]}_{route[1]}"
                from_bal = snapshot.balances.get(from_a)
                if from_bal:
                    opp.notional_usdt = min(from_bal.value_usdt * 0.5, from_bal.value_usdt)  # partial rotate
                    opp.quantity = from_bal.free * 0.5 if from_bal.price_usdt <= 0 else (
                        opp.notional_usdt / from_bal.price_usdt
                    )
                opps.append(opp)

        opps.sort(key=lambda o: o.net_opportunity_pct, reverse=True)
        selected = None
        hold = True
        reason = "no_edge"

        if opps and opps[0].net_opportunity_pct >= self.min_edge_pct:
            # turnover cap
            if snapshot.total_value_usdt > 0:
                projected = self._daily_turnover_usdt + opps[0].notional_usdt
                if (projected / snapshot.total_value_usdt) * 100 > self.max_daily_turnover_pct:
                    reason = "max_daily_turnover"
                else:
                    selected = opps[0]
                    hold = False
                    reason = f"rotate {selected.from_asset}->{selected.to_asset} net={selected.net_opportunity_pct:.3f}%"
        elif not opps:
            if not holdings:
                reason = "no_candidates_empty_balance"
            else:
                reason = "no_candidates"

        return RotationPlan(cycle_id, opps[:10], selected, hold, reason)

    def mark_rotated(self, from_asset: str, notional: float) -> None:
        self._last_rotation_ts = time.time()
        self._hold_since[from_asset] = time.time()
        self._daily_turnover_usdt += notional
