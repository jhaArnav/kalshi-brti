"""
NAIVE fair-value proxy for the dashboard signal line. *** NOT the model. ***

The real digital-option fair value + vol estimator is Phase 1 (model/). This
is a deliberately simple, clearly-labeled placeholder so the live dashboard
has a meaningful "fair" and "signal = kalshi_mid - fair" to plot before Phase
1 exists. Treat its signal as indicative only.

Model: P(BRTI_close >= strike) under a driftless Gaussian on log-price, with a
per-second vol estimated by EWMA of 1s BRTI returns. It does NOT model the
60s settlement averaging (which dampens late spikes) -- another reason this is
a proxy, not the tradeable signal.
"""
from __future__ import annotations

import math
import time


class EwmaVol:
    """EWMA of per-second log returns -> per-second sigma (RiskMetrics)."""
    def __init__(self, lam: float = 0.94, min_obs: int = 30, sample_secs: float = 1.0):
        self.lam = lam
        self.min_obs = min_obs
        self.sample_secs = sample_secs
        self._var: float | None = None
        self._last_px: float | None = None
        self._last_t: float = 0.0
        self.n = 0

    def update(self, price: float) -> None:
        t = time.time()
        if self._last_px is not None and t - self._last_t >= self.sample_secs and price > 0:
            r = math.log(price / self._last_px)
            self._var = r * r if self._var is None else self.lam * self._var + (1 - self.lam) * r * r
            self.n += 1
            self._last_px = price
            self._last_t = t
        elif self._last_px is None:
            self._last_px = price
            self._last_t = t

    @property
    def sigma_per_sec(self) -> float | None:
        if self._var is None or self.n < self.min_obs:
            return None
        return math.sqrt(self._var)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def fair_value(brti: float, strike: float, secs_to_close: float,
               sigma_per_sec: float | None, strike_type: str = "greater_or_equal"
               ) -> float | None:
    """P(BRTI_close >= strike) proxy in [0,1]. None if inputs insufficient."""
    if sigma_per_sec is None or brti <= 0 or strike <= 0:
        return None
    secs = max(secs_to_close, 0.0)
    if secs <= 0:
        return 1.0 if brti >= strike else 0.0
    total_sigma = sigma_per_sec * math.sqrt(secs)  # log-return stdev to close
    if total_sigma <= 0:
        return 1.0 if brti >= strike else 0.0
    z = math.log(brti / strike) / total_sigma
    p_ge = _norm_cdf(z)
    return p_ge if strike_type == "greater_or_equal" else 1.0 - p_ge
