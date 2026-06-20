"""
Kalshi WebSocket client -- canonical live price via the `ticker` channel.

WHY THIS REWRITE (council + bug #1): reconstructing top-of-book from
`orderbook_delta` drifts/desyncs and froze into a crossed, stale price that
produced false signals. Kalshi's `ticker` channel pushes authoritative
`price_dollars` / `yes_bid_dollars` / `yes_ask_dollars` on every change. We use
that as canonical, and we report REAL data age (time since the last ticker
message), NOT socket ping.

The `ticker` channel only pushes on CHANGE, so on a quiet market the data age
grows -- which is the honest truth and exactly what the harness must record.
We seed an initial top-of-book with one REST GetMarket on each window so we have
a value before the first change.

REST is still used for window discovery and the fields the ticker lacks:
`floor_strike` (locked opening 60s-avg BRTI), `strike_type`, `close_time`,
`expiration_value` (post-settlement).

`orderbook_delta` is intentionally NOT used here anymore (it remains available
as a secondary diagnostic, to be validated by periodic REST reconciliation).
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from data.kalshi_client import KalshiClient, MarketSnapshot


def _iso_ms(iso: str | None) -> int | None:
    if not iso:
        return None
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


@dataclass
class KalshiTop:
    """Canonical live top-of-book from the ticker channel (dollars [0,1])."""
    ticker: str | None = None
    strike: float | None = None             # floor_strike = opening 60s-avg BRTI
    strike_type: str | None = None
    close_ts_ms: int | None = None
    open_ts_ms: int | None = None
    expiration_value: float | None = None   # closing 60s-avg BRTI (post-settle)
    yes_bid: float | None = None
    yes_ask: float | None = None
    last_price: float | None = None
    volume: float | None = None
    last_update_ms: float = 0.0             # wall time of last ticker msg (REAL age)
    source: str = "init"                   # "ticker" once live, "rest_seed" at start

    @property
    def yes_mid(self) -> float | None:
        if self.yes_bid is not None and self.yes_ask is not None:
            return round((self.yes_bid + self.yes_ask) / 2, 4)
        return self.yes_bid if self.yes_bid is not None else self.yes_ask

    @property
    def crossed(self) -> bool:
        return (self.yes_bid is not None and self.yes_ask is not None
                and self.yes_bid >= self.yes_ask)

    def age_ms(self, now_ms: float | None = None) -> float | None:
        if not self.last_update_ms:
            return None
        return (now_ms or time.time() * 1000) - self.last_update_ms


class KalshiWS:
    def __init__(self, cfg):
        self.cfg = cfg
        self.book = KalshiTop()            # name kept as .book for back-compat
        self._priv = serialization.load_pem_private_key(_read_key(cfg.secrets), password=None)
        if not isinstance(self._priv, rsa.RSAPrivateKey):
            raise TypeError("Kalshi WS requires an RSA private key")
        self._rest = KalshiClient(
            rest_base=cfg.kalshi.rest_base,
            api_key_id=cfg.secrets.api_key_id,
            private_key_pem_path=cfg.secrets.private_key_pem_path,
            private_key_pem=cfg.secrets.private_key_pem,
        )
        self._ws_path = "/" + cfg.kalshi.ws_base.split("//", 1)[-1].split("/", 1)[-1]

    # --- auth ---
    def _sign(self, ts_ms: str) -> str:
        sig = self._priv.sign(
            (ts_ms + "GET" + self._ws_path).encode(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256())
        return base64.b64encode(sig).decode()

    def _headers(self) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        return {"KALSHI-ACCESS-KEY": self.cfg.secrets.api_key_id,
                "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": self._sign(ts)}

    # --- window discovery (REST) ---
    def _current_market(self) -> MarketSnapshot | None:
        try:
            mkts = self._rest.active_btc15m_markets(self.cfg.kalshi.series_ticker)
        except Exception as e:
            print(f"[kalshi ws: market lookup] {e}", file=sys.stderr)
            return None
        return min(mkts, key=lambda m: m.close_time or "") if mkts else None

    async def run(self) -> None:
        loop = asyncio.get_event_loop()
        while True:
            try:
                m = await loop.run_in_executor(None, self._current_market)
                if not m:
                    await asyncio.sleep(2)
                    continue
                await self._stream_market(m)
            except Exception as e:
                print(f"[kalshi ws reconnect] {e}", file=sys.stderr)
                await asyncio.sleep(2)

    async def _stream_market(self, m: MarketSnapshot) -> None:
        # seed top-of-book + metadata from the REST record (so we have a value
        # before the first ticker change). source='rest_seed' until ticker pushes.
        self.book = KalshiTop(
            ticker=m.ticker, strike=m.floor_strike, strike_type=m.strike_type,
            close_ts_ms=_iso_ms(m.close_time), open_ts_ms=_iso_ms(m.open_time),
            expiration_value=m.expiration_value,
            yes_bid=m.yes_bid, yes_ask=m.yes_ask, last_price=m.last_price,
            last_update_ms=time.time() * 1000, source="rest_seed")
        close_ms = self.book.close_ts_ms

        async with websockets.connect(self.cfg.kalshi.ws_base,
                                      additional_headers=self._headers(),
                                      ping_interval=10) as ws:
            await ws.send(json.dumps({"id": 1, "cmd": "subscribe",
                "params": {"channels": ["ticker"], "market_tickers": [m.ticker]}}))
            while True:
                if close_ms and time.time() * 1000 > close_ms + 1000:
                    return  # window closed -> roll to next
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    continue
                msg = json.loads(raw)
                if msg.get("type") != "ticker":
                    continue
                d = msg.get("msg", {})
                if d.get("market_ticker") and d["market_ticker"] != m.ticker:
                    continue
                self._apply_ticker(d)

    def _apply_ticker(self, d: dict) -> None:
        def dol(k):
            v = d.get(k)
            return float(v) if v not in (None, "") else None
        yb, ya, last = dol("yes_bid_dollars"), dol("yes_ask_dollars"), dol("price_dollars")
        if yb is not None:
            self.book.yes_bid = round(yb, 4)
        if ya is not None:
            self.book.yes_ask = round(ya, 4)
        if last is not None:
            self.book.last_price = round(last, 4)
        vol = d.get("volume_fp") or d.get("volume")
        if vol not in (None, ""):
            self.book.volume = float(vol)
        self.book.last_update_ms = time.time() * 1000
        self.book.source = "ticker"


def _read_key(secrets) -> bytes:
    if secrets.private_key_pem:
        return secrets.private_key_pem.encode()
    from pathlib import Path
    return Path(secrets.private_key_pem_path).expanduser().read_bytes()


if __name__ == "__main__":
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config.settings import load_config

    async def _demo():
        kws = KalshiWS(load_config())
        task = asyncio.create_task(kws.run())
        for _ in range(12):
            await asyncio.sleep(1)
            b = kws.book
            age = b.age_ms()
            print(f"{b.ticker} yes={b.yes_bid}/{b.yes_ask} mid={b.yes_mid} "
                  f"last={b.last_price} strike={b.strike} crossed={b.crossed} "
                  f"src={b.source} age={age:.0f}ms" if age is not None
                  else f"{b.ticker} (warming up)")
        task.cancel()

    asyncio.run(_demo())
