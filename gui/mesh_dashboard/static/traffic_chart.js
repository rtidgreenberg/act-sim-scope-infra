// traffic_chart.js — WAN DDS traffic time-series sparklines (domain 200).
// Subscribes to "traffic_stats" WebSocket messages from mesh_bridge.py (which reads
// DomainTrafficStats published by debug/scripts/domain_traffic_monitor.py). Renders small canvas
// application-carrying frame rates by default. Optional infrastructure series are
// also DDS/RTPS Ethernet frames; visible series alone determine scale and peaks.

(function () {
  "use strict";

  const MAX_POINTS = 300;  // rolling visual history
  const SERIES = [
    {key: "application_tx", label: "WAN application TX", color: "#3a7bd9"},
    {key: "infrastructure_tx", label: "Router heartbeat TX", color: "#b88ce0"},
    {key: "discovery_tx", label: "Discovery TX", color: "#3aa655"},
    {key: "reliability_tx", label: "Reliability/control TX", color: "#e0944c"},
    {key: "mixed_tx", label: "Mixed TX", color: "#e0ce64"},
    {key: "unknown_tx", label: "Unknown TX", color: "#e6553a"},
  ];
  const GRID_COLOR = "#232a34";
  const TEXT_COLOR = "#8a94a6";

  const panel = document.getElementById("traffic-panel");
  const graphEl = document.getElementById("graph");
  const statusbarEl = document.getElementById("statusbar");
  const infraToggle = document.getElementById("toggle-infrastructure-stats");
  let showInfrastructure = false;
  if (!panel) return;

  function syncGraphBottomToPanel() {
    if (!graphEl) return;
    const statusHeight = statusbarEl ? statusbarEl.getBoundingClientRect().height : 0;
    panel.style.bottom = `${Math.ceil(statusHeight)}px`;
    const panelHeight = panel.children.length ? panel.getBoundingClientRect().height : 0;
    const bottom = Math.max(0, Math.ceil(panelHeight + statusHeight));
    graphEl.style.bottom = `${bottom}px`;
    document.documentElement.style.setProperty("--mesh-bottom-inset", `${bottom}px`);
  }

  // domain_id -> { discovery: [{t, bytes}], data: [{t, bytes}], total: [{t, bytes}],
  //                el, chartCanvas, discLabel, dataLabel, rateEl, lastSample }
  const domains = new Map();

  function ensureDomain(domainId) {
    if (domains.has(domainId)) return domains.get(domainId);

    const el = document.createElement("div");
    el.className = "tp-domain";

    const combinedSubplot = document.createElement("div");
    combinedSubplot.className = "tp-subplot";
    const legendDiv = document.createElement("div");
    legendDiv.className = "tp-subplot-label";
    legendDiv.innerHTML = SERIES.map(s =>
      `<span data-series="${s.key}" style="color:${s.color}">${s.label} ` +
      `<span class="tp-subplot-average">waiting</span></span>`).join("");
    combinedSubplot.appendChild(legendDiv);
    const chartCanvas = document.createElement("canvas");
    chartCanvas.height = 132;
    combinedSubplot.appendChild(chartCanvas);
    el.appendChild(combinedSubplot);

    const accountingDiv = document.createElement("div");
    accountingDiv.className = "tp-frame-accounting";
    accountingDiv.textContent = "Waiting for traffic accounting";
    el.appendChild(accountingDiv);

    panel.appendChild(el);

    const entry = {
      series: Object.fromEntries(SERIES.map(s => [s.key, {
        points: [], label: legendDiv.querySelector(`[data-series="${s.key}"]`),
      }])),
      el,
      chartCanvas,
      accountingEl: accountingDiv,
      lastSample: null,
      lastPointTimestamp: null,
      rates: new window.ACTTrafficRates.History(),
    };
    domains.set(domainId, entry);
    redraw(entry);
    syncGraphBottomToPanel();
    return entry;
  }

  function formatRate(bytesPerSec) {
    return `${(bytesPerSec * 8 / 1000).toFixed(1)} kb/s`;
  }

  function pushPoint(arr, value, replace = false) {
    if (replace && arr.length) arr[arr.length - 1] = value;
    else arr.push(value);
    if (arr.length > MAX_POINTS) arr.shift();
  }

  function drawCombinedSparkline(canvas, series, eventIndices) {
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

    const scaleMax = niceNum(Math.max(0, ...series.flatMap((s) => s.points.filter(Number.isFinite)))) || 1;
    const scaleMin = 0;

    const visiblePointCount = Math.max(2, ...series.map((s) => s.points.length));
    const stepX = w / (visiblePointCount - 1);
    const topPad = 12 * dpr;

    // Draw event markers first (behind the line)
    if (eventIndices && eventIndices.length > 0) {
      ctx.save();
      for (const ev of eventIndices) {
        const idx = ev.index;
        if (idx < 0 || idx >= visiblePointCount) continue;
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
        const peakY = h - ((s.peakRate - scaleMin) / scaleMax) * (h - topPad);
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

    // Discovery and user data share one zero-based scale for direct comparison.
    ctx.fillStyle = TEXT_COLOR;
    ctx.font = `${8 * dpr}px sans-serif`;
    ctx.textAlign = "left";
    ctx.fillText(formatRate(scaleMin), 2 * dpr, h - 2 * dpr);
    ctx.textAlign = "right";
    ctx.fillText(`shared scale ${formatRate(scaleMax)}`, w - 2 * dpr, topPad + 2 * dpr);

    for (const s of series) {
      if (s.points.length < 2) continue;
      const seriesStartIdx = visiblePointCount - s.points.length;
      const yOf = (value) => h - (value / scaleMax) * (h - topPad);

      for (let start = 0; start < s.points.length;) {
        if (!Number.isFinite(s.points[start])) { start++; continue; }
        let end = start + 1;
        while (end < s.points.length && Number.isFinite(s.points[end])) end++;
        ctx.beginPath();
        ctx.moveTo((seriesStartIdx + start) * stepX, h);
        for (let i = start; i < end; i++) {
          ctx.lineTo((seriesStartIdx + i) * stepX, yOf(s.points[i]));
        }
        ctx.lineTo((seriesStartIdx + end - 1) * stepX, h);
        ctx.closePath();
        ctx.fillStyle = s.color + "14";
        ctx.fill();
        ctx.strokeStyle = s.color;
        ctx.lineWidth = 1.5 * dpr;
        ctx.lineJoin = "round";
        ctx.beginPath();
        for (let i = start; i < end; i++) {
          const x = (seriesStartIdx + i) * stepX;
          if (i === start) ctx.moveTo(x, yOf(s.points[i]));
          else ctx.lineTo(x, yOf(s.points[i]));
        }
        ctx.stroke();
        start = end;
      }
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
  function redraw(entry) {
    const visible = SERIES.filter(s => showInfrastructure || s.key === "application_tx");
    for (const spec of SERIES) {
      entry.series[spec.key].label.style.display =
        showInfrastructure || spec.key === "application_tx" ? "" : "none";
    }
    drawCombinedSparkline(entry.chartCanvas, visible.map(spec => ({
      label: spec.label, color: spec.color, points: entry.series[spec.key].points,
      peakRate: Math.max(0, ...entry.series[spec.key].points.filter(Number.isFinite)),
    })), getVisibleEvents(entry.series.application_tx.points.length));
    const s = entry.lastSample;
    if (!s) return;
    const warnings = [];
    const problem = window.ACTTrafficRates.issue(s, true) || entry.rates.problem;
    if (problem) warnings.push(problem);
    if (!s.classification_complete) warnings.push("Application rate is known traffic only; mixed/unknown excluded");
    entry.accountingEl.textContent = warnings.join(" | ") +
      (showInfrastructure ? `${warnings.length ? " | " : ""}DDS/RTPS Ethernet frame bytes, not RF airtime` : "");
    entry.accountingEl.style.display = entry.accountingEl.textContent ? "" : "none";
    for (const spec of SERIES) {
      entry.series[spec.key].label.querySelector(".tp-subplot-average").textContent = problem
        ? "unavailable" : `3s avg ${formatRate(entry.rates.rate(spec.key))}`;
    }
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

  function getVisibleEvents(visiblePointCount) {
    const windowStart = globalPointCount - visiblePointCount;
    const visible = [];
    for (const ev of eventMarkers) {
      const idx = ev.atCount - windowStart;
      if (idx >= 0 && idx < visiblePointCount) {
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

    const entry = ensureDomain(domainId);
    const previous = entry.lastSample;
    if (previous && (sample.aggregated_at_ms ?? sample.capture_timestamp) <
        (previous.aggregated_at_ms ?? previous.capture_timestamp)) return;
    entry.lastSample = sample;
    // A delayed contributor can correct an aggregate without advancing its newest source.
    const replace = entry.lastPointTimestamp != null &&
      sample.capture_timestamp <= entry.lastPointTimestamp;
    const discontinuity = !replace && entry.lastPointTimestamp != null &&
      sample.capture_window_start_ms !== entry.lastPointTimestamp;
    const pointTimestamp = replace ? entry.lastPointTimestamp : sample.capture_timestamp;
    const values = Object.fromEntries(SERIES.map(spec =>
      [spec.key, sample[`${spec.key}_bytes_per_second`]]));
    const valid = entry.rates.add(sample, values, true);
    for (const spec of SERIES) {
      const series = entry.series[spec.key];
      if (discontinuity) pushPoint(series.points, null);
      const replaceSeriesPoint = replace && series.lastPointTimestamp === pointTimestamp;
      pushPoint(series.points, valid ? values[spec.key] : null, replaceSeriesPoint);
      series.lastPointTimestamp = pointTimestamp;
    }
    entry.lastPointTimestamp = pointTimestamp;
    if (!replace) globalPointCount += discontinuity ? 2 : 1;
    redraw(entry);
    syncGraphBottomToPanel();
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

  window.addEventListener("resize", syncGraphBottomToPanel);
  if (infraToggle) {
    infraToggle.checked = false;
    infraToggle.addEventListener("change", () => {
      showInfrastructure = infraToggle.checked;
      domains.forEach(redraw);
      syncGraphBottomToPanel();
    });
  }
  setInterval(() => {
    fetch(REST_URL).then(r => r.json()).then(arr => {
      if (Array.isArray(arr)) arr.forEach(handleTrafficSample);
    }).catch(error => console.warn("Traffic refresh failed", error));
    domains.forEach(redraw);
    syncGraphBottomToPanel();
  }, 2000);
  syncGraphBottomToPanel();

  connectTrafficWs();
})();
