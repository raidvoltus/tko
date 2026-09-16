"""Feature engine tests — no lookahead, bounded dim."""
import time
import numpy as np
from src.features.engine import CandleBuffer, FeatureEngine, FEATURE_VERSION


def test_buffer_monotonic():
    b = CandleBuffer(maxlen=10)
    b.push("BTC_USDT", 1.0, 1, 1, 1, 1, 1)
    b.push("BTC_USDT", 0.5, 1, 1, 1, 1, 1)  # older — skip
    assert b.len("BTC_USDT") == 1


def test_features_shape():
    eng = FeatureEngine()
    for i in range(40):
        eng.candles.push("BTC_USDT", time.time() - (40 - i), 100 + i * 0.1, 101, 99, 100 + i * 0.1, 10)
    vec, names = eng.compute("BTC_USDT")
    assert vec.dtype == np.float32
    assert len(vec) == len(names)
    assert FEATURE_VERSION == "v1"
