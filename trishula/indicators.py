"""Pure-Python technical indicators (no numpy dependency, easy to unit-test).

All return a list the same length as the input, with ``None`` during the
warm-up period so callers never index past-the-warmup by accident.
"""
from __future__ import annotations

import math
from typing import List, Optional


def sma(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0:
        return out
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= period:
            run -= values[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def ema(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0 or not values:
        return out
    k = 2.0 / (period + 1)
    prev = None
    for i, v in enumerate(values):
        if i == period - 1:
            prev = sum(values[:period]) / period  # seed with SMA
            out[i] = prev
        elif i >= period:
            prev = v * k + prev * (1 - k)
            out[i] = prev
    return out


def rsi(values: List[float], period: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) <= period:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / period, losses / period
    def rsi_val(g, l):
        if l == 0:
            return 100.0
        rs = g / l
        return 100.0 - 100.0 / (1 + rs)
    out[period] = rsi_val(avg_g, avg_l)
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
        out[i] = rsi_val(avg_g, avg_l)
    return out


def rolling_std(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        if i >= period - 1:
            window = values[i - period + 1:i + 1]
            m = sum(window) / period
            var = sum((x - m) ** 2 for x in window) / period
            out[i] = math.sqrt(var)
    return out


def rolling_max(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        if i >= period - 1:
            out[i] = max(values[i - period + 1:i + 1])
    return out


def rolling_min(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        if i >= period - 1:
            out[i] = min(values[i - period + 1:i + 1])
    return out
