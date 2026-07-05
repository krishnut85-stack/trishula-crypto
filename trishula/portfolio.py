"""Cross-sectional momentum on a multi-coin universe — the 'Quant' lane.

Ranks the whole universe by trailing return, holds the top fraction equal-weight,
rebalances periodically, and (optionally) goes to cash when BTC is below its
long moving average (regime filter). Long-only by default: the momentum research
is clear that the short leg carries the tail risk, and long-only stays inside
Delta India's tax-advantaged F&O bucket (no spot, no TDS).

Pure Python — no pandas/numpy — so it runs on the droplet with only `requests`.
This is a coarse daily model (close-to-close, turnover-based costs, funding as an
optional flat drag) and NOT a guarantee of future results.
"""
from __future__ import annotations

import statistics
from typing import Dict, List, Optional

from .history import Candle


def build_panel(candles_by_symbol: Dict[str, List[Candle]]):
    """Align all symbols onto one sorted timestamp axis. Missing = None."""
    all_t = sorted({c.t for cs in candles_by_symbol.values() for c in cs})
    maps = {s: {c.t: c.c for c in cs} for s, cs in candles_by_symbol.items()}
    panel = {s: [maps[s].get(t) for t in all_t] for s in candles_by_symbol}
    return all_t, panel


def _targets(panel, i, syms, lookback, top_frac, btc, regime_ma, use_regime):
    # regime filter: BTC below its long MA -> flat (cash)
    if use_regime and btc is not None:
        window = [btc[j] for j in range(max(0, i - regime_ma), i) if btc[j]]
        if btc[i] and window and btc[i] < sum(window) / len(window):
            return {s: 0.0 for s in syms}
    scores = {}
    for s in syms:
        c1 = panel[s][i]
        c0 = panel[s][i - lookback] if i - lookback >= 0 else None
        if c1 and c0:
            scores[s] = c1 / c0 - 1
    if len(scores) < 5:
        return {s: 0.0 for s in syms}
    k = max(1, int(len(scores) * top_frac))
    winners = sorted(scores, key=scores.get, reverse=True)[:k]
    w = 1.0 / k
    return {s: (w if s in winners else 0.0) for s in syms}


def _stats(curve: List[float], rets: List[float], ppy: float) -> dict:
    if len(curve) < 30:
        return {"cagr_pct": 0.0, "vol_pct": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0}
    yrs = len(rets) / ppy if ppy else 1.0
    cagr = (curve[-1] ** (1 / yrs) - 1) if (curve[-1] > 0 and yrs > 0) else -1.0
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    sharpe = (statistics.mean(rets) / sd * (ppy ** 0.5)) if sd > 1e-9 else 0.0
    peak, mdd = curve[0], 0.0
    for e in curve:
        peak = max(peak, e)
        if peak > 0:
            mdd = min(mdd, (e - peak) / peak)
    return {"cagr_pct": cagr * 100, "vol_pct": sd * (ppy ** 0.5) * 100,
            "sharpe": sharpe, "max_dd_pct": mdd * 100}


def liquidity_slippage(volmn: Dict[str, float], fee_pct: float = 0.059,
                       slip_base_pct: float = 0.03, slip_k: float = 0.08,
                       slip_cap_pct: float = 1.0) -> Dict[str, float]:
    """Per-side cost % per symbol, scaled by 24h volume ($mn).

    Cheap for liquid majors, expensive for thin low-caps — because momentum
    tends to pick exactly the illiquid movers, and pretending they cost the same
    as BTC is how a backtest lies. fee = taker + 18% GST; slippage grows as
    1/sqrt(volume).
    """
    out = {}
    for s, v in volmn.items():
        slip = min(slip_cap_pct, slip_base_pct + slip_k / max(0.05, (max(v, 1e-6)) ** 0.5))
        out[s] = fee_pct + slip
    return out


def cross_sectional_momentum(
    candles_by_symbol: Dict[str, List[Candle]],
    lookback: int = 56,           # bars (daily -> 8 weeks)
    rebal: int = 7,               # rebalance every N bars (daily -> weekly)
    top_frac: float = 0.2,
    cost_per_side_pct: float = 0.089,   # flat fallback if no per-symbol costs
    cost_by_symbol: Optional[Dict[str, float]] = None,  # per-side % per symbol
    funding_bps_per_day: float = 3.0,   # conservative long funding drag
    regime_symbol: str = "BTCUSD",
    regime_ma: int = 140,         # bars (daily -> ~20 weeks)
    use_regime: bool = True,
    periods_per_year: float = 365.0,
    label: str = "xs_momentum",
) -> dict:
    dates, panel = build_panel(candles_by_symbol)
    n = len(dates)
    syms = list(panel)
    if n < max(lookback, regime_ma) + 10:
        return {"label": label, "bars": n, "error": "not enough history"}

    btc = panel.get(regime_symbol)
    weights = {s: 0.0 for s in syms}
    equity, curve, rets = 1.0, [1.0], []
    fund = funding_bps_per_day / 10000.0

    for i in range(1, n):
        day_ret = 0.0
        for s in syms:
            c0, c1 = panel[s][i - 1], panel[s][i]
            if c0 and c1 and weights[s]:
                day_ret += weights[s] * (c1 / c0 - 1)
        funding_cost = fund * sum(w for w in weights.values() if w > 0)
        cost = 0.0
        if i % rebal == 0:
            new_w = _targets(panel, i, syms, lookback, top_frac, btc,
                             regime_ma, use_regime)
            if cost_by_symbol:
                cost = sum(abs(new_w[s] - weights[s])
                           * (cost_by_symbol.get(s, cost_per_side_pct) / 100.0)
                           for s in syms)
            else:
                cost = sum(abs(new_w[s] - weights[s]) for s in syms) * (cost_per_side_pct / 100.0)
            weights = new_w
        net = day_ret - cost - funding_cost
        equity *= (1 + net)
        curve.append(equity)
        rets.append(net)

    out = {"label": label, "bars": n, "symbols": len(syms),
           "total_return_pct": (equity - 1) * 100,
           "equity_curve": curve}
    out.update(_stats(curve, rets, periods_per_year))
    return out
