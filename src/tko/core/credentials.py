"""Secure credential storage — keyring primary, fail-closed (no plaintext fallback)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from tko.core.types import SecretStr


class CredentialError(Exception):
    pass


class CredentialNotFoundError(CredentialError):
    pass


@dataclass(frozen=True, slots=True)
class TokocryptoCredentials:
    api_key: SecretStr
    api_secret: SecretStr

    def validate(self) -> None:
        k = self.api_key.get_secret_value()
        s = self.api_secret.get_secret_value()
        if not isinstance(k, str) or not isinstance(s, str):
            raise CredentialError("API key/secret must be strings")
        if len(k.strip()) < 8 or len(s.strip()) < 8:
            raise CredentialError("API key/secret too short")
        if k != k.strip() or s != s.strip():
            raise CredentialError("API key/secret must not have leading/trailing whitespace")
        lowered = (k + s).lower()
        for bad in ("changeme", "your_api", "xxx", "placeholder", "test_key"):
            if bad in lowered:
                raise CredentialError("API key/secret looks like a placeholder")


@dataclass(frozen=True, slots=True)
class TelegramCredentials:
    bot_token: SecretStr
    chat_id: str

    def validate(self) -> None:
        token = self.bot_token.get_secret_value()
        if not token or len(token.strip()) < 10:
            raise CredentialError("Telegram bot token too short")
        if ":" not in token:
            raise CredentialError("Telegram bot token format invalid")
        if not str(self.chat_id).strip():
            raise CredentialError("Telegram chat_id empty")


def _state_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "TKO"
    return Path.home() / ".tko"


def _legacy_cred_file() -> Path:
    return _state_root() / "credentials" / "tokocrypto.json"


def _legacy_tg_file() -> Path:
    return _state_root() / "credentials" / "telegram.json"


def _keyring_available() -> bool:
    try:
        import keyring  # noqa: F401

        return True
    except Exception:
        return False


def _require_keyring() -> None:
    if not _keyring_available():
        raise CredentialError(
            "Keyring tidak tersedia. Instal paket 'keyring' dan pastikan backend OS aktif. "
            "Fallback file plaintext telah dinonaktifkan (fail-closed)."
        )


def save_tokocrypto(api_key: str, api_secret: str) -> None:
    creds = TokocryptoCredentials(SecretStr(api_key.strip()), SecretStr(api_secret.strip()))
    creds.validate()
    _require_keyring()
    import keyring

    payload = json.dumps(
        {
            "api_key": creds.api_key.get_secret_value(),
            "api_secret": creds.api_secret.get_secret_value(),
        }
    )
    keyring.set_password("TKO:tokocrypto", "default", payload)
    legacy = _legacy_cred_file()
    if legacy.exists():
        try:
            legacy.unlink()
        except OSError:
            pass


def load_tokocrypto() -> TokocryptoCredentials:
    if _keyring_available():
        try:
            import keyring

            raw = keyring.get_password("TKO:tokocrypto", "default")
            if raw:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise CredentialError("Keyring payload is not a JSON object")
                key = data.get("api_key")
                secret = data.get("api_secret")
                if not key or not secret:
                    raise CredentialError("Keyring payload missing api_key or api_secret")
                creds = TokocryptoCredentials(SecretStr(str(key)), SecretStr(str(secret)))
                creds.validate()  # S2-07: validate on load
                return creds
        except CredentialError:
            raise
        except Exception as exc:
            raise CredentialError(f"Gagal membaca keyring: {exc}") from exc

    legacy = _legacy_cred_file()
    if legacy.exists():
        raise CredentialError(
            f"Ditemukan kredensial plaintext lama di {legacy}. "
            "Fallback plaintext dinonaktifkan. Jalankan 'tko setup' interaktif untuk migrasi ke keyring, "
            "lalu hapus file tersebut."
        )

    raise CredentialNotFoundError(
        "Tokocrypto credentials tidak ditemukan di keyring. Jalankan: tko setup"
    )


def save_telegram(token: str, chat_id: str) -> None:
    creds = TelegramCredentials(SecretStr(token.strip()), str(chat_id).strip())
    creds.validate()
    _require_keyring()
    import keyring

    payload = json.dumps(
        {
            "bot_token": creds.bot_token.get_secret_value(),
            "chat_id": creds.chat_id,
        }
    )
    keyring.set_password("TKO:telegram", "default", payload)
    legacy = _legacy_tg_file()
    if legacy.exists():
        try:
            legacy.unlink()
        except OSError:
            pass


def load_telegram() -> TelegramCredentials | None:
    """Load Telegram creds. Returns None if unset; raises CredentialError if corrupt."""
    if not _keyring_available():
        return None
    try:
        import keyring

        raw = keyring.get_password("TKO:telegram", "default")
        if not raw:
            return None
        data = json.loads(raw)
        creds = TelegramCredentials(
            SecretStr(str(data["bot_token"])),
            str(data["chat_id"]),
        )
        creds.validate()
        return creds
    except CredentialError:
        raise
    except Exception:
        return None
