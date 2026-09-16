"""IPC layer — authenticated GUI↔Core. Core is sole trading authority."""
from .protocol import decode_message, encode_message, sign_payload, verify_payload
from .token import TokenError, ensure_ipc_token, load_ipc_token, token_path

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
