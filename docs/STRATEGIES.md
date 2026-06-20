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

## Pending (broader research sweep in flight)
A skeptical web-research pass is investigating: documented retail bots
(papabrosio et al.), maker rebates / spread capture, favorite-longshot tail
mispricing, cross-market arb (Kalshi vs Polymarket vs perps/options), predictive
signals (perp funding, order-flow, options skew), and time-of-day patterns.
Findings + verdicts will be appended here.

## Running takeaway
Two of the cheapest structural ideas (tie rule, base-rate bias) are already
ruled out on free historical data. Consistent with the prior: the obvious
structural edges aren't there. The live kill-test (proxy lag → executable edge)
remains the main open question.
