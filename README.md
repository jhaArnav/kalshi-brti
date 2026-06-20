# kalshi-brti

Research system to test a single falsifiable hypothesis on Kalshi's
15-minute BTC up/down markets (`KXBTC15M`):

> The gap between a **model fair value** of the 15m binary and the **live
> Kalshi price** predicts the contract's subsequent move — most strongly in
> high-vol windows where BRTI lags spot.
>
> `signal = kalshi_price − model_fair_value`

This is a **truth-seeking backtester, not a money-printer.** Default prior:
there is probably no durable edge after costs. A clean "no edge" result is a
successful outcome. The signal must beat a **random-entry control**
out-of-sample, after realistic fees and latency, or we declare no edge.

## Phased plan (hard gates)

| Phase | What | Gate to advance |
|---|---|---|
| **0** | Data infra: feeds + time-synced logger + backfill | dataset is clean & aligned |
| **1** | Fair-value + vol model, signal, backtest w/ full rigor suite | beats random control OOS, +EV after realistic fees |
| **2** | Paper trading, simulated fills, zero real money | live paper tracks backtest over weeks |
| **3** | Real money — **only on explicit written go-ahead** | tiny size, hard risk limits, kill switch |

We do **not** build real-money execution (`live/`) until explicitly approved
in plain language.

## Why 15m markets specifically

`KXBTC15M` settles against Kalshi's **own recorded reference prices on the
market record** (`floor_strike` / `expiration_value`) — no external index
license needed to know how a market resolved. That makes these the only
Kalshi BTC market we can backtest rigorously for free. (Hourly markets settle
vs license-gated CF Benchmarks BRRNY — out of scope.)

**Settlement catch the backtest must respect:** the underlying index is a
~60s trimmed average at the close, so only *persistent* spot moves change the
outcome. A spike that reverts doesn't move settlement. Edge (if any) lives
**mid-window on persistent moves** and decays to ~0 at the bell.

## Repo layout

```
config/      pydantic settings + default.toml (all costs/fees/latency/thresholds)
data/        brti_proxy.py (consolidated index feed), kalshi_client.py, logger.py
model/       vol.py (EWMA realized), fair_value.py (digital option)   [Phase 1]
backtest/    engine.py, execution.py, controls.py (random+walkforward), report.py [Phase 1]
paper/       live simulated trading                                    [Phase 2]
live/        GATED real-money execution — empty until approved         [Phase 3]
analysis/    notebooks
tests/       pytest
```

## Key design decisions

- **BRTI proxy is a proxy.** It diverges from true BRTI worst during fast
  moves — exactly the regime the signal lives in. Proxy tracking error is
  treated as a first-class source of *false* edge, not noise. `brti_spread`
  (cross-venue dispersion) is logged as a live uncertainty gauge.
- **Spot leaders:** Coinbase + Kraken, both logged; the actual leader is
  chosen empirically from the lag analysis, not assumed.
- **Vol model:** EWMA realized vol (RiskMetrics λ=0.94), tunable and
  stress-tested as a component.
- **Two fee profiles** modeled side by side: Kalshi promo zero-fee and a
  realistic fee schedule. Any edge that only survives at zero fees has an
  expiration date.
- **Fills at the touch** (buy@ask / sell@bid), never mid, plus modeled
  decision→fill latency and slippage.
- **Reproducible:** seeded random control, config-logged runs, versioned
  dataset.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # fill in Kalshi RSA API key id + private key PEM path
python config/settings.py   # sanity-check config loads
```

## Live dashboard (low-latency arbitrage terminal)

A real-time browser terminal to watch the spot -> BRTI -> Kalshi chain and the
gap, since that gap closes in ~10-14s. **No polling** -- everything is
websocket push:

- BRTI proxy feeds (sub-second) + Kalshi `orderbook_delta` (0-30ms freshness)
  run in-process; the server pushes merged state to the browser at ~15 Hz.
- Two time-synced panes (TradingView lightweight-charts): price ($) with the
  strike line + spot leaders + BRTI on top; Kalshi YES vs fair + the signal
  below. Timeframe windows, draw tools (H-lines / marks / notes), live latency
  indicator.
- `fair`/`signal` use a **naive proxy** (`dashboard/fair_naive.py`) until the
  Phase 1 model exists -- labeled PROXY in the UI.

```bash
source .venv/bin/activate
python -m dashboard.server      # then open http://127.0.0.1:8000
```

Needs Kalshi RSA creds in `.env` for the live Kalshi WS feed (the BRTI/spot
side needs nothing).

## Status

Phase 0 data layer complete and verified (config, BRTI proxy, authenticated
Kalshi REST + WS clients, time-synced logger, confirmed backfill source) plus
the live dashboard. Unit tests cover the order-book reconstruction and the
fair-value proxy. **Next gate: Phase 1** (real digital-option fair value +
EWMA vol estimator, signal, and the full backtest rigor suite -- random-entry
control, walk-forward OOS, realistic costs/latency) -- to be built only after
your review.
