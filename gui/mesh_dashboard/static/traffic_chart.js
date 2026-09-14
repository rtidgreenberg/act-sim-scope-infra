// traffic_chart.js — WAN DDS traffic time-series sparklines (domain 200).
// Subscribes to "traffic_stats" WebSocket messages from mesh_bridge.py (which reads
// DomainTrafficStats published by debug/scripts/domain_traffic_monitor.py). Renders small canvas
// sparkline plots on the right panel, one card per domain ID, with discovery and user-data
// kilobits/s overlaid in a single shared-scale graph.

(function () {
  "use strict";

  const MAX_POINTS = 300;  // rolling visual history
  const EMA_ALPHA = 0.1;   // exponential moving average smoothing (lower = smoother)
  const DISCOVERY_COLOR = "#3aa655";
  const DATA_COLOR = "#3a7bd9";
  const DISPLAY_AVERAGE_MS = 10_000;
  const GRID_COLOR = "#232a34";
  const TEXT_COLOR = "#8a94a6";

  const panel = document.getElementById("traffic-panel");
  const graphEl = document.getElementById("graph");
  const statusbarEl = document.getElementById("statusbar");
  if (!panel) return;

  function syncGraphBottomToPanel() {
    if (!graphEl) return;
    const statusHeight = statusbarEl ? statusbarEl.getBoundingClientRect().height : 0;
    const panelHeight = panel.children.length ? panel.getBoundingClientRect().height : 0;
    const bottom = Math.max(0, Math.ceil(panelHeight - statusHeight));
    graphEl.style.bottom = `${bottom}px`;
  }

  // domain_id -> { discovery: [{t, bytes}], data: [{t, bytes}], total: [{t, bytes}],
  //                el, chartCanvas, discLabel, dataLabel, rateEl, lastSample }
  const domains = new Map();

  function ensureDomain(domainId) {
    if (domains.has(domainId)) return domains.get(domainId);

    const el = document.createElement("div");
    el.className = "tp-domain";

    const titleDiv = document.createElement("div");
    titleDiv.className = "tp-domain-title";
    titleDiv.innerHTML = `<span>WAN Domain ${domainId}</span>`;
    el.appendChild(titleDiv);

    const combinedSubplot = document.createElement("div");
    combinedSubplot.className = "tp-subplot";
    const legendDiv = document.createElement("div");
    legendDiv.className = "tp-subplot-label";
    legendDiv.innerHTML =
      `<span><span style="color:${DISCOVERY_COLOR}">WAN discovery TX</span> ` +
      `<span class="tp-subplot-average" style="color:${DISCOVERY_COLOR}">10s avg 0.0 kb/s</span></span>` +
      `<span><span style="color:${DATA_COLOR}">WAN user data TX</span> ` +
      `<span class="tp-subplot-average" style="color:${DATA_COLOR}">10s avg 0.0 kb/s</span></span>`;
    combinedSubplot.appendChild(legendDiv);
    const chartCanvas = document.createElement("canvas");
    chartCanvas.height = 132;
    combinedSubplot.appendChild(chartCanvas);
    el.appendChild(combinedSubplot);

    const accountingDiv = document.createElement("div");
    accountingDiv.className = "tp-frame-accounting";
    accountingDiv.textContent = "Receiver view: control 0 kb/s | bundled 0 kb/s | unknown 0 kb/s";
    el.appendChild(accountingDiv);

    panel.appendChild(el);

    const entry = {
      discovery: [],
      data: [],
      total: [],
      discoverySamples: [],
      dataSamples: [],
      emaDisc: 0,
      emaData: 0,
      emaTotal: 0,
      el,
      chartCanvas,
      discLabel: legendDiv.querySelector("span:first-child .tp-subplot-average"),
      dataLabel: legendDiv.querySelector("span:last-child .tp-subplot-average"),
      accountingEl: accountingDiv,
        maxDiscovery: 0,
        maxData: 0,
      lastSample: null,
    };
    domains.set(domainId, entry);
    syncGraphBottomToPanel();
    return entry;
  }

  function formatRate(bytesPerSec) {
    return `${(bytesPerSec * 8 / 1000).toFixed(1)} kb/s`;
  }

  function pushPoint(arr, value) {
    arr.push(value);
    if (arr.length > MAX_POINTS) arr.shift();
  }

  function pushRateSample(samples, bytes, intervalMs) {
    samples.push({ bytes, intervalMs });
    if (samples.length > MAX_POINTS) samples.shift();
  }

  function trailingRate(samples) {
    let bytes = 0;
    let durationMs = 0;
    for (let index = samples.length - 1; index >= 0 && durationMs < DISPLAY_AVERAGE_MS; index--) {
      const sample = samples[index];
      const includedMs = Math.min(sample.intervalMs, DISPLAY_AVERAGE_MS - durationMs);
      bytes += sample.bytes * includedMs / sample.intervalMs;
      durationMs += includedMs;
    }
    return durationMs ? bytes * 1000 / durationMs : 0;
  }

  function drawCombinedSparkline(canvas, series, eventIndices, sharedScaleMax) {
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

    if (!series.length || series.every((s) => s.points.length < 2)) return;

    // Discovery and user-data share a zero-based scale for direct comparison.
    let maxVal = 0;
    for (const s of series) {
      for (const p of s.points) {
        if (p > maxVal) maxVal = p;
      }
    }
    const scaleMin = 0;
    const scaleMax = sharedScaleMax || niceNum(maxVal) || 1;
    const scaleRange = scaleMax - scaleMin || 1;

    const stepX = w / (MAX_POINTS - 1);
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

      for (const s of series) {
        if (!(s.peakRate > 0 && s.peakRate >= scaleMin && s.peakRate <= scaleMax)) continue;
        const peakY = h - ((s.peakRate - scaleMin) / scaleRange) * (h - topPad);
        ctx.save();
        ctx.strokeStyle = s.color;
        ctx.globalAlpha = 0.65;
        ctx.lineWidth = dpr;
        ctx.setLineDash([4 * dpr, 3 * dpr]);
        ctx.beginPath();
        ctx.moveTo(0, peakY);
        ctx.lineTo(w, peakY);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = s.color;
        ctx.font = `bold ${8 * dpr}px sans-serif`;
        ctx.textAlign = "left";
        ctx.fillText(`${s.label} peak ${formatRate(s.peakRate)}`, 3 * dpr, Math.max(topPad + 8 * dpr, peakY - 3 * dpr));
        ctx.restore();
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

    for (const s of series) {
      if (s.points.length < 2) continue;
      const seriesStartIdx = MAX_POINTS - s.points.length;

      // Fill area under each curve with light transparency.
      ctx.beginPath();
      const firstX = seriesStartIdx * stepX;
      ctx.moveTo(firstX, h);
      for (let i = 0; i < s.points.length; i++) {
        ctx.lineTo((seriesStartIdx + i) * stepX, yOf(s.points[i]));
      }
      ctx.lineTo((seriesStartIdx + s.points.length - 1) * stepX, h);
      ctx.closePath();
      ctx.fillStyle = s.color + "14";
      ctx.fill();

      // Stroke the series line.
      ctx.strokeStyle = s.color;
      ctx.lineWidth = 1.5 * dpr;
      ctx.lineJoin = "round";
      ctx.beginPath();
      for (let i = 0; i < s.points.length; i++) {
        const x = (seriesStartIdx + i) * stepX;
        if (i === 0) ctx.moveTo(x, yOf(s.points[i]));
        else ctx.lineTo(x, yOf(s.points[i]));
      }
      ctx.stroke();
    }
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
  const EMANE_REST_URL = `${location.protocol}//${location.host}/api/emane_stats`;
  const RF_PIPE_RATE_BPS = 1000000;
  let emaneSummaryEl = null;

  function formatBitRate(bitsPerSec) {
    return `${(bitsPerSec / 1000).toFixed(1)} kb/s`;
  }

  function ensureEmaneSummary() {
    if (emaneSummaryEl) return emaneSummaryEl;
    emaneSummaryEl = document.createElement("div");
    emaneSummaryEl.className = "tp-emane-summary";
    panel.prepend(emaneSummaryEl);
    return emaneSummaryEl;
  }

  function handleEmaneSample(sample) {
    if (!sample || sample.scope !== "network") return;
    const intervalSec = (sample.interval_ms || 1000) / 1000;
    const txBytes = sample.mac_tx_bytes || 0;
    const txPackets = sample.mac_tx_packets || 0;
    const txDrops = sample.mac_tx_drops || 0;
    const txBitsPerSec = txBytes * 8 / intervalSec;
    const contributors = (sample.contributors || []).length;
    const averageNodeBitsPerSec = contributors ? txBitsPerSec / contributors : 0;
    const averageNodeUtilization = averageNodeBitsPerSec * 100 / RF_PIPE_RATE_BPS;
    ensureEmaneSummary().innerHTML =
      `<strong>Mesh RF MAC TX aggregate</strong>` +
      `<span>${formatBitRate(txBitsPerSec)}</span>` +
      `<span>avg ${formatBitRate(averageNodeBitsPerSec)}/node ` +
      `(${averageNodeUtilization.toFixed(1)}% of 1 Mb/s)</span>` +
      `<span>${(txPackets / intervalSec).toFixed(0)} pkt/s</span>` +
      `<span>${(txDrops / intervalSec).toFixed(0)} drop/s</span>` +
      `<span class="muted">${contributors} nodes</span>`;
  }

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
    if (sample.scope && sample.scope !== "network") return;
    const domainId = sample.domain_id;
    if (domainId == null) return;

    const intervalSec = (sample.interval_ms || 2000) / 1000;
    const meanOrRaw = (field) => sample[`mean_${field}`] ?? sample[field] ?? 0;
    const discoveryBytes = sample.discovery_tx_bytes ?? meanOrRaw("discovery_bytes");
    const dataBytes = sample.data_tx_bytes ?? meanOrRaw("data_bytes");
    const discRate = discoveryBytes / intervalSec;
    const dataRate = dataBytes / intervalSec;
    const reliabilityRate = meanOrRaw("reliability_bytes") / intervalSec;
    const mixedRate = meanOrRaw("mixed_bytes") / intervalSec;
    const unknownRate = meanOrRaw("unknown_bytes") / intervalSec;
    const totalRate = meanOrRaw("total_bytes") / intervalSec;

    const entry = ensureDomain(domainId);
    pushPoint(entry.discovery, discRate);
    pushPoint(entry.data, dataRate);
    pushPoint(entry.total, totalRate);
    pushRateSample(entry.discoverySamples, discoveryBytes, sample.interval_ms || 2000);
    pushRateSample(entry.dataSamples, dataBytes, sample.interval_ms || 2000);
      entry.maxDiscovery = Math.max(entry.maxDiscovery, discRate);
      entry.maxData = Math.max(entry.maxData, dataRate);
    globalPointCount++;
    // Update EMA
    entry.emaDisc = entry.emaDisc === 0 ? discRate : EMA_ALPHA * discRate + (1 - EMA_ALPHA) * entry.emaDisc;
    entry.emaData = entry.emaData === 0 ? dataRate : EMA_ALPHA * dataRate + (1 - EMA_ALPHA) * entry.emaData;
    entry.emaTotal = entry.emaTotal === 0 ? totalRate : EMA_ALPHA * totalRate + (1 - EMA_ALPHA) * entry.emaTotal;
    entry.lastSample = sample;

    // Update labels with smoothed EMA rates
    // A 10-second average makes periodic DDS bursts comparable across resolution modes.
    entry.discLabel.textContent = `10s avg ${formatRate(trailingRate(entry.discoverySamples))}`;
    entry.dataLabel.textContent = `10s avg ${formatRate(trailingRate(entry.dataSamples))}`;
    entry.accountingEl.textContent =
      `Receiver view: control ${formatRate(reliabilityRate)} | bundled ${formatRate(mixedRate)} | ` +
      `unknown ${formatRate(unknownRate)}`;

    // Redraw sparklines with event markers
        const events = getVisibleEvents();
        const sharedScaleMax = niceNum(Math.max(...entry.discovery, ...entry.data)) || 1;
        drawCombinedSparkline(entry.chartCanvas, [
          { label: "disc", points: entry.discovery, color: DISCOVERY_COLOR, peakRate: entry.maxDiscovery },
          { label: "data", points: entry.data, color: DATA_COLOR, peakRate: entry.maxData },
        ], events, sharedScaleMax);
  }

  function connectTrafficWs() {
    const ws = new WebSocket(WS_URL);
    ws.onmessage = (evt) => {
      let msg;
      try { msg = JSON.parse(evt.data); } catch (_) { return; }
      if (msg.type === "traffic_stats" && msg.data) {
        handleTrafficSample(msg.data);
      } else if (msg.type === "emane_stats" && msg.data) {
        handleEmaneSample(msg.data);
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

  fetch(EMANE_REST_URL)
    .then((r) => r.ok ? r.json() : null)
    .then(handleEmaneSample)
    .catch(() => {});

  window.addEventListener("resize", syncGraphBottomToPanel);
  syncGraphBottomToPanel();

  connectTrafficWs();
})();
