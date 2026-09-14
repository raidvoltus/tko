"""P0 readiness and stream health — fail-closed without live network."""

import time

from tko.execution.intent import IntentStore
from tko.marketdata.user_stream import UserStreamHealth
from tko.marketdata.ws_public import StreamHealth, _symbol_stream_name
from tko.runtime.readiness import evaluate_readiness


def test_stream_health_stale():
    h = StreamHealth(connected=True, last_message_ts=time.time() - 60)
    assert h.is_fresh(max_age_sec=15) is False


def test_stream_health_fresh():
    h = StreamHealth(connected=True, last_message_ts=time.time())
    assert h.is_fresh(max_age_sec=15) is True


def test_symbol_stream_name():
    assert _symbol_stream_name("BTC/USDT") == "btcusdt@ticker"


def test_user_stream_token_validity():
    h = UserStreamHealth(connected=True, token_expires_at_ms=int((time.time() + 3600) * 1000))
    assert h.token_valid() is True
    h2 = UserStreamHealth(connected=True, token_expires_at_ms=int((time.time() - 10) * 1000))
    assert h2.token_valid() is False


def test_readiness_blocks_when_recon_fails():
    r = evaluate_readiness(
        process_alive=True,
        exchange_ok=True,
        market_data_fresh=True,
        user_stream_healthy=True,
        recon_ok=False,
        risk_healthy=True,
        kill_switch_off=True,
    )
    assert r.ready is False
    assert "recon_not_ok" in r.reasons
    assert r.trading_allowed is False


def test_readiness_optional_streams_not_required_by_default():
    r = evaluate_readiness(
        process_alive=True,
        exchange_ok=True,
        market_data_fresh=False,
        user_stream_healthy=False,
        recon_ok=True,
        risk_healthy=True,
        kill_switch_off=True,
        require_ws_market=False,
        require_user_stream=False,
    )
    assert r.ready is True


def test_readiness_requires_streams_when_flagged():
    r = evaluate_readiness(
        process_alive=True,
        exchange_ok=True,
        market_data_fresh=False,
        user_stream_healthy=False,
        recon_ok=True,
        risk_healthy=True,
        kill_switch_off=True,
        require_ws_market=True,
        require_user_stream=True,
    )
    assert r.ready is False
    assert "market_data_stale" in r.reasons
    assert "user_stream_unhealthy" in r.reasons


def test_intent_id_index(tmp_path):
    store = IntentStore(tmp_path / "intents.json")
    intent = store.create(symbol="BTC/USDT", side="buy", quote_amount=100.0)
    assert intent.intent_id
    assert store.by_intent_id(intent.intent_id) is intent
    assert store.by_client_id(intent.client_order_id) is intent
