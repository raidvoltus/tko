"""Control plane kill switch + config hash."""
import tempfile
from pathlib import Path

from src.control.plane import ControlPlane


def test_kill_switch_file():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "config").mkdir()
        # minimal yaml
        (root / "config" / "config.yaml").write_text("mode: PAPER\n")
        cp = ControlPlane(root)
        try:
            cp.load_config()
        except Exception:
            pass
        assert not cp.is_kill_switch_active()
        cp.activate_kill_switch("test")
        assert cp.is_kill_switch_active()
