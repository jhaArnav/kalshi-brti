# ARCHITECTURE — components, data sources, schemas

## Data flow

```
spot exchanges (Coinbase/Kraken/Bitstamp/Gemini)  ──websocket──┐
                                                               ├─► brti_proxy.book ─► consolidated index
Kalshi WS (live market data)  ──websocket──────────────────────┘                         │
                                                                                         ▼
                                          dashboard/server.py  ── merged state ─15Hz─► browser
                                          data/logger.py       ── aligned rows ─────► sqlite/parquet dataset
                                                                                         │
                                                                          model/ + backtest/ (Phase 1)
```

## Components

| Path | Role | Phase |
|---|---|---|
| `config/settings.py`, `config/default.toml` | typed config; ALL costs/fees/latency/thresholds | 0 |
| `data/brti_proxy.py` | consolidated volume-weighted BTC index proxy from 4 spot venues | 0 |
| `data/kalshi_client.py` | Kalshi REST (markets, orderbook, candlesticks, trades); RSA-PSS auth | 0 |
| `data/kalshi_ws.py` | live Kalshi market data over WS | 0 |
| `data/logger.py` | time-synced dataset writer (UTC-ms aligned), run manifest | 0 |
| `dashboard/server.py` | FastAPI; runs feeds in-process; 15Hz browser push | 0 |
| `dashboard/fair_naive.py` | **PROXY** fair value + EWMA vol (NOT the real model) | 0 |
| `dashboard/static/` | lightweight-charts viewing UI | 0 |
| `model/vol.py`, `model/fair_value.py` | the REAL digital-option fair value + vol | 1 |
| `backtest/` | engine, execution (costs/latency), controls (random/walk-forward), report | 1 |
| `paper/` | live simulated trading, zero real money | 2 |
| `live/` | GATED real-money execution — empty until approved | 3 |

## Kalshi API facts (verified live)

- REST base: `https://api.elections.kalshi.com/trade-api/v2`. WS base:
  `wss://api.elections.kalshi.com/trade-api/ws/v2`.
- Auth: RSA-PSS(SHA256) over `timestamp_ms + METHOD + path` (path WITHOUT query
  for REST; `GET` + `/trade-api/ws/v2` for the WS handshake). Headers:
  `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP` (ms), `KALSHI-ACCESS-SIGNATURE`.
- `GetMarkets` status FILTER aliases: `open|settled|unopened|closed` (NOT the
  object's `status` enum). One open `KXBTC15M` market at a time.
- **Prices are dollar floats** with deci-cent ticks (`tapered_deci_cent`,
  0.001 steps near the tails). Use `*_dollars` fields. Never integer cents.
- Market record carries `floor_strike` (the strike) and `expiration_value`
  (settlement reference; EMPTY string until the market settles).
- Settled markets backfill ~2 days via `status=settled`; 1-min candlesticks
  available per market via `/series/{s}/markets/{t}/candlesticks`.

### Kalshi WS channels (verified)
- `orderbook_delta` — full snapshot then deltas. **Reconstruction here drifts/
  desyncs (see STATUS bug #1) — do NOT trust a hand-maintained top-of-book.**
- `ticker` — **authoritative live top-of-book + last price**, pushed on every
  change. Payload: `price_dollars`, `yes_bid_dollars`, `yes_ask_dollars`,
  `volume_fp`, `market_ticker`, ... This is the correct source for the live
  Kalshi YES price.
- `ticker_v2` — DOES NOT EXIST ("Unknown channel name"). Do not use.

## Server → browser WS message (current contract)

```json
{
  "ts": 1781992155100,            // server time, UNIX ms
  "brti": 63955.96,               // consolidated BRTI PROXY index, USD (nullable)
  "brti_spread": 9.51,            // cross-venue dispersion, USD
  "n_venues": 4,
  "spot": {"Coinbase":..., "Kraken":..., "Bitstamp":..., "Gemini":...},
  "kalshi": {
    "ticker": "KXBTC15M-...-...",
    "yes_bid": 0.54, "yes_ask": 0.55, "yes_mid": 0.545,  // dollars 0..1
    "crossed": false,                                     // bid >= ask
    "strike": 64068.34, "strike_type": "greater_or_equal",
    "close_ts": 1781992800000, "secs_to_close": 412.0,
    "age_ms": 26                                          // SEE bug #1: must be DATA age
  },
  "fair": 0.47, "signal": 0.02, "sigma_per_sec": 6.1e-05
}
```

Planned additions (per STATUS bug fixes): per-feed real data-age timestamps,
a `data_ok` / suppression flag, and a `proxy_vs_reference` gap field.

## Reference-price alignment (the trap)

There are THREE different BTC numbers and they must not be conflated:
1. **our BRTI proxy** (`brti`) — what `fair_naive` currently uses.
2. **true BRTI** (CF Benchmarks) — the actual settlement index; published live
   per-second at cfbenchmarks.com (licensing caveat for productizing).
3. **Kalshi's reference** — the market settles on BRTI (=2); `floor_strike` is
   the locked opening 60s-BRTI avg, `expiration_value` the closing avg.

RESOLVED: Kalshi exposes **no live mid-window reference** via API — only the
`floor_strike` open anchor (live) and `expiration_value` (post-settlement). So
we de-bias our proxy against `floor_strike` and trust only its change since open
(see docs/MODEL.md D11), and DISPLAY the discrepancy decomposed into proxy-drift
vs residual so drift can't masquerade as a signal (STATUS bug #3).
