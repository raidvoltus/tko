"""Authentication / signature unit tests."""
from src.tokocrypto.auth import generate_signature, prepare_signed_params


def test_signature_deterministic():
    secret = "testsecret"
    total = "symbol=BTC_USDT&side=0&type=1&quantity=0.16&price=7500&timestamp=1581720670624&recvWindow=5000"
    sig1 = generate_signature(secret, total)
    sig2 = generate_signature(secret, total)
    assert len(sig1) == 64
    assert sig1 == sig2
    assert generate_signature(secret, total + "x") != sig1


def test_prepare_signed_params_adds_fields():
    params = {"symbol": "BTC_USDT", "side": 0}
    out = prepare_signed_params(params, "secretkey", recv_window=5000, timestamp_ms=1000)
    assert "timestamp" in out
    assert "recvWindow" in out
    assert "signature" in out
    assert len(out["signature"]) == 64
