"""Fair backtest harness — baseline = BtcAnalyzer rule strategy."""

from __future__ import annotations

from dataclasses import dataclass, field

from tko.core.config import Settings
from tko.core.types import OHLCV, Signal
from tko.strategy.btc import BtcAnalyzer


@dataclass
class BacktestMetrics:
    n_bars: int = 0
    n_trades: int = 0
    n_wins: int = 0
    n_losses: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    exposure: float = 0.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "n_bars": self.n_bars,
            "n_trades": self.n_trades,
            "n_wins": self.n_wins,
            "n_losses": self.n_losses,
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "net_return": self.net_return,
            "max_drawdown": self.max_drawdown,
            "sharpe": self.sharpe,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "exposure": self.exposure,
        }


@dataclass
class BacktestResult:
    metrics: BacktestMetrics
    equity_curve: list[float] = field(default_factory=list)
    trade_pnls: list[float] = field(default_factory=list)
    name: str = "baseline"


def run_rule_baseline(
    candles: list[OHLCV],
    settings: Settings | None = None,
    *,
    horizon_bars: int = 4,
    fee_bps: float = 10.0,
) -> BacktestResult:
    """Long-only simulation: enter on BUY, exit on SELL or after horizon."""
    settings = settings or Settings(live_mode=True, min_quote_balance=1)
    analyzer = BtcAnalyzer(settings)
    fee = fee_bps / 10000.0
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    curve: list[float] = []
    trade_pnls: list[float] = []
    in_pos = False
    entry = 0.0
    entry_i = 0
    bars_in_pos = 0
    wins = losses = 0
    gp = gl = 0.0

    min_len = max(settings.ema_slow, settings.rsi_period) + 2
    for i in range(min_len, len(candles)):
        window = candles[: i + 1]
        decision = analyzer.analyze(window)
        sig = decision.signal
        px = candles[i].close
        if not in_pos and sig == Signal.BUY and px > 0:
            in_pos = True
            entry = px * (1.0 + fee)
            entry_i = i
        elif in_pos:
            bars_in_pos += 1
            should_exit = sig == Signal.SELL or (i - entry_i) >= horizon_bars
            if should_exit and entry > 0:
                exit_px = px * (1.0 - fee)
                pnl = exit_px / entry - 1.0
                equity *= 1.0 + pnl
                trade_pnls.append(pnl)
                if pnl >= 0:
                    wins += 1
                    gp += pnl
                else:
                    losses += 1
                    gl += abs(pnl)
                in_pos = False
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        curve.append(equity)

    if in_pos and candles and entry > 0:
        px = candles[-1].close * (1.0 - fee)
        pnl = px / entry - 1.0
        equity *= 1.0 + pnl
        trade_pnls.append(pnl)
        if pnl >= 0:
            wins += 1
            gp += pnl
        else:
            losses += 1
            gl += abs(pnl)
        if curve:
            curve[-1] = equity
        else:
            curve.append(equity)

    n_trades = wins + losses
    rets = trade_pnls
    sharpe = 0.0
    if len(rets) >= 2:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std = var**0.5
        if std > 1e-12:
            sharpe = mean / std

    n_bars = max(0, len(candles) - min_len)
    metrics = BacktestMetrics(
        n_bars=n_bars,
        n_trades=n_trades,
        n_wins=wins,
        n_losses=losses,
        gross_profit=gp,
        gross_loss=gl,
        net_return=equity - 1.0,
        max_drawdown=max_dd,
        sharpe=sharpe,
        win_rate=(wins / n_trades) if n_trades else 0.0,
        profit_factor=(gp / gl) if gl > 1e-12 else (999.0 if gp > 0 else 0.0),
        exposure=(bars_in_pos / n_bars) if n_bars else 0.0,
    )
    return BacktestResult(
        metrics=metrics, equity_curve=curve, trade_pnls=trade_pnls, name="btc_rule_baseline"
    )
