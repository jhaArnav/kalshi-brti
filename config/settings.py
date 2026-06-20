"""
Typed, validated configuration for the Kalshi 15m BTC research system.

Loads `config/default.toml` (override path via $KBRTI_CONFIG) and Kalshi
secrets from the environment / .env (never committed). Everything the engine
needs at runtime flows through `load_config()` -> `Config`, so no costs,
fees, latency, or thresholds are hardcoded anywhere downstream.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "default.toml"


class DataCfg(BaseModel):
    spot_venues: list[str]
    brti_staleness_secs: float
    log_format: str
    log_dir: str
    flush_every_secs: float


class KalshiCfg(BaseModel):
    rest_base: str
    ws_base: str
    series_ticker: str
    poll_interval_secs: float


class FeeProfile(BaseModel):
    # one of the per-profile tables; fields optional so both shapes validate
    maker_bps: float | None = None
    taker_per_contract: float | None = None
    trade_fee_coeff: float | None = None
    maker_per_contract: float | None = None


class CostsCfg(BaseModel):
    fill_side: str
    slippage_cents: float
    fees: dict[str, FeeProfile]


class LatencyCfg(BaseModel):
    decision_to_fill_ms: int
    quote_age_tolerance_ms: int


class VolCfg(BaseModel):
    model: str
    ewma_lambda: float
    ewma_min_obs: int
    return_sampling_secs: float


class SettlementCfg(BaseModel):
    averaging_window_secs: float


class SignalCfg(BaseModel):
    entry_threshold: float
    horizon_secs: int


class BacktestCfg(BaseModel):
    random_seed: int
    random_control_trials: int
    walkforward_train_frac: float
    walkforward_n_splits: int


class RiskCfg(BaseModel):
    enabled: bool
    max_contracts_per_trade: int
    bankroll_cap_usd: float
    daily_loss_limit_usd: float
    kill_switch: bool


class KalshiSecrets(BaseModel):
    """Loaded from env, not from toml. Empty until the user provides them."""
    api_key_id: str | None = Field(default=None)
    private_key_pem_path: str | None = Field(default=None)
    private_key_pem: str | None = Field(default=None)  # inline contents (optional)


class Config(BaseModel):
    data: DataCfg
    kalshi: KalshiCfg
    costs: CostsCfg
    latency: LatencyCfg
    vol: VolCfg
    settlement: SettlementCfg
    signal: SignalCfg
    backtest: BacktestCfg
    risk: RiskCfg
    secrets: KalshiSecrets = KalshiSecrets()


def _load_env_file(path: Path) -> None:
    """Minimal .env loader (avoids a hard python-dotenv dep)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path: str | os.PathLike | None = None) -> Config:
    cfg_path = Path(path or os.environ.get("KBRTI_CONFIG", DEFAULT_CONFIG_PATH))
    with open(cfg_path, "rb") as f:
        raw = tomllib.load(f)

    _load_env_file(REPO_ROOT / ".env")
    secrets = KalshiSecrets(
        api_key_id=os.environ.get("KALSHI_API_KEY_ID"),
        private_key_pem_path=os.environ.get("KALSHI_PRIVATE_KEY_PEM_PATH"),
        private_key_pem=os.environ.get("KALSHI_PRIVATE_KEY_PEM"),
    )
    return Config(**raw, secrets=secrets)


if __name__ == "__main__":
    c = load_config()
    print("Loaded config OK.")
    print(f"  series       : {c.kalshi.series_ticker}")
    print(f"  spot venues  : {c.data.spot_venues}")
    print(f"  vol model    : {c.vol.model} (lambda={c.vol.ewma_lambda})")
    print(f"  fee profiles : {list(c.costs.fees.keys())}")
    print(f"  latency      : {c.latency.decision_to_fill_ms}ms")
    print(f"  kalshi creds : {'set' if c.secrets.api_key_id else 'MISSING (.env)'}")
