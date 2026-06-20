"""
Phase 1B -- the kill-test harness.

Runs OFFLINE on a collector DB (data/collector.py). Uses the free, exact
settlement labels (`expiration_value`, `result`) to answer the only question
that gates the project:

    Does the proxy signal predict the settlement outcome well enough to beat
    bid/ask + fees + realistic latency, out of sample -- and is the proxy's
    fast-move tracking error smaller than the edge it claims?

Tests (docs/COUNCIL_VERDICT.md):
  1. opening/closing anchor error   -- proxy vs floor_strike / expiration_value
  2. anchor error vs volatility     -- does proxy fail when the signal fires?
  3. lag sweep                      -- does shifting the proxy improve anchor fit?
  4. OOS calibration                -- do predicted prob buckets resolve right?
  5. executable edge survivability  -- net PnL at ask/bid + fee + latency, to settlement
  6. random-entry control           -- signal must beat random by a real margin

Model (docs/MODEL.md): fair = Phi( ln(proxy_now/proxy_openAvg) / (sigma*sqrt(tau_eff)) ),
tau_eff = max(tau - 40, 0); sigma = EWMA of proxy log-returns.

Nothing here trades. It is designed to FALSIFY. A clean "no edge" is success.

Usage:
  python -m backtest.harness data_store/run_XXXX.db
  python -m backtest.harness data_store/run_XXXX.db --latency 0.5 --edge 0.02 --seed 42
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import statistics
import sys
from dataclasses import dataclass

import numpy as np

AVG_WINDOW_S = 60        # settlement averaging window (open & close)
TAU_AVG_ADJ = 40.0       # tau_eff = tau - 40s (Brownian average variance ~ 1/3)
EWMA_LAMBDA = 0.94
MIN_VOL_OBS = 30


# ---------------------------------------------------------------------------
def load(db: str):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    ticks = [dict(r) for r in c.execute("SELECT * FROM ticks ORDER BY ts_utc_ms")]
    settle = {r["ticker"]: dict(r) for r in c.execute("SELECT * FROM settlements")}
    c.close()
    return ticks, settle


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def fee(p: float, coeff: float = 0.07) -> float:
    """Kalshi taker fee per contract, rounded UP to the cent."""
    if p is None or p <= 0 or p >= 1:
        return 0.0
    return math.ceil(coeff * p * (1 - p) * 100) / 100.0


def ewma_sigma_per_sec(proxy: list[float], lam: float = EWMA_LAMBDA) -> list[float | None]:
    """Per-tick EWMA sigma of log-returns (per sqrt-tick ~ per-sec at 1Hz)."""
    out: list[float | None] = [None] * len(proxy)
    var = None
    n = 0
    for i in range(1, len(proxy)):
        if proxy[i] and proxy[i - 1] and proxy[i] > 0 and proxy[i - 1] > 0:
            r = math.log(proxy[i] / proxy[i - 1])
            var = r * r if var is None else lam * var + (1 - lam) * r * r
            n += 1
            out[i] = math.sqrt(var) if n >= MIN_VOL_OBS else None
    return out


def mean_in_window(ticks, lo_ms, hi_ms, field="brti_proxy"):
    vals = [t[field] for t in ticks if lo_ms <= t["ts_utc_ms"] <= hi_ms and t[field] is not None]
    return statistics.fmean(vals) if vals else None


# ---------------------------------------------------------------------------
@dataclass
class TradeResult:
    n: int
    mean: float
    ci_lo: float
    ci_hi: float

    @staticmethod
    def of(pnls: list[float]) -> "TradeResult":
        n = len(pnls)
        if n == 0:
            return TradeResult(0, float("nan"), float("nan"), float("nan"))
        m = statistics.fmean(pnls)
        sd = statistics.pstdev(pnls) if n > 1 else 0.0
        se = sd / math.sqrt(n) if n else 0.0
        return TradeResult(n, m, m - 1.96 * se, m + 1.96 * se)


def anchor_errors(ticks, settle):
    """Proxy 60s-avg vs the exact BRTI anchors (floor_strike / expiration_value)."""
    rows = []
    for tk, s in settle.items():
        op, cl = s.get("open_ts_ms"), s.get("close_ts_ms")
        if not op or not cl:
            continue
        po = mean_in_window(ticks, op - AVG_WINDOW_S * 1000, op)
        pc = mean_in_window(ticks, cl - AVG_WINDOW_S * 1000, cl)
        rows.append({
            "ticker": tk,
            "open_err": (po - s["floor_strike"]) if (po and s.get("floor_strike")) else None,
            "close_err": (pc - s["expiration_value"]) if (pc and s.get("expiration_value")) else None,
        })
    return rows


def lag_sweep(ticks, settle, lags=range(-20, 21, 2)):
    """Shift the proxy series by N seconds; which lag best matches the closing anchor?"""
    best = {}
    for lag in lags:
        errs = []
        for tk, s in settle.items():
            cl = s.get("close_ts_ms")
            if not cl or s.get("expiration_value") is None:
                continue
            pc = mean_in_window(ticks, cl - AVG_WINDOW_S * 1000 + lag * 1000, cl + lag * 1000)
            if pc:
                errs.append(abs(pc - s["expiration_value"]))
        if errs:
            best[lag] = statistics.fmean(errs)
    return best


def backtest(ticks, settle, latency_s=0.5, edge_thresh=0.02, seed=42):
    """Hold-to-settlement backtest at executable prices + fees + latency.

    For each decision tick with a settled outcome: compute fair; if executable
    edge (fair - ask - fee for YES, or (1-fair) - (1-bid)... i.e. bid - fair - fee
    for selling YES / buying NO) >= threshold, 'enter' at the price `latency_s`
    later and hold to settlement. PnL is netted per contract.
    """
    rng = np.random.default_rng(seed)
    proxy = [t["brti_proxy"] for t in ticks]
    sig = ewma_sigma_per_sec(proxy)

    # per-window opening proxy average (the proxy analogue of floor_strike)
    open_avg: dict[str, float] = {}
    for tk, s in settle.items():
        op = s.get("open_ts_ms")
        if op:
            pa = mean_in_window(ticks, op - AVG_WINDOW_S * 1000, op)
            if pa:
                open_avg[tk] = pa

    # index ticks by time for latency lookups
    ts_arr = [t["ts_utc_ms"] for t in ticks]

    def price_after(i, field):
        target = ts_arr[i] + int(latency_s * 1000)
        j = i
        while j < len(ticks) and ts_arr[j] < target:
            j += 1
        return ticks[j][field] if j < len(ticks) else None

    sig_pnls, rand_pnls = [], []
    calib = []  # (fair, outcome)
    n_decisions = 0

    for i, t in enumerate(ticks):
        tk = t["kalshi_ticker"]
        s = settle.get(tk)
        if not s or s.get("result") not in ("yes", "no"):
            continue
        if t.get("flag_stale") or t.get("flag_crossed") or t.get("flag_last60") or t.get("flag_warming"):
            continue
        oa = open_avg.get(tk)
        sigma = sig[i]
        stc = t.get("secs_to_close")
        if not oa or not sigma or stc is None or proxy[i] is None:
            continue
        tau_eff = max(stc - TAU_AVG_ADJ, 0.0)
        if tau_eff <= 0:
            continue
        z = math.log(proxy[i] / oa) / (sigma * math.sqrt(tau_eff))
        fair = norm_cdf(z)
        outcome = 1 if s["result"] == "yes" else 0
        calib.append((fair, outcome))
        n_decisions += 1

        ask, bid = t.get("yes_ask"), t.get("yes_bid")
        if ask is None or bid is None:
            continue
        # executable edges
        buy_yes_edge = fair - ask - fee(ask)
        buy_no_edge = (1 - fair) - (1 - bid) - fee(1 - bid)   # = bid - fair - fee
        if buy_yes_edge >= edge_thresh:
            entry = price_after(i, "yes_ask")
            if entry is not None:
                sig_pnls.append((outcome - entry - fee(entry)))
        elif buy_no_edge >= edge_thresh:
            entry_bid = price_after(i, "yes_bid")
            if entry_bid is not None:
                # buy NO at (1 - bid); NO pays 1 if outcome==0
                no_cost = 1 - entry_bid
                no_payoff = 1 - outcome
                sig_pnls.append((no_payoff - no_cost - fee(no_cost)))

    # random-entry control: same count of trades, random ticks, buy YES at ask, hold
    valid = [i for i, t in enumerate(ticks)
             if settle.get(t["kalshi_ticker"], {}).get("result") in ("yes", "no")
             and t.get("yes_ask") is not None
             and not (t.get("flag_stale") or t.get("flag_crossed") or t.get("flag_last60"))]
    k = len(sig_pnls)
    if valid and k:
        for i in rng.choice(valid, size=min(k, len(valid)), replace=len(valid) < k):
            t = ticks[int(i)]
            s = settle[t["kalshi_ticker"]]
            outcome = 1 if s["result"] == "yes" else 0
            entry = t["yes_ask"]
            rand_pnls.append(outcome - entry - fee(entry))

    return {
        "n_decisions": n_decisions,
        "signal": TradeResult.of(sig_pnls),
        "random": TradeResult.of(rand_pnls),
        "calibration": calib,
        "params": {"latency_s": latency_s, "edge_thresh": edge_thresh, "seed": seed},
    }


def calibration_table(calib, bins=10):
    if not calib:
        return []
    rows = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        bucket = [o for f, o in calib if lo <= f < hi]
        if bucket:
            rows.append((lo, hi, len(bucket), statistics.fmean(bucket)))
    return rows


def report(db: str, latency_s: float, edge_thresh: float, seed: int):
    ticks, settle = load(db)
    print(f"=== KILL-TEST HARNESS === {db}")
    print(f"ticks={len(ticks)}  settled_windows={len(settle)}  "
          f"(labels with result: {sum(1 for s in settle.values() if s.get('result') in ('yes','no'))})")
    if len(ticks) < 100 or not settle:
        print("\n[insufficient data] need more ticks and >=1 settled window. "
              "Let the collector run longer, then re-run.")
        return

    # 1-2 anchor errors
    ae = anchor_errors(ticks, settle)
    oe = [r["open_err"] for r in ae if r["open_err"] is not None]
    ce = [r["close_err"] for r in ae if r["close_err"] is not None]
    print("\n[1/2] Proxy tracking error vs exact BRTI anchors ($):")
    if oe:
        print(f"  opening: n={len(oe)} mean={statistics.fmean(oe):+.2f} "
              f"absmean={statistics.fmean(map(abs,oe)):.2f} max|={max(map(abs,oe)):.2f}")
    if ce:
        print(f"  closing: n={len(ce)} mean={statistics.fmean(ce):+.2f} "
              f"absmean={statistics.fmean(map(abs,ce)):.2f} max|={max(map(abs,ce)):.2f}")
    if not oe and not ce:
        print("  (no window had 60s of pre-open/pre-close proxy coverage yet)")

    # 3 lag sweep
    ls = lag_sweep(ticks, settle)
    if ls:
        best = min(ls, key=ls.get)
        print(f"\n[3] Lag sweep (closing anchor abs err by proxy shift): "
              f"best lag = {best:+d}s (err ${ls[best]:.2f})")

    # 4-6 backtest
    bt = backtest(ticks, settle, latency_s, edge_thresh, seed)
    print(f"\n[5/6] Hold-to-settlement backtest "
          f"(latency={latency_s}s, edge>={edge_thresh*100:.0f}c, seed={seed}):")
    print(f"  decisions evaluated: {bt['n_decisions']}")
    s, r = bt["signal"], bt["random"]
    print(f"  SIGNAL  trades={s.n:4d}  EV/trade={s.mean*100:+.2f}c  "
          f"95%CI=[{s.ci_lo*100:+.2f}, {s.ci_hi*100:+.2f}]c" if s.n else "  SIGNAL  no qualifying trades")
    print(f"  RANDOM  trades={r.n:4d}  EV/trade={r.mean*100:+.2f}c  "
          f"95%CI=[{r.ci_lo*100:+.2f}, {r.ci_hi*100:+.2f}]c" if r.n else "  RANDOM  no trades")

    print("\n[4] Calibration (predicted fair -> realized YES rate):")
    for lo, hi, n, rate in calibration_table(bt["calibration"]):
        bar = "#" * round(rate * 20)
        print(f"  {lo:.1f}-{hi:.1f}  n={n:4d}  realized={rate:.2f}  {bar}")

    # verdict against the locked threshold (D16)
    print("\n=== READ AGAINST LOCKED THRESHOLD (D16) ===")
    if s.n == 0:
        print("  No qualifying signal trades -> no evidence of edge. (Likely: gap never")
        print("  clears costs.) Lean: NO EDGE. Collect more, but this is the expected result.")
    elif s.ci_lo > 0 and s.mean > r.mean:
        print(f"  Signal EV/trade CI lower bound > 0 ({s.ci_lo*100:+.2f}c) AND beats random.")
        print("  CANDIDATE survivor -> needs walk-forward OOS + more windows before any capital.")
    else:
        print(f"  Signal EV CI includes <=0 or does not beat random -> NO EDGE after costs.")
    print("  (Reminder: marginal/in-sample-only never graduates. Real money needs OOS + size limits.)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--latency", type=float, default=0.5)
    ap.add_argument("--edge", type=float, default=0.02)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    report(args.db, args.latency, args.edge, args.seed)
