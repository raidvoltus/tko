"""CLI entrypoint."""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import signal
import sys
from getpass import getpass

from tko import __version__
from tko.core.config import SettingsError, load_settings
from tko.core.credentials import (
    CredentialError,
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
    print("Kredensial disimpan di OS keyring (fail-closed).\n")
    api_key = input("Tokocrypto API Key: ").strip()
    api_secret = getpass("Tokocrypto API Secret: ").strip()
    if not api_key or not api_secret:
        print("API key/secret wajib diisi.")
        return 1
    try:
        save_tokocrypto(api_key, api_secret)
    except CredentialError as exc:
        print(f"Gagal menyimpan: {exc}")
        return 1
    print("Tokocrypto credentials saved to keyring.\n")
    use_tg = input("Setup Telegram? [y/N]: ").strip().lower()
    if use_tg in ("y", "yes"):
        token = input("Telegram Bot Token: ").strip()
        chat = input("Telegram Chat ID: ").strip()
        if token and chat:
            try:
                save_telegram(token, chat)
                print("Telegram credentials saved to keyring.")
            except CredentialError as exc:
                print(f"Gagal Telegram: {exc}")
                return 1
    print("\nSelesai. Jalankan: python -m tko run")
    return 0


def cmd_run(_: argparse.Namespace) -> int:
    """Fail-closed startup barrier before any trading runtime."""
    try:
        settings = load_settings()
        settings.validate_for_live()
    except SettingsError as exc:
        print(f"CONFIG ERROR (fail-closed): {exc}")
        return 1
    except Exception as exp:  # noqa: BLE001
        print(f"CONFIG ERROR (fail-closed): {exp}")
        return 1

    if settings.live_mode is not True:
        print("LIVE GATE FAILED: live_mode must be True. Aborting.")
        return 1

    _setup_logging(settings.log_level)

    try:
        load_tokocrypto()
    except (CredentialNotFoundError, CredentialError) as exc:
        print(f"CREDENTIAL ERROR (fail-closed): {exc}")
        return 1

    from tko.runtime.instance_lock import InstanceLock, InstanceLockError

    lock = InstanceLock(state_dir() / "tko.lock")
    try:
        lock.acquire()
    except InstanceLockError as exc:
        print(exc)
        return 1

    bot_ref: dict = {"bot": None}

    def _on_signal(signum: int, frame: object) -> None:
        bot = bot_ref.get("bot")
        if bot is not None:
            try:
                if hasattr(bot, "request_shutdown"):
                    bot.request_shutdown()
                elif hasattr(bot, "lifecycle") and hasattr(bot.lifecycle, "request_stop"):
                    bot.lifecycle.request_stop()
            except Exception:  # noqa: BLE001,S110
                pass
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    try:
        from tko.runtime.bot import TradingBot

        bot = TradingBot(settings, state_dir())
        bot_ref["bot"] = bot
        print("Starting TKO LIVE bot (Ctrl+C to stop)...")
        try:
            bot.start()
        except KeyboardInterrupt:
            bot.stop()
            print("\nStopped.")
        except SystemExit:
            try:
                bot.stop()
            except Exception:  # noqa: BLE001,S110
                pass
            raise
        return 0
    finally:
        bot = bot_ref.get("bot")
        if bot is not None:
            try:
                bot.stop()
            except Exception:  # noqa: BLE001,S110
                pass
        lock.release()


def cmd_status(_: argparse.Namespace) -> int:
    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"CONFIG ERROR: {exc}")
        return 1
    _setup_logging(settings.log_level)
    from tko.exchange.tokocrypto import TokocryptoClient
    from tko.runtime.metrics import MetricsStore

    try:
        creds = load_tokocrypto()
    except (CredentialNotFoundError, CredentialError) as exc:
        print(exc)
        return 1
    client = TokocryptoClient(creds)
    client.connect()
    bal = client.fetch_balance()
    print("=== Balances ===")
    for asset, b in sorted(bal.items()):
        if b.total > 0:
            print(f"  {asset:8s} free={b.free:.8f}  used={b.used:.8f}  total={b.total:.8f}")
    m = MetricsStore(state_dir() / "metrics.json").snapshot()
    print(
        f"\n=== Metrics ===\n  status={m.status} orders_today={m.orders_today} "
        f"ok={m.orders_success} fail={m.orders_failed} pnl={m.daily_pnl:.4f}"
    )
    client.close()
    return 0


