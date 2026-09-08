"""Stage 2: Settings bounds, LIVE gate, fail-closed validation."""

from __future__ import annotations

import os

import pytest

from tko.core.config import Settings, SettingsError, load_settings


def test_default_settings_pass_live_gate():
    s = Settings()
    s.validate_for_live()
    assert s.live_mode is True
    assert s.exchange_id == "tokocrypto"


def test_live_mode_false_rejected(monkeypatch):
    monkeypatch.setenv("TKO_LIVE_MODE", "false")
    with pytest.raises((SettingsError, Exception)):
        load_settings()


def test_negative_max_daily_loss_rejected():
    with pytest.raises(Exception):
        Settings(max_daily_loss_pct=-1)


def test_zero_max_order_notional_rejected():
    with pytest.raises(Exception):
        Settings(max_order_notional=0)


def test_negative_max_daily_notional_rejected():
    with pytest.raises(Exception):
        Settings(max_daily_notional=-100)


def test_negative_max_open_positions_rejected():
    with pytest.raises(Exception):
        Settings(max_open_positions=-5)


def test_zero_loop_interval_rejected():
    with pytest.raises(Exception):
        Settings(loop_interval_sec=0)


def test_nan_rejected():
    with pytest.raises(Exception):
        Settings(max_order_notional=float("nan"))


def test_inf_rejected():
    with pytest.raises(Exception):
        Settings(max_daily_notional=float("inf"))


def test_ema_fast_ge_slow_rejected():
    with pytest.raises(Exception):
        Settings(ema_fast=21, ema_slow=9)


def test_rsi_oversold_ge_overbought_rejected():
    with pytest.raises(Exception):
        Settings(rsi_oversold=80, rsi_overbought=20)


def test_order_notional_gt_daily_rejected():
    with pytest.raises(Exception):
        Settings(max_order_notional=100, max_daily_notional=50)


def test_wrong_exchange_rejected():
    with pytest.raises(Exception):
        Settings(exchange_id="binance")


def test_validate_for_live_on_valid():
    Settings().validate_for_live()


def test_load_settings_returns_validated(monkeypatch):
    for k in list(os.environ):
        if k.startswith("TKO_"):
            monkeypatch.delenv(k, raising=False)
    s = load_settings()
    assert s.live_mode is True
