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


def rsi2(low: float = 10, high: float = 90, trend_ma: int = 200) -> Callable:
    """Connors-style RSI(2) mean-reversion with an optional trend filter.

    Long when RSI(2) < low AND price is above the trend MA (buy dips in an
    uptrend); short when RSI(2) > high AND price is below the trend MA (fade rips
    in a downtrend); exit to flat when RSI crosses back past the 50 midline.
    ``trend_ma=0`` disables the filter (pure symmetric RSI-2 reversion).
    """
    def strat(candles: List[Candle]) -> List[int]:
        closes = [c.c for c in candles]
        r = ind.rsi(closes, 2)
        ma = ind.sma(closes, trend_ma) if trend_ma > 0 else [None] * len(closes)
        pos: List[int] = []
        cur = 0
        for i in range(len(closes)):
            if r[i] is None:
                pos.append(0)
                continue
            up = trend_ma == 0 or (ma[i] is not None and closes[i] > ma[i])
            dn = trend_ma == 0 or (ma[i] is not None and closes[i] < ma[i])
            if cur <= 0 and r[i] < low and up:
                cur = 1
            elif cur >= 0 and r[i] > high and dn:
                cur = -1
            elif cur == 1 and r[i] > 50:
                cur = 0
            elif cur == -1 and r[i] < 50:
                cur = 0
            pos.append(cur)
        return pos
    strat.__name__ = f"rsi2_{int(low)}_{int(high)}_ma{trend_ma}"
    return strat


def ts_momentum(lookback: int = 720) -> Callable:
    """Time-series momentum: long if price is above its value ``lookback`` bars
    ago, short otherwise. The single-asset momentum the research calls robust.
    On 1h bars, 168 ~ 7 days, 720 ~ 30 days.
    """
    def strat(candles: List[Candle]) -> List[int]:
        closes = [c.c for c in candles]
        out: List[int] = []
        for i in range(len(closes)):
            if i < lookback:
                out.append(0)
            else:
                out.append(1 if closes[i] > closes[i - lookback] else -1)
        return out
    strat.__name__ = f"ts_momentum_{lookback}"
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
        ts_momentum(168),
        ts_momentum(720),
        rsi2(10, 90, 200),   # Connors RSI-2 with trend filter
        rsi2(10, 90, 0),     # pure RSI-2 reversion, no filter
    ]


def by_name(name: str) -> Callable:
    """Return a strategy builder-instance from the pool by its __name__."""
    pool = {s.__name__: s for s in default_pool()}
    if name not in pool:
        raise KeyError(f"unknown strategy {name!r}; choices: {sorted(pool)}")
    return pool[name]
