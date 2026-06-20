# STRATEGIES — candidate edges, evidence, verdicts

Skeptical log of strategies that might win on Kalshi 15-min BTC (`KXBTC15M`) or
similar short-window markets. Default prior: **no edge after costs**
(~1.75¢/contract at 50¢ + spread + latency). Each entry gets a verdict:
**PLAUSIBLE / LIKELY-NO-EDGE / NEEDS-DATA / DEAD**. Append-only.

Cost reminder: a tradable edge must clear ≈4–5¢ round-trip (fee + half-spread +
adverse selection + margin). A 1–2¢ raw gap is noise (see docs/COUNCIL_VERDICT.md).

---

## S1 — "Tie-goes-to-YES" structural edge — **DEAD**
Idea: YES settles on `closing 60s-avg ≥ opening 60s-avg`; ties resolve YES, a
free sliver of YES bias.
Data (2,996 historical settled windows, pulled live via REST): exact-ish ties
(`|close_avg − open_avg| < $0.01`) = **1 / 2996 = 0.03%**. The two 60-second
BRTI averages essentially never coincide (BTC moves ~$159 std over 15 min).
**Verdict: DEAD.** The tie rule is irrelevant in practice; no exploitable bias.

## S2 — Directional base-rate bias (always-buy-YES or always-buy-NO) — **LIKELY-NO-EDGE**
Idea: if YES resolves ≠ 50%, blindly buy the favored side.
Data (same 2,996 windows): **YES rate 48.5% / NO 51.5%**; mean open→close move
**−$4.43 (−0.6 bps)**, median −$3.67. The slight NO lean is just a small net
DOWN-drift over the sample period (BTC direction), not a structural property —
it would flip in an up-drifting sample. And the market prices the base rate, so
"always buy NO" just pays spread+fees to bet on BTC drift.
**Verdict: LIKELY-NO-EDGE.** Base rate ≈ coin flip; any deviation is sample
drift the market already reflects. (Re-check on the live sample as a control.)

## Calibration byproduct (useful, not a strategy)
Open→close BRTI move over 15 min: **std ≈ $159** (≈24 bps), mean ≈ 0. This is
the vol scale for the fair-value model (σ·√τ). Confirms these are high-variance
windows — direction is rarely a true 50/50 near expiry, but the move is
symmetric, so no free directional money.

---

