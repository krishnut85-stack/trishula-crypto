"""Render the live PAPER account as a self-contained HTML dashboard.

Same idea as Garuda's report.py: pure standard library, no templating deps —
turn the saved paper portfolio into a single HTML page. Auto-refreshes so an
open browser tab stays live as the hourly engine updates the state.
"""
from __future__ import annotations

import html
import time
from typing import Optional


def _fmt(v, dp=2):
    return f"{v:,.{dp}f}"


def render_html(d: dict, refresh_secs: int = 30) -> str:
    capital = d.get("capital", 10000.0)
    hist = d.get("equity_history", [])
    equity = hist[-1]["equity"] if hist else d.get("cash", capital)
    ret = (equity / capital - 1) * 100 if capital else 0.0
    realized = d.get("realized", 0.0)
    positions = d.get("positions", {})
    last_prices = d.get("last_prices", {})
    trades = d.get("trades", [])
    updated = d.get("updated") or 0
    upd = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(updated)) if updated else "—"

    closed = [t for t in trades if t.get("action") == "close"]
    wins = [t for t in closed if t.get("pnl", 0) > 0]
    wr = f"{len(wins)/len(closed)*100:.0f}%" if closed else "n/a"

    # equity curve points (downsample to ~300)
    pts = [h["equity"] for h in hist] or [capital]
    if len(pts) > 300:
        step = len(pts) / 300
        pts = [pts[int(i * step)] for i in range(300)]
    pts_js = ",".join(f"{p:.2f}" for p in pts)

    # open positions rows
    prow = []
    for s, p in positions.items():
        side = p.get("side", 0)
        if not side:
            continue
        entry = p.get("entry", 0)
        cur = last_prices.get(s, entry)
        units = p.get("units", 0)
        upnl = side * units * (cur - entry)
        upct = (cur / entry - 1) * 100 * side if entry else 0
        cls = "pos" if upnl >= 0 else "neg"
        prow.append(
            f"<tr><td>{html.escape(s)}</td>"
            f"<td class='{'pos' if side>0 else 'neg'}'>{'LONG' if side>0 else 'SHORT'}</td>"
            f"<td class='num'>{_fmt(entry)}</td><td class='num'>{_fmt(cur)}</td>"
            f"<td class='num {cls}'>{upnl:+,.2f}</td>"
            f"<td class='num {cls}'>{upct:+.2f}%</td></tr>")
    if not prow:
        prow.append("<tr><td colspan='6' class='muted'>flat — all cash</td></tr>")

    # recent trades (last 12, newest first)
    trow = []
    for t in reversed(trades[-12:]):
        ts = time.strftime("%m-%d %H:%M", time.gmtime(t.get("t", 0)))
        act = t.get("action", "")
        side = t.get("side", 0)
        pnl = t.get("pnl")
        pnl_html = (f"<span class='{'pos' if pnl>=0 else 'neg'}'>{pnl:+,.2f}</span>"
                    if pnl is not None else "—")
        trow.append(
            f"<tr><td>{ts}</td><td>{html.escape(t.get('symbol',''))}</td>"
            f"<td>{html.escape(act)}</td>"
            f"<td class='{'pos' if side>0 else 'neg'}'>{'L' if side>0 else 'S'}</td>"
            f"<td class='num'>{pnl_html}</td></tr>")
    if not trow:
        trow.append("<tr><td colspan='5' class='muted'>no trades yet</td></tr>")

    ret_cls = "pos" if ret >= 0 else "neg"
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh_secs}">
<title>Trishula Paper</title>
<style>
:root{{--bg:#070a09;--surface:#0f1412;--surface2:#0b100e;--border:#1c2521;
--text:#cdd6d1;--muted:#7c8983;--accent:#ef7d4b;--pos:#37d07f;--neg:#e5484d;
--mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;}}
*{{box-sizing:border-box;}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:var(--mono);font-size:13px;-webkit-font-smoothing:antialiased;}}
.wrap{{max-width:900px;margin:0 auto;padding:16px;}}
.bar{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 14px;border:1px solid var(--border);background:var(--surface);border-radius:8px;}}
.bar h1{{font-size:14px;margin:0;letter-spacing:.16em;margin-right:auto;}}
.pill{{display:inline-flex;align-items:center;gap:6px;padding:4px 9px;border-radius:999px;font-size:10px;letter-spacing:.1em;text-transform:uppercase;border:1px solid var(--border);color:var(--muted);background:var(--surface2);}}
.pill.live{{color:var(--pos);border-color:color-mix(in srgb,var(--pos) 45%,var(--border));}}
.dot{{width:6px;height:6px;border-radius:50%;background:currentColor;animation:b 1.6s steps(2) infinite;}}
@keyframes b{{50%{{opacity:.25;}}}}
.hero{{margin-top:12px;padding:16px;border:1px solid var(--border);background:var(--surface);border-radius:8px;}}
.eq{{font-size:clamp(30px,7vw,46px);font-weight:700;line-height:1;font-variant-numeric:tabular-nums;}}
.ret{{font-size:16px;font-weight:700;margin-left:8px;}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:14px;}}
.stat{{padding:8px;border:1px solid var(--border);border-radius:6px;background:var(--surface2);}}
.lbl{{text-transform:uppercase;letter-spacing:.1em;font-size:9px;color:var(--muted);}}
.stat .v{{font-size:16px;font-weight:700;margin-top:3px;font-variant-numeric:tabular-nums;}}
.panel{{margin-top:12px;padding:14px;border:1px solid var(--border);background:var(--surface);border-radius:8px;}}
.panel h2{{font-size:11px;margin:0 0 10px;letter-spacing:.14em;text-transform:uppercase;}}
canvas{{display:block;width:100%;height:auto;border-radius:6px;}}
table{{width:100%;border-collapse:collapse;font-size:12px;}}
th,td{{text-align:left;padding:7px 8px;border-bottom:1px dashed var(--border);}}
th{{color:var(--muted);text-transform:uppercase;font-size:9px;letter-spacing:.08em;}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;}}
.pos{{color:var(--pos);}} .neg{{color:var(--neg);}} .muted{{color:var(--muted);}}
footer{{margin-top:12px;color:var(--muted);font-size:10px;letter-spacing:.1em;text-transform:uppercase;text-align:center;}}
</style></head><body><div class="wrap">
<div class="bar">
  <h1>🔱 TRISHULA QUANT</h1>
  <span class="pill live"><span class="dot"></span> PAPER</span>
  <span class="pill">DELTA INDIA</span>
  <span class="pill">{upd}</span>
