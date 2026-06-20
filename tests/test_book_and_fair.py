"""Unit tests for the latency-critical, easy-to-get-wrong bits:
order-book reconstruction and the naive fair-value proxy."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.kalshi_ws import KalshiBook
from dashboard.fair_naive import EwmaVol, fair_value


# --- order book -------------------------------------------------------------
def test_snapshot_best_bid_ask():
    b = KalshiBook()
    # yes bids up to 0.40; no bids up to 0.55 -> yes_ask = 1-0.55 = 0.45
    b.apply_snapshot({
        "yes_dollars_fp": [["0.4000", "10"], ["0.3900", "5"]],
        "no_dollars_fp": [["0.5500", "8"], ["0.5400", "3"]],
    })
    assert b.yes_bid == 0.40
    assert b.yes_ask == 0.45
    assert b.yes_mid == 0.425
    assert b.crossed is False


def test_delta_add_and_remove():
    b = KalshiBook()
    b.apply_snapshot({"yes_dollars_fp": [["0.40", "10"]],
                      "no_dollars_fp": [["0.55", "8"]]})
    # better yes bid appears at 0.42
    b.apply_delta({"side": "yes", "price_dollars": "0.4200", "delta_fp": "4"})
    assert b.yes_bid == 0.42
    # it gets fully removed -> falls back to 0.40
    b.apply_delta({"side": "yes", "price_dollars": "0.4200", "delta_fp": "-4"})
    assert b.yes_bid == 0.40


def test_crossed_flag():
    b = KalshiBook()
    # yes bid 0.54 and no bid 0.48 -> yes_ask 0.52 -> crossed (bid>ask)
    b.apply_snapshot({"yes_dollars_fp": [["0.54", "1"]],
                      "no_dollars_fp": [["0.48", "1"]]})
    assert b.yes_bid == 0.54
    assert b.yes_ask == 0.52
    assert b.crossed is True


def test_legacy_integer_cents():
    b = KalshiBook()
    # some schema variants give integer-cent prices (1..99); should /100
    b.apply_snapshot({"yes": [[40, 10]], "no": [[55, 8]]})
    assert b.yes_bid == 0.40
    assert b.yes_ask == 0.45


# --- fair value -------------------------------------------------------------
def test_fair_at_strike_is_half():
    # BRTI exactly at strike -> P(>=) = 0.5 regardless of vol/time
    f = fair_value(brti=60000, strike=60000, secs_to_close=300,
                   sigma_per_sec=1e-4)
    assert abs(f - 0.5) < 1e-9


def test_fair_monotonic_in_price():
    lo = fair_value(59900, 60000, 300, 1e-4)
    hi = fair_value(60100, 60000, 300, 1e-4)
    assert lo < 0.5 < hi


def test_fair_at_expiry_is_step():
    assert fair_value(60001, 60000, 0, 1e-4) == 1.0
    assert fair_value(59999, 60000, 0, 1e-4) == 0.0


def test_fair_less_or_equal_inverts():
    ge = fair_value(60100, 60000, 300, 1e-4, "greater_or_equal")
    le = fair_value(60100, 60000, 300, 1e-4, "less_or_equal")
    assert abs((ge + le) - 1.0) < 1e-9


def test_fair_none_without_vol():
    assert fair_value(60000, 60000, 300, None) is None


def test_ewma_warmup_then_emits():
    v = EwmaVol(min_obs=3, sample_secs=0.0)  # sample_secs=0 -> accept every tick
    assert v.sigma_per_sec is None
    for px in (60000, 60010, 59990, 60005, 60002):
        v.update(px)
    # after >=3 obs it should produce a positive sigma
    assert v.sigma_per_sec is not None and v.sigma_per_sec > 0
