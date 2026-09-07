# Tokocrypto Production Trading Bot (Windows 7 Compatible)

Production-grade trading bot targeting **Windows 7 SP1 64-bit** with full Risk Engine, PAPER/SHADOW/LIVE modes, ML inference (optional), Telegram trade notifications, and single-dashboard Tkinter GUI.

## Quick Start (Windows 7)

1. Install **Python 3.8.10 64-bit**
2. Run `scripts\install_win7.bat`
3. Run `python main.py`
4. Enter API Key / Secret / Telegram credentials in the GUI
5. Save → Test → Start in **PAPER** mode

## Architecture

```
Tokocrypto REST/WS
  → Data Normalization
  → Feature Engineering
  → ML Inference (optional, probability only)
  → Signal (BUY/SELL/WAIT candidate)
  → Risk Engine (mandatory gate)
  → Exchange Rule Validation (PRICE_FILTER, LOT_SIZE, NOTIONAL, ...)
  → Order Execution
  → Order Tracking / Reconciliation
  → Portfolio / PnL
  → Telegram (notification only)
```

**ML never has direct order authority.**

## Modes

| Mode   | Behavior                                      |
|--------|-----------------------------------------------|
| PAPER  | Simulated fills, no real orders (default)     |
| SHADOW | Logic runs, orders not sent                   |
| LIVE   | Real orders — explicit confirm + safety gates |

## Safety

- Kill switch
- Circuit breaker
- Max order / position / exposure / daily loss
- Stale market / WS disconnect / orderbook desync protection
- No blind retry on POST order
- UNKNOWN status → reconciliation, never assumed failed
- No withdrawal API
- Secrets encrypted at rest, never logged

## Tests

```bash
python -m pytest tests/ -v
```

## Training ML (modern machine)

```bash
pip install -r requirements-training.txt
# train, evaluate, export package to models/default/
# copy package to Win7 machine
```

## Compatibility

See `docs/COMPATIBILITY_MATRIX.md`.
