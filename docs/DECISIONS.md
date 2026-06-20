# DECISIONS — running log

Append-only-ish log of decisions and WHY, so we don't relitigate or forget.

## D1 — Test on KXBTC15M only
15m markets settle on Kalshi's own recorded reference (`floor_strike` /
`expiration_value`), readable free post-settlement → the only Kalshi BTC market
we can backtest rigorously without a data license. Hourly (BRRNY, gated) is out.

## D2 — Spot leaders: Coinbase + Kraken, both logged
Log both; pick the empirical leader from the lag analysis rather than assuming.

## D3 — Vol model: EWMA realized (RiskMetrics λ=0.94) to start
Simple, few params, easy to stress-test. The real digital-option fair value is
Phase 1; GARCH only if EWMA proves inadequate.

## D4 — Kalshi auth: RSA-PSS API key, secrets in gitignored .env + .pem
Verified working against the live API. Never printed, never committed.

## D5 — Prices stored as dollar floats, not integer cents
KXBTC15M uses `tapered_deci_cent` ticks (0.001 near the tails). Integer cents
would truncate exactly the late-window 0/1 resolution the strategy cares about.

## D6 — Dashboard stack: FastAPI + lightweight-charts, all websocket push
User priority is LATENCY (the gap closes in ~10-14s) and seeing it live. No
polling on the live path.

## D7 — Live Kalshi price source: `ticker` channel, NOT orderbook reconstruction
**Reversal of the initial approach.** Hand-maintaining the book from
`orderbook_delta` drifts/desyncs → frozen, crossed, wrong top-of-book (the
"frozen 25¢ / BUY YES" false signal). Kalshi's `ticker` channel pushes
authoritative `price_dollars` / `yes_bid_dollars` / `yes_ask_dollars` live on
every change. `ticker_v2` does not exist. See STATUS bug #1.

## D8 — Freeze + spec before big builds; transparency over verdicts
After the dashboard went off the rails, the user (correctly) demanded docs
first. Also: a bare yes/no verdict is useless — the UI must SHOW the inputs
(BTC vs strike, time decay, fair prob, Kalshi price, and the difference
decomposed into proxy-drift vs real mispricing) without clutter. See
docs/DASHBOARD.md.

## D9 — Anchor fair value to the settlement reference, and show proxy drift
Three different BTC numbers exist (our proxy, true BRTI, Kalshi's own
reference). The market settles on the BRTI reference. Pricing fair value off
our proxy bakes in $15-20 tracking error and misreads it as edge. ALWAYS
display the proxy-vs-reference gap so drift can't masquerade as signal. See
STATUS bug #3 and D11.

## D10 — Mechanic corrected (primary source): relative open-vs-close BRTI bet
KXBTC15M settles on **CF Benchmarks BRTI** (event `settlement_sources` field),
as **closing 60s-avg ≥ opening 60s-avg**, NOT "BTC above a fixed strike."
`floor_strike` = locked opening 60s-BRTI avg; `expiration_value` = closing avg.
This is a drift bet. Earlier "fixed strike / Kalshi-capture" framing was wrong.
See docs/MODEL.md. (Source: Kalshi live API rules text + help.kalshi.com.)

## D11 — De-bias the proxy with the floor_strike anchor
Kalshi exposes NO live mid-window reference, but `floor_strike` is an exact BRTI
reading at open. So estimate `BRTI_now ≈ floor_strike + (proxy_now − proxy_at_open)`
— fair then depends only on the proxy's CHANGE since open, canceling absolute
level bias. Residual risk = proxy tracking error in the change during fast moves.

## D12 — Model standard fees; NO zero-fee assumption
Research: the zero-fee promo is for BTCPERP (a different product), not
KXBTC15M. Base case = standard fee `roundup(0.07·C·P·(1−P))` (~1.75¢/contract at
50¢). Costs are first-order on these short markets, not a footnote.

## D13 — Our proxy is a LOOSE approximation of BRTI (accept + measure)
CF Benchmarks methodology: BRTI is order-book depth-integrated, exponentially
weighted, 8 constituents, 200ms, with outlier screens — none of which a 4-venue
VW-BBO-mid replicates. So proxy tracking error is a severe confound, worst in
fast moves. We will (a) improve the proxy incrementally, and (b) MEASURE its
error for free against `floor_strike`/`expiration_value` (two exact BRTI
readings per window) before trusting any signal. See docs/MODEL.md.

## D14 — The spot→BRTI lag is UNVERIFIED; measure, don't assume
No official lag figure exists; the kickoff's "~8-14s" is uncited third-party
that conflates BRTI with TWAP. The entire hypothesis rests on this lag, so it
must be measured empirically before believing any edge. If we can't measure it
cheaply, that limitation is itself a key finding.

## D15 — Council verdict adopted: harness-first KILL-TEST (unanimous 3/3)
Gemini + OpenAI 5.5 + Claude Opus all judged: understanding sound; free anchors
legit for falsification (not a live-BRTI substitute); **build the measurement
harness FIRST, demote the dashboard**; don't stop but expect "no edge."
Adopted (see docs/COUNCIL_VERDICT.md):
- Thesis rewritten to the weaker, honest form (BRTI is not a TWAP). Prior moves down.
- **Executable edge** framing (`fair−ask−fee` / `bid−fair−fee`), never `kalshi−fair`.
- **Log-return** anchoring (not additive); **`τ_eff ≈ τ−40s`**; **σ-sensitivity** check.
- Plan reorder: 1A minimal feed correctness → 1B headless anchor harness → 1C
  diagnostic dashboard. No real money. No signal UI until proven.
- Pre-committed go/no-go threshold locked BEFORE looking at data (D16).

## D16 — Pre-committed go/no-go threshold (lock before analysis)
A "tradable discrepancy" = executable edge (after real fees + half-spread) of
**≥ ~2¢ net (≈4–5¢ gross)**, **sustained ≥ 2–3s** (reachable at real latency),
occurring **≥ a handful of times/day**, **NOT** concentrated in stale/crossed/thin
windows, with **proxy anchor-error < the claimed edge** in those fast-move
windows, holding **out-of-sample** with **calibrated** probability buckets and
**beating the seeded random-entry control**. Anything weaker = NO EDGE → stop.
Marginal/in-sample-only never graduates to capital. Locked now to prevent
post-hoc rationalization.
