"""
Headless data collector (Phase 1A) -- the honest dataset for the kill-test.

NO dashboard, NO signals, NO trading. It just captures, losslessly and
time-aligned, everything the measurement harness (Phase 1B) needs to decide
whether any edge survives costs. Per council (docs/COUNCIL_VERDICT.md): build
this first; the dashboard is demoted.

Two tables (SQLite, crash-safe per-row commit):

  ticks        -- one row per poll: BRTI proxy (consolidated + per-venue) and
                  the canonical Kalshi `ticker` top-of-book, with REAL data age
                  and data-quality flags, on aligned UTC-ms.
  settlements  -- one row per resolved window: floor_strike (opening 60s-avg
                  BRTI), expiration_value (closing 60s-avg BRTI), result. These
                  are the FREE, exact settlement labels the whole kill-test
                  hinges on.

The opening/closing 60s-average PROXY anchors and all modeling are derived
OFFLINE in the harness from the logged `ticks` series -- the collector stays
dumb and lossless on purpose.

Usage:
  python -m data.collector                       # default sqlite in data_store/
  python -m data.collector --out data_store/run.db --duration 172800   # 48h
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config.settings import load_config              # noqa: E402
from data import brti_proxy as bp                    # noqa: E402
from data.kalshi_ws import KalshiWS                  # noqa: E402
from data.kalshi_client import KalshiClient          # noqa: E402

STALE_MS = 2000      # kalshi data older than this = unusable
LAST60_S = 60        # final-minute averaging zone = suppress

TICK_COLS = [
    "ts_utc_ms",
    "brti_proxy", "brti_spread", "n_venues",
    "cb", "kraken", "bitstamp", "gemini",
    "kalshi_ticker", "yes_bid", "yes_ask", "yes_mid", "last_price", "volume",
    "kalshi_src", "kalshi_age_ms", "crossed",
    "floor_strike", "strike_type", "open_ts_ms", "close_ts_ms", "secs_to_close",
    "flag_stale", "flag_crossed", "flag_last60", "flag_warming",
]
SETTLE_COLS = [
    "ticker", "floor_strike", "expiration_value", "result",
    "open_ts_ms", "close_ts_ms", "settled_capture_ms",
]


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_ms(iso: str | None) -> int | None:
    if not iso:
        return None
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(f"CREATE TABLE IF NOT EXISTS ticks ({','.join(TICK_COLS)})")
        self.conn.execute(f"CREATE TABLE IF NOT EXISTS settlements ({','.join(SETTLE_COLS)})")
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_ticks_ts ON ticks(ts_utc_ms)")
        self.conn.commit()
        self._ins_t = f"INSERT INTO ticks VALUES ({','.join('?'*len(TICK_COLS))})"
        self._ins_s = f"INSERT INTO settlements VALUES ({','.join('?'*len(SETTLE_COLS))})"

    def tick(self, row: dict) -> None:
        self.conn.execute(self._ins_t, [row.get(c) for c in TICK_COLS])
        self.conn.commit()

    def has_settlement(self, ticker: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM settlements WHERE ticker=? LIMIT 1", (ticker,)).fetchone() is not None

    def settlement(self, row: dict) -> None:
        self.conn.execute(self._ins_s, [row.get(c) for c in SETTLE_COLS])
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit(); self.conn.close()


def build_tick(cfg, kws: KalshiWS) -> dict | None:
    ts = now_ms()
    idx, fresh = bp.consolidate(max_staleness=cfg.data.brti_staleness_secs)
    if idx is None:
        return None
    spread = (max(fresh.values()) - min(fresh.values())) if len(fresh) > 1 else 0.0
    b = kws.book
    age = b.age_ms(ts)
    stc = ((b.close_ts_ms - ts) / 1000.0) if b.close_ts_ms else None
    return {
        "ts_utc_ms": ts,
        "brti_proxy": round(idx, 4), "brti_spread": round(spread, 4),
        "n_venues": len(fresh),
        "cb": fresh.get("Coinbase"), "kraken": fresh.get("Kraken"),
        "bitstamp": fresh.get("Bitstamp"), "gemini": fresh.get("Gemini"),
        "kalshi_ticker": b.ticker,
        "yes_bid": b.yes_bid, "yes_ask": b.yes_ask, "yes_mid": b.yes_mid,
        "last_price": b.last_price, "volume": b.volume,
        "kalshi_src": b.source, "kalshi_age_ms": round(age) if age is not None else None,
        "crossed": int(b.crossed),
        "floor_strike": b.strike, "strike_type": b.strike_type,
        "open_ts_ms": b.open_ts_ms, "close_ts_ms": b.close_ts_ms,
        "secs_to_close": round(stc, 2) if stc is not None else None,
        "flag_stale": int(age is not None and age > STALE_MS),
        "flag_crossed": int(b.crossed),
        "flag_last60": int(stc is not None and stc < LAST60_S),
        "flag_warming": int(len(fresh) < 2),
    }


async def settlement_watcher(cfg, store: Store, rest: KalshiClient):
    """When a window closes, fetch its settled record to capture the free label
    (expiration_value = closing 60s-avg BRTI, result). Retries until populated."""
    loop = asyncio.get_event_loop()
    pending: dict[str, int] = {}   # ticker -> close_ts_ms seen
    while True:
        await asyncio.sleep(20)
        t = now_ms()
        # discover recently-closed windows via the settled filter
        try:
            settled = await loop.run_in_executor(
                None, lambda: list(rest.get_markets(
                    series_ticker=cfg.kalshi.series_ticker, status="settled", limit=20)))
        except Exception as e:
            print(f"[settlement watcher] {e}", file=sys.stderr)
            continue
        for m in settled:
            if m.expiration_value is None or store.has_settlement(m.ticker):
                continue
            store.settlement({
                "ticker": m.ticker, "floor_strike": m.floor_strike,
                "expiration_value": m.expiration_value, "result": m.result,
                "open_ts_ms": iso_ms(m.open_time), "close_ts_ms": iso_ms(m.close_time),
                "settled_capture_ms": t,
            })
            print(f"[settled] {m.ticker} strike={m.floor_strike} "
                  f"exp={m.expiration_value} result={m.result}")


async def reporter(cfg, kws: KalshiWS, store: Store, duration: float | None):
    start = time.time()
    n = 0
    while True:
        await asyncio.sleep(cfg.kalshi.poll_interval_secs)
        row = build_tick(cfg, kws)
        if row is None:
            continue
        store.tick(row); n += 1
        if n % 15 == 0:  # heartbeat every ~15s, terse
            flags = "".join(k[5] for k in ("flag_stale", "flag_crossed", "flag_last60", "flag_warming")
                            if row[k])  # s/c/l/w
            print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} "
                  f"n={n} idx={row['brti_proxy']:.0f} {row['kalshi_ticker']} "
                  f"yes={row['yes_mid']} age={row['kalshi_age_ms']}ms "
                  f"t-{row['secs_to_close']}s flags=[{flags or '-'}]")
        if duration and (time.time() - start) >= duration:
            break
    print(f"\ncollected {n} ticks.")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--duration", type=float, default=None, help="seconds; default: until Ctrl-C")
    args = ap.parse_args()
    cfg = load_config()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = args.out or str(Path(cfg.data.log_dir) / f"collect_{stamp}.db")
    store = Store(out)
    kws = KalshiWS(cfg)
    rest = KalshiClient(rest_base=cfg.kalshi.rest_base, api_key_id=cfg.secrets.api_key_id,
                        private_key_pem_path=cfg.secrets.private_key_pem_path,
                        private_key_pem=cfg.secrets.private_key_pem)
    print(f"Collecting -> {out}  (headless; no signals, no trading). Ctrl-C to stop.\n")
    bg = [asyncio.create_task(c) for c in (
        bp.coinbase(), bp.kraken(), bp.bitstamp(), bp.gemini(),
        kws.run(), settlement_watcher(cfg, store, rest))]
    try:
        await reporter(cfg, kws, store, args.duration)   # controller
    finally:
        for t in bg:
            t.cancel()
        await asyncio.gather(*bg, return_exceptions=True)
        store.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped.")
