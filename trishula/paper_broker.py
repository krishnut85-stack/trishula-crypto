"""Persistent PAPER portfolio for forward paper-trading.

Tracks cash, open positions, realised P&L, a full trade log, and an equity
history that CARRIES ACROSS RUNS (data/paper_portfolio.json). This is the
crypto analogue of Garuda's persistent paper portfolio.

PAPER ONLY. There is no order-placement code anywhere in this module — a
"position" is just a signed notional exposure recorded in JSON. Nothing here can
reach the exchange. Costs (fee + GST + slippage) are charged on every fill so the
forward track record is honest.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional


class PaperPortfolio:
    def __init__(self, capital: float = 10000.0, cost_per_side_pct: float = 0.089):
        self.capital = capital
        self.cost = cost_per_side_pct / 100.0
        self.cost_per_side_pct = cost_per_side_pct
        self.cash = capital
        self.positions: Dict[str, dict] = {}   # sym -> {side, entry, units, opened}
        self.realized = 0.0
        self.trades: List[dict] = []
        self.equity_history: List[dict] = []
        self.last_bar: Dict[str, int] = {}
        self.last_prices: Dict[str, float] = {}
        self.fetch_fails: Dict[str, int] = {}   # consecutive failed fetches per symbol
        self.stopped: Dict[str, int] = {}        # sym -> side we stopped out of (block re-entry)
        self.created: Optional[int] = None
        self.updated: Optional[int] = None

    # ------------------------------------------------------------ persistence
    @classmethod
    def load(cls, path: str) -> Optional["PaperPortfolio"]:
        if not os.path.exists(path):
            return None
        d = json.load(open(path))
        p = cls(capital=d.get("capital", 10000.0),
                cost_per_side_pct=d.get("cost_per_side_pct", 0.089))
        p.cash = d["cash"]
        p.positions = d.get("positions", {})
        p.realized = d.get("realized", 0.0)
        p.trades = d.get("trades", [])
        p.equity_history = d.get("equity_history", [])
        p.last_bar = d.get("last_bar", {})
        p.last_prices = d.get("last_prices", {})
        p.fetch_fails = d.get("fetch_fails", {})
        p.stopped = d.get("stopped", {})
        p.created = d.get("created")
        p.updated = d.get("updated")
        return p

    def save(self, path: str, ts: Optional[int] = None) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if ts is not None:
            self.updated = ts
        if self.created is None:
            self.created = self.updated
        with open(path, "w") as fh:
            json.dump({
                "capital": self.capital, "cost_per_side_pct": self.cost_per_side_pct,
                "cash": self.cash, "positions": self.positions,
                "realized": self.realized, "trades": self.trades[-2000:],
                "equity_history": self.equity_history[-5000:],
                "last_bar": self.last_bar, "last_prices": self.last_prices,
                "fetch_fails": self.fetch_fails, "stopped": self.stopped,
                "created": self.created, "updated": self.updated,
            }, fh, indent=2)

    # ------------------------------------------------------------ accounting
    def equity(self, prices: Dict[str, float]) -> float:
        eq = self.cash
        for s, p in self.positions.items():
            px = prices.get(s)
            if px and p.get("side"):
                eq += p["side"] * p["units"] * (px - p["entry"])
        return eq

    def set_position(self, sym: str, target_side: int, price: float,
                     ts: int, alloc: float) -> Optional[int]:
        """Move ``sym`` to ``target_side`` (-1/0/1) at ``price``. Charges costs.
        Returns the new side if a trade happened, else None."""
        cur = self.positions.get(sym, {"side": 0, "entry": price, "units": 0.0})
        if cur["side"] == target_side:
            return None

        # close whatever is open
        if cur["side"] != 0 and cur["units"] > 0:
            pnl = cur["side"] * cur["units"] * (price - cur["entry"])
            exit_cost = cur["units"] * price * self.cost
            self.cash += pnl - exit_cost
            self.realized += pnl
            self.trades.append({"t": ts, "symbol": sym, "action": "close",
                                "side": cur["side"], "entry": cur["entry"],
                                "exit": price, "pnl": round(pnl, 2),
                                "cost": round(exit_cost, 2)})

        # open the new side
        if target_side != 0 and price > 0:
            units = alloc / price
            entry_cost = units * price * self.cost
            self.cash -= entry_cost
            self.positions[sym] = {"side": target_side, "entry": price,
                                   "units": units, "opened": ts}
            self.trades.append({"t": ts, "symbol": sym, "action": "open",
                                "side": target_side, "entry": price,
                                "units": round(units, 6), "cost": round(entry_cost, 2)})
        else:
            self.positions[sym] = {"side": 0, "entry": price, "units": 0.0, "opened": ts}
        return target_side

    def snapshot(self, prices: Dict[str, float], ts: int) -> float:
        eq = self.equity(prices)
        self.equity_history.append({"t": ts, "equity": round(eq, 2)})
        return eq

    def summary(self, prices: Dict[str, float]) -> dict:
        eq = self.equity(prices)
        closed = [t for t in self.trades if t["action"] == "close"]
        wins = [t for t in closed if t.get("pnl", 0) > 0]
        return {
            "equity": eq,
            "return_pct": (eq / self.capital - 1) * 100,
            "cash": self.cash,
            "realized": self.realized,
            "open_positions": {s: p["side"] for s, p in self.positions.items() if p.get("side")},
            "closed_trades": len(closed),
            "win_rate_pct": (len(wins) / len(closed) * 100) if closed else None,
        }
