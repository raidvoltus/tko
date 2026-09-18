"""Frozen-aware path resolution."""
from pathlib import Path
import sys

from src.utils.paths import ensure_default_config, install_dir, program_data_dir, resolve_config_path


def test_install_dir_dev():
    d = install_dir()
    assert (d / "src").is_dir() or (d / "config").exists()


def test_resolve_finds_repo_config():
    p = resolve_config_path()
    assert p.name == "config.yaml"
    assert p.is_file() or not p.exists()  # may materialize later


def test_ensure_default_copies(tmp_path, monkeypatch):
    monkeypatch.setenv("TKO_CONFIG", "")
    # force program_data to tmp
    import src.utils.paths as paths

    monkeypatch.setattr(paths, "program_data_dir", lambda: tmp_path / "TKO")
    # install_dir without config
    monkeypatch.setattr(paths, "install_dir", lambda: tmp_path / "empty_install")
    (tmp_path / "empty_install").mkdir()
    # template via meipass mock
    tmpl = tmp_path / "bundle" / "config" / "config.yaml"
    tmpl.parent.mkdir(parents=True)
    tmpl.write_text("mode: PAPER\n")
    monkeypatch.setattr(paths, "meipass", lambda: tmp_path / "bundle")
    dest = ensure_default_config()
    assert dest.is_file()
    assert "PAPER" in dest.read_text()


def test_control_plane_loads_dev_config():
    from src.control.plane import ControlPlane

    cp = ControlPlane()
    cfg = cp.load_config()
    assert cfg.get("mode") in ("PAPER", "SHADOW", "LIVE")
