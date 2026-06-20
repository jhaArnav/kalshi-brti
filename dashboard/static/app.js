/* =============================================================================
 * BRTI / Kalshi Arbitrage Research Terminal — Phase-0 frontend
 *
 * Single WebSocket at ws://${location.host}/ws pushes JSON state ~15x/sec.
 * Message shape (fields may be null while warming up; `kalshi` may be null):
 *
 * {
 *   ts: 1781992155100,        // server time, UNIX MILLISECONDS
 *   brti: 63955.96,           // consolidated BRTI proxy index, USD (nullable)
 *   brti_spread: 9.51,        // cross-venue dispersion, USD
 *   n_venues: 4,
 *   spot: { Coinbase: 63954.73, Kraken: 63959.45, Bitstamp: ..., Gemini: ... },
 *   kalshi: {
 *     ticker, yes_bid, yes_ask, yes_mid,   // PROBABILITY dollars 0..1
 *     crossed,                              // true => spread unreliable / thin book
 *     strike, strike_type,
 *     close_ts, secs_to_close, age_ms      // age_ms = Kalshi data freshness
 *   } | null,
 *   fair: null,               // naive fair value 0..1  (PHASE-0 PROXY)
 *   signal: null,             // yes_mid - fair (the tradeable gap) (PHASE-0 PROXY)
 *   sigma_per_sec: null
 * }
 *
 * NOTE: `fair` and `signal` are a Phase-0 PROXY / placeholder, NOT the real
 * pricing model. They are flagged with a "PROXY" badge in the UI.
 *
 * Time handling: lightweight-charts wants strictly ascending time in SECONDS.
 * We use time = ts/1000 (fractional seconds OK). Per-series we track lastTime
 * and nudge by +0.001 if a new point would not advance, so update() never
 * throws on non-ascending time.
 * ========================================================================== */

