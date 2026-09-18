"""Authority invariants: static scan + runtime capability boundary."""
from pathlib import Path

from src.champion.promotion import PromotionGate
from src.champion.registry import ChampionRegistry
from src.decision.ensemble import Ensemble
from src.decision.governor import Governor
from src.decision.plane import DecisionPlane
from src.decision.strategies import StrategyEngine
from src.security.authority import assert_no_execution_capability

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
    assert "ExecutionManager" not in text


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
    assert sites
    for s in sites:
        assert "rest.py" in s or "manager.py" in s, s


def test_runtime_strategy_has_no_execution_capability():
    eng = StrategyEngine()
    assert_no_execution_capability(eng, "StrategyEngine")


def test_runtime_governor_has_no_execution_capability():
    g = Governor()
    assert_no_execution_capability(g, "Governor")


def test_runtime_ensemble_has_no_execution_capability():
    e = Ensemble()
    assert_no_execution_capability(e, "Ensemble")


def test_runtime_decision_plane_has_no_execution_capability():
    dp = DecisionPlane()
    assert_no_execution_capability(dp, "DecisionPlane")
    assert_no_execution_capability(dp.strategies, "DecisionPlane.strategies")
    assert_no_execution_capability(dp.governor, "DecisionPlane.governor")
    assert_no_execution_capability(dp.ensemble, "DecisionPlane.ensemble")


def test_runtime_champion_registry_has_no_execution_capability():
    reg = ChampionRegistry()
    assert_no_execution_capability(reg, "ChampionRegistry")
    gate = PromotionGate()
    assert_no_execution_capability(gate, "PromotionGate")


def test_runtime_violation_detected():
    class Fake:
        pass
    class ExecutionManager:
        pass
    f = Fake()
    f.exec_mgr = ExecutionManager()
    try:
        assert_no_execution_capability(f, "Fake")
        raised = False
    except RuntimeError as e:
        raised = "AUTHORITY_VIOLATION" in str(e)
    assert raised
