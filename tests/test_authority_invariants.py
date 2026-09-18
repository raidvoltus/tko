"""Hard invariants: strategy/ML/challenger/governor cannot call order endpoints."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="ignore")


def test_strategy_no_new_order():
    text = _read("src/decision/strategies.py")
    assert "new_order" not in text
    assert "ExecutionManager" not in text
    assert "RestClient" not in text


def test_governor_no_new_order():
    text = _read("src/decision/governor.py")
    assert "new_order" not in text
    assert "submit(" not in text or "submit" not in text


def test_champion_no_new_order():
    for rel in [
        "src/champion/manifest.py",
        "src/champion/registry.py",
        "src/champion/promotion.py",
        "src/champion/states.py",
    ]:
        text = _read(rel)
        assert "new_order" not in text
        assert "ExecutionManager" not in text


def test_ml_no_execution_import():
    for rel in ["src/ml/base.py", "src/ml/sklearn_model.py", "src/ml/xgboost_model.py"]:
        text = _read(rel)
        assert "ExecutionManager" not in text
        assert "new_order" not in text


def test_new_order_only_in_rest_and_manager():
    sites = []
    for p in (ROOT / "src").rglob("*.py"):
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if "new_order" in line:
                sites.append(f"{p.relative_to(ROOT)}:{i}")
    assert sites, "expected new_order sites"
    for s in sites:
        assert "rest.py" in s or "manager.py" in s, s
