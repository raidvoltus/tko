"""Secure credential persistence — no plaintext secrets in config files.

Windows: DPAPI via ctypes CryptProtectData / CryptUnprotectData.
Other OS: machine-bound Fernet blob under ProgramData/TKO/secrets (not plaintext).
If protection fails: CREDENTIALS_UNAVAILABLE (fail closed).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

NAMESPACE_TOKO = "tokocrypto/api"
NAMESPACE_TG = "tokocrypto/telegram"


def _secrets_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA") or os.environ.get("LOCALAPPDATA") or str(Path.home())
        d = Path(base) / "TKO" / "secrets"
    else:
        d = Path.home() / ".tko" / "secrets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dpapi_protect(data: bytes) -> Optional[bytes]:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        blob_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
        blob_out = DATA_BLOB()
        if not crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return None
        try:
            protected = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            return protected
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception as e:
        logger.warning("DPAPI protect unavailable: %s", type(e).__name__)
        return None


def _dpapi_unprotect(data: bytes) -> Optional[bytes]:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        blob_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
        blob_out = DATA_BLOB()
        if not crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception as e:
        logger.warning("DPAPI unprotect unavailable: %s", type(e).__name__)
        return None


def _fernet_key() -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    parts = [
        os.environ.get("COMPUTERNAME", ""),
        os.environ.get("USERNAME", ""),
        str(Path.home()),
        "tko-credential-v1",
    ]
    salt_path = _secrets_dir() / "cred.salt"
    if salt_path.exists():
        salt = salt_path.read_bytes()
    else:
        salt = os.urandom(16)
        salt_path.write_bytes(salt)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=120_000)
    return base64.urlsafe_b64encode(kdf.derive("|".join(parts).encode()))


def _protect(data: bytes) -> tuple[bytes, str]:
    """Returns (blob, method). method is DPAPI or FERNET."""
    p = _dpapi_protect(data)
    if p is not None:
        return p, "DPAPI"
    try:
        from cryptography.fernet import Fernet

        f = Fernet(_fernet_key())
        return f.encrypt(data), "FERNET"
    except Exception as e:
        raise RuntimeError(f"CREDENTIALS_UNAVAILABLE: {type(e).__name__}") from e


def _unprotect(blob: bytes, method: str) -> bytes:
    if method == "DPAPI":
        out = _dpapi_unprotect(blob)
        if out is None:
            raise RuntimeError("CREDENTIALS_UNAVAILABLE: DPAPI_UNPROTECT_FAILED")
        return out
    if method == "FERNET":
        from cryptography.fernet import Fernet

        return Fernet(_fernet_key()).decrypt(blob)
    raise RuntimeError("CREDENTIALS_UNAVAILABLE: UNKNOWN_METHOD")


class CredentialStore:
    """Namespace-scoped secure secret store."""

    def __init__(self, namespace: str = NAMESPACE_TOKO):
        self.namespace = namespace
        safe = namespace.replace("/", "_")
        self.path = _secrets_dir() / f"{safe}.bin"
        self.meta_path = _secrets_dir() / f"{safe}.meta.json"

    def exists(self) -> bool:
        return self.path.is_file() and self.meta_path.is_file()

    def save(self, secrets: Dict[str, str]) -> None:
        # never log values
        payload = json.dumps(secrets, separators=(",", ":")).encode("utf-8")
        blob, method = _protect(payload)
        self.path.write_bytes(blob)
        if os.name != "nt":
            try:
                os.chmod(self.path, 0o600)
            except Exception:
                pass
        meta = {
            "method": method,
            "keys": sorted(secrets.keys()),
            "fingerprint": hashlib.sha256(blob).hexdigest()[:16],
        }
        self.meta_path.write_text(json.dumps(meta), encoding="utf-8")

    def load(self) -> Dict[str, str]:
        if not self.exists():
            return {}
        try:
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            method = str(meta.get("method", "FERNET"))
            raw = _unprotect(self.path.read_bytes(), method)
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                return {}
            return {str(k): str(v) for k, v in data.items() if v}
        except Exception as e:
            logger.error("Credential load failed: %s", type(e).__name__)
            return {}

    def delete(self) -> None:
        for p in (self.path, self.meta_path):
            if p.exists():
                p.unlink()

    def validate(self) -> str:
        """Return status string: VALID | MISSING | CORRUPT | CREDENTIALS_UNAVAILABLE."""
        if not self.exists():
            return "MISSING"
        data = self.load()
        if not data:
            return "CORRUPT"
        return "VALID"


def save_toko_credentials(api_key: str, api_secret: str) -> None:
    from src.security.redact import get_redactor

    get_redactor().register(api_key, api_secret)
    CredentialStore(NAMESPACE_TOKO).save({"api_key": api_key, "api_secret": api_secret})


def load_toko_credentials() -> Dict[str, str]:
    data = CredentialStore(NAMESPACE_TOKO).load()
    from src.security.redact import get_redactor

    get_redactor().register(data.get("api_key"), data.get("api_secret"))
    return data


def save_telegram_credentials(bot_token: str, chat_id: str) -> None:
    from src.security.redact import get_redactor

    get_redactor().register(bot_token)
    CredentialStore(NAMESPACE_TG).save({"bot_token": bot_token, "chat_id": chat_id})


def load_telegram_credentials() -> Dict[str, str]:
    data = CredentialStore(NAMESPACE_TG).load()
    from src.security.redact import get_redactor

    get_redactor().register(data.get("bot_token"))
    return data
