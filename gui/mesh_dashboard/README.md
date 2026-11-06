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

## Traffic layers and compact router diagnostics

### WAN Wire Delivery

Delivery measures **source WAN interface TX to destination WAN interface RX**,
not LAN publication, DDS reader acceptance, or application consumption. Each node's
traffic monitor writes `wan_events.jsonl` by parsing raw RTPS in domain 200.
The EMANE launcher supplies `WAN_DELIVERY_NODES` with the actual node/IP map;
host-only captures without a distinct per-node WAN mapping cannot provide this metric.

Samples match on domain, physical writer GUID, RTPS sequence, source IP and
destination IP. IP and RTPS fragmentation are reconstructed; repair traffic and
duplicate copies to the same destination node count once. Unicast fanout counts
once per destination node, not per reader or UDP port. Multicast and unmapped
destinations are explicitly unsupported, not assigned inferred recipients.

Rows are grouped by **discovered writer QoS**: Reliable, Best Effort, then
Unknown QoS. A complete writer discovery ParameterList may omit default-valued
QoS: an absent reliability parameter restores the standard DataWriter default,
Reliable (DDSI-RTPS 2.3 Table 9.14 and DDS 1.4 RELIABILITY defaults). Explicit
Best Effort overrides that default. Missing discovery stays unknown; malformed
discovery is reported as a capture error, not defaulted. Topic names and local
configuration do not determine captured QoS. The ParameterList terminator is
required, but its length is ignored as specified by DDSI-RTPS 2.3 section 9.4.2.11.
Reliable samples have a 15-second observation grace period, other groups 2 seconds.
The rolling window is anchored to source sends, so late repair corrects an earlier
missing observation while its send remains in the window. A reporting-mode change
does not reinterpret LAN samples or erase captured history. Mode is an explicit
display filter only.

**Avg recovery** reports the mean original WAN send to complete WAN receipt for
Reliable samples received after a confirmed retry. Confirmation requires an
ACKNACK or NACK_FRAG requesting that writer/sequence at the source, followed by
repeat DATA/DATA_FRAG for the same destination and matching reader. Original
fanout copies, acknowledgements without missing bits, first-attempt deliveries,
and still-missing samples do not contribute. A fragmented receipt finishes when
all fragments arrive, not when its first fragment arrives. Each sample contributes
once, including multiple-retry recoveries, in its original rolling send window;
the loss grace period does not delay this metric. The parenthesized count exposes
the average's sample size. No confirmed recoveries shows a dash, not zero;
unavailable capture coverage shows Unknown. Missing request/retry observations
can omit recoveries, so a dash does not prove that no retransmissions occurred.
This includes loss detection and retry delays, not just repair-packet latency.

A left-side reading guide explains the continuously sliding 30-second window,
why steady-rate counts level off, and the difference between settled summary
cards and newer receives in the table. Reliable rows use blue and Best Effort
rows use teal, matching the guide; Unknown QoS stays neutral. Text headings
identify each group without relying on color. On narrow screens the guide
stacks above the counters.

Source and Destination filters constrain the direction of the displayed flows.
The Topics menu supports any combination of topics, with Select all and Clear
controls. These filters also update the comparison cards; they do not change
capture or accounting. The existing either-end Node and Mode filters remain
available, including mission-selection integration. Explicit source, destination,
and topic choices survive a temporary absence of observations.

The **Configured WAN QoS** sidebar lists the selected topics' configured WAN
writer and reader profiles, reliability, history/depth, durability, deadline, and
liveliness lease. With all topics selected, expand individual topic headings.
These settings are resolved from the active router YAML's QoS libraries once at
dashboard startup, including inherited XML policies; the reader and writer can
have different settings. Dedicated RouterHealth/RouterLinkProbe profiles are
included alongside forwarding routes. Auto-QoS legs are marked as runtime-learned
instead of receiving invented static settings. This is a configuration reference,
**not a writer association or a fallback for observed QoS classification**.
Configured enabled states follow side-specific overrides, then the route-level
setting, defaulting to disabled, just like the router. They are startup settings,
not the current state after runtime mode changes. Both scalar and per-role
`presence_participant` configurations are supported.

The canonical launchers pass `--router-config` to the bridge. Standalone launches
should pass the YAML actually used by the router; without it, the sidebar
explicitly reports unavailable configuration. Config edits are not hot-reloaded
as though they had already been deployed. `GET /api/delivery_config` exposes this
startup configuration snapshot. Capture diagnostics remain fleet-wide and are
labeled separately from the filtered table.

