"""IPC server/client integration on localhost."""
import time

import pytest

from src.ipc.client import IpcClient
from src.ipc.server import IpcServer


@pytest.fixture()
def ipc_dir(tmp_path, monkeypatch):
    d = tmp_path / "TKO"
    monkeypatch.setenv("TKO_IPC_DIR", str(d))
    return d


def test_ping_auth(ipc_dir):
    def handler(req):
        if req.get("cmd") == "ping":
            return {"ok": True, "pong": True}
        return {"ok": False, "error": "unknown"}

    # unique port to avoid clash
    port = 17695
    srv = IpcServer(handler, port=port)
    srv.start()
    time.sleep(0.2)
    try:
        cli = IpcClient(port=port)
        cli.connect_auth()
        r = cli.request({"cmd": "ping"})
        assert r.get("ok") is True
        assert r.get("pong") is True
        # order commands rejected by handler policy in core; here generic
        r2 = cli.request({"cmd": "unknown"})
        assert r2.get("ok") is False
    finally:
        srv.stop()
