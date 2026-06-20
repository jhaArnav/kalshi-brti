# PROJECT — hypothesis, phases, gates

## The hypothesis (falsifiable) — REWRITTEN per council

Original (now rejected as weak): "BRTI is a smoothed TWAP that lags spot, so
Kalshi lags fair." BRTI is a real-time order-book aggregation (200ms,
depth-weighted, outlier-screened), NOT a TWAP — so this premise does not hold.

Current (weaker, honest, testable):

> "Kalshi's binary price may lag the best available estimate of the official
> opening-to-closing BRTI-average outcome, especially during fast moves, because
> traders may underreact to settlement-index dynamics, final-minute averaging,
> and short-horizon volatility."

This admits the edge may be zero. Prior: **very likely no cheap tradeable edge
unless measurement proves otherwise.** We are running a KILL-TEST, not building a
bot. See docs/COUNCIL_VERDICT.md.

## The math

> The gap between a model fair value of the 15m binary and the live Kalshi
> contract price predicts the contract's subsequent move — most strongly in
> high-volatility windows where the settlement index (BRTI) lags spot.

Tradeable quantity:
```
signal = kalshi_price − model_fair_value
fair   = P(index closes ≥ strike)                           # see docs/MODEL.md
       ≈ Φ( ln(BRTI_now / floor_strike) / (σ · √τ_eff) )
```

**Mechanic (primary source):** KXBTC15M is a **binary option with a strike** you
trade in real time (buy/sell YES or NO). Settles on **CF Benchmarks BRTI**. Two
details under the hood: the strike (`floor_strike`) is set to the index price at
the window's open (so YES ≈ "closes higher than it opened"), and the settlement
value is the **closing 60s-average** of BRTI vs that strike. Full detail in
docs/MODEL.md.

We test whether `signal` predicts the contract's move over the next N seconds,
**after realistic costs and latency**, and whether it beats a random-entry
control out-of-sample.

## The settlement catch (non-negotiable)

`KXBTC15M` settles on a ~60-second average of the index at the close. So:
- Only **persistent** moves into the close change the outcome.
- A spike that reverts does NOT move the averaged settlement.
- Therefore edge (if any) lives **mid-window on persistent moves** and decays
  toward zero approaching the bell. Late-window entries are unreliable.

The backtest, model, and any live signal MUST respect this. It is also why we
suppress signals in the final seconds (see STATUS bug #2).

## Why 15m markets only

`KXBTC15M` settles on **CF Benchmarks BRTI**, but the inputs are recorded on the
market object: `floor_strike` (locked opening 60s-BRTI avg) and
`expiration_value` (closing 60s-BRTI avg, filled post-settlement). Both are
readable for free after a market resolves, so we can label every historical
window's outcome without a data license. That makes these the only Kalshi BTC
market we can backtest rigorously for free. (Mid-window we do NOT get the live
BRTI from Kalshi — see docs/MODEL.md proxy de-biasing.) Hourly/daily fixed-strike
BTC markets are a different product and OUT OF SCOPE.

## Phased plan with HARD GATES

Advancing a gate is the USER's call, in plain language. Default to NOT advancing.

### Phase 0 — Data infrastructure  (current)
- Kalshi client (REST + live WS), BRTI proxy, spot leader feeds.
- Time-synced logger → research dataset (UTC-ms aligned).
- Backfill of available 15m history.
- A dashboard for watching live (a tool, not part of the backtest).
- **Gate:** dataset is clean, aligned, and the live feeds are TRUSTWORTHY
  (no stale/garbage prices). The current dashboard bugs block this gate.

### Phase 1 — Backtest  (GATE before Phase 2)
- Real digital-option fair value + vol estimator (EWMA to start).
- Signal, entry/exit logic, and the FULL rigor suite:
  - seeded **random-entry control** (signal must beat it OOS),
  - **walk-forward** out-of-sample (fit on train window, validate on held-out),
  - realistic execution: bid/ask fills + fees (zero AND realistic) + latency + slippage,
  - report EV/trade with confidence intervals, full P/L distribution, max drawdown,
  - flag negative-skew variants (win small often / lose big rarely).
- **Pass criterion (user decides):** signal beats random control OOS with
  positive EV after realistic costs AND realistic fees, with sane drawdown.
  Works only at zero fees or only in-sample → FAIL, reported plainly.

### Phase 2 — Paper trading  (GATE before Phase 3)
- Live, simulated fills only, ZERO real money. Run forward for weeks.
- Watch backtest→live decay. **Pass:** paper materially tracks the backtest.

### Phase 3 — Real money  (ONLY on explicit written go-ahead)
- Do not build live-order execution until the user approves in plain language.
- Then: hard-coded risk limits — tiny per-trade size, bankroll cap, daily loss
  limit that halts trading, kill switch. Start at the smallest possible size.

## Win condition

Knowing the TRUTH about whether this edge exists, cheaply and rigorously. A
rigorous "no edge" is a win. Beating the random control OOS after costs is the
only thing that counts as "edge."
