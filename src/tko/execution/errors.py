"""Exchange/execution error classification for safe retry policy."""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    AUTH_ERROR = "AUTH_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    TIMEOUT = "TIMEOUT"
    TEMPORARY_NETWORK_ERROR = "TEMPORARY_NETWORK_ERROR"
    EXCHANGE_5XX = "EXCHANGE_5XX"
    DEFINITIVE_REJECTED = "DEFINITIVE_REJECTED"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


AMBIGUOUS_CATEGORIES = frozenset(
    {
        ErrorCategory.TIMEOUT,
        ErrorCategory.TEMPORARY_NETWORK_ERROR,
        ErrorCategory.EXCHANGE_5XX,
        ErrorCategory.UNKNOWN_ERROR,
    }
)


def classify_exception(exc: BaseException) -> ErrorCategory:
    """Map an exception to a category."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()

    if any(x in name for x in ("authentication", "permission", "authorization")):
        return ErrorCategory.AUTH_ERROR
    if "invalid api" in msg or "api-key" in msg or "signature" in msg:
        return ErrorCategory.AUTH_ERROR

    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return ErrorCategory.RATE_LIMIT
    if "418" in msg or "ip ban" in msg or "banned" in msg:
        return ErrorCategory.CIRCUIT_BREAKER
    if "ratelimit" in name or "ddos" in name:
        return ErrorCategory.RATE_LIMIT

    if "timeout" in name or "timeout" in msg:
        return ErrorCategory.TIMEOUT
    if any(x in name for x in ("network", "requesttimeout", "exchangenotavailable")):
        return ErrorCategory.TEMPORARY_NETWORK_ERROR
    if any(x in msg for x in ("connection", "network", "temporarily unavailable", "econnreset")):
        return ErrorCategory.TEMPORARY_NETWORK_ERROR

    if any(code in msg for code in (" 500", " 502", " 503", " 504", "http 5")):
        return ErrorCategory.EXCHANGE_5XX
    if "exchange not available" in msg or "internal error" in msg:
        return ErrorCategory.EXCHANGE_5XX

    if any(
        x in name
        for x in (
            "insufficientfunds",
            "invalidorder",
            "badrequest",
            "ordernotfound",
            "cancelpending",
        )
    ):
        return ErrorCategory.DEFINITIVE_REJECTED
    if any(
        x in msg
        for x in (
            "insufficient",
            "notional",
            "lot size",
            "min notional",
            "invalid quantity",
            "invalid price",
            "filter failure",
            "order would trigger",
            "duplicate order",
        )
    ):
        return ErrorCategory.DEFINITIVE_REJECTED

    if "invalid" in name or "bad symbol" in msg:
        return ErrorCategory.INVALID_REQUEST

    return ErrorCategory.UNKNOWN_ERROR


def is_ambiguous(category: ErrorCategory) -> bool:
    return category in AMBIGUOUS_CATEGORIES


def extract_http_status(exc: BaseException) -> int | None:
    for attr in ("http_status", "status", "code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int) and 100 <= val <= 599:
            return val
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        st = resp.get("status")
        if isinstance(st, int):
            return st
    return None
