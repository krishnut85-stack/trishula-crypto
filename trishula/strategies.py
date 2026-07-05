"""Candidate deterministic strategies — the pool the backtest ranks.

Each strategy maps a candle series to a list of TARGET POSITIONS, one per bar,
in {-1 (short), 0 (flat), +1 (long)}. The position at bar i is entered on the
close of bar i and earns the return from close[i] -> close[i+1], so there is no
look-ahead: bar i's signal only uses data up to and including bar i.

Perps allow shorts, so strategies may go short. Set ``allow_short=False`` in the
backtest to force long/flat only.

This is the crypto analogue of Garuda's deterministic scorers. The backtester
tells us which of these actually beats buy-and-hold after costs — brains get
built only around the ones that earn their keep.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from .history import Candle
from . import indicators as ind


def _carry(raw: List[Optional[int]]) -> List[int]:
    """Resolve a signal list: None = hold previous position; start flat."""
    pos, cur = [], 0
    for s in raw:
        if s is not None:
            cur = s
        pos.append(cur)
    return pos


def ema_cross(fast: int = 12, slow: int = 48) -> Callable:
    def strat(candles: List[Candle]) -> List[int]:
        closes = [c.c for c in candles]
        ef, es = ind.ema(closes, fast), ind.ema(closes, slow)
        raw: List[Optional[int]] = []
        for i in range(len(closes)):
            if ef[i] is None or es[i] is None:
                raw.append(0)
            else:
                raw.append(1 if ef[i] > es[i] else -1)
        return _carry(raw)
    strat.__name__ = f"ema_cross_{fast}_{slow}"
    return strat


def rsi_reversion(period: int = 14, low: float = 30, high: float = 70) -> Callable:
    def strat(candles: List[Candle]) -> List[int]:
        closes = [c.c for c in candles]
        r = ind.rsi(closes, period)
        raw: List[Optional[int]] = []
        for i in range(len(closes)):
            if r[i] is None:
                raw.append(0)
            elif r[i] < low:
                raw.append(1)      # oversold -> long
            elif r[i] > high:
                raw.append(-1)     # overbought -> short
            else:
                raw.append(None)   # hold
        return _carry(raw)
    strat.__name__ = f"rsi_reversion_{period}"
    return strat


def donchian_breakout(period: int = 48) -> Callable:
    def strat(candles: List[Candle]) -> List[int]:
        highs = [c.h for c in candles]
        lows = [c.l for c in candles]
        closes = [c.c for c in candles]
        # channel uses the PRIOR `period` bars (exclude current) -> shift by 1
        hh = ind.rolling_max(highs, period)
        ll = ind.rolling_min(lows, period)
        raw: List[Optional[int]] = [0]
        for i in range(1, len(closes)):
            if hh[i - 1] is None or ll[i - 1] is None:
                raw.append(0)
            elif closes[i] > hh[i - 1]:
                raw.append(1)
            elif closes[i] < ll[i - 1]:
                raw.append(-1)
            else:
                raw.append(None)
        return _carry(raw)
    strat.__name__ = f"donchian_breakout_{period}"
    return strat


def bollinger_reversion(period: int = 20, k: float = 2.0) -> Callable:
    def strat(candles: List[Candle]) -> List[int]:
        closes = [c.c for c in candles]
        mid = ind.sma(closes, period)
        sd = ind.rolling_std(closes, period)
        raw: List[Optional[int]] = []
        for i in range(len(closes)):
            if mid[i] is None or sd[i] is None:
                raw.append(0)
                continue
            upper, lower = mid[i] + k * sd[i], mid[i] - k * sd[i]
            if closes[i] < lower:
                raw.append(1)          # below band -> revert up
            elif closes[i] > upper:
                raw.append(-1)         # above band -> revert down
            elif (raw and raw[-1] == 1 and closes[i] >= mid[i]) or \
                 (raw and raw[-1] == -1 and closes[i] <= mid[i]):
                raw.append(0)          # exit back at the mean
            else:
                raw.append(None)
        return _carry(raw)
    strat.__name__ = f"bollinger_reversion_{period}_{k}"
    return strat


def buy_hold() -> Callable:
    def strat(candles: List[Candle]) -> List[int]:
        return [1] * len(candles)
    strat.__name__ = "buy_hold"
    return strat


def default_pool() -> List[Callable]:
    """The strategies the backtest ranks by default."""
    return [
        ema_cross(12, 48),
        ema_cross(9, 21),
        rsi_reversion(14, 30, 70),
        donchian_breakout(48),
        donchian_breakout(96),
        bollinger_reversion(20, 2.0),
    ]


def by_name(name: str) -> Callable:
    """Return a strategy builder-instance from the pool by its __name__."""
    pool = {s.__name__: s for s in default_pool()}
    if name not in pool:
        raise KeyError(f"unknown strategy {name!r}; choices: {sorted(pool)}")
    return pool[name]
