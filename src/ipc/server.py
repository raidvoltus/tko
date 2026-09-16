"""IPC server — runs inside TKO-Core only. GUI never holds trading authority."""
from __future__ import annotations

import json
import logging
import socket
import threading
from typing import Any, Callable, Dict, Optional

from .protocol import decode_message, encode_message, try_read_frame
from .token import ensure_ipc_token, load_ipc_token, TokenError

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17691


class IpcServer:
    def __init__(
        self,
        handler: Callable[[Dict[str, Any]], Dict[str, Any]],
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ):
        self.handler = handler
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._token: str = ""
        self.ready = False

    def start(self) -> None:
        ensure_ipc_token()
        self._token = load_ipc_token()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(8)
        self._sock.settimeout(1.0)
        self._stop.clear()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        self.ready = True
        logger.info("IPC server listening on %s:%s", self.host, self.port)

    def stop(self) -> None:
        self._stop.set()
        self.ready = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass

    def _accept_loop(self) -> None:
        assert self._sock
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if addr[0] not in ("127.0.0.1", "::1"):
                conn.close()
                continue
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn: socket.socket) -> None:
        conn.settimeout(30.0)
        buf = b""
        try:
            while not self._stop.is_set():
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while True:
                    frame, buf = try_read_frame(buf)
                    if frame is None:
                        break
                    try:
                        req = decode_message(self._token, frame)
                    except PermissionError:
                        resp = {"ok": False, "error": "auth_failed"}
                        conn.sendall(encode_message(self._token, resp))
                        return
                    except Exception as e:
                        resp = {"ok": False, "error": f"bad_message:{type(e).__name__}"}
                        try:
                            conn.sendall(encode_message(self._token, resp))
                        except Exception:
                            pass
                        continue
                    try:
                        result = self.handler(req)
                    except Exception as e:
                        logger.exception("IPC handler error")
                        result = {"ok": False, "error": str(e)}
                    if not isinstance(result, dict):
                        result = {"ok": True, "data": result}
                    conn.sendall(encode_message(self._token, result))
        except Exception as e:
            logger.debug("IPC client closed: %s", e)
        finally:
            try:
                conn.close()
            except Exception:
                pass
