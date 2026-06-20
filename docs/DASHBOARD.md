# DASHBOARD — design spec (transparent, uncluttered)

## The problem with what we have

A bare "BUY YES" verdict is a black box — it tells the user nothing about WHY,
so it's worthless (and worse, it has been firing off stale/garbage data). The
dashboard's job is to make the signal's reasoning **legible at a glance**:
the user must see every input that produces the call, and how much to trust it.

## Principle

Show the **chain of reasoning**, in order, left to right / top to bottom. The
verdict is the LAST thing, derived visibly from the things above it. Nothing
renders a directional call unless the data feeding it is fresh and sane.

> Goal: the user looks at the screen and can say, in one breath, "BTC is $X
> above the strike, with N minutes left and vol V, so fair ≈ P%. Kalshi is
> trading Q%. The Q−P gap is G, and of that, D is just my proxy drifting, so
> the real edge is ~R." If R isn't clearly there, the screen says so.

## The reasoning pipeline (the inputs that MUST be visible)

1. **BTC vs strike** — the price, the strike, distance in **$** AND in **σ**
   (standard deviations to close). σ-distance is the real driver of the odds.
2. **Time left** — mm:ss, and the **time-decay context**: these are 15-min
   binaries; as time→0 the fair probability snaps toward 0% or 100% (theta).
   Show that the clock is sharpening the number, and that the last ~60s settle
   on an average (so late moves may not count).
3. **Volatility** — the short-horizon σ estimate feeding fair value (and a note
   that wrong vol = fake mispricing).
4. **Fair probability** — `P(settle ≥ strike)`, built from 1+2+3. Labeled
   PROXY until the Phase-1 model exists.
5. **Kalshi YES (live)** — the market's actual price, with its **real data age**
   ("updated X.Xs ago"), not socket ping.
6. **The difference, decomposed** — `Kalshi − Fair`, split into:
   - **proxy drift** = (our proxy − settlement reference) translated into ¢,
   - **residual** = the part NOT explained by proxy drift ← the only candidate edge.
7. **Data-quality gate** — fresh? uncrossed? enough size? If not → NO SIGNAL.

## Layout (uncluttered)

```
┌────────────────────────────────────────────────────────────────────────┐
│ KXBTC15M-26JUN201830-30      CLOSES IN  4:12      ● live · Kalshi 0.3s ago│
├────────────────────────────────────────────────────────────────────────┤
│  REASONING                                                               │
│  BTC 64,043   →  vs STRIKE 64,068   →  −$25 (−0.6σ)  →  4:12 left, vol V  │
│        →  FAIR 31% [proxy]   vs   KALSHI 29%   →   DIFF −2¢                │
│        of which:  proxy drift −5¢ · residual +3¢                          │
├──────────────────────────────────┬─────────────────────────────────────┤
│  VERDICT (only if data is good)   │  data: fresh ✓  book ok ✓  size ✓     │
│  WAIT — no clear edge             │  (else: NO SIGNAL — data unreliable)  │
├──────────────────────────────────┴─────────────────────────────────────┤
│  Bitcoin — BRTI proxy (USD)         │  Kalshi odds — YES vs Fair (%)      │
│   ───── price, ----- strike         │   ───── Kalshi YES, ----- Fair      │
│   [ line chart ]                    │   [ line chart; the GAP = signal ]  │
└─────────────────────────────────────┴─────────────────────────────────────┘
```

- **Two charts, separated, side by side.** Left: BTC ($) with the strike line —
  see price approach/cross the line. Right: **Kalshi YES and Fair on the same
  %-axis** — the vertical distance between the two lines IS the signal, over
  time. That single visual is the whole thesis.
- The reasoning strip is text tiles, calm, monospace numbers, one row. No
  blinking, no 500 series. Color used sparingly: green/red only on the final
  DIFF/residual and the verdict.

## Hard data-quality gates (suppress, don't "caution")

Render **NO SIGNAL — data unreliable** (grey, no direction) if ANY of:
- Kalshi price data age > ~2s (real field age, not ping).
- Book crossed: `yes_bid ≥ yes_ask`.
- Top-of-book size below a minimum threshold (thin/untradeable).
- Fair value not available (vol still warming up).
- Under ~60s to close (settlement-average zone → unreliable).

A directional call may ONLY appear when all gates pass.

## Explicitly LEFT OUT (anti-clutter)

- Drawing tools, multiple timeframes, per-venue spaghetti — not now. Keep
  Coinbase/Kraken as faint context lines at most, or drop them.
- No more than the two charts + one reasoning strip + one verdict line.
- No metric shown without a unit and a plain-language label.

## Honesty rules in the UI

- Everything proxy/naive is badged "proxy."
- If the DIFF is fully explained by proxy drift, the verdict says so:
  "no real edge — this is proxy drift," not a trade call.
- Latency/age fields show the TRUTH about freshness; never imply live when stale.
