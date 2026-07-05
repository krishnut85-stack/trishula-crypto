#!/usr/bin/env python3
"""TRISHULA paper engine — Donchian-1h trend on the majors, forward paper.

Run once per hour (cron/systemd). Each run:
  1. fetches the latest 1h candles for the majors from Delta,
  2. computes the Donchian-48 signal (target side per symbol),
  3. moves the PAPER portfolio to those sides at the latest price (costs charged),
  4. marks to market, snapshots equity, saves state, reports.

PAPER_MODE_HARD: there is no order-placement code. Positions live only in
data/paper_portfolio.json. Nothing here can reach the exchange with an order.

Run on the droplet (hourly):
    cd /home/globalbot/trishula-crypto && set -a && source /home/globalbot/.env && set +a \
        && python3 scripts/paper_engine.py
Offline smoke test:
    python3 scripts/paper_engine.py --synthetic
"""
from __future__ import annotations

import argparse
import os
import sys
import time

try:
    from trishula import history, strategies
    from trishula.paper_broker import PaperPortfolio
    from trishula.notify import send_telegram
    from trishula.config import CONFIG
    from trishula.delta_client import DeltaError
except ModuleNotFoundError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from trishula import history, strategies
    from trishula.paper_broker import PaperPortfolio
    from trishula.notify import send_telegram
    from trishula.config import CONFIG
    from trishula.delta_client import DeltaError

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "data", "paper_portfolio.json")

SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD"]
RESOLUTION = "1h"
DONCHIAN_PERIOD = 48
CAPITAL = 10000.0


def latest_signal(candles):
    positions = strategies.donchian_breakout(DONCHIAN_PERIOD)(candles)
    return positions[-1], candles[-1].c, candles[-1].t


def main() -> int:
    ap = argparse.ArgumentParser(description="Trishula paper engine (Donchian-1h)")
    ap.add_argument("--synthetic", action="store_true", help="offline smoke test")
    ap.add_argument("--capital", type=float, default=CAPITAL)
    ap.add_argument("--quiet", action="store_true", help="no Telegram this run")
    ap.add_argument("--status", action="store_true",
                    help="just print the saved account (no fetch, no trade)")
    args = ap.parse_args()

    if args.status:
        pf = PaperPortfolio.load(STATE)
        if not pf or not pf.equity_history:
            print("no paper account yet — run the engine once first.")
            return 0
        eq = pf.equity_history[-1]["equity"]
        ret = (eq / pf.capital - 1) * 100
        closed = [t for t in pf.trades if t["action"] == "close"]
        wins = [t for t in closed if t.get("pnl", 0) > 0]
        wr = (len(wins) / len(closed) * 100) if closed else None
        upd = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(pf.updated or 0))
        print("TRISHULA paper account (saved state — no fetch)")
        print(f"  updated : {upd}")
        print(f"  equity  : ${eq:,.2f} ({ret:+.2f}%) from ${pf.capital:,.0f}")
        print(f"  realised: ${pf.realized:,.2f} · closed {len(closed)} · "
              f"win {('%.0f%%' % wr) if wr is not None else 'n/a'}")
        print("  positions:")
        openp = [(s, p) for s, p in pf.positions.items() if p.get("side")]
        for s, p in openp:
            print(f"    {s}: {'LONG' if p['side'] > 0 else 'SHORT'} @ {p['entry']:,.2f}")
        if not openp:
            print("    (flat — all cash)")
        print(f"  equity snapshots: {len(pf.equity_history)}")
        return 0

    # PAPER_MODE_HARD: this engine is paper by construction; make it loud.
    now = int(time.time())
    weight = 1.0 / len(SYMBOLS)

    pf = PaperPortfolio.load(STATE) or PaperPortfolio(capital=args.capital)

    # ---- gather latest candles + signals ----
    prices, signals, bars = {}, {}, {}
    for s in SYMBOLS:
        try:
            if args.synthetic:
                c = history.synthetic_candles(n=400, seed=abs(hash(s + str(now // 3600))) % 999 + 1,
                                              resolution="1h")
            else:
                c = history.fetch_candles(s, RESOLUTION, days=15, use_cache=False)
            if len(c) < DONCHIAN_PERIOD + 5:
                continue
            side, price, bar_t = latest_signal(c)
            prices[s], signals[s], bars[s] = price, side, bar_t
        except DeltaError as exc:
            print(f"  ! {s}: fetch failed ({exc})")

    if not prices:
        print("no data this run; nothing to do.")
        return 1

    # ---- size off current equity (compounding), then apply target sides ----
    sizing_equity = pf.equity(prices)
    acted = []
    for s in SYMBOLS:
        if s not in prices:
            continue
        # only act on a NEW bar (avoid double-processing within the same hour)
        if pf.last_bar.get(s) == bars[s]:
            continue
        changed = pf.set_position(s, signals[s], prices[s], now, sizing_equity * weight)
        pf.last_bar[s] = bars[s]
        if changed is not None:
            acted.append(f"{s}->{ {1:'LONG',0:'FLAT',-1:'SHORT'}[changed] }")

    eq = pf.snapshot(prices, now)
    pf.save(STATE, ts=now)
    summ = pf.summary(prices)

    # ---- report ----
    sides = " ".join(f"{s}:{ {1:'L',0:'-',-1:'S'}[signals.get(s,0)] }" for s in SYMBOLS)
    line = (f"TRISHULA paper · {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(now))} "
            f"· equity ${eq:,.2f} ({summ['return_pct']:+.2f}%) · {sides}")
    print(line)
    if acted:
        print("  trades: " + ", ".join(acted))
    print(f"  realised ${pf.realized:,.2f} · closed {summ['closed_trades']} "
          f"· win {('%.0f%%' % summ['win_rate_pct']) if summ['win_rate_pct'] is not None else 'n/a'} "
          f"· {'SYNTHETIC' if args.synthetic else 'PAPER · REAL prices'}")

    if not args.synthetic and not args.quiet and acted:
        send_telegram(f"🔱 <b>Trishula paper</b>\n{line}\ntrades: {', '.join(acted)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
