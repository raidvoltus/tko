"""Stage 2: credentials validated on load (S2-07)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tko.core.credentials import (
    CredentialError,
    CredentialNotFoundError,
    TokocryptoCredentials,
    load_tokocrypto,
)
from tko.core.types import SecretStr


def test_validate_rejects_short_key():
    creds = TokocryptoCredentials(SecretStr("short"), SecretStr("also_short_but"))
    with pytest.raises(CredentialError):
        creds.validate()


def test_validate_rejects_placeholder():
    creds = TokocryptoCredentials(
        SecretStr("changeme_api_key_xx"),
        SecretStr("changeme_api_secret_yy"),
    )
    with pytest.raises(CredentialError, match="placeholder"):
        creds.validate()


def test_load_validates_payload(monkeypatch):
    monkeypatch.setattr("tko.core.credentials._keyring_available", lambda: True)
    payload = json.dumps({"api_key": "ab", "api_secret": "cd"})
    with patch("keyring.get_password", return_value=payload):
        with pytest.raises(CredentialError):
            load_tokocrypto()


def test_load_validates_missing_fields(monkeypatch):
    monkeypatch.setattr("tko.core.credentials._keyring_available", lambda: True)
    payload = json.dumps({"api_key": "present_but_no_secret_ok"})
    with patch("keyring.get_password", return_value=payload):
        with pytest.raises(CredentialError):
            load_tokocrypto()


def test_load_accepts_valid(monkeypatch):
    monkeypatch.setattr("tko.core.credentials._keyring_available", lambda: True)
    payload = json.dumps(
        {
            "api_key": "valid_api_key_value_12345",
            "api_secret": "valid_api_secret_value_67890",
        }
    )
    with patch("keyring.get_password", return_value=payload):
        creds = load_tokocrypto()
        assert len(creds.api_key.get_secret_value()) >= 8


def test_load_missing_raises_not_found(monkeypatch):
    monkeypatch.setattr("tko.core.credentials._keyring_available", lambda: True)
    monkeypatch.setattr(
        "tko.core.credentials._legacy_cred_file",
        lambda: __import__("pathlib").Path("/nonexistent/tko_cred.json"),
    )
    with patch("keyring.get_password", return_value=None):
        with pytest.raises(CredentialNotFoundError):
            load_tokocrypto()
