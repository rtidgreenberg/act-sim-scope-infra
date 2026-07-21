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

  function healthTitle(health, extra) {
    return `role: ${health.role}\noverall_state: ${health.overall_state}\n` +
           `n_routes: ${health.n_routes}  n_degraded: ${health.n_degraded}  n_error: ${health.n_error}\n` +
           `heartbeat_seq: ${health.heartbeat_seq}` + (extra ? `\n${extra}` : "");
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
                   title: "(this dashboard's own vantage point)" });

    const directPeers = new Set();
    (data.peers || []).forEach((peerEntry) => {
      const health = peerEntry && peerEntry.health;
      if (!health || !health.router) return;
      const peerId = health.router;
      directPeers.add(peerId);

      // Full per-peer detail -- the "full resolution" ActRouterMeshStatus carries that the
      // WAN RouterHealth topic's trimmed peers_seen refs don't.
      nodes.update({
        id: peerId, label: peerId, color: KNOWN_NODE_COLOR,
        title: healthTitle(health, `last_seen (from ${observerId}): ${peerEntry.last_seen_delta_ms} ms ago`),
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
                       title: "(only known via another router's own roster, not directly)" });
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