## S3 — Documented retail bots / public approaches — **LIKELY-NO-EDGE**
papabrosio/kalshi-btc-15min-trader (explicitly educational, no after-fee P&L;
its "99% last-minute accuracy" is the regime MMs own), reedjacobp/kalshi-trading-bot
(paper only, no results), "13 AI agents" dev.to post (+$177, unaudited, no fees,
not KXBTC15M), Turbine FI "Sharpe 9.46" (vendor marketing; concedes "simulated
only," excludes fees/slippage). No verified after-fees retail profit anywhere.
**Verdict: LIKELY-NO-EDGE.**

## S4 — Maker rebates / spread capture — **LIKELY-NO-EDGE**
No maker rebates exist on Kalshi; best case is zero maker fee, but the crypto
"rapid" series likely sit in the ~0.44¢ maker-fee bucket (couldn't load the fee
PDF to confirm). Structural adverse selection: a resting bid fills exactly when
BTC ticks against it, picked off by faster players with licensed BRTI + colo.
**Verdict: LIKELY-NO-EDGE.**

## S5 — Favorite-longshot / tail mispricing — **LIKELY-NO-EDGE (buy side) / NEEDS-DATA (sell side)**
Bürgi-Deng-Whelan 2026 (karlwhelan.com/Papers/Kalshi.pdf, 46k contracts): real
favorite-longshot bias — <10¢ contracts lose >60% of stake, avg return ≈ −20%,
worse for takers — BUT on ≥24h events, not 15-min crypto. Buying cheap longshots
is firmly −EV (fee ≈7% of a 2¢ stake + huge overpricing). Selling longshots /
buying 88–95¢ favorites shows ~1–2%/trade in SLOW markets, decaying, with 20:1
tail risk; in 15-min BTC the deep-OOM leg only appears after a clear move (max
adverse selection). **Verdict: buy-longshot LIKELY-NO-EDGE; favorite/sell-side NEEDS-DATA.**

## S6 — Cross-market arbitrage — **LIKELY-NO-EDGE / basis NEEDS-DATA**
Polymarket BTC up/down settles on Chainlink point-in-time (NOT a 60s average) →
"YES@Kalshi + DOWN@Poly < $1" is a basis bet with real variance, not riskless
arb. Robinhood event contracts run on Kalshi's backend (same book/fee → no arb).
Deribit's shortest expiry is daily (no 15-min options) → static hedge impossible;
dynamic digital replication costs many cents (gamma explodes ATM near expiry) >>
the 1.25–1.75¢ gap. **Verdict: no arb; Kalshi↔Polymarket basis = NEEDS-DATA (relative value).**

## S7 — Predictive signals (funding / OFI / CVD / skew / momentum) — **LIKELY-NO-EDGE**
Order-flow imbalance & CVD decay in ~1s (HFT regime, can't compete); funding is
an 8h–daily signal (flat within 15 min); options skew has no intraday
predictiveness; BTC momentum/mean-reversion R² ≈ 0.16%, "insufficient to extract
profits" (arXiv 2003.13517). None clear the ~2.7bps / 53.5% hurdle.
**Verdict: LIKELY-NO-EDGE.**

## S8 — Behavioral / time-of-day — **LIKELY-NO-EDGE / fade-spike NEEDS-DATA**
Round numbers cluster price but don't predict returns; session effects are a
VOL axis (push toward 50/50), not direction; best hour ≈52.7% < 53.5%; weekend
effects not significant net of frictions. Intraday reversal (~3bps of a 0.3%
move) sits AT the hurdle before decay. Best use is defensive (avoid paying spread
in the 13–17 UTC high-vol window). **Verdict: LIKELY-NO-EDGE; conditional fade-the-spike = NEEDS-DATA.**

## Liquidity reality (who's on the other side)
Kalshi runs a formal DMM/Liquidity-Provider program; crypto series typically
have 1–2 designated providers (Galaxy Digital reported) with reduced/zero fees,
higher limits, incentive payments, licensed real-time BRTI, and colocation.
**There is no carved-out lane for a latency-disadvantaged retail Python trader —
retail is the natural taker feeding ~1.75¢ each way to the MMs.**

---

## Shortlist of "least-dead" ideas testable on FREE settlement labels
1. **YES base-rate asymmetry — ANSWERED, NO.** Predicted ~50.2–50.7%; our 2,996-
   window sample shows 48.5% (no positive tilt) and ties 0.03%. Fails the
   ~51.75% break-even. (See S1/S2.)
2. **Conditional fade-the-spike reversal — FIRST NON-DEAD CANDIDATE (still likely no-edge).**
   Tested on 2,995 consecutive historical windows (settlement labels only):
   - Unconditional reversal: **51.4%** — below the 53.5% hurdle (dead).
   - After a BIG prior move (top 25%, |ret|≥20.9bps): reversal **55.1%** (n=749)
     — above the gross hurdle.
   BUT: 55.1% ± 3.6% → **95% CI [51.5%, 58.7%]; lower bound is BELOW 53.5%** (not
   significant). In-sample, single regime. And it assumes entry near 50¢ — we have
   NOT checked whether the market already prices the reversal into the next
   window's OPEN bid/ask. If it does (MMs surely know about mean-reversion), the
   edge vanishes at executable prices.
   **Status: the one candidate warranting live testing.** The collector now
   captures each window's open bid/ask, so over the 48h run we can test the real
   question: after a big prior move, does buying the reversal side AT THE OPEN
   PRICE clear costs, out-of-sample? Default expectation: the market prices it in.
3. **Kalshi↔Polymarket settlement-reference basis — NEEDS-DATA.** Needs a second
   venue feed; relative-value, not arb. Lower priority.

## Running takeaway
Tie rule and base rate are dead on free data; the 7 broader candidates are all
LIKELY-NO-EDGE after costs. The single exception: **fade-the-spike** shows a
suggestive 55.1% reversal after big prior moves (in-sample, CI lower bound still
below the hurdle, real entry prices untested). It is the ONE thing worth testing
live. Everything else matches the prior — no durable retail edge. Open tests:
(a) live kill-test (proxy lag → executable edge), (b) fade-the-spike at real
open prices, out-of-sample.

## Verification gaps (web tools rate-limited this pass)
Could not load Kalshi's fee PDF (KXBTC15M maker-fee bucket unconfirmed) or the
rulebook page (verbatim ≥/floor-strike wording); no first-hand P&L thread or
measured Kalshi–Polymarket BTC spread series surfaced.
