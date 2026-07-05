#!/usr/bin/env python3
"""TRISHULA ⚡ QUANT — live paper dashboard (Streamlit), styled like Garuda.

Mirrors the Garuda Quant dashboard: header + status badges, a 5-metric box,
a colored signals table for the traded universe, a candlestick chart with the
Donchian channel (TradingView lightweight-charts), and the combined paper
equity curve. Token-gated like Garuda.

Run on the droplet (Streamlit already installed there):
    cd /home/globalbot/trishula-crypto
    nohup streamlit run dashboard/trishula_app.py --server.port 8502 \
        --server.address 0.0.0.0 --server.headless true > data/streamlit.log 2>&1 &

Open:  http://<droplet-ip>:8502/?token=trishulaLIVE2026
Custom token via env TRISHULA_DASH_TOKEN.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trishula import history, strategies, indicators  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "data", "paper_portfolio.json")
TOKEN = os.getenv("TRISHULA_DASH_TOKEN", "trishulaLIVE2026")
SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD"]
REFRESH_SECS = 60
IST = timezone(timedelta(hours=5, minutes=30))

st.set_page_config(page_title="Trishula ⚡ Quant", page_icon="🔱",
                   layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
html,body,[class*="css"]{font-family:ui-monospace,"JetBrains Mono",Menlo,Consolas,monospace;}
.block-container{padding-top:1.1rem;max-width:1150px;}
.title{font-size:2rem;font-weight:800;letter-spacing:.06em;}
.title .g{color:#37d07f;} .title .b{color:#ef7d4b;} .title .q{color:#e0a63b;}
.sub{color:#7c8983;font-size:.8rem;margin:2px 0 10px;}
.badge{display:inline-block;padding:4px 11px;border-radius:6px;font-size:.72rem;
letter-spacing:.08em;margin-right:8px;border:1px solid #1c2521;}
.badge.live{background:#3a1113;color:#ff6b6b;border-color:#5a1a1d;}
.badge.mkt{background:#0f1412;color:#7c8983;}
.tgreen{color:#37d07f;} .tgold{color:#e0a63b;} .tgrey{color:#7c8983;}
.metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;background:#1c2521;
border:1px solid #1c2521;border-radius:10px;overflow:hidden;margin:10px 0 6px;}
.mcell{background:#0b100e;padding:14px 16px;}
.mlbl{color:#7c8983;font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;}
.mval{font-size:1.5rem;font-weight:700;margin-top:6px;font-variant-numeric:tabular-nums;}
table.sig{width:100%;border-collapse:collapse;font-size:.82rem;}
table.sig th{color:#7c8983;text-transform:uppercase;font-size:.62rem;letter-spacing:.08em;
text-align:right;padding:8px 10px;border-bottom:1px solid #1c2521;}
table.sig th:first-child,table.sig td:first-child{text-align:left;}
table.sig td{padding:9px 10px;border-bottom:1px solid #141a17;text-align:right;font-variant-numeric:tabular-nums;}
.tag{font-size:.6rem;border:1px solid #2b3a33;border-radius:4px;padding:1px 5px;color:#7c8983;margin-left:6px;}
.long{color:#37d07f;font-weight:700;} .short{color:#e5484d;font-weight:700;} .flat{color:#7c8983;}
.pos{color:#37d07f;} .neg{color:#e5484d;}
.foot{color:#55625c;font-size:.72rem;margin-top:14px;text-align:center;}
h3{letter-spacing:.06em;font-size:.95rem!important;color:#cdd6d1;}
</style>
""", unsafe_allow_html=True)
st.markdown(f'<meta http-equiv="refresh" content="{REFRESH_SECS}">', unsafe_allow_html=True)

# ---- token gate ----
try:
    params = dict(st.query_params)
except Exception:
    params = st.experimental_get_query_params()
tok = params.get("token", "")
tok = tok[0] if isinstance(tok, list) else tok
if tok != TOKEN:
    st.markdown('<div class="title"><span class="g">TRISHULA</span> ⚡ <span class="q">QUANT</span></div>',
                unsafe_allow_html=True)
    st.error("Access denied. Add your token to the URL:  ?token=YOUR_TOKEN")
    st.stop()