Capture status exposes fresh contributors, unsupported paths, incomplete samples,
and invalid observations. Flows without fresh enabled source/destination captures
show Unknown loss and are excluded from comparison summaries. Coverage must span
the entire selected delivery window: startup, capture rotation/restarts, unhealthy
capture reports, or missing reporting intervals restart this coverage period.
Parser-error totals remain cumulative diagnostics; only new errors invalidate
the reporting interval. An error-counter reset also restarts coverage. A historical
error does not prevent recovery after a full clean window.
A fresh live report alone does not establish historical coverage; loss and recovery
averages stay Unknown until a full uninterrupted window is available. Kernel capture drops
are unreported, not zero: missing observations are not proof of RF loss. Captures
on different hosts require synchronized clocks. Existing application `events.jsonl`
audits remain separate diagnostics and never feed this tab.

The graph starts in the C2 operator view. **Infrastructure stats** is unchecked on
every fresh page. Enabling it adds DDS heartbeat/probe, discovery,
reliability/control, and mixed/unknown chart series. Node DDS TX/RX rates remain
visible independently of that checkbox. Mesh presentation is DDS-only: MAC
values are retained in diagnostic collection/API responses, not drawn as a
competing traffic rate or used to scale the chart.
It changes presentation only. RouterLinkStats, link activity, health, connected
peers, route badges, and mission/delivery operations remain available when unchecked.

**Link activity** is a right-edge drawer that starts collapsed as a vertical tab.
Click its title to expand it alongside the graph or collapse it again. Filters
stack above the scrollable activity list, and the drawer stops above the WAN
chart. The graph reserves the drawer's width so activity does not cover nodes.

Normal chart rates count known application-carrying **Ethernet frames**, including
their transport overhead and any piggybacked protocol submessages, not just serialized
application payload. RouterHealth and RouterLinkProbe are infrastructure; debug
diagnostic traffic is operator-selected application telemetry. LAN-only RouterLinkStats
publication bytes do not enter the domain-200 WAN chart. Ambiguous topic roles stay
mixed/unknown and produce a lower-bound warning rather than fabricated application bytes.
SPDP2 bootstrap/configuration traffic is recognized by its verified built-in writer
IDs, without requiring SEDP topic metadata. Other RTI-specific writer IDs are not
automatically treated as discovery.

The DDS values are domain-200 RTPS traffic captured at `emane0`, not native EMANE
MAC counters and not payload-only throughput. Node rates include all captured DDS
traffic in each direction. Network chart rates sum sender TX rates, never RX copies,
using the latest capture window common to all expected contributors.
Each chart series title appears above its three-second average; the stat blocks
wrap on narrow screens and the graph spacing follows the rendered panel height.

Monitor schema 3 assigns each physical frame to its original capture-time window,
not the time tshark finishes decoding it. Reporting waits two seconds for decoding
and holds windows containing unresolved IP fragments until reassembly/expiry.
Late rows are explicitly counted and invalidate rates rather than being moved
into the next interval. Every window intersecting a capture restart is flagged.
Capture runs continuously through an owned `dumpcap` pipe, without five-minute
capture restarts or raw capture files accumulating on disk. Finite `tshark`
sessions decode batches after one second, 2,048 packets, or 2 MiB, whichever comes
first, so decoder history cannot grow with run duration. Unfinished IPv4 fragments
and fragmented SEDP samples are replayed into the next session; replay rows never
enter accounting or delivery events twice. The replay budget is 16 MiB; exceeding
it fails capture explicitly rather than silently discarding reassembly state.
Decoder timeouts/failures and missing output rows are explicit capture failures.
All children and pipes are reaped before monitor shutdown completes.
Initial continuous-pipe verification on 2026-11-06 crossed the former five-minute restart boundary:
all seven nodes retained their capture/decoder processes, with zero restarts and
no gaps in 370–372 settled one-second windows per node. A 315-second browser/API
check found no unavailable network rates, all six peers healthy, and no browser
errors. See the [retained continuity report](../../debug/test_reports/capture-continuity-after-20261106.json).
That initial check did not test long-run decoder memory. The bounded-session
follow-up processed 480,000 offline frames with exact byte/packet conservation:
parent RSS stabilized at approximately 26 MiB and peak decoder RSS stayed below
145 MiB, instead of accumulating per-packet state throughout the run. Offline
regressions also verify reordered/duplicate IPv4 fragments and fragmented SEDP
across real decoder sessions.
After deployment, all seven nodes retained their capture/monitor processes with
195–196 consecutive settled windows and no gaps or late rows. Monitor RSS stayed
approximately 23–25 MiB during the 135-second live browser/API check; all six peers
remained healthy and every sampled network rate was valid. See the
[memory stress report](../../debug/test_reports/bounded-decoder-memory-20261106.json)
and [live verification](../../debug/test_reports/bounded-decoder-live-20261106.json).
Node and chart averages require three contiguous seconds of valid capture coverage;
gaps, restarts, invalid rows, stale sources, and incomplete network coverage clear
the average and show unavailable/warming instead of a depressed rate. Graph lines
break at invalid windows and missing capture intervals. Older processing-time monitors are explicitly unavailable
until upgraded. Freshness uses the report time, while bandwidth uses capture time.

