# Trishula 🔱

Autonomous crypto algorithmic trading system for **Delta Exchange India**
(perpetual futures + options). Runs unattended, **paper-mode-hard** until
explicitly promoted to live. Three engines — Ensemble, News/Catalyst, Momentum
— feed one coordinator and an execution router (the three prongs of the
trishula).

> ⚠️ **This is NOT investment advice and NOT a profit guarantee.**
> It runs in **paper mode by default** (simulated, zero real orders). Live
> trading is off and requires two explicit switches. Automated trading can lose
> real money fast — test on testnet for a long time first.

Trishula is a **standalone system**, fully separate from the NSE equity system
(SectorBot / Garuda). It only trades on FIU-IND registered venues — never spot,
never Binance/OKX/Bybit/Deribit.

## Status

What's in place:

- `trishula/config.py` — safety-first config; `PAPER_MODE` hard-on by default.
- `trishula/delta_client.py` — signed REST client for Delta **India**
  (HMAC-SHA256, Unix-seconds timestamp, India endpoints). Read + signed-read
  paths only; no order placement ships yet.
- `scripts/smoke_test.py` — testnet smoke test: products + spec cache, signed
  positions/balances. Places no orders.
- **Backtester (Garuda-style):** `trishula/history.py` (Delta candle fetch +
  cache + synthetic), `indicators.py`, `strategies.py` (EMA cross, RSI reversion,
  Donchian breakout, Bollinger reversion, buy-hold), `backtest.py` (cost-aware
  walk-forward engine), `scorecard.py` (honest verdict vs buy-and-hold), and
  `scripts/run_backtest.py` (strategy leaderboard — "which strategy is working").

Next (per build order): WebSocket L2 feed → dual-track tax ledger → safety rails
→ one momentum engine end-to-end → ensemble/news/options/router → Telegram
reporting. UI/dashboard to match the reference model.

## Quick start

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env          # fill DELTA_API_KEY / DELTA_API_SECRET, point
                              # DELTA_BASE_URL at the testnet host

# Rank strategies by cost-aware edge vs buy-and-hold (REAL Delta candles):
python3 scripts/run_backtest.py --symbols BTCUSD,ETHUSD,SOLUSD --resolution 1h --days 180

# Offline validation anywhere (synthetic candles, clearly labelled):
python3 scripts/run_backtest.py --synthetic

# Testnet connectivity check (no orders):
python3 scripts/smoke_test.py
```

**Benchmark = buy-and-hold** (the crypto "index"). A strategy has to beat just
holding the coin *after costs*, or it's not worth trading — the same discipline
Garuda applies against the Nifty index.

**TradingView data:** there is no legal programmatic feed; TradingView is for
chart/visual confirmation (embed widget) or manual CSV export. The backtest data
source is **Delta candles**.

On the droplet:

```bash
cd /home/globalbot && set -a && source .env && set +a && python3 scripts/smoke_test.py
```

## Non-negotiable rules

1. **PAPER_MODE_HARD** — no live-order path reachable by default.
2. **Never hardcode** tick size, lot size, or product IDs — fetch `/v2/products`.
3. Web-search current exchange/tax rules before writing API or tax code.
4. Backtester before new strategy brains.
5. Droplet must be NTP-synced (5-second signature validity).

See `CLAUDE.md` for the full build brief.
