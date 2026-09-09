"""Stage 4 micro-closure: fail-closed LIVE POST gate + global call-site audit."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from tko.core.config import Settings
from tko.risk.engine import RiskDecision
from tko.execution.engine import ExecutionEngine


def test_live_create_order_requires_lifecycle_fail_closed(tmp_path: Path):
    client = MagicMock()
    client.circuit_open = False
    client.validate_symbol_ready.return_value = (True, "ok")
    constraints = MagicMock()
    constraints.validate_notional.return_value = (True, "ok")
    constraints.normalize_quantity.side_effect = lambda q, market_order=False: q
    constraints.validate_quantity.return_value = (True, "ok")
    client.get_constraints.return_value = constraints

    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=None)
    dec = RiskDecision(True, "approved", size_quote=10000, size_base=0.01)
    assert eng.buy("BTC/IDR", "BTC", "IDR", dec, last_price=1000.0) is None
    assert client.create_order.call_count == 0


def test_live_create_order_requires_run_authorized_submit_api(tmp_path: Path):
    client = MagicMock()
    client.circuit_open = False
    client.validate_symbol_ready.return_value = (True, "ok")
    constraints = MagicMock()
    constraints.validate_notional.return_value = (True, "ok")
    constraints.normalize_quantity.side_effect = lambda q, market_order=False: q
    constraints.validate_quantity.return_value = (True, "ok")
    client.get_constraints.return_value = constraints

    class FakeLC:
        trading_authorized = True
        state = type("S", (), {"value": "READY"})()

    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=FakeLC())
    dec = RiskDecision(True, "approved", size_quote=10000, size_base=0.01)
    assert eng.buy("BTC/IDR", "BTC", "IDR", dec, last_price=1000.0) is None
    assert client.create_order.call_count == 0


def test_global_live_post_paths_must_use_authorization_gate():
    """Production src must not call client.create_order outside the gate."""
    root = Path(__file__).resolve().parents[1] / "src" / "tko"
    offenders: list[str] = []

    for path in root.rglob("*.py"):
        if path.name in ("tokocrypto.py", "engine.py"):
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ".create_order(" in line and not line.strip().startswith("#"):
                offenders.append(f"{path.relative_to(root.parent.parent)}:{i}: {line.strip()}")

    eng = (root / "execution" / "engine.py").read_text(encoding="utf-8")
    assert "run_authorized_submit" in eng
    assert "lifecycle required for LIVE create_order" in eng
    stripped = eng.replace("return run(lambda: self.client.create_order(**kwargs))", "")
    assert "return self.client.create_order(**kwargs)" not in stripped
    assert "if lc is None:\n            return self.client.create_order" not in eng

    assert not offenders, "Unauthorized LIVE create_order call sites:\n" + "\n".join(offenders)
