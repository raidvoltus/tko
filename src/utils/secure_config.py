"""Encrypted local configuration compatible with Windows 7."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# Machine-local salt file (not secret by itself)
SALT_FILE = "config.salt"
CONFIG_FILE = "config.enc"
DEFAULT_CONFIG_DIR = Path.home() / ".tokocrypto_bot"


def _derive_key(password: bytes, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password))


def _machine_fingerprint() -> bytes:
    """Best-effort machine id for key material (Win7 compatible)."""
    parts = [
        os.environ.get("COMPUTERNAME", ""),
        os.environ.get("USERNAME", ""),
        str(Path.home()),
    ]
    return hashlib.sha256("|".join(parts).encode()).digest()


class SecureConfig:
    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Any] = {}
        self._fernet: Optional[Fernet] = None

    def _ensure_key(self) -> Fernet:
        if self._fernet:
            return self._fernet
        salt_path = self.config_dir / SALT_FILE
        if salt_path.exists():
            salt = salt_path.read_bytes()
        else:
            salt = os.urandom(16)
            salt_path.write_bytes(salt)
        key = _derive_key(_machine_fingerprint(), salt)
        self._fernet = Fernet(key)
        return self._fernet

    def load(self) -> Dict[str, Any]:
        path = self.config_dir / CONFIG_FILE
        if not path.exists():
            self._data = {}
            return self._data
        try:
            f = self._ensure_key()
            raw = path.read_bytes()
            plain = f.decrypt(raw)
            self._data = json.loads(plain.decode("utf-8"))
            return self._data
        except Exception as e:
            logger.error("Failed to decrypt config: %s", e)
            self._data = {}
            return self._data

    def save(self, data: Optional[Dict[str, Any]] = None) -> None:
        if data is not None:
            self._data = data
        f = self._ensure_key()
        plain = json.dumps(self._data, indent=2).encode("utf-8")
        token = f.encrypt(plain)
        path = self.config_dir / CONFIG_FILE
        path.write_bytes(token)
        # restrict permissions on Unix; on Windows best-effort
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def mask(self, key: str) -> str:
        v = str(self._data.get(key, ""))
        if not v:
            return ""
        if len(v) <= 8:
            return "*" * len(v)
        return v[:4] + "*" * (len(v) - 8) + v[-4:]
