#!/usr/bin/env python3
"""Tiny live server for the Trishula paper dashboard.

Renders the paper account fresh from data/paper_portfolio.json on every page
load, so opening the URL always shows the current state. Pure standard library.

Run persistently on the droplet:
    cd /home/globalbot/trishula-crypto
    nohup python3 scripts/serve_dashboard.py --port 8787 > data/dashboard.log 2>&1 &

Then open  http://<droplet-ip>:8787  on your phone. (Open the port in your
firewall/cloud console if it doesn't load.)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    from trishula import paper_report
except ModuleNotFoundError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from trishula import paper_report

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "data", "paper_portfolio.json")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] not in ("/", "/index.html", "/dashboard", "/paper"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            with open(STATE) as fh:
                body = paper_report.render_html(json.load(fh)).encode()
        except FileNotFoundError:
            body = (b"<meta http-equiv='refresh' content='10'>"
                    b"<body style='background:#070a09;color:#cdd6d1;font-family:monospace'>"
                    b"<h2>Trishula paper: no account yet</h2>"
                    b"<p>Run the engine once, then refresh.</p></body>")
        except Exception as exc:  # noqa: BLE001
            body = f"<pre>dashboard error: {exc}</pre>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Serve the Trishula paper dashboard")
    ap.add_argument("--port", type=int, default=int(os.getenv("DASHBOARD_PORT", "8787")))
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    print(f"Trishula dashboard on http://{args.host}:{args.port}  (state: {STATE})")
    HTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
