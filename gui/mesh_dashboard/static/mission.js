(function () {
  "use strict";
  const map = window.MissionMap;
  const style = window.ActMissionStyle;
  const ns = "http://www.w3.org/2000/svg";
  const inventoryEl = document.getElementById("mission-inventory");
  const teamsEl = document.getElementById("mission-teams");
  const mapEl = document.getElementById("mission-map");
  const statusEl = document.getElementById("mission-status");
  const planEl = document.getElementById("mission-plan-state");
  const hintEl = document.getElementById("mission-hint");
  const timeScaleEl = document.getElementById("mission-time-scale");
  const applyTimeEl = document.getElementById("mission-apply-time-scale");
  const clockStateEl = document.getElementById("mission-clock-state");
  const loiterRadiusEl = document.getElementById("mission-loiter-radius");
  const scenarioStatusEl = document.getElementById("mission-scenario-status");
  const menu = document.getElementById("mission-context");
  const teams = [
    { id: "TEAM_A", name: "Alpha", color: style.teamColor("TEAM_A") },
    { id: "TEAM_B", name: "Bravo", color: style.teamColor("TEAM_B") },
    { id: "TEAM_C", name: "Reserve", color: style.teamColor("TEAM_C") },
    { id: "", name: "Unassigned", color: style.unassignedColor },
  ];
  const rows = new Map(), boats = new Map(), teamButtons = new Map();
  const commandPushVersions = new Map();
  let watchedCommands = [];
  let menuState = null;
  let inventory = [], commands = [], selected = new Set(), selectedTeam = null;
  let route = [], drafting = false, validated = null, draftVersion = 0;
  let scenarioTargets = [], draftTarget = null;
  let busy = false, refreshing = false, loaded = false, gesture = null;
  let lastPushAt = 0, lastHistoryAt = 0, pushRevision = 0;
  let view = { x: 0, y: 0, width: 1000, height: 700 };
  const pendingAssignments = new Map();
  const svg = element("svg", { viewBox: "0 0 1000 700", role: "img", "aria-label": "San Diego coastal mission" });
  const background = element("g"), activeLayer = element("g"), draftLayer = element("g");
  const waypointLayer = element("g"), boatLayer = element("g");
  const targetLayer = element("g");
  const clusterLayer = element("g");
  let baseLabel = null;
  svg.append(background, activeLayer, draftLayer, waypointLayer, targetLayer, boatLayer, clusterLayer);
  mapEl.appendChild(svg);

  function element(tag, attributes = {}, text) {
    const node = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function notify(message) { watchedCommands = []; statusEl.textContent = message; }
  function teamFor(id) { return teams.find((team) => team.id === id) || { name: id, color: "#617c8e" }; }
  function pointFor(boat) {
    const p = boat.position || {};
    return Number.isFinite(p.latitude) && Number.isFinite(p.longitude)
      ? map.project([p.latitude, p.longitude]) : null;
  }
  function screenPoint(event) {
    const matrix = svg.getScreenCTM();
    return matrix ? new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse()) : null;
  }
  function inside(point) { return point && point.x >= 0 && point.x <= 1000 && point.y >= 0 && point.y <= 700; }
  function invalidate() { draftVersion++; validated = null; menu.hidden = true; }
  function emitState() {
    const state = { platforms: inventory, commands, selected: [...selected], team: selectedTeam };
    window.ActMissionState = state;
    window.dispatchEvent(new CustomEvent("act-mission-state", { detail: state }));
  }
  function setSelection(ids, team = null) {
    const next = new Set(ids);
    const changed = next.size !== selected.size || [...next].some((id) => !selected.has(id));
    selected = next; selectedTeam = team;
    if (changed) invalidate();
    menu.hidden = true;
    render(); emitState();
  }

  function createDraft(target = null) {
    if (!selected.size) { notify("Select boats or a team first."); return; }
    const fleet = inventory.filter((boat) => selected.has(boat.platform));
    const team = fleet[0]?.squad;
    if (!team || !fleet.every((boat) => boat.squad === team)) {
      notify("Assign the selected boats to one team first."); return;
    }
    setSelection(inventory.filter((boat) => boat.squad === team).map((boat) => boat.platform), team);
    draftTarget = target;
    if (draftTarget && !scenarioTargets.find((target) => target.id === draftTarget)?.revealed) {
      notify("Activate this target from the scenario controller first."); return;
    }
    route = []; drafting = true; invalidate(); menu.hidden = true;
    notify(draftTarget ? "Target mission: Validate the water approach and continuous orbit, then Go." :
      "Mission draft: click water destinations, drag waypoints to edit, then Validate mission.");
    render();
  }

  function initializeMap() {
    background.appendChild(element("rect", { width: 1000, height: 700, fill: "#202323" }));
    for (let x = 0; x <= 1000; x += 100) background.appendChild(element("path", {
      d: `M${x} 0 V700`, stroke: "#283032", "stroke-width": .7,
    }));
    for (let y = 0; y <= 700; y += 100) background.appendChild(element("path", {
      d: `M0 ${y} H1000`, stroke: "#283032", "stroke-width": .7,
    }));
    background.appendChild(element("path", {
      d: map.landPath(), fill: "#070a0b", stroke: "#526164", "stroke-width": 1.5, "fill-rule": "evenodd",
    }));
    background.appendChild(element("text", { x: 30, y: 40, fill: "#859599", "font-size": 16 }, "PACIFIC OCEAN"));
    const city = map.project([32.7157, -117.1611]);
    background.appendChild(element("text", { x: city.x, y: city.y - 24, fill: "#859599", "font-size": 16 }, "SAN DIEGO"));
    const base = element("g", { class: "mission-base", transform: `translate(${city.x} ${city.y})` });
    base.append(element("title", {}, "Control_20 · fixed demo shore base, not a boat position"),
      element("rect", { x: -12, y: -12, width: 24, height: 24, rx: 3, fill: style.controlColor, stroke: "#f0faff" }));
    baseLabel = element("text", { x: 18, y: 4, fill: "#f0faff", "font-size": 11, "font-weight": 700 }, "P20");
    base.appendChild(baseLabel); background.appendChild(base);
    background.appendChild(element("text", { x: 20, y: 676, fill: "#91a3a6", "font-size": 11 },
      "Natural Earth 1:10m · offline coastline · demo only, not for navigation"));
  }

  function drawRoute(layer, points, active, color) {
    if (!points.length) return;
    const line = element("polyline", {
      points: points.map((p) => `${p.x},${p.y}`).join(" "),
      class: `mission-route${active ? " active" : ""}`,
    });
    if (color) line.style.stroke = color;
    layer.appendChild(line);
  }

  function renderRoutes() {
    activeLayer.replaceChildren(); draftLayer.replaceChildren();
    for (const boat of inventory) {
      const command = boat.mission?.command ||
        commands.find((entry) => entry.command_id === boat.mission?.mission_id);
      const start = pointFor(boat);
      if (start && command?.waypoints?.length && ["accepted", "accepted_late"].includes(command.state) &&
          boat.mission?.mission_id === command.command_id &&
          !["IDLE", "CANCELLED", "COMPLETE"].includes(boat.mission?.phase)) {
        const points = boat.mission.phase === "ORBIT" && command.loop_start !== undefined
          ? command.waypoints.slice(Math.max(0, command.loop_start - 1)).map(map.project)
          : [start, ...command.waypoints.slice(boat.mission.waypoint_index || 0).map(map.project)];
        drawRoute(activeLayer, points, true, teamFor(boat.squad).color);
      }
    }
    if (validated) {
      for (const plan of validated.plans) {
        const boat = inventory.find((entry) => entry.platform === plan.target), start = boat && pointFor(boat);
        if (start) drawRoute(draftLayer, [start, ...plan.waypoints.map(map.project)], false, "#83dcf1");
      }
    } else if (route.length) {
      for (const boat of inventory.filter((entry) => selected.has(entry.platform))) {
        const start = pointFor(boat);
        if (start) drawRoute(draftLayer, [start, ...route], false, "#4b819b");
      }
    }
    const markers = [...waypointLayer.children];
    while (markers.length > route.length) markers.pop().remove();
    route.forEach((point, index) => {
      let marker = markers[index];
      if (!marker) {
        marker = element("g", { "data-waypoint": index });
        marker.append(element("rect", { x: -10, y: -10, width: 20, height: 20,
          fill: "transparent", class: "mission-waypoint-hit" }),
          element("circle", { r: 3, class: "mission-waypoint" }),
          element("text", { x: 6, y: -5, fill: "#d7efff", "font-size": 9 }, String(index + 1)));
        waypointLayer.appendChild(marker);
      }
      marker.setAttribute("transform", `translate(${point.x} ${point.y})`);
    });
    layoutWaypointMarkers();
  }

  function render() {
    for (const target of scenarioTargets) {
      const button = document.getElementById(target.id === "target_1" ? "mission-target-1" : "mission-target-2");
      button.textContent = `${target.label}: ${target.revealed ? "On" : "Off"}`;
      button.setAttribute("aria-pressed", String(Boolean(target.revealed)));
      button.disabled = busy;
    }
    document.getElementById("mission-setup").hidden = !drafting;
    document.getElementById("mission-loiter-control").hidden = !drafting || !draftTarget;
    document.getElementById("mission-destination-state").textContent = drafting
      ? `Mission: ${draftTarget ? scenarioTargets.find((target) => target.id === draftTarget)?.label || draftTarget : "Waypoints"}` : "";
    loiterRadiusEl.disabled = busy;
    const zoomSelection = document.getElementById("mission-zoom-selection");
    zoomSelection.hidden = !selected.size;
    zoomSelection.disabled = busy;
    scenarioStatusEl.textContent = "Toggle targets on or off to show or hide them. Existing missions are unchanged.";
    if (loaded) {
      targetLayer.replaceChildren();
      for (const target of scenarioTargets.filter((target) => target.revealed)) {
        const point = map.project([target.latitude, target.longitude]);
        const group = element("g", { class: "mission-target", "data-target": target.id,
          transform: `translate(${point.x} ${point.y})` });
        group.append(element("circle", { r: 9, fill: "#e6553a", stroke: "#ffd4c9", "stroke-width": 1.5 }),
          element("path", { d: "M-14 0 H14 M0 -14 V14", stroke: "#e6553a", "stroke-width": 1 }),
          element("text", { x: 16, y: -10, fill: "#ffb6a5", "font-size": 12 }, target.label));
        targetLayer.appendChild(group);
      }
    }
    const current = new Set(inventory.map((boat) => boat.platform));
    for (const [id, row] of rows) if (!current.has(id)) { row.root.remove(); rows.delete(id); }
    for (const [id, group] of boats) if (!current.has(id)) { group.remove(); boats.delete(id); }
    for (const boat of inventory) {
      let row = rows.get(boat.platform);
      if (!row) {
        const root = document.createElement("div"); root.className = "mission-row";
        root.dataset.platform = boat.platform;
        root.tabIndex = 0; root.setAttribute("role", "option");
        const info = document.createElement("span"); info.className = "mission-row-info";
        const name = document.createElement("strong"), details = document.createElement("small");
        const ack = document.createElement("small");
        const toggle = () => {
          if (busy) return;
          const ids = new Set(selected);
          if (ids.has(boat.platform)) ids.delete(boat.platform); else ids.add(boat.platform);
          setSelection(ids);
        };
        root.addEventListener("click", toggle);
        root.addEventListener("contextmenu", (event) => contextMenu(event));
        root.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); }
        });
        info.append(name, details, ack); root.append(info); inventoryEl.appendChild(root);
        row = { root, name, details, ack }; rows.set(boat.platform, row);
      }
      const mission = boat.mission || {}, last = commands.find((entry) => entry.target === boat.platform);
      const battery = Number.isFinite(boat.battery_pct) ? `${Math.round(boat.battery_pct)}% battery` : "Battery unknown";
      const etaSeconds = ["TRANSIT", "RTB"].includes(mission.phase) && Number.isFinite(mission.distance_to_waypoint_m) &&
        boat.time_scale && boat.position?.speed_knots > 0
        ? mission.distance_to_waypoint_m / (boat.position.speed_knots * 1852 / 3600 * boat.time_scale) : null;
      const eta = etaSeconds === null ? "" : ` · Next WP ~${etaSeconds >= 60 ? `${Math.ceil(etaSeconds / 60)} min` : `${Math.ceil(etaSeconds)}s`} demo`;
      row.name.textContent = `${boat.display_name} · ${teamFor(boat.squad).name}`;
      const destination = scenarioTargets.find((target) => target.id === mission.target_id)?.label;
      const navigation = { BUFFER_HOLD: "300 ft buffer hold", WAITING_PEERS: "Waiting for shared boat locations",
        STALE_PEERS: "Holding: shared locations stale" }[mission.navigation_state];
      row.details.textContent = `${mission.phase || "IDLE"}${navigation ? ` · ${navigation}` : ""}${destination ? ` · ${destination}` : ""}${mission.loiter_radius_m ? ` · ${mission.loiter_radius_m / 1000} km loiter` : ""} · ${mission.waypoint_index || 0}/${mission.waypoints_total || 0}${eta} · ${battery} · ${boat.available ? "Connected" : "Stale/offline"}`;
      row.ack.textContent = boat.inheritance_error ? `Mission inheritance: ${boat.inheritance_error}` :
        pendingAssignments.has(boat.platform) ? "Team change pending DDS confirmation" :
        (last ? `Command ${last.state}${last.late_ack ? " (late acknowledgement)" : ""}${last.state === "timed_out" ? ": No acknowledgement within 15s" : ""}${last.ack?.message ? `: ${last.ack.message}` : ""}${last.error ? `: ${last.error}` : ""}` : boat.health);
      row.root.classList.toggle("selected", selected.has(boat.platform));
      row.root.setAttribute("aria-selected", String(selected.has(boat.platform)));
      row.root.setAttribute("aria-disabled", String(busy));
      const point = pointFor(boat);
      if (!point || !loaded) continue;
      let group = boats.get(boat.platform);
      if (!group) {
        group = element("g", { "data-platform": boat.platform });
        group.append(element("circle", { r: 17, fill: "none", "stroke-width": 2 }),
          element("polygon", { points: "0,-16 12,13 0,9 -12,13" }),
          element("text", { x: 17, y: 5, fill: "#f0faff", "font-size": 11, "font-weight": 700 },
            boat.platform.replace("Platform_", "P")));
        boatLayer.appendChild(group); boats.set(boat.platform, group);
      }
      group.setAttribute("class", `mission-boat${selected.has(boat.platform) ? " selected" : ""}${boat.available ? "" : " unavailable"}`);
      group.setAttribute("transform", `translate(${point.x} ${point.y})`);
      group.children[0].setAttribute("stroke", teamFor(boat.squad).color);
      group.children[1].setAttribute("transform", `rotate(${boat.position.heading_deg || 0})`);
      const color = boat.available ? teamFor(boat.squad).color : "#59656f";
      group.children[1].style.fill = color;
      group.children[1].style.filter = boat.available ? `drop-shadow(0 0 3px ${color})` : "none";
      positionIconLabel(group.children[2], false);
    }
    if (loaded) layoutBoatMarkers();
    const outcomes = watchedCommands.map((id) => commands.find((command) => command.command_id === id));
    if (outcomes.length && outcomes.every(Boolean)) {
      const unresolved = outcomes.filter((command) => command.state === "pending").length;
      const errors = outcomes.filter((command) => !["pending", "accepted"].includes(command.state));
      statusEl.textContent = unresolved ? `${unresolved} boat acknowledgements pending.` :
        errors.length ? errors.map((command) => `${command.target}: ${command.state}`).join("; ") :
        `All ${outcomes.length} boats acknowledged. Live mission progress is shown below.`;
    }
    for (const team of teams) {
      const count = inventory.filter((boat) => (boat.squad || "") === team.id).length;
      const button = teamButtons.get(team.id);
      button.textContent = `${team.name} · ${count} boats`;
      button.setAttribute("aria-pressed", String(selectedTeam === team.id)); button.disabled = busy;
    }
    if (validated && (validated.expires_at <= Date.now() ||
        JSON.stringify(inventory.filter((boat) => boat.squad === validated.team).map((boat) => boat.platform).sort()) !==
          JSON.stringify(validated.plans.map((plan) => plan.target).sort()) ||
        [...selected].some((id) => !inventory.find((boat) => boat.platform === id)?.available))) {
      invalidate(); notify("Validation expired, the team roster changed, or a boat is offline. Validate again.");
    }
    const actions = actionState(), { replaces, pending } = actions;
    if (!menu.hidden && menuState !== actionSignature(actions)) menu.hidden = true;
    const clockScales = new Set(inventory.map((boat) => boat.time_scale));
    clockStateEl.textContent = inventory.length && clockScales.size === 1 && Number.isFinite(inventory[0].time_scale)
      ? `${inventory[0].time_scale}× confirmed on ${inventory.length} boats · Autonomous navigation · 300 ft minimum buffer`
      : "Clock not confirmed for all boats · Set a rate to synchronize the fleet.";
    applyTimeEl.textContent = `Set ${timeScaleEl.value}×`;
    applyTimeEl.disabled = busy || !inventory.length || inventory.some((boat) => !boat.available);
    applyTimeEl.hidden = inventory.length > 0 && clockScales.size === 1 &&
      inventory[0].time_scale === Number(timeScaleEl.value);
    timeScaleEl.disabled = busy;
    svg.style.cursor = gesture ? "grabbing" : "grab";
    hintEl.dataset.stage = busy || pending ? "waiting" : validated ? "go" : drafting ? "draft" :
      replaces ? "running" : selected.size ? "setup" : scenarioTargets.some((target) => target.revealed) ? "select" : "activate";
    const assignedTeam = actions.fleet.length > 0 && actions.fleet[0].squad &&
      actions.fleet.every((boat) => boat.squad === actions.fleet[0].squad);
    hintEl.textContent = busy ? "Working · Waiting for the current action to finish." :
      pending ? "Commands sent · Waiting for boat acknowledgements. No need to resend." :
      validated ? "Step 6 · Water routes validated. Click Go to start the mission." :
      drafting && draftTarget ? "Step 5 · Choose the loiter distance, then Validate. Go starts the team circling the unknown target." :
      drafting && route.length ? "Step 5 · Validate mission to avoid land. Drag a waypoint to edit; right-click to remove." :
      drafting ? "Step 4 · Click the map to place the first waypoint. Choose water, not land." :
      replaces ? (actions.fleet.every((boat) => boat.mission?.phase === "ORBIT") ? "Loitering" :
        actions.fleet.every((boat) => boat.mission?.phase === "RTB") ? "Returning to base" : "Mission running") +
        " · Right-click to set a replacement mission, cancel, or return to base." :
      (selectedTeam !== null || assignedTeam) && selected.size ? "Step 3 · Right-click → Set mission → Draw waypoints or Select target." :
      selected.size ? "Step 2 · Right-click the selected boats and assign Alpha, Bravo, or Reserve." :
      !scenarioTargets.some((target) => target.revealed) ? "Start · Toggle a target On, or select boats to draw a mission." :
      "Step 1 · Click a boat to select; Shift-click adds or removes boats. Or choose a team. Left-drag pans.";
    planEl.textContent = validated ? `Validated ${draftTarget ? "continuous orbit" : "water route"} · ${validated.plans.reduce((n, plan) => n + plan.waypoints.length, 0)} waypoints · Ready to Go` :
      (drafting ? `Draft · ${draftTarget ? "Target orbit" : `${route.length}/64 destinations`} · ${selected.size} boats · Not validated` :
        replaces ? "Live mission running" : "No mission draft");
    document.getElementById("mission-create").textContent = replaces ? "Replace mission" : "Set mission";
    for (const [id, available] of [
      ["mission-create", actions.setMission], ["mission-validate", actions.validate],
      ["mission-go", actions.go], ["mission-remove-waypoint", actions.edit],
      ["mission-clear-route", actions.edit], ["mission-cancel", actions.cancel],
      ["mission-rtb", actions.rtb],
    ]) {
      const button = document.getElementById(id);
      button.hidden = !available; button.disabled = !available;
    }
    if (loaded && !gesture) renderRoutes();
  }

  function replacesPhase(phase) { return ["TRANSIT", "HOLDING", "RTB", "ORBIT"].includes(phase); }

  function actionState() {
    const fleet = inventory.filter((boat) => selected.has(boat.platform));
    const pending = commands.some((command) => selected.has(command.target) && command.state === "pending");
    const ready = !busy && !pending && selected.size > 0 && fleet.length === selected.size &&
      fleet.every((boat) => boat.available && !pendingAssignments.has(boat.platform));
    const replaces = fleet.some((boat) => replacesPhase(boat.mission?.phase));
    const away = fleet.some((boat) => boat.position && boat.home &&
      (Math.abs(boat.position.latitude - boat.home.latitude) > .00002 ||
       Math.abs(boat.position.longitude - boat.home.longitude) > .00002));
    const team = fleet[0]?.squad;
    const teamReady = ready && team && fleet.every((boat) => boat.squad === team) &&
      inventory.filter((boat) => boat.squad === team).every((boat) => boat.available && boat.team_confirmed &&
        !pendingAssignments.has(boat.platform) &&
        !commands.some((command) => command.target === boat.platform && command.state === "pending"));
    return { fleet, ready, pending, replaces,
      assign: ready && !drafting,
      setMission: Boolean(teamReady && !drafting),
      validate: Boolean(teamReady && drafting && !validated && (route.length || draftTarget)),
      go: Boolean(teamReady && drafting && validated && validated.expires_at > Date.now()),
      edit: !busy && drafting && !draftTarget && route.length > 0 && !validated,
      cancel: ready && !drafting && replaces,
      rtb: ready && !drafting && away,
    };
  }

  function actionSignature(actions) {
    const { fleet, ...availability } = actions;
    return JSON.stringify([availability, [...selected].sort(), fleet.map((boat) => boat.squad),
      scenarioTargets.map((target) => [target.id, target.revealed])]);
  }

  async function request(url, body) {
    const response = await fetch(url, body === undefined ? undefined : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const text = await response.text();
    if (!response.ok) {
      let message = text;
      if (response.headers.get("content-type")?.includes("application/json")) {
        const value = JSON.parse(text); message = value.error || value.message || text;
      }

      throw new Error(message || `HTTP ${response.status}`);
    }
    return JSON.parse(text);
  }

  function applyFleet(fleet) {
      inventory = fleet.platforms;
      scenarioTargets = fleet.scenario_targets || [];
      for (const [id, team] of pendingAssignments) {
        if ((inventory.find((boat) => boat.platform === id)?.squad || "") === team) pendingAssignments.delete(id);
      }
      const next = selectedTeam === null
        ? [...selected].filter((id) => inventory.some((boat) => boat.platform === id))
        : inventory.filter((boat) => (boat.squad || "") === selectedTeam).map((boat) => boat.platform);
      if (next.length !== selected.size || next.some((id) => !selected.has(id))) {
        selected = new Set(next); invalidate();
      }
      render(); emitState();
  }

  function mergeCommands(incoming) {
    const latest = new Map(commands.map((command) => [command.command_id, command]));
    const rank = { pending: 0, timed_out: 1, accepted: 2, rejected: 2, failed: 2 };
    for (const command of incoming) {
      const previous = latest.get(command.command_id);
      const timestamp = command.updated_at ?? command.created_at;
      const previousTimestamp = previous?.updated_at ?? previous?.created_at;
      if (!previous || timestamp > previousTimestamp ||
          (timestamp === previousTimestamp && rank[command.state] >= rank[previous.state])) {
        latest.set(command.command_id, command);
      }
    }
    commands = [...latest.values()].sort((a, b) => b.created_at - a.created_at).slice(0, 200);
    const retained = new Set(commands.map((command) => command.command_id));
    for (const id of commandPushVersions.keys()) if (!retained.has(id)) commandPushVersions.delete(id);
  }

  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    const revision = pushRevision;
    try {
      const [fleet, history] = await Promise.all([request("/api/mission_inventory"), request("/api/mission_commands")]);
      const pushed = commands.filter((command) => commandPushVersions.get(command.command_id) > revision);
      commands = [];
      mergeCommands([...history.commands, ...pushed]);
      lastHistoryAt = Date.now();
      if (revision === pushRevision) applyFleet(fleet);
      else { render(); emitState(); }
    } catch (error) { notify(`Live mission state unavailable: ${error.message}`); }
    finally { refreshing = false; }
  }

  async function assign(team) {
    menu.hidden = true;
    if (busy || !selected.size) return;
    const ids = [...selected]; busy = true; render();
    try {
      for (const id of ids) {
        const response = await fetch("/api/team_assignment", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ platform_node: id, team_name: team }),
        });
        if (!response.ok) throw new Error(await response.text());
        pendingAssignments.set(id, team);
      }
      notify(`Assigning ${ids.length} boats to ${teamFor(team).name}; waiting for live DDS membership.`);
      const deadline = Date.now() + 15000;
      while (ids.some((id) => pendingAssignments.has(id)) && Date.now() < deadline) {
        await refresh(); await new Promise((resolve) => setTimeout(resolve, 300));
      }
      if (ids.some((id) => pendingAssignments.has(id))) throw new Error("Team confirmation timed out; see each boat's live membership.");
      notify(`${ids.length} boats confirmed in ${teamFor(team).name}.`);
      invalidate();
    } catch (error) { notify(`Team assignment: ${error.message}`); }
    finally { busy = false; render(); }
  }

  function showMenu(items, position) {
    menu.replaceChildren();
    if (!items.length) { menu.hidden = true; return; }
    for (const item of items) {
      const button = document.createElement("button"); button.type = "button";
      button.setAttribute("role", "menuitem"); button.textContent = item.label;
      button.disabled = Boolean(item.disabled);
      button.addEventListener("click", item.action); menu.appendChild(button);
    }
    menu.hidden = false;
    menuState = actionSignature(actionState());
    menu.style.left = `${Math.max(0, Math.min(position.x, window.innerWidth - menu.offsetWidth))}px`;
    menu.style.top = `${Math.max(0, Math.min(position.y, window.innerHeight - menu.offsetHeight))}px`;
    menu.querySelector("button:not(:disabled)")?.focus();
  }

  function missionChoices(position) {
    showMenu([
      { label: "Draw waypoints", action: () => createDraft() },
      ...(scenarioTargets.some((target) => target.revealed) ? [
        { label: "Select target", action: () => showMenu(scenarioTargets.filter((target) => target.revealed).map((target) => ({
        label: target.label,
        action: () => createDraft(target.id),
      })), position) }] : []),
    ], position);
  }

  function contextMenu(event, team) {
    event.preventDefault();
    if (busy) return;
    const waypoint = event.target.closest("[data-waypoint]")?.dataset.waypoint;
    if (waypoint !== undefined) {
      showMenu([{ label: "Remove waypoint", action: () => {
        route.splice(Number(waypoint), 1); invalidate(); menu.hidden = true; render();
      } }], { x: event.clientX, y: event.clientY });
      return;
    }
    if (team !== undefined) setSelection(inventory.filter((boat) => (boat.squad || "") === team).map((boat) => boat.platform), team);
    else {
      const cluster = event.target.closest("[data-platforms]")?.dataset.platforms;
      const node = event.target.closest("[data-platform]")?.dataset.platform;
      if (cluster) setSelection(cluster.split(","));
      else if (node && !selected.has(node)) setSelection([node]);
    }
    const position = { x: event.clientX, y: event.clientY };
    const actions = actionState();
    showMenu([
      ...(actions.setMission ? [{ label: "Set mission", action: () => missionChoices(position) }] : []),
      ...(actions.validate ? [{ label: "Validate mission", action: validate }] : []),
      ...(actions.go ? [{ label: "Go", action: () => send("route") }] : []),
      ...(actions.go && !draftTarget ? [{ label: "Edit waypoints", action: () => { invalidate(); render(); } }] : []),
      ...(drafting ? [{ label: "Discard draft", action: () => {
        drafting = false; route = []; invalidate(); notify("Mission draft discarded."); render();
      } }] : []),
      ...(actions.cancel ? [{ label: "Cancel mission", action: () => send("cancel") }] : []),
      ...(actions.rtb ? [{ label: "Return to base", action: () => send("return_to_base") }] : []),
      ...(actions.assign ? teams.filter((entry) => !actions.fleet.every((boat) => (boat.squad || "") === entry.id))
        .map((entry) => ({ label: `Assign to ${entry.name}`, action: () => assign(entry.id) })) : []),
    ], position);
  }

  async function validate() {
    if (busy || (!route.length && !draftTarget) || !selected.size) return;
    const team = inventory.find((boat) => selected.has(boat.platform))?.squad;
    if (!team) { notify("Assign the selected boats to a team first."); return; }
    setSelection(inventory.filter((boat) => boat.squad === team).map((boat) => boat.platform), team);
    const version = draftVersion; busy = true; render();
    try {
      const result = await request("/api/mission_validate", {
        targets: [...selected], team,
        ...(draftTarget ? { target_id: draftTarget, loiter_radius_m: Number(loiterRadiusEl.value) } :
          { waypoints: route.map(map.coordinate) }),
      });
      if (version !== draftVersion) throw new Error("Draft changed during validation; validate again.");
      validated = { ...result, team, expires_at: result.expires_at || Date.now() + 300000 };
      notify(draftTarget ? `Validated: ${loiterRadiusEl.value / 1000} km loiter distance. Click Go.` :
        "Mission validated. Water detours are shown; click Go to publish commands.");
    } catch (error) { validated = null; notify(`Validation: ${error.message}`); }
    finally { busy = false; render(); }
  }

  async function send(operation) {
    if (busy || !selected.size) return;
    if (operation === "route" && !validated) { notify("Validate the mission before Go."); return; }
    busy = true; render();
    try {
      const replaces = operation === "route" && inventory.some((boat) => selected.has(boat.platform) &&
        replacesPhase(boat.mission?.phase));
      const result = await request("/api/mission_commands", operation === "route" ?
        { operation: replaces ? "replace_route" : "route", plan_id: validated.plan_id } : { operation, targets: [...selected] });
      const errors = result.errors || [];
      notify(`${result.commands.filter((command) => command.published).length} commands published; awaiting platform acknowledgements.` +
        (errors.length ? ` Errors: ${errors.map((entry) => `${entry.target}: ${entry.message}`).join("; ")}` : ""));
      if (!errors.length) watchedCommands = result.commands.map((command) => command.command_id);
      invalidate();
      if (!errors.length) { drafting = false; route = []; }
      await refresh();
    } catch (error) { invalidate(); notify(`Command: ${error.message}`); }
    finally { busy = false; render(); }
  }

  async function setTimeScale() {
    if (busy || !inventory.length) return;
    busy = true; render();
    try {
      const result = await request("/api/mission_commands", {
        operation: "set_time_scale", targets: inventory.map((boat) => boat.platform),
        time_scale: Number(timeScaleEl.value),
      });
      notify(`Demo clock command sent to ${result.commands.filter((command) => command.published).length} boats. ` +
        (result.errors.length ? result.errors.map((error) => `${error.target}: ${error.message}`).join("; ") :
          "Waiting for DDS confirmation; positions and progress use accelerated simulator time."));
      await refresh();
    } catch (error) { notify(`Demo clock: ${error.message}`); }
    finally { busy = false; render(); }
  }

  async function revealTarget(id) {
    const visible = !scenarioTargets.find((target) => target.id === id)?.revealed;
    busy = true; render();
    try {
      await request("/api/mission_scenario", { target_id: id, visible });
      notify(`Target ${visible ? "on" : "off"} requested. Waiting for DDS confirmation.`);
      await refresh();
    } catch (error) { notify(`Scenario: ${error.message}`); }
    finally { busy = false; render(); }
  }

  function positionIconLabel(label, base) {
    const inset = view.width <= 600;
    label.setAttribute("x", inset ? 0 : (base ? 18 : 17));
    label.setAttribute("y", inset ? 4 : 5);
    label.setAttribute("text-anchor", inset ? "middle" : "start");
    label.setAttribute("font-size", inset ? (base ? 9 : 9.5) : 11);
    label.setAttribute("paint-order", "stroke");
    label.setAttribute("stroke", "#111820");
    label.setAttribute("stroke-width", inset ? 1.2 : .3);
  }
  function layoutWaypointMarkers() {
    const matrix = svg.getScreenCTM();
    if (!matrix) return;
    const unit = 1 / Math.hypot(matrix.a, matrix.b);
    for (const marker of waypointLayer.children) {
      const transform = marker.transform.baseVal.getItem(0).matrix;
      marker.setAttribute("transform", `translate(${transform.e} ${transform.f}) scale(${unit})`);
    }
  }
  function layoutBoatMarkers() {
    const matrix = svg.getScreenCTM();
    if (!matrix) return;
    const unit = 1 / Math.hypot(matrix.a, matrix.b), gap = 4 * unit;
    const overlaps = (a, b) => a.x < b.x + b.width + gap && a.x + a.width + gap > b.x &&
      a.y < b.y + b.height + gap && a.y + a.height + gap > b.y;
    for (const node of [...targetLayer.children, ...background.querySelectorAll(".mission-base")]) {
      const transform = node.transform.baseVal.getItem(0).matrix;
      node.setAttribute("transform", `translate(${transform.e} ${transform.f}) scale(${unit})`);
    }
    clusterLayer.replaceChildren();
    const groups = [];
    for (const boat of inventory) {
      const group = boats.get(boat.platform), actual = pointFor(boat);
      if (!group || !actual) continue;
      group.setAttribute("transform", `translate(${actual.x} ${actual.y}) scale(${unit})`);
      const visible = actual.x >= view.x && actual.x <= view.x + view.width &&
        actual.y >= view.y && actual.y <= view.y + view.height;
      group.setAttribute("visibility", visible ? "visible" : "hidden");
      if (!visible) continue;
      const box = group.getBBox();
      const left = Math.min(box.x, -26), top = Math.min(box.y, -26);
      const entry = { boat, actual, group, rect: { x: actual.x + left * unit, y: actual.y + top * unit,
        width: (Math.max(box.x + box.width, 26) - left) * unit,
        height: (Math.max(box.y + box.height, 26) - top) * unit } };
      const joined = groups.filter((members) => members.some((other) => overlaps(entry.rect, other.rect)));
      for (const members of joined) groups.splice(groups.indexOf(members), 1);
      groups.push([entry, ...joined.flat()].sort((a, b) => a.boat.platform.localeCompare(b.boat.platform)));
    }
    for (const members of groups.filter((entries) => entries.length > 1)) {
      for (const entry of members) entry.group.setAttribute("visibility", "hidden");
      const anchor = members[0], ids = members.map((entry) => entry.boat.platform);
      const sameTeam = members.every((entry) => entry.boat.squad === anchor.boat.squad);
      const color = sameTeam ? teamFor(anchor.boat.squad).color : "#687f8f";
      const cluster = element("g", { class: "mission-cluster", "data-platforms": ids.join(","),
        transform: `translate(${anchor.actual.x} ${anchor.actual.y}) scale(${unit})`,
        role: "button", tabindex: 0, "aria-label": `${ids.join(", ")}: zoom to ${ids.length} boats` });
      cluster.append(element("title", {}, `${ids.map((id) => id.replace("Platform_", "P")).join(", ")} · Click to zoom`),
        element("circle", { r: 18, fill: color, stroke: "#d7efff", "stroke-width": 1.5 }),
        element("text", { y: 4, "text-anchor": "middle", fill: "#fff", "font-size": 12, "font-weight": 700 }, String(ids.length)));
      cluster.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault(); if (!busy) zoomToBoats(ids);
        }
      });
      clusterLayer.appendChild(cluster);
    }
    const meters = 100 * unit * .6 / 1000 * 111195 * Math.cos(32.725 * Math.PI / 180);
    document.getElementById("mission-map-scale").textContent = meters >= 1000
      ? `${(meters / 1000).toFixed(1)} km` : `${Math.round(meters)} m`;
  }
  function zoomToBoats(ids) {
    const points = inventory.filter((boat) => ids.includes(boat.platform)).map(pointFor).filter(Boolean);
    if (!points.length) { notify("No reported positions for the selected boats."); return; }
    const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const width = Math.max(1, Math.min(1000, Math.max((maxX - minX) * 1.8, (maxY - minY) * 1.8 / .7)));
    view = { x: Math.max(0, Math.min(1000 - width, (minX + maxX - width) / 2)),
      y: Math.max(0, Math.min(700 - width * .7, (minY + maxY - width * .7) / 2)), width, height: width * .7 };
    applyView();
  }
  function applyView() {
    svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.width} ${view.height}`);
    layoutWaypointMarkers();
    for (const boat of boats.values()) positionIconLabel(boat.children[2], false);
    if (baseLabel) positionIconLabel(baseLabel, true);
    if (loaded) layoutBoatMarkers();
  }
  function zoom(factor, point) {
    const width = Math.max(1, Math.min(1000, view.width * factor)), ratio = width / view.width;
    const center = point || { x: view.x + view.width / 2, y: view.y + view.height / 2 };
    view = { x: center.x - (center.x - view.x) * ratio, y: center.y - (center.y - view.y) * ratio,
      width, height: width * .7 };
    view.x = Math.max(-view.width / 2, Math.min(1000 - view.width / 2, view.x));
    view.y = Math.max(-view.height / 2, Math.min(700 - view.height / 2, view.y)); applyView();
  }

  svg.addEventListener("contextmenu", (event) => contextMenu(event));
  svg.addEventListener("pointerdown", (event) => {
    if (busy || !loaded || (event.button !== 0 && event.button !== 1)) return;
    menu.hidden = true;
    const cluster = event.target.closest("[data-platforms]")?.dataset.platforms;
    const target = event.target.closest("[data-target]")?.dataset.target;
    const point = screenPoint(event); if (!point) return;
    const index = event.target.closest("[data-waypoint]")?.dataset.waypoint;
    const boat = event.target.closest("[data-platform]")?.dataset.platform;
    gesture = { start: point, current: point, screenX: event.clientX, screenY: event.clientY,
      moved: false, shift: event.shiftKey, view: { ...view }, boat, cluster, target,
      unit: 1 / Math.hypot(svg.getScreenCTM().a, svg.getScreenCTM().b),
      type: event.altKey || event.button === 1 ? "pan" : index !== undefined ? "waypoint" : "map",
      index: index === undefined ? null : Number(index) };
    svg.setPointerCapture(event.pointerId); event.preventDefault();
  });
  svg.addEventListener("pointermove", (event) => {
    if (!gesture) return;
    const point = screenPoint(event); if (!point) return;
    gesture.current = point;
    if (Math.hypot(event.clientX - gesture.screenX, event.clientY - gesture.screenY) > 4) gesture.moved = true;
    if (gesture.type === "pan" || (gesture.type === "map" && gesture.moved)) {
      view.x = Math.max(-view.width / 2, Math.min(1000 - view.width / 2, gesture.view.x - (event.clientX - gesture.screenX) * gesture.unit));
      view.y = Math.max(-view.height / 2, Math.min(700 - view.height / 2, gesture.view.y - (event.clientY - gesture.screenY) * gesture.unit)); applyView();
    } else if (gesture.type === "waypoint" && inside(point)) {
      route[gesture.index] = { x: point.x, y: point.y }; invalidate(); renderRoutes();
    }
  });
  svg.addEventListener("pointerup", (event) => {
    if (!gesture) return;
    const g = gesture; gesture = null;
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    if (g.type === "map" && !g.moved && g.cluster) {
      zoomToBoats(g.cluster.split(","));
    } else if (g.type === "map" && !g.moved && g.target) {
      render(); return;
    } else if (g.type === "map" && !g.moved && g.boat) {
      const ids = g.shift ? new Set(selected) : new Set();
      if (g.shift && ids.has(g.boat)) ids.delete(g.boat); else ids.add(g.boat);
      setSelection(ids);
    } else if (g.type === "map" && !g.moved) {
      if (drafting && !draftTarget) {
        if (!inside(g.current)) notify("Waypoint outside offline map coverage. Choose a destination within the map.");
        else if (route.length >= 64) notify("Maximum 64 destinations.");
        else { route.push({ x: g.current.x, y: g.current.y }); invalidate(); }
      } else if (!drafting) {
        setSelection(g.shift ? selected : []);
      }
    }
    render();
  });
  svg.addEventListener("pointercancel", () => { gesture = null; render(); });
  svg.addEventListener("wheel", (event) => { event.preventDefault(); zoom(event.deltaY < 0 ? .8 : 1.25, screenPoint(event)); }, { passive: false });
  document.addEventListener("pointerdown", (event) => { if (!menu.contains(event.target)) menu.hidden = true; });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      menu.hidden = true;
      if (drafting && !busy) { drafting = false; route = []; invalidate(); notify("Mission draft discarded."); }
      render();
    }
    if (event.key === "Backspace" && drafting && !busy && event.target === document.body) {
      event.preventDefault(); route.pop(); invalidate(); render();
    }
  });
  for (const team of teams) {
    const button = document.createElement("button"); button.type = "button"; button.className = "mission-team";
    button.dataset.team = team.id; button.style.setProperty("--team-color", team.color);
    button.addEventListener("click", () => setSelection(inventory.filter((boat) => (boat.squad || "") === team.id).map((boat) => boat.platform), team.id));
    button.addEventListener("contextmenu", (event) => contextMenu(event, team.id));
    teamsEl.appendChild(button); teamButtons.set(team.id, button);
  }
  document.getElementById("mission-create").addEventListener("click", (event) => {
    const box = event.currentTarget.getBoundingClientRect();
    missionChoices({ x: box.right, y: box.top });
  });
  applyTimeEl.addEventListener("click", setTimeScale);
  timeScaleEl.addEventListener("change", render);
  loiterRadiusEl.addEventListener("change", () => { invalidate(); render(); });
  document.getElementById("mission-target-1").addEventListener("click", () => revealTarget("target_1"));
  document.getElementById("mission-target-2").addEventListener("click", () => revealTarget("target_2"));
  document.getElementById("mission-validate").addEventListener("click", validate);
  document.getElementById("mission-go").addEventListener("click", () => send("route"));
  document.getElementById("mission-cancel").addEventListener("click", () => send("cancel"));
  document.getElementById("mission-rtb").addEventListener("click", () => send("return_to_base"));
  document.getElementById("mission-remove-waypoint").addEventListener("click", () => { route.pop(); invalidate(); render(); });
  document.getElementById("mission-clear-route").addEventListener("click", () => { route = []; invalidate(); render(); });
  document.getElementById("mission-zoom-in").addEventListener("click", () => zoom(.8));
  document.getElementById("mission-zoom-out").addEventListener("click", () => zoom(1.25));
  document.getElementById("mission-zoom-selection").addEventListener("click", () => zoomToBoats([...selected]));
  document.getElementById("mission-map-reset").addEventListener("click", () => { view = { x: 0, y: 0, width: 1000, height: 700 }; applyView(); });
  window.addEventListener("act-workspace-change", () => { menu.hidden = true; refresh(); });
  window.addEventListener("resize", applyView);
  window.addEventListener("act-dashboard-message", (event) => {
    if (!loaded) return;
    const message = event.detail;
    if (message.type === "mission_inventory" && Array.isArray(message.data?.platforms)) {
      lastPushAt = Date.now(); pushRevision++; applyFleet(message.data);
    } else if (message.type === "mission_command" && message.data?.command_id) {
      lastPushAt = Date.now(); pushRevision++;
      commandPushVersions.set(message.data.command_id, pushRevision);
      mergeCommands([message.data]);
      render(); emitState();
    }
  });
  map.load().then(() => {
    loaded = true; initializeMap(); notify("Select boats and assign a team to start."); return refresh();
  }).catch((error) => notify(`Coastline unavailable: ${error.message}`));
  render();
  setInterval(() => {
    if (loaded && (Date.now() - lastPushAt > 2500 || Date.now() - lastHistoryAt >= 5000 ||
        commands.some((command) => command.state === "pending"))) refresh();
  }, 1000);
})();
