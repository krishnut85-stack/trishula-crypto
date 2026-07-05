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
import json
import os
import sys
import time

try:
    from trishula import history, strategies
    from trishula.paper_broker import PaperPortfolio
    from trishula.notify import send_telegram
    from trishula.config import CONFIG
    from trishula.delta_client import DeltaClient, DeltaError
except ModuleNotFoundError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from trishula import history, strategies
    from trishula.paper_broker import PaperPortfolio
    from trishula.notify import send_telegram
    from trishula.config import CONFIG
    from trishula.delta_client import DeltaClient, DeltaError

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "data", "paper_portfolio.json")

SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD"]
RESOLUTION = "1h"
DONCHIAN_PERIOD = 48
CAPITAL = 10000.0


def latest_signal(candles):
    positions = strategies.donchian_breakout(DONCHIAN_PERIOD)(candles)
    return positions[-1], candles[-1].c, candles[-1].t


def get_top_liquid(n):
    """Top-N live perps by 24h USD volume."""
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
    return sorted(syms, key=lambda s: vol.get(s, 0), reverse=True)[:n]


def main() -> int:
    ap = argparse.ArgumentParser(description="Trishula paper engine (Donchian-1h)")
    ap.add_argument("--synthetic", action="store_true", help="offline smoke test")
    ap.add_argument("--capital", type=float, default=CAPITAL)
    ap.add_argument("--quiet", action="store_true", help="no Telegram this run")
    ap.add_argument("--status", action="store_true",
                    help="just print the saved account (no fetch, no trade)")
    ap.add_argument("--top", type=int, default=20,
                    help="trade the top-N liquid perps by 24h volume (default 20)")
    ap.add_argument("--symbols", default="", help="comma list to override the universe")
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
    pf = PaperPortfolio.load(STATE) or PaperPortfolio(capital=args.capital)

    # ---- determine the traded universe: top-N liquid perps ----
    if args.symbols:
        universe = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.synthetic:
        universe = (SYMBOLS + [f"ALT{i}USD" for i in range(args.top)])[:args.top]
    else:
        try:
            universe = get_top_liquid(args.top)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! universe fetch failed ({exc}); falling back to majors")
            universe = SYMBOLS
    held = [s for s, p in pf.positions.items() if p.get("side")]
    process = list(dict.fromkeys(universe + held))   # trade universe + manage dropouts
    weight = 1.0 / max(1, len(universe))

    # ---- gather latest candles + signals ----
    prices, signals, bars = {}, {}, {}
    for s in process:
        ok = False
        try:
            if args.synthetic:
                c = history.synthetic_candles(n=400, seed=abs(hash(s + str(now // 3600))) % 999 + 1,
                                              resolution="1h")
            else:
                c = history.fetch_candles(s, RESOLUTION, days=15, use_cache=False)
            if len(c) >= DONCHIAN_PERIOD + 5:
                side, price, bar_t = latest_signal(c)
                # a coin that dropped out of the universe is exited to flat
                prices[s], signals[s], bars[s] = price, (side if s in universe else 0), bar_t
                pf.fetch_fails[s] = 0          # healthy fetch -> reset the fail counter
                ok = True
        except DeltaError as exc:
            print(f"  ! {s}: fetch failed ({exc})")
        if not ok:
            pf.fetch_fails[s] = pf.fetch_fails.get(s, 0) + 1

    if not prices:
        print("no data this run; nothing to do.")
        return 1

    # ---- size off current equity (compounding), then apply target sides ----
    sizing_equity = pf.equity(prices)
    acted = []
    STALE_LIMIT = 3   # force-flatten a held coin after this many failed fetches (~hours)
    for s in process:
        if s not in prices:
            # SAFETY: a held coin we can't price for several hours (thin/delisted)
            # is force-flattened at its last known price so it can't linger forever.
            held = pf.positions.get(s, {}).get("side", 0)
            if held and pf.fetch_fails.get(s, 0) >= STALE_LIMIT:
                px = pf.last_prices.get(s) or pf.positions[s]["entry"]
                if pf.set_position(s, 0, px, now, 0.0) is not None:
                    acted.append(f"{s}->FLAT(stale {pf.fetch_fails[s]}x)")
            continue
        # only act on a NEW bar (avoid double-processing within the same hour)
        if pf.last_bar.get(s) == bars[s]:
            continue
        changed = pf.set_position(s, signals[s], prices[s], now, sizing_equity * weight)
        pf.last_bar[s] = bars[s]
        if changed is not None:
            acted.append(f"{s}->{ {1:'LONG',0:'FLAT',-1:'SHORT'}[changed] }")

    eq = pf.snapshot(prices, now)
    pf.last_prices = prices
    pf.save(STATE, ts=now)
    summ = pf.summary(prices)

    # regenerate the live dashboard HTML (Garuda-style static file)
    try:
        from trishula import paper_report
        paper_report.write_dashboard(json.load(open(STATE)),
                                     os.path.join(HERE, "dashboard", "paper.html"))
    except Exception:
        pass

    # ---- report ----
    longs = sum(1 for s in universe if signals.get(s, 0) > 0)
    shorts = sum(1 for s in universe if signals.get(s, 0) < 0)
    line = (f"TRISHULA paper · {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(now))} "
            f"· equity ${eq:,.2f} ({summ['return_pct']:+.2f}%) · universe {len(universe)} "
            f"· {longs}L/{shorts}S · {len(summ['open_positions'])} open")
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
