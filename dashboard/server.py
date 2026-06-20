"""
Dashboard backend (FastAPI). Low-latency BRTI-vs-Kalshi arbitrage terminal.

Runs every feed in-process and pushes a merged state snapshot to the browser
over a single WebSocket at ~15 Hz (no polling anywhere):

  - BRTI proxy   : data.brti_proxy websocket feeds -> consolidated VW mid
  - Kalshi       : data.kalshi_ws orderbook_delta -> live best yes bid/ask
  - fair/signal  : dashboard.fair_naive (PROXY; real model is Phase 1)

Run:
  uvicorn dashboard.server:app --reload      # or: python -m dashboard.server
Then open http://127.0.0.1:8000
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config.settings import load_config
from data import brti_proxy as bp
from data.kalshi_ws import KalshiWS
from dashboard.fair_naive import EwmaVol, fair_value

STATIC_DIR = Path(__file__).resolve().parent / "static"
PUSH_HZ = 15.0  # browser push cadence

cfg = load_config()
_kws: KalshiWS | None = None
_vol = EwmaVol(lam=cfg.vol.ewma_lambda, min_obs=cfg.vol.ewma_min_obs,
               sample_secs=cfg.vol.return_sampling_secs)
_clients: set[WebSocket] = set()


def build_state() -> dict:
    ts = int(time.time() * 1000)
    idx, fresh = bp.consolidate(max_staleness=cfg.data.brti_staleness_secs)
    spread = (max(fresh.values()) - min(fresh.values())) if len(fresh) > 1 else 0.0
    if idx is not None:
        _vol.update(idx)

    kalshi = None
    fair = signal = None
    if _kws is not None:
        b = _kws.book
        stc = ((b.close_ts_ms - ts) / 1000.0) if b.close_ts_ms else None
        kalshi = {
            "ticker": b.ticker,
            "yes_bid": b.yes_bid, "yes_ask": b.yes_ask, "yes_mid": b.yes_mid,
            "crossed": b.crossed,
            "strike": b.strike, "strike_type": b.strike_type,
            "close_ts": b.close_ts_ms, "secs_to_close": stc,
            "age_ms": int(ts - b.last_update_ms) if b.last_update_ms else None,
        }
        if idx is not None and b.strike and stc is not None:
            fair = fair_value(idx, b.strike, stc, _vol.sigma_per_sec,
                              b.strike_type or "greater_or_equal")
            if fair is not None and b.yes_mid is not None:
                signal = round(b.yes_mid - fair, 4)

    return {
        "ts": ts,
        "brti": round(idx, 2) if idx is not None else None,
        "brti_spread": round(spread, 2),
        "n_venues": len(fresh),
        "spot": {k: round(v, 2) for k, v in fresh.items()},
        "kalshi": kalshi,
        "fair": round(fair, 4) if fair is not None else None,
        "signal": signal,
        "sigma_per_sec": (round(_vol.sigma_per_sec, 8)
                          if _vol.sigma_per_sec else None),
    }


async def _broadcaster() -> None:
    period = 1.0 / PUSH_HZ
    while True:
        await asyncio.sleep(period)
        if not _clients:
            continue
        state = build_state()
        dead = []
        for ws in list(_clients):
            try:
                await ws.send_json(state)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _clients.discard(ws)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global _kws
    _kws = KalshiWS(cfg)
    tasks = [
        asyncio.create_task(bp.coinbase()),
        asyncio.create_task(bp.kraken()),
        asyncio.create_task(bp.bitstamp()),
        asyncio.create_task(bp.gemini()),
        asyncio.create_task(_kws.run()),
        asyncio.create_task(_broadcaster()),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="kalshi-brti terminal", lifespan=lifespan)


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
async def api_state():
    """One-shot snapshot (debug / health)."""
    return build_state()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # keepalive; client may send pings
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    STATIC_DIR.mkdir(exist_ok=True)
    uvicorn.run("dashboard.server:app", host="127.0.0.1", port=8000, reload=False)
