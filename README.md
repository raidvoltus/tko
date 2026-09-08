# TKO — Autonomous Tokocrypto LIVE Trading Bot

Bot trading **LIVE** khusus [Tokocrypto](https://www.tokocrypto.com) untuk Windows 10+.

**Mode: LIVE only.** Tidak ada paper, demo, atau dry-run.

## Fitur utama

- Deteksi saldo multi-asset (IDR, USDT, ETH, BTC, dll)
- Analisis potensi **BTC** (RSI + EMA)
- Beli BTC otomatis jika sinyal bagus & saldo quote cukup
- Jual posisi (BTC/ETH/…) saat TP / SL / sinyal sell
- Risk limit + kill switch
- Setup API Key Tokocrypto + Telegram via CLI
- Execution hardened: client order ID, reconciliasi, filter exchange

## Peringatan keras

Trading kripto **berisiko tinggi**. Bot ini mengirim order **LIVE** ke exchange.
- Hanya gunakan modal yang siap hilang.
- Pastikan API Key **Withdrawal = DISABLED**.
- Jalankan dulu dengan modal kecil.
- Tidak ada jaminan profit.

## Quick start

```powershell
git clone https://github.com/raidvoltus/tko.git
cd tko
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python -m tko setup
python -m tko run
```

## CLI

```text
python -m tko setup    # API key + Telegram
python -m tko run      # LIVE bot
python -m tko status   # saldo
python -m tko stop     # kill switch
```

## Strategi

1. Scan semua free balance (IDR/USDT/ETH/BTC/…).
2. Jika ada posisi base → evaluasi SELL (TP/SL/sinyal).
3. Jika tidak: cari quote cukup (prioritas IDR → USDT → USDC).
4. Analisis BTC; jika BUY + risk OK → MARKET BUY (quoteOrderQty).
5. Loop interval konfigurasi.

## Execution safety

- recvWindow = 5000
- Client order ID persistent sebelum submit
- Timeout / HTTP 5XX → UNKNOWN → reconciliation (bukan blind retry)
- Quantity SELL di-floor ke stepSize (Decimal)
- LOT_SIZE / MARKET_LOT_SIZE / minNotional
- 429 backoff, 418 circuit breaker
- Missing metadata → fail closed
