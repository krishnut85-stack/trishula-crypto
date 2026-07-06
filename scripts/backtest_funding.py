#!/usr/bin/env python3
"""Funding-rate capture analysis for Delta Exchange India perps.

The deep-research pass flagged funding capture as the ONE fast-ish edge that is
even plausibly retail-accessible — but warned its margins are thin and easily
eaten by costs. This script measures it honestly on Delta's top-N liquid perps
so we decide with data, not vibes.

Two important realities it is built around:
  1. Delta India is derivatives-only — there is NO spot on the same venue to
     hedge a perp against. So a clean market-NEUTRAL carry (hold spot / short
     perp, zero price risk) is NOT possible single-venue. What you CAN do here
     is a DIRECTIONAL funding harvest: hold the side that RECEIVES funding and
     wear the price risk. This script measures whether the funding income is
     big enough to matter next to that price risk and the round-trip cost.
  2. Funding on a perp flips: funding_rate > 0 => longs pay shorts (short side
     receives); funding_rate < 0 => shorts pay longs (long side receives).

Run on the droplet (real data):
    cd /home/globalbot/trishula-crypto && set -a && source /home/globalbot/.env && set +a \
        && python3 scripts/backtest_funding.py --top 20 --days 60
Offline smoke test (fabricated funding, NOT real):
    python3 scripts/backtest_funding.py --synthetic

Paper. Not advice.
"""
from __future__ import annotations

import argparse
import math
import os
import statistics
import sys

try:
    from trishula import history
    from trishula.delta_client import DeltaClient, DeltaError
except ModuleNotFoundError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from trishula import history
    from trishula.delta_client import DeltaClient, DeltaError

# round-trip cost to open AND close one position (fee+GST per side ~0.059%)
COST_PER_SIDE_PCT = 0.059


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def get_top_perps(client, n):
    """Top-N live perps by 24h USD volume, with current funding + mark price."""
    prods = client.get_products()
    perps = {p["symbol"] for p in prods
             if p.get("contract_type") == "perpetual_futures" and p.get("state") == "live"}
    rows = []
    for t in client.get_tickers():
        s = t.get("symbol")
        if s not in perps:
            continue
        vol = _f(t.get("turnover_usd") or t.get("turnover") or t.get("volume"))
        rows.append({
            "sym": s,
            "vol": vol,
            "mark": _f(t.get("mark_price") or t.get("close") or t.get("spot_price")),
            # Delta reports funding_rate as a PERCENT per funding interval
            "funding_pct": _f(t.get("funding_rate")),
        })
    rows.sort(key=lambda r: r["vol"], reverse=True)
    return rows[:n]


def synthetic_perps(n):
    """Fabricated funding + price so the script runs offline. NOT real."""
    rows = []
    for i in range(n):
        # spread of funding: some rich, some negative, most small
        fr = ((i * 7 % 11) - 4) * 0.004     # -0.016%..+0.028% per 8h
        rows.append({"sym": f"ALT{i}USD", "vol": (n - i) * 1e6,
                     "mark": 100.0 + i, "funding_pct": round(fr, 4)})
    return rows


def annual_vol_pct(candles):
    """Annualised volatility (%) from 1h close-to-close returns."""
    closes = [c.c for c in candles]
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))
            if closes[i - 1] > 0]
    if len(rets) < 10:
        return None
    hourly_sd = statistics.pstdev(rets)
    return hourly_sd * math.sqrt(24 * 365) * 100


