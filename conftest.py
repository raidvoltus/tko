"""TKO test bootstrap: path, markers, ProgramData isolation."""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONPATH", str(ROOT))

IS_WINDOWS = platform.system() == "Windows"


def pytest_collection_modifyitems(config, items):
    try:
        import sklearn  # noqa: F401

        has_sklearn = True
    except ImportError:
        has_sklearn = False

    skip_win = pytest.mark.skip(reason="Windows-only test")
    skip_ml = pytest.mark.skip(reason="scikit-learn not installed")

    for item in items:
        if "windows" in item.keywords and not IS_WINDOWS:
            item.add_marker(skip_win)
        if "ml" in item.keywords and not has_sklearn:
            item.add_marker(skip_ml)


@pytest.fixture(scope="session")
def sklearn_mod():
    return pytest.importorskip("sklearn", reason="ml tests require scikit-learn")


@pytest.fixture(scope="session")
def win32_only():
    if not IS_WINDOWS:
        pytest.skip("Windows-only")
    return True


@pytest.fixture(autouse=True)
def _isolate_ipc_and_state(monkeypatch, tmp_path):
    """Do not touch real ProgramData / IPC dir during tests."""
    fake = tmp_path / "ProgramData" / "TKO"
    fake.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ProgramData", str(tmp_path / "ProgramData"))
    monkeypatch.setenv("TKO_IPC_DIR", str(fake))
    monkeypatch.setenv("TKO_STATE_DIR", str(fake))
    yield
