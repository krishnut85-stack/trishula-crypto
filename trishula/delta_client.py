"""Signed REST client for Delta Exchange **India**.

Auth (verified against Delta India docs, July 2026):

    prehash   = method + timestamp + path + query_string + body
    signature = HMAC-SHA256(secret, prehash).hexdigest()
    headers   = api-key, timestamp (UNIX SECONDS), signature
    validity  = 5 seconds  ->  host clock MUST be NTP-synced or you get
                SignatureExpired.

Rules honoured here:
  * India endpoints only (never the global api.delta.exchange).
  * Never hardcode tick/lot/product_id -> fetched from /v2/products and cached.
  * No order-placement path ships in this module (PAPER_MODE_HARD). Read and
    signed-read paths only; a live execution layer must be added deliberately
    and gated behind ``config.assert_live_authorized``.

Rate limit: 500 operations/sec per product (caller's responsibility to pace).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

import requests

from .config import CONFIG


class DeltaError(RuntimeError):
    """Raised on transport errors or Delta API error responses."""


class DeltaClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        timeout: int = 10,
    ) -> None:
        self.base_url = (base_url or CONFIG.delta_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else CONFIG.delta_api_key
        self.api_secret = (
            api_secret if api_secret is not None else CONFIG.delta_api_secret
        )
        self.timeout = timeout
        self.session = requests.Session()
        self._product_cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ signing
    def _sign(self, method: str, path: str, query: str = "", body: str = ""):
        ts = str(int(time.time()))  # SECONDS, not milliseconds
        prehash = method + ts + path + query + body
        sig = hmac.new(
            self.api_secret.encode(), prehash.encode(), hashlib.sha256
        ).hexdigest()
        return ts, sig

    def _headers(self, method, path, query="", body="", signed=False):
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "trishula-crypto/0.1",
        }
        if signed:
            if not self.api_key or not self.api_secret:
                raise DeltaError("API key/secret required for a signed request")
            ts, sig = self._sign(method, path, query, body)
            h.update({"api-key": self.api_key, "timestamp": ts, "signature": sig})
        return h

    def _request(self, method, path, params=None, payload=None, signed=False):
        # Build the exact query string that is BOTH signed and sent.
        query = ""
        if params:
            query = "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        body = ""
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":"))

        url = self.base_url + path + query
        headers = self._headers(method, path, query, body, signed=signed)
        try:
            resp = self.session.request(
                method, url, data=(body or None), headers=headers, timeout=self.timeout
            )
        except requests.RequestException as exc:  # pragma: no cover - network
            raise DeltaError(f"{method} {path} transport error: {exc}") from exc

        if resp.status_code >= 400:
            raise DeltaError(
                f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        if isinstance(data, dict) and data.get("success") is False:
            raise DeltaError(f"{method} {path} -> API error: {data}")
        return data

    # ------------------------------------------------------------------- public
    def get_products(self):
        """All products (no auth). Source of truth for contract specs."""
        return self._request("GET", "/v2/products").get("result", [])

    def get_tickers(self):
        return self._request("GET", "/v2/tickers").get("result", [])

    def product_spec(self, symbol: str, refresh: bool = False) -> Dict[str, Any]:
        """Return the cached spec (tick/lot/product_id/...) for ``symbol``.

        Fetches and caches /v2/products on first use. Never hardcode specs;
        contract identifiers change across expiries.
        """
        if refresh or not self._product_cache:
            for p in self.get_products():
                sym = p.get("symbol")
                if sym:
                    self._product_cache[sym] = p
        spec = self._product_cache.get(symbol)
        if spec is None:
            raise DeltaError(f"Unknown symbol {symbol!r} (not in /v2/products)")
        return spec

    # ------------------------------------------------------------ private (read)
    def get_wallet_balances(self):
        return self._request("GET", "/v2/wallet/balances", signed=True).get("result")

    def get_positions(self, **params):
        return self._request(
            "GET", "/v2/positions", params=(params or None), signed=True
        ).get("result")

    def get_orders(self, **params):
        return self._request(
            "GET", "/v2/orders", params=(params or None), signed=True
        ).get("result")

    # NOTE: order placement / modify / cancel are intentionally NOT implemented
    # here. They belong in a separate, deliberately-wired execution module that
    # calls config.assert_live_authorized() before any live request.
