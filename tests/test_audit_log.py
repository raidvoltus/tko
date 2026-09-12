"""Audit log unit tests."""

from pathlib import Path

from tko.audit.audit_log import AuditLog


def test_hash_chain(tmp_path: Path):
    log = AuditLog(tmp_path / "audit.jsonl")
    h1 = log.record("DECISION", symbol="BTC/IDR", reason="test1")
    h2 = log.record("ORDER_FILLED", symbol="BTC/IDR", quantity=1.0, price=2.0)
    assert h1 != h2
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    import json

    row2 = json.loads(lines[1])
    assert row2["prev_hash"] == h1
    assert row2["hash"] == h2
