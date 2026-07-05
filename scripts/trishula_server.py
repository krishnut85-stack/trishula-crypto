#!/usr/bin/env python3
"""Trishula live dashboard server — stdlib only (Garuda's server pattern).

Serves the professional dashboard + a token-protected /data JSON endpoint priced
from live Delta candles, plus /chart for on-demand symbol charts. A background
thread refreshes prices/candles on a timer.

Run on the droplet:
    cd /home/globalbot/trishula-crypto
    nohup python3 scripts/trishula_server.py --token trishulaLIVE2026 --port 8503 \
        > data/webserver.log 2>&1 &

Open:  http://<droplet-ip>:8503/?token=trishulaLIVE2026
PAPER ONLY — never places a real order.
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trishula.live_state import TrishulaLive  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HTML = os.path.join(HERE, "dashboard", "dashboard_live.html")
_STATE = {"json": b'{"profiles":[]}'}
_TOKEN = {"value": ""}
_LIVE = {"obj": None}


def _refresh_loop(live, every=15.0):
    tick = 0
    while True:
        try:
            live.refresh(full=(tick % 40 == 0))     # re-pull universe every ~10 min
            # keep the charts of the traded coins warm
            if tick % 4 == 0:
                for s in ("BTCUSD", "ETHUSD", "SOLUSD"):
                    live.refresh_chart(s)
            _STATE["json"] = json.dumps(live.build_state()).encode()
        except Exception as exc:  # noqa: BLE001 — never let the loop die
            print(f"[refresh] {exc}", flush=True)
        tick += 1
        time.sleep(every)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _tok_ok(self):
        q = parse_qs(urlparse(self.path).query)
        return _TOKEN["value"] and q.get("token", [""])[0] == _TOKEN["value"]

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._send(200, b"ok", "text/plain")
        if not self._tok_ok():
            return self._send(403, b"denied", "text/plain")
        if path == "/data":
            return self._send(200, _STATE["json"], "application/json")
        if path == "/chart":
            sym = parse_qs(urlparse(self.path).query).get("sym", [""])[0].upper()
            ch = _LIVE["obj"].chart_for(sym) if (_LIVE["obj"] and sym) else None
            return self._send(200, json.dumps(ch or {}).encode(), "application/json")
        with open(_HTML, "rb") as fh:      # fresh read so `git pull` updates without restart
            return self._send(200, fh.read(), "text/html")

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)


def main():
    args = sys.argv[1:]

    def opt(f, cast, d):
        return cast(args[args.index(f) + 1]) if f in args else d

    token = opt("--token", str, os.getenv("TRISHULA_DASH_TOKEN", ""))
    if not token:
        raise SystemExit("--token YOURSECRET is required (protects the dashboard)")
    _TOKEN["value"] = token
    port = opt("--port", int, 8503)
    top_n = opt("--top", int, 200)

    live = TrishulaLive(top_n=top_n)
    _LIVE["obj"] = live
    print("seeding universe + prices (first pull ~1 min)…", flush=True)
    try:
        live.refresh(full=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[seed] {exc}", flush=True)
    _STATE["json"] = json.dumps(live.build_state()).encode()
    threading.Thread(target=_refresh_loop, args=(live,), daemon=True).start()

    print(f"Trishula dashboard: http://0.0.0.0:{port}/?token={token}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
