#!/usr/bin/env python3
"""TRISHULA QUANT — two lanes on the real Delta India universe.

  Lane A (Quant / portfolio) : cross-sectional momentum over the top-N perps,
                               long-only, weekly rebalance, BTC-regime filter.
  Lane B (Trend)             : Donchian-1h breakout on the majors (our validated
                               single-asset trend strategy, short-capable).

Reuses Trishula's PROVEN Delta fetch (history.fetch_candles) — no untested schema.
Reports full-period AND out-of-sample (last 30%) so you aren't fooled by fitting.

Run on the droplet (real data):
    python3 scripts/run_quant.py --top 40 --days 1095
Offline engine check:
    python3 scripts/run_quant.py --synthetic

Paper only. Coarse model. NOT advice, NOT a performance promise.
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys

try:
    from trishula import history, strategies, backtest, portfolio
    from trishula.delta_client import DeltaClient, DeltaError
except ModuleNotFoundError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from trishula import history, strategies, backtest, portfolio
    from trishula.delta_client import DeltaClient, DeltaError

MAJORS = ["BTCUSD", "ETHUSD", "SOLUSD"]


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
    return syms


def synth_universe(top_n):
    syms = MAJORS + [f"ALT{i}USD" for i in range(top_n)]
    out = {}
    for k, s in enumerate(syms[:top_n]):
        out[s] = history.synthetic_candles(n=1100, seed=k + 1,
                                           drift=0.02 + (k % 5) * 0.15,
                                           vol=0.8, resolution="1d")
    return out


def stats_from_curve(curve, ppy=365.0):
    if len(curve) < 30:
        return {"cagr_pct": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0}
    rets = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve)) if curve[i - 1]]
    yrs = len(rets) / ppy
    cagr = (curve[-1] ** (1 / yrs) - 1) if curve[-1] > 0 and yrs > 0 else -1.0
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    sharpe = (statistics.mean(rets) / sd * (ppy ** 0.5)) if sd > 1e-9 else 0.0
    peak, mdd = curve[0], 0.0
    for e in curve:
        peak = max(peak, e)
        if peak > 0:
            mdd = min(mdd, (e - peak) / peak)
    return {"cagr_pct": cagr * 100, "sharpe": sharpe, "max_dd_pct": mdd * 100}


def oos_tail(curve, frac=0.3):
    cut = int(len(curve) * (1 - frac))
    tail = curve[cut:]
    if len(tail) < 20:
        return {"cagr_pct": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0}
    base = tail[0] or 1.0
    return stats_from_curve([x / base for x in tail])


def line(name, full, oos):
    print(f"  {name:26} {full['sharpe']:>6.2f} {full['cagr_pct']:>+8.1f}% "
          f"{full['max_dd_pct']:>7.1f}%   {oos['sharpe']:>6.2f} {oos['cagr_pct']:>+8.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description="Trishula Quant — momentum + trend lanes")
    ap.add_argument("--top", type=int, default=40, help="universe size (top-N perps)")
    ap.add_argument("--days", type=int, default=365 * 3)
    ap.add_argument("--lookback", type=int, default=56, help="momentum lookback (daily bars)")
    ap.add_argument("--top-frac", type=float, default=0.2)
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    print(f"\nTRISHULA QUANT · universe top-{args.top} · {args.days}d daily · paper · not advice")

    # -------- fetch universe daily candles --------
    if args.synthetic:
        candles = synth_universe(args.top)
        real = False
    else:
        try:
            client = DeltaClient()
            syms = get_universe(client, args.top)
            print(f"universe: {len(syms)} live perps by 24h volume")
            candles, real = {}, True
            for i, s in enumerate(syms):
                try:
                    c = history.fetch_candles(s, "1d", args.days, client=client, use_cache=True)
                    if len(c) > 120:
                        candles[s] = c
                except DeltaError:
                    pass
                if (i + 1) % 20 == 0:
                    print(f"  fetched {i + 1}/{len(syms)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! universe fetch failed ({exc}); using SYNTHETIC")
            candles, real = synth_universe(args.top), False

    if len(candles) < 6:
        print("  not enough symbols with history; aborting.")
        return 1

    tag = "REAL Delta data" if real else "SYNTHETIC (offline) — NOT a real edge"

    # -------- Lane A: portfolio momentum --------
    mom_gate = portfolio.cross_sectional_momentum(
        candles, lookback=args.lookback, top_frac=args.top_frac, use_regime=True,
        label="xs_momentum + regime")
    mom_raw = portfolio.cross_sectional_momentum(
        candles, lookback=args.lookback, top_frac=args.top_frac, use_regime=False,
        label="xs_momentum (no regime)")

    # benchmarks
    btc = candles.get("BTCUSD") or next(iter(candles.values()))
    btc_closes = [c.c for c in btc]
    btc_curve, e = [1.0], 1.0
    for i in range(1, len(btc_closes)):
        e *= btc_closes[i] / btc_closes[i - 1]
        btc_curve.append(e)

    print("\n" + "=" * 78)
    print(f"  TRISHULA QUANT · LANE A (portfolio momentum)   ({tag})")
    print("=" * 78)
    print(f"  {'strategy':26} {'Sharpe':>6} {'CAGR':>9} {'MaxDD':>8}   {'OOS Shp':>7} {'OOS CAGR':>9}")
    print("  " + "-" * 74)
    for m in (mom_gate, mom_raw):
        if "error" in m:
            print(f"  {m['label']:26} {m['error']}")
            continue
        line(m["label"], m, oos_tail(m["equity_curve"]))
    line("buy_hold_BTC", stats_from_curve(btc_curve), oos_tail(btc_curve))
    print("=" * 78)

    # -------- Lane B: Donchian-1h trend on majors --------
    print(f"\n  TRISHULA QUANT · LANE B (Donchian-1h trend, majors)   ({tag})")
    print("  " + "-" * 74)
    print(f"  {'symbol':10} {'net':>9} {'hold':>9} {'edge':>9} {'trades':>7}")
    if args.synthetic:
        maj = {s: history.synthetic_candles(n=3000, seed=hash(s) % 999 + 1, resolution="1h")
               for s in MAJORS}
    else:
        maj = {}
        for s in MAJORS:
            try:
                maj[s] = history.fetch_candles(s, "1h", min(args.days, 365), use_cache=True)
            except DeltaError:
                pass
    don = strategies.donchian_breakout(48)
    for s, c in maj.items():
        if len(c) < 200:
            continue
        r = backtest.backtest_strategy(c, don, resolution="1h")
        b = backtest.backtest_strategy(c, strategies.buy_hold(), resolution="1h")
        edge = r["net_return_pct"] - b["net_return_pct"]
        print(f"  {s:10} {r['net_return_pct']:>+8.1f}% {b['net_return_pct']:>+8.1f}% "
              f"{edge:>+8.1f}% {r['trades']:>7}")
    print("  " + "-" * 74)

    print("\n  Read: Lane A = which coins to hold (momentum, risk-off to cash in bear).")
    print("        Lane B = short-capable trend on majors (profits when they fall).")
    print("        Two engines, different jobs — the coordinator blends them next.")
    if not real:
        print("  NOTE: SYNTHETIC — re-run on the droplet for real Delta numbers.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