(function () {
  "use strict";

  const LWC = window.LightweightCharts;
  if (!LWC) { console.error("LightweightCharts failed to load from CDN"); return; }

  // ------------------------------------------------------------------ config
  const MAX_POINTS = 5000;          // hard cap retained per series
  const DEFAULT_WINDOW = 60;        // default visible window seconds (1m)

  const COLORS = {
    bg: "#0b0e11", grid: "#1b212c", text: "#7c8595", border: "#232a36",
    brti: "#f7931a", cb: "#3b82f6", kr: "#a78bfa",
    mid: "#29b6f6", fair: "#ffb74d",
    sigUp: "#26a69a", sigDn: "#ef5350",
    strike: "#e0e0e0", zero: "#5a6472",
  };

  // ------------------------------------------------------------------ charts
  const chartCommon = {
    layout: { background: { color: COLORS.bg }, textColor: COLORS.text, fontSize: 11,
              fontFamily: 'SF Mono, Roboto Mono, Menlo, monospace' },
    grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
    rightPriceScale: { borderColor: COLORS.border },
    timeScale: { borderColor: COLORS.border, timeVisible: true, secondsVisible: true,
                 rightOffset: 4 },
    crosshair: { mode: LWC.CrosshairMode.Normal },
    handleScale: true, handleScroll: true,
  };

  const elPrice = document.getElementById("chart-price");
  const elProb  = document.getElementById("chart-prob");

  const priceChart = LWC.createChart(elPrice, {
    ...chartCommon,
    rightPriceScale: { ...chartCommon.rightPriceScale, scaleMargins: { top: 0.08, bottom: 0.08 } },
  });
  const probChart = LWC.createChart(elProb, {
    ...chartCommon,
    rightPriceScale: { ...chartCommon.rightPriceScale, scaleMargins: { top: 0.12, bottom: 0.12 } },
  });

  // ---- Price pane series
  const sBrti = priceChart.addLineSeries({ color: COLORS.brti, lineWidth: 2,
    priceLineVisible: false, lastValueVisible: true, title: "BRTI" });
  const sCb = priceChart.addLineSeries({ color: COLORS.cb, lineWidth: 1,
    priceLineVisible: false, lastValueVisible: false });
  const sKr = priceChart.addLineSeries({ color: COLORS.kr, lineWidth: 1,
    priceLineVisible: false, lastValueVisible: false });

  // ---- Prob pane series (0..100)
  const sMid = probChart.addLineSeries({ color: COLORS.mid, lineWidth: 2,
    priceLineVisible: false, lastValueVisible: true, title: "mid",
    priceFormat: { type: "price", precision: 1, minMove: 0.1 } });
  const sFair = probChart.addLineSeries({ color: COLORS.fair, lineWidth: 1, lineStyle: 2,
    priceLineVisible: false, lastValueVisible: false,
    priceFormat: { type: "price", precision: 1, minMove: 0.1 } });
  // Signal as a baseline-style area around 0, on a separate overlay scale so it
  // emphasises deviation without distorting the 0..100 axis.
  const sSig = probChart.addAreaSeries({
    priceScaleId: "sig",
    lineColor: COLORS.sigUp, topColor: "rgba(38,166,154,0.35)", bottomColor: "rgba(239,83,80,0.35)",
    lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    priceFormat: { type: "price", precision: 1, minMove: 0.1 },
  });
  probChart.priceScale("sig").applyOptions({
    scaleMargins: { top: 0.05, bottom: 0.05 }, visible: false,
  });
  // Zero baseline reference for the signal.
  sSig.createPriceLine({ price: 0, color: COLORS.zero, lineWidth: 1, lineStyle: 2,
    axisLabelVisible: false, title: "0" });

  // ------------------------------------------------------------- time guards
  const lastT = new WeakMap();      // series -> last time used
  function nextTime(series, t) {
    const prev = lastT.get(series);
    if (prev !== undefined && t <= prev) t = prev + 0.001;
    lastT.set(series, t);
    return t;
  }
  function push(series, t, value) {
    if (value === null || value === undefined || !isFinite(value)) return;
    const tt = nextTime(series, t);
    series.update({ time: tt, value });
    const buf = buffers.get(series);
    buf.push({ time: tt, value });
    if (buf.length > MAX_POINTS) {              // trim occasionally via setData
      const trimmed = buf.slice(buf.length - Math.floor(MAX_POINTS * 0.8));
      buffers.set(series, trimmed);
      series.setData(trimmed);
    }
  }
  const buffers = new Map();
  [sBrti, sCb, sKr, sMid, sFair, sSig].forEach(s => buffers.set(s, []));

  // ------------------------------------------------------- time-axis linking
  // Mirror logical range between the two charts so pan/zoom stays in sync.
  let syncing = false;
  function link(srcChart, dstChart) {
    srcChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (syncing || !range) return;
      syncing = true;
      try { dstChart.timeScale().setVisibleLogicalRange(range); } catch (e) {}
      syncing = false;
    });
  }
  link(priceChart, probChart);
  link(probChart, priceChart);

  // ----------------------------------------------------- window / follow ctl
  let follow = true;
  let windowSecs = DEFAULT_WINDOW;
  let latestT = null;

  function applyWindow() {
    if (latestT === null) return;
    if (windowSecs === 0) {                      // Full
      priceChart.timeScale().fitContent();
      return;                                    // linkage mirrors to prob
    }
    const from = latestT - windowSecs;
    const to = latestT + 2;                       // small headroom on the right
    try { priceChart.timeScale().setVisibleRange({ from, to }); } catch (e) {}
  }

  document.querySelectorAll("[data-win]").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-win]").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      windowSecs = parseInt(btn.dataset.win, 10);
      applyWindow();
    });
  });
  // mark default window button active
  document.querySelector('[data-win="60"]').classList.add("active");

  const followBtn = document.getElementById("btn-follow");
  followBtn.addEventListener("click", () => {
    follow = !follow;
    followBtn.classList.toggle("active", follow);
    followBtn.textContent = follow ? "Auto-follow ●" : "Auto-follow ○";
    if (follow) applyWindow();
  });

  // --------------------------------------------------------------- drawings
  const drawings = { priceLines: [], priceMarkers: [], probMarkers: [] };
  let armed = null;   // "hline" | "note" | null
  const btnHline = document.getElementById("btn-hline");
  const btnNote = document.getElementById("btn-note");

  function disarm() {
    armed = null;
    btnHline.classList.remove("armed");
    btnNote.classList.remove("armed");
  }
  btnHline.addEventListener("click", () => {
    const was = armed === "hline"; disarm();
    if (!was) { armed = "hline"; btnHline.classList.add("armed"); }
  });
  btnNote.addEventListener("click", () => {
    const was = armed === "note"; disarm();
    if (!was) { armed = "note"; btnNote.classList.add("armed"); }
  });

  // Click on price pane: place H-line at clicked price (or note marker).
  priceChart.subscribeClick(param => {
    if (!armed) return;
    if (armed === "hline") {
      const price = param.seriesData && param.seriesData.get(sBrti) !== undefined
        ? param.seriesData.get(sBrti).value
        : (param.point ? sBrti.coordinateToPrice(param.point.y) : null);
      if (price == null) return;
      const pl = sBrti.createPriceLine({
        price, color: COLORS.yellow || "#f5c542", lineWidth: 1, lineStyle: 0,
        axisLabelVisible: true, title: price.toFixed(2),
      });
      drawings.priceLines.push(pl);
      disarm();
    } else if (armed === "note") {
      const txt = window.prompt("Note text:");
      if (txt && param.time !== undefined) {
        drawings.priceMarkers.push({ time: param.time, position: "aboveBar",
          color: "#f5c542", shape: "circle", text: txt });
        sBrti.setMarkers(drawings.priceMarkers);
      }
      disarm();
    }
  });

  // Vertical time mark on both panes at latest time.
  document.getElementById("btn-mark").addEventListener("click", () => {
    if (latestT === null) return;
    const label = "M" + (drawings.priceMarkers.length + 1);
    drawings.priceMarkers.push({ time: latestT, position: "belowBar",
      color: "#9aa4b2", shape: "arrowUp", text: label });
    drawings.probMarkers.push({ time: latestT, position: "belowBar",
      color: "#9aa4b2", shape: "arrowUp", text: label });
    sBrti.setMarkers(drawings.priceMarkers);
    sMid.setMarkers(drawings.probMarkers);
  });

  document.getElementById("btn-clear").addEventListener("click", () => {
    drawings.priceLines.forEach(pl => { try { sBrti.removePriceLine(pl); } catch (e) {} });
    drawings.priceLines = [];
    drawings.priceMarkers = []; drawings.probMarkers = [];
    sBrti.setMarkers([]); sMid.setMarkers([]);
    disarm();
  });

  // ------------------------------------------------------------ status bar
  const $ = id => document.getElementById(id);
  const fmtUSD = v => (v == null ? "—" : "$" + v.toLocaleString("en-US",
    { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const fmtPct = v => (v == null ? "—" : (v * 100).toFixed(1) + "¢");

  let strikeLine = null;           // price line on price pane
  let lastStrike = null;
  const prevSpot = {};             // venue -> previous value for up/down tick
  const venueEls = {};             // venue -> {val, tick} element refs

  function ensureVenueTile(name) {
    if (venueEls[name]) return venueEls[name];
    const wrap = document.createElement("div");
    wrap.className = "venue";
    wrap.innerHTML = `<div class="label">${name}</div>` +
      `<div class="val mono"><span class="num">—</span><span class="tick"></span></div>`;
    $("s-venues").appendChild(wrap);
    venueEls[name] = { num: wrap.querySelector(".num"), tick: wrap.querySelector(".tick") };
    return venueEls[name];
  }

  function setLatency(age) {
    const el = $("s-age");
    if (age == null) { el.textContent = "—"; el.className = "val mono"; return; }
    el.textContent = age.toFixed(0) + " ms";
    el.className = "val mono " + (age < 100 ? "lat-green" : age < 500 ? "lat-yellow" : "lat-red");
  }

  function setCountdown(secs) {
    const box = $("s-countdown-box");
    const el = $("s-countdown");
    if (secs == null) { el.textContent = "--:--"; box.classList.remove("urgent"); return; }
    const s = Math.max(0, Math.floor(secs));
    const mm = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    el.textContent = `${mm}:${ss}`;
    box.classList.toggle("urgent", s < 60);
  }

  function updateStatus(msg) {
    $("s-brti").textContent = fmtUSD(msg.brti);
    $("s-spread").textContent = msg.brti_spread == null ? "—" : "$" + msg.brti_spread.toFixed(2);

    // venues with up/down ticks
    if (msg.spot) {
      for (const name in msg.spot) {
        const v = msg.spot[name];
        const refs = ensureVenueTile(name);
        refs.num.textContent = v == null ? "—" : v.toLocaleString("en-US",
          { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        const prev = prevSpot[name];
        if (prev != null && v != null) {
          if (v > prev) { refs.tick.textContent = "▲"; refs.tick.className = "tick up"; }
          else if (v < prev) { refs.tick.textContent = "▼"; refs.tick.className = "tick down"; }
          else { refs.tick.textContent = "•"; refs.tick.className = "tick flat"; }
        }
        prevSpot[name] = v;
      }
    }

    const k = msg.kalshi;
    if (k) {
      $("s-ticker").textContent = k.ticker || "—";
      $("s-strike").textContent = fmtUSD(k.strike);
      $("s-bid").textContent = fmtPct(k.yes_bid);
      $("s-ask").textContent = fmtPct(k.yes_ask);
      $("s-mid").textContent = fmtPct(k.yes_mid);
      setCountdown(k.secs_to_close);
      setLatency(k.age_ms);
    } else {
      setCountdown(null); setLatency(null);
    }

    // fair / signal (PROXY)
    if (msg.fair == null) {
      $("s-fair").textContent = "warming up";
      $("s-fair").className = "val mono flat";
    } else {
      $("s-fair").textContent = fmtPct(msg.fair);
      $("s-fair").className = "val mono";
    }
    if (msg.signal == null) {
      $("s-signal").textContent = "warming up";
      $("s-signal").className = "val mono flat";
    } else {
      const sigPts = msg.signal * 100;            // in cents/pct points
      const sign = sigPts >= 0 ? "+" : "";
      $("s-signal").textContent = sign + sigPts.toFixed(1) + "¢";
      $("s-signal").className = "val mono " + (sigPts >= 0 ? "pos" : "neg");
    }
  }

  // -------------------------------------------------------------- ingest msg
  function onMessage(msg) {
    const t = msg.ts / 1000;                       // seconds (fractional ok)
    latestT = t;

    // Price pane
    push(sBrti, t, msg.brti);
    if (msg.spot) {
      push(sCb, t, msg.spot.Coinbase);
      push(sKr, t, msg.spot.Kraken);
    }

    const k = msg.kalshi;
    if (k) {
      // strike price line (recreate if it moved)
      if (k.strike != null && k.strike !== lastStrike) {
        if (strikeLine) { try { sBrti.removePriceLine(strikeLine); } catch (e) {} }
        strikeLine = sBrti.createPriceLine({
          price: k.strike, color: COLORS.strike, lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: "strike",
        });
        lastStrike = k.strike;
      }
      // prob pane: YES mid in 0..100
      if (k.yes_mid != null) push(sMid, t, k.yes_mid * 100);
    }

    if (msg.fair != null) push(sFair, t, msg.fair * 100);
    if (msg.signal != null) push(sSig, t, msg.signal * 100);

    updateStatus(msg);

    if (follow) applyWindow();
  }

  // ------------------------------------------------------------- websocket
  const dot = $("conn-dot");
  let ws = null, backoff = 500;

  function connect() {
    const url = `ws://${location.host}/ws`;
    try { ws = new WebSocket(url); } catch (e) { scheduleReconnect(); return; }

    ws.onopen = () => { dot.classList.add("ok"); backoff = 500; };
    ws.onmessage = ev => {
      let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
      try { onMessage(msg); } catch (e) { console.error("onMessage", e); }
    };
    ws.onclose = () => { dot.classList.remove("ok"); scheduleReconnect(); };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }
  function scheduleReconnect() {
    dot.classList.remove("ok");
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 8000);        // exponential backoff, cap 8s
  }
  connect();

  // -------------------------------------------------------------- resizing
  function resizeChart(chart, el) {
    chart.resize(el.clientWidth, el.clientHeight);
  }
  const ro = new ResizeObserver(() => {
    resizeChart(priceChart, elPrice);
    resizeChart(probChart, elProb);
  });
  ro.observe(elPrice);
  ro.observe(elProb);
  window.addEventListener("resize", () => {
    resizeChart(priceChart, elPrice);
    resizeChart(probChart, elProb);
  });
  // initial size
  resizeChart(priceChart, elPrice);
  resizeChart(probChart, elProb);
})();