# ---- cached data helpers ----
@st.cache_data(ttl=300, show_spinner=False)
def daily(sym):
    try:
        return [(c.t, c.o, c.h, c.l, c.c, c.v)
                for c in history.fetch_candles(sym, "1d", days=200, use_cache=True)]
    except Exception:
        return []


@st.cache_data(ttl=300, show_spinner=False)
def signal_1h(sym):
    try:
        cs = history.fetch_candles(sym, "1h", days=15, use_cache=True)
        return strategies.donchian_breakout(48)(cs)[-1]
    except Exception:
        return 0


def pct(closes, n):
    return (closes[-1] / closes[-n - 1] - 1) * 100 if len(closes) > n else None


# ---- load paper state ----
d = {}
if os.path.exists(STATE):
    with open(STATE) as fh:
        d = json.load(fh)
capital = d.get("capital", 10000.0)
hist = d.get("equity_history", [])
equity = hist[-1]["equity"] if hist else d.get("cash", capital)
ret = (equity / capital - 1) * 100 if capital else 0.0
realized = d.get("realized", 0.0)
positions = d.get("positions", {})
trades = d.get("trades", [])
closed = [t for t in trades if t.get("action") == "close"]
wins = [t for t in closed if t.get("pnl", 0) > 0]
wr = f"{len(wins)/len(closed)*100:.0f}%" if closed else "n/a"
open_n = sum(1 for p in positions.values() if p.get("side"))
# day P&L: change since first snapshot in the last 24h
day_pl = 0.0
if hist:
    cutoff = time.time() - 86400
    base = next((h["equity"] for h in hist if h["t"] >= cutoff), hist[0]["equity"])
    day_pl = equity - base

now_utc = datetime.now(timezone.utc)
ist = now_utc.astimezone(IST)

# ---- header ----
st.markdown('<div class="title"><span class="g">TRISHULA</span> ⚡ <span class="q">QUANT</span></div>',
            unsafe_allow_html=True)
st.markdown('<div class="sub">Donchian-1h trend + momentum · Delta Exchange India perps · paper</div>',
            unsafe_allow_html=True)
st.markdown(
    '<span class="badge live">● LIVE · PAPER</span>'
    '<span class="badge mkt">OPEN · 24/7</span>'
    f'<span class="tgreen">IST {ist.strftime("%a %H:%M:%S")}</span>'
    f'&nbsp;&nbsp;<span class="tgrey">UTC {now_utc.strftime("%H:%M:%S")}</span>',
    unsafe_allow_html=True)

# ---- metrics box ----
rcls = "tgreen" if ret >= 0 else "neg"
dcls = "tgreen" if day_pl >= 0 else "neg"
st.markdown(f"""
<div class="metrics">
  <div class="mcell"><div class="mlbl">Paper equity</div><div class="mval">${equity:,.0f}</div></div>
  <div class="mcell"><div class="mlbl">Total P&amp;L</div><div class="mval {rcls}">{ret:+.2f}%</div></div>
  <div class="mcell"><div class="mlbl">Day P&amp;L</div><div class="mval {dcls}">${day_pl:+,.0f}</div></div>
  <div class="mcell"><div class="mlbl">Win rate</div><div class="mval tgold">{wr}</div></div>
  <div class="mcell"><div class="mlbl">Open positions</div><div class="mval">{open_n}</div></div>
</div>
""", unsafe_allow_html=True)

# ---- signals table ----
st.markdown("### Signals · Donchian-1h trend")
rows = []
for s in SYMBOLS:
    dd = daily(s)
    closes = [x[4] for x in dd]
    ltp = closes[-1] if closes else d.get("last_prices", {}).get(s, 0)
    sig = signal_1h(s)
    held = positions.get(s, {}).get("side", 0)
    sig_html = ('<span class="long">LONG</span>' if sig > 0 else
                '<span class="short">SHORT</span>' if sig < 0 else '<span class="flat">FLAT</span>')
    held_html = ("🟢 held" if held == sig and held != 0 else "—")

    def c(v):
        if v is None:
            return "<td class='tgrey'>—</td>"
        return f"<td class='{'pos' if v>=0 else 'neg'}'>{v:+.2f}%</td>"
    rows.append(
        f"<tr><td><b>{s}</b><span class='tag'>PERP</span></td>"
        f"<td>{sig_html}</td><td>{ltp:,.2f}</td>"
        f"{c(pct(closes,1))}{c(pct(closes,7))}{c(pct(closes,30))}"
        f"<td class='tgrey'>{held_html}</td></tr>")
