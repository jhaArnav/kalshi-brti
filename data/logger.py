"""
Time-synced research logger (Phase 0 core deliverable).

Runs the BRTI-proxy websocket feeds in-process and, once per
`kalshi.poll_interval_secs`, samples three things at one aligned UTC-ms
timestamp and writes a single row:

  1. BRTI proxy   : volume-weighted consolidated mid + per-venue mids + spread
  2. Spot leaders : Coinbase & Kraken raw mids (from the same proxy book --
                    no duplicate sockets; leader chosen empirically later)
  3. Kalshi       : top of book + last + floor_strike + (post-close)
                    expiration_value for the single open KXBTC15M market

This aligned dataset is the whole foundation. Timestamp alignment is the
priority: BRTI and spot are sampled from the in-memory book at the same wall
clock instant the Kalshi snapshot is taken, and every source carries its own
capture-age so staleness is auditable downstream.

Backends: SQLite (crash-safe incremental append) or Parquet (batched).
A run_manifest.json records params + git SHA + the config for reproducibility.

Usage:
  python -m data.logger                      # sqlite/parquet per config
  python -m data.logger --format sqlite --out data_store/session.db
  python -m data.logger --duration 900       # run 15 min then stop
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config.settings import load_config, Config  # noqa: E402
from data import brti_proxy as bp  # reuse its book + feed coroutines  # noqa: E402
from data.kalshi_client import KalshiClient, MarketSnapshot  # noqa: E402

# Column order for the synced dataset. Keep stable -- analysis depends on it.
COLUMNS = [
    "ts_utc_ms",           # capture instant (join key)
    "brti_proxy",          # consolidated VW mid
    "brti_spread",         # max-min venue mid (proxy uncertainty)
    "brti_n_venues",       # how many venues were fresh
    "cb", "kraken", "bitstamp", "gemini",   # per-venue mids
    "spot_leader_cb",      # Coinbase mid as leader candidate
    "spot_leader_kraken",  # Kraken mid as leader candidate
    "kalshi_ticker",
    "kalshi_yes_bid", "kalshi_yes_ask",     # dollars [0,1]
    "kalshi_no_bid", "kalshi_no_ask",
    "kalshi_last",
    "kalshi_floor_strike",
    "kalshi_strike_type",
    "kalshi_expiration_value",   # null until settled
    "kalshi_close_ts_ms",
    "secs_to_close",
    "kalshi_age_ms",       # age of the Kalshi snapshot at sample time
]


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_to_ms(iso: str | None) -> int | None:
    if not iso:
        return None
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


# --- storage backends -------------------------------------------------------
class SqliteWriter:
    """Crash-safe: every row committed immediately."""
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        cols = ", ".join(f'"{c}"' for c in COLUMNS)
        self.conn.execute(f"CREATE TABLE IF NOT EXISTS ticks ({cols})")
        self.conn.commit()
        self._ins = f"INSERT INTO ticks VALUES ({','.join('?' * len(COLUMNS))})"

    def write(self, row: dict[str, Any]) -> None:
        self.conn.execute(self._ins, [row.get(c) for c in COLUMNS])
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


class ParquetWriter:
    """Batched: buffers rows, flushes a row group on interval / close.

    Parquet can't append a single row efficiently, so we accumulate and write
    one file per session. Flush cadence bounds data loss on crash.
    """
    def __init__(self, path: str, flush_every: int = 30):
        import pyarrow as pa  # noqa
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.flush_every = flush_every
        self._buf: list[dict[str, Any]] = []
        self._pa = pa

    def write(self, row: dict[str, Any]) -> None:
        self._buf.append(row)
        if len(self._buf) >= self.flush_every:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        import pyarrow.parquet as pq
        # Rewrite the whole file (sessions are short -> 15m markets). For long
        # runs, partition by date upstream; kept simple and correct here.
        existing = []
        if Path(self.path).exists():
            existing = pq.read_table(self.path).to_pylist()
        table = self._pa.Table.from_pylist(existing + self._buf,
                                           schema=self._schema())
        pq.write_table(table, self.path)
        self._buf.clear()

    def _schema(self):
        pa = self._pa
        f = {
            "ts_utc_ms": pa.int64(), "kalshi_close_ts_ms": pa.int64(),
            "brti_n_venues": pa.int64(), "kalshi_age_ms": pa.int64(),
            "kalshi_ticker": pa.string(), "kalshi_strike_type": pa.string(),
        }
        return pa.schema([(c, f.get(c, pa.float64())) for c in COLUMNS])

    def close(self) -> None:
        self.flush()


# --- Kalshi poller (separate cadence; market data is public) ----------------
class KalshiPoller:
    """Polls the single open KXBTC15M market in a background thread-free async
    loop. Holds the latest snapshot + the wall time it was captured so the
    sampler can record its age."""
    def __init__(self, client: KalshiClient, series: str, interval: float):
        self.client = client
        self.series = series
        self.interval = interval
        self.snap: MarketSnapshot | None = None
        self.snap_ts_ms: int | None = None

    async def run(self) -> None:
        loop = asyncio.get_event_loop()
        while True:
            try:
                # requests is blocking -> run in executor to not stall feeds
                mkts = await loop.run_in_executor(
                    None, self.client.active_btc15m_markets, self.series)
                if mkts:
                    # pick the soonest-closing open market (the live window)
                    self.snap = min(mkts, key=lambda m: m.close_time or "")
                    self.snap_ts_ms = now_ms()
            except Exception as e:
                print(f"[kalshi poll] {e}", file=sys.stderr)
            await asyncio.sleep(self.interval)


# --- sampler ----------------------------------------------------------------
def build_row(cfg: Config, poller: KalshiPoller) -> dict[str, Any] | None:
    ts = now_ms()
    idx, fresh = bp.consolidate(max_staleness=cfg.data.brti_staleness_secs)
    if idx is None:
        return None  # no fresh venue yet
    spread = (max(fresh.values()) - min(fresh.values())) if len(fresh) > 1 else 0.0
    m = poller.snap
    close_ms = iso_to_ms(m.close_time) if m else None
    row: dict[str, Any] = {
        "ts_utc_ms": ts,
        "brti_proxy": round(idx, 4),
        "brti_spread": round(spread, 4),
        "brti_n_venues": len(fresh),
        "cb": fresh.get("Coinbase"),
        "kraken": fresh.get("Kraken"),
        "bitstamp": fresh.get("Bitstamp"),
        "gemini": fresh.get("Gemini"),
        "spot_leader_cb": fresh.get("Coinbase"),
        "spot_leader_kraken": fresh.get("Kraken"),
        "kalshi_ticker": m.ticker if m else None,
        "kalshi_yes_bid": m.yes_bid if m else None,
        "kalshi_yes_ask": m.yes_ask if m else None,
        "kalshi_no_bid": m.no_bid if m else None,
        "kalshi_no_ask": m.no_ask if m else None,
        "kalshi_last": m.last_price if m else None,
        "kalshi_floor_strike": m.floor_strike if m else None,
        "kalshi_strike_type": m.strike_type if m else None,
        "kalshi_expiration_value": m.expiration_value if m else None,
        "kalshi_close_ts_ms": close_ms,
        "secs_to_close": (close_ms - ts) / 1000.0 if close_ms else None,
        "kalshi_age_ms": (ts - poller.snap_ts_ms) if poller.snap_ts_ms else None,
    }
    return row


def git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def write_manifest(out_dir: Path, cfg: Config, out_path: str, fmt: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "output": out_path,
        "format": fmt,
        "series_ticker": cfg.kalshi.series_ticker,
        "poll_interval_secs": cfg.kalshi.poll_interval_secs,
        "brti_staleness_secs": cfg.data.brti_staleness_secs,
        "spot_venues": cfg.data.spot_venues,
        "columns": COLUMNS,
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2))


async def reporter(cfg: Config, poller: KalshiPoller, writer, duration: float | None):
    start = time.time()
    n = 0
    while True:
        await asyncio.sleep(cfg.kalshi.poll_interval_secs)
        row = build_row(cfg, poller)
        if row is None:
            continue
        writer.write(row)
        n += 1
        k = row["kalshi_ticker"] or "(no open mkt)"
        idxv, yb, ya = row["brti_proxy"], row["kalshi_yes_bid"], row["kalshi_yes_ask"]
        stc = row["secs_to_close"]
        print(f"{datetime.now(timezone.utc).isoformat(timespec='milliseconds')}  "
              f"idx={idxv:,.1f} spr={row['brti_spread']:.1f} | {k} "
              f"yes={yb}/{ya} strike={row['kalshi_floor_strike']} "
              f"t-{stc:.0f}s" if stc is not None else
              f"... idx={idxv:,.1f} | {k}")
        if duration and (time.time() - start) >= duration:
            break
    writer.close()
    print(f"\nlogged {n} rows.")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", choices=["sqlite", "parquet"], default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--duration", type=float, default=None,
                    help="seconds to run then stop (default: run until Ctrl-C)")
    args = ap.parse_args()

    cfg = load_config()
    fmt = args.format or cfg.data.log_format
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    default_name = f"kbrti_{stamp}.{'db' if fmt == 'sqlite' else 'parquet'}"
    out_path = args.out or str(Path(cfg.data.log_dir) / default_name)

    writer = SqliteWriter(out_path) if fmt == "sqlite" else ParquetWriter(out_path)
    write_manifest(Path(cfg.data.log_dir), cfg, out_path, fmt)

    client = KalshiClient(
        rest_base=cfg.kalshi.rest_base,
        api_key_id=cfg.secrets.api_key_id,
        private_key_pem_path=cfg.secrets.private_key_pem_path,
        private_key_pem=cfg.secrets.private_key_pem,
    )
    poller = KalshiPoller(client, cfg.kalshi.series_ticker, cfg.kalshi.poll_interval_secs)

    print(f"Logging -> {out_path} ({fmt}). Series {cfg.kalshi.series_ticker}. "
          f"Ctrl-C to stop.\n")
    await asyncio.gather(
        bp.coinbase(), bp.kraken(), bp.bitstamp(), bp.gemini(),
        poller.run(),
        reporter(cfg, poller, writer, args.duration),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped.")
