#!/usr/bin/env python3
"""Backtest Donchian-1h with different hard stop-loss levels.

Applies the SAME stop rule as the live engine (cut a position when its unrealised
loss exceeds the stop %; block re-entry in that direction until the signal flips)
and compares stop levels head-to-head on the traded universe, so we can pick the
level that actually improves the edge instead of guessing.

Run on the droplet (real data):
    python3 scripts/backtest_stops.py --top 15 --days 365 --stops 0,5,8,12,15
Offline check:
    python3 scripts/backtest_stops.py --synthetic

Coarse close-to-close model (matches the hourly engine). Paper. Not advice.
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    from trishula import history, strategies, backtest
    from trishula.delta_client import DeltaClient, DeltaError
except ModuleNotFoundError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from trishula import history, strategies, backtest
    from trishula.delta_client import DeltaClient, DeltaError

MAJORS = ["BTCUSD", "ETHUSD", "SOLUSD"]
DONCHIAN = 48


def apply_stop(candles, sig, stop_pct):
    """Turn a raw signal series into the ACTUAL position series a hard stop would
    produce — same rules as the live engine."""
    closes = [c.c for c in candles]
    out = []
    cur, entry, stopped = 0, 0.0, 0
    for i in range(len(closes)):
        price, target = closes[i], sig[i]
        # 1) stop-loss on the current holding
        if stop_pct > 0 and cur != 0 and entry:
            upnl = cur * (price / entry - 1) * 100
            if upnl <= -stop_pct:
                stopped, cur, entry = cur, 0, 0.0
                out.append(0)
                continue
        # 2) block re-entry in the direction we were stopped out of…
        if target != 0 and stopped == target:
            out.append(cur)
            continue
        # 3) …clear the block once the signal changes
        if stopped != 0 and target != stopped:
            stopped = 0
        # 4) apply the target
        if target != cur:
            cur = target
            entry = price if cur != 0 else 0.0
        out.append(cur)
    return out


def get_universe(top_n, synthetic):
    if synthetic:
        return (MAJORS + [f"ALT{i}USD" for i in range(top_n)])[:top_n]
    try:
        cl = DeltaClient()
        prods = cl.get_products()
        perps = [p for p in prods if p.get("contract_type") == "perpetual_futures"
                 and p.get("state") == "live"]
        ticks = cl.get_tickers()
        vol = {}
        for t in ticks:
            s = t.get("symbol")
            v = t.get("turnover_usd") or t.get("turnover") or t.get("volume") or 0
            try:
                vol[s] = float(v)
            except (TypeError, ValueError):
                vol[s] = 0.0
        syms = [p["symbol"] for p in perps if p.get("symbol") in vol]
        return sorted(syms, key=lambda s: vol.get(s, 0), reverse=True)[:top_n]
    except Exception:
        return MAJORS


def candles_for(sym, days, synthetic):
    if synthetic:
        return history.synthetic_candles(n=min(days * 24, 4000),
                                         seed=abs(hash(sym)) % 999 + 1, resolution="1h")
    try:
        return history.fetch_candles(sym, "1h", days, use_cache=True)
    except DeltaError:
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest Donchian-1h stop-loss levels")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--stops", default="0,5,8,12,15")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    stops = [float(x) for x in args.stops.split(",") if x.strip() != ""]
    syms = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if args.symbols else get_universe(args.top, args.synthetic))

    print(f"\nSTOP-LEVEL BACKTEST · donchian_breakout_{DONCHIAN} · 1h · {args.days}d")
    print(f"symbols: {len(syms)} · stops: {stops}")

    # fetch once per symbol; reuse across stop levels
    data = {}
    all_real = True
    for s in syms:
        c = candles_for(s, args.days, args.synthetic)
        if len(c) > DONCHIAN + 50:
            data[s] = c
        all_real = all_real and not args.synthetic
    if not data:
        print("no data.")
        return 1

    don = strategies.donchian_breakout(DONCHIAN)
    agg = {st: {"net": [], "edge": [], "sharpe": [], "dd": [], "trades": 0, "wr": []}
           for st in stops}
    for s, c in data.items():
        raw = don(c)
        bench = backtest.run_backtest(c, [1] * len(c), resolution="1h")["net_return_pct"]
        for st in stops:
            pos = apply_stop(c, raw, st)
            r = backtest.run_backtest(c, pos, resolution="1h")
            a = agg[st]
            a["net"].append(r["net_return_pct"])
            a["edge"].append(r["net_return_pct"] - bench)
            a["sharpe"].append(r["sharpe"])
            a["dd"].append(r["max_drawdown_pct"])
            a["trades"] += r["trades"]
            if r["win_rate_pct"] is not None:
                a["wr"].append(r["win_rate_pct"])

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    tag = "SYNTHETIC — NOT a real edge" if args.synthetic else "REAL Delta data"
    print("\n" + "=" * 72)
    print(f"  STOP LEVELS · avg over {len(data)} coins   ({tag} · paper · not advice)")
    print("=" * 72)
    print(f"  {'stop':>6} {'net':>9} {'edge':>9} {'sharpe':>7} {'maxDD':>8} {'trades':>7} {'win':>5}")
    print("  " + "-" * 68)
    rows = []
    for st in stops:
        a = agg[st]
        row = {"stop": st, "net": mean(a["net"]), "edge": mean(a["edge"]),
               "sharpe": mean(a["sharpe"]), "dd": mean(a["dd"]),
               "trades": a["trades"], "wr": mean(a["wr"])}
        rows.append(row)
        label = "none" if st == 0 else f"{st:.0f}%"
        print(f"  {label:>6} {row['net']:>+8.1f}% {row['edge']:>+8.1f}% {row['sharpe']:>7.2f} "
              f"{row['dd']:>7.1f}% {row['trades']:>7} {row['wr']:>4.0f}%")
    print("=" * 72)

    best_edge = max(rows, key=lambda r: r["edge"])
    best_dd = max(rows, key=lambda r: r["dd"])   # dd is negative; max = least-bad
    print(f"  best edge : {('no stop' if best_edge['stop']==0 else str(best_edge['stop'])+'%')} "
          f"({best_edge['edge']:+.1f}% vs hold)")
    print(f"  best maxDD: {('no stop' if best_dd['stop']==0 else str(best_dd['stop'])+'%')} "
          f"({best_dd['dd']:.1f}%)")
    print("  Read: pick the stop that keeps most of the edge while cutting maxDD.")
    if args.synthetic:
        print("  NOTE: synthetic — run on the droplet for real Delta numbers.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
