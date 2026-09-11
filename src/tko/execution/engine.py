"""LIVE order execution with intent, normalization, reconciliation. No dry-run."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from tko.core.config import Settings
from tko.core.types import OrderResult, OrderType, Side
from tko.exchange.tokocrypto import TokocryptoClient, TokocryptoError
from tko.execution.errors import ErrorCategory, is_ambiguous
from tko.execution.fill_journal import FillEvent, FillJournal, make_event_id
from tko.execution.intent import IntentStore, OrderIntent, OrderIntentStatus
from tko.reconciliation.reconciler import Reconciler
from tko.risk.engine import RiskDecision, RiskEngine
from tko.risk.position_store import PositionStore

logger = logging.getLogger(__name__)
