(function () {
  "use strict";

  const tabs = [...document.querySelectorAll("#app-tabs [data-tab]")];
  const deliveryTable = document.getElementById("delivery-table");
  const deliveryBody = deliveryTable.querySelector("tbody");
  const deliveryEmpty = document.getElementById("delivery-empty");
  const deliveryWindow = document.getElementById("delivery-window");
  const deliveryNodeFilter = document.getElementById("delivery-filter-node");
  const deliveryResolutionFilter = document.getElementById("delivery-filter-resolution");
  const deliveryVersions = new Map();
  let latestSnapshot = { rows: [], node_modes: {}, window_ms: 30_000 };
  const deliveryUrl = `${location.protocol}//${location.host}/api/delivery_stats`;
  const websocketUrl = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`;
  const experimentState = document.getElementById("experiment-state");
  const experimentSource = document.getElementById("experiment-source");
  const experimentDestination = document.getElementById("experiment-destination");
  const experimentDirection = document.getElementById("experiment-direction");
  const experimentPathloss = document.getElementById("experiment-pathloss");
  const experimentApply = document.getElementById("experiment-apply");
  const experimentReset = document.getElementById("experiment-reset");
  const controllerUrl = `${location.protocol}//${location.host}/api/emane_controller`;

  const MISSION_TOPICS = new Set(["PlatformDetailStatus", "PlatformMissionStatus", "PlatformWaypointStatus"]);
  const DEBUG_TOPICS = new Set(["PlatformDebugStatus", "PlatformThrusterStatus", "PlatformPowerStatus"]);

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

  function renderDelivery(snapshot) {
    latestSnapshot = snapshot || latestSnapshot;
    const rows = latestSnapshot?.rows || [];
    rebuildNodeFilterOptions(rows);
    autoModeForSelectedNode();
    const selectedNode = deliveryNodeFilter.value || "all";
    const selectedResolution = deliveryResolutionFilter.value || "all";
    const filteredRows = rows.filter((row) =>
      rowMatchesNodeFilter(row, selectedNode) && rowMatchesResolutionFilter(row, selectedResolution));
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
      groupCell.colSpan = 6;
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
      const expected = document.createElement("td");
      expected.textContent = String(row.expected);
      const previous = deliveryVersions.get(flowKey);
      if (previous && previous.sent < row.sent_event_version) {
        expected.className = "delivery-new";
      }
      tableRow.appendChild(expected);
      const sendRate = document.createElement("td");
      sendRate.textContent = row.send_rate_hz == null ? "-" : `${row.send_rate_hz.toFixed(1)} Hz`;
      tableRow.appendChild(sendRate);
      const received = document.createElement("td");
      received.textContent = String(row.received);
      if (previous && previous.received < row.received_event_version) {
        received.className = "delivery-new";
      }
      tableRow.appendChild(received);
      const missing = document.createElement("td");
      missing.textContent = String(row.missing);
      tableRow.appendChild(missing);
      deliveryVersions.set(flowKey, {
        sent: row.sent_event_version,
        received: row.received_event_version,
      });
      const percentage = document.createElement("td");
      const minSamples = Number(row.percentage_required_samples ?? row.percentage_min_samples ?? 30);
      const expectedSamples = Number(row.expected ?? 0);
      const percentageReady = Boolean(row.percentage_ready);
      if (!percentageReady) {
        percentage.className = "delivery-rate warn";
        percentage.textContent = `Calculating (${expectedSamples}/${minSamples}) until full set`;
      } else {
        percentage.className = `delivery-rate ${percentClass(row.percentage ?? 0)}`;
        percentage.textContent = row.percentage == null ? "-" : `${row.percentage.toFixed(1)}%`;
      }
      tableRow.appendChild(percentage);
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
      fetch(deliveryUrl)
        .then((response) => response.ok ? response.json() : Promise.reject(response.status))
        .then(renderDelivery)
        .catch(() => {});
    }
    if (name === "experiment") refreshExperimentController();
  }

  function setExperimentEnabled(enabled) {
    experimentApply.disabled = !enabled;
    experimentReset.disabled = !enabled;
    experimentSource.disabled = !enabled;
    experimentDestination.disabled = !enabled;
    experimentDirection.disabled = !enabled;
    experimentPathloss.disabled = !enabled;
    experimentState.textContent = enabled ? "Controller ready" : "Controller unavailable";
    experimentState.classList.toggle("ready", enabled);
  }

  function refreshExperimentController() {
    fetch(controllerUrl).then((response) => response.json()).then((status) => {
      setExperimentEnabled(Boolean(status.available));
    }).catch(() => setExperimentEnabled(false));
  }

  async function sendExperiment(pathlossDb) {
    const response = await fetch(controllerUrl, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        source: experimentSource.value, destination: experimentDestination.value,
        direction: experimentDirection.value, pathloss_db: pathlossDb,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "EMANE controller request failed");
    experimentState.textContent = pathlossDb === 0 ? "Scenario reset" : `Applied ${pathlossDb} dB pathloss`;
  }

  experimentApply.addEventListener("click", () => {
    sendExperiment(Number(experimentPathloss.value)).catch((error) => {
      experimentState.textContent = error.message;
    });
  });
  experimentReset.addEventListener("click", () => {
    sendExperiment(0).catch((error) => {
      experimentState.textContent = error.message;
    });
  });

  for (const tab of tabs) {
    tab.addEventListener("click", () => selectTab(tab.dataset.tab));
  }
  deliveryNodeFilter.addEventListener("change", () => {
    autoModeForSelectedNode();
    renderDelivery(latestSnapshot);
  });
  deliveryResolutionFilter.addEventListener("change", () => renderDelivery(latestSnapshot));
  selectTab("mesh");

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
})();
