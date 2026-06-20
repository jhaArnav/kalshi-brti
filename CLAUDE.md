# CLAUDE.md — kalshi-brti

Operating guide for working in this repo. Read this first, every session.
Repo: https://github.com/jhaArnav/kalshi-brti  ·  Local: `/Users/arnavjha/Desktop/trading`

## What this project is

A research system to test ONE falsifiable hypothesis on Kalshi's 15-minute
BTC up/down markets (`KXBTC15M`):

> The gap between a **model fair value** of the 15m binary and the **live
> Kalshi price** predicts the contract's next move — strongest when the
> settlement index (BRTI) lags spot.
>
> `signal = kalshi_price − model_fair_value`

This is a **truth-seeking tester, not a money-printer.** The default prior is
that there is NO durable edge after costs. A clean, well-supported "no edge"
is a SUCCESS. Never hype a result. If it doesn't beat the random-entry control
out-of-sample after realistic fees, say so plainly and loudly.

## Hard rules (do not violate)

1. **Phases are gated. Do not skip ahead.** 0 → 1 → 2 → 3. Each gate is the
   USER's decision, stated in plain language. See `docs/PROJECT.md`.
2. **No real money without explicit written approval.** Do NOT build or wire
   any real-money order execution (`live/`) until the user says, in plain
   language, "go to real money." Good paper results do not imply approval.
3. **Freeze and spec before big builds.** Write or update the relevant
   `docs/*.md` BEFORE writing a large chunk of code. Do not invent scope.
   When a design choice is genuinely the user's, ask — don't guess.
4. **Settlement reality is sacred.** `KXBTC15M` settles on a ~60s average of
   the index at close. Only PERSISTENT moves change the outcome; reverting
   spikes do not. Any model/backtest/threshold MUST respect this. Edge (if
   any) lives mid-window and decays to ~0 at the bell.
5. **The BRTI proxy is a PROXY.** It diverges from true BRTI worst during fast
   moves — exactly where the signal supposedly lives. Treat proxy tracking
   error as a first-class source of FALSE edge, never as noise. Label every
   proxy/naive component as such in code and UI.
6. **Secrets never leave the machine.** `.env`, `*.pem`, keys are gitignored.
   Never print private keys, never commit secrets. Verify before every commit.

## Honest priors / known failure modes (build defenses, don't paper over)

- Kalshi↔BRTI is likely already arbed tight; only spot↔BRTI lag is a candidate
  and it is crowded by latency-advantaged players.
- Vol-estimation error fabricates fake mispricings — vol is a tunable,
  stress-tested component.
- Costs: fills at ask-when-buying / bid-when-selling, never mid, plus fees.
  Model BOTH zero-fee (promo) AND a realistic fee schedule.
- Latency: if the gap closes in 300ms and round-trip is slower, instant-fill
  backtests lie. Model decision→fill latency + slippage.
- Overfitting: thresholds/TP/SL are trivially curve-fit. Walk-forward + a
  seeded random-entry control are mandatory before believing anything.

## How to run (current)

```bash
source .venv/bin/activate
python -m dashboard.server      # live dashboard at http://127.0.0.1:8000
python -m data.logger           # record the research dataset (sqlite/parquet)
python -m pytest -q             # tests
python config/settings.py       # sanity-check config + that .env is loaded
```

## Architecture (see docs/ARCHITECTURE.md for detail)

- `config/`   pydantic settings + `default.toml` — ALL costs/fees/latency/
  thresholds live here, never hardcoded downstream.
- `data/`     feeds + dataset: `brti_proxy.py` (consolidated index),
  `kalshi_client.py` (REST), `kalshi_ws.py` (live market data),
  `logger.py` (time-synced writer).
- `model/`    `vol.py` + `fair_value.py` — Phase 1, the REAL digital-option model.
- `backtest/` engine + execution + controls (random/walk-forward) + report — Phase 1.
- `paper/`    Phase 2 live sim, zero real money.
- `live/`     Phase 3 — GATED, stays empty until approved.
- `dashboard/` FastAPI server + lightweight-charts frontend (a viewing tool,
  not part of the научный backtest). `fair_naive.py` is a labeled PROXY only.

## Conventions

- Python 3.14, venv at `.venv`. Dependency-light; add to `requirements.txt`.
- Prices: Kalshi contract prices are **dollar floats in [0,1]** with deci-cent
  ticks — NEVER integer cents (that truncates the late-window 0/1 resolution).
- Time: store/align everything on **UTC milliseconds**.
- No polling on latency-critical paths — use websockets.
- Tests for the easy-to-get-wrong bits (book math, fair value, settlement).
- Commit messages end with the Co-Authored-By trailer. Commit/push only what
  the user asks; never commit secrets.

## Status & next step

See `docs/STATUS.md` — it is the live to-do and known-issues list. Update it as
work progresses. Right now there is an OPEN BUG (Kalshi live odds) documented
there; that is the next thing to fix.
