// traffic_chart.js — Per-domain DDS traffic time-series sparklines.
// Subscribes to "traffic_stats" WebSocket messages from mesh_bridge.py (which reads
// DomainTrafficStats published by domain_traffic_monitor.py). Renders small canvas
// sparkline plots on the right panel, one card per domain ID, each with two subplots:
// discovery bytes/s (green) and user-data bytes/s (blue).

(function () {
  "use strict";

  const MAX_POINTS = 300;  // rolling window (at 10 Hz = 30 seconds)
  const EMA_ALPHA = 0.1;   // exponential moving average smoothing (lower = smoother)
  const DISCOVERY_COLOR = "#3aa655";
  const DATA_COLOR = "#3a7bd9";
  const GRID_COLOR = "#232a34";
  const TEXT_COLOR = "#8a94a6";

  const panel = document.getElementById("traffic-panel");
  if (!panel) return;

  // domain_id -> { discovery: [{t, bytes}], data: [{t, bytes}], total: [{t, bytes}],
  //                el, discCanvas, dataCanvas, discLabel, dataLabel, rateEl, lastSample }
  const domains = new Map();

  function ensureDomain(domainId) {
    if (domains.has(domainId)) return domains.get(domainId);

    // Build header on first domain
    if (domains.size === 0) {
      const hdr = document.createElement("div");
      hdr.className = "tp-header";
      hdr.innerHTML = `<span>Domain Traffic</span>`;
      panel.appendChild(hdr);
    }

    const el = document.createElement("div");
    el.className = "tp-domain";

    const titleDiv = document.createElement("div");
    titleDiv.className = "tp-domain-title";
    const rateSpan = document.createElement("span");
    rateSpan.className = "tp-rate";
    rateSpan.textContent = "0 B/s";
    titleDiv.innerHTML = `<span>Domain ${domainId}</span>`;
    titleDiv.appendChild(rateSpan);
    el.appendChild(titleDiv);

    // Discovery subplot
    const discSubplot = document.createElement("div");
    discSubplot.className = "tp-subplot";
    const discLabelDiv = document.createElement("div");
    discLabelDiv.className = "tp-subplot-label";
    discLabelDiv.innerHTML = `<span style="color:${DISCOVERY_COLOR}">discovery</span><span>0 B/s</span>`;
    discSubplot.appendChild(discLabelDiv);
    const discCanvas = document.createElement("canvas");
    discCanvas.height = 40;
    discSubplot.appendChild(discCanvas);
    el.appendChild(discSubplot);

    // User-data subplot
    const dataSubplot = document.createElement("div");
    dataSubplot.className = "tp-subplot";
    const dataLabelDiv = document.createElement("div");
    dataLabelDiv.className = "tp-subplot-label";
    dataLabelDiv.innerHTML = `<span style="color:${DATA_COLOR}">user data</span><span>0 B/s</span>`;
    dataSubplot.appendChild(dataLabelDiv);
    const dataCanvas = document.createElement("canvas");
    dataCanvas.height = 40;
    dataSubplot.appendChild(dataCanvas);
    el.appendChild(dataSubplot);

    panel.appendChild(el);

    const entry = {
      discovery: [],
      data: [],
      total: [],
      emaDisc: 0,
      emaData: 0,
      emaTotal: 0,
      el,
      discCanvas,
      dataCanvas,
      discLabel: discLabelDiv.querySelector("span:last-child"),
      dataLabel: dataLabelDiv.querySelector("span:last-child"),
      rateEl: rateSpan,
      lastSample: null,
    };
    domains.set(domainId, entry);
    return entry;
  }

  function formatRate(bytesPerSec) {
    if (bytesPerSec < 1024) return `${bytesPerSec.toFixed(0)} B/s`;
    if (bytesPerSec < 1024 * 1024) return `${(bytesPerSec / 1024).toFixed(1)} KB/s`;
    return `${(bytesPerSec / (1024 * 1024)).toFixed(2)} MB/s`;
  }

  function pushPoint(arr, value) {
    arr.push(value);
    if (arr.length > MAX_POINTS) arr.shift();
  }

  function drawSparkline(canvas, points, color, eventIndices) {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const w = rect.width * dpr;
    const h = rect.height * dpr;
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, w, h);

    if (points.length < 2) return;

    // Compute y-axis scale — use min/max relative scaling to amplify changes.
    // Keep a 20% padding above and below, and floor minVal at 0.
    let minVal = Infinity, maxVal = 0;
    for (const p of points) {
      if (p < minVal) minVal = p;
      if (p > maxVal) maxVal = p;
    }
    if (minVal === Infinity) minVal = 0;
    minVal = Math.max(0, minVal);
    const range = maxVal - minVal;
    // If range is tiny relative to max (< 5%), use 0-based to avoid noisy zooming
    const useRelative = range > 0 && maxVal > 0 && (range / maxVal) < 0.5;
    let scaleMin, scaleMax;
    if (useRelative && range > 0) {
      const pad = range * 0.25;
      scaleMin = Math.max(0, minVal - pad);
      scaleMax = maxVal + pad;
    } else {
      scaleMin = 0;
      scaleMax = niceNum(maxVal) || 1;
    }
    const scaleRange = scaleMax - scaleMin || 1;

    const stepX = w / (MAX_POINTS - 1);
    const startIdx = MAX_POINTS - points.length;
    const topPad = 12 * dpr;

    // Draw event markers first (behind the line)
    if (eventIndices && eventIndices.length > 0) {
      ctx.save();
      for (const ev of eventIndices) {
        const idx = ev.index;
        if (idx < 0 || idx >= MAX_POINTS) continue;
        const x = idx * stepX;
        // Vertical dashed line
        ctx.strokeStyle = "#e0b84d";
        ctx.lineWidth = 1 * dpr;
        ctx.setLineDash([3 * dpr, 3 * dpr]);
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
        ctx.stroke();
        ctx.setLineDash([]);
        // Small label at top
        ctx.fillStyle = "#e0b84d";
        ctx.font = `bold ${8 * dpr}px sans-serif`;
        ctx.textAlign = "center";
        ctx.fillText(ev.label, x, 8 * dpr);
      }
      ctx.restore();
    }

    // Draw horizontal grid lines (3 lines: min, mid, max)
    ctx.strokeStyle = GRID_COLOR;
    ctx.lineWidth = 1;
    for (let i = 0; i <= 2; i++) {
      const frac = i / 2;
      const gy = h - frac * (h - topPad);
      ctx.beginPath();
      ctx.moveTo(0, gy);
      ctx.lineTo(w, gy);
      ctx.stroke();
    }

    // Draw y-axis labels (min and max)
    ctx.fillStyle = TEXT_COLOR;
    ctx.font = `${8 * dpr}px sans-serif`;
    ctx.textAlign = "left";
    ctx.fillText(formatRate(scaleMin), 2 * dpr, h - 2 * dpr);
    ctx.textAlign = "right";
    ctx.fillText(formatRate(scaleMax), w - 2 * dpr, topPad + 2 * dpr);

    // Map value to y coordinate
    function yOf(val) {
      return h - ((val - scaleMin) / scaleRange) * (h - topPad);
    }

    // Fill area under curve
    ctx.beginPath();
    const firstX = startIdx * stepX;
    ctx.moveTo(firstX, h);
    for (let i = 0; i < points.length; i++) {
      ctx.lineTo((startIdx + i) * stepX, yOf(points[i]));
    }
    ctx.lineTo((startIdx + points.length - 1) * stepX, h);
    ctx.closePath();
    ctx.fillStyle = color + "1a";
    ctx.fill();

    // Stroke the line
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5 * dpr;
    ctx.lineJoin = "round";
    ctx.beginPath();
    for (let i = 0; i < points.length; i++) {
      const x = (startIdx + i) * stepX;
      if (i === 0) ctx.moveTo(x, yOf(points[i]));
      else ctx.lineTo(x, yOf(points[i]));
    }
    ctx.stroke();
  }

  function niceNum(val) {
    if (val <= 0) return 1;
    const exp = Math.floor(Math.log10(val));
    const frac = val / Math.pow(10, exp);
    let nice;
    if (frac <= 1) nice = 1;
    else if (frac <= 2) nice = 2;
    else if (frac <= 5) nice = 5;
    else nice = 10;
    return nice * Math.pow(10, exp);
  }

  // Hook into the existing WebSocket. mesh_graph.js creates the WS; we intercept
  // traffic_stats messages by monkey-patching or by listening on the same WS.
  // Simpler: poll the REST endpoint and also listen for WS messages via a
  // MutationObserver-style hook. Since mesh_graph.js's connectWebSocket is in an IIFE,
  // we create our own parallel WS connection (same URL, minimal overhead).

  const WS_ORIGIN = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}`;
  const WS_URL = `${WS_ORIGIN}/ws`;
  const REST_URL = `${location.protocol}//${location.host}/api/traffic_stats`;

  // Event markers: track resolution changes as indices into the point arrays.
  // Each entry: { index: <position in MAX_POINTS window>, label: "D"/"M"/"I" }
  // Global counter tracks how many points have been pushed (so we can convert
  // a "now" event into a rolling-window index).
  let globalPointCount = 0;
  const eventMarkers = [];  // { atCount: <globalPointCount when event fired>, label }
  const MODE_LABELS = { debug: "D", mission: "M", init: "I" };

  function addEventMarker(label) {
    eventMarkers.push({ atCount: globalPointCount, label: label });
  }

  function getVisibleEvents() {
    // Convert absolute atCount to index within the current MAX_POINTS window
    const windowStart = globalPointCount - MAX_POINTS;
    const visible = [];
    for (const ev of eventMarkers) {
      const idx = ev.atCount - windowStart;
      if (idx >= 0 && idx < MAX_POINTS) {
        visible.push({ index: idx, label: ev.label });
      }
    }
    // Prune old markers that scrolled off
    while (eventMarkers.length > 0 && eventMarkers[0].atCount < windowStart - 10) {
      eventMarkers.shift();
    }
    return visible;
  }

  function handleTrafficSample(sample) {
    const domainId = sample.domain_id;
    if (domainId == null) return;

    const intervalSec = (sample.interval_ms || 2000) / 1000;
    const discRate = (sample.discovery_bytes || 0) / intervalSec;
    const dataRate = (sample.data_bytes || 0) / intervalSec;
    const totalRate = (sample.total_bytes || 0) / intervalSec;

    const entry = ensureDomain(domainId);
    pushPoint(entry.discovery, discRate);
    pushPoint(entry.data, dataRate);
    pushPoint(entry.total, totalRate);
    globalPointCount++;
    // Update EMA
    entry.emaDisc = entry.emaDisc === 0 ? discRate : EMA_ALPHA * discRate + (1 - EMA_ALPHA) * entry.emaDisc;
    entry.emaData = entry.emaData === 0 ? dataRate : EMA_ALPHA * dataRate + (1 - EMA_ALPHA) * entry.emaData;
    entry.emaTotal = entry.emaTotal === 0 ? totalRate : EMA_ALPHA * totalRate + (1 - EMA_ALPHA) * entry.emaTotal;
    entry.lastSample = sample;

    // Update labels with smoothed EMA rates
    entry.rateEl.textContent = formatRate(entry.emaTotal);
    entry.discLabel.textContent = formatRate(entry.emaDisc);
    entry.dataLabel.textContent = formatRate(entry.emaData);

    // Redraw sparklines with event markers
    const events = getVisibleEvents();
    drawSparkline(entry.discCanvas, entry.discovery, DISCOVERY_COLOR, events);
    drawSparkline(entry.dataCanvas, entry.data, DATA_COLOR, events);
  }

  function connectTrafficWs() {
    const ws = new WebSocket(WS_URL);
    ws.onmessage = (evt) => {
      let msg;
      try { msg = JSON.parse(evt.data); } catch (_) { return; }
      if (msg.type === "traffic_stats" && msg.data) {
        handleTrafficSample(msg.data);
      } else if (msg.type === "resolution_change") {
        const mode = (msg.resolution_mode || "").toLowerCase();
        const label = MODE_LABELS[mode] || mode.charAt(0).toUpperCase();
        addEventMarker(label);
      }
    };
    ws.onclose = () => { setTimeout(connectTrafficWs, 3000); };
    ws.onerror = () => {};
  }

  // Seed from REST on load
  fetch(REST_URL)
    .then((r) => r.json())
    .then((arr) => {
      if (Array.isArray(arr)) arr.forEach(handleTrafficSample);
    })
    .catch(() => {});

  connectTrafficWs();
})();
