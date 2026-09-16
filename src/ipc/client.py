"""IPC client — used by GUI only. Cannot submit orders to exchange."""
from __future__ import annotations

import logging
import socket
from typing import Any, Dict

from .protocol import decode_message, encode_message, try_read_frame
from .server import DEFAULT_HOST, DEFAULT_PORT
from .token import ensure_ipc_token, load_ipc_token

logger = logging.getLogger(__name__)


class IpcClient:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._token: str = ""

    def connect_auth(self) -> None:
        ensure_ipc_token()  # first-run: create if Core not yet started
        self._token = load_ipc_token()

    def request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._token:
            self.connect_auth()
        data = encode_message(self._token, payload)
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.sendall(data)
            buf = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    raise ConnectionError("IPC connection closed")
                buf += chunk
                frame, rest = try_read_frame(buf)
                if frame is not None:
                    return decode_message(self._token, frame)