def cmd_stop(_: argparse.Namespace) -> int:
    from tko.risk.engine import RiskEngine

    settings = load_settings()
    RiskEngine(settings, state_dir()).activate_kill_switch("cli stop")
    print("Kill switch activated.")
    return 0


def cmd_backup(_: argparse.Namespace) -> int:
    from tko.runtime.backup import backup_state
    from tko.runtime.paths import app_root

    path = backup_state(state_dir(), app_root() / "backups")
    print(f"Backup written: {path}")
    return 0


def cmd_watchdog(args: argparse.Namespace) -> int:
    settings = load_settings()
    _setup_logging(settings.log_level)
    from tko.core.credentials import load_telegram
    from tko.notify.telegram import TelegramNotifier
    from tko.runtime.watchdog import Watchdog

    notify = TelegramNotifier(load_telegram() if settings.telegram_enabled else None)

    def on_stale(age: float) -> None:
        notify.send(f"TKO WATCHDOG: heartbeat stale age={age}s")

    wd = Watchdog(
        state_dir() / "heartbeat.json",
        stale_after_sec=float(getattr(args, "stale_after", None) or settings.heartbeat_stale_sec),
        on_stale=on_stale,
    )
    print(f"Watchdog monitoring (stale_after={wd.stale_after_sec}s). Ctrl+C to stop.")
    try:
        wd.run_loop(interval_sec=float(getattr(args, "interval", 30) or 30))
    except KeyboardInterrupt:
        print("Watchdog stopped.")
    return 0


def cmd_install_service(args: argparse.Namespace) -> int:
    from tko.runtime.windows_service import DEFAULT_TASK_NAME, install_scheduled_task_xml

    result = install_scheduled_task_xml(
        getattr(args, "task_name", None) or DEFAULT_TASK_NAME,
        force=bool(getattr(args, "force", False)),
        use_system=bool(getattr(args, "use_system", False)),
    )
    print(result.message)
    if result.detail and not result.ok:
        print(result.detail)
    return 0 if result.ok else 1


def cmd_uninstall_service(args: argparse.Namespace) -> int:
    from tko.runtime.windows_service import DEFAULT_TASK_NAME, uninstall_scheduled_task

    result = uninstall_scheduled_task(getattr(args, "task_name", None) or DEFAULT_TASK_NAME)
    print(result.message)
    if result.detail and not result.ok:
        print(result.detail)
    return 0 if result.ok else 1


def cmd_gui(_: argparse.Namespace) -> int:
    """Launch desktop control panel (IPC client only; does not stop Core on exit)."""
    from tko.gui.app import main as gui_main

    return gui_main()


def cmd_version(_: argparse.Namespace) -> int:
    """Print version / build metadata (no secrets)."""
    import platform

    from tko import __version__
    from tko.runtime.paths import app_root, data_root, is_frozen

    build = {
        "version": __version__,
        "commit": __import__("os").environ.get("TKO_BUILD_COMMIT", "unknown"),
        "build_time": __import__("os").environ.get("TKO_BUILD_TIME", "unknown"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "frozen": is_frozen(),
        "app_root": str(app_root()),
        "data_root": str(data_root()),
    }
    for k, v in build.items():
        print(f"{k}={v}")
    return 0


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(prog="tko", description="TKO Tokocrypto LIVE bot")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", help="show version/build metadata").set_defaults(func=cmd_version)
    sub.add_parser("setup").set_defaults(func=cmd_setup)
    sub.add_parser("run").set_defaults(func=cmd_run)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("stop").set_defaults(func=cmd_stop)
    sub.add_parser("backup").set_defaults(func=cmd_backup)
    sub.add_parser("gui").set_defaults(func=cmd_gui)

    p_wd = sub.add_parser("watchdog")
    p_wd.add_argument("--stale-after", type=float, default=None)
    p_wd.add_argument("--interval", type=float, default=30.0)
    p_wd.set_defaults(func=cmd_watchdog)

    p_install = sub.add_parser("install-service")
    p_install.add_argument("--task-name", default="TkoBot")
    p_install.add_argument("--force", action="store_true")
    p_install.add_argument("--use_system", action="store_true")
    p_install.set_defaults(func=cmd_install_service)

    p_uninstall = sub.add_parser("uninstall-service")
    p_uninstall.add_argument("--task-name", default="TkoBot")
    p_uninstall.set_defaults(func=cmd_uninstall_service)

    args = parser.parse_args(argv)
    if args.version:
        return cmd_version(args)
    if args.smoke:
        print(f"tko {__version__} smoke OK")
        return 0
    if not args.command:
        parser.print_help()
        return 0
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
