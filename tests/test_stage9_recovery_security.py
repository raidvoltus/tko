"""Stage 9 — Recovery, security, packaging integrity regression."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from tko.audit.audit_log import AuditLog
from tko.core.redact import is_sensitive_key, redact_mapping, redact_text
from tko.runtime.backup import (
    backup_state,
    extract_file_from_backup,
    is_structurally_valid_intents,
    is_structurally_valid_positions,
    recover_json_state,
    rotate_backups,
    validate_json_file,
)
from tko.runtime.lifecycle import LifecycleGovernor, LifecycleState


def test_redact_sensitive_keys():
    assert is_sensitive_key("api_key")
    assert is_sensitive_key("API_SECRET")
    assert is_sensitive_key("bot_token")
    assert not is_sensitive_key("symbol")


def test_redact_mapping_and_text():
    d = redact_mapping({"api_key": "supersecret123", "symbol": "BTC/IDR", "note": "ok"})
    assert d["api_key"] == "***REDACTED***"
    assert d["symbol"] == "BTC/IDR"
    text = redact_text("api_key=supersecret123 failed")
    assert "supersecret123" not in text
    assert "REDACTED" in text


def test_audit_log_redacts_secrets(tmp_path: Path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record(
        "ERROR",
        reason="auth failed api_key=ABCDEFGHijklmnop",
        extra={"api_secret": "should_not_leak", "symbol": "BTC/IDR"},
    )
    raw = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "should_not_leak" not in raw
    assert "ABCDEFGHijklmnop" not in raw
    assert "REDACTED" in raw
    assert "BTC/IDR" in raw


def test_validate_json_missing_empty_invalid(tmp_path: Path):
    p = tmp_path / "x.json"
    ok, reason, _ = validate_json_file(p)
    assert not ok and reason == "missing"
    p.write_text("   \n", encoding="utf-8")
    ok, reason, _ = validate_json_file(p)
    assert not ok and reason == "empty"
    p.write_text("{truncated", encoding="utf-8")
    ok, reason, _ = validate_json_file(p)
    assert not ok and "invalid_json" in reason
    p.write_text('{"a":1}', encoding="utf-8")
    ok, reason, data = validate_json_file(p)
    assert ok and data == {"a": 1}


def test_structural_positions_intents():
    assert is_structurally_valid_positions({"positions": []})
    assert not is_structurally_valid_positions([1, 2, 3])
    assert is_structurally_valid_intents({"intents": []})
    assert not is_structurally_valid_intents({"intents": "bad"})


def test_backup_excludes_credentials_and_lock(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "positions.json").write_text('{"positions":[]}', encoding="utf-8")
    (state / "tko.lock").write_text("123", encoding="utf-8")
    (state / "credentials.json").write_text('{"api_key":"x"}', encoding="utf-8")
    backups = tmp_path / "backups"
    z = backup_state(state, backups, keep=5)
    assert z.exists()
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
    assert "positions.json" in names
    assert "tko.lock" not in names
    assert not any("credential" in n.lower() for n in names)


def test_backup_rotation_bounded(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "a.json").write_text("{}", encoding="utf-8")
    backups = tmp_path / "backups"
    for _ in range(5):
        backup_state(state, backups, keep=3)
    zips = list(backups.glob("state_*.zip"))
    assert len(zips) <= 3


def test_rotate_backups_explicit(tmp_path: Path):
    backups = tmp_path / "backups"
    backups.mkdir()
    for i in range(5):
        p = backups / f"state_2020010{i}_000000.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("x.json", "{}")
    removed = rotate_backups(backups, keep=2)
    assert removed == 3
    assert len(list(backups.glob("state_*.zip"))) == 2


def test_recover_from_valid_backup(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    primary = state / "positions.json"
    primary.write_text('{"positions":[{"symbol":"BTC/IDR","amount":1}]}', encoding="utf-8")
    backups = tmp_path / "backups"
    backup_state(state, backups, keep=5)
    primary.write_text("{truncated", encoding="utf-8")
    source, data = recover_json_state(
        primary, backups, arcname="positions.json", structural_ok=is_structurally_valid_positions
    )
    assert source == "backup"
    assert data is not None
    assert is_structurally_valid_positions(data)


def test_recover_fails_when_all_corrupt(tmp_path: Path):
    primary = tmp_path / "positions.json"
    primary.write_text("{bad", encoding="utf-8")
    backups = tmp_path / "backups"
    backups.mkdir()
    bad_zip = backups / "state_20200101_000000.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("positions.json", "{also_bad")
    source, data = recover_json_state(
        primary, backups, arcname="positions.json", structural_ok=is_structurally_valid_positions
    )
    assert source == "none"
    assert data is None


def test_extract_rejects_corrupt_backup_json(tmp_path: Path):
    z = tmp_path / "b.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("positions.json", "{truncated")
    dest = tmp_path / "out.json"
    ok, msg = extract_file_from_backup(z, "positions.json", dest)
    assert not ok


def test_recovery_cannot_bypass_authorize_ready():
    lc = LifecycleGovernor()
    lc.force(LifecycleState.STARTING, reason="post_recovery")
    assert not lc.trading_authorized
    lc.transition(LifecycleState.RECONCILING, reason="post_recovery_recon")
    lc.force(LifecycleState.READY, reason="hack")
    assert lc.state == LifecycleState.RECONCILING
    assert not lc.trading_authorized
    assert not lc.authorize_ready(
        recon_ok=False,
        kill_switch_clear=True,
        circuit_clear=True,
        daily_risk_ok=True,
        positions_ok=True,
        exchange_ok=True,
        reason="partial",
    )


def test_recovery_cannot_bypass_risk_reservation(tmp_path: Path):
    from tko.core.config import Settings
    from tko.risk.engine import RiskEngine
    from tko.risk.pnl_tracker import DailyPnLTracker

    s = Settings(max_daily_notional=1000, max_order_notional=1000, min_quote_balance=1)
    risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "p.jsonl", timezone_name="UTC"))
    ok, _ = risk.try_reserve_notional(float("nan"), reservation_id="x")
    assert not ok
    d = risk.evaluate_entry(
        symbol="BTC/IDR",
        quote_free=1e7,
        last_price=1000,
        signal=None,
    )
    assert not d.approved


def test_production_modules_not_placeholder():
    import tko.runtime.bot as bot_mod
    import tko.runtime.lifecycle as lc_mod
    import tko.runtime.backup as bak_mod

    for mod in (bot_mod, lc_mod, bak_mod):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert src.strip() != "PLACEHOLDER"
        assert "class " in src or "def " in src
    assert "class TradingBot" in Path(bot_mod.__file__).read_text(encoding="utf-8")
    assert "authorize_ready" in Path(lc_mod.__file__).read_text(encoding="utf-8")


def test_packaging_spec_has_critical_modules():
    root = Path(__file__).resolve().parents[1]
    spec = (root / "packaging" / "TKO.spec").read_text(encoding="utf-8")
    for name in (
        "tko.runtime.bot",
        "tko.runtime.entrypoint",
        "tko.runtime.lifecycle",
        "tko.runtime.backup",
        "tko.core.redact",
        "tko.risk.engine",
        "tko.execution.engine",
    ):
        assert name in spec


def test_windows_service_module_importable_and_no_order_path():
    import tko.runtime.windows_service as ws

    src = Path(ws.__file__).read_text(encoding="utf-8")
    assert "create_order" not in src
    assert "schtasks" in src
    if hasattr(ws, "install_scheduled_task"):
        res = ws.install_scheduled_task()
        assert hasattr(res, "ok")
