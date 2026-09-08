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
python -m tko setup              # API key + Telegram
python -m tko run                # LIVE bot
python -m tko status             # saldo
python -m tko stop               # kill switch
tko.exe install-service          # scheduled task ONSTART (Admin)
tko.exe uninstall-service        # hapus scheduled task (Admin)
```

## Windows scheduled task (portable .exe)

Agar bot jalan otomatis saat Windows menyala **tanpa login user**, gunakan `schtasks` bawaan Windows (bukan NSSM).

**Prasyarat**

1. Build/portable `tko.exe` sudah ada di folder tetap (mis. `D:\TKO\`).
2. Sudah menjalankan `tko.exe setup` (API key + Telegram).
3. Buka **Command Prompt sebagai Administrator**.

**Pasang**

```text
cd /d D:\TKO
tko.exe install-service
tko.exe install-service --task-name MyBot --force
```

Perilaku install:

- Task name default: `TkoBot` (ubah dengan `--task-name`).
- Trigger: **ONSTART** (saat sistem boot).
- Account: **SYSTEM** (run whether user is logged on or not; tidak minta password).
- Action: `tko.exe run` dengan working directory = folder exe.
- Membuat file `.portable` di samping exe agar state/log tetap di folder itu.
- `--force` menimpa task yang sudah ada.

**Cabut**

```text
tko.exe uninstall-service
tko.exe uninstall-service --task-name MyBot
```

**Portabilitas**

- Path **absolut** ke exe disimpan di task. Jika folder dipindah, jalankan `uninstall-service` di lokasi lama (atau hapus task manual), lalu `install-service` di lokasi baru.
- Pastikan akun SYSTEM bisa baca/tulis folder exe (state, logs, kredensial). Contoh:

```text
icacls "D:\TKO" /grant "SYSTEM:(OI)(CI)F" /T
```

- Jika `/RU SYSTEM` ditolak kebijakan, install dibatalkan dengan pesan jelas (tidak ada prompt password).

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
