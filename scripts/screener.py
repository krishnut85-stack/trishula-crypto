#!/usr/bin/env python3
"""Universe screener — every Delta India perp, ranked by momentum.

Shows the full top-N universe with 24h volume, liquidity-scaled slippage, and
trailing momentum over 4 / 8 / 26 weeks. Flags the coins the cross-sectional
momentum strategy would currently hold (top fraction by 8-week momentum), and
whether the BTC regime filter is risk-on or risk-off. Writes the full table to
universe_screener.csv.

Run on the droplet:
    python3 scripts/screener.py --top 200 --days 400 --sort mom8w
Offline check:
    python3 scripts/screener.py --synthetic

Paper research tool. Not advice.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

try:
    from trishula import history, portfolio
    from trishula.delta_client import DeltaClient, DeltaError
except ModuleNotFoundError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from trishula import history, portfolio
    from trishula.delta_client import DeltaClient, DeltaError

MAJORS = ["BTCUSD", "ETHUSD", "SOLUSD"]
CSV_PATH = "universe_screener.csv"


def get_universe(client, top_n):
    prods = client.get_products()
    perps = [p for p in prods if p.get("contract_type") == "perpetual_futures"
             and p.get("state") == "live"]
    ticks = client.get_tickers()
    vol = {}
    for t in ticks:
        sym = t.get("symbol")
        v = t.get("turnover_usd") or t.get("turnover") or t.get("volume") or 0
        try:
            vol[sym] = float(v)
        except (TypeError, ValueError):
            vol[sym] = 0.0
    syms = [p["symbol"] for p in perps if p.get("symbol") in vol]
    syms = sorted(syms, key=lambda s: vol.get(s, 0), reverse=True)[:top_n]
    return syms, {s: vol.get(s, 0) / 1e6 for s in syms}


def synth_universe(top_n):
    syms = (MAJORS + [f"ALT{i}USD" for i in range(top_n)])[:top_n]
    out, volmn = {}, {}
    for k, s in enumerate(syms):
        out[s] = history.synthetic_candles(n=400, seed=k + 1,
                                           drift=0.05 + (k % 7 - 3) * 0.2,
                                           vol=0.8, resolution="1d")
        volmn[s] = max(0.1, 5000.0 / (k + 1))
    return out, volmn


def mom(closes, weeks):
    n = weeks * 7
    if len(closes) > n:
        return (closes[-1] / closes[-n - 1] - 1) * 100
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Trishula universe screener")
    ap.add_argument("--top", type=int, default=200)
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--top-frac", type=float, default=0.2)
    ap.add_argument("--sort", default="mom8w",
                    choices=["mom8w", "mom4w", "mom26w", "vol"])
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    if args.synthetic:
        candles, volmn = synth_universe(args.top)
        real = False
    else:
        try:
            client = DeltaClient()
            syms, volmn = get_universe(client, args.top)
            print(f"universe: {len(syms)} live perps; fetching daily candles...")
            candles, real = {}, True
            for i, s in enumerate(syms):
                try:
                    c = history.fetch_candles(s, "1d", args.days, client=client, use_cache=True)
                    if len(c) > 40:
                        candles[s] = c
                except DeltaError:
                    pass
                if (i + 1) % 25 == 0:
                    print(f"  fetched {i + 1}/{len(syms)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! fetch failed ({exc}); using SYNTHETIC")
            candles, volmn, real = *synth_universe(args.top), False

    if not candles:
        print("no data.")
        return 1

    cost = portfolio.liquidity_slippage({s: volmn.get(s, 0.1) for s in candles})

    # BTC regime (risk-on if above its ~20wk MA)
    btc = candles.get("BTCUSD")
    regime_on = True
    if btc:
        bc = [c.c for c in btc]
        if len(bc) > 140:
            regime_on = bc[-1] > sum(bc[-140:]) / 140

    rows = []
    for s, c in candles.items():
        closes = [x.c for x in c]
        rows.append({
            "symbol": s,
            "vol_mn": round(volmn.get(s, 0), 1),
            "slip_pct": round(cost.get(s, 0), 3),
            "last": closes[-1],
            "mom4w": mom(closes, 4),
            "mom8w": mom(closes, 8),
            "mom26w": mom(closes, 26),
        })

    key = {"mom8w": "mom8w", "mom4w": "mom4w", "mom26w": "mom26w", "vol": "vol_mn"}[args.sort]
    rows.sort(key=lambda r: (r[key] is None, -(r[key] if r[key] is not None else -1e9)))

    # which coins the strategy would hold now (top-frac by 8w momentum, if risk-on)
    ranked8 = [r for r in rows if r["mom8w"] is not None]
    ranked8.sort(key=lambda r: r["mom8w"], reverse=True)
    k = max(1, int(len(ranked8) * args.top_frac))
    held = {r["symbol"] for r in ranked8[:k]} if regime_on else set()
    for r in rows:
        r["held"] = "Y" if r["symbol"] in held else ""

    tag = "REAL Delta data" if real else "SYNTHETIC (offline)"
    print("\n" + "=" * 66)
    print(f"  TRISHULA SCREENER · {len(rows)} coins · sort={args.sort} · ({tag})")
    print(f"  regime: {'RISK-ON (holding)' if regime_on else 'RISK-OFF (all cash)'} "
          f"· would hold top {int(args.top_frac*100)}% = {len(held)} coins")
    print("=" * 66)
    print(f"  {'#':>3} {'symbol':12} {'vol$mn':>8} {'slip%':>6} "
          f"{'4w%':>7} {'8w%':>7} {'26w%':>7} {'hold':>4}")
    print("  " + "-" * 62)

    def f(v):
        return f"{v:+.0f}" if isinstance(v, (int, float)) else "  n/a"
    for i, r in enumerate(rows, 1):
        print(f"  {i:>3} {r['symbol']:12} {r['vol_mn']:>8.1f} {r['slip_pct']:>6.3f} "
              f"{f(r['mom4w']):>7} {f(r['mom8w']):>7} {f(r['mom26w']):>7} {r['held']:>4}")
    print("=" * 66)

    with open(CSV_PATH, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["symbol", "vol_mn", "slip_pct", "last",
                                           "mom4w", "mom8w", "mom26w", "held"])
        w.writeheader()
        w.writerows(rows)
    print(f"  saved {CSV_PATH} ({len(rows)} rows) · 'hold' = strategy would own it now")
    if not real:
        print("  NOTE: SYNTHETIC — re-run on the droplet for real Delta numbers.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