</div>

<div class="hero">
  <div class="lbl">Paper equity · Donchian-1h trend</div>
  <div><span class="eq {ret_cls}">${_fmt(equity)}</span><span class="ret {ret_cls}">{ret:+.2f}%</span></div>
  <div class="stats">
    <div class="stat"><div class="lbl">Start</div><div class="v">${_fmt(capital,0)}</div></div>
    <div class="stat"><div class="lbl">Realised</div><div class="v {'pos' if realized>=0 else 'neg'}">${_fmt(realized)}</div></div>
    <div class="stat"><div class="lbl">Closed</div><div class="v">{len(closed)}</div></div>
    <div class="stat"><div class="lbl">Win rate</div><div class="v">{wr}</div></div>
  </div>
</div>

<div class="panel">
  <h2>Equity curve · forward paper</h2>
  <canvas id="eq" width="860" height="160"></canvas>
</div>

<div class="panel">
  <h2>Open positions</h2>
  <table><thead><tr><th>symbol</th><th>side</th><th>entry</th><th>price</th><th>uP&amp;L</th><th>%</th></tr></thead>
  <tbody>{''.join(prow)}</tbody></table>
</div>

<div class="panel">
  <h2>Recent trades</h2>
  <table><thead><tr><th>time</th><th>symbol</th><th>action</th><th>side</th><th>pnl</th></tr></thead>
  <tbody>{''.join(trow)}</tbody></table>
</div>

<footer>PAPER MODE HARD · NOT ADVICE · auto-refresh {refresh_secs}s</footer>
</div>
<script>
(function(){{
  var pts=[{pts_js}];
  var cv=document.getElementById('eq'),dpr=Math.min(window.devicePixelRatio||1,2);
  var r=cv.getBoundingClientRect(),w=r.width||cv.width,h=cv.height*(r.width?r.width/cv.width:1);
  cv.width=w*dpr;cv.height=h*dpr;cv.style.height=h+'px';
  var x=cv.getContext('2d');x.setTransform(dpr,0,0,dpr,0,0);
  var css=getComputedStyle(document.documentElement);
  var up=pts[pts.length-1]>=pts[0],col=css.getPropertyValue(up?'--pos':'--neg').trim();
  var lo=Math.min.apply(null,pts),hi=Math.max.apply(null,pts),rng=(hi-lo)||1,pad=6;
  x.strokeStyle='rgba(255,255,255,.05)';for(var g=1;g<=3;g++){{var yy=h/4*g;x.beginPath();x.moveTo(0,yy);x.lineTo(w,yy);x.stroke();}}
  function X(i){{return pad+i/(pts.length-1||1)*(w-pad*2);}}
  function Y(v){{return (h-pad)-(v-lo)/rng*(h-pad*2);}}
  x.beginPath();x.moveTo(X(0),h-pad);for(var i=0;i<pts.length;i++)x.lineTo(X(i),Y(pts[i]));
  x.lineTo(X(pts.length-1),h-pad);x.closePath();x.globalAlpha=.13;x.fillStyle=col;x.fill();x.globalAlpha=1;
  x.beginPath();for(i=0;i<pts.length;i++){{i?x.lineTo(X(i),Y(pts[i])):x.moveTo(X(i),Y(pts[i]));}}
  x.strokeStyle=col;x.lineWidth=1.6;x.stroke();
  x.beginPath();x.arc(X(pts.length-1),Y(pts[pts.length-1]),3,0,7);x.fillStyle=col;x.fill();
}})();
</script>
</body></html>"""


def write_dashboard(state: dict, out_path: str, refresh_secs: int = 30) -> str:
    import os
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write(render_html(state, refresh_secs))
    return out_path
