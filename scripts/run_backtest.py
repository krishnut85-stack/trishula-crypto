#!/usr/bin/env python3
"""Rank the strategy pool by honest, cost-aware edge vs buy-and-hold.

Answers the Garuda question for crypto: *which strategy is actually working?*

Real data (default):
    python3 scripts/run_backtest.py --symbols BTCUSD,ETHUSD --resolution 1h --days 180

Offline validation (no network — synthetic candles, clearly labelled):
    python3 scripts/run_backtest.py --synthetic

On the droplet:
    cd /home/globalbot && set -a && source .env && set +a && \
        python3 scripts/run_backtest.py --symbols BTCUSD,ETHUSD --resolution 1h --days 180

Rollback: read-only (fetches candles + prints). Cached candles live in
data/candles/ and can be deleted freely.
"""
from __future__ import annotations

import argparse
import sys

try:
    from trishula import history, strategies, backtest, scorecard
    from trishula.delta_client import DeltaError
except ModuleNotFoundError:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from trishula import history, strategies, backtest, scorecard
    from trishula.delta_client import DeltaError


def get_candles(symbol, resolution, days, synthetic):
    if synthetic:
        # different seed per symbol so they aren't identical series
        seed = abs(hash(symbol)) % 9999 + 1
        return history.synthetic_candles(n=min(days * 24, 3000), seed=seed,
                                         resolution=resolution), False
    try:
        return history.fetch_candles(symbol, resolution, days), True
    except (DeltaError, Exception) as exc:  # noqa: BLE001 - fall back gracefully
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
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    pool = strategies.default_pool()
    allow_short = not args.no_short

    print(f"\nTRISHULA backtest · {args.resolution} · {args.days}d · "
          f"cost {args.cost:.3f}%/side · short={'off' if args.no_short else 'on'}")
    print(f"symbols: {', '.join(symbols)}")

    all_real = True
    # agg[strategy] = list of scorecards across symbols
    agg = {s.__name__: [] for s in pool}

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
            agg[strat.__name__].append(sc)
            if args.detail:
                print("\n" + scorecard.format_scorecard(sc, real_data=real))

    # ---- leaderboard: rank by mean edge vs hold across symbols ----
    def mean(xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else 0.0

    board = []
    for name, cards in agg.items():
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

    if board:
        best = board[0]
        status, expl = scorecard._verdict(best["net"], best["hold"], best["trades"])
        print(f"  WINNER: {best['strategy']}  ->  {status}")
        print(f"  {expl}")
        if not all_real:
            print("  NOTE: synthetic run — validate on REAL Delta candles before believing this.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
