"""Credentials fail-closed tests."""

from pathlib import Path
from unittest.mock import patch

import pytest

from tko.core import credentials as cred


def test_legacy_file_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cred, "_state_root", lambda: tmp_path)
    monkeypatch.setattr(cred, "_keyring_available", lambda: False)
    legacy = tmp_path / "credentials"
    legacy.mkdir()
    (legacy / "tokocrypto.json").write_text(
        '{"api_key":"x","api_secret":"y"}', encoding="utf-8"
    )
    with pytest.raises(cred.CredentialError, match="plaintext"):
        cred.load_tokocrypto()


def test_missing_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cred, "_state_root", lambda: tmp_path)
    monkeypatch.setattr(cred, "_keyring_available", lambda: True)
    with patch("keyring.get_password", return_value=None), pytest.raises(cred.CredentialNotFoundError):
        cred.load_tokocrypto()
