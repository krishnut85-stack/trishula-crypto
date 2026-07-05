"""Live state for the Trishula dashboard — produces the same JSON schema as
Garuda's build_state(), so Garuda's dashboard_live.html renders it unchanged.

Two lanes (profiles): 'trend' (Donchian-1h paper book, BTC/ETH/SOL) and
'momentum' (top-N universe scanner, watch-only for now). Data comes from Delta
India via the proven fetch layer; positions from the paper portfolio.

PAPER ONLY — reads the paper book, never places an order.
"""
from __future__ import annotations

import os
import time
from typing import Dict, List

from . import history, indicators, strategies
from .delta_client import DeltaClient, DeltaError
from .paper_broker import PaperPortfolio

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "data", "paper_portfolio.json")

PROFILES = {
    "trend": {"name": "Trishula-Trend", "desc": "donchian-1h · majors",
              "proven_win": 39.0, "proven_ret": 1.7, "capital": 10000.0},
    "momentum": {"name": "Trishula-Mom", "desc": "xs-momentum · top perps",
                 "proven_win": 0.0, "proven_ret": 0.0, "capital": 10000.0},
}
MAJORS = ["BTCUSD", "ETHUSD", "SOLUSD"]


def _pct(cl, n):
    return round((cl[-1] / cl[-n - 1] - 1) * 100, 2) if len(cl) > n and cl[-n - 1] > 0 else None


