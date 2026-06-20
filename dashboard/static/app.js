/*
 * BRTI vs Kalshi — simple decision view.
 *
 * Consumes the server's ~15Hz WebSocket state (see dashboard/server.py):
 *   { ts, brti, brti_spread, spot:{Coinbase,Kraken,...},
 *     kalshi:{ yes_bid, yes_ask, yes_mid, crossed, strike, secs_to_close, age_ms },
 *     fair, signal, ... }
 * fair/signal are a Phase-0 PROXY (not the real model, not yet backtested).
 *
 * Shows ONE plain-English verdict (the discrepancy + whether to get in) and
 * TWO separated charts: Bitcoin/BRTI ($) and Kalshi odds (%).
 */

const EDGE_C = 5;      // cents of discrepancy to call it actionable
const MIN_SECS = 60;   // under this, settlement averaging makes entries unreliable
const WINDOW_S = 180;  // seconds of history kept visible

const $ = (id) => document.getElementById(id);
const cents = (p) => (p == null ? null : p * 100);
const fmtC = (p) => (p == null ? "—" : (p * 100).toFixed(1) + "¢");
const fmtUSD = (v) => (v == null ? "—" : "$" + v.toLocaleString(undefined, { maximumFractionDigits: 0 }));

/* ---------------- charts ---------------- */
const LWC = window.LightweightCharts;
const baseOpts = {
  layout: { background: { color: "transparent" }, textColor: "#8a95a3", fontSize: 11 },
  grid: { vertLines: { color: "#1c222a" }, horzLines: { color: "#1c222a" } },
  rightPriceScale: { borderColor: "#262d36" },
  timeScale: { borderColor: "#262d36", timeVisible: true, secondsVisible: true },
  crosshair: { mode: 0 },
};

const priceChart = LWC.createChart($("priceChart"), baseOpts);
const brtiS = priceChart.addLineSeries({ color: "#f5f7fa", lineWidth: 2, priceFormat: { type: "price", precision: 0, minMove: 1 } });
const cbS = priceChart.addLineSeries({ color: "#3b82f6", lineWidth: 1 });
const krS = priceChart.addLineSeries({ color: "#a855f7", lineWidth: 1 });
let strikeLine = null;

const oddsChart = LWC.createChart($("oddsChart"), { ...baseOpts,
  rightPriceScale: { borderColor: "#262d36", scaleMargins: { top: 0.1, bottom: 0.1 } } });
const yesS = oddsChart.addLineSeries({ color: "#16c784", lineWidth: 2, priceFormat: { type: "price", precision: 1, minMove: 0.1 } });
const fairS = oddsChart.addLineSeries({ color: "#f0a020", lineWidth: 1, lineStyle: 2 });

const lastT = {};                 // series key -> last time, to keep time strictly ascending
function push(series, key, time, value) {
  if (value == null || !isFinite(value)) return;
  let t = time;
  if (lastT[key] != null && t <= lastT[key]) t = lastT[key] + 0.001;
  lastT[key] = t;
  series.update({ time: t, value });
}

let follow = true;
function follevel() {
  if (!follow) return;
  const now = Date.now() / 1000;
  const from = now - WINDOW_S;
  try { priceChart.timeScale().setVisibleRange({ from, to: now }); } catch (e) {}
  try { oddsChart.timeScale().setVisibleRange({ from, to: now }); } catch (e) {}
}

function resize() {
  for (const [el, ch] of [[$("priceChart"), priceChart], [$("oddsChart"), oddsChart]]) {
    ch.resize(el.clientWidth, el.clientHeight);
  }
}
new ResizeObserver(resize).observe($("priceChart"));
new ResizeObserver(resize).observe($("oddsChart"));
window.addEventListener("resize", resize);

