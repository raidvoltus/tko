"""Secure credential persistence and redaction tests — no real secrets."""

import pytest

from src.security.credentials import (
    NAMESPACE_TG,
    NAMESPACE_TOKO,
    CredentialStore,
    load_telegram_credentials,
    load_toko_credentials,
    save_telegram_credentials,
    save_toko_credentials,
)
from src.security.redact import SecretRedactor


@pytest.fixture()
def secret_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    # force secrets under tmp
    import src.security.credentials as cred

    monkeypatch.setattr(cred, "_secrets_dir", lambda: tmp_path / "secrets")
    (tmp_path / "secrets").mkdir(parents=True, exist_ok=True)
    return tmp_path / "secrets"


def test_save_load_delete_toko(secret_dir):
    save_toko_credentials("testkey123456", "testsecret123456")
    assert CredentialStore(NAMESPACE_TOKO).validate() == "VALID"
    data = load_toko_credentials()
    assert data["api_key"] == "testkey123456"
    assert data["api_secret"] == "testsecret123456"
    # not plaintext in bin
    raw = (secret_dir / "tokocrypto_api.bin").read_bytes()
    assert b"testsecret123456" not in raw
    CredentialStore(NAMESPACE_TOKO).delete()
    assert CredentialStore(NAMESPACE_TOKO).validate() == "MISSING"


def test_telegram_persistence(secret_dir):
    save_telegram_credentials("123456:ABC-DEF_secret_token_xx", "999888")
    d = load_telegram_credentials()
    assert d["chat_id"] == "999888"
    assert "ABC-DEF" in d["bot_token"]
    CredentialStore(NAMESPACE_TG).delete()
    assert load_telegram_credentials() == {}


def test_redact_secrets():
    r = SecretRedactor()
    r.register("supersecretkey999")
    assert "supersecretkey999" not in r.redact("key=supersecretkey999 failed")
    assert "***REDACTED***" in r.redact("api_secret=supersecretkey999")


def test_require_live_still():
    import pytest

    from src.execution.production_policy import require_live

    with pytest.raises(ValueError):
        require_live("PAPER")
