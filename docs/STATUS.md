# STATUS — current state, known issues, next steps

_Update this as work progresses. This is the single source of "what now."_

## Where we are

Phase 0. Data layer largely built; live dashboard built but **emitting false
signals off bad data** (see bugs below). NOT past the Phase 0 gate — the gate
requires trustworthy live feeds. No Phase 1 model or backtest yet.

### Works (verified)
- Config layer; BRTI proxy (4 venues, consolidated index).
- Kalshi REST client + RSA-PSS auth (balance endpoint confirmed).
- Kalshi `ticker` WS channel confirmed to deliver live `price_dollars` /
  `yes_bid_dollars` / `yes_ask_dollars`.
- Time-synced logger (sqlite/parquet); backfill source confirmed.
- Unit tests for book math + fair-value proxy (10/10) — note the book-math
  approach itself is being replaced (bug #1).

### Broken / not trusted
- Live Kalshi price on the dashboard is **stale/frozen and crossed** → fake
  signals. This blocks everything.

## OPEN BUGS (priority order) — this is the next work, pending approval

### Bug #1 — Kalshi price feed is stale/frozen
Dashboard pinned Kalshi YES at ~25¢ while the real market was ~33¢. Root cause:
hand-reconstructing the book from `orderbook_delta` drifts/desyncs (confirmed:
our top-of-book = 0.18/0.20 vs Kalshi REST 0.28/0.29 in the same instant; over
a long run it freezes into a crossed 0.35/0.15 garbage state).
**Fix:** drive the live Kalshi price from the **`ticker` channel** (authoritative,
pushes on every change) instead of reconstructing the book. Keep `age` as a
REAL data-age ("Kalshi updated X.Xs ago" from the last ticker timestamp), NOT
socket ping. (`ticker_v2` does not exist.)

### Bug #2 — Signals render on bad data
A directional call printed while the book was crossed AND the price stale.
**Fix:** hard gates (see docs/DASHBOARD.md). If Kalshi data age > ~2s, OR book
crossed (`yes_bid ≥ yes_ask`), OR size below threshold, OR vol not ready, OR
< ~60s to close → render **"NO SIGNAL — data unreliable"**, grey, no direction.

### Bug #3 — Fair value anchored to the wrong reference  [research RESOLVED]
Three different BTC numbers (our proxy ~64,043 / true BRTI ~64,037 / Kalshi NOW
~64,023). RESEARCH RESULT: **Kalshi does NOT expose a live mid-window reference
price via API** — so we cannot anchor to it directly. BUT `floor_strike` IS an
exact BRTI reading at the open (the locked opening 60s-avg).
**Fix (see docs/MODEL.md):** (a) strike = `floor_strike` from the record,
(b) **de-bias the proxy** using the open anchor: `BRTI_now ≈ floor_strike +
(proxy_now − proxy_at_open)`, so fair depends only on the proxy's CHANGE since
open and the absolute level error cancels, (c) display the discrepancy
**decomposed** into proxy-drift vs residual so drift can't masquerade as edge.

### Bug #4 — Time decay + 60s averaging  [mechanic now confirmed]
RESEARCH RESULT: settlement is the **closing 60s-average** of BRTI vs the
opening 60s-average (a relative drift bet). Fair must (a) reflect theta — the
digital sharpens as τ→0 via the shrinking horizon (verify), and (b) model the
closing 60s average as an **effective horizon** `τ_eff` (≈ τ − 30s, calibrate).
Under ~60s left = averaging zone → suppress. Surface the clock + decay in the UI.

### Model correction (from primary-source research) — affects everything
KXBTC15M is a RELATIVE open-vs-close BRTI bet, NOT a fixed-dollar-strike bet,
and settles on **CF Benchmarks BRTI** (not a Kalshi capture / not BRRNY). There
is **no zero-fee promo** on these (that's BTCPERP); model standard fees
`roundup(0.07·C·P·(1−P))`. See docs/MODEL.md. PROJECT.md/ARCHITECTURE.md updated.

## Acceptance test (the test that matters)

After bugs #1-#3: re-run the SAME window with the Kalshi feed live and
references aligned, and check —

> **Does a "discrepancy" still appear?**
> If it largely vanishes, the earlier "edge" was frozen feed + proxy drift —
> and that finding IS the result. Report it plainly.

## Reality check (post-research) — read before believing any signal

Two findings reshape the odds of this working cheaply:

1. **Our BRTI proxy is structurally loose.** Real BRTI is order-book
   depth-integrated, exponentially weighted, 8 constituents, 200ms, with outlier
   screens. A 4-venue VW-BBO-mid is a loose cousin, worst exactly in fast moves
   where the signal supposedly lives. → proxy error is a severe confound.
2. **The lag thesis is unverified.** No published spot→BRTI lag exists; "~10s"
   is uncited. The whole hypothesis rests on it.

Constructive path: `floor_strike` (open 60s-avg BRTI) and `expiration_value`
(close 60s-avg BRTI) give TWO exact, free BRTI readings per window. Use them to
MEASURE proxy tracking error and to study the lag — before trusting a signal.
This measurement is now a Phase-0 prerequisite (added to work items).

## Research (complete)
- Kalshi settlement + API + fees: DONE → docs/MODEL.md, ARCHITECTURE.md.
- BRTI/BRRNY methodology + proxy error + index access: DONE → docs/MODEL.md.

## Added Phase-0 work items (from research)
- **Proxy-error measurement harness:** per window, compare proxy open/close 60s
  averages to `floor_strike`/`expiration_value`; accumulate the error
  distribution. Gate: is the proxy even good enough?
- **Proxy improvements** (L2 depth, 8 constituents where free WS exists,
  exponential depth-utility weighting, outlier screens, 1s sync) — incremental.
- **Lag measurement:** cross-correlate spot/proxy vs the exact BRTI anchors.

## CURRENT PLAN — council-approved harness-first kill-test (D15)

Dashboard is DEMOTED. Build the headless harness that can KILL the idea. Order:

### Phase 1A — minimal feed correctness (in progress)
- Drive Kalshi price from the **`ticker`** channel (canonical); real per-feed
  data-age (not socket ping). Keep `orderbook_delta` only as a secondary
  diagnostic, validated by periodic REST reconciliation.
- Gate: flag stale/crossed/thin/warming/last-60s as unusable.
- **Headless logger** persists the full field set per tick: raw ticker msgs,
  REST snapshots, `floor_strike`, `expiration_value` (post-settle), Kalshi
  bid/ask/last, all 4 proxy constituents + VW mid, proxy opening & closing 60s
  averages, vol estimate, and data-quality flags — UTC-ms aligned.

### Phase 1B — anchor measurement harness (the kill-test)
Run on logged data + free settlement labels. Tests (docs/COUNCIL_VERDICT.md):
opening/closing anchor error · error-vs-volatility · lag sweep · OOS
calibration · executable survivability (bid/ask + fees + latency 250ms/500ms/1s/2s)
· seeded random-entry control. Apply the locked go/no-go threshold (D16).

### Phase 1C — diagnostic dashboard (only if 1B is positive)
Rebuild as a "NO SIGNAL unless proven" truth display, not a trading console.

### Then (only on user go-ahead, only if the gate clears)
Paper trading → real money at smallest size with hard risk limits.

**Run plan:** stand up 1A, collect ~48h of clean data, build 1B in parallel,
then analyze against the locked threshold. Expected outcome per council: likely
a clean "no edge" — which is a win.
