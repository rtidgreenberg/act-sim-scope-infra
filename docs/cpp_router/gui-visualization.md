# Mesh GUI Visualization: Dynamic Node Graph over Web Integration Service

> Investigation and architecture proposal for a browser-based dynamic node graph — routers
> as nodes, edges colored by connection state — driven off the mesh topics the router
> already publishes. Status: **v1 IMPLEMENTED 2026-07-21**
> ([Phase 16](implementation-plan.md#phase-16-mesh-gui-v1--node-graph-over-web-integration-service),
> `gui/mesh_dashboard/`), verified end to end against a real 2-router mesh (REST + a live
> WebSocket presence-transition push). **Scope corrected mid-build: LAN-side
> (`ActRouterMeshStatus`, colocated with the C2/control node), not the originally-planned
> WAN-side `RouterHealth` (Option A below)** — see "Why Option A was retired" and Phase 16's
> banner for the two real reasons (a WAN discovery restriction found independently, plus
> user direction). Drafted 2026-07-20; updated 2026-07-21 twice — first with the WAN spike
> results, then with the LAN-side pivot and final v1 evidence.

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

**Key finding (theoretical, at doc-drafting time): the topology graph could be mesh-wide on
one WAN domain.** `RouterHealth.peers_seen` means a single subscriber on domain `200` sees
every router's heartbeat *and* every router's own roster edges — enough in principle to
reconstruct the full "who's alive, who sees whom" graph without touching any node's LAN.
**This did not survive contact with the real production config** — see "Why Option A was
retired" below; v1 reads `ActRouterMeshStatus` on one node's LAN instead. **Link-quality
detail (RTT, loss) is NOT mesh-wide either way** — `RouterLinkStats` is explicitly LAN-only
by design (D14), so edge *coloring by RTT/loss* requires either per-node local access or a
future change to publish link stats onto the WAN (a real design decision, not a GUI concern
— tracked as Option C below).

### Why Option A (WAN `RouterHealth`) was retired

Two independent reasons converged during Phase 16's build, 2026-07-21:

1. **User direction** — WIS should read the LAN-side "full resolution" topic, not the WAN
   presence heartbeat.
2. **A real architectural blocker, found independently while building the WAN version
   first:** `control_wan`/`platform_wan` use `wan_qos_lib.xml` QoS profiles with
   `discovery.initial_peers` set to an explicit unicast peer list and
   `multicast_receive_addresses` disabled. An arbitrary external subscriber like WIS is not
   on that peer list, so it cannot passively discover WAN participants the way this doc
   originally assumed — confirmed by standing up the real `control-platform.yaml` mesh plus
   a WIS instance on domain `200` and observing REST return `[]` indefinitely despite
   correct config, then wire-capturing domain-200 loopback traffic to see the actual cause.

`ActRouterMeshStatus` sidesteps this entirely: `control_lan` (and `platform_lan`) carry
**no custom QoS at all** in `control-platform.yaml` — plain default participant QoS, which
means standard **multicast** discovery, no peer list, no restriction. This is not a
tweak WIS needs to opt into; it's what the LAN admin participants already are. Confirmed
directly while testing: both the control router's LAN participant and WIS's own LAN
participant bound to port `12400` (domain 20's standard multicast discovery port), and
that shared multicast group is exactly the mechanism that let them find each other — no
`initial_peers` list, on either side, anywhere in this path. (The unicast-peer-list
restriction above applies **only** to the retired WAN participants, `control_wan`/
`platform_wan`/`team_wan` — never to `control_lan`/`platform_lan`.) `ActRouterMeshStatus`
is also richer for the purpose anyway: each peer entry is that peer's **complete** last
`RouterHealth` summary (not the WAN topic's trimmed `peers_seen` refs) — literally "full
resolution," per the framing that drove the redirect.

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

**Option A — mesh-wide topology view via `RouterHealth` only (WAN domain). RETIRED,
2026-07-21 — see above.** Would have been the cheapest to stand up and reachable from
anywhere on the WAN, but the production WAN QoS profiles' unicast-peer-list discovery
model made it unworkable for an arbitrary external subscriber without also editing every
router's peer list. Superseded by what shipped (below).

**Shipped for v1 — `ActRouterMeshStatus`, colocated with the C2/control node's LAN.** One
WIS instance reads `control_lan`'s `ActRouterMeshStatus`: nodes = the observer + every peer
it knows about (full `RouterHealth` detail per peer, not just trimmed refs), edges = colored
by `presence`, PLUS each peer's own embedded `peers_seen` roster reconstructs further edges
— a fuller multi-hop graph than a star, from one LAN vantage point. This is closer to
**Option B**'s shape (below) than Option A, but reads only `ActRouterMeshStatus`, not
`RouterLinkStats`/`RouterStatus`, and runs one instance (at C2) rather than one per node.

**Option B — per-node operational dashboard via LAN topics (not implemented; still a real
next step).** A WIS instance colocated with each node's LAN admin participant (domain `20`
for control, `30` for platform) additionally pulls `RouterLinkStats` (RTT/loss → edge
color/thickness) and `RouterStatus` (route detail on node click). Richer, but siloed per
node — no single page shows the whole mesh's link quality, and it's `N` WIS
instances/configs instead of one. The shipped v1's data model doesn't need restructuring to
add this later — it's additive.

**Option C — a future WAN republish of link-stats summaries** (extending the Phase 9
`LinkStatsCollector`/D14 design to also emit a compact WAN-safe rollup). Direction set
2026-07-21 (minimal variant — see sizing below), not implemented; blocked on defining the
quality metric itself, and now also reads on the shipped LAN topic rather than the retired
WAN one, so its design would need revisiting against what actually shipped.

### Option C sizing (2026-07-21) — leaning toward pursuing this, minimal variant

User direction: **likely go with Option C, minimal variant** — add per-peer link-quality
fields directly to `RouterHealth`'s existing `RouterPeerRef` entries (no new WAN topic)
rather than mirroring the full `RouterLinkStats` struct onto the WAN. Real wire numbers,
measured (not estimated) by capturing loopback traffic from a live 2-router mesh and
decoding a real `RouterHealth` sample byte-for-byte against the IDL:

- **Current confirmed cost:** 248 bytes CDR payload / 326 bytes on the wire for one
  `RouterHealth` heartbeat with one `peers_seen` entry, at the existing 1 Hz rate.
- **Added cost per peer edge** (this scales with edges seen, not a flat per-message cost):
  - Minimal (RTT + one loss/quality number, 2× `uint32`): **+8 bytes/peer** — CDR-computed
    from the field widths, not independently wire-verified (no code changes yet).
  - Full `RouterLinkStats` mirror (16× uint64 + 7× uint32 counters, for comparison only —
    **not** the chosen direction): +156 bytes/peer.
- **Mesh-wide, minimal variant, at 1 Hz:** ~160 bytes/sec for a 5-router mesh (20 edges),
  ~720 bytes/sec for 10 routers (90 edges) — negligible in absolute bandwidth terms. The
  real cost D14 was originally protecting against was continuous per-edge WAN traffic, not
  raw bytes, and the minimal variant keeps that cost small enough that it's arguably not a
  blocker anymore — but this is a router-side call, not a GUI one, and still needs its own
  D-numbered decision before implementation.

**Post-pivot simplification (2026-07-21):** this sizing was computed against the original
WAN-wide plan. Since v1 now runs colocated with C2's own LAN, getting C2's own link-quality
numbers needs **no WAN change and no new WAN bytes at all** — `ActRouterLinkStats` already
exists on `control_lan` (Phase 9, LAN-only by design) and is just another topic the same WIS
instance could subscribe to (this is Option B, not C). The WAN-rollup sizing above still
matters only if a *mesh-wide* (not just C2's-view) quality graph becomes a real requirement
later — worth re-confirming that's still wanted before pursuing it, now that the cheaper
LAN-only path exists for C2's own view.

**Open blocker — the quality metric itself is not yet defined.** This isn't a new gap: it's
the **same open item as D14's "Deferred: inference and the correlation experiment"**
([link-health.md](link-health.md#deferred-inference-and-the-correlation-experiment)) —
nothing today maps raw protocol counters (NACKs, RTT, etc.) to a health/quality
classification, because thresholds without the planned link-degradation correlation
experiment (netem/EMANE sweep) would be guesses. Concretely for this WAN rollup: **which
one or two numbers go in those 8 bytes** — raw `rtt_mean_us` alone (defers "is this good or
bad" to the dashboard, no interpretation risk) vs. some derived loss/quality score (needs
the correlation experiment first to mean anything) — is still unresolved. Recommend not
blocking the minimal-variant router change on the full correlation experiment: ship
`rtt_mean_us` (an honest raw number, matches D14's existing capture-first stance) now, add
a real loss/quality figure later once the correlation experiment actually defines one.

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
3. **Phase 16 — v1 IMPLEMENTED, 2026-07-21.** Full deliver/evidence list:
   [implementation-plan.md#phase-16](implementation-plan.md#phase-16-mesh-gui-v1--node-graph-over-web-integration-service).
   **Scope corrected mid-build to LAN-side** (`ActRouterMeshStatus` on `control_lan`, not
   the WAN `RouterHealth` graph the spike above proved) — see "Why Option A was retired."
   Verified against a real 2-router mesh: REST returns a real sample (`observer_node` =
   the C2 node, `peers[0].health` = platform's full status); a SIGKILLed platform router
   produced a live WebSocket push showing `presence: "PRESENCE_STALE"` ~2s later. Two real
   bugs found and fixed in the process — a `register_type` alias breaking SEDP type
   matching, and a VOLATILE reader never receiving a TRANSIENT_LOCAL writer's already-
   written history on a change-driven (non-periodic) topic — both detailed in
   `implementation-plan.md`'s Phase 16 section and `gui/mesh_dashboard/README.md`. Not
   independently verified: actual in-browser rendering (no browser/automation tool
   available in this environment) — the data path and the `vis-network` API calls used are
   confirmed, but the page has not been visually observed rendering.

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

- **Primary consumer: C2 operator** (mesh-wide health-at-a-glance).
- **Ships LAN-side, colocated with the C2/control node** (`control_lan`,
  `ActRouterMeshStatus`) — superseded the original "Option A, WAN `RouterHealth`" decision
  below it once building the WAN version surfaced the `initial_peers` discovery
  restriction. Kept here, struck through, for the historical record rather than deleted:
  ~~Ships **Option A** first: one WIS instance on the WAN `RouterHealth` topic (domain
  `200`), no per-node LAN access required for the first cut.~~
- **Front-end graph library: `vis-network`**, vendored locally (pinned 9.1.13). Implemented,
  not just decided.
- **WIS hosting: standalone process**, colocated with (or reachable to) the C2/control
  node's LAN. Implemented.

## Open questions (still need a decision — not guessed at here)

- **Is Option B (per-node `RouterLinkStats`/`RouterStatus`) worth adding next?** Now cheaper
  than originally scoped for at least C2's own view — `ActRouterLinkStats` is already on
  the same `control_lan` this WIS instance reads, so it's just another topic subscription,
  not a new instance/config. Still an open scope question, not a technical blocker.
- ~~Is Option C worth pursuing later?~~ **Direction set 2026-07-21: likely yes, minimal
  variant** (see sizing above) — narrowed to one concrete open item: **what the per-peer
  quality number actually is**, which is D14's already-deferred inference/correlation-
  experiment question, not a new one. Blocks implementation, not the direction decision.
  Note the post-pivot simplification above: this only matters for a *mesh-wide* quality
  view now, not for getting C2 its own link-quality numbers (Option B covers that cheaply).
- **Should a per-node (not just C2-colocated) deployment be built too?** i.e., is Option B's
  "one WIS instance per node" shape wanted, or is C2's single vantage point sufficient for
  the program's actual operational need?
