#!/usr/bin/env python3
"""Rank the strategy pool by honest, cost-aware edge vs buy-and-hold.

Answers the Garuda question for crypto: *which strategy is actually working?*
With --dashboard it also writes the live cockpit (dashboard/cockpit.html) and
dashboard/data.json from the REAL results.

Real data + dashboard (on the droplet):
    python3 scripts/run_backtest.py --symbols BTCUSD,ETHUSD --resolution 1h --days 180 --dashboard

Offline validation (no network — synthetic candles, clearly labelled):
    python3 scripts/run_backtest.py --synthetic --dashboard

Rollback: read-only except the two dashboard files it writes when --dashboard is
passed (dashboard/cockpit.html, dashboard/data.json) and cached candles in
data/candles/. All can be regenerated or deleted freely.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

try:
    from trishula import history, strategies, backtest, scorecard, report
    from trishula.delta_client import DeltaError
except ModuleNotFoundError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from trishula import history, strategies, backtest, scorecard, report
    from trishula.delta_client import DeltaError

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH_HTML = os.path.join(HERE, "dashboard", "cockpit.html")
DASH_JSON = os.path.join(HERE, "dashboard", "data.json")


def get_candles(symbol, resolution, days, synthetic):
    if synthetic:
        seed = abs(hash(symbol)) % 9999 + 1
        return history.synthetic_candles(n=min(days * 24, 3000), seed=seed,
                                         resolution=resolution), False
    try:
        return history.fetch_candles(symbol, resolution, days), True
    except Exception as exc:  # noqa: BLE001 - fall back gracefully
        print(f"  ! {symbol}: live fetch failed ({exc}); using SYNTHETIC instead")
        seed = abs(hash(symbol)) % 9999 + 1
        return history.synthetic_candles(n=min(days * 24, 3000), seed=seed,
                                         resolution=resolution), False


def main() -> int:
    ap = argparse.ArgumentParser(description="Trishula strategy backtest leaderboard")
    ap.add_argument("--symbols", default="BTCUSD,ETHUSD,SOLUSD")
    ap.add_argument("--resolution", default="1h")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--cost", type=float, default=backtest.DEFAULT_COST_PER_SIDE_PCT,
                    help="cost per side %% (fee + GST). Default ~0.059")
    ap.add_argument("--no-short", action="store_true", help="long/flat only")
    ap.add_argument("--synthetic", action="store_true",
                    help="offline: use synthetic candles (NOT a real edge)")
    ap.add_argument("--detail", action="store_true", help="print per-symbol scorecards")
    ap.add_argument("--dashboard", action="store_true",
                    help="write dashboard/cockpit.html + data.json from these results")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    pool = strategies.default_pool()
    allow_short = not args.no_short

    print(f"\nTRISHULA backtest · {args.resolution} · {args.days}d · "
          f"cost {args.cost:.3f}%/side · short={'off' if args.no_short else 'on'}")
    print(f"symbols: {', '.join(symbols)}")

    all_real = True
    # per strategy: aligned lists of (scorecard, raw result), one entry per symbol
    cards_by = {s.__name__: [] for s in pool}
    results_by = {s.__name__: [] for s in pool}

    for symbol in symbols:
        candles, real = get_candles(symbol, args.resolution, args.days, args.synthetic)
        all_real = all_real and real
        if len(candles) < 50:
            print(f"  ! {symbol}: only {len(candles)} candles, skipping")
            continue
        bench = backtest.backtest_strategy(
            candles, strategies.buy_hold(), resolution=args.resolution,
            cost_per_side_pct=args.cost, allow_short=allow_short)
        for strat in pool:
            res = backtest.backtest_strategy(
                candles, strat, resolution=args.resolution,
                cost_per_side_pct=args.cost, allow_short=allow_short)
            sc = scorecard.build_scorecard(res, bench, symbol=symbol)
            cards_by[strat.__name__].append(sc)
            results_by[strat.__name__].append(res)
            if args.detail:
                print("\n" + scorecard.format_scorecard(sc, real_data=real))

    def mean(xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else 0.0

    board = []
    for name, cards in cards_by.items():
        if not cards:
            continue
        board.append({
            "strategy": name,
            "edge": mean([c["edge_vs_hold_pct"] for c in cards]),
            "net": mean([c["net_return_pct"] for c in cards]),
            "hold": mean([c["buy_hold_pct"] for c in cards]),
            "sharpe": mean([c["sharpe"] for c in cards]),
            "trades": sum(c["trades"] for c in cards),
            "wr": mean([c["win_rate_pct"] for c in cards]),
        })
    board.sort(key=lambda r: r["edge"], reverse=True)

    tag = "REAL Delta data" if all_real else "SYNTHETIC (offline) — NOT a real edge"
    print("\n" + "=" * 74)
    print(f"  LEADERBOARD · edge vs buy-hold, averaged over {len(symbols)} symbols")
    print(f"  ({tag} · paper · not advice)")
    print("=" * 74)
    print(f"  {'strategy':26} {'edge':>8} {'net':>8} {'hold':>8} {'shrp':>6} {'trades':>7} {'win':>5}")
    print("  " + "-" * 70)
    for r in board:
        wr = f"{r['wr']:.0f}%" if r["wr"] else "  n/a"
        print(f"  {r['strategy']:26} {r['edge']:+7.1f}% {r['net']:+7.1f}% "
              f"{r['hold']:+7.1f}% {r['sharpe']:6.2f} {r['trades']:7d} {wr:>5}")
    print("=" * 74)

    if not board:
        print("  no results.\n")
        return 1

    best = board[0]
    status, expl = scorecard._verdict(best["net"], best["hold"], best["trades"])
    print(f"  WINNER: {best['strategy']}  ->  {status}")
    print(f"  {expl}")
    if not all_real:
        print("  NOTE: synthetic run — validate on REAL Delta candles before believing this.")

    # ---- dashboard ----
    if args.dashboard:
        meta = {
            "symbols": symbols, "resolution": args.resolution, "days": args.days,
            "real_data": all_real, "cost": args.cost, "short": allow_short,
            "generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(time.time())),
        }
        winner_name = best["strategy"]
        data = report.build_data(
            winner_name, cards_by[winner_name], results_by[winner_name], board, meta)
        os.makedirs(os.path.dirname(DASH_JSON), exist_ok=True)
        with open(DASH_JSON, "w") as fh:
            json.dump(data, fh, separators=(",", ":"))
        try:
            report.write_dashboard(data, template_path=DASH_HTML, out_path=DASH_HTML)
            print(f"\n  dashboard: wrote {os.path.relpath(DASH_HTML, HERE)} "
                  f"and {os.path.relpath(DASH_JSON, HERE)}")
            print("  open dashboard/cockpit.html in a browser (self-contained).")
        except Exception as exc:  # noqa: BLE001
            print(f"\n  ! dashboard render failed: {exc}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
