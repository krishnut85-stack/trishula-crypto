#!/usr/bin/env python3
"""TRISHULA QUANT — live paper dashboard (Streamlit), same style as Garuda.

Reads the paper account (data/paper_portfolio.json) written by the hourly engine
and shows equity, forward curve, open positions and trades. Token-gated like the
Garuda dashboard.

Run on the droplet (Streamlit is already installed there for Garuda):
    cd /home/globalbot/trishula-crypto
    nohup streamlit run dashboard/trishula_app.py --server.port 8502 \
        --server.address 0.0.0.0 --server.headless true > data/streamlit.log 2>&1 &

Open:  http://<droplet-ip>:8502/?token=trishulaLIVE2026
Set a custom token with env TRISHULA_DASH_TOKEN.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "data", "paper_portfolio.json")
TOKEN = os.getenv("TRISHULA_DASH_TOKEN", "trishulaLIVE2026")
REFRESH_SECS = 30

st.set_page_config(page_title="Trishula Quant · Paper", page_icon="🔱",
                   layout="wide", initial_sidebar_state="collapsed")

# ---- terminal-style CSS ----
st.markdown("""
<style>
:root{--pos:#37d07f;--neg:#e5484d;--accent:#ef7d4b;}
html,body,[class*="css"]{font-family:ui-monospace,"JetBrains Mono",Menlo,Consolas,monospace;}
.block-container{padding-top:1.4rem;max-width:1050px;}
h1{letter-spacing:.14em;font-size:1.5rem!important;}
[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums;}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:.7rem;
letter-spacing:.1em;border:1px solid #1c2521;background:#0f1412;color:#7c8983;margin-right:6px;}
.pill.live{color:#37d07f;border-color:#2b6e50;}
.small{color:#7c8983;font-size:.75rem;}
</style>
""", unsafe_allow_html=True)

# auto-refresh (meta tag; Streamlit re-runs on reload)
st.markdown(f'<meta http-equiv="refresh" content="{REFRESH_SECS}">', unsafe_allow_html=True)

# ---- token gate (like Garuda) ----
try:
    params = dict(st.query_params)
except Exception:
    params = st.experimental_get_query_params()
supplied = params.get("token", "")
if isinstance(supplied, list):
    supplied = supplied[0] if supplied else ""
if supplied != TOKEN:
    st.title("🔱 Trishula Quant")
    st.error("Access denied. Add your token to the URL:  ?token=YOUR_TOKEN")
    st.stop()

# ---- load state ----
if not os.path.exists(STATE):
    st.title("🔱 Trishula Quant · Paper")
    st.warning("No paper account yet. Run the engine once, then refresh.")
    st.stop()

with open(STATE) as fh:
    d = json.load(fh)

capital = d.get("capital", 10000.0)
hist = d.get("equity_history", [])
equity = hist[-1]["equity"] if hist else d.get("cash", capital)
ret = (equity / capital - 1) * 100 if capital else 0.0
realized = d.get("realized", 0.0)
positions = d.get("positions", {})
last_prices = d.get("last_prices", {})
trades = d.get("trades", [])
updated = d.get("updated") or 0
upd = datetime.fromtimestamp(updated, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if updated else "—"

closed = [t for t in trades if t.get("action") == "close"]
wins = [t for t in closed if t.get("pnl", 0) > 0]
wr = f"{len(wins)/len(closed)*100:.0f}%" if closed else "n/a"

# ---- header ----
st.title("🔱 TRISHULA QUANT")
st.markdown(
    '<span class="pill live">● PAPER</span>'
    '<span class="pill">DONCHIAN-1H TREND</span>'
    '<span class="pill">DELTA INDIA</span>'
    f'<span class="pill">{upd}</span>', unsafe_allow_html=True)

# ---- metrics ----
c1, c2, c3, c4 = st.columns(4)
c1.metric("Paper equity", f"${equity:,.2f}", f"{ret:+.2f}%")
c2.metric("Realised P&L", f"${realized:,.2f}")
c3.metric("Closed trades", f"{len(closed)}")
c4.metric("Win rate", wr)

# ---- equity curve ----
st.subheader("Equity curve · forward paper")
if len(hist) > 1:
    ec = pd.DataFrame({
        "time": [datetime.fromtimestamp(h["t"], timezone.utc) for h in hist],
        "equity": [h["equity"] for h in hist],
    }).set_index("time")
    try:
        st.line_chart(ec, height=260, color="#37d07f")
    except TypeError:
        st.line_chart(ec, height=260)   # older Streamlit without color=
else:
    st.caption("Equity curve builds as the hourly engine runs — check back after a few runs.")

# ---- positions + trades ----
left, right = st.columns(2)
with left:
    st.subheader("Open positions")
    prows = []
    for s, p in positions.items():
        side = p.get("side", 0)
        if not side:
            continue
        entry = p.get("entry", 0.0)
        cur = last_prices.get(s, entry)
        units = p.get("units", 0.0)
        upnl = side * units * (cur - entry)
        upct = (cur / entry - 1) * 100 * side if entry else 0.0
        prows.append({"symbol": s, "side": "LONG" if side > 0 else "SHORT",
                      "entry": round(entry, 2), "price": round(cur, 2),
                      "uP&L $": round(upnl, 2), "uP&L %": round(upct, 2)})
    if prows:
        st.dataframe(pd.DataFrame(prows), hide_index=True, use_container_width=True)
    else:
        st.caption("Flat — all cash.")

with right:
    st.subheader("Recent trades")
    trows = []
    for t in reversed(trades[-15:]):
        trows.append({
            "time": datetime.fromtimestamp(t.get("t", 0), timezone.utc).strftime("%m-%d %H:%M"),
            "symbol": t.get("symbol", ""), "action": t.get("action", ""),
            "side": "L" if t.get("side", 0) > 0 else "S",
            "pnl $": round(t["pnl"], 2) if t.get("pnl") is not None else None,
        })
    if trows:
        st.dataframe(pd.DataFrame(trows), hide_index=True, use_container_width=True)
    else:
        st.caption("No trades yet.")

st.markdown('<p class="small">PAPER MODE HARD · not investment advice · '
            f'auto-refresh {REFRESH_SECS}s · started '
            f'{datetime.fromtimestamp(d.get("created", updated) or 0, timezone.utc).strftime("%Y-%m-%d")}</p>',
            unsafe_allow_html=True)
