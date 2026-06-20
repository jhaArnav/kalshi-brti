# Council Brief — Kalshi BTC 15-min Fair-Value / Lag Project

**Purpose of this document:** a self-contained briefing for an external LLM/agent
council with no prior context. You are asked to judge whether the approach is
sound and whether the project should proceed (and how). Be skeptical. The
project's own ethos is truth-seeking: a rigorous "there is no edge" is a
success, not a failure. A "case against" is included; weigh it honestly.

---

## 1. One-paragraph summary

We are testing whether a measurable, tradeable mispricing exists on Kalshi's
15-minute Bitcoin up/down markets (`KXBTC15M`). The thesis: the market price of
these binary contracts lags the "true" fair value during fast Bitcoin moves,
because the settlement index (CF Benchmarks BRTI) is a smoothed, multi-exchange,
order-book-weighted index that trails raw spot. If we can estimate fair value
faster than the market reprices, the gap is a signal. We have built a Phase-0
data + dashboard layer, hit several real bugs, and — critically — primary-source
research has surfaced two facts that materially lower the probability of a cheap,
real edge. We froze coding to write a spec and are seeking a decision on whether
and how to proceed.

---

## 2. The product (accurate, from Kalshi's live API + help center)

`KXBTC15M` is a **binary (digital) option** on Bitcoin, one new contract every 15
minutes. You buy/sell YES or NO contracts in real time; prices are dollars in
[0,1] (a direct probability read) and pay $1 / $0 at expiry. Mechanics verified
from Kalshi's live API rules text and help.kalshi.com:

- **Settles on CF Benchmarks BRTI** (the CME CF Bitcoin Real-Time Index), per the
  event's `settlement_sources` field. NOT a Kalshi-proprietary capture, NOT the
  once-daily BRR/BRRNY. (Common blog claims to the contrary are wrong.)
- **The strike** (`floor_strike`, shown as "Target Price") is set to the index's
  **opening 60-second average** at the window's start — an oddly specific number,
  not a round level. Locked and readable for the whole window.
- **Settlement value** (`expiration_value`) is the **closing 60-second average**
  of BRTI. YES settles if `expiration_value ≥ floor_strike` (ties → YES).
  Populated only after the market closes.
- Net: YES ≈ "BTC's index closes higher than it opened." Tradeable as a plain
  strike option throughout the 15 minutes.
- Prices use sub-cent ("deci-cent", $0.001) ticks near 0/1; standard 1¢ ticks in
  the middle.
- **Fees** (standard schedule): `roundup(0.07 × contracts × P × (1−P))`, peaking
  ≈ **1.75¢/contract at P=0.50**, →0 near the tails. There is **no zero-fee
  promo** on these (the no-fee promo is for BTCPERP, a different product). The
  exact crypto fee multiplier (0.07) is unconfirmed vs a gated official PDF.

---

## 3. The hypothesis (falsifiable)

```
signal = kalshi_price − model_fair_value
fair   = P(index closes ≥ strike) ≈ Φ( ln(BRTI_now / floor_strike) / (σ·√τ_eff) )
```

