"""Historical OHLCV candles for backtesting.

Real data source: Delta Exchange India public endpoint

    GET /v2/history/candles?symbol=BTCUSD&resolution=1h&start=<unix_s>&end=<unix_s>

No auth required. Delta caps each response (~2000 candles), so we page
backwards in time. Candles are cached to data/candles/<symbol>_<res>.csv so a
backtest re-runs offline without re-hitting the API.

A ``synthetic_candles`` generator is provided so the backtester can be validated
anywhere (e.g. this build container) without network access. Synthetic results
are clearly labelled and must never be read as a real edge.
"""
from __future__ import annotations

import csv
import math
import os
import time
from collections import namedtuple
from typing import List, Optional

from .delta_client import DeltaClient, DeltaError

Candle = namedtuple("Candle", "t o h l c v")

# seconds per candle for each Delta resolution
RES_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
    "1d": 86400, "1w": 604800, "2w": 1209600, "7d": 604800, "30d": 2592000,
}

# candles per year, for annualising Sharpe/vol
PERIODS_PER_YEAR = {k: (365 * 86400) / v for k, v in RES_SECONDS.items()}

CACHE_DIR = os.path.join("data", "candles")


def _cache_path(symbol: str, resolution: str) -> str:
    return os.path.join(CACHE_DIR, f"{symbol}_{resolution}.csv")


def save_cache(symbol: str, resolution: str, candles: List[Candle]) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(symbol, resolution)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "o", "h", "l", "c", "v"])
        for c in candles:
            w.writerow([c.t, c.o, c.h, c.l, c.c, c.v])
    return path


def load_cache(symbol: str, resolution: str) -> Optional[List[Candle]]:
    path = _cache_path(symbol, resolution)
    if not os.path.exists(path):
        return None
    out = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            out.append(Candle(int(row["t"]), float(row["o"]), float(row["h"]),
                              float(row["l"]), float(row["c"]), float(row["v"])))
    return out or None


def fetch_candles(
    symbol: str,
    resolution: str = "1h",
    days: int = 120,
    client: Optional[DeltaClient] = None,
    use_cache: bool = True,
) -> List[Candle]:
    """Fetch ``days`` of candles for ``symbol`` at ``resolution`` from Delta.

    Pages backwards until the window is covered. Caches on success.
    Raises DeltaError on failure (caller may fall back to synthetic/cache).
    """
    if resolution not in RES_SECONDS:
        raise ValueError(f"Unsupported resolution {resolution!r}")
    if use_cache:
        cached = load_cache(symbol, resolution)
        if cached:
            return cached

    client = client or DeltaClient()
    step = RES_SECONDS[resolution]
    end = int(time.time())
    start = end - days * 86400
    chunk = 1800 * step  # ~1800 candles per request, safely under the cap

    by_t = {}
    cursor_end = end
    guard = 0
    while cursor_end > start and guard < 200:
        guard += 1
        cursor_start = max(start, cursor_end - chunk)
        params = {"symbol": symbol, "resolution": resolution,
                  "start": cursor_start, "end": cursor_end}
        data = client._request("GET", "/v2/history/candles", params=params)
        rows = data.get("result", []) if isinstance(data, dict) else []
        if not rows:
            break
        for r in rows:
            t = int(r["time"])
            by_t[t] = Candle(t, float(r["open"]), float(r["high"]),
                             float(r["low"]), float(r["close"]), float(r.get("volume", 0)))
        oldest = min(int(r["time"]) for r in rows)
        if oldest >= cursor_end:  # no progress, avoid infinite loop
            break
        cursor_end = oldest - step

    candles = [by_t[t] for t in sorted(by_t)]
    if not candles:
        raise DeltaError(f"No candles returned for {symbol} {resolution}")
    save_cache(symbol, resolution, candles)
    return candles


def synthetic_candles(
    n: int = 2000,
    seed: int = 1,
    start_price: float = 60000.0,
    drift: float = 0.02,
    vol: float = 0.9,
    resolution: str = "1h",
) -> List[Candle]:
    """Deterministic GBM-ish candles for OFFLINE validation only.

    NOT market data. Uses a seeded LCG so results are reproducible and no
    Math.random-style nondeterminism leaks into the backtest.
    """
    step = RES_SECONDS.get(resolution, 3600)
    ppy = PERIODS_PER_YEAR.get(resolution, 8760)
    mu = drift / ppy
    sigma = vol / math.sqrt(ppy)

    state = seed & 0xFFFFFFFF
    def rnd():  # LCG -> uniform(0,1)
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF

    def gauss():  # Box-Muller
        u1 = max(1e-9, rnd())
        return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * rnd())

    out = []
    price = start_price
    t0 = 1_700_000_000
    for i in range(n):
        ret = mu + sigma * gauss()
        o = price
        c = price * (1 + ret)
        hi = max(o, c) * (1 + abs(gauss()) * sigma * 0.4)
        lo = min(o, c) * (1 - abs(gauss()) * sigma * 0.4)
        out.append(Candle(t0 + i * step, o, hi, lo, c, 1000 + rnd() * 500))
        price = c
    return out
