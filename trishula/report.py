"""Turn backtest results into the dashboard data model + rendered HTML.

The dashboard (dashboard/cockpit.html) carries a data slot between markers:

    /*TRISHULA_DATA_START*/window.TRISHULA_DATA = null;/*TRISHULA_DATA_END*/

With the slot ``null`` the page shows clearly-labelled ILLUSTRATIVE placeholders.
``render`` replaces the slot with the real backtest payload, so the page shows
REAL (or SYNTHETIC, when run offline) numbers. Rendering is idempotent — it
always overwrites the whole slot, so re-running never double-injects.
"""
from __future__ import annotations

import json
import os
import re
from typing import List, Optional

_SLOT = re.compile(
    r"/\*TRISHULA_DATA_START\*/.*?/\*TRISHULA_DATA_END\*/", re.S
)


def build_data(
    winner_name: str,
    winner_cards: List[dict],
    winner_results: List[dict],
    leaderboard: List[dict],
    meta: dict,
) -> dict:
    """Assemble the TRISHULA_DATA payload.

    winner_cards / winner_results are the per-symbol scorecards and raw backtest
    results for the winning strategy (aligned by index).
    """
    # pick the symbol where the winner did best, for the headline equity curve
    best_i = max(range(len(winner_cards)),
                 key=lambda i: winner_cards[i]["net_return_pct"]) if winner_cards else 0
    head_card = winner_cards[best_i] if winner_cards else {}
    head_res = winner_results[best_i] if winner_results else {}

    # aggregate winner stats across symbols
    def avg(key):
        xs = [c[key] for c in winner_cards if c.get(key) is not None]
        return sum(xs) / len(xs) if xs else None

    # all winner trades across symbols, tagged, for top-trades + distribution
    all_trades = []
    for card, res in zip(winner_cards, winner_results):
        for t in res.get("trade_list", []):
            tt = dict(t)
            tt["symbol"] = card["symbol"]
            all_trades.append(tt)
    top_trades = sorted(all_trades, key=lambda t: t["pnl_pct"], reverse=True)[:6]
    trade_pnls = [round(t["pnl_pct"], 3) for t in all_trades]

    per_symbol = []
    for card, res in zip(winner_cards, winner_results):
        curve = res.get("equity_curve", [])
        # downsample long curves so the payload stays small
        per_symbol.append({
            "symbol": card["symbol"],
            "net": round(card["net_return_pct"], 2),
            "hold": round(card["buy_hold_pct"], 2),
            "edge": round(card["edge_vs_hold_pct"], 2),
            "equity_curve": _downsample(curve, 160),
        })

    return {
        "meta": meta,
        "winner": {
            "strategy": winner_name,
            "symbol": head_card.get("symbol", ""),
            "verdict": head_card.get("verdict", ""),
            "explanation": head_card.get("explanation", ""),
            "net_return_pct": round(avg("net_return_pct") or 0, 2),
            "gross_return_pct": round(avg("gross_return_pct") or 0, 2),
            "cost_drag_pct": round(avg("cost_drag_pct") or 0, 2),
            "edge_vs_hold_pct": round(avg("edge_vs_hold_pct") or 0, 2),
            "buy_hold_pct": round(avg("buy_hold_pct") or 0, 2),
            "sharpe": round(avg("sharpe") or 0, 2),
            "max_drawdown_pct": round(avg("max_drawdown_pct") or 0, 2),
            "trades": sum(c["trades"] for c in winner_cards),
            "win_rate_pct": round(avg("win_rate_pct"), 1) if avg("win_rate_pct") is not None else None,
            "profit_factor": round(avg("profit_factor"), 2) if avg("profit_factor") is not None else None,
            "equity_curve": _downsample(head_res.get("equity_curve", []), 240),
        },
        "leaderboard": [
            {
                "strategy": r["strategy"],
                "edge": round(r["edge"], 2),
                "net": round(r["net"], 2),
                "hold": round(r["hold"], 2),
                "sharpe": round(r["sharpe"], 2),
                "trades": r["trades"],
                "wr": round(r["wr"], 1) if r["wr"] else None,
            }
            for r in leaderboard
        ],
        "top_trades": [
            {
                "symbol": t["symbol"], "side": t["side"],
                "entry": round(t["entry"], 4), "exit": round(t["exit"], 4),
                "pnl_pct": round(t["pnl_pct"], 2), "bars": t["bars"],
            }
            for t in top_trades
        ],
        "trade_pnls": trade_pnls,
        "per_symbol": per_symbol,
    }


def _downsample(curve: List[float], target: int) -> List[float]:
    n = len(curve)
    if n <= target:
        return [round(x, 5) for x in curve]
    step = n / target
    return [round(curve[int(i * step)], 5) for i in range(target)]


def render(template_html: str, data: dict) -> str:
    payload = ("/*TRISHULA_DATA_START*/window.TRISHULA_DATA = "
               + json.dumps(data, separators=(",", ":"))
               + ";/*TRISHULA_DATA_END*/")
    if not _SLOT.search(template_html):
        raise ValueError("dashboard template is missing the TRISHULA_DATA slot")
    return _SLOT.sub(lambda _m: payload, template_html, count=1)


def write_dashboard(data: dict, template_path: str, out_path: Optional[str] = None) -> str:
    out_path = out_path or template_path
    with open(template_path) as fh:
        html = fh.read()
    rendered = render(html, data)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write(rendered)
    return out_path
