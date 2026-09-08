from tko.execution.errors import ErrorCategory, classify_exception, is_ambiguous


def test_timeout_ambiguous():
    class TimeoutError_(Exception):
        pass

    cat = classify_exception(TimeoutError_("request timeout"))
    assert cat == ErrorCategory.TIMEOUT
    assert is_ambiguous(cat)


def test_5xx_ambiguous():
    cat = classify_exception(Exception("HTTP 503 service unavailable"))
    assert cat == ErrorCategory.EXCHANGE_5XX
    assert is_ambiguous(cat)


def test_418_circuit():
    cat = classify_exception(Exception("418 IP banned"))
    assert cat == ErrorCategory.CIRCUIT_BREAKER


def test_429_rate_limit():
    cat = classify_exception(Exception("429 too many requests"))
    assert cat == ErrorCategory.RATE_LIMIT
    assert not is_ambiguous(cat)


def test_insufficient_rejected():
    cat = classify_exception(Exception("Insufficient funds"))
    assert cat == ErrorCategory.DEFINITIVE_REJECTED
    assert not is_ambiguous(cat)
