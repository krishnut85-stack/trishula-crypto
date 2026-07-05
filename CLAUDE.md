# Trishula 🔱 — Build Brief (Claude Code context)

Auto-loaded project context. Self-contained: assume no other memory.
Trishula is the crypto counterpart to the NSE equity system (SectorBot / Garuda)
and is a **brand-new, standalone codebase** in this repo.

---

## 1. Mission

Autonomous crypto algo-trading system that trades **perpetual futures and
options on Delta Exchange India**, runs unattended on a droplet, and stays in
**paper mode** until explicitly promoted to live. Philosophy: zero manual
intervention, aggressive capital protection, automatic compounding.

## 2. Standing order — audit before building

Before any change: grep for existing logic, trace call sites, map overlapping
paths (place/modify/cancel/audit/trail/sync/restart), confirm no conflict, and
state what exists vs. what is new. Do not rebuild modules that already work.

Prior droplet work (separate legacy prefix `gir-crypto-*`, may/may not be live):
`gir-crypto-options-feed`, `gir-crypto-iv-rank`, `gir-crypto-vol-regime`. When on
the droplet, check `systemctl list-units 'gir-crypto*'` and read
`/home/globalbot/gircrypto/` before duplicating anything.

## 3. Platform & API — Delta Exchange India

Use **India** endpoints, NOT the global `api.delta.exchange`.

| Item | Value |
|---|---|
| REST base | `https://api.india.delta.exchange` |
| WebSocket | `wss://socket.india.delta.exchange` |
| Testnet | available — use for paper/integration testing |
| Auth headers | `api-key`, `signature`, `timestamp` |
| Signature | HMAC-SHA256 hexdigest over `method + timestamp + path + query + body` |
| Timestamp | **Unix seconds** (not ms). Valid **5 s** → droplet MUST be NTP-synced |
| IP whitelist | trading keys require the droplet IP whitelisted |
| Rate limit | 500 ops/sec **per product** |

**Never hardcode** contract specs (tick/lot/product_id) — fetch `/v2/products`
at startup and cache; identifiers change across expiries. Implemented in
`trishula/delta_client.py` (`product_spec`).

Public: `/v2/products`, `/v2/tickers`. Private (signed): `/v2/orders`,
`/v2/orders/bracket`, `/v2/positions`, `/v2/wallet/balances`.

## 4. Tax module rules (India)

⚠️ Re-verify with a web search before finalizing any tax code — not CBDT-settled.

As last verified (Delta India FAQ, May 2026): Delta India **perps + options** →
no 1% TDS, no 30% flat VDA; **business/speculative income at slab**, loss offset
allowed. Cost per trade = fees + 18% GST on the fee (~0.118% round-trip at 0.05%
taker). **Spot** incurs 1% TDS + 30% flat + no offset → the bot must never trade
spot. Build the ledger **dual-track**: log both an AGGRESSIVE (slab) and a
CONSERVATIVE (30% flat VDA) view. FIU-IND venues only — never
Binance/OKX/Bybit/Deribit.

## 5. Architecture (V100 reference)

Independent parallel engines → coordinator, not a single funnel:

- **Engine 1 — Ensemble:** ~100 perspectives = deterministic Python scorers
  (EMA cross, RSI, VWAP, Bollinger, orderbook imbalance, volume surge, price
  velocity) + a smaller set of LLM personas. "100 AI" = 100 *perspectives*,
  mostly deterministic — NOT 100 LLM calls.
- **Engine 2 — News/Catalyst:** Gemini + RSS + on-chain.
- **Engine 3 — Momentum:** pure price/volume; daily trend → 4H momentum → 1H
  trigger; no AI.
- **Coordinator:** de-dupe, sector caps, correlation limits.
- **Execution router:** high conviction → options; medium → perp; range + high
  IV → sell premium.
- **Safety rails:** cooldown, dedup, Gemini budget guard, position sizer, trade
  manager.

Feed via Delta WebSocket L2.

## 6. Environment & conventions

- **Deploy target:** droplet (BLR1). Operator uses ConnectBot SSH on mobile →
  outputs must be copy-paste-able, single actions, no long interactive sessions.
- **Credentials:** `/home/globalbot/.env`. Never ask for or guess them. Add
  Delta key/secret if missing. No access token stored — sign per request.
- **Run standalone:** `cd /home/globalbot && set -a && source .env && set +a && python3 script.py`
- **Services:** systemd units — new prefix `trishula-*`, run as `/usr/bin/python3`.
- Fully separate from the NSE equity system (`gir.py`, `globaleye.service`,
  SectorBot). Do NOT touch NSE code.

## 7. Hard rules (non-negotiable)

1. **PAPER_MODE_HARD** — paper until explicitly authorized live; no live-order
   path reachable by default (see `trishula/config.assert_live_authorized`).
2. **Pre-code audit** (standing order, §2).
3. **Never hardcode** tick/lot/product IDs — fetch `/v2/products`.
4. **Web-search current exchange/tax rules** before writing API/tax code.
5. **Backtester FIRST** — build and validate it before new strategy brains.
6. Timestamped backup before editing any file; AST-validate Python after edits;
   rollback line on every deploy.
7. Combine bash into **one block** per step.
8. Deterministic core, LLM as advisory layer — never a single point of failure
   in the execution path.

## 8. Build order

1. ✅ Config + signed REST client + `/v2/products` + `/v2/positions` smoke test
   (testnet).
2. WebSocket L2 feed with reconnect + heartbeat.
3. Backtester over historical Delta data.
4. Dual-track tax ledger (§4).
5. Safety rails: cooldown, anti-dup, budget guard, position sizer.
6. One deterministic momentum engine end-to-end in paper mode → validate the
   trade lifecycle.
7. Ensemble scorers, news engine, options brain, coordinator, router.
8. Telegram reporting per lane + dashboard/UI (to match the reference model).
