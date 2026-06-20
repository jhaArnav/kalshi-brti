# MODEL — fair value for KXBTC15M (corrected from primary sources)

Grounded in Kalshi's live API rules text + help center (2026-06-20). This
supersedes the kickoff's assumptions where they conflict.

## What the contract actually is

**A binary (digital) option with a strike.** Kalshi shows a target price; you
buy YES (settles $1 if the index closes ≥ strike) or NO (settles $1 if below),
and you can buy/sell those YES/NO contracts in real time as their prices move
like option premiums. The trader's model is simply: **will the index be above or
below the strike at close?**

Two mechanical details sit underneath the strike (they refine the fair-value
math; they do NOT change how it trades):

1. **The strike is set to the index price at the window's open.** That's why
   it's an oddly specific number (record showed `floor_strike = 64235.29`, shown
   as "Target Price: $64,235.29"), not a round $64,000. Practically: YES ≈ "the
   index closes higher than it opened." `floor_strike` is locked at open and
   readable all window.
2. **At settlement the compared value is a 60-second average**, not the final
   tick. `expiration_value` = closing 60s-average of the index (empty until
   settled). `strike_type = greater_or_equal` → YES iff
   `expiration_value ≥ floor_strike` (ties → YES). Matters mainly in the last minute.

Settlement index: **CF Benchmarks BRTI** (per-second real-time index), per the
event's `settlement_sources` field — not BRRNY, not a Kalshi capture.

So: **fair = P(index closes ≥ strike) = P(closing 60s-avg BRTI ≥ floor_strike)**.

## Fair-value formula

Driftless Gaussian on log-returns of BRTI over the remaining window τ:

```
fair = Φ( ln(BRTI_now / floor_strike) / (σ · √τ_eff) )
```

- `floor_strike` — exact, from the market record (the locked opening avg).
- `BRTI_now` — our best live estimate of the current BRTI (we don't get the
  real one live; see proxy de-biasing).
- `σ` — per-√second BRTI vol (EWMA to start; Phase-1 component, stress-tested).
- `τ_eff` — **effective** time to close, accounting for the 60s closing average.
  The settled value is an average of the last 60 one-second BRTI ticks, which
  dampens late moves. Approximate the averaging as reducing the effective
  horizon (a common approximation: `τ_eff ≈ τ − 30s`, i.e. center-of-averaging;
  to be calibrated). Under ~60s left the contract is effectively in its
  averaging zone → treat as unreliable / suppress (see DASHBOARD gates).

The formula SHAPE matches what we had; the fixes are the anchor (`floor_strike`
= opening avg, not a derived strike), the proxy de-biasing, and `τ_eff`.

## Proxy de-biasing (free, uses the exact BRTI anchor)

We can't read live BRTI, but `floor_strike` IS an exact BRTI reading at open.
Our proxy has some slowly-varying bias `b` (proxy = BRTI + b). Estimate it at
window open and only trust the proxy's **change**:

```
b_est        = proxy_at_open − floor_strike          # exact at open
BRTI_now_est = proxy_now − b_est
             = floor_strike + (proxy_now − proxy_at_open)
```

Then fair depends only on the proxy's **move since open**, canceling the
proxy's absolute level error:

```
fair ≈ Φ( (proxy_now − proxy_at_open) / (floor_strike · σ · √τ_eff) )
```

This directly attacks bug #3: absolute proxy drift no longer reads as "Kalshi
cheap/rich." Residual risk = the proxy's tracking error in the *change* during
fast moves (still real — quantify with the BRTI methodology research).

## Show the decomposition (anti-black-box)

The displayed discrepancy `Kalshi_YES − fair` must be split so the user sees
what's real:
- **proxy-drift component** — how much the gap moves if we use raw proxy level
  vs the de-biased anchor. If this explains the gap, there's no edge.
- **residual component** — the part left after de-biasing ← the only candidate.

## Costs (corrected)

- Fee: `roundup(0.07 · C · P · (1−P))`, peak ≈ 1.75¢/contract at P=0.50,
  → toward 0 near the tails. Maker ≈ 25% of taker (often $0 on small orders).
  Crypto multiplier assumed 0.07 — UNCONFIRMED vs the gated official PDF; verify.
- **No zero-fee promo on KXBTC15M** (the no-fee promo is BTCPERP, a different
  product). Model standard fees as the base case; still also report zero-fee for
  reference, but do NOT assume zero-fee is the live regime.
- Fills at the touch (buy@ask / sell@bid), deci-cent ticks near the tails.

## How good is our proxy, really? (sobering)

Primary CF Benchmarks methodology (v16.7, Jun 2026): **BRTI is NOT a
volume-weighted BBO mid.** It is computed from full order books across **8
constituents** (Bitstamp, Coinbase, itBit, Kraken, Gemini, LMAX Digital,
Bullish, Crypto.com), every 200ms, by:
- consolidating L2 depth (with a winsorized order-size cap),
- computing utilized depth out to a 0.5% mid-spread threshold,
- **exponentially weighting** the mid price-volume curve (near-touch dominates),
- excluding venues >5% off the cross-exchange median, dropping stale/crossed books.

Our proxy (VW mid of 4 venues' top-of-book) replicates NONE of: depth
integration, exponential depth-utility weighting, the 8-venue set, or the
outlier screens. So it is a **loose** approximation, and the error is largest in
thin/fast markets — precisely the regime the signal supposedly lives in. Treat
proxy tracking error as a SEVERE confound, not a footnote.

The floor_strike de-biasing (above) cancels the proxy's *absolute level* error
but NOT its error in the *change* during fast moves. That residual change-error
is the real danger and must be measured.

## Measuring proxy quality for FREE (the key validation)

Every window hands us TWO exact BRTI readings:
- `floor_strike` = opening 60s-avg BRTI,
- `expiration_value` = closing 60s-avg BRTI (post-settlement).

So we can, per window, compare our proxy's own opening-60s-avg and closing-60s-avg
to these exact values and accumulate the **distribution of proxy tracking error**
— continuously, license-free. This:
- quantifies how trustworthy the proxy is (and whether it's good enough at all),
- calibrates `b_est` and its drift,
- is itself a Phase-0 deliverable BEFORE believing any signal.

For finer-grained validation, the free cfbenchmarks.com BRTI web display can be
sampled for research only (NOT productized — licensing).

## Proxy improvements (priority, when we unfreeze)
1. Match the 8-constituent set where free public WS exists (verify which of
   itBit/LMAX/Bullish/Crypto.com are accessible; document gaps).
2. Use L2 depth, not just BBO; build consolidated PV curves.
3. Approximate the exponential depth-utility weighting + 0.5% utilized-depth cap.
4. Add the >5%-median outlier screen + stale-book dropping.
5. Sync venues onto a common 1s grid.
Each step reduces but never eliminates the gap. Measure error after each.

## The lag thesis is UNVERIFIED
No official spot→BRTI lag figure exists. The kickoff's "~8-14s" is uncited
third-party and conflates BRTI with TWAP averaging. The lead-lag must be
MEASURED (cross-correlate our spot/proxy against BRTI readings) before any
signal is trusted. Do not hard-code a lag.

## Implications for the hypothesis

The edge, if any, is about predicting BRTI's **drift over the rest of the
window** better than the Kalshi market does — where the candidate inefficiency
is the spot→BRTI lag (BRTI is a smoothed multi-exchange index; spot leads it).
Because both endpoints are 60s averages and only persistent drift counts, this
remains a mid-window, persistent-move story that decays to the bell. Fees at
~1.75¢ near 50¢ are a large fraction of any plausible edge — costs are
first-order here, not a footnote.
