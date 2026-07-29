# gui/mesh_dashboard — Phase 16 v1: mesh node graph

Live browser dashboard: one node per router (`"<node>/<router>"`), edges colored by presence
(green=ALIVE, amber=STALE, red=DEAD). Design:
[docs/cpp_router/gui-visualization.md](../../docs/cpp_router/gui-visualization.md). Plan:
[docs/cpp_router/implementation-plan.md#phase-16](../../docs/cpp_router/implementation-plan.md#phase-16-mesh-gui-v1--node-graph-over-web-integration-service).

**LAN-side, not WAN (scope correction, 2026-07-21).** WIS subscribes to `ActRouterMeshStatus`
on the C2/control node's admin LAN participant (`control_lan`, domain `20`), not
`RouterHealth` on the WAN. Two reasons this replaced the original WAN-based plan:

1. User direction — the LAN "full resolution" topic, not the WAN presence heartbeat.
2. Independently confirmed while building the WAN version first: `control_wan`/`platform_wan`
   use QoS profiles with an explicit unicast `initial_peers` list and multicast receive
   disabled — an arbitrary external subscriber like WIS isn't in that peer list and can't
   passively discover WAN participants the way the original doc assumed. The LAN admin
   participant has no such restriction, and `ActRouterMeshStatus` is a strictly richer
   dataset anyway: each peer entry is that peer's **complete** last `RouterHealth` summary
   (not the WAN topic's trimmed `peers_seen` refs) — literally "full resolution."

**Trade-off accepted:** `control_lan` is node-local, so this instance sees the mesh only from
the C2/control node's own point of view. That node's `ActRouterMeshStatus` already aggregates
every peer it knows about (D75/D76 presence design), and each peer's embedded heartbeat
carries *its own* `peers_seen` roster too — so the page reconstructs the fuller multi-hop
graph, not just a star centered on the observer — but it's still one vantage point, not a
run-anywhere WAN-wide view.

v1 is presence-only — no link-quality edge coloring. That's Option C, tracked separately
(direction set, not implemented — see the design doc).

**Data bridge replaced 2026-07-29: `server/mesh_bridge.py`, not RTI Web Integration
Service (WIS).** WIS itself is no longer used by this dashboard. Three real, reproduced
WIS problems motivated the replacement (the XML config-parser `base_type` collision, the
"REST seed silently returns `[]`" black-box bug documented further down in this file, and
a PUT/POST write-verb inconsistency) — see
[docs/cpp_router/mesh-dashboard-bridge-implementation-plan.md](../../docs/cpp_router/mesh-dashboard-bridge-implementation-plan.md)
for the full rationale and design. The sections below describing WIS's protocol and the
bugs found against it are kept as historical record (they're real, reproduced findings),
but are no longer how this dashboard actually runs — see "How it works" and "Running it
against a real mesh" for the current mechanism.

## How it works

`server/mesh_bridge.py` is a small standalone Python process: one `DomainParticipant` on
`control_lan` (domain `20`), one `DynamicData` reader on `ActRouterMeshStatus`, one
`DynamicData` writer on `ActTeamAssignment` — built with the plain `rti.connextdds`
Python binding (`QosProvider` against the real, unmodified
`harness_v2/datamodel/gen/ActTypes.xml`, no WIS-style XML service-config parsing
involved). It exposes:

- `GET /api/mesh_status` — the current cached `ActRouterMeshStatus` samples (REST seed).
- `GET /ws` — a WebSocket that pushes `{"data": {...}}` for every new sample.
- `POST /api/team_assignment` — body `{"platform_node": ..., "team_name": ...}`, writes
  one `ActTeamAssignment` sample.
- The dashboard's own static files (`static/`) at `/`.

`static/mesh_graph.js` talks to exactly this contract — a single always-on WebSocket, no
HELLO/bind handshake, no connection-creation POST (that was all WIS's own
application-level protocol on top of the real WS upgrade).

## Running it against a real mesh

```bash
# from the repo root
export NDDSHOME=/home/rti/rti_connext_dds-7.7.0

# 1. Have (or already have running) a router_main mesh whose control-role process uses
#    control_lan as its admin participant (control-platform.yaml's default for --role
#    control with --admin-participant control_lan) -- this dashboard reads THAT process's
#    ActRouterMeshStatus.

# 2. Start the bridge (serves the dashboard page itself too, no second web server):
python3 gui/mesh_dashboard/server/mesh_bridge.py --domain 20 --port 8080

# 3. Open http://localhost:8080/ in a browser.
```

`harness_v2/scripts/run_mesh.sh up --with-dashboard` does exactly this (single process,
tracked by one PID in `pids.txt` — no shell-wrapper/child-process pair to track or kill,
unlike WIS).

## Directory layout

- `server/mesh_bridge.py` — **current data bridge** (replaced WIS, 2026-07-29): a small
  standalone `rti.connextdds` + `aiohttp` process serving the dashboard's REST/WebSocket
  API and the static page itself. See "How it works" above and
  [docs/cpp_router/mesh-dashboard-bridge-implementation-plan.md](../../docs/cpp_router/mesh-dashboard-bridge-implementation-plan.md).
- `static/index.html`, `static/mesh_graph.js` — the dashboard page.
- `static/vendor/vis-network.min.js` — vendored (pinned 9.1.13, no CDN dependency at
  deploy time) graph-rendering library.

## Browser verification (2026-07-22)

This dev VM has no browser installed, so earlier passes (v1, team ring, interactivity)
could only be checked by hand-reading `mesh_graph.js`. Closed that gap: `pip install --user
playwright` (no sudo — pure-Python wheel) + `python3 -m playwright install chromium`
(downloads a headless Chromium build to `~/.cache/ms-playwright`, no system package
changes) gives a real, if headless, browser in this environment.

Driven against a live 2-process mesh (`control-platform.yaml`, control-role `Control_20` +
platform-role `Platform_30`, WIS on `:8080` per "Running it against a real mesh" above)
with a short Playwright script: `page.goto("http://localhost:8080/")`, screenshot, then
`page.mouse.click()` on the rendered node. Confirmed real: the graph draws both nodes
"heard directly" (blue) with a green ALIVE edge, the legend renders, clicking a node opens
the detail side panel with live `RouterHealth` fields (role/overall_state/presence/routes/
team/raw team_partition/heartbeat_seq/config_hash), and `console --errors` equivalent
(`page.on("console")` filtered to `type == "error"`) was empty throughout.

Not covered by this pass: the team **filter** legend chips (needs ≥2 distinct teams
present) and multi-hop placeholder-node rendering (needs a 3rd, indirectly-known router) —
both still only hand-read, not watched render.

**Follow-up pass (same day) — scaled to 4 nodes, team-filter chips now verified too.**
Added `Platform_31`/`Platform_32` (own `control-platform.yaml` copies with `platform_lan`
bumped to domain 31/32, everything else identical/shared WAN) alongside the original
`Platform_30`, all four mutually discovered. Assigned real team memberships over the
live admin channel (`ADD_PARTICIPANT_PARTITION platform_wan=<name>`, the same mechanism
`test_mesh_team_partition.py` uses) — `Platform_30`+`Platform_31` → `TEAM_A`,
`Platform_32` → `TEAM_B` (+`TEAM_C` later, to prove a peer can carry >1 non-identity
partition and still classify correctly). Browser-confirmed: 4 nodes render, `TEAM_A`/
`TEAM_B` rings and legend chips are correct, and clicking the `TEAM_A` chip dims
`Platform_32` while keeping `Platform_30`/`Platform_31` (matching team) and `Control_20`
(observer, exempt per spec) at full opacity — the first real verification of the
filter-chip feature. Screenshots + driver scripts: see the session's scratchpad (not
committed — ephemeral verification aids, not part of the shipped page).

**Real finding — WIS REST seed can silently return nothing even with correct, current
data sitting in the reader** (historical; this is one of the three bugs that motivated
replacing WIS with `server/mesh_bridge.py` — see the note near the top of this file).
Reproduced repeatedly, not a one-off: after the mesh sits idle for a while (no roster/team
change since the last one), a *fresh* browser load logs
`Seeded 0 sample(s) from REST`, and a **direct REST `GET` against the same reader
resource also returns `[]`** — even right after **restarting WIS from scratch** (a brand
new `MeshStatusReader` object, freshly matched). This is NOT the query-parameter/state-
mask issue it might look like (tried `sampleStateMask=READ|NOT_READ`,
`viewStateMask=NEW|NOT_NEW`, `instanceStateMask=ALIVE|NOT_ALIVE_DISPOSED|
NOT_ALIVE_NO_WRITERS` explicitly wide open — still `[]`), and NOT a QoS/config problem
either: a plain `rti.connextdds` Python reader built from WIS's own generated QoS profile
(`MeshDashboardQosLib::MeshStatusReaderQos`, same domain/topic) instantly gets 1 matched
publication and 1 valid sample via the same `TRANSIENT_LOCAL` replay every time. WIS's own
log confirms the gap is internal to WIS, not the wire: `Read 0 DynamicData samples from
DataReader's cache`, logged by WIS's own DDS wrapper. **What does work:** once a genuinely
NEW change happens (another `ADD_PARTICIPANT_PARTITION`, a presence transition, etc.)
while a page's WebSocket is already connected, that page receives it live and renders
correctly — confirmed twice. **Practical implication:** a viewer opening the dashboard
after the mesh has been stable for a while sees an empty graph and no obvious error until
the next real topology change — worth knowing if a demo/screenshot needs "current state
visible immediately." Root cause is unconfirmed (WIS is closed-source; this is a black-box
behavioral finding, not a source-level diagnosis) — logged here rather than in
`docs/connext-ai-issues/connext-ai-issues.md` since that file is scoped to AI-assistant
wrong claims, not general product behavior.

**Also surfaced by scaling to 3 platforms, then FIXED (D96): `PlatformPrimaryStatus` was
unkeyed.** Real `platform_sim.py` instances were run for all three platforms (genuine 1Hz
app traffic, not just router heartbeats). Reading `PlatformPrimaryStatus` on `control_lan`
showed `matched_publications: 1` and exactly one visible sample at any time — control's
single re-publishing writer for this topic had no per-platform key, so each platform's
update overwrote the same instance rather than coexisting as three trackable values. Fixed
by keying `platform_primary_status` on `msg.source` in `harness_v2/datamodel/act_types.xml`
(see design-decisions.md D96 for the full rationale, including the empirically-confirmed
two-level `key="true"` annotation this needed). Verified live: after restarting the 4
routers + 3 sims to pick up the new type, `control_lan` shows all three sources
(`Platform_30`, `Platform_31`, `Platform_32`) coexisting. Deliberately narrow — every other
`base_type`-wrapped route (`control_command`, `platform_status`, `platform_detail_status`,
`platform_data`, `contact_report`) is untouched and stays unkeyed, matching what was asked.

## Known limitations (v1 scope, not bugs)

- Single vantage point (`control_lan`) — see "Trade-off accepted" above. Deploying one WIS
  instance per node (reading each node's own admin LAN participant) is the natural next step
  if per-node siloed views are wanted instead of/alongside this one.
- Presence only — no RTT/loss edge coloring (Option C, separately tracked).
- A router only known via another router's own roster (not the observer's direct peer list)
  renders as a gray placeholder node.

## Team-membership ring (follow-on, 2026-07-21)

Each known node now renders with a colored ring (border) whose fill color is unchanged
(still known-directly-blue vs. placeholder-gray) — the ring encodes team membership,
derived from a new `RouterHealth.team_partition` field (`platform_wan`'s live
participant-partition set under D103, absorbed from `team_wan`; D83/D87). Since
`RouterHealth` rides `control_wan`/`platform_wan` (matched via `control_wan`'s standing
protected wildcard, D103), this reaches the dashboard with no dependency on a separate
gated discovery mechanism — sidesteps D89's GUID-only-for-cross-team problem entirely for
this specific consumer.

**Classification, not a wire-level tag.** `team_partition` is the RAW partition set —
D83's single-mechanism design means it mixes the node's own protected identity, an
optional team name, and any ad-hoc direct-peer-tap names with no structural marker
distinguishing them. `mesh_graph.js` classifies client-side: it subtracts every node
identity it already knows about (its own + every peer's, from `RouterHealth.router`)
from the set; whatever's left is treated as the team name(s) to color by. **Known,
accepted edge case:** a team named identically to a real node's own identity string
misclassifies as "no team" — not solved here, flagged in both the IDL comment and the JS.

Verified end-to-end (real 3-process mesh: 1 control + 2 platform, `test_mesh_team_partition.py`,
stable 3/3 + the full e2e suite 25/25): before any team is assigned, `team_partition` on
the wire is exactly the protected-identity singleton; after `ADD_PARTICIPANT_PARTITION
platform_wan=TEAM_A` on both platforms, it becomes `{identity, "TEAM_A"}` on both
`RouterHealth` directly and on the control node's own `ActRouterMeshStatus` aggregate —
proving the field survives `PresenceMonitor`'s roster-copy path unmodified.

**Browser-verified 2026-07-22** (see "Browser verification" below) against a 2-node
mesh with no team assigned: the ring-vs-fill classification logic itself (team name
subtracted out of known node identities) was not exercised with an actual second team in
that pass, but the rendering pipeline it depends on (node fill/ring draw, detail panel
pulling `team_partition` off the `vis-network` `DataSet`) is now confirmed live rather
than only hand-read.

## Interactivity (follow-on, 2026-07-21)

Two additions, both pure front-end over the same `ActRouterMeshStatus` samples — no new
subscription, no wire change:

- **Node detail panel** — click any node for a side panel with its full `RouterHealth`:
  role, overall state, presence, last-seen delta, route counts (routes / degraded / error),
  derived team, the raw `team_partition` set, heartbeat seq, and a truncated `config_hash`.
  Placeholder nodes (known only via a peer's roster) and the observer node show an
  explanatory note instead. An open panel live-refreshes in place as new samples arrive.
  All fields are read back from the `vis-network` `DataSet` (stashed on each node at ingest),
  not re-parsed off the wire.
- **Team filter** — the team legend chips are clickable: click one (or several) to
  highlight only those teams' nodes and dim the rest; click again to remove; empty = show
  all. The observer node stays full-opacity regardless (it's your own vantage point).

**Browser-verified 2026-07-22** (see "Browser verification" below): the node detail panel
was exercised in a real headless Chromium against a live 2-node mesh and renders the
expected fields (role, overall_state, presence, routes, team, raw team_partition,
heartbeat_seq, config_hash) sourced correctly off the `vis-network` `DataSet`. The team
**filter** chips were not clicked in that pass (only one, un-teamed node was present, so
there was no second team to filter against) — still unexercised in a real browser.

## A real bug this scope change caught

Building the (now-retired) WAN version first surfaced a genuine WIS-config bug worth keeping
in mind for any future topic/type change: giving `register_type` a **local alias name that
differs from the real struct name** (done there only to dodge an XML-DOM `addChild`
collision with the topic's own name) silently broke SEDP type matching — the reader
advertised type name `RouterHealthType`, the real writer advertised `RouterHealth`, and
Connext logged `type names ... do not match` and never matched (verbosity 4 required to see
it; it's otherwise silent). This config's topic name (`ActRouterMeshStatus`) and struct name
(`RouterMeshStatus`) already differ, so no alias — and no bug class — is needed here.
