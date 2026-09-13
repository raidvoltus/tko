"""IPC token first-run bootstrap, fail-closed validation, concurrency."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from tko.ipc.protocol import (
    IpcTokenError,
    ensure_token,
    load_token,
    pack,
    unpack,
)
from tko.ipc.transport import IPCClient, IPCServer


def test_missing_token_bootstrap_creates(tmp_path: Path):
    path = tmp_path / "ipc.token"
    assert not path.exists()
    tok = ensure_token(path)
    assert path.exists()
    assert len(tok) >= 32
    assert tok == path.read_bytes().strip()


def test_existing_token_stable_across_calls(tmp_path: Path):
    path = tmp_path / "ipc.token"
    a = ensure_token(path)
    b = ensure_token(path)
    c = load_token(path)
    assert a == b == c
    # restart simulation: content unchanged
    assert path.read_bytes().strip() == a


def test_corrupt_token_fail_closed(tmp_path: Path):
    path = tmp_path / "ipc.token"
    path.write_bytes(b"not-a-valid-token!!!")
    with pytest.raises(IpcTokenError):
        ensure_token(path)
    with pytest.raises(IpcTokenError):
        load_token(path)


def test_empty_token_fail_closed(tmp_path: Path):
    path = tmp_path / "ipc.token"
    path.write_bytes(b"")
    with pytest.raises(IpcTokenError):
        load_token(path)
    with pytest.raises(IpcTokenError):
        ensure_token(path)


def test_whitespace_token_fail_closed(tmp_path: Path):
    path = tmp_path / "ipc.token"
    path.write_bytes(b"   \n\t  ")
    with pytest.raises(IpcTokenError):
        load_token(path)


def test_concurrent_ensure_single_canonical(tmp_path: Path):
    path = tmp_path / "ipc.token"
    results: list[bytes] = []
    errors: list[BaseException] = []

    def worker() -> bytes:
        return ensure_token(path)

    with ThreadPoolExecutor(max_workers=16) as pool:
        futs = [pool.submit(worker) for _ in range(32)]
        for f in as_completed(futs):
            try:
                results.append(f.result())
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

    assert not errors
    assert results
    assert len(set(results)) == 1
    assert path.read_bytes().strip() == results[0]


def test_client_bootstrap_matches_server(tmp_path: Path):
    path = tmp_path / "ipc.token"
    # Server creates
    srv_tok = ensure_token(path)

    def echo(cmd: str, args: dict) -> dict:
        return {"ok": True, "cmd": cmd}

    port = 17991
    os.environ["TKO_IPC_TCP"] = "1"
    try:
        server = IPCServer(echo, token_path=path, host="127.0.0.1", port=port)
        server.start()
        import time

        time.sleep(0.15)
        client = IPCClient(token_path=path, host="127.0.0.1", port=port)
        assert client.connect() is True
        assert client._token == srv_tok
        resp = client.request("ping", {})
        assert resp.get("ok") is True
        client.disconnect()
        server.stop()
    finally:
        os.environ.pop("TKO_IPC_TCP", None)


def test_client_connect_without_prior_token_bootstraps(tmp_path: Path):
    path = tmp_path / "ipc.token"
    assert not path.exists()
    os.environ["TKO_IPC_TCP"] = "1"
    try:
        # Client-side ensure_token creates material even if Core not up yet
        client = IPCClient(token_path=path, host="127.0.0.1", port=17992)
        # connect will bootstrap token then fail on core offline — token must exist
        ok = client.connect()
        assert path.exists()
        assert load_token(path)
        # core offline expected
        assert ok is False
        assert client.last_error
    finally:
        os.environ.pop("TKO_IPC_TCP", None)


def test_hmac_roundtrip_uses_token(tmp_path: Path):
    path = tmp_path / "ipc.token"
    tok = ensure_token(path)
    frame = pack({"cmd": "status", "args": {}}, tok)
    msg = unpack(frame, tok)
    assert msg["cmd"] == "status"


def test_token_not_logged_on_init(tmp_path: Path, caplog):
    import logging

    path = tmp_path / "ipc.token"
    with caplog.at_level(logging.INFO, logger="tko.ipc.protocol"):
        tok = ensure_token(path)
    joined = " ".join(r.message for r in caplog.records)
    assert tok.decode("ascii") not in joined
    assert "ipc_token_initialized" in joined or path.exists()


def test_load_token_missing_message(tmp_path: Path):
    path = tmp_path / "nope.token"
    with pytest.raises(IpcTokenError) as ei:
        load_token(path)
    assert "missing or invalid" in str(ei.value).lower() or "missing" in str(ei.value).lower()
