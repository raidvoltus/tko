"""
Runtime capability boundaries.

Strategy / ML / Challenger / Governor must not hold ExecutionManager or RestClient.
RiskEngine may be consulted; only ExecutionManager may call RestClient order submission.
"""
from __future__ import annotations

from typing import Any, Iterable, Type

FORBIDDEN_ATTR_NAMES = (
    "exec_mgr",
    "execution_manager",
    "rest_client",
    "rest",
    "_rest",
    "order_client",
)


def assert_no_execution_capability(obj: Any, label: str = "component") -> None:
    """Raise if object appears to hold execution/order client capability."""
    if obj is None:
        return
    # Direct type name checks
    cls_name = type(obj).__name__
    if cls_name in ("ExecutionManager", "RestClient"):
        raise RuntimeError(f"AUTHORITY_VIOLATION: {label} is {cls_name}")
    # Attribute scan (shallow)
    for name in FORBIDDEN_ATTR_NAMES:
        if hasattr(obj, name):
            val = getattr(obj, name, None)
            if val is not None and type(val).__name__ in ("ExecutionManager", "RestClient"):
                raise RuntimeError(
                    f"AUTHORITY_VIOLATION: {label} holds {name}={type(val).__name__}"
                )


def assert_types_forbidden(objs: Iterable[Any], forbidden: Iterable[Type], label: str) -> None:
    for o in objs:
        for ft in forbidden:
            if isinstance(o, ft):
                raise RuntimeError(f"AUTHORITY_VIOLATION: {label} instance of {ft.__name__}")