Claim: `signal` predicts the contract's next move, strongest in high-vol windows
where BRTI lags spot. Edge (if any) lives **mid-window on persistent moves** and
decays to ~0 at the bell (because settlement is a 60s average — reverting spikes
don't count).

**Default prior (stated up front): probably NO durable edge after costs.** The
Kalshi↔BRTI leg is likely already arbitraged tight; only the spot→BRTI lag is a
candidate, and it is crowded by latency-advantaged players.

---

## 4. What has been built (Phase 0)

- **Config layer** (pydantic + TOML): all costs/fees/latency/thresholds.
- **BRTI proxy**: streams best bid/ask from 4 spot venues (Coinbase, Kraken,
  Bitstamp, Gemini) over websockets → a volume-weighted consolidated mid, ~1/s.
- **Kalshi REST client**: RSA-PSS auth (verified working — read live balance),
  markets/orderbook/candlesticks; prices handled as dollar floats.
- **Kalshi WS client**: live market data. (Has a bug — see §6.)
- **Time-synced logger**: writes BRTI/spot/Kalshi to SQLite/Parquet on aligned
  UTC-ms timestamps; run manifest for reproducibility.
- **Dashboard**: FastAPI backend pushing merged state to a lightweight-charts
  browser UI at ~15 Hz. (Currently emits false signals — see §6.)
- **Tests**: order-book math + fair-value proxy (10 passing).
- Real account balance is ~$21. Real-money trading is GATED and not built;
  it requires explicit written approval and is out of scope until then.

Phases are hard-gated: 0 data infra → 1 backtest → 2 paper → 3 real money. We
are in Phase 0 and have NOT passed its gate (gate = trustworthy live feeds +
clean dataset).

---

## 5. Verified technical facts (primary sources)

**Kalshi API:** REST `api.elections.kalshi.com/trade-api/v2`; RSA-PSS(SHA256)
auth over `ts+METHOD+path`. WS `ticker` channel pushes authoritative live
`price_dollars`/`yes_bid_dollars`/`yes_ask_dollars` on every change (`ticker_v2`
does not exist). **No live underlying/reference price is exposed via API** —
only `floor_strike` (live) and `expiration_value` (post-settlement).

**CF Benchmarks BRTI methodology (v16.7, Jun 2026):** BRTI is **order-book
depth-integrated and exponentially weighted**, computed every 200ms across **8
constituents** (Bitstamp, Coinbase, itBit, Kraken, Gemini, LMAX Digital,
Bullish, Crypto.com), with outlier exclusion (drop venues >5% off the
cross-exchange median; drop stale/crossed books) and a winsorized order-size
cap; utilized depth out to a 0.5% mid-spread band. Weights are **dynamic**
(liquidity-determined), not fixed. The real-time value is **license-gated**
(viewable on cfbenchmarks.com, but programmatic use/redistribution needs a paid
license). No open-source replication exists.

**Spot→BRTI lag:** **no official figure exists.** The "~8–14s" in the original
project brief traces to a single uncited third-party source that also mislabels
BRTI as a TWAP. Must be measured, not assumed.

---

## 6. What went wrong (bugs found)

1. **Stale/frozen Kalshi feed.** The dashboard reconstructed the order book from
   the `orderbook_delta` channel; this drifts/desyncs (our top-of-book read
   0.18/0.20 while Kalshi's REST said 0.28/0.29 in the same instant; over time it
   froze into a crossed 0.35/0.15 garbage state). A frozen Kalshi price made the
   "discrepancy" pure artifact.
2. **Signals rendered on bad data.** A "BUY YES" printed while the book was
   crossed AND the price stale. The latency badge measured socket ping, not data
   freshness — i.e. it lied about staleness.
3. **Fair value anchored to the wrong reference.** Three different BTC numbers
   were in play (our proxy ~64,043 / true BRTI ~64,037 / Kalshi's own ~64,023).
   Pricing fair off our proxy baked $15–20 of proxy tracking error into the
   "discrepancy" and misread it as edge — the classic false-edge trap.
4. **Time decay / 60s averaging** not fully modeled.
5. **UX:** the dashboard was a cluttered black box — a bare "BUY/SELL" verdict
   with no visibility into the reasoning, which is useless (and dangerous on bad
   data).

---

## 7. The honest reality / central risks

- **Our proxy is a loose approximation of BRTI.** A 4-venue volume-weighted
  top-of-book mid replicates none of BRTI's depth integration, exponential
  weighting, 8-venue set, or outlier screens. The error is largest in thin/fast
  markets — precisely the regime the signal supposedly lives in. This is a
  severe confound.
- **The lag thesis is unverified** and is the load-bearing assumption.
- **Costs are first-order.** ~1.75¢/contract near 50¢ is a large fraction of any
  plausible edge on a 15-minute contract.
- **We cannot cheaply get the real BRTI** in real time (license-gated). So we
  can't directly compute the true fair value live; we're stuck with a proxy.

**The one genuinely promising asset:** every window yields **two exact, free
BRTI readings** — `floor_strike` (opening 60s-avg) and `expiration_value`
(closing 60s-avg). These let us:
- measure our proxy's tracking error empirically (twice per 15 min, no license),
- de-bias the proxy (anchor `BRTI_now ≈ floor_strike + (proxy_now − proxy_at_open)`,
  so only the proxy's *change since open* matters and absolute level error
  cancels),
- study the spot→BRTI lead-lag against ground truth.

This converts "we're guessing" into "we can measure whether the proxy and the
lag are good enough" — cheaply.

---

## 8. The proposed model (corrected)

- `fair = Φ( ln(BRTI_now / floor_strike) / (σ·√τ_eff) )`, a digital-option form.
- `BRTI_now` from the de-biased proxy (change-since-open anchored to floor_strike).
- `σ` = short-horizon vol (EWMA realized to start; tunable, stress-tested).
- `τ_eff` = effective horizon accounting for the closing 60s average (≈ τ − 30s,
  to be calibrated); suppress under ~60s to close (averaging zone).
- Display the discrepancy **decomposed** into a "proxy-drift" component vs a
  "real residual" — so drift cannot masquerade as edge.

---

## 9. The proposed plan (code currently frozen)

1. Drive Kalshi price from the authoritative `ticker` channel; show real per-feed
   data-age.
2. Hard-gate signals: render "NO SIGNAL — data unreliable" when stale (>~2s),
   crossed (bid≥ask), thin, last-60s, or vol not warmed up. No call on bad data.
3. Anchor fair to `floor_strike`, de-bias the proxy, show the drift/residual
   decomposition.
4. Rebuild the dashboard uncluttered: a one-line reasoning strip (BTC vs strike →
   time left → vol → fair % → Kalshi % → difference) and **two separated charts**
   (BTC vs strike; Kalshi-YES vs Fair, where the gap between the lines IS the
   signal).
5. Build the proxy-error / lag measurement harness using the free BRTI anchors.

**Acceptance test that matters:** after fixes, re-run the same window with a live
feed and aligned references — *does a discrepancy still appear?* If it largely
vanishes, the earlier "edge" was frozen feed + proxy drift, and that is itself
the finding.

---

## 10. Case FOR proceeding

- The fixes are targeted, not a rewrite; the fair-value formula shape is correct.
- The free BRTI anchors give a real, cheap way to validate the proxy and the lag
  — turning unknowns into measurements.
- Even a clean "no edge" is a valuable, cheap result and de-risks any future
  capital.
- Cost is low (data + compute); no real money at risk in Phases 0–2.

## 11. Case AGAINST proceeding (steelman)

- The load-bearing assumption (a tradeable spot→BRTI lag) is unverified and may
  not exist at a horizon/size we can exploit.
- Even if a lag exists, our proxy is too loose to measure it reliably in fast
  moves, and improving it toward true BRTI is a large effort with diminishing
  returns (and the real index is license-gated anyway).
- Fees (~1.75¢ near 50¢) plus bid/ask + latency likely exceed any residual edge;
  latency-advantaged players are already there.
- The whole thing may be a sophisticated way to rediscover market efficiency.

## 12. The decision requested of the council

1. Is the corrected understanding of the product and settlement sound?
2. Is the free-anchor validation (floor_strike/expiration_value as ground truth)
   a legitimate substitute for licensed BRTI, sufficient to test the hypothesis?
3. Should the project proceed now — and if so, start with the dashboard fixes
   (steps 1–4, a usable honest screen) or with the measurement harness (step 5,
   which most directly tests viability)?
4. Is there a fatal flaw that argues for stopping at Phase 0 and declaring the
   likely-no-edge result?