class TrishulaLive:
    def __init__(self, top_n: int = 200):
        self.top_n = top_n
        self.client = DeltaClient()
        self.candles: Dict[str, list] = {}      # sym -> list[Candle]
        self.prices: Dict[str, float] = {}
        self.vol_mn: Dict[str, float] = {}
        self.charts: Dict[str, dict] = {}
        self.momentum_syms: List[str] = []
        self.last_scan = ""
        self._last_full = 0.0

    # ---- data ----
    def _universe(self):
        prods = self.client.get_products()
        perps = [p for p in prods if p.get("contract_type") == "perpetual_futures"
                 and p.get("state") == "live"]
        try:
            ticks = self.client.get_tickers()
        except DeltaError:
            ticks = []
        vol = {}
        for t in ticks:
            s = t.get("symbol")
            v = t.get("turnover_usd") or t.get("turnover") or t.get("volume") or 0
            try:
                vol[s] = float(v)
            except (TypeError, ValueError):
                vol[s] = 0.0
        syms = [p["symbol"] for p in perps if p.get("symbol") in vol]
        syms = sorted(syms, key=lambda s: vol.get(s, 0), reverse=True)
        self.vol_mn = {s: vol.get(s, 0) / 1e6 for s in syms}
        mom = [s for s in syms if s not in MAJORS][: self.top_n]
        self.momentum_syms = mom
        return MAJORS + mom

    def refresh(self, full: bool = False):
        """Refresh candles (daily) + prices. `full` re-pulls the universe."""
        if full or not self.momentum_syms:
            universe = self._universe()
        else:
            universe = MAJORS + self.momentum_syms
        for s in universe:
            if full or s not in self.candles:
                try:
                    self.candles[s] = history.fetch_candles(s, "1d", days=300,
                                                             client=self.client, use_cache=True)
                except DeltaError:
                    continue
            cs = self.candles.get(s)
            if cs:
                self.prices[s] = cs[-1].c
        self.last_scan = time.strftime("%Y-%m-%d", time.gmtime())
        self._last_full = time.time()

    # ---- chart (matches Garuda chart schema) ----
    def refresh_chart(self, sym):
        cs = self.candles.get(sym)
        if not cs:
            try:
                cs = history.fetch_candles(sym, "1d", days=300, client=self.client, use_cache=True)
                self.candles[sym] = cs
            except DeltaError:
                return
        candles = [{"t": int(c.t), "o": round(c.o, 4), "h": round(c.h, 4),
                    "l": round(c.l, 4), "c": round(c.c, 4)} for c in cs]
        closes = [c.c for c in cs]
        r = indicators.rsi(closes, 2)
        ma20, ma50 = indicators.sma(closes, 20), indicators.sma(closes, 50)
        markers, prev = [], None
        for i, v in enumerate(r):
            if v is None:
                continue
            if v < 10 and (prev is None or prev >= 10):
                markers.append({"t": candles[i]["t"], "type": "buy"})
            elif v > 90 and (prev is None or prev <= 90):
                markers.append({"t": candles[i]["t"], "type": "sell"})
            prev = v
        self.charts[sym] = {
            "candles": candles,
            "rsi": [round(v, 1) if v is not None else None for v in r],
            "ma20": [round(v, 2) if v is not None else None for v in ma20],
            "ma50": [round(v, 2) if v is not None else None for v in ma50],
            "markers": markers,
        }

    def chart_for(self, sym):
        if sym and sym not in self.charts:
            self.refresh_chart(sym)
        return self.charts.get(sym)

    # ---- watch row for one symbol ----
    def _watch(self, sym, pf):
        cs = self.candles.get(sym)
        if not cs:
            return None
        closes = [c.c for c in cs]
        ltp = self.prices.get(sym, closes[-1])
        prev = closes[-2] if len(closes) > 1 else closes[-1]
        chg = round((ltp - prev) / prev * 100, 2) if prev else 0.0
        win = closes[-365:]
        r = indicators.rsi(closes, 2)
        h = pf.positions.get(sym) if pf else None
        held = bool(h and h.get("side"))
        return {
            "sym": sym, "ltp": round(ltp, 4), "chg": chg,
            "chg_w": _pct(closes, 7), "chg_m": _pct(closes, 30),
            "hi52": round(max(win), 4) if win else None,
            "lo52": round(min(win), 4) if win else None,
            "mcap": self.vol_mn.get(sym),      # 24h volume ($mn) in the mcap column
            "rsi2": round(r[-1], 1) if r and r[-1] is not None else None,
            "held": held, "qty": (h["units"] if held else None),
            "pnl": (round(h["side"] * h["units"] * (ltp - h["entry"]), 2) if held else None),
            "o": round(cs[-1].o, 4), "h": round(cs[-1].h, 4), "l": round(cs[-1].l, 4),
        }

    # ---- full state (matches Garuda build_state schema) ----
    def build_state(self):
        pf = PaperPortfolio.load(STATE)
        profs = []
        for key, meta in PROFILES.items():
            universe = MAJORS if key == "trend" else self.momentum_syms
            positions = []
            day_pnl = 0.0
            if key == "trend" and pf:
                for s, h in pf.positions.items():
                    if not h.get("side"):
                        continue
                    ltp = self.prices.get(s, h["entry"])
                    pnl = h["side"] * h["units"] * (ltp - h["entry"])
                    chg = (ltp - h["entry"]) / h["entry"] * 100 if h["entry"] else 0
                    day_pnl += pnl
                    positions.append({"sym": s, "qty": round(h["units"], 4),
                                      "entry": round(h["entry"], 4), "ltp": round(ltp, 4),
                                      "chg": round(chg, 2), "pnl": round(pnl, 2),
                                      "rsi2": None, "side": h["side"]})
            positions.sort(key=lambda x: x["pnl"], reverse=True)

            watch = [w for w in (self._watch(s, pf) for s in universe) if w]

            if key == "trend" and pf:
                eq = pf.equity_history[-1]["equity"] if pf.equity_history else pf.capital
                cap = pf.capital
            else:
                eq, cap = meta["capital"], meta["capital"]
            green = sum(1 for x in positions if x["pnl"] > 0)
            profs.append({
                "key": key, "name": meta["name"], "desc": meta["desc"],
                "capital": cap, "equity": round(eq, 0),
                "pnl_pct": round((eq / cap - 1) * 100, 2) if cap else 0.0,
                "day_pnl": round(day_pnl, 0), "cash": round(pf.cash if (key == "trend" and pf) else cap, 0),
                "positions": positions, "watch": watch, "universe": universe,
                "win": meta["proven_win"] or None, "win_kind": "backtest",
                "win_open": round(green / len(positions) * 100) if positions else None,
                "proven_win": meta["proven_win"], "proven_ret": meta["proven_ret"],
                "pf": None, "buys": [], "sells": [],
                "best": positions[0] if positions else None,
                "worst": positions[-1] if positions else None,
                "chart_sym": positions[0]["sym"] if positions else (universe[0] if universe else None),
                "chart": self.charts.get(positions[0]["sym"] if positions else
                                         (universe[0] if universe else "")),
            })
        totals = {
            "equity": round(sum(p["equity"] for p in profs), 0),
            "capital": round(sum(p["capital"] for p in profs), 0),
            "day_pnl": round(sum(p["day_pnl"] for p in profs), 0),
            "positions": sum(len(p["positions"]) for p in profs),
        }
        totals["pnl_pct"] = round((totals["equity"] / totals["capital"] - 1) * 100, 2) \
            if totals["capital"] else 0.0
        curve = [h["equity"] for h in (pf.equity_history if pf else [])]
        totals["curve"] = curve or [totals["equity"]]
        return {"live": True, "profiles": profs, "totals": totals,
                "market_open": True, "market_status": "● OPEN · 24/7",
                "last_scan": self.last_scan, "today": self.last_scan}
