#!/usr/bin/env python3
"""Trishula smoke test — run against Delta India TESTNET first.

Verifies: public /v2/products fetch + contract-spec caching, and (if keys are
present) a signed /v2/positions + /v2/wallet/balances read. Places NO orders.

Run:
    cd /home/globalbot && set -a && source .env && set +a && \
        python3 -m scripts.smoke_test        # or: python3 scripts/smoke_test.py

Set DELTA_BASE_URL to the testnet host before trusting live-India results.
Rollback: this script is read-only; nothing to roll back.
"""
from __future__ import annotations

import sys

try:
    from trishula.config import CONFIG
    from trishula.delta_client import DeltaClient, DeltaError
except ModuleNotFoundError:  # allow running as a bare file from repo root
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from trishula.config import CONFIG
    from trishula.delta_client import DeltaClient, DeltaError


def main() -> int:
    print(f"[trishula] env={CONFIG.delta_env}  base={CONFIG.delta_base_url}")
    print(f"[trishula] paper_mode={CONFIG.paper_mode}  live_enabled={CONFIG.live_enabled}")

    client = DeltaClient()

    # 1) Public: products + spec cache (never hardcode specs).
    try:
        products = client.get_products()
        print(f"[public ] /v2/products -> {len(products)} products")
        for sym in ("BTCUSD", "ETHUSD"):
            try:
                spec = client.product_spec(sym)
                print(
                    f"[public ]   {sym}: id={spec.get('id')} "
                    f"tick={spec.get('tick_size')} "
                    f"lot={spec.get('contract_value') or spec.get('lot_size')}"
                )
            except DeltaError as exc:
                print(f"[public ]   {sym}: {exc}")
    except DeltaError as exc:
        print(f"[public ] FAILED: {exc}")
        return 1

    # 2) Signed reads (only if credentials are configured).
    if not (CONFIG.delta_api_key and CONFIG.delta_api_secret):
        print("[signed ] skipped — no DELTA_API_KEY/SECRET in environment")
        return 0

    try:
        balances = client.get_wallet_balances()
        n = len(balances) if isinstance(balances, list) else "n/a"
        print(f"[signed ] /v2/wallet/balances -> {n} wallet rows")
        positions = client.get_positions()
        n = len(positions) if isinstance(positions, list) else "n/a"
        print(f"[signed ] /v2/positions -> {n} open positions")
    except DeltaError as exc:
        print(f"[signed ] FAILED: {exc}")
        print("[signed ] check: IP whitelisted? clock NTP-synced (SignatureExpired)?")
        return 1

    print("[trishula] smoke test OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
