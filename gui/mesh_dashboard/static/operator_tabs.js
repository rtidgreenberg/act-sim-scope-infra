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
  const deliverySourceFilter = document.getElementById("delivery-filter-source");
  const deliveryDestinationFilter = document.getElementById("delivery-filter-destination");
  const deliveryTopicOptions = document.getElementById("delivery-topic-options");
  const deliveryConfigStatus = document.getElementById("delivery-config-status");
  const deliveryConfigTopics = document.getElementById("delivery-config-topics");
  let selectedTopics = null;
  let topicOptionsKey = "";
  let configRenderKey = "";
  let deliveryConfig = null;
  let configRequest = null;
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
  const configurationUrl = `${location.protocol}//${location.host}/api/emane_configuration`;
  const configurationStatus = document.getElementById("experiment-config-status");
  const configurationValues = document.getElementById("experiment-config-values");
  const configurationDetails = document.getElementById("experiment-config-details");
  const configurationParameters = document.getElementById("experiment-config-parameters");
  let configurationRequestVersion = 0;
  const EXPERIMENT_REFRESH_MS = 2000;
  let experimentRequestVersion = 0;
  let missionContext = { selected: [], platforms: [], team: null };
  let missionSelectionKey = "";
  const teamNames = { TEAM_A: "Alpha", TEAM_B: "Bravo", TEAM_C: "Reserve", "": "Unassigned" };
  const deliveryMissionLabel = document.createElement("span");
  const experimentMissionLabel = document.createElement("span");
  deliveryMissionLabel.className = experimentMissionLabel.className = "mission-instructions";
  deliveryNodeFilter.parentElement.parentElement.appendChild(deliveryMissionLabel);
  experimentSource.parentElement.parentElement.appendChild(experimentMissionLabel);
  window.addEventListener("act-mission-state", (event) => {
    missionContext = event.detail;
    const label = missionContext.selected.length
      ? `Mission selection: ${teamNames[missionContext.team] || "Boats"} · ${missionContext.selected.join(", ")}`
      : "";
    deliveryMissionLabel.textContent = experimentMissionLabel.textContent = label;
    const key = `${missionContext.team}|${[...missionContext.selected].sort().join(",")}`;
    if (key !== missionSelectionKey) {
      missionSelectionKey = key;
      if (missionContext.selected.length) {
        renderDelivery(latestSnapshot);
        deliveryNodeFilter.value = "mission";
        renderDelivery(latestSnapshot);
        if ([...experimentSource.options].some((option) => option.value === missionContext.selected[0])) {
          experimentSource.value = missionContext.selected[0];
          experimentDestination.value = "Control_20";
        }
      } else if (deliveryNodeFilter.value === "mission") {
        deliveryNodeFilter.value = "all"; renderDelivery(latestSnapshot);
      }
    }
  });

  const MISSION_TOPICS = new Set(["PlatformDetailStatus", "PlatformMissionStatus", "PlatformWaypointStatus"]);
  const DEBUG_TOPICS = new Set(["PlatformDebugStatus", "PlatformThrusterStatus", "PlatformPowerStatus", "ActRouterDiagnostics"]);

  function rowMatchesNodeFilter(row, node) {
    if (!node || node === "all") return true;
    if (node === "mission") return missionContext.selected.some((id) => row.source === id || row.recipient === id);
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
    if (missionContext.selected.length) {
      const option = document.createElement("option");
      option.value = "mission";
      option.textContent = `Mission: ${teamNames[missionContext.team] || "selected boats"}`;
      deliveryNodeFilter.appendChild(option);
    }
    for (const node of sorted) {
      const option = document.createElement("option");
      option.value = node;
      option.textContent = node;
      deliveryNodeFilter.appendChild(option);
    }
    deliveryNodeFilter.value = sorted.includes(previous) || previous === "all" ||
      (previous === "mission" && missionContext.selected.length) ? previous : "all";
  }

  function rebuildEndpointFilter(select, names, allLabel) {
    const previous = select.value || "all";
    if (previous !== "all") names.add(previous);
    const sorted = ["all", ...[...names].sort()];
    if (JSON.stringify([...select.options].map(option => option.value)) !== JSON.stringify(sorted)) {
      select.replaceChildren(...sorted.map(name => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name === "all" ? allLabel : name;
        return option;
      }));
      select.value = previous;
    }
  }

  function rebuildTopicFilter(rows) {
    const topics = [...new Set([...rows.map(row => row.topic),
      ...Object.keys(deliveryConfig?.topics || {}), ...(selectedTopics || [])])].sort();
    const key = JSON.stringify(topics);
    if (key !== topicOptionsKey) {
      topicOptionsKey = key;
      deliveryTopicOptions.replaceChildren(...topics.map(topic => {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = topic;
        input.addEventListener("change", () => {
          if (selectedTopics === null) selectedTopics = new Set(topics);
          if (input.checked) selectedTopics.add(topic);
          else selectedTopics.delete(topic);
          renderDelivery(latestSnapshot);
        });
        label.append(input, document.createTextNode(topic));
        return label;
      }));
    }
    for (const input of deliveryTopicOptions.querySelectorAll("input")) {
      input.checked = selectedTopics === null || selectedTopics.has(input.value);
    }
    const label = document.getElementById("delivery-topic-filter-label");
    label.textContent = selectedTopics === null ? "Topics: All" : `Topics: ${selectedTopics.size} selected`;
    label.title = selectedTopics === null ? "All topics" : [...selectedTopics].join(", ");
  }

  function renderConfiguredQos() {
    if (!deliveryConfig) return;
    const topics = (selectedTopics === null ? Object.keys(deliveryConfig.topics || {})
      : [...selectedTopics]).sort();
    const key = JSON.stringify([deliveryConfig, topics, selectedTopics === null]);
    if (!deliveryConfig.available) {
      deliveryConfigStatus.textContent = deliveryConfig.reason || "Active router configuration unavailable.";
      deliveryConfigStatus.removeAttribute("title");
    } else {
      deliveryConfigStatus.textContent = topics.length
        ? `Config: ${deliveryConfig.config_file.split("/").pop()}` : "No topics selected.";
      deliveryConfigStatus.title = `${deliveryConfig.config_file}\n${deliveryConfig.qos_libraries.join("\n")}`;
    }
    if (key === configRenderKey) return;
    configRenderKey = key;
    deliveryConfigTopics.replaceChildren();
    if (!deliveryConfig.available) return;
    const duration = value => value === null ? "Infinite" : `${value} s`;
    for (const topic of topics) {
      const details = document.createElement("details");
      details.open = selectedTopics !== null && topics.length <= 2;
      const summary = document.createElement("summary");
      summary.textContent = topic;
      details.appendChild(summary);
      const entries = deliveryConfig.topics[topic] || [];
      if (!entries.length) {
        const message = document.createElement("p");
        message.textContent = "No WAN QoS entry in the supplied router configuration.";
        details.appendChild(message);
      }
      const profiles = new Map();
      for (const entry of entries) {
        const key = JSON.stringify([entry.profile, entry.settings, entry.domain_id, entry.enabled]);
        if (!profiles.has(key)) {
          profiles.set(key, { ...entry, participants: new Set(), endpoints: new Set(), routes: [] });
        }
        const profile = profiles.get(key);
        profile.participants.add(entry.participant);
        profile.endpoints.add(entry.endpoint);
        profile.routes = [...new Set([...profile.routes, ...entry.routes])];
      }
      for (const entry of profiles.values()) {
        const section = document.createElement("div");
        section.className = "delivery-config-endpoint";
        const heading = document.createElement("strong");
        const endpoints = ["writer", "reader"].filter(kind => entry.endpoints.has(kind))
          .map(kind => kind === "writer" ? "Writer" : "Reader").join(" / ");
        heading.textContent = `${endpoints} · ${[...entry.participants].sort().join(", ")} · domain ${entry.domain_id}`;
        section.appendChild(heading);
        const fields = {
          Profile: entry.profile || "Auto QoS (learned at runtime)",
          Routes: entry.routes.join(", "),
          "Configured enabled": entry.enabled ? "Yes" : "No",
        };
        if (entry.settings) {
          const qos = entry.settings;
          Object.assign(fields, {
            Reliability: qos.reliability,
            History: qos.history === "KEEP_LAST" ? `${qos.history} · depth ${qos.history_depth}` : qos.history,
            Durability: qos.durability,
            Deadline: duration(qos.deadline_seconds),
            Liveliness: `${qos.liveliness} · lease ${duration(qos.lease_seconds)}`,
          });
        }
        const list = document.createElement("dl");
        for (const [name, value] of Object.entries(fields)) {
          const term = document.createElement("dt");
          term.textContent = name;
          const definition = document.createElement("dd");
          definition.textContent = value;
          list.append(term, definition);
        }
        section.appendChild(list);
        details.appendChild(section);
      }
      deliveryConfigTopics.appendChild(details);
    }
  }

  function percentClass(value) {
    if (value < 80) return "bad";
    if (value < 90) return "warn";
    return "";
  }

  function renderDeliveryComparison(rows) {
    const groups = [
      ["reliable", deliveryReliableSummary, "15 s grace"],
      ["best_effort", deliveryPeriodicSummary, "2 s grace"],
    ];
    for (const [reliability, element, grace] of groups) {
      const all = rows.filter((row) => row.reliability === reliability);
      const selected = all.filter((row) => row.coverage_complete !== false);
      const settled = selected.reduce((sum, row) => sum + (row.settled || 0), 0);
      const received = selected.reduce((sum, row) => sum + (row.settled_received || 0), 0);
      const lost = selected.reduce((sum, row) => sum + (row.lost || 0), 0);
      const ready = selected.filter((row) => row.percentage_ready).length;
      const label = reliability === "reliable" ? "Reliable" : "Best Effort";
      element.innerHTML = `<strong>${received}/${settled}</strong> settled received · ` +
        `<strong>${lost}</strong> lost · ${ready}/${all.length} flows ready · ${grace} · ${label}` +
        (all.length > selected.length ? ` · ${all.length - selected.length} capture-unknown flows excluded` : "");
    }
  }

  function renderDelivery(snapshot) {
    latestSnapshot = snapshot || latestSnapshot;
    const rows = latestSnapshot?.rows || [];
    rebuildNodeFilterOptions(rows);
    rebuildEndpointFilter(deliverySourceFilter, new Set(rows.map(row => row.source)), "All sources");
    rebuildEndpointFilter(deliveryDestinationFilter, new Set(rows.map(row => row.recipient)), "All destinations");
    rebuildTopicFilter(rows);
    renderConfiguredQos();
    const selectedNode = deliveryNodeFilter.value || "all";
    const selectedResolution = deliveryResolutionFilter.value || "all";
    const filteredRows = rows.filter((row) =>
      rowMatchesNodeFilter(row, selectedNode) && rowMatchesResolutionFilter(row, selectedResolution)
      && (deliverySourceFilter.value === "all" || row.source === deliverySourceFilter.value)
      && (deliveryDestinationFilter.value === "all" || row.recipient === deliveryDestinationFilter.value)
      && (selectedTopics === null || selectedTopics.has(row.topic)));
    renderDeliveryComparison(filteredRows);
    const windowSeconds = Math.round((latestSnapshot?.window_ms || 30_000) / 1000);
    deliveryWindow.textContent = `Rolling ${windowSeconds} s`;
    const capture = latestSnapshot?.capture;
    document.getElementById("delivery-capture-status").textContent =
      `Fleet-wide capture diagnostics: ${capture?.contributors?.length || 0} fresh` +
      (capture?.complete && capture?.capture_complete && capture?.wire_delivery_enabled
        ? " · captures live" : " · incomplete/unavailable coverage") +
      ` · ${capture?.wire_unknown_destinations || 0} unsupported destinations` +
      ` · ${capture?.wire_incomplete_fragments || 0} incomplete RTPS samples` +
      ` · ${latestSnapshot?.invalid_records || 0} invalid observations` +
      ` · kernel capture drops unreported · ${latestSnapshot?.unknown_qos_flows || 0} Unknown QoS flows`;
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
      const routeGroup = `${row.reliability || "unknown"}|${row.source || "Unknown"} -> ${row.recipient || "Unknown"}`;
      if (!routeGroups.has(routeGroup)) routeGroups.set(routeGroup, []);
      routeGroups.get(routeGroup).push(row);
    }

    let previousReliability = null;
    const labels = { reliable: "Reliable", best_effort: "Best Effort", unknown: "Unknown QoS" };
    const order = { reliable: 0, best_effort: 1, unknown: 2 };
    for (const [key, groupRows] of [...routeGroups].sort((a, b) =>
      (order[a[1][0].reliability] ?? 2) - (order[b[1][0].reliability] ?? 2) || a[0].localeCompare(b[0]))) {
      const reliability = groupRows[0].reliability || "unknown";
      const routeGroup = key.split("|")[1];
      if (reliability !== previousReliability) {
        const heading = document.createElement("tr");
        heading.className = "delivery-reliability-group";
        heading.dataset.reliability = reliability;
        const cell = document.createElement("th");
        cell.colSpan = 5;
        cell.textContent = labels[reliability] || labels.unknown;
        heading.appendChild(cell);
        deliveryBody.appendChild(heading);
        previousReliability = reliability;
      }
      const groupRow = document.createElement("tr");
      groupRow.className = "delivery-route-group";
      const groupCell = document.createElement("td");
      groupCell.colSpan = 5;
      groupCell.textContent = `${routeGroup} (${groupRows.length} topics)`;
      groupRow.appendChild(groupCell);
      deliveryBody.appendChild(groupRow);
      const groupKey = key.replace(/[^a-zA-Z0-9_-]/g, "_");
      let expanded = true;
      groupRow.classList.add("expanded");
      for (const row of groupRows) {
      const tableRow = document.createElement("tr");
      tableRow.className = "delivery-topic-row";
      tableRow.dataset.reliability = reliability;
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
      missing.textContent = row.coverage_complete === false ? "Unknown" : String(row.lost ?? row.missing);
      tableRow.appendChild(missing);
      const recovery = document.createElement("td");
      recovery.className = "delivery-recovery";
      recovery.textContent = "—";
      recovery.title = "Reliable samples only; no confirmed recoveries observed in this window.";
      if (reliability === "reliable") {
        if (row.coverage_complete === false) {
          recovery.textContent = "Unknown";
          recovery.title = "Source or destination WAN capture coverage is incomplete within this window.";
        } else if (Number.isFinite(row.avg_recovery_ms) && row.recovered_samples > 0) {
          const duration = row.avg_recovery_ms >= 1000
            ? `${(row.avg_recovery_ms / 1000).toFixed(2)} s`
            : `${row.avg_recovery_ms.toFixed(1)} ms`;
          recovery.textContent = `${duration} (${row.recovered_samples})`;
          recovery.title = `Mean original WAN send to complete WAN receipt for ${row.recovered_samples} samples received after a confirmed resend.`;
        }
      } else {
        recovery.title = "Recovery timing applies only to Reliable topics.";
      }
      tableRow.appendChild(recovery);
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
      refreshDeliveryConfig();
    }
    if (name === "experiment") refreshExperimentController();
    window.dispatchEvent(new CustomEvent("act-workspace-change", { detail: { name } }));
  }

  function refreshDelivery() {
    return fetch(deliveryUrl)
      .then((response) => response.ok ? response.json() : Promise.reject(response.status))
      .then(renderDelivery)
      .catch(() => {});
  }

  function refreshDeliveryConfig() {
    if (configRequest) return configRequest;
    deliveryConfigStatus.textContent = "Loading configured WAN QoS…";
    configRequest = fetch("/api/delivery_config")
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then(config => {
        if (typeof config.available !== "boolean" || !config.topics || typeof config.topics !== "object") {
          throw new Error("Invalid configured WAN QoS response");
        }
        deliveryConfig = config;
        renderDelivery(latestSnapshot);
      })
      .catch(error => {
        deliveryConfig = {available: false, topics: {},
          reason: `Configured WAN QoS unavailable: ${error.message}`};
        renderDelivery(latestSnapshot);
        console.warn("Delivery configuration request failed", error);
      })
      .finally(() => { configRequest = null; });
    return configRequest;
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
    refreshExperimentConfiguration();
    Promise.all([
      fetch(controllerUrl).then((response) => response.json()),
      fetch(`${location.protocol}//${location.host}/api/mesh_status`).then((response) => response.json()),
    ]).then(([status, mesh]) => {
      rebuildExperimentNodeOptions(mesh);
      setExperimentEnabled(Boolean(status.available), status.unavailable_reason);
      renderExperimentState(status);
    }).catch(() => setExperimentEnabled(false));
  }

  async function refreshExperimentConfiguration() {
    const version = ++configurationRequestVersion;
    let status;
    try {
      const response = await fetch(configurationUrl);
      if (!response.ok) throw new Error(`Configuration API returned HTTP ${response.status}`);
      status = await response.json();
    } catch (error) {
      status = {available: false, unavailable_reason: error.message};
    }
    if (version !== configurationRequestVersion) return;
    configurationValues.replaceChildren();
    configurationParameters.replaceChildren();
    configurationValues.hidden = !status.available;
    configurationDetails.hidden = !status.available;
    configurationStatus.classList.toggle("ready", Boolean(status.available));
    if (!status.available) {
      configurationStatus.textContent = `Runtime configuration unavailable: ${status.unavailable_reason || "No runtime snapshot provided"}`;
      return;
    }
    configurationStatus.textContent = `${status.observer_node} / NEM ${status.nem_id} only - read ${new Date(status.checked_at_ms).toLocaleTimeString()} (cached up to ${status.cache_ttl_ms / 1000} s). Other nodes may differ.`;
    const components = status.components || {};
    const value = (component, parameter) => components[component]?.[parameter] ?? "Unavailable";
    const scaled = (component, parameter, divisor, unit) => {
      const raw = components[component]?.[parameter];
      if (raw == null || raw.trim() === "" || !Number.isFinite(Number(raw))) return "Unavailable";
      return `${Number((Number(raw) / divisor).toPrecision(8))} ${unit}`;
    };
    const models = status.models || {};
    const modelNames = {rfpipemaclayer: "RF Pipe", transvirtual: "Virtual TAP", commeffectshim: "CommEffect"};
    const model = (component) => {
      const library = models[component];
      return library ? `${modelNames[library] || library} (${library})` : "Not present";
    };
    const rows = [
      ["MAC model", model("mac")],
      ["Transport model", model("transport")],
      ["Shim model", model("shim0")],
      ["MAC nominal rate", scaled("mac", "datarate", 1e6, "Mbps")],
      ["Virtual transport rate", scaled("transport", "bitrate", 1e6, "Mbps")],
      ["RF channel width", scaled("phy", "bandwidth", 1e6, "MHz")],
      ["RF frequency", scaled("phy", "frequency", 1e9, "GHz")],
      ["TX power", scaled("phy", "txpower", 1, "dBm")],
      ["System noise figure", scaled("phy", "systemnoisefigure", 1, "dB")],
      ["MAC delay", scaled("mac", "delay", 0.001, "ms")],
      ["MAC jitter", scaled("mac", "jitter", 0.001, "ms")],
      ["Propagation / fading", `${value("phy", "propagationmodel")} / ${value("phy", "fading.model")}`],
      ["Noise mode", value("phy", "noisemode")],
      ["CommEffect default connectivity", value("shim0", "defaultconnectivitymode")],
      ["CommEffect promiscuous mode", value("shim0", "enablepromiscuousmode")],
    ];
    for (const [label, text] of rows) {
      const entry = document.createElement("div");
      const term = document.createElement("dt");
      const description = document.createElement("dd");
      term.textContent = label;
      description.textContent = text;
      entry.append(term, description);
      configurationValues.appendChild(entry);
    }
    for (const [component, parameters] of Object.entries(components)) {
      for (const [parameter, text] of Object.entries(parameters)) {
        const row = document.createElement("tr");
        for (const cellText of [component, parameter, text || "(empty)"]) {
          const cell = document.createElement("td");
          cell.textContent = cellText;
          row.appendChild(cell);
        }
        configurationParameters.appendChild(row);
      }
    }
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
        const boat = missionContext.platforms.find((entry) => entry.platform === node);
        option.textContent = boat ? `${node} (${teamNames[boat.squad] || boat.squad || "Unassigned"})` : node;
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
    experimentCurrentNetwork.textContent = observed && !observed.stale
      ? `MAC, ${observed.contributors.length} nodes${observed.complete ? "" : " (partial)"}: TX ${observed.mac_tx_packets_per_second.toFixed(1)} pkt/s, RX ${observed.mac_rx_packets_per_second.toFixed(1)} pkt/s, drops ${(observed.mac_rx_drops_per_second + observed.mac_tx_drops_per_second).toFixed(1)}/s`
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
    renderDelivery(latestSnapshot);
  });
  deliveryResolutionFilter.addEventListener("change", () => renderDelivery(latestSnapshot));
  deliverySourceFilter.addEventListener("change", () => renderDelivery(latestSnapshot));
  deliveryDestinationFilter.addEventListener("change", () => renderDelivery(latestSnapshot));
  document.getElementById("delivery-topics-all").addEventListener("click", () => {
    selectedTopics = null;
    renderDelivery(latestSnapshot);
  });
  document.getElementById("delivery-topics-clear").addEventListener("click", () => {
    selectedTopics = new Set();
    renderDelivery(latestSnapshot);
  });
  selectTab("mesh");
  updateExperimentKindFields();

  function connectDeliverySocket() {
    const socket = new WebSocket(websocketUrl);
    socket.onopen = () => {
      if (document.body.dataset.activeTab === "delivery") refreshDeliveryConfig();
    };
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
