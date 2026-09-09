"""Main LIVE trading loop with Stage-4 lifecycle governor. LIVE only."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from tko.audit.audit_log import AuditLog
from tko.core.config import Settings
from tko.core.credentials import load_telegram, load_tokocrypto
from tko.core.types import Signal
from tko.exchange.tokocrypto import TokocryptoClient
from tko.execution.engine import ExecutionEngine
from tko.notify.telegram import TelegramNotifier
from tko.reconciliation.reconciler import Reconciler
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker
from tko.risk.position_store import PositionStore
from tko.runtime.lifecycle import LifecycleGovernor, LifecycleState
from tko.runtime.metrics import MetricsStore
from tko.runtime.watchdog import Heartbeat
from tko.strategy.btc import BtcAnalyzer
from tko.ml.filter import MlSignalFilter
from tko.ml.ohlcv_store import OhlcvStore

logger = logging.getLogger(__name__)
STABLE_LIKE = frozenset({"IDR", "USDT", "USDC", "BUSD", "USD", "BNB"})
