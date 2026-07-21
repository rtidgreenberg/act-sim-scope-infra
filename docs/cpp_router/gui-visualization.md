# Mesh GUI Visualization: Dynamic Node Graph over Web Integration Service

> Investigation and architecture proposal for a browser-based dynamic node graph — routers
> as nodes, WAN links as edges colored/weighted by connection state — driven off the mesh
> topics the router already publishes. Status: **Option A spiked and PASSED
> (2026-07-21, [spikes/wis_mesh_dashboard/](../../spikes/wis_mesh_dashboard/README.md))** —
> not yet a design-decisions.md entry or a scheduled phase (would land after
> [Phase 15](implementation-plan.md#phase-15-network-capture--debug-mode) as **Phase 16** if
> adopted; front-end graph work not yet started). Drafted 2026-07-20 from the current repo
> state (`router/admin/RouterAdminTypes.idl`, `PresenceMonitor`, `LinkStatsCollector`) plus a
> scan of the installed `rtiwebintegrationservice` (Connext 7.7.0); updated 2026-07-21 with
> spike results.

## Goal

A live web page showing the router mesh as a node graph: one node per router, edges for
who-sees-who, edge color/thickness reflecting link quality (RTT, loss), updating in
real time as routers come up, go STALE/DEAD, or reconnect — without writing a custom
DDS-to-web bridge. RTI **Web Integration Service (WIS)** ships in this Connext 7.7.0
install (`$NDDSHOME/bin/rtiwebintegrationservice`) specifically to expose DDS topics over
REST + WebSocket for exactly this kind of browser consumer, so the question is whether our
existing mesh topics are already shaped for it, not whether to build a bridge from scratch.

## What mesh topic data already exists

All of this is already published by `router_main` — no new router-side capture needed for
a first cut. Source: `router/admin/RouterAdminTypes.idl` (see also
[presence-and-health.md](presence-and-health.md), [link-health.md](link-health.md)).

| Topic | Rides | Scope | Carries |
|---|---|---|---|
| `RouterHealth` | WAN participant (domain `200` in `control-platform.yaml`) | **mesh-wide** | Per-router heartbeat (1 Hz): `router` identity (`"<node>/<router>"`), `role`, `overall_state` (OK/DEGRADED/ERROR), `n_routes`/`n_degraded`/`n_error`, `config_hash`, **and `peers_seen`: this router's own roster edges** (`RouterPeerRef{router, presence}`, presence = ALIVE/STALE/DEAD) |
| `ActRouterMeshStatus` (`RouterMeshStatus`) | LAN admin participant (domain `20`/`30`, per node) | **per-node local** | Aggregated roster: `observer_node`/`observer_router` + every peer's last `RouterHealth` summary + `last_seen_delta_ms`, republished on roster change |
| `ActRouterLinkStats` (`RouterLinkStats`) | LAN admin participant, per node (`LinkStatsCollector`, Phase 9) | **per-node local** | Per-WAN-link (observer→peer) reliable-protocol rollup: pushed/pulled/nack counts, `rtt_min/mean/max_us`, `rediscovery_in_interval` |
| `ActRouterStatus` (`RouterStatus`) | LAN admin participant, per node | **per-node local** | Per-route operational/discovery state, matched counts, QoS warnings — the "why isn't this route forwarding" detail view |
| `ActRouterControllerJournal` | LAN admin participant, per node | **per-node local** | Per-decision event stream (command received, endpoint discovered/lost, match changed) — a timeline/log feed, not graph state |

**Key finding: the topology graph (nodes + presence edges) is already mesh-wide on one WAN
domain.** `RouterHealth.peers_seen` means a single subscriber on domain `200` sees every
router's heartbeat *and* every router's own roster edges — enough to reconstruct the full
"who's alive, who sees whom" graph without touching any node's LAN. **Link-quality detail
(RTT, loss) is NOT mesh-wide** — `RouterLinkStats` is explicitly LAN-only by design (D14),
so edge *coloring by RTT/loss* requires either per-node local access or a future change to
publish link stats onto the WAN (a real design decision, not a GUI concern — flagged as an
open question below, not assumed).

## Web Integration Service: what it is, what it needs

Confirmed installed and present in this Connext 7.7.0 distribution (not previously used in
this repo — no existing WIS config, no prior mention in `design-decisions.md`/
`implementation-plan.md`):

- Binary: `$NDDSHOME/bin/rtiwebintegrationservice`; schema:
  `$NDDSHOME/resource/schema/rti_web_integration_service.xsd`; sample config:
  `$NDDSHOME/resource/xml/RTI_WEB_INTEGRATION_SERVICE.xml` (ShapesDemo).
- Config shape is the familiar Connext-services XML: `<types>`, `<qos_library>`,
  `<domain_library>`, then a `<web_integration_service>` block wiring
  `domain_participant`/`publisher`/`subscriber`/`data_reader` entries against a
  `domain_ref`/`topic_ref` — structurally identical to a Routing Service or Recording
  Service deployment file, and to the pattern already proven in this repo for the C++
  router's own QoS profiles.
- **It exposes matched readers/writers over REST (poll) and WebSocket (push)** — a
  WebSocket subscription per topic is the natural fit for a live-updating node graph (push
  new samples straight into the graph's node/edge state, no client-side polling loop).
- **Type declaration: CONFIRMED, solved cheaply — no hand transcription needed.**
  `ask_connext_question` (2026-07-21) confirmed WIS only accepts inline XML `<types>`
  declarations (Dynamic Data), no compiled type-plugin loading, matching the schema
  inference above. But `rtiddsgen -convertToXml -d <dir> router/admin/RouterAdminTypes.idl`
  (verified working, both by hand and inside the spike's `run_spike.sh`) produces a clean
  `<dds><types>...</types></dds>` document with every struct/enum verbatim — `RouterHealth`
  included, `router` correctly `key="true"`, `peers_seen` correctly a `nonBasic` sequence of
  `RouterPeerRef`. **The IDL stays the single source of truth; the XML is a regenerated
  build artifact, spliced into the WIS config, never hand-maintained.** One WIS-authoring
  gotcha found while wiring this up (not a `-convertToXml` problem): a WIS `<register_type>`
  and `<topic>` sharing the literal same `name` in the same domain collide
  (`RTIXMLObject_addChild` error) — give `register_type` a distinct alias and keep
  `type_ref`/`topic name` pointing at the real names. See
  `spikes/wis_mesh_dashboard/config/wis_config.xml.template`.

## Architecture options

**Option A — mesh-wide topology view via `RouterHealth` only (WAN domain).** One WIS
instance subscribes to `RouterHealth` on domain `200`. Nodes = distinct `router` identities
seen; edges = each router's own `peers_seen` entries, colored by `presence`
(ALIVE/STALE/DEAD). Cheapest to stand up, works from anywhere with WAN reachability (a
natural fit for a C2-side "mesh health at a glance" view), but no link-quality edge
weighting — presence only.

**Option B — per-node operational dashboard via LAN topics.** A WIS instance colocated with
each node's LAN admin participant (domain `20` for control, `30` for platform) additionally
pulls `RouterLinkStats` (RTT/loss → edge color/thickness) and `RouterStatus` (route detail
on node click). Richer, but siloed per node — no single page shows the whole mesh's link
quality, and it's `N` WIS instances/configs instead of one.

**Option C — A + a future WAN republish of link-stats summaries** (extending the Phase 9
`LinkStatsCollector`/D14 design to also emit a compact WAN-safe rollup, mirroring how
`RouterHealth` already carries roster edges mesh-wide). Gets a single mesh-wide graph with
both presence *and* link-quality edges, but is a router-side design change, not just a GUI
integration — its own D-numbered decision, out of scope for a first GUI cut.

**Recommendation:** start with **Option A** as a spike (`spikes/wis_mesh_dashboard/`, per
repo convention) — it needs zero router-side changes and already answers "is the node
graph alive/updating." Layer Option B in once A is proven, if per-node link-quality detail
is wanted in the same pass. Treat C as a separate, later design decision.

## Proposed phasing

1. ~~**Spike: WIS type-registration path.**~~ **Done, 2026-07-21** — see above; solved by
   `rtiddsgen -convertToXml`.
2. ~~**Spike: Option A end-to-end.**~~ **Done, 2026-07-21, PASSED all 4 checks** —
   [spikes/wis_mesh_dashboard/](../../spikes/wis_mesh_dashboard/README.md) ran a real
   2-router mesh (fixed domains, `config/e2e_presence_fixed_domains.yaml`) against a live
   `rtiwebintegrationservice`, and confirmed real `RouterHealth` samples (with populated
   `peers_seen`) over **both** REST and WebSocket. Reusable working endpoints:
   - REST: `GET http://localhost:8080/dds/rest1/applications/MeshDashboardApp/domain_participants/MeshParticipant/subscribers/MeshSubscriber/data_readers/RouterHealthReader?sampleFormat=json&removeFromReaderCache=false`
   - WebSocket: create via `POST http://localhost:8080/dds/v1/websocket_connections` body
     `[{"name": "MeshWsConn"}]`, then connect `ws://localhost:8080/dds/websocket/MeshWsConn`
     and HELLO/BIND per the WIS WebSocket API (see spike README for the exact handshake —
     **this shape differs from the product manual's own documented example**, see the
     correction below).
   - No front-end HTML/graph-rendering code yet — library choice still open (see below).
3. **Phase 16 candidate (if adopted):** productionize the WIS config alongside
   `router/config/`, decide long-term hosting (colocated with a node vs. a standalone
   dashboard host), build the actual graph-rendering page, fold in Option B's per-node
   link-stats view.

### Correction: WIS WebSocket API — manual/MCP claim vs. observed behavior (2026-07-21)

The spike's first pass used the WebSocket shape `ask_connext_question` reported
(`ws://<host>:8080/dds/v1/{connectionName}`, connect-and-implicitly-bind). Running it
against the real service showed this was wrong on several points, corroborated by the
product's own shipped manual (`$NDDSHOME/doc/manuals/.../using_websocket_api.html`) once
read directly rather than only via the MCP:

- WebSockets are **disabled by default** — `rtiwebintegrationservice` needs
  `-enableWebSockets`, or every WS-related request 404s.
- The connect URL is `/dds/websocket/<name>`, not `/dds/v1/<name>` (`/dds/v1/...` is only
  the REST path used to *create* the named connection resource).
- Binding a DataReader is an explicit post-connect HELLO → HELLO_OK → JSON `bind` message
  exchange, not something that happens implicitly by connecting to a per-DataReader URL.
- The manual's own documented example body for creating a connection
  (`{"name": <websocket_name>}`) gets HTTP 422 from the real 7.7.0 service; it only accepts
  the **array-wrapped** form (`[{"name": "MeshWsConn"}]`), matching the older shipped
  ShapesDemo JS sample instead of the current manual text.

Per the repo's Connext-verification rule, this belongs in
`docs/connext-ai-issues/connext-ai-issues.md` (not yet added as of this edit — needs the
submodule commit/push step; flagging here so it isn't lost).

## Decisions (2026-07-21)

- **Primary consumer: C2 operator** (mesh-wide health-at-a-glance). Ships **Option A**
  first: one WIS instance on the WAN `RouterHealth` topic (domain `200`), no per-node LAN
  access required for the first cut.
- **Spiking now**: `spikes/wis_mesh_dashboard/` — verify the WIS type-registration question
  (below) and get a real mesh's `RouterHealth` visible over WIS REST/WebSocket end to end.

## Open questions (still need a decision — not guessed at here)

- **Front-end graph library:** no preference recorded anywhere in the repo. Candidates for a
  live-updating force-directed graph: `vis-network`, `cytoscape.js`, `d3-force`. Worth
  picking before the spike's HTML page is written, not during.
- **Where does WIS run long-term?** Colocated with each router process (simplest for a
  future Option B/LAN access) vs. a separate dashboard host with WAN-only reach (matches how
  a C2 node already only expects WAN-side topics — fine for the Option A spike either way).
- **Is Option C (WAN-safe link-stats rollup) worth pursuing later?**, i.e., is a mesh-wide
  *quality*-weighted graph a real requirement, or is per-node link detail (Option B) enough
  once an operator has clicked into a node from the Option A overview?