/* ---------------- verdict ---------------- */
function decide(s) {
  const k = s.kalshi;
  if (!k || k.ticker == null) return { cls: "wait", v: "NO OPEN MARKET", r: "No 15-minute window is trading right now." };
  const stc = k.secs_to_close;
  if (s.fair == null || s.signal == null)
    return { cls: "wait", v: "WARMING UP…", r: "Estimating short-term volatility — fair value is ready ~30s after launch." };

  const edge = s.signal * 100;                 // discrepancy in cents (yes − fair)
  const yesc = fmtC(k.yes_mid), fairc = fmtC(s.fair);
  const crossNote = k.crossed ? " (book is crossed/thin — treat the spread with caution)" : "";

  if (stc != null && stc < MIN_SECS)
    return { cls: "late", v: "TOO LATE — SETTLING", r: `Only ${Math.round(stc)}s left. The final 60s settlement average means late moves may not stick. Sit out.` };

  if (edge >= EDGE_C)
    return { cls: "sell", v: "KALSHI RICH → BUY NO", r: `Kalshi YES ${yesc} vs fair ${fairc} — about ${edge.toFixed(1)}¢ too expensive. The model says YES is overpriced: buy NO / sell YES.${crossNote}` };
  if (edge <= -EDGE_C)
    return { cls: "buy", v: "KALSHI CHEAP → BUY YES", r: `Kalshi YES ${yesc} vs fair ${fairc} — about ${(-edge).toFixed(1)}¢ too cheap. The model says YES is underpriced: buy YES.${crossNote}` };

  return { cls: "wait", v: "WAIT — NO CLEAR EDGE", r: `Kalshi YES ${yesc} ≈ fair ${fairc}. Only ${edge.toFixed(1)}¢ gap, under the ${EDGE_C}¢ bar. Nothing to do.${crossNote}` };
}

/* ---------------- render ---------------- */
function render(s) {
  const k = s.kalshi || {};
  $("market").textContent = k.ticker || "—";

  // clock
  const stc = k.secs_to_close;
  const clk = $("clock");
  if (stc != null && stc >= 0) {
    const m = Math.floor(stc / 60), sec = Math.floor(stc % 60);
    clk.textContent = `${m}:${String(sec).padStart(2, "0")}`;
    clk.classList.toggle("urgent", stc < 60);
  } else { clk.textContent = "--:--"; }

  // latency
  const age = k.age_ms;
  const lat = $("latency");
  lat.textContent = age == null ? "— ms" : `${age} ms`;
  lat.style.color = age == null ? "#8a95a3" : age < 100 ? "#16c784" : age < 500 ? "#f0a020" : "#ea3943";

  // verdict
  const d = decide(s);
  $("card").className = "verdict-card " + d.cls;
  $("verdict").textContent = d.v;
  $("reason").textContent = d.r;

  // chips
  $("yes").textContent = fmtC(k.yes_mid);
  $("fair").textContent = s.fair == null ? "warming up" : fmtC(s.fair);
  const edgeEl = $("edge");
  if (s.signal == null) { edgeEl.textContent = "—"; edgeEl.className = ""; }
  else {
    const e = s.signal * 100;
    edgeEl.textContent = (e >= 0 ? "+" : "") + e.toFixed(1) + "¢ " + (e >= 0 ? "rich" : "cheap");
    edgeEl.className = e >= 0 ? "rich" : "cheap";
  }
  // spot lead vs BRTI (which way price is being pulled)
  const spots = s.spot || {};
  const leadVals = [spots.Coinbase, spots.Kraken].filter((x) => x != null);
  if (leadVals.length && s.brti != null) {
    const lead = leadVals.reduce((a, b) => a + b, 0) / leadVals.length - s.brti;
    $("lead").textContent = (lead >= 0 ? "+" : "") + "$" + lead.toFixed(0) + (Math.abs(lead) >= 1 ? (lead > 0 ? " ↑" : " ↓") : " ·");
  } else { $("lead").textContent = "—"; }

  // charts
  const t = s.ts / 1000;
  push(brtiS, "brti", t, s.brti);
  push(cbS, "cb", t, spots.Coinbase);
  push(krS, "kr", t, spots.Kraken);
  if (k.strike != null) {
    if (!strikeLine || strikeLine._v !== k.strike) {
      if (strikeLine) brtiS.removePriceLine(strikeLine);
      strikeLine = brtiS.createPriceLine({ price: k.strike, color: "#f0a020", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "strike" });
      strikeLine._v = k.strike;
    }
  }
  push(yesS, "yes", t, cents(k.yes_mid));
  push(fairS, "fair", t, cents(s.fair));

  $("brtiNow").textContent = fmtUSD(s.brti);
  $("oddsNow").textContent = fmtC(k.yes_mid);

  follevel();
}

/* ---------------- websocket ---------------- */
let ws, backoff = 500;
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => { backoff = 500; $("dot").classList.add("ok"); };
  ws.onclose = () => {
    $("dot").classList.remove("ok");
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 8000);
  };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
  ws.onmessage = (ev) => {
    let s; try { s = JSON.parse(ev.data); } catch (e) { return; }
    render(s);
  };
}
connect();
resize();
