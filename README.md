# TKO — Autonomous Tokocrypto LIVE Trading Bot

Bot trading **LIVE** khusus [Tokocrypto](https://www.tokocrypto.com) untuk Windows 10+.

## Fitur utama

- Deteksi saldo (IDR / USDT / aset lain)
- Analisis potensi **BTC** (teknikal sederhana + momentum)
- Beli BTC otomatis jika sinyal bagus & saldo cukup
- Jual ke pair mana pun yang profitable (IDR, USDT, dll)
- Risk limit (max posisi, max daily loss, kill switch)
- Setup API Key Tokocrypto + Telegram via CLI
- Mode **LIVE only** (tidak ada paper/demo)
- Standalone `.exe` (one-folder portable)

## Peringatan keras

Trading kripto **berisiko tinggi**. Bot ini bisa kehilangan uang.
- Hanya gunakan modal yang siap hilang.
- Pastikan API Key **Withdrawal = DISABLED**.
- Jalankan dulu dengan modal kecil.
- Tidak ada jaminan profit.

## Requirement

- Windows 10 / 11 (x64)
- Python 3.10+ (hanya untuk development; runtime pakai `.exe`)
- Akun Tokocrypto + API Key (trading enabled, withdrawal disabled)

## Quick start (development)

```powershell
git clone https://github.com/raidvoltus/tko.git
cd tko
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

# Setup credentials (sekali saja)
python -m tko setup

# Jalankan bot LIVE
python -m tko run
```

## Setup credentials

```powershell
python -m tko setup
```

Akan meminta:

1. Tokocrypto API Key
2. Tokocrypto API Secret
3. Telegram Bot Token (opsional)
4. Telegram Chat ID (opsional)

Credentials disimpan di Windows Credential Manager (via keyring) bila tersedia,  
atau file terenkripsi lokal.

## CLI

```text
python -m tko setup          # simpan API key + Telegram
python -m tko run            # jalankan bot LIVE
python -m tko status         # cek saldo + posisi
python -m tko stop           # kirim sinyal stop (kill switch)
python -m tko --version
python -m tko --smoke        # test packaging tanpa order
```

## Strategi (v0.1)

1. Baca free balance IDR (atau quote utama).
2. Analisis BTC (RSI + EMA crossover + volume).
3. Jika sinyal BUY dan risk OK → market/limit buy BTC.
4. Monitor posisi; jika profit target tercapai atau stop-loss → jual ke pair terbaik (IDR/USDT).
5. Loop terus dengan interval yang bisa dikonfigurasi.

## Build standalone (Windows)

```powershell
pip install -e ".[packaging]"
powershell -File scripts/build_windows.ps1
```

Hasil: `dist/TKO/` (one-folder portable).

## Struktur

```text
src/tko/
  core/         # config, credentials, types
  exchange/     # Tokocrypto adapter (CCXT)
  strategy/     # analisis BTC + decision
  risk/         # limit & kill switch
  execution/    # order submit + reconcile
  notify/       # Telegram
  runtime/      # entrypoint, loop
```

## Lisensi

MIT. Gunakan dengan tanggung jawab sendiri.
