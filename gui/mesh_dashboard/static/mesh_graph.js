// mesh_graph.js — Phase 16 v1 mesh dashboard (docs/cpp_router/implementation-plan.md#phase-16,
// design: docs/cpp_router/gui-visualization.md). Renders the router mesh as seen from ONE
// node's admin LAN participant (colocated deployment, control_lan by default): one node per
// router identity ("<node>/<router>"), edges from the "who reports whom" relationships in
// ActRouterMeshStatus, arrows drawn in the direction the underlying RouterHealth/heartbeat
// traffic actually flows -- reporter <- subject, i.e. into whichever node received/"heard"
// it (2026-07-23; see upsertEdge's comment) -- not observer -> peer as a query/ownership
// arrow would read. Trusts presence/overall_state as authoritative -- never recomputes
// liveliness timing client-side (Tenet 9).
//
// LAN-side, not WAN (scope correction, 2026-07-21 -- see docs/cpp_router/gui-visualization.md
// for why): ActRouterMeshStatus is the LAN aggregate roster -- each entry is a
// peer's COMPLETE last RouterHealth summary (not the WAN topic's trimmed peers_seen refs),
// so this page gets "full resolution" per-node detail plus, as a bonus, each peer's own
// embedded peers_seen roster -- enough to reconstruct the fuller multi-hop graph, not just
// a star centered on the observing node, from a single LAN vantage point.
//
// Data source: gui/mesh_dashboard/server/mesh_bridge.py, a small purpose-built DDS<->HTTP
// bridge that replaced RTI Web Integration Service (WIS) here -- see
// docs/cpp_router/mesh-dashboard-bridge-implementation-plan.md for why (three real WIS
// bugs: an XML config-parser name collision, a REST seed that could silently return stale
// empty data, and PUT/POST verb inconsistency). REST GET seed + WebSocket push, same as
// before, but a plain `{"data": {...}}` envelope on both instead of WIS's REST1/bind-HELLO
// protocol. Assumes this page is served by the bridge's own static handler (same-origin), so
// host/port are read from window.location rather than hardcoded.

