"""IPC transport: Windows Named Pipe when available, else TCP loopback (tests/dev)."""

from __future__ import annotations

import hmac as _hmac
import json
import logging
import os
import socket
import struct
import threading
import time
from collections.abc import Callable

from tko.ipc.protocol import (
    MAX_FRAME,
    PIPE_NAME,
    default_ipc_port,
    ensure_token,
    load_token,
    pack,
    split_header,
    unpack,
)

logger = logging.getLogger(__name__)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


def recv_frame(sock: socket.socket) -> bytes:
    hdr = _recv_exact(sock, 8)
    blen, slen = split_header(hdr)
    if blen + slen > MAX_FRAME:
        raise ValueError("frame too big")
    return hdr + _recv_exact(sock, blen + slen)


class IPCServer:
    """Bounded daemon IPC server. Never blocks the trading loop."""

    def __init__(self, dispatcher: Callable[[str, dict], dict], *, token_path=None, host="127.0.0.1", port=None) -> None:
        self.dispatcher = dispatcher
        self._token = ensure_token(token_path)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._host = host
        self._port = port if port is not None else default_ipc_port()
        self._use_named_pipe = os.name == "nt" and os.environ.get("TKO_IPC_TCP", "") != "1"
        self.bound_endpoint = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="tko-ipc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if not self._use_named_pipe:
            try:
                socket.create_connection((self._host, self._port), timeout=0.5).close()
            except OSError:
                pass

    def _serve(self) -> None:
        if self._use_named_pipe:
            self._serve_named_pipe()
        else:
            self._serve_tcp()

    def _serve_tcp(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self._host, self._port))
        srv.listen(5)
        srv.settimeout(1.0)
        self.bound_endpoint = f"tcp://{self._host}:{self._port}"
        logger.info("event=ipc_listen %s", self.bound_endpoint)
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                continue
            try:
                self._handle_client(conn)
            except Exception as exc:  # noqa: BLE001
                logger.warning("event=ipc_client_error err=%s", exc)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
        try:
            srv.close()
        except OSError:
            pass

    def _serve_named_pipe(self) -> None:
        try:
            import win32file  # type: ignore
            import win32pipe  # type: ignore
        except ImportError:
            self._use_named_pipe = False
            self._serve_tcp()
            return
        self.bound_endpoint = PIPE_NAME
        while not self._stop.is_set():
            pipe = None
            try:
                pipe = win32pipe.CreateNamedPipe(
                    PIPE_NAME,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
                    win32pipe.PIPE_UNLIMITED_INSTANCES,
                    65536,
                    65536,
                    500,
                    None,
                )
                win32pipe.ConnectNamedPipe(pipe, None)
                self._handle_named_pipe_client(pipe)
            except Exception as exc:  # noqa: BLE001
                if not self._stop.is_set():
                    logger.warning("event=ipc_pipe_error err=%s", exc)
                    time.sleep(0.3)
            finally:
                if pipe is not None:
                    try:
                        win32pipe.DisconnectNamedPipe(pipe)
                    except Exception:  # noqa: BLE001,S110
                        pass
                    try:
                        win32file.CloseHandle(pipe)
                    except Exception:  # noqa: BLE001,S110
                        pass

    def _auth_hello(self, body: bytes) -> bool:
        try:
            msg = json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return False
        if not _hmac.compare_digest(str(msg.get("hello", "")).encode(), self._token):
            logger.warning("event=ipc_auth_failed")
            return False
        return True

    def _handle_client(self, conn: socket.socket) -> None:
        conn.settimeout(5.0)
        raw = recv_frame(conn)
        blen, _ = split_header(raw)
        if not self._auth_hello(raw[8 : 8 + blen]):
            return
        conn.sendall(pack({"type": "hello_ack", "ok": True}, self._token))
        while not self._stop.is_set():
            try:
                cmd_msg = unpack(recv_frame(conn), self._token)
            except Exception:  # noqa: BLE001
                return
            try:
                reply = self.dispatcher(cmd_msg.get("cmd", ""), cmd_msg.get("args") or {})
            except Exception as exc:  # noqa: BLE001
                reply = {"ok": False, "error": str(exc)}
            try:
                conn.sendall(pack(reply, self._token))
            except OSError:
                return

    def _handle_named_pipe_client(self, pipe) -> None:
        import win32file  # type: ignore

        def read_exact(n: int) -> bytes:
            data = b""
            while len(data) < n:
                _, chunk = win32file.ReadFile(pipe, n - len(data))
                if not chunk:
                    raise ConnectionError("pipe closed")
                data += chunk
            return data

        hdr = read_exact(8)
        blen, slen = split_header(hdr)
        raw = hdr + read_exact(blen + slen)
        if not self._auth_hello(raw[8 : 8 + blen]):
            return
        win32file.WriteFile(pipe, pack({"type": "hello_ack", "ok": True}, self._token))
        while not self._stop.is_set():
            try:
                hdr = read_exact(8)
                blen, slen = split_header(hdr)
                cmd_msg = unpack(hdr + read_exact(blen + slen), self._token)
            except Exception:  # noqa: BLE001
                return
            try:
                reply = self.dispatcher(cmd_msg.get("cmd", ""), cmd_msg.get("args") or {})
            except Exception as exc:  # noqa: BLE001
                reply = {"ok": False, "error": str(exc)}
            try:
                win32file.WriteFile(pipe, pack(reply, self._token))
            except Exception:  # noqa: BLE001
                return


