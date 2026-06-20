"""
Kalshi REST client for the KXBTC15M research data layer.

Scope (Phase 0): read-only market data only -- list/fetch markets, top of
book, orderbook, candlesticks, and the on-record settlement fields
(`floor_strike`, `expiration_value`). NO order placement here; real-money
execution is Phase 3 and gated.

Auth: per-request RSA-PSS(SHA256) signature over `timestamp_ms + METHOD + path`
(path WITHOUT query string), per Kalshi API key docs. Works unauthenticated
for public market-data endpoints too -- if no key is configured, requests are
sent without signature headers (public reads still succeed; private endpoints
will 401).

Docs: https://docs.kalshi.com/getting_started/api_keys
      https://docs.kalshi.com/api-reference/market/get-markets
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


# ---------------------------------------------------------------------------
# Normalized market snapshot. Prices kept in integer cents (Kalshi native).
# ---------------------------------------------------------------------------
@dataclass
class MarketSnapshot:
    """One KXBTC15M market snapshot.

    Prices are DOLLAR FLOATS in [0, 1], NOT integer cents. KXBTC15M uses a
    `tapered_deci_cent` tick (0.001 steps in the 0-0.10 and 0.90-1.00 tails),
    so cents would silently truncate exactly the late-window resolution the
    strategy depends on. We keep full dollar precision.
    """
    ticker: str
    event_ticker: str | None
    status: str | None              # object enum: initialized/active/.../finalized
    result: str | None              # "" until settled, then "yes"/"no"
    floor_strike: float | None      # strike the YES contract is measured against
    cap_strike: float | None
    strike_type: str | None         # e.g. "greater_or_equal"
    expiration_value: float | None  # on-record settlement reference ("" pre-close)
    open_time: str | None
    close_time: str | None
    yes_bid: float | None           # dollars [0,1]
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    last_price: float | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_settled(self) -> bool:
        return bool(self.result)

    @classmethod
    def from_api(cls, m: dict[str, Any]) -> "MarketSnapshot":
        def dollars(key: str) -> float | None:
            # Prefer *_dollars; fall back to legacy integer-cent field / 100.
            d = m.get(f"{key}_dollars")
            if d is not None and d != "":
                return float(d)
            c = m.get(key)
            return float(c) / 100.0 if c is not None else None

        def num(key: str) -> float | None:
            v = m.get(key)
            return float(v) if v not in (None, "") else None

        return cls(
            ticker=m["ticker"],
            event_ticker=m.get("event_ticker"),
            status=m.get("status"),
            result=m.get("result") or "",
            floor_strike=num("floor_strike"),
            cap_strike=num("cap_strike"),
            strike_type=m.get("strike_type"),
            expiration_value=num("expiration_value"),
            open_time=m.get("open_time"),
            close_time=m.get("close_time"),
            yes_bid=dollars("yes_bid"),
            yes_ask=dollars("yes_ask"),
            no_bid=dollars("no_bid"),
            no_ask=dollars("no_ask"),
            last_price=dollars("last_price"),
            raw=m,
        )


class KalshiClient:
    def __init__(
        self,
        rest_base: str,
        api_key_id: str | None = None,
        private_key_pem_path: str | None = None,
        private_key_pem: str | None = None,
        timeout: float = 10.0,
    ):
        self.rest_base = rest_base.rstrip("/")
        # path prefix to sign, e.g. "/trade-api/v2"
        self._path_prefix = "/" + self.rest_base.split("//", 1)[-1].split("/", 1)[-1]
        self.api_key_id = api_key_id
        self.timeout = timeout
        self._priv: rsa.RSAPrivateKey | None = None
        if private_key_pem:  # inline PEM contents take precedence
            self._priv = self._load_key_bytes(private_key_pem.encode())
        elif private_key_pem_path and Path(private_key_pem_path).expanduser().exists():
            self._priv = self._load_key(private_key_pem_path)
        elif private_key_pem_path:
            import warnings
            warnings.warn(
                f"Kalshi private key not found at {private_key_pem_path!r}; "
                "falling back to UNAUTHENTICATED public reads. Set "
                "KALSHI_PRIVATE_KEY_PEM_PATH in .env to enable signed requests.",
                stacklevel=2,
            )
        self._session = requests.Session()

    # --- auth ---------------------------------------------------------------
    @classmethod
    def _load_key(cls, pem_path: str) -> rsa.RSAPrivateKey:
        return cls._load_key_bytes(Path(pem_path).expanduser().read_bytes())

    @staticmethod
    def _load_key_bytes(data: bytes) -> rsa.RSAPrivateKey:
        key = serialization.load_pem_private_key(data, password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise TypeError("Kalshi API key must be an RSA private key")
        return key

    def _sign(self, ts_ms: str, method: str, path_no_query: str) -> str:
        msg = (ts_ms + method.upper() + path_no_query).encode()
        sig = self._priv.sign(  # type: ignore[union-attr]
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode()

    def _headers(self, method: str, path_no_query: str) -> dict[str, str]:
        if not (self.api_key_id and self._priv):
            return {}  # public read; unauthenticated
        ts_ms = str(int(time.time() * 1000))
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts_ms,
            "KALSHI-ACCESS-SIGNATURE": self._sign(ts_ms, method, path_no_query),
        }

    # --- core request -------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        # path is the part AFTER the rest_base, e.g. "/markets".
        full_path = self._path_prefix + path  # signed path includes the version prefix
        for attempt in range(5):
            r = self._session.get(
                self.rest_base + path,
                params=params,
                headers=self._headers("GET", full_path),
                timeout=self.timeout,
            )
            if r.status_code == 429:  # rate limited -> backoff and retry
                time.sleep(0.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()
        return r.json()

    # --- market data --------------------------------------------------------
    def get_markets(
        self,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        status: str | None = None,
        tickers: list[str] | None = None,
        min_close_ts: int | None = None,
        max_close_ts: int | None = None,
        limit: int = 1000,
    ) -> Iterator[MarketSnapshot]:
        """Yield markets, transparently following the cursor pagination."""
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": limit}
            if series_ticker:
                params["series_ticker"] = series_ticker
            if event_ticker:
                params["event_ticker"] = event_ticker
            if status:
                params["status"] = status
            if tickers:
                params["tickers"] = ",".join(tickers)
            if min_close_ts is not None:
                params["min_close_ts"] = min_close_ts
            if max_close_ts is not None:
                params["max_close_ts"] = max_close_ts
            if cursor:
                params["cursor"] = cursor
            data = self._get("/markets", params)
            for m in data.get("markets", []):
                yield MarketSnapshot.from_api(m)
            cursor = data.get("cursor") or None
            if not cursor:
                break

    def get_market(self, ticker: str) -> MarketSnapshot:
        data = self._get(f"/markets/{ticker}")
        return MarketSnapshot.from_api(data["market"])

    def get_orderbook(self, ticker: str, depth: int = 10) -> dict:
        """Raw orderbook: {'yes': [[price, qty], ...], 'no': [...]}.

        Kalshi returns bids only; a YES bid at X == a NO ask at (100 - X).
        """
        data = self._get(f"/markets/{ticker}/orderbook", {"depth": depth})
        return data.get("orderbook", data)

    def get_candlesticks(
        self,
        series_ticker: str,
        ticker: str,
        start_ts: int,
        end_ts: int,
        period_interval: int = 1,  # minutes; no native 15m -> aggregate 1m
    ) -> list[dict]:
        path = f"/series/{series_ticker}/markets/{ticker}/candlesticks"
        data = self._get(path, {
            "start_ts": start_ts, "end_ts": end_ts,
            "period_interval": period_interval,
        })
        return data.get("candlesticks", [])

    def get_trades(self, ticker: str | None = None, limit: int = 1000,
                   min_ts: int | None = None, max_ts: int | None = None) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if min_ts is not None:
            params["min_ts"] = min_ts
        if max_ts is not None:
            params["max_ts"] = max_ts
        return self._get("/markets/trades", params).get("trades", [])

    # --- KXBTC15M convenience ----------------------------------------------
    def active_btc15m_markets(self, series_ticker: str = "KXBTC15M") -> list[MarketSnapshot]:
        """Currently-open 15m BTC markets (the live tradeable windows).

        The GetMarkets `status` FILTER uses legacy aliases
        (open/settled/unopened/closed), distinct from the Market object's own
        `status` enum (initialized/active/.../finalized).
        """
        return list(self.get_markets(series_ticker=series_ticker, status="open"))


if __name__ == "__main__":
    # Smoke test: unauthenticated public read of KXBTC15M markets.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config.settings import load_config

    cfg = load_config()
    client = KalshiClient(
        rest_base=cfg.kalshi.rest_base,
        api_key_id=cfg.secrets.api_key_id,
        private_key_pem_path=cfg.secrets.private_key_pem_path,
        private_key_pem=cfg.secrets.private_key_pem,
    )
    print(f"Querying {cfg.kalshi.series_ticker} (auth: "
          f"{'yes' if cfg.secrets.api_key_id else 'public/unauth'}) ...")
    mkts = client.active_btc15m_markets(cfg.kalshi.series_ticker)
    print(f"  {len(mkts)} active markets")
    for m in mkts[:8]:
        print(f"  {m.ticker:34s} strike={m.floor_strike} "
              f"yes_bid/ask={m.yes_bid}/{m.yes_ask} close={m.close_time}")
