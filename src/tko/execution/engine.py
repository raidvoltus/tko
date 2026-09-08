"""LIVE order execution."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

from tko.core.config import Settings
from tko.core.types import OrderResult, OrderType, Side
from tko.exchange.tokocrypto import TokocryptoClient
from tko.risk.engine import RiskDecision

logger = logging.getLogger(__name__)


@dataclass
class PositionState:
    symbol: str
    base: str
    quote: str
    amount: float
    entry_price: float
    opened_at: float = field(default_factory=time.time)


class ExecutionEngine:
    def __init__(self, client: TokocryptoClient, settings: Settings) -> None:
        self.client = client
        self.s = settings
        self.positions: dict[str, PositionState] = {}

    def buy(
        self,
        symbol: str,
        base: str,
        quote: str,
        decision: RiskDecision,
        last_price: float,
    ) -> OrderResult | None:
        if not decision.approved or decision.size_base <= 0:
            return None
        amount = decision.size_base
        cid = f"tko-buy-{uuid.uuid4().hex[:12]}"
        logger.info(
            "LIVE BUY %s amount=%.8f (~%.0f %s) reason=%s",
            symbol,
            amount,
            decision.size_quote,
            quote,
            decision.reason,
        )
        if self.s.dry_run:
            logger.warning("dry_run=True — order not sent")
            return None
        result = self.client.create_order(
            symbol=symbol,
            side=Side.BUY,
            amount=amount,
            order_type=OrderType.MARKET,
            client_order_id=cid,
        )
        filled = result.filled or amount
        avg = result.average or last_price
        self.positions[symbol] = PositionState(
            symbol=symbol, base=base, quote=quote, amount=filled, entry_price=avg
        )
        logger.info("BUY filled id=%s filled=%.8f avg=%.4f", result.id, filled, avg)
        return result

    def sell(
        self,
        symbol: str,
        decision: RiskDecision,
        last_price: float,
    ) -> OrderResult | None:
        if not decision.approved or decision.size_base <= 0:
            return None
        amount = decision.size_base
        cid = f"tko-sell-{uuid.uuid4().hex[:12]}"
        logger.info(
            "LIVE SELL %s amount=%.8f reason=%s",
            symbol,
            amount,
            decision.reason,
        )
        if self.s.dry_run:
            logger.warning("dry_run=True — order not sent")
            return None
        result = self.client.create_order(
            symbol=symbol,
            side=Side.SELL,
            amount=amount,
            order_type=OrderType.MARKET,
            client_order_id=cid,
        )
        self.positions.pop(symbol, None)
        logger.info(
            "SELL filled id=%s filled=%.8f avg=%s",
            result.id,
            result.filled,
            result.average,
        )
        return result
