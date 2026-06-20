#!/usr/bin/env python3
"""
brti_proxy.py  --  Real-time BRTI (CME CF Bitcoin Real-Time Index) proxy.

WHAT IT IS
  Reconstructs a close approximation of Kalshi's BTC settlement index by
  streaming best bid/ask from the CF Benchmarks *constituent* spot exchanges
  and consolidating them into one volume-weighted mid, updated sub-second.

  You do NOT need TradingView. TradingView is just a chart front-end and does
  not expose BRTI (it's license-gated). This goes straight to the same venues
  CF Benchmarks aggregates, all free, all real-time.

WHAT IT IS NOT
  Not the official BRTI. CF's exact methodology (order-book depth utility
  function, outlier trimming, precise constituent weights) is proprietary.
  This is a research-grade PROXY: it tracks BRTI within a few bps in calm
  tape, and diverges more during fast moves -- which is exactly the regime
  your lag signal lives in, so treat divergence there as real, not noise.

CONSTITUENTS (verify current list at cfbenchmarks.com methodology)
  Coinbase, Kraken, Bitstamp, Gemini. (LMAX/itBit also qualify historically
  but lack free public WS.) Volume-weighting leans the index toward Coinbase/
  Kraken, which is consistent with their dominance in the real index.

USAGE
  pip install websockets
  python3 brti_proxy.py                 # live index to console
  python3 brti_proxy.py --log idx.csv   # also append timestamped rows for
                                          # syncing against Kalshi later
DEPS: websockets  (stdlib for everything else)
"""

import asyncio, json, time, argparse, sys
from datetime import datetime, timezone

try:
    import websockets
except ImportError:
    sys.exit("pip install websockets  (then re-run)")

# --- shared state: latest top-of-book per venue -----------------------------
# book[venue] = {"bid":float, "ask":float, "ts":float}
book = {}
# rough 24h-volume weights; refreshed lazily, fine as static priors for a session
WEIGHT = {"Coinbase": 3800.0, "Kraken": 900.0, "Bitstamp": 600.0, "Gemini": 50.0}

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

# --- per-exchange websocket coroutines --------------------------------------
async def coinbase():
    url = "wss://ws-feed.exchange.coinbase.com"
    sub = {"type": "subscribe", "product_ids": ["BTC-USD"],
           "channels": ["ticker"]}
    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                await ws.send(json.dumps(sub))
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("type") == "ticker" and "best_bid" in m:
                        book["Coinbase"] = {"bid": float(m["best_bid"]),
                                            "ask": float(m["best_ask"]),
                                            "ts": time.time()}
        except Exception as e:
            print(f"[coinbase reconnect] {e}", file=sys.stderr)
            await asyncio.sleep(2)

async def kraken():
    url = "wss://ws.kraken.com"
    sub = {"event": "subscribe", "pair": ["XBT/USD"], "subscription": {"name": "ticker"}}
    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                await ws.send(json.dumps(sub))
                async for raw in ws:
                    m = json.loads(raw)
                    if isinstance(m, list) and len(m) > 1 and isinstance(m[1], dict):
                        d = m[1]
                        if "b" in d and "a" in d:
                            book["Kraken"] = {"bid": float(d["b"][0]),
                                              "ask": float(d["a"][0]),
                                              "ts": time.time()}
        except Exception as e:
            print(f"[kraken reconnect] {e}", file=sys.stderr)
            await asyncio.sleep(2)

async def bitstamp():
    url = "wss://ws.bitstamp.net"
    sub = {"event": "bts:subscribe", "data": {"channel": "order_book_btcusd"}}
    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                await ws.send(json.dumps(sub))
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("event") == "data":
                        d = m["data"]
                        if d.get("bids") and d.get("asks"):
                            book["Bitstamp"] = {"bid": float(d["bids"][0][0]),
                                                "ask": float(d["asks"][0][0]),
                                                "ts": time.time()}
        except Exception as e:
            print(f"[bitstamp reconnect] {e}", file=sys.stderr)
            await asyncio.sleep(2)

async def gemini():
    url = "wss://api.gemini.com/v2/marketdata"
    sub = {"type": "subscribe", "subscriptions": [{"name": "l2", "symbols": ["BTCUSD"]}]}
    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                await ws.send(json.dumps(sub))
                bid = ask = None
                async for raw in ws:
                    m = json.loads(raw)
                    for ch in m.get("changes", []):
                        side, px, sz = ch[0], float(ch[1]), float(ch[2])
                        if sz == 0:
                            continue
                        if side == "buy":
                            bid = px if bid is None else max(bid, px)
                        elif side == "sell":
                            ask = px if ask is None else min(ask, px)
                    if bid and ask and ask > bid:
                        book["Gemini"] = {"bid": bid, "ask": ask, "ts": time.time()}
        except Exception as e:
            print(f"[gemini reconnect] {e}", file=sys.stderr)
            await asyncio.sleep(2)

# --- consolidation ----------------------------------------------------------
def consolidate(max_staleness=5.0):
    """Volume-weighted consolidated mid across fresh venues. BRTI proxy."""
    t = time.time()
    num = den = 0.0
    fresh = {}
    for v, d in book.items():
        if t - d["ts"] > max_staleness:
            continue  # drop a venue that's gone quiet -> avoids stale prints
        mid = (d["bid"] + d["ask"]) / 2
        w = WEIGHT.get(v, 100.0)
        num += mid * w
        den += w
        fresh[v] = mid
    if den == 0:
        return None, fresh
    return num / den, fresh

async def reporter(log_path=None):
    logf = open(log_path, "a") if log_path else None
    if logf and logf.tell() == 0:
        logf.write("ts_utc,index," + ",".join(WEIGHT.keys()) + ",spread\n")
    try:
        while True:
            await asyncio.sleep(1.0)  # once-per-second, matching BRTI cadence
            idx, fresh = consolidate()
            if idx is None:
                continue
            spread = (max(fresh.values()) - min(fresh.values())) if len(fresh) > 1 else 0.0
            line = " | ".join(f"{v}:{p:,.1f}" for v, p in fresh.items())
            print(f"{now_iso()}  INDEX {idx:,.2f}  (spread {spread:5.1f})  {line}")
            if logf:
                row = [now_iso(), f"{idx:.2f}"]
                row += [f"{fresh.get(v,'')}" for v in WEIGHT.keys()]
                row += [f"{spread:.2f}"]
                logf.write(",".join(row) + "\n"); logf.flush()
    finally:
        if logf:
            logf.close()

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", help="append timestamped index rows to this CSV")
    args = ap.parse_args()
    print("Connecting to constituent exchanges... (Ctrl-C to stop)\n")
    await asyncio.gather(
        coinbase(), kraken(), bitstamp(), gemini(),
        reporter(args.log),
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped.")
