"""
Kalshi WebSocket client -- near-zero-latency market data for the dashboard.

REST polling (~1s, ~800ms stale) is useless when the BRTI->Kalshi gap closes
in 10-14s. This subscribes to the `orderbook_delta` channel, maintains the
live order book from the initial snapshot + incremental deltas, and exposes
the current best YES bid/ask/mid with sub-second freshness.

Book convention (Kalshi): both YES and NO resting bids are published. A NO bid
at price p is equivalent to a YES ask at (1 - p). So:
    best_yes_bid = max YES price with qty > 0
    best_yes_ask = 1 - (max NO price with qty > 0)

Auth: RSA-PSS(SHA256) headers on the connection handshake, signing
`timestamp_ms + "GET" + "/trade-api/ws/v2"` (same scheme as REST).

Auto-rolls to the next 15m window: a small REST helper finds the current open
market; when it closes, we resubscribe to the new one.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from dataclasses import dataclass, field

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from data.kalshi_client import KalshiClient, MarketSnapshot


@dataclass
class KalshiBook:
    """Live top-of-book derived from the maintained order book."""
    ticker: str | None = None
    strike: float | None = None
    strike_type: str | None = None
    close_ts_ms: int | None = None
    yes_bid: float | None = None
    yes_ask: float | None = None
    crossed: bool = False
    last_update_ms: float = 0.0
    # internal price->qty ladders (dollars -> contracts)
    _yes: dict[float, float] = field(default_factory=dict, repr=False)
    _no: dict[float, float] = field(default_factory=dict, repr=False)

    @property
    def yes_mid(self) -> float | None:
        if self.yes_bid is not None and self.yes_ask is not None:
            return round((self.yes_bid + self.yes_ask) / 2, 4)
        return self.yes_bid if self.yes_bid is not None else self.yes_ask

    def _recompute(self) -> None:
        yb = max((p for p, q in self._yes.items() if q > 0), default=None)
        nb = max((p for p, q in self._no.items() if q > 0), default=None)
        self.yes_bid = round(yb, 4) if yb is not None else None
        self.yes_ask = round(1.0 - nb, 4) if nb is not None else None
        # A crossed book (bid > ask) is transient opening-auction noise; flag it
        # rather than silently emitting an inverted spread.
        self.crossed = (self.yes_bid is not None and self.yes_ask is not None
                        and self.yes_bid > self.yes_ask)

    def apply_snapshot(self, msg: dict) -> None:
        self._yes.clear()
        self._no.clear()
        for side, store in (("yes", self._yes), ("no", self._no)):
            levels = msg.get(f"{side}_dollars_fp") or msg.get(side) or []
            for lvl in levels:
                price = float(lvl[0])
                qty = float(lvl[1])
                # legacy integer-cent levels: price is 1..99 -> dollars
                if price > 1.0:
                    price = price / 100.0
                if qty > 0:
                    store[round(price, 4)] = qty
        self._recompute()

    def apply_delta(self, msg: dict) -> None:
        side = msg.get("side")
        store = self._yes if side == "yes" else self._no
        price = msg.get("price_dollars")
        price = float(price) if price is not None else float(msg["price"]) / 100.0
        if price > 1.0:
            price = price / 100.0
        price = round(price, 4)
        delta = float(msg.get("delta_fp", msg.get("delta", 0)))
        store[price] = store.get(price, 0.0) + delta
        if store[price] <= 0:
            store.pop(price, None)
        self._recompute()


class KalshiWS:
    def __init__(self, cfg):
        self.cfg = cfg
        self.book = KalshiBook()
        self._priv = serialization.load_pem_private_key(
            _read_key(cfg.secrets), password=None)
        if not isinstance(self._priv, rsa.RSAPrivateKey):
            raise TypeError("Kalshi WS requires an RSA private key")
        self._rest = KalshiClient(
            rest_base=cfg.kalshi.rest_base,
            api_key_id=cfg.secrets.api_key_id,
            private_key_pem_path=cfg.secrets.private_key_pem_path,
            private_key_pem=cfg.secrets.private_key_pem,
        )
        self._ws_path = "/" + cfg.kalshi.ws_base.split("//", 1)[-1].split("/", 1)[-1]

    def _sign(self, ts_ms: str) -> str:
        msg = (ts_ms + "GET" + self._ws_path).encode()
        sig = self._priv.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode()

    def _headers(self) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        return {
            "KALSHI-ACCESS-KEY": self.cfg.secrets.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": self._sign(ts),
        }

    def _current_market(self) -> MarketSnapshot | None:
        try:
            mkts = self._rest.active_btc15m_markets(self.cfg.kalshi.series_ticker)
        except Exception as e:
            print(f"[kalshi ws: market lookup] {e}", file=sys.stderr)
            return None
        if not mkts:
            return None
        return min(mkts, key=lambda m: m.close_time or "")

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
        from datetime import datetime
        close_ms = (int(datetime.fromisoformat(
            m.close_time.replace("Z", "+00:00")).timestamp() * 1000)
            if m.close_time else None)
        # reset book for the new window
        self.book = KalshiBook(ticker=m.ticker, strike=m.floor_strike,
                               strike_type=m.strike_type, close_ts_ms=close_ms)
        async with websockets.connect(
                self.cfg.kalshi.ws_base,
                additional_headers=self._headers(),
                ping_interval=10) as ws:
            await ws.send(json.dumps({
                "id": 1, "cmd": "subscribe",
                "params": {"channels": ["orderbook_delta"],
                           "market_tickers": [m.ticker]},
            }))
            while True:
                # roll to next window shortly after close
                if close_ms and time.time() * 1000 > close_ms + 1000:
                    return
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    continue
                msg = json.loads(raw)
                t = msg.get("type")
                if t == "orderbook_snapshot":
                    self.book.apply_snapshot(msg["msg"])
                    self.book.last_update_ms = time.time() * 1000
                elif t == "orderbook_delta":
                    self.book.apply_delta(msg["msg"])
                    self.book.last_update_ms = time.time() * 1000


def _read_key(secrets) -> bytes:
    if secrets.private_key_pem:
        return secrets.private_key_pem.encode()
    from pathlib import Path
    return Path(secrets.private_key_pem_path).expanduser().read_bytes()


if __name__ == "__main__":
    # smoke test: stream live top-of-book for a few seconds
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config.settings import load_config

    async def _demo():
        kws = KalshiWS(load_config())
        task = asyncio.create_task(kws.run())
        for _ in range(10):
            await asyncio.sleep(1)
            b = kws.book
            age = (time.time() * 1000 - b.last_update_ms) if b.last_update_ms else None
            print(f"{b.ticker} yes_bid/ask={b.yes_bid}/{b.yes_ask} "
                  f"mid={b.yes_mid} strike={b.strike} "
                  f"age={age:.0f}ms" if age else f"{b.ticker} (warming up)")
        task.cancel()

    asyncio.run(_demo())
