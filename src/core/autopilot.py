"""Autopilot cycle runner - wires seven planes, single execution worker."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.control.plane import ControlPlane
from src.decision.plane import DecisionPlane
from src.execution.manager import ExecutionManager
from src.features.engine import CandleBuffer, FeatureEngine
from src.observability.cycle import CycleJournal, DataQualityGate
from src.portfolio.account_state import AccountReconciler, AccountStatus
from src.portfolio.rotation import (
    AssetScanner,
    OpportunityScorer,
    PortfolioRotationEngine,
    RouteOptimizer,
)
from src.risk.engine import RiskEngine, RiskLimits
from src.telegram.notifier import TelegramNotifier
from src.tokocrypto.rest import RestClient

logger = logging.getLogger(__name__)


class Autopilot:
    """Single-process full autopilot loop for resource-constrained node."""

    def __init__(self, root: Optional[Path] = None):
        # None → ControlPlane uses frozen-aware ProgramData / install_dir paths
        self.root = Path(root) if root is not None else None
        self.control = ControlPlane(self.root)
        try:
            self.cfg = self.control.load_config()
        except Exception as e:
            logger.error("Config load failed: %s — using safe defaults", e)
            self.cfg = {"mode": "LIVE", "cycle_interval_sec": 30}

        risk_cfg = self.cfg.get("risk") or {}
        self.risk = RiskEngine(
            RiskLimits(
                max_order_value_usdt=float(risk_cfg.get("max_order_value_usdt", 100)),
                max_position_usdt=float(risk_cfg.get("max_position_usdt", 500)),
                max_exposure_usdt=float(risk_cfg.get("max_exposure_usdt", 1000)),
                max_daily_loss_usdt=float(risk_cfg.get("max_daily_loss_usdt", 50)),
                max_consecutive_losses=int(risk_cfg.get("max_consecutive_losses", 5)),
                min_balance_usdt=float(risk_cfg.get("min_balance_usdt", 10)),
                max_stale_market_sec=float(risk_cfg.get("max_stale_market_sec", 45)),
            )
        )
        self.rest = RestClient()
        from src.execution.production_policy import require_live
        try:
            mode = require_live(str(self.cfg.get("mode", "LIVE")))
        except ValueError as e:
            logger.error("%s — NO TRADE", e)
            mode = "LIVE"
            self.cfg["mode"] = "LIVE"
        self.exec_mgr = ExecutionManager(self.rest, self.risk, mode=mode)
        self.tg = TelegramNotifier()

        pf = self.cfg.get("portfolio") or {}
        hw = self.cfg.get("hardware") or {}
        sym_cfg = self.cfg.get("symbols") or {}

        self.scanner = AssetScanner(
            min_quote_volume=float(sym_cfg.get("min_quote_volume_usdt", 5000)),
            prefer_quote=list(sym_cfg.get("prefer_quote") or ["USDT", "IDR"]),
            whitelist=list(sym_cfg.get("whitelist") or []),
            blacklist=list(sym_cfg.get("blacklist") or []),
            max_symbols=int(hw.get("max_symbols_scan", 40)),
        )
        self.rotation = PortfolioRotationEngine(
            scanner=self.scanner,
            scorer=OpportunityScorer(),
            router=RouteOptimizer(),
            min_edge_pct=float(pf.get("min_edge_pct", 0.35)),
            min_holding_period_sec=float(pf.get("min_holding_period_sec", 300)),
            cooldown_sec=float(pf.get("cooldown_sec", 60)),
            max_daily_turnover_pct=float(pf.get("max_daily_turnover_pct", 50)),
        )
        buf_size = int(hw.get("candle_buffer_size", 300))
        self.candles = CandleBuffer(maxlen=buf_size)
        self.features = FeatureEngine(self.candles)
        self.decision = DecisionPlane(feature_version=str((self.cfg.get("ml") or {}).get("feature_version", "v1")))
        self.quality = DataQualityGate(
            max_stale_sec=float(risk_cfg.get("max_stale_market_sec", 45)),
        )
        self.journal = CycleJournal(self.control.audit_dir)

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.running = False
        self.last_cycle: Dict[str, Any] = {}
        self.symbols_cache: List[Dict[str, Any]] = []
        self.prices: Dict[str, float] = {}
        self.balances: Dict[str, Dict[str, float]] = {}
        self.balance_source: str = "EMPTY"
        self.account_recon = AccountReconciler()
        self.account_state = self.account_recon.state
        self.model_valid = False
        self.logs: List[str] = []

    def _log(self, msg: str, level: str = "INFO") -> None:
        line = f"{time.strftime('%H:%M:%S')} [{level}] {msg}"
        self.logs.append(line)
        if len(self.logs) > 150:
            self.logs = self.logs[-80:]
        logger.log(getattr(logging, level, logging.INFO), msg)

    def configure_credentials(self, api_key: str, api_secret: str, tg_token: str = "", tg_chat: str = "") -> None:
        self.rest.api_key = api_key
        self.rest.api_secret = api_secret
        if tg_token and tg_chat:
            self.tg.configure(tg_token, tg_chat)
            self.tg.start()

    def start(self) -> None:
        if self.running:
            return
        if self.control.is_kill_switch_active():
            self.risk.activate_kill_switch("file_flag")
            self._log("Kill switch flag present — not starting LIVE orders", "WARN")
        self.running = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._log(f"Autopilot started mode={self.exec_mgr.mode}")
        self.tg.notify_system("Autopilot started", f"Mode={self.exec_mgr.mode}")

    def stop(self) -> None:
        self._stop.set()
        self.running = False
        self._log("Autopilot stopped")
        self.tg.notify_system("Autopilot stopped")

    def kill(self) -> None:
        self.control.activate_kill_switch("ui_or_api")
        self.risk.activate_kill_switch("ui_or_api")
        self.stop()
        self.tg.notify_system("EMERGENCY KILL SWITCH")

    def _loop(self) -> None:
        interval = float(self.cfg.get("cycle_interval_sec", 30))
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self.run_cycle()
            except Exception as e:
                self._log(f"Cycle error: {e}", "ERROR")
                self.control.append_audit("errors", {"ts": time.time(), "error": str(e)})
            elapsed = time.time() - t0
            sleep_for = max(1.0, interval - elapsed)
            self._stop.wait(sleep_for)


    @staticmethod
    def _extract_balances(body: Dict[str, Any]) -> Optional[Dict[str, Dict[str, float]]]:
        """Normalize Tokocrypto account payload to {ASSET: {free, locked}}."""
        if not isinstance(body, dict):
            return None
        candidates = []
        data = body.get("data")
        if isinstance(data, list):
            candidates.append(data)
        if isinstance(data, dict):
            for k in ("balances", "balance", "list", "spotBalances", "assets"):
                v = data.get(k)
                if isinstance(v, list):
                    candidates.append(v)
        for k in ("balances", "balance", "list"):
            v = body.get(k)
            if isinstance(v, list):
                candidates.append(v)
        raw = None
        for c in candidates:
            if isinstance(c, list):
                raw = c
                if c:
                    break
        if not isinstance(raw, list):
            return None
        out: Dict[str, Dict[str, float]] = {}
        for b in raw:
            if not isinstance(b, dict):
                continue
            asset = str(b.get("asset") or b.get("coin") or b.get("currency") or "").upper()
            if not asset:
                continue
            try:
                free = float(b.get("free") or b.get("available") or b.get("avail") or 0)
                locked = float(b.get("locked") or b.get("freeze") or b.get("frozen") or 0)
            except (TypeError, ValueError):
                continue
            if free + locked > 0:
                out[asset] = {"free": free, "locked": locked}
        return out

    @staticmethod
    def _parse_ticker_price(tick: Dict[str, Any]) -> float:
        if not isinstance(tick, dict):
            return 0.0
        data = tick.get("data") if isinstance(tick.get("data"), dict) else tick
        if not isinstance(data, dict):
            data = tick
        for k in ("last", "lastPrice", "price", "c", "close", "bid", "ask"):
            v = data.get(k)
            try:
                if v is not None and float(v) > 0:
                    return float(v)
            except (TypeError, ValueError):
                continue
        return 0.0

    def run_cycle(self) -> Dict[str, Any]:
        """One full autopilot cycle — bounded work for i3/8GB."""
        t0 = time.time()
        cycle_id = uuid.uuid4().hex[:16]

        if self.control.is_kill_switch_active():
            self.risk.kill_switch = True

        # 1) exchange info / symbol scan (cached lightly)
        if not self.symbols_cache:
            try:
                info = self.rest.exchange_info()
                self.symbols_cache = self.scanner.scan(info if isinstance(info, dict) else {})
                if not self.symbols_cache:
                    # config fallback so LIVE/PAPER can still operate on majors
                    fb = list((self.cfg.get("symbols") or {}).get("fallback") or [])
                    if not fb:
                        fb = ["BTC_USDT", "ETH_USDT", "BNB_USDT", "SOL_USDT"]
                    self.symbols_cache = [
                        {"symbol": s, "base": s.split("_")[0], "quote": s.split("_")[-1], "raw": {}, "quote_volume": 0.0}
                        for s in fb
                    ]
                    err = ""
                    if isinstance(info, dict):
                        err = str(info.get("error") or info.get("msg") or info.get("code") or "")
                        err += f" http={info.get('_http_status')} keys={list(info.keys())[:8]}"
                    self._log(
                        f"Scanned 0 from API — using fallback {len(self.symbols_cache)} symbols ({err})",
                        "WARN",
                    )
                else:
                    self.rotation.router.set_pairs(self.symbols_cache)
                    self._log(f"Scanned {len(self.symbols_cache)} symbols")
                self.rotation.router.set_pairs(self.symbols_cache)
            except Exception as e:
                self._log(f"symbol scan failed: {e}", "WARN")
                fb = list((self.cfg.get("symbols") or {}).get("fallback") or ["BTC_USDT", "ETH_USDT"])
                self.symbols_cache = [
                    {"symbol": s, "base": s.split("_")[0], "quote": s.split("_")[-1], "raw": {}, "quote_volume": 0.0}
                    for s in fb
                ]
                self.rotation.router.set_pairs(self.symbols_cache)

        # 2) FULL account reconciliation (authoritative)
        if not (self.rest.api_key and self.rest.api_secret):
            self.balance_source = "NO_CREDENTIALS"
            self.account_state = self.account_recon.mark_failed("NO_CREDENTIALS")
        else:
            try:
                st, body = self.rest.account()
                body = body if isinstance(body, dict) else {}
                # reject non-success API codes when present
                code = body.get("code")
                if code is not None and str(code) not in ("0", "200", ""):
                    self.account_state = self.account_recon.mark_failed(f"api_code={code}")
                    self.balance_source = "FETCH_FAILED"
                    self._log(f"account API code={code} msg={body.get('msg')}", "WARN")
                else:
                    self.account_state = self.account_recon.apply_rest_snapshot(body)
                    # mirror into legacy balances dict for rotation (free/locked floats)
                    self.balances = {}
                    for a, bal in self.account_state.assets.items():
                        if bal.total > 0:
                            self.balances[a] = {
                                "free": float(bal.free),
                                "locked": float(bal.locked),
                            }
                    self.balance_source = (
                        "LIVE_REST" if self.account_state.status == AccountStatus.VALID else self.account_state.status.value
                    )
                    self._log(
                        f"account recon status={self.account_state.status.value} "
                        f"n={len(self.account_state.assets)} holdings={len(self.account_state.holdings())} "
                        f"errors={self.account_state.errors[:3]}"
                    )
                    # push to risk
                    try:
                        usdt = self.account_state.assets.get("USDT")
                        avail = float(usdt.free) if usdt else 0.0
                        pos = {a: float(b.total) for a, b in self.account_state.assets.items() if b.total > 0}
                        self.risk.apply_authoritative_snapshot(avail, pos, daily_pnl=float(self.risk.daily_pnl or 0))
                    except Exception as e:
                        self._log(f"risk snapshot apply failed: {e}", "WARN")
            except Exception as e:
                self._log(f"account fetch failed: {e}", "WARN")
                self.balance_source = "FETCH_FAILED"
                self.account_state = self.account_recon.mark_failed(str(e))

        # 3) prices for valuation (no fabricated zeros for unknown)
        for asset in list(self.balances.keys()) + ["BTC", "ETH", "USDT"]:
            asset_u = str(asset).upper()
            if asset_u in ("USDT", "USDC", "BUSD", "USD"):
                self.prices[asset_u] = 1.0
                continue
            if float(self.prices.get(asset_u) or 0) > 0:
                continue
            try:
                tick = self.rest.ticker(f"{asset_u}_USDT")
                px = self._parse_ticker_price(tick if isinstance(tick, dict) else {})
                if px > 0:
                    self.prices[asset_u] = px
            except Exception:
                pass
        self.account_recon.apply_valuations(self.prices)

# seed synthetic candles for feature engine if empty (PAPER / offline)
        for sym_info in self.symbols_cache[:5]:
            sym = sym_info["symbol"]
            if self.candles.len(sym) < 30:
                # bootstrap flat series so features don't explode; real WS fills later
                base_px = 100.0
                for i in range(40):
                    self.candles.push(sym, time.time() - (40 - i) * 60, base_px, base_px, base_px, base_px, 1.0)

        # 4) features + expected returns
        feature_map: Dict[str, Any] = {}
        for asset in set(list(self.balances.keys()) + ["BTC", "ETH"]):
            # map asset to a USDT symbol if present
            sym = f"{asset}_USDT"
            vec, _ = self.features.compute(sym)
            feature_map[asset] = vec

        expected = self.decision.expected_returns_from_features(feature_map)
        # ensure USDT baseline
        expected.setdefault("USDT", 0.0)

        # Strategy richness: update expected returns from confluence before rotation
        import numpy as np
        for asset in list(expected.keys()):
            sym = f"{asset}_USDT"
            cl = self.candles.closes(sym)
            if len(cl) < 40:
                continue
            vol = self.candles.volumes(sym)
            rsi_val = 50.0
            vec = feature_map.get(asset)
            if vec is not None and len(vec) > 12:
                rsi_val = float(vec[12])
            sc = self.decision.evaluate_strategies(cl, volumes=vol if len(vol) else None, rsi=rsi_val)
            # blend feature heuristic with strategy expected return
            expected[asset] = 0.4 * float(expected.get(asset, 0.0)) + 0.6 * self.decision.strategies.expected_return_pct(sc)

        # 5) portfolio snapshot + rotation plan
        # Never invent exchange balances
        bal_for_snap = dict(self.balances)
        snap = self.rotation.build_snapshot(bal_for_snap, self.prices)
        plan = self.rotation.plan_cycle(snap, expected, symbols=self.symbols_cache)

        # 6) decision signal (primary symbol)
        primary = self.symbols_cache[0]["symbol"] if self.symbols_cache else "BTC_USDT"
        closes = self.candles.closes(primary)
        net = plan.selected.net_opportunity_pct if plan.selected else 0.0
        prob = 0.5
        if plan.selected and plan.selected.net_opportunity_pct > 0:
            prob = min(0.9, 0.5 + plan.selected.net_opportunity_pct / 10.0)
        closes_arr = closes if len(closes) else np.zeros(40, dtype=np.float32)
        vols_arr = self.candles.volumes(primary)
        # RSI from feature vector when available (index 12 in FEATURE_NAMES)
        rsi_val = 50.0
        btc_vec = feature_map.get("BTC")
        if btc_vec is not None and len(btc_vec) > 12:
            rsi_val = float(btc_vec[12])
            # stored RSI is 0-100 from engine
        signal = self.decision.make_signal(
            cycle_id=cycle_id,
            symbol=primary,
            closes=closes_arr,
            probability=prob,
            expected_return_pct=float(expected.get("BTC", 0)),
            net_opportunity_pct=net,
            model_hash="heuristic" if not self.model_valid else "loaded",
            volumes=vols_arr if len(vols_arr) else None,
            rsi=rsi_val,
            use_strategy_overlay=True,
        )
        # Feed strategy expected return into rotation map for primary asset
        if signal.strategy_composite != 0:
            expected["BTC"] = 0.5 * float(expected.get("BTC", 0)) + 0.5 * float(signal.expected_return_pct)

        # 7) risk + optional execution (PAPER only auto; LIVE needs model + no kill)
        order_result = None
        if (
            not plan.hold
            and plan.selected
            and signal.action in ("BUY", "SELL")
            and not self.risk.kill_switch
        ):
            if self.account_state.status != AccountStatus.VALID:
                self._log(f"LIVE blocked: account_state={self.account_state.status.value}", "WARN")
            elif self.exec_mgr.mode == "LIVE" and not self.model_valid and (self.cfg.get("ml") or {}).get("require_for_live", True):
                self._log("LIVE blocked: model invalid", "WARN")
            else:
                side = 0 if signal.action == "BUY" else 1
                qty = max(plan.selected.quantity, 0.001)
                px = self.prices.get(plan.selected.to_asset) or self.prices.get(plan.selected.from_asset) or 1.0
                # ensure filters exist (minimal)
                from decimal import Decimal

                from src.execution.filters import SymbolFilters
                sym = plan.selected.symbol or primary
                if sym not in self.exec_mgr.filters:
                    self.exec_mgr.filters[sym] = SymbolFilters(
                        symbol=sym,
                        qty_step=Decimal("0.0001"),
                        min_qty=Decimal("0.0001"),
                        max_qty=Decimal("1000"),
                        price_tick=Decimal("0.01"),
                        min_notional=Decimal("5"),
                    )
                order = self.exec_mgr.submit(
                    symbol=sym,
                    side=side,
                    order_type=1,
                    quantity=str(round(qty, 6)),
                    price=str(round(px, 2)) if px else "1",
                    available_balance=float(snap.available_usdt or 0.0),
                    reference_price=px or 1.0,
                )
                order_result = {
                    "client_id": order.client_id,
                    "state": order.state.value,
                    "symbol": order.symbol,
                }
                if order.state.value == "FILLED":
                    self.rotation.mark_rotated(plan.selected.from_asset, plan.selected.notional_usdt)
                    self.tg.notify_trade(
                        event="BUY_FILLED" if side == 0 else "SELL_FILLED",
                        symbol=order.symbol,
                        side="BUY" if side == 0 else "SELL",
                        quantity=order.quantity,
                        price=order.price or str(px),
                        order_id=str(order.exchange_order_id or ""),
                        client_id=order.client_id,
                        status="FILLED",
                        mode=self.exec_mgr.mode,
                    )

        latency = {"total_ms": (time.time() - t0) * 1000}
        cycle = {
            "cycle_id": cycle_id,
            "timestamp": time.time(),
            "symbols_scanned": len(self.symbols_cache),
            "portfolio_snapshot": {
                "total_value_usdt": snap.total_value_usdt,
                "available_usdt": snap.available_usdt,
                "balance_source": self.balance_source,
                "assets": {k: v.value_usdt for k, v in snap.balances.items()},
                "assets_detail": {
                    k: {"free": v.free, "locked": v.locked, "value_usdt": v.value_usdt}
                    for k, v in snap.balances.items()
                },
            },
            "signal": {
                "action": signal.action,
                "probability": signal.probability,
                "regime": signal.regime,
                "hash": signal.signal_hash,
            },
            "opportunity_ranking": [
                {
                    "from": o.from_asset,
                    "to": o.to_asset,
                    "net": o.net_opportunity_pct,
                    "route": o.route,
                }
                for o in plan.opportunities[:5]
            ],
            "route": {
                "selected": None
                if not plan.selected
                else {
                    "from": plan.selected.from_asset,
                    "to": plan.selected.to_asset,
                    "route": plan.selected.route,
                    "net": plan.selected.net_opportunity_pct,
                }
            },
            "hold": plan.hold,
            "reason": plan.reason,
            "risk_result": {"kill": self.risk.kill_switch, "circuit": self.risk.circuit_breaker},
            "order_result": order_result,
            "latency_ms": latency,
            "mode": self.exec_mgr.mode,
        }
        self.journal.write_cycle(cycle)
        self.last_cycle = cycle
        self._log(
            f"Cycle {cycle_id} hold={plan.hold} reason={plan.reason} "
            f"bal_src={getattr(self, 'balance_source', '?')} "
            f"n_bal={len(getattr(self, 'balances', {}) or {})}"
        )
        return cycle


    def _format_positions_for_gui(self, c: Dict[str, Any]) -> list:
        """Authoritative holdings for GUI."""
        st = getattr(self, "account_state", None)
        if st is None:
            return ["(account UNRECONCILED)"]
        if st.status.value in ("UNRECONCILED", "UNKNOWN", "INVALID", "RECONCILING"):
            return [f"(account {st.status.value} — not showing fabricated zeros)"]
        rows = []
        for a in st.holdings():
            vu = f"{a.valuation_usdt}" if a.valuation_usdt is not None else "VALUATION UNKNOWN"
            rows.append(f"{a.asset}: free={a.free} locked={a.locked} total={a.total} (~{vu} USDT)")
        if not rows:
            return [f"(no non-zero balances — status={st.status.value})"]
        return rows