def main() -> int:
    ap = argparse.ArgumentParser(description="Delta funding-rate capture analysis")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--days", type=int, default=60,
                    help="days of 1h candles for the volatility estimate")
    ap.add_argument("--funding-hours", type=float, default=8.0,
                    help="hours per funding interval on Delta (default 8)")
    ap.add_argument("--synthetic", action="store_true", help="offline, fabricated data")
    args = ap.parse_args()

    intervals_per_day = 24.0 / args.funding_hours
    real = not args.synthetic

    if real:
        try:
            client = DeltaClient()
            perps = get_top_perps(client, args.top)
        except Exception as exc:  # noqa: BLE001
            print(f"! could not reach Delta ({exc}); run with --synthetic offline.")
            return 1
    else:
        client, perps = None, synthetic_perps(args.top)

    if not perps:
        print("no perps found.")
        return 1

    tag = "REAL Delta data" if real else "SYNTHETIC — NOT real"
    print(f"\nFUNDING CAPTURE · top {len(perps)} perps · funding every "
          f"{args.funding_hours:g}h · ({tag} · paper · not advice)")
    print("=" * 92)
    print(f"  {'symbol':<14}{'fund/8h':>9}{'fund/yr':>9}{'receiver':>9}"
          f"{'px vol/yr':>11}{'fund/vol':>9}{'breakeven':>11}")
    print("  " + "-" * 88)

    ranked = []
    for p in perps:
        fpct = p["funding_pct"]                       # % per interval
        annual = fpct * intervals_per_day * 365        # % per year (naive carry)
        receiver = "SHORT" if fpct > 0 else ("LONG" if fpct < 0 else "—")
        # price risk: annualised vol from candles (real) or a placeholder (synthetic)
        if real:
            try:
                cs = history.fetch_candles(p["sym"], "1h", days=args.days,
                                           client=client, use_cache=True)
                pv = annual_vol_pct(cs)
            except DeltaError:
                pv = None
        else:
            pv = 60.0 + (hash(p["sym"]) % 40)
        # funding income vs price risk (Sharpe-ish): >1 means funding dominates risk
        fv = (abs(annual) / pv) if pv else None
        # breakeven: how many days of funding pay back the round-trip entry+exit cost
        daily_fund_pct = abs(fpct) * intervals_per_day
        be_days = (2 * COST_PER_SIDE_PCT / daily_fund_pct) if daily_fund_pct > 0 else None
        ranked.append({**p, "annual": annual, "receiver": receiver,
                       "pv": pv, "fv": fv, "be_days": be_days})

    # show richest funding first (by |annualised|)
    ranked.sort(key=lambda r: abs(r["annual"]), reverse=True)
    for r in ranked:
        pv = f"{r['pv']:.0f}%" if r["pv"] is not None else "n/a"
        fv = f"{r['fv']:.2f}" if r["fv"] is not None else "n/a"
        be = f"{r['be_days']:.1f}d" if r["be_days"] else "—"
        print(f"  {r['sym']:<14}{r['funding_pct']:>+8.4f}%{r['annual']:>+8.1f}%"
              f"{r['receiver']:>9}{pv:>11}{fv:>9}{be:>11}")
    print("=" * 92)

    # ---- honest read ----
    annuals = [abs(r["annual"]) for r in ranked]
    med = statistics.median(annuals) if annuals else 0.0
    rich = [r for r in ranked if r["fv"] is not None and r["fv"] >= 1.0]
    print("  READ:")
    print(f"  · median |annualised funding| across top {len(perps)}: {med:.1f}%/yr")
    print(f"  · richest: {ranked[0]['sym']} {ranked[0]['annual']:+.1f}%/yr "
          f"(receive by going {ranked[0]['receiver']})")
    print(f"  · coins where funding income > price risk (fund/vol >= 1): "
          f"{len(rich)} of {len(ranked)}")
    print("  · 'fund/vol' < 1 means the PRICE swings you'd wear are bigger than the")
    print("    funding you'd collect — i.e. directional harvest is mostly a coin bet,")
    print("    not a clean carry. On Delta (no same-venue spot) you cannot hedge that")
    print("    price risk away, so treat fund/vol < 1 coins as NOT a funding edge.")
    if not rich:
        print("  · VERDICT: no coin clears fund/vol >= 1 — single-venue funding harvest")
        print("    is dominated by price risk here. Real neutral capture needs a spot")
        print("    hedge on a second venue (bigger project). Keep Donchian as the book.")
    else:
        print(f"  · VERDICT: {len(rich)} coin(s) show funding income above price risk —")
        print("    worth a closer market-neutral study IF a spot hedge venue is added.")
    if args.synthetic:
        print("  NOTE: synthetic — run on the droplet for real Delta funding numbers.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