class IPCClient:
    """Fail-closed client used by GUI. No exchange access."""

    CONNECT_TIMEOUT = 2.0

    def __init__(self, *, token_path=None, host="127.0.0.1", port=None) -> None:
        self._token_path = token_path
        self._token: bytes | None = None
        self._host = host
        self._port = port if port is not None else default_ipc_port()
        self._sock = None
        self._lock = threading.Lock()
        self.connected = False
        self.last_error: str | None = None
        self._use_named_pipe = os.name == "nt" and os.environ.get("TKO_IPC_TCP", "") != "1"

    def connect(self) -> bool:
        try:
            self._token = load_token(self._token_path) if self._token_path else load_token()
        except FileNotFoundError as exc:
            self.last_error = str(exc)
            self.connected = False
            return False
        return self._connect_pipe() if self._use_named_pipe else self._connect_tcp()

    def _connect_tcp(self) -> bool:
        deadline = time.time() + self.CONNECT_TIMEOUT
        while time.time() < deadline:
            try:
                sock = socket.create_connection((self._host, self._port), timeout=0.5)
                self._sock = sock
                return self._handshake_tcp(sock)
            except OSError:
                time.sleep(0.1)
        self.last_error = "core offline"
        self.connected = False
        return False

    def _connect_pipe(self) -> bool:
        try:
            import win32file  # type: ignore
            import win32pipe  # type: ignore
        except ImportError:
            self._use_named_pipe = False
            return self._connect_tcp()
        deadline = time.time() + self.CONNECT_TIMEOUT
        while time.time() < deadline:
            try:
                h = win32file.CreateFile(
                    PIPE_NAME,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None,
                )
                win32pipe.SetNamedPipeHandleState(h, win32pipe.PIPE_READMODE_MESSAGE, None, None)
                self._sock = h
                return self._handshake_pipe(h)
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        self.last_error = "core offline"
        self.connected = False
        return False

    def _handshake_tcp(self, sock: socket.socket) -> bool:
        assert self._token is not None
        hello = json.dumps({"hello": self._token.decode("ascii")}).encode()
        sock.sendall(struct.pack(">I", len(hello)) + struct.pack(">I", 0) + hello)
        ack = unpack(recv_frame(sock), self._token)
        if ack.get("type") != "hello_ack":
            self.last_error = "handshake failed"
            self.connected = False
            return False
        self.connected = True
        self.last_error = None
        return True

    def _handshake_pipe(self, handle) -> bool:
        import win32file  # type: ignore

        assert self._token is not None
        hello = json.dumps({"hello": self._token.decode("ascii")}).encode()
        win32file.WriteFile(handle, struct.pack(">I", len(hello)) + struct.pack(">I", 0) + hello)
        hdr, _ = win32file.ReadFile(handle, 8)
        blen, slen = split_header(hdr)
        body, _ = win32file.ReadFile(handle, blen + slen)
        ack = unpack(hdr + body, self._token)
        if ack.get("type") != "hello_ack":
            self.last_error = "handshake failed"
            self.connected = False
            return False
        self.connected = True
        self.last_error = None
        return True

    def request(self, cmd: str, args: dict | None = None) -> dict:
        if not self.connected and not self.connect():
            return {"ok": False, "error": self.last_error or "offline"}
        with self._lock:
            try:
                assert self._token is not None
                frame = pack({"cmd": cmd, "args": args or {}}, self._token)
                if self._use_named_pipe and not isinstance(self._sock, socket.socket):
                    import win32file  # type: ignore

                    win32file.WriteFile(self._sock, frame)
                    hdr, _ = win32file.ReadFile(self._sock, 8)
                    blen, slen = split_header(hdr)
                    body, _ = win32file.ReadFile(self._sock, blen + slen)
                    return unpack(hdr + body, self._token)
                assert isinstance(self._sock, socket.socket)
                self._sock.sendall(frame)
                return unpack(recv_frame(self._sock), self._token)
            except Exception as exc:  # noqa: BLE001
                self.connected = False
                self.last_error = f"core offline: {exc}"
                return {"ok": False, "error": self.last_error}

    def disconnect(self) -> None:
        try:
            if isinstance(self._sock, socket.socket):
                self._sock.close()
            elif self._sock is not None:
                try:
                    import win32file  # type: ignore

                    win32file.CloseHandle(self._sock)
                except Exception:  # noqa: BLE001,S110
                    pass
        except OSError:
            pass
        self._sock = None
        self.connected = False
