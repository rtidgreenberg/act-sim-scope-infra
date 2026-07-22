// mesh_graph.js — Phase 16 v1 mesh dashboard (docs/cpp_router/implementation-plan.md#phase-16,
// design: docs/cpp_router/gui-visualization.md). Renders the router mesh as seen from ONE
// node's admin LAN participant (colocated deployment, control_lan by default): one node per
// router identity ("<node>/<router>"), edges from the "who reports whom" relationships in
// ActRouterMeshStatus. Trusts presence/overall_state as authoritative -- never recomputes
// liveliness timing client-side (Tenet 9).
//
// LAN-side, not WAN (scope correction, 2026-07-21 -- see wis_config.xml.template's header
// comment for why): ActRouterMeshStatus is the LAN aggregate roster -- each entry is a
// peer's COMPLETE last RouterHealth summary (not the WAN topic's trimmed peers_seen refs),
// so this page gets "full resolution" per-node detail plus, as a bonus, each peer's own
// embedded peers_seen roster -- enough to reconstruct the fuller multi-hop graph, not just
// a star centered on the observing node, from a single LAN vantage point.
//
// Data source: RTI Web Integration Service, same REST + WebSocket protocol proven end to
// end by spikes/wis_mesh_dashboard/ (PASSED 2026-07-21, against RouterHealth -- the
// REST/WebSocket mechanics are identical for any topic/type, only the config's domain/topic
// changed). Assumes this page is served by WIS's own -documentRoot (same-origin), so
// host/port are read from window.location rather than hardcoded.

