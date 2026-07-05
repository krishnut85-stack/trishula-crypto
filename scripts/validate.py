#!/usr/bin/env python3
"""Robustness check for ONE strategy — is the edge real or a lucky window?

Stress-tests a strategy three ways:
  1. Across TIMEFRAMES (15m / 1h / 4h / 1d) over the same lookback.
  2. Across contiguous SUB-WINDOWS (out-of-sample-ish): splits each timeframe
     into N equal segments and checks how many segments still beat buy-hold.
  3. With FUNDING cost added (perps pay funding every 8h) to see the drag.

A single 180d backtest can win just because that window was a downtrend. If the
edge survives other timeframes AND most sub-windows AND funding, it is far more
believable. Still coarse (close-to-close, constant funding approx) and NOT advice.

Run on the droplet (real Delta candles):
    python3 scripts/validate.py --strategy donchian_breakout_48 \
        --symbols BTCUSD,ETHUSD,SOLUSD --days 365 --funding 1.0

Offline engine check:
    python3 scripts/validate.py --synthetic
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    from trishula import history, strategies, backtest
    from trishula.delta_client import DeltaError
except ModuleNotFoundError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from trishula import history, strategies, backtest
    from trishula.delta_client import DeltaError


def get_candles(symbol, resolution, days, synthetic):
    if synthetic:
        seed = abs(hash(symbol + resolution)) % 9999 + 1
        return history.synthetic_candles(n=min(days * 24, 4000), seed=seed,
                                         resolution=resolution), False
    try:
        return history.fetch_candles(symbol, resolution, days, use_cache=False), True
    except Exception as exc:  # noqa: BLE001
        print(f"    ! {symbol} {resolution}: fetch failed ({exc}); SYNTHETIC")
        seed = abs(hash(symbol + resolution)) % 9999 + 1
        return history.synthetic_candles(n=min(days * 24, 4000), seed=seed,
                                         resolution=resolution), False


def edge_on(candles, strat, resolution, cost, funding, allow_short):
    b = backtest.backtest_strategy(candles, strategies.buy_hold(), resolution=resolution,
                                   cost_per_side_pct=cost, allow_short=allow_short,
                                   funding_bps_per_8h=funding)
    r = backtest.backtest_strategy(candles, strat, resolution=resolution,
                                   cost_per_side_pct=cost, allow_short=allow_short,
                                   funding_bps_per_8h=funding)
    return {"net": r["net_return_pct"], "hold": b["net_return_pct"],
            "edge": r["net_return_pct"] - b["net_return_pct"],
            "trades": r["trades"], "sharpe": r["sharpe"]}


def segment_edges(candles, strat, resolution, cost, funding, allow_short, segments):
    n = len(candles)
    size = n // segments
    out = []
    for k in range(segments):
        lo = k * size
        hi = n if k == segments - 1 else (k + 1) * size
        chunk = candles[lo:hi]
        if len(chunk) < 60:
            continue
        out.append(edge_on(chunk, strat, resolution, cost, funding, allow_short))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate one strategy's robustness")
    ap.add_argument("--strategy", default="donchian_breakout_48")
    ap.add_argument("--symbols", default="BTCUSD,ETHUSD,SOLUSD")
    ap.add_argument("--resolutions", default="15m,1h,4h,1d")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--segments", type=int, default=4)
    ap.add_argument("--cost", type=float, default=backtest.DEFAULT_COST_PER_SIDE_PCT)
    ap.add_argument("--funding", type=float, default=1.0,
                    help="funding drag, bps of notional per 8h (perps). Default 1.0")
    ap.add_argument("--no-short", action="store_true")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    try:
        strat = strategies.by_name(args.strategy)
    except KeyError as exc:
        print(exc)
        return 2

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    resolutions = [r.strip() for r in args.resolutions.split(",") if r.strip()]
    allow_short = not args.no_short

    print(f"\nVALIDATE · {args.strategy} · {args.days}d · {args.segments} segments/TF")
    print(f"symbols {', '.join(symbols)} · funding {args.funding} bps/8h · "
          f"short={'off' if args.no_short else 'on'}")

    all_real = True
    tf_rows = []   # per timeframe: aggregated over symbols
    for res in resolutions:
        agg_full_nofund, agg_full, seg_beat, seg_total, seg_pos = [], [], 0, 0, 0
        for symbol in symbols:
            candles, real = get_candles(symbol, res, args.days, args.synthetic)
            all_real = all_real and real
            if len(candles) < 120:
                continue
            full0 = edge_on(candles, strat, res, args.cost, 0.0, allow_short)
            full = edge_on(candles, strat, res, args.cost, args.funding, allow_short)
            agg_full_nofund.append(full0["edge"])
            agg_full.append(full)
            for s in segment_edges(candles, strat, res, args.cost, args.funding,
                                   allow_short, args.segments):
                seg_total += 1
                if s["edge"] > 0:
                    seg_beat += 1
                if s["net"] > 0:
                    seg_pos += 1
        if not agg_full:
            continue

        def mean(xs):
            return sum(xs) / len(xs) if xs else 0.0
        tf_rows.append({
            "res": res,
            "edge_nofund": mean(agg_full_nofund),
            "edge": mean([a["edge"] for a in agg_full]),
            "net": mean([a["net"] for a in agg_full]),
            "hold": mean([a["hold"] for a in agg_full]),
            "trades": sum(a["trades"] for a in agg_full),
            "seg_beat": seg_beat, "seg_total": seg_total, "seg_pos": seg_pos,
        })

    tag = "REAL Delta data" if all_real else "SYNTHETIC (offline) — NOT a real edge"
    print("\n" + "=" * 76)
    print(f"  ROBUSTNESS · {args.strategy}   ({tag} · paper · not advice)")
    print("=" * 76)
    print(f"  {'tf':>4} {'edge(fund)':>11} {'edge(no)':>9} {'net':>8} {'hold':>8} "
          f"{'trades':>7} {'segs>hold':>10}")
    print("  " + "-" * 72)
    robust_tfs = 0
    for r in tf_rows:
        seg = f"{r['seg_beat']}/{r['seg_total']}"
        robust = r["edge"] > 0 and r["seg_total"] and r["seg_beat"] >= (r["seg_total"] + 1) // 2
        if robust:
            robust_tfs += 1
        flag = "✓" if robust else " "
        print(f"  {r['res']:>4} {r['edge']:>+10.1f}% {r['edge_nofund']:>+8.1f}% "
              f"{r['net']:>+7.1f}% {r['hold']:>+7.1f}% {r['trades']:>7} {seg:>10} {flag}")
    print("=" * 76)

    n = len(tf_rows)
    if n == 0:
        print("  no data.\n")
        return 1
    tot_beat = sum(r["seg_beat"] for r in tf_rows)
    tot_seg = sum(r["seg_total"] for r in tf_rows)
    if robust_tfs == n and robust_tfs > 0:
        verdict = "ROBUST — edge survives every timeframe and most sub-windows."
    elif robust_tfs >= (n + 1) // 2:
        verdict = "MIXED — holds on some timeframes/windows, not all. Promising, keep testing."
    else:
        verdict = "REGIME-DEPENDENT — the edge mostly vanishes off the original window. Do NOT trust it."
    print(f"  Timeframes robust : {robust_tfs}/{n}")
    print(f"  Sub-windows beating hold : {tot_beat}/{tot_seg}")
    print(f"  VERDICT: {verdict}")
    if not all_real:
        print("  NOTE: synthetic — re-run on the droplet for real Delta candles.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
