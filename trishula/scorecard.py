"""Honest scorecard + plain-English VERDICT for a backtested strategy.

The only benchmark that counts in crypto is the lazy option: **buy and hold**.
A strategy that can't beat holding the coin after costs is not worth building a
brain around — exactly the discipline Garuda applies against the Nifty index.

The verdict refuses to bless a strategy on too few trades, because a handful of
trades is noise, not signal — that is how people fool themselves into risking
real money too early.
"""
from __future__ import annotations

from typing import Optional

# need at least this many closed trades before a verdict means anything
MIN_TRADES = 20


def _verdict(net: float, bench: float, trades: int) -> tuple:
    if trades < MIN_TRADES:
        return ("TOO FEW TRADES",
                f"Only {trades} trades (need {MIN_TRADES}+). This is noise, not "
                "signal — extend the history/symbols before trusting it.")
    if net <= 0:
        return ("LOSING",
                "Net return is negative after costs. Not working — do NOT risk "
                "real money.")
    if net < bench:
        return ("LAGGING BUY-HOLD",
                f"Up {net:+.1f}%, but just holding did {bench:+.1f}% — the lazy "
                "option is winning. Not worth trading yet.")
    return ("PROMISING",
            f"Up {net:+.1f}% vs buy-hold's {bench:+.1f}% — beating the lazy "
            "option after costs. Keep validating on more data before any capital.")


def build_scorecard(result: dict, benchmark: dict, symbol: str = "") -> dict:
    net = result["net_return_pct"]
    bench = benchmark["net_return_pct"]
    status, explanation = _verdict(net, bench, result.get("trades", 0))
    return {
        "symbol": symbol,
        "strategy": result.get("strategy", "strategy"),
        "bars": result["bars"],
        "net_return_pct": net,
        "gross_return_pct": result["gross_return_pct"],
        "cost_drag_pct": result["cost_drag_pct"],
        "buy_hold_pct": bench,
        "edge_vs_hold_pct": net - bench,
        "max_drawdown_pct": result["max_drawdown_pct"],
        "sharpe": result["sharpe"],
        "trades": result["trades"],
        "win_rate_pct": result["win_rate_pct"],
        "profit_factor": result["profit_factor"],
        "verdict": status,
        "explanation": explanation,
    }


def format_scorecard(sc: dict, real_data: bool = True) -> str:
    def pct(v):
        return f"{v:+.2f}%" if isinstance(v, (int, float)) else "n/a"
    wr = f"{sc['win_rate_pct']:.0f}%" if sc["win_rate_pct"] is not None else "n/a"
    pf = f"{sc['profit_factor']:.2f}" if sc["profit_factor"] is not None else "n/a"
    tag = "REAL Delta data" if real_data else "SYNTHETIC data — NOT a real edge"
    lines = [
        "=" * 64,
        f"  TRISHULA · SCORECARD  {sc['symbol']} · {sc['strategy']}",
        f"  ({tag} · paper · not advice)",
        "=" * 64,
        f"  Bars tested         : {sc['bars']}",
        f"  Strategy (net)      : {pct(sc['net_return_pct'])}   "
        f"(gross {pct(sc['gross_return_pct'])}, cost drag -{sc['cost_drag_pct']:.2f}%)",
        f"  Buy & hold          : {pct(sc['buy_hold_pct'])}",
        f"  Edge vs hold        : {pct(sc['edge_vs_hold_pct'])}   <- the number that matters",
        f"  Max drawdown        : {sc['max_drawdown_pct']:.2f}%",
        f"  Sharpe (annualised) : {sc['sharpe']:.2f}",
        f"  Trades              : {sc['trades']}  (win rate {wr}, profit factor {pf})",
        "-" * 64,
        f"  VERDICT: {sc['verdict']}",
        f"  {sc['explanation']}",
        "=" * 64,
    ]
    return "\n".join(lines)
