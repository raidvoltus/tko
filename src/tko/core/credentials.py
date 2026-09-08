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
        if len(k) < 8 or len(s) < 8:
            raise CredentialError("API key/secret too short")


@dataclass(frozen=True, slots=True)
class TelegramCredentials:
    bot_token: SecretStr
    chat_id: str


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
                return TokocryptoCredentials(
                    SecretStr(data["api_key"]), SecretStr(data["api_secret"])
                )
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
    _require_keyring()
    import keyring

    payload = json.dumps({"bot_token": token.strip(), "chat_id": str(chat_id).strip()})
    keyring.set_password("TKO:telegram", "default", payload)
    legacy = _legacy_tg_file()
    if legacy.exists():
        try:
            legacy.unlink()
        except OSError:
            pass


def load_telegram() -> TelegramCredentials | None:
    if not _keyring_available():
        return None
    try:
        import keyring

        raw = keyring.get_password("TKO:telegram", "default")
        if not raw:
            return None
        data = json.loads(raw)
        return TelegramCredentials(SecretStr(data["bot_token"]), str(data["chat_id"]))
    except Exception:
        return None
