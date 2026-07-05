"""Walk-forward backtest engine (honest, costs included).

Given a candle series and a strategy's target positions per bar, simulate the
paper equity curve. Costs are charged on every position change:

    Delta India cost ~ fee + 18% GST on the fee. At 0.05% taker that is
    ~0.059% per side (~0.118% round-trip). Set via ``cost_per_side_pct``.

A high-churn strategy is therefore correctly penalised. Results report BOTH
gross (before costs) and net (after costs) so the cost drag is visible — the
same discipline Garuda uses. Individual trades are recorded (side, entry/exit
price, pnl, bars held) so the dashboard can show real closed trades. This is a
coarse test (bar close-to-close, no intraday stops/funding) and is NOT a
guarantee of future results.
"""
from __future__ import annotations

import statistics
from typing import Callable, List

from .history import Candle, PERIODS_PER_YEAR

# ~0.059% per side = 0.05% taker fee + 18% GST on the fee
DEFAULT_COST_PER_SIDE_PCT = 0.059


def _max_drawdown(equity: List[float]) -> float:
    peak, mdd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            mdd = min(mdd, (e - peak) / peak)
    return mdd * 100


def run_backtest(
    candles: List[Candle],
    positions: List[int],
    resolution: str = "1h",
    cost_per_side_pct: float = DEFAULT_COST_PER_SIDE_PCT,
    allow_short: bool = True,
) -> dict:
    n = len(candles)
    if n < 3 or len(positions) != n:
        raise ValueError("need >=3 candles and one position per candle")

    closes = [c.c for c in candles]
    cps = cost_per_side_pct / 100.0
    pos = [p if allow_short else max(0, p) for p in positions]

    equity, gross_equity = 1.0, 1.0
    eq_curve = [1.0]
    bar_net_returns: List[float] = []
    trades: List[dict] = []

    prev = 0
    seg_pos, seg_gross, seg_start = 0, 1.0, 0

    def close_segment(exit_idx: int) -> None:
        if seg_pos == 0:
            return
        pnl = (seg_gross - 1) - 2 * cps * abs(seg_pos)
        trades.append({
            "side": "LONG" if seg_pos > 0 else "SHORT",
            "entry": closes[seg_start],
            "exit": closes[exit_idx],
            "entry_t": candles[seg_start].t,
            "exit_t": candles[exit_idx].t,
            "bars": exit_idx - seg_start,
            "pnl_pct": pnl * 100,
        })

    for i in range(n - 1):
        p = pos[i]
        cost = abs(p - prev) * cps
        r = closes[i + 1] / closes[i] - 1
        gross = p * r
        net = gross - cost

        gross_equity *= (1 + gross)
        equity *= (1 + net)
        eq_curve.append(equity)
        bar_net_returns.append(net)

        if p != seg_pos:
            close_segment(i)              # exit prior segment at close[i]
            seg_pos, seg_gross, seg_start = p, 1.0, i
        if seg_pos != 0:
            seg_gross *= (1 + seg_pos * r)
        prev = p

    # flatten at the end
    if prev != 0:
        equity *= (1 - abs(prev) * cps)
        eq_curve[-1] = equity
        close_segment(n - 1)

    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    gross_win = sum(t["pnl_pct"] for t in wins)
    gross_loss = -sum(t["pnl_pct"] for t in losses)

    sd = statistics.pstdev(bar_net_returns) if len(bar_net_returns) > 1 else 0.0
    ppy = PERIODS_PER_YEAR.get(resolution, 8760)
    if sd > 1e-9 and bar_net_returns:
        sharpe = statistics.mean(bar_net_returns) / sd * (ppy ** 0.5)
    else:
        sharpe = 0.0

    return {
        "bars": n,
        "net_return_pct": (equity - 1) * 100,
        "gross_return_pct": (gross_equity - 1) * 100,
        "cost_drag_pct": (gross_equity - equity) * 100,
        "max_drawdown_pct": _max_drawdown(eq_curve),
        "sharpe": sharpe,
        "trades": len(trades),
        "win_rate_pct": (len(wins) / len(trades) * 100) if trades else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "equity_curve": eq_curve,
        "trade_list": trades,
        "cost_per_side_pct": cost_per_side_pct,
    }


def backtest_strategy(
    candles: List[Candle],
    strategy: Callable[[List[Candle]], List[int]],
    resolution: str = "1h",
    **kw,
) -> dict:
    positions = strategy(candles)
    result = run_backtest(candles, positions, resolution=resolution, **kw)
    result["strategy"] = getattr(strategy, "__name__", "strategy")
    return result
