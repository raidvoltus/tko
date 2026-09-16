"""IPC token lifecycle tests — no hardcoded token, atomic, fail-closed."""
import threading

import pytest

from src.ipc.token import (
    TokenError,
    ensure_ipc_token,
    load_ipc_token,
    token_path,
)


@pytest.fixture()
def ipc_dir(tmp_path, monkeypatch):
    d = tmp_path / "TKO"
    monkeypatch.setenv("TKO_IPC_DIR", str(d))
    return d


def test_create_and_persist(ipc_dir):
    p1 = ensure_ipc_token()
    assert p1.exists()
    t1 = load_ipc_token()
    assert len(t1) >= 32
    # second start must NOT regenerate
    p2 = ensure_ipc_token()
    t2 = load_ipc_token()
    assert t1 == t2
    assert p1 == p2


def test_missing_raises(ipc_dir):
    # ensure dir but no token
    ipc_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(TokenError):
        load_ipc_token()


def test_empty_token_fail(ipc_dir):
    ensure_ipc_token()
    path = token_path()
    path.write_bytes(b"")
    with pytest.raises(TokenError):
        load_ipc_token()


def test_corrupt_short_fail(ipc_dir):
    ensure_ipc_token()
    path = token_path()
    path.write_bytes(b"abc")
    with pytest.raises(TokenError):
        load_ipc_token()


def test_directory_instead_of_file(ipc_dir):
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    path.mkdir()
    with pytest.raises(TokenError):
        load_ipc_token()


def test_concurrent_create_one_token(ipc_dir):
    results = []

    def worker():
        ensure_ipc_token()
        results.append(load_ipc_token())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 1
    assert len(results[0]) >= 32


def test_protocol_auth():
    from src.ipc.protocol import decode_message, encode_message

    token = "a" * 64
    frame = encode_message(token, {"cmd": "ping"})
    body = decode_message(token, frame)
    assert body["cmd"] == "ping"
    with pytest.raises(PermissionError):
        decode_message("b" * 64, frame)
