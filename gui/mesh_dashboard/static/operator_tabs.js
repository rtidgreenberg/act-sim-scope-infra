(function () {
  "use strict";

  const tabs = [...document.querySelectorAll("#app-tabs [data-tab]")];
  const deliveryTable = document.getElementById("delivery-table");
  const deliveryBody = deliveryTable.querySelector("tbody");
  const deliveryEmpty = document.getElementById("delivery-empty");
  const deliveryReliableSummary = document.getElementById("delivery-reliable-summary");
  const deliveryPeriodicSummary = document.getElementById("delivery-periodic-summary");
  const deliveryWindow = document.getElementById("delivery-window");
  const deliveryNodeFilter = document.getElementById("delivery-filter-node");
  const deliveryResolutionFilter = document.getElementById("delivery-filter-resolution");
  const deliveryVersions = new Map();
  let latestSnapshot = { rows: [], node_modes: {}, window_ms: 30_000 };
  const deliveryUrl = `${location.protocol}//${location.host}/api/delivery_stats`;
  const websocketUrl = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`;
  const DELIVERY_REFRESH_MS = 1000;
  const experimentState = document.getElementById("experiment-state");
  const experimentSource = document.getElementById("experiment-source");
  const experimentDestination = document.getElementById("experiment-destination");
  const experimentDirection = document.getElementById("experiment-direction");
  const experimentKind = document.getElementById("experiment-kind");
  const experimentPathloss = document.getElementById("experiment-pathloss");
  const experimentLatency = document.getElementById("experiment-latency");
  const experimentJitter = document.getElementById("experiment-jitter");
  const experimentUnicast = document.getElementById("experiment-unicast");
  const experimentBroadcast = document.getElementById("experiment-broadcast");
  const experimentLoss = document.getElementById("experiment-loss");
  const experimentDuplicate = document.getElementById("experiment-duplicate");
  const experimentApply = document.getElementById("experiment-apply");
  const experimentReset = document.getElementById("experiment-reset");
  const experimentCurrentPath = document.getElementById("experiment-current-path");
  const experimentCurrentPathloss = document.getElementById("experiment-current-pathloss");
  const experimentCurrentUpdated = document.getElementById("experiment-current-updated");
  const experimentCurrentNetwork = document.getElementById("experiment-current-network");
  const controllerUrl = `${location.protocol}//${location.host}/api/emane_controller`;
  const EXPERIMENT_REFRESH_MS = 2000;
  let experimentRequestVersion = 0;

  const MISSION_TOPICS = new Set(["PlatformDetailStatus", "PlatformMissionStatus", "PlatformWaypointStatus"]);
  const DEBUG_TOPICS = new Set(["PlatformDebugStatus", "PlatformThrusterStatus", "PlatformPowerStatus"]);
  const RELIABLE_TOPICS = new Set(["ControlCommand", "PlatformCommandAck", "RouterHealth"]);

  function rowMatchesNodeFilter(row, node) {
    if (!node || node === "all") return true;
    const recipient = row.recipient || "Control_20";
    return row.source === node || recipient === node;
  }

  function rowMatchesResolutionFilter(row, resolution) {
    if (!resolution || resolution === "all") return true;
    if (resolution === "init") return !MISSION_TOPICS.has(row.topic) && !DEBUG_TOPICS.has(row.topic);
    if (resolution === "mission") return !DEBUG_TOPICS.has(row.topic);
    if (resolution === "debug") return true;
    return true;
  }

  function autoModeForSelectedNode() {
    const selectedNode = deliveryNodeFilter.value || "all";
    if (!selectedNode.startsWith("Platform_")) {
      deliveryResolutionFilter.value = "all";
      return;
    }
    const nodeModes = latestSnapshot?.node_modes || {};
    const mode = String(nodeModes[selectedNode] || "init").toLowerCase();
    if (["init", "mission", "debug"].includes(mode)) {
      deliveryResolutionFilter.value = mode;
    }
  }

  function rebuildNodeFilterOptions(rows) {
    const previous = deliveryNodeFilter.value || "all";
    const nodes = new Set(["Control_20"]);
    for (const row of rows) {
      if (row.source) nodes.add(row.source);
      nodes.add(row.recipient || "Control_20");
    }
    const sorted = [...nodes].sort();
    deliveryNodeFilter.replaceChildren();
    const allOption = document.createElement("option");
    allOption.value = "all";
    allOption.textContent = "All nodes";
    deliveryNodeFilter.appendChild(allOption);
    for (const node of sorted) {
      const option = document.createElement("option");
      option.value = node;
      option.textContent = node;
      deliveryNodeFilter.appendChild(option);
    }
    deliveryNodeFilter.value = sorted.includes(previous) || previous === "all" ? previous : "all";
  }

  function percentClass(value) {
    if (value < 80) return "bad";
    if (value < 90) return "warn";
    return "";
  }

  function renderDeliveryComparison(rows) {
    const groups = [
      [RELIABLE_TOPICS, deliveryReliableSummary, "15 s grace"],
      [null, deliveryPeriodicSummary, "2 s grace"],
    ];
    for (const [topicSet, element, grace] of groups) {
      const selected = rows.filter((row) => !topicSet || topicSet.has(row.topic));
      const settled = selected.reduce((sum, row) => sum + (row.settled || 0), 0);
      const received = selected.reduce((sum, row) => sum + (row.settled_received || 0), 0);
      const lost = selected.reduce((sum, row) => sum + (row.lost || 0), 0);
      const ready = selected.filter((row) => row.percentage_ready).length;
      const label = topicSet ? "reliable" : "periodic/report";
      element.innerHTML = `<strong>${received}/${settled}</strong> settled received · ` +
        `<strong>${lost}</strong> lost · ${ready}/${selected.length} flows ready · ${grace} · ${label}`;
    }
  }

  function renderDelivery(snapshot) {
    latestSnapshot = snapshot || latestSnapshot;
    const rows = latestSnapshot?.rows || [];
    rebuildNodeFilterOptions(rows);
    autoModeForSelectedNode();
    const selectedNode = deliveryNodeFilter.value || "all";
    const selectedResolution = deliveryResolutionFilter.value || "all";
    const filteredRows = rows.filter((row) =>
      rowMatchesNodeFilter(row, selectedNode) && rowMatchesResolutionFilter(row, selectedResolution));
    renderDeliveryComparison(filteredRows);
    const windowSeconds = Math.round((latestSnapshot?.window_ms || 30_000) / 1000);
    deliveryWindow.textContent = `Rolling ${windowSeconds} s`;
    deliveryBody.replaceChildren();
    deliveryTable.hidden = filteredRows.length === 0;
    deliveryEmpty.hidden = filteredRows.length > 0;
    if (rows.length > 0 && filteredRows.length === 0) {
      deliveryEmpty.textContent = "No flows match the selected filters.";
    } else {
      deliveryEmpty.textContent = "Waiting for status samples.";
    }

    const routeGroups = new Map();
    for (const row of filteredRows) {
      const routeGroup = `${row.source || "Unknown"} -> ${row.recipient || "Control_20"}`;
      if (!routeGroups.has(routeGroup)) routeGroups.set(routeGroup, []);
      routeGroups.get(routeGroup).push(row);
    }

    for (const [routeGroup, groupRows] of routeGroups) {
      const groupRow = document.createElement("tr");
      groupRow.className = "delivery-route-group";
      const groupCell = document.createElement("td");
      groupCell.colSpan = 4;
      groupCell.textContent = `${routeGroup} (${groupRows.length} topics)`;
      groupRow.appendChild(groupCell);
      deliveryBody.appendChild(groupRow);
      const groupKey = routeGroup.replace(/[^a-zA-Z0-9_-]/g, "_");
      let expanded = true;
      groupRow.classList.add("expanded");
      for (const row of groupRows) {
      const tableRow = document.createElement("tr");
      tableRow.className = "delivery-topic-row";
      tableRow.dataset.routeGroup = groupKey;
      const flow = document.createElement("td");
      flow.textContent = row.topic;
      tableRow.appendChild(flow);
      const flowKey = `${row.topic}|${row.source}|${row.recipient}`;
      const sent = document.createElement("td");
      sent.textContent = String(row.sent ?? row.expected ?? 0);
      const previous = deliveryVersions.get(flowKey);
      if (previous && previous.sent < row.sent_event_version) {
        sent.className = "delivery-new";
      }
      tableRow.appendChild(sent);
      const received = document.createElement("td");
      received.textContent = String(row.received);
      if (previous && previous.received < row.received_event_version) {
        received.className = "delivery-new";
      }
      tableRow.appendChild(received);
      const missing = document.createElement("td");
      missing.textContent = String(row.lost ?? row.missing);
      tableRow.appendChild(missing);
      deliveryVersions.set(flowKey, {
        sent: row.sent_event_version,
        received: row.received_event_version,
      });
      deliveryBody.appendChild(tableRow);
      }
      groupRow.addEventListener("click", () => {
        expanded = !expanded;
        groupRow.classList.toggle("expanded", expanded);
        for (const topicRow of deliveryBody.querySelectorAll(`tr[data-route-group="${groupKey}"]`)) {
          topicRow.hidden = !expanded;
        }
      });
    }
  }

  function selectTab(name) {
    document.body.dataset.activeTab = name;
    for (const tab of tabs) {
      tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
    }
    if (name === "delivery") {
      refreshDelivery();
    }
    if (name === "experiment") refreshExperimentController();
  }

  function refreshDelivery() {
    return fetch(deliveryUrl)
      .then((response) => response.ok ? response.json() : Promise.reject(response.status))
      .then(renderDelivery)
      .catch(() => {});
  }

  function setExperimentEnabled(enabled, unavailableReason) {
    experimentApply.disabled = !enabled;
    experimentReset.disabled = !enabled;
    experimentSource.disabled = !enabled;
    experimentDestination.disabled = !enabled;
    experimentDirection.disabled = !enabled;
    experimentKind.disabled = !enabled;
    experimentPathloss.disabled = !enabled;
    experimentLatency.disabled = !enabled;
    experimentJitter.disabled = !enabled;
    experimentUnicast.disabled = !enabled;
    experimentBroadcast.disabled = !enabled;
    experimentLoss.disabled = !enabled;
    experimentDuplicate.disabled = !enabled;
    experimentState.textContent = enabled ? "Controller ready" : (unavailableReason || "Controller unavailable");
    experimentState.classList.toggle("ready", enabled);
  }

  function refreshExperimentController() {
    Promise.all([
      fetch(controllerUrl).then((response) => response.json()),
      fetch(`${location.protocol}//${location.host}/api/mesh_status`).then((response) => response.json()),
    ]).then(([status, mesh]) => {
      rebuildExperimentNodeOptions(mesh);
      setExperimentEnabled(Boolean(status.available), status.unavailable_reason);
      renderExperimentState(status);
    }).catch(() => setExperimentEnabled(false));
  }

  function rebuildExperimentNodeOptions(mesh) {
    const previousSource = experimentSource.value || "Platform_30";
    const previousDestination = experimentDestination.value || "Control_20";
    const nodes = new Set(["Control_20"]);
    for (const row of mesh || []) {
      const data = row.data || {};
      if (data.observer_node) nodes.add(data.observer_node);
      for (const peer of data.peers || []) {
        const router = peer.health?.router || "";
        const node = router.split("/")[0];
        if (node) nodes.add(node);
      }
    }
    const sorted = [...nodes].sort();
    for (const select of [experimentSource, experimentDestination]) {
      select.replaceChildren();
      for (const node of sorted) {
        const option = document.createElement("option");
        option.value = node;
        option.textContent = node;
        select.appendChild(option);
      }
    }
    experimentSource.value = sorted.includes(previousSource) ? previousSource : sorted.find((node) => node !== "Control_20") || sorted[0];
    experimentDestination.value = sorted.includes(previousDestination) ? previousDestination : "Control_20";
    if (experimentSource.value === experimentDestination.value && sorted.length > 1) {
      experimentDestination.value = sorted.find((node) => node !== experimentSource.value) || sorted[0];
    }
  }

  function renderExperimentState(status) {
    const impairments = status.impairments || [];
    if (impairments.length > 0) {
      experimentCurrentPath.textContent = impairments
        .map((impairment) => `${impairment.source} -> ${impairment.destination}`)
        .join("; ");
      experimentCurrentPathloss.textContent = impairments
        .map((impairment) => impairment.kind === "commeffect"
          ? `${impairment.source} -> ${impairment.destination}: latency ${Number(impairment.latency_ms).toFixed(1)} ms, jitter ${Number(impairment.jitter_ms).toFixed(1)} ms, unicast ${impairment.unicast_bps} bps, broadcast ${impairment.broadcast_bps} bps, loss ${Number(impairment.loss_percent).toFixed(1)}%, duplicate ${Number(impairment.duplicate_percent).toFixed(1)}%`
          : `${impairment.source} -> ${impairment.destination}: ${Number(impairment.pathloss_db).toFixed(1)} dB`)
        .join("; ");
      experimentCurrentUpdated.textContent = new Date(Math.max(...impairments.map((impairment) => impairment.updated_at || 0))).toLocaleTimeString();
    } else {
      experimentCurrentPath.textContent = "No impairment command recorded";
      experimentCurrentPathloss.textContent = "-";
      experimentCurrentUpdated.textContent = "-";
    }
    const observed = status.observed_network;
    experimentCurrentNetwork.textContent = observed
      ? `All nodes, ${observed.interval_ms} ms: TX ${observed.mac_tx_packets} packets, RX ${observed.mac_rx_packets} packets, drops ${observed.mac_rx_drops + observed.mac_tx_drops}`
      : "Waiting for EMANE counters";
  }

  async function sendExperiment(pathlossDb, resetScenario = false) {
    const requestVersion = ++experimentRequestVersion;
    const kind = experimentKind.value;
    const source = experimentSource.value;
    const destination = experimentDestination.value;
    const body = {
      kind,
      source,
      destination,
      direction: resetScenario ? "bidirectional" : experimentDirection.value,
      reset_all: resetScenario,
      pathloss_db: pathlossDb,
      latency_ms: Number(experimentLatency.value),
      jitter_ms: Number(experimentJitter.value),
      unicast_bps: Number(experimentUnicast.value),
      broadcast_bps: Number(experimentBroadcast.value),
      loss_percent: Number(experimentLoss.value),
      duplicate_percent: Number(experimentDuplicate.value),
    };
    if (resetScenario) {
      body.latency_ms = 0;
      body.jitter_ms = 0;
      body.unicast_bps = 0;
      body.broadcast_bps = 0;
      body.loss_percent = 0;
      body.duplicate_percent = 0;
    }
    const response = await fetch(controllerUrl, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "EMANE controller request failed");
    }
    if (requestVersion !== experimentRequestVersion) return;
    renderExperimentState(result);
    experimentState.textContent = resetScenario
      ? "Scenario reset"
      : (kind === "commeffect" ? "Applied CommEffect" : `Applied ${pathlossDb} dB pathloss`);
    if (resetScenario) {
      experimentPathloss.value = "0";
      experimentLatency.value = "0";
      experimentJitter.value = "0";
      experimentUnicast.value = "0";
      experimentBroadcast.value = "0";
      experimentLoss.value = "0";
      experimentDuplicate.value = "0";
    }
  }

  function updateExperimentKindFields() {
    const isCommEffect = experimentKind.value === "commeffect";
    for (const element of document.querySelectorAll(".experiment-commeffect-field")) {
      element.hidden = !isCommEffect;
    }
    for (const element of document.querySelectorAll(".experiment-pathloss-field")) {
      element.hidden = isCommEffect;
    }
  }

  experimentApply.addEventListener("click", () => {
    sendExperiment(Number(experimentPathloss.value)).catch((error) => {
      experimentState.textContent = error.message;
    });
  });
  experimentReset.addEventListener("click", () => {
    sendExperiment(0, true).catch((error) => {
      experimentState.textContent = error.message;
    });
  });
  experimentKind.addEventListener("change", updateExperimentKindFields);

  for (const tab of tabs) {
    tab.addEventListener("click", () => selectTab(tab.dataset.tab));
  }
  deliveryNodeFilter.addEventListener("change", () => {
    autoModeForSelectedNode();
    renderDelivery(latestSnapshot);
  });
  deliveryResolutionFilter.addEventListener("change", () => renderDelivery(latestSnapshot));
  selectTab("mesh");
  updateExperimentKindFields();

  function connectDeliverySocket() {
    const socket = new WebSocket(websocketUrl);
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === "delivery_stats") renderDelivery(message.data);
      } catch (_) {}
    };
    socket.onclose = () => setTimeout(connectDeliverySocket, 3000);
  }

  connectDeliverySocket();
  setInterval(() => {
    if (document.body.dataset.activeTab === "delivery") refreshDelivery();
  }, DELIVERY_REFRESH_MS);
  setInterval(() => {
    if (document.body.dataset.activeTab === "experiment") {
      refreshExperimentController();
    }
  }, EXPERIMENT_REFRESH_MS);
})();
