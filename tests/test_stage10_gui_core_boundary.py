"""Stage 10 — GUI/Core process boundary, IPC, no order path in GUI."""

from __future__ import annotations

import ast
import socket
import time
from pathlib import Path

import pytest

from tko.ipc.dispatcher import dispatch
from tko.ipc.protocol import ensure_token, pack, unpack
from tko.ipc.transport import IPCClient, IPCServer
from tko.runtime.lifecycle import LifecycleGovernor, LifecycleState


def test_pack_unpack_hmac_roundtrip(tmp_path: Path):
    tok = ensure_token(tmp_path / "ipc.token")
    frame = pack({"cmd": "status", "args": {}}, tok)
    msg = unpack(frame, tok)
    assert msg["cmd"] == "status"
    assert "ts" in msg


def test_bad_hmac_rejected(tmp_path: Path):
    tok = ensure_token(tmp_path / "ipc.token")
    frame = pack({"cmd": "status"}, tok)
    bad = ensure_token(tmp_path / "other.token")
    with pytest.raises(PermissionError):
        unpack(frame, bad)


def test_dispatcher_status_without_core():
    r = dispatch(None, "status", started_at=1.0, worker_id="abc")
    assert r["ok"] is True
    assert r["state"]["ready"] is False
    assert r["state"]["core_present"] is False


def test_dispatcher_cannot_force_ready_via_start():
    class Fake:
        def __init__(self):
            self.lifecycle = LifecycleGovernor()
            self.shutdowns = 0

        def request_shutdown(self):
            self.shutdowns += 1

        def status_dict(self):
            snap = self.lifecycle.snapshot()
            return {
                "lifecycle": snap.state.value,
                "ready": snap.trading_authorized,
                "trading_authorized": snap.trading_authorized,
                "process_alive": snap.process_alive,
                "position": {},
                "pnl": {},
                "recon_status": "PENDING",
                "risk_status": "OK",
                "kill_status": "ARMED",
                "heartbeat": time.time(),
                "started_at": 1.0,
                "worker_id": "w1",
                "pid": 1,
                "core_present": True,
            }

    core = Fake()
    dispatch(core, "start")
    assert core.lifecycle.state != LifecycleState.READY
    assert not core.lifecycle.trading_authorized
    r2 = dispatch(core, "stop")
    assert r2["ok"] is True
    assert core.shutdowns == 1


def test_dispatcher_kill_forces_kill_state():
    class Fake:
        def __init__(self):
            self.lifecycle = LifecycleGovernor()
            self.lifecycle.transition(LifecycleState.RECONCILING, reason="t")
            assert self.lifecycle.authorize_ready(
                recon_ok=True,
                kill_switch_clear=True,
                circuit_clear=True,
                daily_risk_ok=True,
                positions_ok=True,
                exchange_ok=True,
                reason="t",
            )
            self.shutdowns = 0

        def request_shutdown(self):
            self.shutdowns += 1

        def status_dict(self):
            return {"lifecycle": self.lifecycle.state.value, "ready": False}

    core = Fake()
    assert core.lifecycle.trading_authorized
    r = dispatch(core, "kill")
    assert r["ok"] is True
    assert core.lifecycle.state == LifecycleState.KILL
    assert not core.lifecycle.trading_authorized


def test_ipc_tcp_status_roundtrip(tmp_path: Path, monkeypatch):
    tok_path = tmp_path / "ipc.token"
    ensure_token(tok_path)
    monkeypatch.setenv("TKO_IPC_TCP", "1")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    class Fake:
        def __init__(self):
            self.lifecycle = LifecycleGovernor()

        def request_shutdown(self):
            pass

        def status_dict(self):
            snap = self.lifecycle.snapshot()
            return {
                "lifecycle": snap.state.value,
                "ready": False,
                "trading_authorized": False,
                "process_alive": True,
                "position": {},
                "pnl": {},
                "recon_status": "PENDING",
                "risk_status": "OK",
                "kill_status": "ARMED",
                "heartbeat": time.time(),
                "started_at": time.time(),
                "worker_id": "w-test",
                "pid": 42,
                "core_present": True,
            }

    core = Fake()

    def disp(cmd, args):
        return dispatch(core, cmd, args, started_at=1.0, worker_id="w-test")

    server = IPCServer(disp, token_path=tok_path, port=port)
    server.start()
    time.sleep(0.2)
    try:
        client = IPCClient(token_path=tok_path, port=port)
        assert client.connect()
        r = client.request("status")
        assert r["ok"] is True
        assert r["state"]["worker_id"] == "w-test"
        assert r["state"]["ready"] is False
        client.disconnect()
    finally:
        server.stop()


def test_gui_package_has_no_exchange_or_order_imports():
    root = Path(__file__).resolve().parents[1] / "src" / "tko" / "gui"
    forbidden = ("create_order", "TokocryptoClient", "api_secret", "ccxt", "keyring")
    for py in root.rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in src, f"{py} contains forbidden token {token}"
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("tko.exchange")
                    assert not alias.name.startswith("tko.execution")
                    assert alias.name != "ccxt"
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("tko.exchange")
                assert not node.module.startswith("tko.execution")
                assert node.module != "ccxt"


def test_gui_close_must_not_call_stop_in_on_close_source():
    app = Path(__file__).resolve().parents[1] / "src" / "tko" / "gui" / "app.py"
    src = app.read_text(encoding="utf-8")
    start = src.index("def _on_close")
    end = src.index("def run", start)
    body = src[start:end]
    assert 'request("stop")' not in body
    assert 'request("kill")' not in body
    assert "disconnect" in body


def test_packaging_specs_exist_and_split_core_gui():
    root = Path(__file__).resolve().parents[1]
    core = (root / "packaging" / "TKO-Core.spec").read_text(encoding="utf-8")
    gui = (root / "packaging" / "TKO-GUI.spec").read_text(encoding="utf-8")
    assert "TKO-Core" in core
    assert "tkinter" in core
    assert "TKO-GUI" in gui
    assert "tko.exchange" in gui
    assert "ccxt" in gui


def test_bot_status_dict_method_exists():
    from tko.runtime import bot as bot_mod

    src = Path(bot_mod.__file__).read_text(encoding="utf-8")
    assert "def status_dict" in src
    assert "def _start_ipc" in src
    assert "class TradingBot" in src
    assert src.strip() != "PLACEHOLDER"


def test_core_independent_of_gui_dispatcher_contract():
    r = dispatch(None, "status", worker_id="x")
    assert r["state"]["ready"] is False
