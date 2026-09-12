"""Stage 4 micro-closure: fail-closed LIVE POST gate + expanded submission surface audit."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

from tko.core.config import Settings
from tko.execution.engine import ExecutionEngine
from tko.risk.engine import RiskDecision


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


_SUSPECT_PATTERNS = [
    re.compile(r"\.create_order\s*\("),
    re.compile(r"\.createOrder\s*\("),
    re.compile(r'getattr\s*\([^,]+,\s*[\'"]create_order[\'"]'),
    re.compile(r'getattr\s*\([^,]+,\s*[\'"]createOrder[\'"]'),
    re.compile(r"requests\.post\s*\("),
    re.compile(r"httpx\.post\s*\("),
    re.compile(r"session\.post\s*\("),
    re.compile(r"""\.request\s*\(\s*['\"]POST['\"]""", re.IGNORECASE),
    re.compile(r'["\']/api/v\d+/order'),
    re.compile(r'["\']/api/v\d+/orders'),
    re.compile(r"private_post_order"),
    re.compile(r"create_market_buy_order"),
    re.compile(r"create_market_sell_order"),
]


def test_global_live_submission_surface_audit():
    root = Path(__file__).resolve().parents[1] / "src" / "tko"
    offenders: list[str] = []
    allow_files = {"tokocrypto.py", "engine.py"}

    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(root.parent.parent))
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pat in _SUSPECT_PATTERNS:
                if pat.search(line):
                    if path.name in allow_files:
                        continue
                    offenders.append(f"{rel}:{i}: {stripped[:120]}")

    eng = (root / "execution" / "engine.py").read_text(encoding="utf-8")
    assert "run_authorized_submit" in eng
    assert "lifecycle required for LIVE create_order" in eng
    stripped = eng.replace("return run(lambda: self.client.create_order(**kwargs))", "")
    assert "return self.client.create_order(**kwargs)" not in stripped
    assert eng.count("self.client.create_order") == 1
    assert not offenders, "Unauthorized LIVE submission surface:\n" + "\n".join(offenders)


def test_engine_create_order_count_is_exactly_one_gated_call():
    eng_path = Path(__file__).resolve().parents[1] / "src" / "tko" / "execution" / "engine.py"
    text = eng_path.read_text(encoding="utf-8")
    assert text.count("self.client.create_order") == 1
    assert "run_authorized_submit" in text
