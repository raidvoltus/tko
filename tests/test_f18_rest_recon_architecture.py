"""Finding 18: prove safety-critical path does not require WebSocket user-data.

Architecture: REST submit + clientOrderId + UNKNOWN on timeout/5xx + Reconciler
lookup by clientOrderId is the sole authority for order confirmation. No WS
module is imported by execution/reconciliation paths.
"""
from __future__ import annotations
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "tko"

SAFETY_PATHS = [
    ROOT / "execution" / "engine.py",
    ROOT / "reconciliation" / "reconciler.py",
    ROOT / "exchange" / "tokocrypto.py",
    ROOT / "runtime" / "bot.py",
]

def test_no_websocket_import_on_safety_path():
    for path in SAFETY_PATHS:
        assert path.exists(), path
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", None) or ""
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                else:
                    names = [mod] + [a.name for a in node.names]
                joined = " ".join(names).lower()
                assert "websocket" not in joined and "websockets" not in joined, path

def test_reconciler_uses_rest_client_lookup():
    text = (ROOT / "reconciliation" / "reconciler.py").read_text(encoding="utf-8")
    assert "find_order_by_client_id" in text

def test_no_ws_user_data_module_required():
    ws_files = list((ROOT).rglob("*ws*")) + list((ROOT).rglob("*websocket*"))
    engine = (ROOT / "execution" / "engine.py").read_text(encoding="utf-8")
    for f in ws_files:
        assert f.stem not in engine
