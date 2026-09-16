"""IPC layer — authenticated GUI↔Core. Core is sole trading authority."""
from .token import ensure_ipc_token, load_ipc_token, token_path, TokenError
from .protocol import encode_message, decode_message, sign_payload, verify_payload

__all__ = [
    "ensure_ipc_token",
    "load_ipc_token",
    "token_path",
    "TokenError",
    "encode_message",
    "decode_message",
    "sign_payload",
    "verify_payload",
]
