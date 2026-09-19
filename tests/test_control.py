"""Control plane kill switch + config hash."""
import tempfile
from pathlib import Path

from src.control.plane import ControlPlane


def test_kill_switch_file():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "config").mkdir()
        (root / "config" / "config.yaml").write_text("mode: LIVE\n")
        cp = ControlPlane(root)
        cp.load_config()
        assert not cp.is_kill_switch_active()
        cp.activate_kill_switch("test")
        assert cp.is_kill_switch_active()


def test_rejects_paper_mode():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "config").mkdir()
        (root / "config" / "config.yaml").write_text("mode: PAPER\n")
        cp = ControlPlane(root)
        try:
            cp.load_config()
            raised = False
        except ValueError as e:
            raised = "LIVE" in str(e) or "INVALID" in str(e)
        assert raised
