"""P0: WebSocket + user stream must be wired into TradingBot runtime (not orphan modules)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from tko.runtime.readiness import evaluate_readiness
from tko.marketdata.ws_public import PublicMarketStream, StreamHealth
from tko.marketdata.user_stream import UserStreamHealth


ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "src" / "tko" / "runtime" / "bot.py").read_text(encoding="utf-8")


def test_bot_source_wires_public_and_user_stream() -> None:
    assert "PublicMarketStream" in BOT
    assert "UserDataStream" in BOT
    assert "UserListenTokenClient" in BOT
    assert "def _start_streams" in BOT
    assert "def _stop_streams" in BOT
    assert "def _enforce_stream_gates" in BOT
    assert "def _wait_streams_healthy" in BOT
    assert "self._start_streams()" in BOT
    assert "self._stop_streams()" in BOT
    assert "self._enforce_stream_gates()" in BOT
    # Must be after recon path, before authorize_ready (ordering contract)
    start_idx = BOT.find("self._start_streams()")
    auth_idx = BOT.find("self.lifecycle.authorize_ready(")
    assert start_idx > 0 and auth_idx > 0 and start_idx < auth_idx


def test_readiness_ws_stale_blocks_trade() -> None:
    r = evaluate_readiness(
        process_alive=True,
        exchange_ok=True,
        market_data_fresh=False,
        user_stream_healthy=True,
        recon_ok=True,
        risk_healthy=True,
        kill_switch_off=True,
        require_ws_market=True,
        require_user_stream=True,
    )
    assert r.live is True
    assert r.ready is False
    assert "market_data_stale" in r.reasons
    assert r.trading_allowed is False


def test_readiness_user_stream_down_blocks_trade() -> None:
    r = evaluate_readiness(
        process_alive=True,
        exchange_ok=True,
        market_data_fresh=True,
        user_stream_healthy=False,
        recon_ok=True,
        risk_healthy=True,
        kill_switch_off=True,
        require_ws_market=True,
        require_user_stream=True,
    )
    assert r.ready is False
    assert "user_stream_unhealthy" in r.reasons


def test_readiness_all_green_allows_trade() -> None:
    r = evaluate_readiness(
        process_alive=True,
        exchange_ok=True,
        market_data_fresh=True,
        user_stream_healthy=True,
        recon_ok=True,
        risk_healthy=True,
        kill_switch_off=True,
        require_ws_market=True,
        require_user_stream=True,
    )
    assert r.ready is True
    assert r.trading_allowed is True
    assert r.reasons == ()


def test_readiness_liveness_without_readiness() -> None:
    r = evaluate_readiness(
        process_alive=True,
        exchange_ok=False,
        market_data_fresh=False,
        user_stream_healthy=False,
        recon_ok=False,
        risk_healthy=True,
        kill_switch_off=True,
        require_ws_market=True,
        require_user_stream=True,
    )
    assert r.live is True
    assert r.ready is False


def test_stream_health_fresh_and_stale() -> None:
    h = StreamHealth(connected=True, last_message_ts=0.0)
    assert h.is_fresh(max_age_sec=15.0) is False
    import time
    h.last_message_ts = time.time()
    assert h.is_fresh(max_age_sec=15.0) is True


def test_user_stream_health_token_and_terminated() -> None:
    import time
    h = UserStreamHealth(connected=True, terminated=False, token_expires_at_ms=int(time.time() * 1000) + 60_000)
    assert h.token_valid() is True
    assert h.is_healthy() is True
    h.terminated = True
    assert h.is_healthy() is False
    h.terminated = False
    h.token_expires_at_ms = int(time.time() * 1000) - 1
    assert h.token_valid() is False
    assert h.is_healthy() is False


def test_public_stream_subscribe_symbol_name() -> None:
    from tko.marketdata.ws_public import _symbol_stream_name
    assert _symbol_stream_name("BTC/USDT") == "btcusdt@ticker"
    assert _symbol_stream_name("ETH_IDR", "ticker") == "ethidr@ticker"


def test_settings_require_streams_default_on() -> None:
    from tko.core.config import Settings
    s = Settings()
    assert s.require_ws_market is True
    assert s.require_user_stream is True
    assert s.ws_startup_timeout_sec >= 5.0