(function () {
  "use strict";

  const WIS_APP = "MeshDashboardApp";
  const WIS_PARTICIPANT = "MeshParticipant";
  const WIS_SUBSCRIBER = "MeshSubscriber";
  const WIS_READER = "MeshStatusReader";
  const READER_URI = `/dds/rest1/applications/${WIS_APP}/domain_participants/${WIS_PARTICIPANT}` +
                      `/subscribers/${WIS_SUBSCRIBER}/data_readers/${WIS_READER}`;

  const HTTP_ORIGIN = `${location.protocol}//${location.host}`;
  const WS_ORIGIN = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}`;
  const REST_URL = `${HTTP_ORIGIN}${READER_URI}?sampleFormat=json&removeFromReaderCache=false`;
  const WS_CREATE_URL = `${HTTP_ORIGIN}/dds/v1/websocket_connections`;

  // WIS's own manual documents `{"name": ...}` for this POST body; the real 7.7.0 service
  // only accepts the array-wrapped form -- confirmed empirically, logged in
  // docs/connext-ai-issues/connext-ai-issues.md (2026-07-21).
  const WS_CONTENT_TYPE = "application/dds-web+json";

  const PRESENCE_COLOR = {
    PRESENCE_ALIVE: "#3aa655",
    PRESENCE_STALE: "#d9a441",
    PRESENCE_DEAD: "#c0392b",
  };
  const KNOWN_NODE_COLOR = "#4a90d9";
  const PLACEHOLDER_NODE_COLOR = "#888888";
  const RECONNECT_DELAY_MS = 3000;

  // Team-membership ring (follow-on to v1's presence-only graph, 2026-07-21). RouterHealth
  // now carries team_partition -- team_wan's raw participant-partition set (D83's
  // single-mechanism design means it mixes the node's own protected identity, an optional
  // team name, and any ad-hoc direct-peer-tap names, with NO structural tag telling them
  // apart). Classification happens here, not on the wire: subtract every identity we know
  // to be a node's own ("<node>/<router>" split on "/") and whatever's left is treated as
  // team name(s). A team deliberately or accidentally named the same as a real node's own
  // identity string will misclassify as "no team" -- a known, accepted edge case (see the
  // IDL comment on RouterHealth.team_partition), not a bug to chase here.
  const NO_TEAM_BORDER = "#3a3f4a";
  const TEAM_PALETTE = [
    "#e6553a", "#3aa1e6", "#e6c53a", "#8a4ae6",
    "#3ae6a1", "#e63a8a", "#a1e63a", "#3ae6e6",
  ];
  const teamColors = new Map(); // team name -> assigned palette color
  const teamLegendEl = document.getElementById("team-legend");

  // Team filter (interactivity, 2026-07-21): click a team's legend chip to highlight only
  // that team's nodes (others dimmed). Multi-select — click several to widen the set;
  // click an active one to remove it; empty set = show everything. Filtering is a pure
  // client-side view over the same samples — it never changes what's subscribed.
  const activeTeamFilter = new Set();
  const DIM_OPACITY = 0.2;

  function colorForTeam(team) {
    if (teamColors.has(team)) return teamColors.get(team);
    let hash = 0;
    for (let i = 0; i < team.length; i++) {
      hash = (hash * 31 + team.charCodeAt(i)) >>> 0;
    }
    const color = TEAM_PALETTE[hash % TEAM_PALETTE.length];
    teamColors.set(team, color);
    const span = document.createElement("span");
    span.innerHTML = `<i style="background:${color}"></i> ${team}`;
    span.style.cursor = "pointer";
    span.title = "click to filter the graph to this team";
    span.addEventListener("click", () => {
      if (activeTeamFilter.has(team)) {
        activeTeamFilter.delete(team);
        span.classList.remove("active");
      } else {
        activeTeamFilter.add(team);
        span.classList.add("active");
      }
      applyTeamFilter();
    });
    teamLegendEl.appendChild(span);
    return color;
  }

  // Dim every node whose team set doesn't intersect the active filter. The observer node
  // (this vantage point) is always kept full — it's "you", relevant regardless of filter.
  // Empty filter = everything full. Called after every ingest so newly-arrived nodes
  // respect the current filter too.
  function applyTeamFilter() {
    const updates = [];
    nodes.get().forEach((n) => {
      let full = true;
      if (activeTeamFilter.size > 0 && n.kind !== "observer") {
        const teams = n.teamNames || [];
        full = teams.some((t) => activeTeamFilter.has(t));
      }
      updates.push({ id: n.id, opacity: full ? 1 : DIM_OPACITY });
    });
    if (updates.length) nodes.update(updates);
  }

  // Every node id in the graph is "<node>/<router>" -- the node-name half is what
  // team_partition's protected/direct-tap entries actually contain (D83: "${node.name}",
  // not the full router identity).
  function knownNodeNames() {
    const names = new Set();
    nodes.get().forEach((n) => {
      const slash = String(n.id).indexOf("/");
      if (slash > 0) names.add(n.id.slice(0, slash));
    });
    return names;
  }

  // extraNodeNames covers this same sample's peers before they're written into `nodes`
  // (upsertMeshStatusSample processes one sample's peers in one pass, so the dataset may
  // not yet contain a sibling peer this one's team_partition happens to name as a direct tap).
  function deriveTeamNames(health, extraNodeNames) {
    if (!health || !Array.isArray(health.team_partition)) return [];
    const ownNode = health.router.slice(0, health.router.indexOf("/"));
    const known = knownNodeNames();
    return health.team_partition.filter(
      (entry) => entry !== ownNode && !known.has(entry) && !extraNodeNames.has(entry)
    );
  }

  function teamBorder(teamNames) {
    return teamNames.length ? colorForTeam(teamNames[0]) : NO_TEAM_BORDER;
  }

  const statusEl = document.getElementById("statusbar");
  function setStatus(text) {
    statusEl.textContent = text;
  }

  const nodes = new vis.DataSet();
  const edges = new vis.DataSet();
  const network = new vis.Network(
    document.getElementById("graph"),
    { nodes, edges },
    {
      nodes: { shape: "dot", size: 16, font: { color: "#e6e8eb" } },
      edges: { width: 2, smooth: { type: "continuous" } },
      physics: { stabilization: false, barnesHut: { springLength: 160 } },
      interaction: { hover: true },
    }
  );

  // --- Detail panel (interactivity, 2026-07-21) ---------------------------------------
  // Click a node -> side panel with its full RouterHealth. All fields already live on the
  // node object (stashed in upsertMeshStatusSample), so this reads the DataSet, not the
  // wire. selectedId lets an in-place sample update refresh the open panel live.
  const detailEl = document.getElementById("detail");
  let selectedId = null;

  function detailRow(k, v) {
    return `<div class="detail-row"><span class="k">${k}</span>` +
           `<span class="v">${v}</span></div>`;
  }

  function renderDetail(id) {
    const n = nodes.get(id);
    if (!n) { hideDetail(); return; }
    selectedId = id;
    let body = "";
    if (n.kind === "observer") {
      body = `<div class="detail-note">This dashboard's own vantage point.</div>`;
    } else if (n.kind === "placeholder") {
      body = `<div class="detail-note">Known only via another router's roster` +
             (n.knownVia ? ` (${n.knownVia})` : "") + `, not heard directly — no ` +
             `RouterHealth summary available.</div>`;
    } else if (n.health) {
      const h = n.health;
      const teams = (n.teamNames && n.teamNames.length)
        ? n.teamNames.join(", ") : "(no team)";
      const raw = (h.team_partition || []).join(", ") || "(none)";
      body =
        detailRow("role", h.role) +
        detailRow("overall_state", h.overall_state) +
        detailRow("presence", n.presence != null ? n.presence : "?") +
        detailRow("last seen", n.lastSeenMs != null ? `${n.lastSeenMs} ms ago` : "?") +
        detailRow("routes", `${h.n_routes} (${h.n_degraded} degraded, ${h.n_error} error)`) +
        detailRow("team", teams) +
        detailRow("raw team_partition", `[${raw}]`) +
        detailRow("heartbeat_seq", h.heartbeat_seq) +
        detailRow("config_hash", h.config_hash ? String(h.config_hash).slice(0, 12) + "…" : "?");
    }
    detailEl.innerHTML =
      `<span class="detail-close" title="close">×</span>` +
      `<div class="detail-title">${id}</div>` + body;
    detailEl.querySelector(".detail-close").addEventListener("click", hideDetail);
    detailEl.style.display = "block";
  }

  function hideDetail() {
    detailEl.style.display = "none";
    selectedId = null;
  }

  network.on("selectNode", (p) => { if (p.nodes.length) renderDetail(p.nodes[0]); });
  network.on("deselectNode", () => hideDetail());

  function healthTitle(health, teamNames, extra) {
    const rawPartition = (health.team_partition || []).join(", ") || "(none)";
    const team = teamNames.length ? teamNames.join(", ") : "(no team assigned)";
    return `role: ${health.role}\noverall_state: ${health.overall_state}\n` +
           `n_routes: ${health.n_routes}  n_degraded: ${health.n_degraded}  n_error: ${health.n_error}\n` +
           `heartbeat_seq: ${health.heartbeat_seq}\n` +
           `team: ${team}  (raw team_partition: [${rawPartition}])` +
           (extra ? `\n${extra}` : "");
  }

  function upsertEdge(fromId, toId, presence, title) {
    edges.update({
      id: `${fromId}->${toId}`, from: fromId, to: toId, arrows: "to",
      color: { color: PRESENCE_COLOR[presence] || "#999999" }, title: title || presence,
    });
  }

  function pruneStaleEdgesFrom(fromId, currentTargets) {
    edges.get({ filter: (e) => e.from === fromId }).forEach((e) => {
      if (!currentTargets.has(e.to)) edges.remove(e.id);
    });
  }

  // data is one ActRouterMeshStatus sample: {observer_node, observer_router, state_revision,
  // peers: [{health: <full RouterHealth>, presence, last_seen_delta_ms}, ...]}.
  function upsertMeshStatusSample(data) {
    if (!data || !data.observer_node || !data.observer_router) return;
    const observerId = `${data.observer_node}/${data.observer_router}`;
    nodes.update({ id: observerId, label: observerId, color: KNOWN_NODE_COLOR,
                   title: "(this dashboard's own vantage point)", kind: "observer" });

    const directPeers = new Set();
    const sampleNodeNames = new Set(
      (data.peers || [])
        .map((p) => p && p.health && p.health.router)
        .filter(Boolean)
        .map((r) => r.slice(0, r.indexOf("/")))
    );
    (data.peers || []).forEach((peerEntry) => {
      const health = peerEntry && peerEntry.health;
      if (!health || !health.router) return;
      const peerId = health.router;
      directPeers.add(peerId);

      // Full per-peer detail -- the "full resolution" ActRouterMeshStatus carries that the
      // WAN RouterHealth topic's trimmed peers_seen refs don't.
      const teamNames = deriveTeamNames(health, sampleNodeNames);
      nodes.update({
        id: peerId, label: peerId,
        color: { background: KNOWN_NODE_COLOR, border: teamBorder(teamNames) },
        borderWidth: 4,
        title: healthTitle(health, teamNames,
                            `last_seen (from ${observerId}): ${peerEntry.last_seen_delta_ms} ms ago`),
        // Stashed for the detail panel (interactivity) + team filter — read back from the
        // DataSet on click, so the panel never re-parses the wire.
        kind: "peer", health: health, teamNames: teamNames,
        presence: peerEntry.presence, lastSeenMs: peerEntry.last_seen_delta_ms,
      });
      upsertEdge(observerId, peerId, peerEntry.presence,
                 `${peerEntry.presence}, last_seen ${peerEntry.last_seen_delta_ms} ms ago`);

      // Bonus: each peer's own embedded heartbeat (health) carries ITS OWN peers_seen
      // roster -- reconstructs the fuller multi-hop mesh graph from one LAN vantage point,
      // not just a star centered on the observer.
      const subPeers = new Set();
      (health.peers_seen || []).forEach((subPeer) => {
        if (!subPeer || !subPeer.router) return;
        subPeers.add(subPeer.router);
        if (!nodes.get(subPeer.router)) {
          nodes.add({ id: subPeer.router, label: subPeer.router, color: PLACEHOLDER_NODE_COLOR,
                       title: "(only known via another router's own roster, not directly)",
                       kind: "placeholder", knownVia: peerId });
        }
        upsertEdge(peerId, subPeer.router, subPeer.presence);
      });
      pruneStaleEdgesFrom(peerId, subPeers);
    });
    pruneStaleEdgesFrom(observerId, directPeers);
  }

  function ingestSampleArray(samples) {
    if (!Array.isArray(samples)) return 0;
    let n = 0;
    samples.forEach((s) => {
      if (s && s.data) {
        upsertMeshStatusSample(s.data);
        n++;
      }
    });
    if (n) {
      applyTeamFilter();                       // new nodes respect the active filter
      if (selectedId) renderDetail(selectedId); // live-refresh an open panel in place
    }
    return n;
  }

  async function seedFromRest() {
    try {
      const resp = await fetch(REST_URL);
      if (!resp.ok) {
        setStatus(`REST seed failed: HTTP ${resp.status}`);
        return;
      }
      const n = ingestSampleArray(await resp.json());
      setStatus(`Seeded ${n} sample(s) from REST — connecting WebSocket…`);
    } catch (err) {
      setStatus(`REST seed error: ${err} — connecting WebSocket…`);
    }
  }

  function connectWebSocket() {
    const connName = "MeshDashboardWs_" + Math.random().toString(36).slice(2, 10);
    fetch(WS_CREATE_URL, {
      method: "POST",
      headers: { "Content-Type": WS_CONTENT_TYPE },
      body: JSON.stringify([{ name: connName }]),
    })
      .then((resp) => {
        if (resp.status !== 204) {
          setStatus(`WebSocket connection creation failed: HTTP ${resp.status} — retrying…`);
          setTimeout(connectWebSocket, RECONNECT_DELAY_MS);
          return;
        }
        openSocket(connName);
      })
      .catch((err) => {
        setStatus(`WebSocket connection creation error: ${err} — retrying…`);
        setTimeout(connectWebSocket, RECONNECT_DELAY_MS);
      });
  }

  function openSocket(connName) {
    const ws = new WebSocket(`${WS_ORIGIN}/dds/websocket/${connName}`);

    ws.onopen = () => {
      // WIS's own application-level handshake (not part of RFC 6455 -- the browser already
      // handled the real WS upgrade). Sequence + exact HELLO fields verified against the
      // real running service by spikes/wis_mesh_dashboard/ws_probe.py (PASSED 2026-07-21);
      // OMG-DDS-API-Key is mandatory even empty, or the server replies "HELLO FAIL".
      ws.send(
        "Content-Type:application/dds-web+json\r\n" +
        "Accept:application/dds-web+json\r\n" +
        "OMG-DDS-API-Key:\r\n" +
        "Version:1\r\n\r\n"
      );
      ws.send(JSON.stringify({
        kind: "bind",
        body: [{ bind_kind: "bind_datareader", bind_id: WIS_READER, uri: READER_URI }],
      }));
    };

    ws.onmessage = (evt) => {
      let msg;
      try {
        msg = JSON.parse(evt.data);
      } catch (_err) {
        return; // the plaintext "HELLO OK:..." reply isn't JSON -- ignore it
      }
      if (msg && msg.kind === "b_push" && msg.body && Array.isArray(msg.body.read_sample_seq)) {
        ingestSampleArray(msg.body.read_sample_seq);
        setStatus(`Live — last update ${new Date().toLocaleTimeString()}, ` +
                  `${nodes.length} node(s), ${edges.length} edge(s)`);
      }
    };

    ws.onclose = () => {
      setStatus("WebSocket closed — reconnecting…");
      setTimeout(connectWebSocket, RECONNECT_DELAY_MS);
    };
    ws.onerror = () => {
      setStatus("WebSocket error — reconnecting…");
    };
  }

  seedFromRest().then(connectWebSocket);
})();
