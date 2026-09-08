"""Secure credential storage for Tokocrypto API + Telegram."""

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


def _cred_file() -> Path:
    d = _state_root() / "credentials"
    d.mkdir(parents=True, exist_ok=True)
    return d / "tokocrypto.json"


def save_tokocrypto(api_key: str, api_secret: str) -> None:
    creds = TokocryptoCredentials(SecretStr(api_key.strip()), SecretStr(api_secret.strip()))
    creds.validate()
    if sys.platform == "win32":
        try:
            import keyring

            payload = json.dumps(
                {
                    "api_key": creds.api_key.get_secret_value(),
                    "api_secret": creds.api_secret.get_secret_value(),
                }
            )
            keyring.set_password("TKO:tokocrypto", "default", payload)
            return
        except Exception:
            pass
    path = _cred_file()
    data = {
        "api_key": creds.api_key.get_secret_value(),
        "api_secret": creds.api_secret.get_secret_value(),
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_tokocrypto() -> TokocryptoCredentials:
    if sys.platform == "win32":
        try:
            import keyring

            raw = keyring.get_password("TKO:tokocrypto", "default")
            if raw:
                data = json.loads(raw)
                return TokocryptoCredentials(
                    SecretStr(data["api_key"]), SecretStr(data["api_secret"])
                )
        except Exception:
            pass
    path = _cred_file()
    if not path.exists():
        raise CredentialNotFoundError(
            "Tokocrypto credentials not found. Run: python -m tko setup"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return TokocryptoCredentials(SecretStr(data["api_key"]), SecretStr(data["api_secret"]))


def save_telegram(bot_token: str, chat_id: str) -> None:
    path = _state_root() / "credentials" / "telegram.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"bot_token": bot_token.strip(), "chat_id": str(chat_id).strip()}),
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_telegram() -> TelegramCredentials | None:
    path = _state_root() / "credentials" / "telegram.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    token = data.get("bot_token") or ""
    chat = data.get("chat_id") or ""
    if not token or not chat:
        return None
    return TelegramCredentials(SecretStr(token), str(chat))
