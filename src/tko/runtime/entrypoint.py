"""CLI entrypoint."""

from __future__ import annotations

import argparse
import logging
import sys
from getpass import getpass

from tko import __version__
from tko.core.config import load_settings
from tko.core.credentials import (
    CredentialNotFoundError,
    load_tokocrypto,
    save_telegram,
    save_tokocrypto,
)
from tko.runtime.paths import log_dir, state_dir


def _setup_logging(level: str) -> None:
    log_dir().mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir() / "tko.log", encoding="utf-8"),
        ],
    )


def cmd_setup(_: argparse.Namespace) -> int:
    print("=== TKO Credential Setup (Tokocrypto LIVE) ===")
    print("Pastikan API Key punya izin TRADING dan WITHDRAWAL = DISABLED.\n")
    api_key = input("Tokocrypto API Key: ").strip()
    api_secret = getpass("Tokocrypto API Secret: ").strip()
    if not api_key or not api_secret:
        print("API key/secret wajib diisi.")
        return 1
    save_tokocrypto(api_key, api_secret)
    print("Tokocrypto credentials saved.\n")

    use_tg = input("Setup Telegram? [y/N]: ").strip().lower()
    if use_tg in ("y", "yes"):
        token = input("Telegram Bot Token: ").strip()
        chat = input("Telegram Chat ID: ").strip()
        if token and chat:
            save_telegram(token, chat)
            print("Telegram credentials saved.")
    print("\nSelesai. Jalankan: python -m tko run")
    return 0


def cmd_run(_: argparse.Namespace) -> int:
    settings = load_settings()
    _setup_logging(settings.log_level)
    try:
        load_tokocrypto()
    except CredentialNotFoundError as exc:
        print(exc)
        return 1
    from tko.runtime.bot import TradingBot

    bot = TradingBot(settings, state_dir())
    print("Starting TKO LIVE bot (Ctrl+C to stop)...")
    try:
        bot.start()
    except KeyboardInterrupt:
        bot.stop()
        print("\nStopped.")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    settings = load_settings()
    _setup_logging(settings.log_level)
    from tko.exchange.tokocrypto import TokocryptoClient

    try:
        creds = load_tokocrypto()
    except CredentialNotFoundError as exc:
        print(exc)
        return 1
    client = TokocryptoClient(creds)
    client.connect()
    bal = client.fetch_balance()
    print("=== Balances ===")
    for asset, b in sorted(bal.items()):
        if b.total > 0:
            print(f"  {asset:8s} free={b.free:.8f}  used={b.used:.8f}  total={b.total:.8f}")
    symbol = client.resolve_symbol(settings.base_asset, settings.quote_asset)
    if symbol:
        t = client.fetch_ticker(symbol)
        print(f"\n{symbol} last={t.last}")
    client.close()
    return 0


def cmd_stop(_: argparse.Namespace) -> int:
    from tko.risk.engine import RiskEngine

    settings = load_settings()
    risk = RiskEngine(settings, state_dir())
    risk.activate_kill_switch()
    print("Kill switch activated. Bot will stop opening new positions.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tko", description="TKO Tokocrypto LIVE bot")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="packaging smoke (no orders)")
    sub = parser.add_subparsers(dest="command")

    p_setup = sub.add_parser("setup", help="Save API key + Telegram")
    p_setup.set_defaults(func=cmd_setup)

    p_run = sub.add_parser("run", help="Start LIVE bot")
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="Show balances")
    p_status.set_defaults(func=cmd_status)

    p_stop = sub.add_parser("stop", help="Activate kill switch")
    p_stop.set_defaults(func=cmd_stop)

    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    if args.smoke:
        print(f"tko {__version__} smoke OK")
        return 0
    if not args.command:
        parser.print_help()
        return 0
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