Stale/missing contributors are explicitly unavailable/partial. Packet reassembly counts
all physical fragments in their original windows; monitor startup precedes DDS discovery and cached full
writer identities support capture rotation/restart. Live kernel-drop counts are
unreported, not zero; capture health means no detected parser/fragment/rotation error.
Container startup waits for the canonical launcher to establish the nominal RF matrix
before enabling DDS; use the owned mesh lifecycle rather than starting individual nodes.

Runtime participant profiles inherit `ACT_QOS_LIB::participant_discovery_base`,
which disables RTPX default-domain announcements with an infinite announcement
period. The journal subscriber uses the external UDPv4-only
`router_journal_participant` profile, and standalone bridge launches also use the
XML LAN base rather than factory defaults. Ordinary SPDP/SPDP2 and SEDP
discovery are unchanged. RTI tools must explicitly join the desired domains;
automatic domain discovery through RTPX is no longer available.

LAN profiles inherit `ACT_QOS_LIB::lan_participant_base`, which denies `emane0`
for UDPv4. This prevents LAN participants from advertising/receiving on the radio
interface and sending their discovery multicast there, while preserving their
other LAN interfaces and existing loopback allow-list. WAN profiles do not inherit
this deny-list; the EMANE WAN profile still explicitly allows `emane0`.
DDS domain IDs separate discovery/matching and UDP port ranges, not physical
interfaces: unrestricted LAN discovery can otherwise be sent over the radio
directly, without a forwarded application route. An interface deny-list is not a
firewall for explicitly addressed unicast; outgoing unicast still follows OS routes.
Restart all mesh participants through the owned lifecycle to apply the XML change;
running participants do not hot-reload it.

RouterHealth is a 1 Hz Best Effort, KEEP_LAST(1) latest-state heartbeat on the WAN;
new heartbeats supersede missed ones without reliable repair traffic. Both endpoints
use the external `router_health` profile. Commands and acknowledgements retain their
Reliable event profiles. Delivery groups health under Best Effort with a 2-second
observation grace period; presence deadline/liveliness timing is separate and unchanged.
RouterHealth contains compact route summaries and the always-on peer roster.
Full platform `ActRouterDiagnostics` snapshots follow `platform_debug_status` only
while debug is enabled. C2-local diagnostics remain LAN-local. The bridge exposes
`GET /api/router_diagnostics` and WebSocket `router_diagnostics` updates; the graph
refreshes diagnostics through REST so freshness is checked even if publishing stops.
Full detail is displayed only for a current matching configuration/revision and
debug-entry timestamp. The expandable **Full router diagnostics** section includes
the complete snapshot, including desired configuration, matching, QoS and errors.
Compact state remains authoritative.

Aggregate responses include `aggregated_at_ms` to order REST/WebSocket snapshots
independently of the newest source timestamp. Late contributor corrections replace
the current chart point and its averaging weight rather than adding duplicate history.
Reported debug-route state, not a remembered UI request, controls diagnostics eligibility.
The browser expires displayed diagnostics even while refresh requests are pending,
invalidates detail after failed refreshes, and never reopens a closed panel on polling.

**Schema rollout:** rebuild/restart routers and all consumers with the matching
IDL/generated XML. Mixed old/new RouterHealth and RouterMeshStatus are not a supported
rolling deployment. See [measurements, implementation and remaining acceptance gates](../../docs/cpp_router/emane-stats-and-health-optimization-plan.md).

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

**Also surfaced by scaling to 3 platforms, then FIXED (D96): `PlatformInitStatus` was
unkeyed.** Real `platform_sim.py` instances were run for all three platforms (genuine 1Hz
app traffic, not just router heartbeats). Reading `PlatformInitStatus` on `control_lan`
showed `matched_publications: 1` and exactly one visible sample at any time — control's
single re-publishing writer for this topic had no per-platform key, so each platform's
update overwrote the same instance rather than coexisting as three trackable values. Fixed
by keying `platform_init_status` on `msg.source` in `harness_v2/datamodel/act_types.xml`
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
