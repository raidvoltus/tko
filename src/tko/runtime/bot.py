"""Main LIVE trading loop with Stage-4 lifecycle governor. LIVE only.

IMPORTANT: This is a temporary stub. The full bot.py was corrupted on remote.
Restore from project artifact before running LIVE or full tests:

  cp bot_stage5_ml_wired.py src/tko/runtime/bot.py
  # SHA256: ffd9eca4ded411ab920c24011160e6f9ebef2cbb95343437d08b99dcdc3ff8c0

See docs/RESTORE_BOT.md.
"""

from __future__ import annotations

from pathlib import Path

from tko.core.config import Settings


class TradingBot:
    """Stub — replace this file with the full Stage-5 wired implementation."""

    def __init__(self, settings: Settings, state_dir: Path) -> None:
        self.s = settings
        self.state_dir = state_dir
        raise RuntimeError(
            "src/tko/runtime/bot.py is a restore stub. "
            "Copy bot_stage5_ml_wired.py over this file (see docs/RESTORE_BOT.md)."
        )

    def start(self) -> None:
        raise RuntimeError("bot.py not restored")

    def stop(self) -> None:
        return None

    def request_shutdown(self) -> None:
        return None

    def _startup_barrier(self) -> bool:
        raise RuntimeError("bot.py not restored")

    def _tick(self) -> None:
        raise RuntimeError("bot.py not restored")
