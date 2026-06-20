# COUNCIL VERDICT — synthesis & resulting decisions

Three external reviewers (Gemini, OpenAI 5.5, Claude Opus) judged docs/COUNCIL_BRIEF.md.
They were near-unanimous. This file records the verdict and what we change.

## Unanimous conclusions

1. **Product/settlement understanding is sound.** Settles on CF Benchmarks
   BRTI; YES iff closing 60s-avg ≥ opening 60s-avg (`floor_strike`). Cautions:
   triple-confirm `settlement_sources` + the tie rule directly from contract
   terms; the fee correction (no zero-fee promo; ~1.75¢/contract at 50¢) is the
   single most consequential line — costs are now first-order.

2. **Free-anchor validation is legitimate for FALSIFICATION, not a substitute
   for live BRTI.** `expiration_value` is an EXACT, free settlement label, so the
   strategy outcome ("did YES resolve when the signal said so, after costs?") is
   fully backtestable on free data. But two anchors/window ≠ continuous
   intra-window ground truth. The de-bias cancels LEVEL error, NOT the
   fast-move variance error — which is exactly the regime the signal lives in.
   Use the anchors to MEASURE that error, not to assume it away.

3. **Build the measurement harness FIRST. Demote the dashboard.** The polished
   UI tested nothing load-bearing and previously made bad data feel actionable.
   Do only minimal feed/data-quality fixes to collect honest data; build the
   harness that can KILL the idea; rebuild the dashboard later as a diagnostic
   "NO SIGNAL unless proven" display.

4. **Don't stop — but expect "no edge."** No fatal flaw in continuing (cheap +
   the free label makes it unusually definitive). Near-fatal flaw in the ORIGINAL
   STORY: BRTI is a real-time order-book aggregation, not a slow TWAP, so "BRTI
   lags raw spot" is weak. Rewrite the thesis. A clean, evidenced "no edge" is
   the most probable outcome and is a genuine success.

## Decisions adopted (see DECISIONS.md D15)

- **Thesis rewrite.** Not "BRTI lags raw spot." Instead: *"Kalshi's binary price
  may lag the best available estimate of the official opening-to-closing
  BRTI-average outcome, especially during fast moves, because traders may
  underreact to settlement-index dynamics, final-minute averaging, and
  short-horizon volatility."* Admits the edge may be zero.

- **Executable-edge framing (not `kalshi − fair`).**
  - buy:  `edge = fair − ask − fees`
  - sell: `edge = bid − fair − fees`
  A 1–2¢ raw discrepancy is noise until proven; the hurdle is ~4¢ round-trip.

- **Model fixes (MODEL.md):**
  1. **Log-return anchoring**, not additive: `ln(BRTI_now/floor) ≈ ln(proxy_now/proxy_openAvg)`.
  2. **`τ_eff ≈ τ − 40s`** (Brownian average over the final 60s contributes ≈⅓
     of the interval to variance). Inside the final 60s: partial-average model or suppress.
  3. **Vol sensitivity**: compute fair at σ low/base/high. If the edge vanishes
     under reasonable σ, it isn't an edge.

- **Plan reorder (STATUS.md):** Phase 1A minimal feed correctness → 1B anchor
  measurement harness → 1C diagnostic dashboard. Harness before UI.

- **Data hygiene:** persist raw ticker messages, REST snapshots, proxy
  constituents, timestamps, market metadata. Treat `orderbook_delta` reconstruction
  as a secondary diagnostic only, validated by periodic REST reconciliation.

## Harness test matrix (Phase 1B)

| Test | Question it answers |
|---|---|
| Opening anchor error | Can the proxy reproduce `floor_strike`? |
| Closing anchor error | Can the proxy reproduce `expiration_value` (settlement)? |
| Error vs volatility | Does proxy error explode exactly when the signal fires? |
| Lag sweep | Does shifting the proxy by N seconds improve anchor fit? |
| OOS calibration | Do predicted 60/70/80% buckets resolve at those rates? |
| Executable survivability | Does edge survive bid/ask + fees + realistic latency? |
| Random-entry control | Does the signal beat seeded random entries OOS? |

## Acceptance gate (HARSHENED — all required before any real capital)

1. Feed integrity: ticker data-age + bid/ask + REST reconciliation pass over
   many live windows.
2. Proxy tracking: opening/closing anchor error small enough that probability
   error < the trade threshold.
3. Fast-move test: proxy error does NOT explode in the regimes where the signal fires.
4. No-lookahead: signal uses only data available at decision time.
5. Executable edge: PnL netted at bid/ask + real fee formula + realistic fill/latency.
6. Calibration: predicted probability buckets resolve at ~their rates OOS.
7. Beats the seeded random-entry control by a statistically meaningful margin OOS.
8. Kill result accepted: if the discrepancy vanishes after fixes → declare
   "no edge found" and stop.

## Proposed go/no-go threshold (answer to the council's question back)

To justify moving past the harness toward (paper, then) real capital — user
sets final numbers:

- **Executable edge** (after bid/ask + real fees + modeled decision→fill latency)
  with a **95% CI lower bound > 0**, i.e. mean net edge clears the all-in cost
  stack `fee + half-spread + adverse-selection + margin` (~4–5¢ round-trip).
- **Frequency:** such opportunities occur often enough to matter (e.g. ≥ a few
  qualifying setups/day) — rare one-offs don't justify infrastructure.
- **Persistence:** the executable edge is sustained long enough to actually fill
  at our latency (e.g. ≥ ~1–2s), not a sub-second flicker.
- **Robustness:** survives walk-forward OOS, holds across σ low/base/high, and
  proxy fast-move error is materially smaller than the edge.
- Anything marginal or in-sample-only does NOT graduate; real money still starts
  at the smallest possible size with hard risk limits.