st.markdown(
    "<table class='sig'><thead><tr><th>symbol</th><th>signal</th><th>LTP</th>"
    "<th>1D</th><th>1W</th><th>1M</th><th>position</th></tr></thead><tbody>"
    + "".join(rows) + "</tbody></table>", unsafe_allow_html=True)

# ---- candlestick chart with Donchian channel ----
st.markdown("### Chart · daily candles + Donchian channel")
sym = st.selectbox("symbol", SYMBOLS, label_visibility="collapsed")
dd = daily(sym)
if len(dd) > 30:
    highs = [x[2] for x in dd]
    lows = [x[3] for x in dd]
    up = indicators.rolling_max(highs, 20)
    lo = indicators.rolling_min(lows, 20)
    candles = [{"time": int(x[0]), "open": x[1], "high": x[2], "low": x[3], "close": x[4]} for x in dd]
    upper = [{"time": int(dd[i][0]), "value": up[i]} for i in range(len(dd)) if up[i] is not None]
    lower = [{"time": int(dd[i][0]), "value": lo[i]} for i in range(len(dd)) if lo[i] is not None]
    vol = [{"time": int(x[0]), "value": x[5],
            "color": "#1f5c42" if x[4] >= x[1] else "#5c2a2d"} for x in dd]
    tmpl = """
<div id="chart" style="height:380px;width:100%"></div>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<script>
(function(){
  if(!window.LightweightCharts){document.getElementById('chart').innerHTML='<p style="color:#7c8983;font-family:monospace">chart library blocked — check network</p>';return;}
  var chart=LightweightCharts.createChart(document.getElementById('chart'),{
    layout:{background:{color:'#070a09'},textColor:'#7c8983'},
    grid:{vertLines:{color:'#141a17'},horzLines:{color:'#141a17'}},
    rightPriceScale:{borderColor:'#1c2521'},timeScale:{borderColor:'#1c2521',timeVisible:false},
    height:380,autoSize:true});
  var cs=chart.addCandlestickSeries({upColor:'#37d07f',downColor:'#e5484d',borderVisible:false,wickUpColor:'#37d07f',wickDownColor:'#e5484d'});
  cs.setData(__CANDLES__);
  var u=chart.addLineSeries({color:'#ef7d4b',lineWidth:1,lastValueVisible:false}); u.setData(__UPPER__);
  var l=chart.addLineSeries({color:'#3aa0ff',lineWidth:1,lastValueVisible:false}); l.setData(__LOWER__);
  var v=chart.addHistogramSeries({priceFormat:{type:'volume'},priceScaleId:''}); v.setData(__VOL__);
  v.priceScale().applyOptions({scaleMargins:{top:0.82,bottom:0}});
  chart.timeScale().fitContent();
})();
</script>"""
    chart_html = (tmpl.replace("__CANDLES__", json.dumps(candles))
                  .replace("__UPPER__", json.dumps(upper))
                  .replace("__LOWER__", json.dumps(lower))
                  .replace("__VOL__", json.dumps(vol)))
    st.components.v1.html(chart_html, height=400)
    st.caption("orange = 20-day Donchian upper · blue = lower · volume bars. "
               "The engine trades the 1h Donchian; this daily view is for context.")
else:
    st.caption("Not enough candle history yet — check back after the next fetch.")

# ---- combined equity curve ----
st.markdown("### Portfolio P&L · combined equity curve (paper)")
if len(hist) > 1:
    ec = pd.DataFrame({"time": [datetime.fromtimestamp(h["t"], timezone.utc) for h in hist],
                       "equity": [h["equity"] for h in hist]}).set_index("time")
    try:
        st.line_chart(ec, height=240, color="#37d07f")
    except TypeError:
        st.line_chart(ec, height=240)
else:
    st.caption("P&L curve builds as the hourly engine runs.")

st.markdown(f'<div class="foot">Trishula · paper on live Delta prices · served from your droplet · '
            f'updated {ist.strftime("%H:%M:%S")} IST</div>', unsafe_allow_html=True)
