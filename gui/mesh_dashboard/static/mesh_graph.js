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

  // Edge color encodes source, not presence (2026-07-23) -- green for a direct mesh_status
  // edge (this router's own peers list), blue for a relayed peers_seen edge (a peer's own
  // reported roster, one hop further out). Presence still surfaces via decay/opacity, the
  // edge label/title, and the node detail panel -- it just no longer has its own hue.
  // peers_seen's blue is deliberately NOT KNOWN_NODE_COLOR (also blue, below) -- reusing
  // that exact shade would make a relayed *edge* look like it means "heard directly", the
  // opposite of what KNOWN_NODE_COLOR means for a *node*.
  const EDGE_SOURCE_COLOR = {
    mesh_status: "#3aa655",
    peers_seen: "#3a7bd9",
  };
  const KNOWN_NODE_COLOR = "#4a90d9";
  const PLACEHOLDER_NODE_COLOR = "#888888";
  const RECONNECT_DELAY_MS = 3000;

  // Edge decay (2026-07-23): fade an edge toward EDGE_MIN_OPACITY as it ages, floor reached
  // at EDGE_DECAY_WINDOW_MS -- deliberately the same 3s window as the AUTOMATIC liveliness
  // lease (D75), so a fully-faded edge lines up with when presence itself would flip to
  // STALE/DEAD. asOfMs is the wall-clock point the edge's data was last known-current, not
  // "when the browser happened to receive a message" -- see the two upsertEdge call sites.
  const EDGE_DECAY_WINDOW_MS = 3000;
  const EDGE_MIN_OPACITY = 0.15;
  const EDGE_DECAY_TICK_MS = 300;

  // Pin the C2/control node at a fixed spot above the force-directed layout, per the
  // user's "simplify the GUI" ask -- a minimal anchor rather than switching the whole
  // graph to vis's hierarchical layout (which would misrepresent this mesh's peer-to-peer
  // edges as parent/child ranks). The observer node IS the C2/control node here, always --
  // wis_config.xml.template hardcodes this dashboard to control_lan (domain 20), so
  // whichever router's admin LAN participant WIS subscribes to is definitionally the
  // control node (see that template's header comment). RouterHealth.role ("control" vs
  // "platform", RouterAdminTypes.idl) is also checked on peers as a defense-in-depth
  // fallback, though in this dashboard's fixed control_lan deployment a peer can never
  // legitimately carry role "control" -- only the observer can. x for the peer-role case
  // is a deterministic hash of the node id (same technique as colorForTeam) so multiple
  // control-role nodes don't land on top of each other.
  const C2_PIN_X = 0;
  const C2_PIN_Y = -400;
  const C2_PIN_X_SLOTS = 5;
  const C2_PIN_X_SPACING = 220;
  const OBSERVER_PIN_FIELDS = { x: C2_PIN_X, y: C2_PIN_Y, fixed: { x: true, y: true } };

  function c2PinFields(health) {
    if (!health || health.role !== "control") return null;
    const id = health.router;
    let hash = 0;
    for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
    const slot = (hash % C2_PIN_X_SLOTS) - Math.floor(C2_PIN_X_SLOTS / 2);
    return { x: slot * C2_PIN_X_SPACING, y: C2_PIN_Y, fixed: { x: true, y: true } };
  }

  // Two-tier hierarchy (2026-07-23, "C2 on top, platforms below"): pin every non-control
  // node's y into a fixed band below C2, same anchor-not-engine approach as C2_PIN_Y above
  // and for the same reason -- vis's real hierarchical layout mode derives rank from edge
  // direction, which would misrepresent a platform-to-platform peers_seen edge as a
  // parent/child relationship instead of a peer one. Only y is fixed; x stays physics-driven
  // (barnesHut) so nodes still spread out and settle within the row instead of stacking.
  const PLATFORM_PIN_Y = 200;
  const PLATFORM_PIN_FIELDS = { y: PLATFORM_PIN_Y, fixed: { x: false, y: true } };

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
      edges: {
        width: 2, smooth: { type: "continuous" },
        font: { size: 9, color: "#9aa0a8", strokeWidth: 0, align: "top" },
      },
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

  // opts.source labels which relationship produced this edge, drives both the label (shown
  // on the graph, not just on hover) and the color (EDGE_SOURCE_COLOR) -- "mesh_status"
  // (observer's own direct peers list) vs "peers_seen" (a peer's own embedded roster, one
  // hop further out). opts.asOfMs anchors decay, derived the same way for both: the wire's
  // own last_seen_delta_ms (D97 added this to RouterPeerRef too -- a duration, not a
  // timestamp, so no clock-sync assumption either way).
  function upsertEdge(fromId, toId, presence, opts) {
    opts = opts || {};
    const baseColor = EDGE_SOURCE_COLOR[opts.source] || "#999999";
    edges.update({
      id: `${fromId}->${toId}`, from: fromId, to: toId, arrows: "to",
      label: opts.source || "",
      color: { color: baseColor, opacity: 1 },
      baseColor, asOfMs: opts.asOfMs != null ? opts.asOfMs : Date.now(),
      title: opts.title || presence,
    });
  }

  function pruneStaleEdgesFrom(fromId, currentTargets) {
    edges.get({ filter: (e) => e.from === fromId }).forEach((e) => {
      if (!currentTargets.has(e.to)) edges.remove(e.id);
    });
  }

  // Runs independently of sample ingest -- a roster that's gone quiet (no new sample, so no
  // upsertEdge call) still needs its edges to visibly age between updates, not just jump
  // stale the instant the next sample finally arrives.
  function tickEdgeDecay() {
    const now = Date.now();
    const updates = [];
    edges.get().forEach((e) => {
      if (e.asOfMs == null || !e.baseColor) return;
      const frac = Math.max(0, Math.min(1, (now - e.asOfMs) / EDGE_DECAY_WINDOW_MS));
      updates.push({ id: e.id, color: { color: e.baseColor, opacity: 1 - frac * (1 - EDGE_MIN_OPACITY) } });
    });
    if (updates.length) edges.update(updates);
  }
  setInterval(tickEdgeDecay, EDGE_DECAY_TICK_MS);

  // data is one ActRouterMeshStatus sample: {observer_node, observer_router, state_revision,
  // peers: [{health: <full RouterHealth>, presence, last_seen_delta_ms}, ...]}.
  function upsertMeshStatusSample(data) {
    if (!data || !data.observer_node || !data.observer_router) return;
    const observerId = `${data.observer_node}/${data.observer_router}`;
    nodes.update({ id: observerId, label: observerId, color: KNOWN_NODE_COLOR,
                   title: "(this dashboard's own vantage point -- the C2/control node)",
                   kind: "observer", ...OBSERVER_PIN_FIELDS });

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
        ...PLATFORM_PIN_FIELDS,
        ...(c2PinFields(health) || {}),
      });
      upsertEdge(observerId, peerId, peerEntry.presence, {
        title: `${peerEntry.presence}, last_seen ${peerEntry.last_seen_delta_ms} ms ago`,
        source: "mesh_status",
        asOfMs: Date.now() - (peerEntry.last_seen_delta_ms || 0),
      });

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
                       kind: "placeholder", knownVia: peerId, ...PLATFORM_PIN_FIELDS });
        }
        upsertEdge(peerId, subPeer.router, subPeer.presence, {
          title: `${subPeer.presence}, last_seen (from ${peerId}): ${subPeer.last_seen_delta_ms} ms ago`,
          source: "peers_seen",
          asOfMs: Date.now() - (subPeer.last_seen_delta_ms || 0),
        });
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