(function () {
  "use strict";

  const HTTP_ORIGIN = `${location.protocol}//${location.host}`;
  const WS_ORIGIN = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}`;
  const REST_URL = `${HTTP_ORIGIN}/api/mesh_status`;
  const WS_URL = `${WS_ORIGIN}/ws`;
  const ASSIGN_URL = `${HTTP_ORIGIN}/api/team_assignment`;

  // Edge color encodes source, not presence (2026-07-23) -- green for a direct mesh_status
  // edge (this router's own peers list), blue for a relayed peers_seen edge (a peer's own
  // reported roster, one hop further out). Presence still surfaces via decay/opacity, the
  // edge label/title, and the node detail panel -- it just no longer has its own hue.
  const EDGE_SOURCE_COLOR = {
    mesh_status: "#3aa655",
    peers_seen: "#3a7bd9",
  };
  // Node color reuses the exact same hue mapping as edge color (2026-07-23, user ask): green
  // always means "direct" (this dashboard has the node's own RouterHealth, same as a
  // mesh_status edge), blue always means "relayed/secondhand" (only known via another peer's
  // embedded peers_seen roster, same as a peers_seen edge) -- one consistent color language
  // across nodes and edges instead of two unrelated palettes.
  const OBSERVER_NODE_COLOR = "#e0b84d";
  const KNOWN_NODE_COLOR = EDGE_SOURCE_COLOR.mesh_status;
  const PLACEHOLDER_NODE_COLOR = EDGE_SOURCE_COLOR.peers_seen;
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
  // mesh_bridge.py defaults to control_lan (domain 20), so whichever router's admin LAN
  // participant the bridge subscribes to is definitionally the control node (see
  // mesh-dashboard-bridge-implementation-plan.md). RouterHealth.role ("control" vs
  // "platform", RouterAdminTypes.idl) is also checked on peers as a defense-in-depth
  // fallback, though in this dashboard's fixed control_lan deployment a peer can never
  // legitimately carry role "control" -- only the observer can. x for the peer-role case
  // is a deterministic hash of the node id (same technique as colorForTeam) so multiple
  // control-role nodes don't land on top of each other.
  const C2_PIN_X = 0;
  const C2_PIN_Y = -45;
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
  // parent/child relationship instead of a peer one.
  //
  // x used to be left physics-driven (barnesHut) so nodes would spread out within the row,
  // but with physics running continuously that meant nodes visibly jittered back and forth
  // forever hunting for equilibrium (reported 2026-07-23). Physics is now fully disabled
  // (see the `physics: { enabled: false }` network option below) and x is instead pinned
  // explicitly.
  //
  // First tried a center-out INSERTION-order scheme (2026-07-23, user ask: "start center,
  // left right alternate") -- 1st node seen at x=0, 2nd one slot right, 3rd one slot left,
  // etc. Reverted same day (user report: "not center aligned") -- that scheme is only
  // symmetric for an ODD node count; caught mid-discovery (or with an even final count) it's
  // off by exactly one slot, biased right, because "start at center" for node #1 leaves
  // nothing placed on the left until node #3 arrives. Sorting by id and recomputing every
  // node's x from scratch on every ingest (relayoutPlatformRow) instead guarantees a row
  // that's symmetric around C2's x=0 at ANY node count, not just odd ones, and it's
  // deterministic (same node set -> same layout) rather than dependent on discovery-order
  // timing. Nodes are never removed from the DataSet once seen (no nodes.remove() anywhere
  // in this file) -- the row only ever grows, so relayouting on every ingest is cheap and
  // simply idempotent when the set hasn't changed.
  const PLATFORM_PIN_Y = 35;
  const PLATFORM_PIN_X_SPACING = 210; // 140 * 1.5 (2026-07-23, user ask: widen 50%)
  const PLATFORM_ROW_Y_FIELDS = { y: PLATFORM_PIN_Y, fixed: { x: true, y: true } };

  function relayoutPlatformRow() {
    const rowIds = nodes
      .get({ filter: (n) => n.kind === "peer" || n.kind === "placeholder" })
      .map((n) => n.id)
      .sort();
    const n = rowIds.length;
    if (!n) return;
    nodes.update(
      rowIds.map((id, i) => ({
        id,
        x: (i - (n - 1) / 2) * PLATFORM_PIN_X_SPACING,
        y: PLATFORM_PIN_Y,
        fixed: { x: true, y: true },
      }))
    );
  }

  // Team-membership ring (follow-on to v1's presence-only graph, 2026-07-21). RouterHealth
  // now carries team_partition -- platform_wan's raw participant-partition set (D83's
  // single-mechanism design, absorbed from team_wan under D103, means it mixes the node's
  // own protected identity, an optional
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
  const teamChips = new Map();  // team name -> DOM span element (for pruning)
  const teamLegendEl = document.getElementById("team-legend");
  const directLinksToggle = document.getElementById("toggle-direct-links");
  const relayedLinksToggle = document.getElementById("toggle-relayed-links");

  // Team filter (interactivity, 2026-07-21): click a team's legend chip to highlight only
  // that team's nodes (others dimmed). Multi-select — click several to widen the set;
  // click an active one to remove it; empty set = show everything. Filtering is a pure
  // client-side view over the same samples — it never changes what's subscribed.
  const activeTeamFilter = new Set();
  let selectedId = null;
  const DIM_OPACITY = 0.2;
  const HIGHLIGHT_DIM_OPACITY = 0.18;

  function edgeVisibleBySource(edge) {
    if (edge.source === "mesh_status") return directLinksToggle.checked;
    if (edge.source === "peers_seen") return relayedLinksToggle.checked;
    return true;
  }

  function selectedNeighborhood() {
    if (!selectedId) return null;
    const nodeIds = new Set([selectedId]);
    const edgeIds = new Set();
    // First pass: collect direct neighbors (edges touching the selected node).
    edges.get().forEach((e) => {
      if (e.from === selectedId || e.to === selectedId) {
        nodeIds.add(e.from);
        nodeIds.add(e.to);
        edgeIds.add(e.id);
      }
    });
    // Second pass: include edges BETWEEN neighbors (2-hop closure). This makes
    // inter-peer peers_seen links visible when the observer is selected — e.g.
    // clicking Control_20 shows the dashed Platform_30↔Platform_31 edge if both
    // are in the same team / WAN partition and can see each other.
    edges.get().forEach((e) => {
      if (nodeIds.has(e.from) && nodeIds.has(e.to)) {
        edgeIds.add(e.id);
      }
    });
    return { nodeIds, edgeIds };
  }

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
    span.addEventListener("click", () => {
      if (activeTeamFilter.has(team)) {
        activeTeamFilter.delete(team);
        span.classList.remove("active");
      } else {
        activeTeamFilter.add(team);
        span.classList.add("active");
      }
      applyViewFilters();
    });
    teamLegendEl.appendChild(span);
    teamChips.set(team, span);
    return color;
  }

  // Remove team legend chips for teams that no longer have any member nodes.
  // Called after every sample ingest so the legend stays in sync with the live mesh.
  function pruneStaleTeams() {
    const liveTeams = new Set();
    nodes.get().forEach((n) => {
      (n.teamNames || []).forEach((t) => liveTeams.add(t));
    });
    teamChips.forEach((span, team) => {
      if (!liveTeams.has(team)) {
        span.remove();
        teamChips.delete(team);
        teamColors.delete(team);
        activeTeamFilter.delete(team);
      }
    });
  }

  // Dim every node whose team set doesn't intersect the active filter. The observer node
  // (this vantage point) is always kept full — it's "you", relevant regardless of filter.
  // Empty filter = everything full. Called after every ingest so newly-arrived nodes
  // respect the current filter too.
  function applyViewFilters() {
    const nodeUpdates = [];
    const edgeUpdates = [];
    const neighborhood = selectedNeighborhood();
    nodes.get().forEach((n) => {
      let full = true;
      if (activeTeamFilter.size > 0 && n.kind !== "observer") {
        const teams = n.teamNames || [];
        full = teams.some((t) => activeTeamFilter.has(t));
      }
      if (neighborhood && !neighborhood.nodeIds.has(n.id)) full = false;
      nodeUpdates.push({ id: n.id, opacity: full ? 1 : (neighborhood ? HIGHLIGHT_DIM_OPACITY : DIM_OPACITY) });
    });
    edges.get().forEach((e) => {
      const sourceVisible = edgeVisibleBySource(e);
      const selectedVisible = !neighborhood || neighborhood.edgeIds.has(e.id);
      edgeUpdates.push({
        id: e.id,
        hidden: !(sourceVisible && selectedVisible),
        color: { color: e.baseColor, opacity: sourceVisible && selectedVisible ? e.currentOpacity || 1 : HIGHLIGHT_DIM_OPACITY },
      });
    });
    if (nodeUpdates.length) nodes.update(nodeUpdates);
    if (edgeUpdates.length) edges.update(edgeUpdates);
  }

  // The wire's RouterHealth.router / observer_node+observer_router carry the full D79
  // "<node>/<router>" name-only identity, but this dashboard's graph nodes are keyed on just
  // the node half (2026-07-23, user ask: "only use node name as unique identifier -- we don't
  // need router name as well") -- valid here because this deployment is one router per node
  // (D79), so the node half alone is already unique. nodeNameOf() is the one place that split
  // happens, applied wherever a wire router-identity string becomes (or is looked up as) a
  // graph node id.
  function nodeNameOf(routerId) {
    const slash = String(routerId).indexOf("/");
    return slash > 0 ? routerId.slice(0, slash) : String(routerId);
  }

  function routerLabel(id) {
    return String(id);
  }

  // Every node id in the graph is now just the node name (nodeNameOf, above) -- which is
  // exactly what team_partition's protected/direct-tap entries contain too (D83:
  // "${node.name}"), so this is just "every id currently in the DataSet", no splitting needed.
  function knownNodeNames() {
    const names = new Set();
    nodes.get().forEach((n) => names.add(String(n.id)));
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
      nodes: { shape: "dot", size: 16, font: { size: 13, color: "#e6e8eb" } },
      edges: { width: 2, smooth: { enabled: false } },
      physics: { enabled: false },
      interaction: { hover: false },
    }
  );
  // Expose for headless Playwright e2e tests (test_team_assignment_e2e.py) — lets
  // page.evaluate() call network.getPositions() / canvasToDOM() to find node coords.
  window.__network = network;

  // --- Detail panel (interactivity, 2026-07-21) ---------------------------------------
  // Click a node -> side panel with its full RouterHealth. All fields already live on the
  // node object (stashed in upsertMeshStatusSample), so this reads the DataSet, not the
  // wire. selectedId lets an in-place sample update refresh the open panel live.
  const detailEl = document.getElementById("detail");

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
      const peersSeen = (h.peers_seen || []).map((p) => {
        const name = nodeNameOf(p.router || "?");
        const pres = (p.presence || "").replace("PRESENCE_", "");
        const ago = p.last_seen_delta_ms != null ? `${p.last_seen_delta_ms}ms` : "?";
        return `${name} (${pres}, ${ago})`;
      }).join(", ") || "(none)";
      body =
        detailRow("role", h.role) +
        detailRow("overall_state", h.overall_state) +
        detailRow("presence", n.presence != null ? n.presence : "?") +
        detailRow("last seen", n.lastSeenMs != null ? `${n.lastSeenMs} ms ago` : "?") +
        detailRow("routes", `${h.n_routes} (${h.n_degraded} degraded, ${h.n_error} error)`) +
        detailRow("team", teams) +
        detailRow("raw team_partition", `[${raw}]`) +
        detailRow("peers_seen", peersSeen) +
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
    applyViewFilters();
  }

  network.on("selectNode", (p) => {
    if (p.nodes.length) {
      renderDetail(p.nodes[0]);
      applyViewFilters();
    }
  });
  network.on("deselectNode", () => hideDetail());

  directLinksToggle.addEventListener("change", applyViewFilters);
  relayedLinksToggle.addEventListener("change", applyViewFilters);

  // reporterId is whichever node's roster produced this entry (the observer's own
  // mesh_status peers list, or a peer's embedded peers_seen roster); subjectId is the node
  // that roster entry is ABOUT. reporterId only learns subjectId's presence because
  // subjectId's RouterHealth/heartbeat traffic actually reached it -- the arrow (2026-07-23,
  // "point the way the message actually flows, not who's asking about whom") is drawn
  // subjectId -> reporterId to show that: reporterId "hears"/receives from subjectId. The
  // edge id/pruning key stays reporterId->subjectId (unchanged from before this fix) --
  // only the DataSet's visual from/to swap relative to that key, everything else (decay,
  // color, id-based upsert) is unaffected by which end the arrowhead is drawn at.
  //
  // opts.source labels which relationship produced this edge, drives the color
  // (EDGE_SOURCE_COLOR) -- "mesh_status" (observer's own direct peers list) vs "peers_seen"
  // (a peer's own embedded roster, one hop further out). No persistent on-canvas text label
  // (2026-07-23, dropped a prior attempt) -- vis has no reliable way to offset two edges'
  // labels apart when they overlap (the common reciprocal mesh_status/peers_seen case,
  // straight-only per the arrow-direction fix above), so labels ended up left/right of the
  // line inconsistently or floating disconnected from any visible edge. Color + the legend
  // already convey source; opts.source is folded into the hover title instead, so the
  // source remains internal edge metadata instead of fighting for on-canvas placement.
  // opts.asOfMs anchors decay, derived the same way for both: the wire's own
  // last_seen_delta_ms (D97 added this to RouterPeerRef too -- a duration, not a timestamp,
  // so no clock-sync assumption either way).
  function upsertEdge(reporterId, subjectId, presence, opts) {
    opts = opts || {};
    const baseColor = EDGE_SOURCE_COLOR[opts.source] || "#999999";
    const isRelayed = opts.source === "peers_seen";
    edges.update({
      id: `${reporterId}->${subjectId}`, from: subjectId, to: reporterId, arrows: "to",
      label: "",
      dashes: isRelayed ? [8, 6] : false,
      source: opts.source,
      color: { color: baseColor, opacity: 1 },
      hidden: opts.source ? !edgeVisibleBySource({ source: opts.source }) : false,
      baseColor, asOfMs: opts.asOfMs != null ? opts.asOfMs : Date.now(),
      currentOpacity: 1,
    });
    refreshParallelEdges(subjectId, reporterId);
  }

  // Tried curvedCW/CCW here (2026-07-23) to fan the reciprocal mesh_status/peers_seen pair
  // between the same two routers apart instead of one drawing on top of the other -- reverted
  // same day (user ask: straight point-to-point edges only). Also found empirically that
  // vis's curvedCW/CCW rendering put the arrowhead at the wrong end relative to the DataSet's
  // own from/to/arrows:"to" (confirmed via a debug dump of the live edge data: `to` was
  // correctly the receiving node, but the visual arrow tip rendered at the *other* end) -- so
  // straight edges aren't just simpler here, they're the correct choice. A reciprocal pair
  // between the same two nodes goes back to drawing as one overlapping line (both edges still
  // present/correct in the DataSet, reachable via hover/click) -- a known tradeoff of
  // straight-only, not a bug. Undirected key so it groups a pair regardless of which one is
  // `from`/`to`.
  function undirectedPairKey(a, b) {
    return a < b ? `${a} ${b}` : `${b} ${a}`;
  }

  function refreshParallelEdges(a, b) {
    const key = undirectedPairKey(a, b);
    const siblings = edges.get({ filter: (e) => undirectedPairKey(e.from, e.to) === key });
    edges.update(siblings.map((e) => ({ id: e.id, smooth: { enabled: false } })));
  }

  // reporterId here matches upsertEdge's reporterId (id/pruning key); the visual `to` field
  // (not `from`) is what now holds it, per the arrow-direction swap above.
  function pruneStaleEdgesFrom(reporterId, currentTargets) {
    edges.get({ filter: (e) => e.to === reporterId }).forEach((e) => {
      if (!currentTargets.has(e.from)) {
        const other = e.from;
        edges.remove(e.id);
        // Reflow whatever's left in that pair back toward straight (refreshParallelEdges'
        // n<=1 case) instead of leaving a lone survivor still bowed from when it had a
        // sibling.
        refreshParallelEdges(other, reporterId);
      }
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
      updates.push({
        id: e.id,
        currentOpacity: 1 - frac * (1 - EDGE_MIN_OPACITY),
        color: { color: e.baseColor, opacity: 1 - frac * (1 - EDGE_MIN_OPACITY) },
      });
    });
    if (updates.length) edges.update(updates);
    applyViewFilters();
  }
  setInterval(tickEdgeDecay, EDGE_DECAY_TICK_MS);

  // data is one ActRouterMeshStatus sample: {observer_node, observer_router, state_revision,
  // peers: [{health: <full RouterHealth>, presence, last_seen_delta_ms}, ...]}.
  function upsertMeshStatusSample(data) {
    if (!data || !data.observer_node || !data.observer_router) return;
    const observerId = data.observer_node; // already just the node name (RouterMeshStatus)
    nodes.update({ id: observerId, label: routerLabel(observerId), color: OBSERVER_NODE_COLOR,
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
      const peerId = nodeNameOf(health.router);
      directPeers.add(peerId);

      // Full per-peer detail -- the "full resolution" ActRouterMeshStatus carries that the
      // WAN RouterHealth topic's trimmed peers_seen refs don't.
      const teamNames = deriveTeamNames(health, sampleNodeNames);
      nodes.update({
        id: peerId, label: routerLabel(peerId),
        color: { background: KNOWN_NODE_COLOR, border: teamBorder(teamNames) },
        borderWidth: 4,
        // Stashed for the detail panel (interactivity) + team filter — read back from the
        // DataSet on click, so the panel never re-parses the wire.
        kind: "peer", health: health, teamNames: teamNames,
        presence: peerEntry.presence, lastSeenMs: peerEntry.last_seen_delta_ms,
        ...PLATFORM_ROW_Y_FIELDS,
        ...(c2PinFields(health) || {}),
      });
      upsertEdge(observerId, peerId, peerEntry.presence, {
        source: "mesh_status",
        asOfMs: Date.now() - (peerEntry.last_seen_delta_ms || 0),
      });

      // Bonus: each peer's own embedded heartbeat (health) carries ITS OWN peers_seen
      // roster -- reconstructs the fuller multi-hop mesh graph from one LAN vantage point,
      // not just a star centered on the observer.
      const subPeers = new Set();
      (health.peers_seen || []).forEach((subPeer) => {
        if (!subPeer || !subPeer.router) return;
        const subId = nodeNameOf(subPeer.router);
        subPeers.add(subId);
        if (!nodes.get(subId)) {
          nodes.add({ id: subId, label: routerLabel(subId), color: PLACEHOLDER_NODE_COLOR,
                       kind: "placeholder", knownVia: peerId, ...PLATFORM_ROW_Y_FIELDS });
        }
        upsertEdge(peerId, subId, subPeer.presence, {
          source: "peers_seen",
          asOfMs: Date.now() - (subPeer.last_seen_delta_ms || 0),
        });
      });
      pruneStaleEdgesFrom(peerId, subPeers);
    });
    pruneStaleEdgesFrom(observerId, directPeers);
    relayoutPlatformRow();
    updateTopologyStatus(data);
  }

  function updateTopologyStatus(data) {
    const directCount = (data.peers || []).length;
    const aliveCount = (data.peers || []).filter((p) => p && p.presence === "PRESENCE_ALIVE").length;
    const relayedCount = edges.get({ filter: (e) => e.source === "peers_seen" }).length;
    const revision = data.state_revision != null ? ` · rev ${data.state_revision}` : "";
    setStatus(`View from ${data.observer_node} · control_lan · ${aliveCount}/${directCount} direct alive · ` +
          `${relayedCount} relayed${revision}`);
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
      pruneStaleTeams();                        // remove chips for teams with no members
      applyViewFilters();                      // new nodes respect active filters/highlight
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

  // Single always-on push socket -- no connection-creation POST, no HELLO handshake, no
  // bind_datareader frame (that was all WIS's own application-level protocol; the bridge
  // just pushes {"data": {...}} directly, same envelope as the REST seed).
  function connectWebSocket() {
    const ws = new WebSocket(WS_URL);

    ws.onmessage = (evt) => {
      let msg;
      try {
        msg = JSON.parse(evt.data);
      } catch (_err) {
        return;
      }
      if (ingestSampleArray([msg])) {
        setStatus(`${statusEl.textContent} · ${new Date().toLocaleTimeString()} · ` +
            `${nodes.length} nodes / ${edges.length} edges`);
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

  // --- Team assignment (team-control-topic-plan.md §4) ----------------------------
  // Right-click a platform node → modal prompt → POST TeamAssignment to the bridge. The
  // partition change propagates through the mesh; the node's RouterHealth.team_partition
  // updates within ~1-2 heartbeat cycles and the existing ring rendering recolours
  // automatically — no new subscription needed.
  function publishTeamAssignment(platformNode, teamName) {
    const sample = { platform_node: platformNode, team_name: teamName };
    return fetch(ASSIGN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sample),
    }).then((resp) => {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setStatus(`Assigned ${platformNode} → ${teamName || "(no team)"}`);
    }).catch((err) => {
      setStatus(`Team assignment failed: ${err}`);
    });
  }

  // Context menu (right-click on a node). Vis.js fires "oncontext" but doesn't expose
  // a built-in context menu, so we position a small custom one at the pointer and let
  // the user pick "Assign to team" or "Remove from team".
  const ctxMenu = document.createElement("div");
  ctxMenu.id = "ctx-menu";
  ctxMenu.style.cssText = "display:none;position:absolute;z-index:1000;" +
    "background:#1e2028;border:1px solid #3a3f4a;border-radius:4px;padding:2px 0;" +
    "font:13px/1.6 sans-serif;color:#e6e8eb;min-width:160px;box-shadow:0 4px 12px rgba(0,0,0,.4);";
  document.body.appendChild(ctxMenu);
  let ctxNodeId = null;

  function hideCtxMenu() { ctxMenu.style.display = "none"; ctxNodeId = null; }
  document.addEventListener("click", (e) => {
    // Don't hide if clicking inside the team-input modal
    if (e.target.closest("#team-input-modal")) return;
    hideCtxMenu();
  });
  document.addEventListener("contextmenu", (e) => {
    // Let only the vis canvas handler show the menu; hide on any other right-click.
    if (!e.target.closest("#graph")) hideCtxMenu();
  });

  function ctxItem(label, fn) {
    const el = document.createElement("div");
    el.textContent = label;
    el.style.cssText = "padding:4px 14px;cursor:pointer;";
    el.addEventListener("mouseenter", () => { el.style.background = "#2a2d38"; });
    el.addEventListener("mouseleave", () => { el.style.background = ""; });
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const savedNodeId = ctxNodeId;   // capture before hideCtxMenu nulls it
      hideCtxMenu();
      fn(savedNodeId);
    });
    return el;
  }

  // Inline team-name input modal — replaces window.prompt() which is not supported
  // in VS Code's Simple Browser (throws "prompt() is not supported").
  const teamInputModal = document.createElement("div");
  teamInputModal.id = "team-input-modal";
  teamInputModal.style.cssText = "display:none;position:fixed;top:0;left:0;right:0;bottom:0;" +
    "z-index:2000;background:rgba(0,0,0,.5);align-items:center;justify-content:center;";
  teamInputModal.innerHTML =
    `<div style="background:#1e2028;border:1px solid #3a3f4a;border-radius:6px;padding:16px 20px;` +
    `min-width:280px;box-shadow:0 8px 24px rgba(0,0,0,.6);font:13px/1.6 sans-serif;color:#e6e8eb;">` +
    `<div id="team-input-label" style="margin-bottom:8px;"></div>` +
    `<input id="team-input-field" type="text" style="width:100%;box-sizing:border-box;` +
    `padding:6px 10px;background:#14181f;border:1px solid #3a3f4a;border-radius:4px;` +
    `color:#e6e8eb;font:13px sans-serif;outline:none;" />` +
    `<div style="margin-top:12px;display:flex;gap:8px;justify-content:flex-end;">` +
    `<button id="team-input-cancel" style="padding:4px 14px;background:#2a2d38;border:1px solid #3a3f4a;` +
    `border-radius:4px;color:#e6e8eb;cursor:pointer;font:13px sans-serif;">Cancel</button>` +
    `<button id="team-input-ok" style="padding:4px 14px;background:#3aa655;border:none;` +
    `border-radius:4px;color:#fff;cursor:pointer;font:13px sans-serif;">Assign</button>` +
    `</div></div>`;
  document.body.appendChild(teamInputModal);

  const teamInputLabel = teamInputModal.querySelector("#team-input-label");
  const teamInputField = teamInputModal.querySelector("#team-input-field");
  const teamInputOk = teamInputModal.querySelector("#team-input-ok");
  const teamInputCancel = teamInputModal.querySelector("#team-input-cancel");
  let teamInputResolve = null;

  function showTeamInput(nodeId) {
    teamInputLabel.textContent = `Team name for ${nodeId}:`;
    teamInputField.value = "";
    teamInputModal.style.display = "flex";
    teamInputField.focus();
    return new Promise((resolve) => { teamInputResolve = resolve; });
  }

  function closeTeamInput(value) {
    teamInputModal.style.display = "none";
    if (teamInputResolve) { teamInputResolve(value); teamInputResolve = null; }
  }

  teamInputOk.addEventListener("click", () => closeTeamInput(teamInputField.value));
  teamInputCancel.addEventListener("click", () => closeTeamInput(null));
  teamInputField.addEventListener("keydown", (e) => {
    if (e.key === "Enter") closeTeamInput(teamInputField.value);
    if (e.key === "Escape") closeTeamInput(null);
  });

  network.on("oncontext", (params) => {
    params.event.preventDefault();
    const nodeId = network.getNodeAt(params.pointer.DOM);
    if (!nodeId) { hideCtxMenu(); return; }
    const n = nodes.get(nodeId);
    if (!n || n.kind === "observer") { hideCtxMenu(); return; }
    ctxNodeId = nodeId;
    ctxMenu.innerHTML = "";
    ctxMenu.appendChild(ctxItem("Assign to team…", (nodeId) => {
      showTeamInput(nodeId).then((team) => {
        if (team != null) publishTeamAssignment(nodeId, team);
      });
    }));
    ctxMenu.appendChild(ctxItem("Remove from team", (nodeId) => {
      publishTeamAssignment(nodeId, "");
    }));
    ctxMenu.style.left = params.event.pageX + "px";
    ctxMenu.style.top = params.event.pageY + "px";
    ctxMenu.style.display = "block";
  });

  seedFromRest().then(connectWebSocket);
})();
