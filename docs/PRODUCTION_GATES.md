# Production Gates Evidence (LIVE-only)

Policy: **NO TRADE** over uncertain trade. **RECONCILE** over duplicate. **UNKNOWN** over false success.

| Gate | Status | Evidence |
|------|--------|----------|
| Live Tokocrypto adapter (CCXT) | PARTIAL | `exchange/tokocrypto.py` REST |
| Authentication / keyring | PASS (code) | credentials fail-closed tests |
| recvWindow≤5000 | PASS (code) | DEFAULT_RECV_WINDOW=5000 |
| Symbol constraints Decimal | PASS (code) | constraints.py |
| Market data REST | PASS (code) | ohlcv/ticker |
| Strategy RSI+EMA | PASS (code) | strategy/btc.py |
| Expected edge | PASS (code+test) | strategy/edge.py |
| Multi-asset rank | PASS (code+test) | strategy/ranker.py |
| Ensemble multi-model | NOT READY | single rule + optional ML |
| Risk engine | PASS (code+test) | kill/daily loss/notional |
| Order intent / UNKNOWN | PASS (code+test) | IntentStore |
| Reconciliation | PASS (code+test) | Reconciler |
| WebSocket | **NOT READY** | REST poll only |
| User data stream | **NOT READY** | not implemented |
| Rate limit 429/418 | PASS (code) | circuit cooldown |
| Telegram | PASS (code) | notify |
| Linux systemd | PARTIAL | deploy/tko.service |
| Unit tests | PASS | 330 local |
| Production simulation path | PASS (absent) | LIVE-only Settings |

**Overall: NOT READY** until WebSocket/user-stream and live runtime verification.
