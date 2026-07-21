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

## How it works

RTI Web Integration Service (WIS) exposes `ActRouterMeshStatus` over REST + WebSocket. The
page (`static/index.html` + `static/mesh_graph.js`) does a REST GET on load to seed the graph
immediately, then opens a WebSocket for live push updates as the roster changes — same
REST/WebSocket protocol mechanics proven end to end by
[spikes/wis_mesh_dashboard/](../../spikes/wis_mesh_dashboard/README.md) (PASSED 2026-07-21,
against `RouterHealth` — the wire protocol is identical for any topic/type, only the config's
domain/topic changed for this LAN version). WIS serves the static page itself via
`-documentRoot`, so there's no second web server.

`RouterAdminTypes.idl` stays the single source of truth for the wire types —
`config/generate_wis_config.sh` regenerates the DDS-XML type declarations WIS needs
(`rtiddsgen -convertToXml`) and splices them into the WIS config at deploy time. Never hand-
edit a parallel type description; re-run the generator instead.

## Running it against a real mesh

```bash
# from the repo root
export NDDSHOME=/home/rti/rti_connext_dds-7.7.0

# 1. Generate the WIS config into a LOCAL working dir (never the vboxsf share --
#    generated config + WIS's own runtime state are runtime artifacts, repo filesystem rule).
OUT_DIR="$(mktemp -d /tmp/mesh_dashboard.XXXXXX)"
gui/mesh_dashboard/config/generate_wis_config.sh "$OUT_DIR"

# 2. Start WIS: -enableWebSockets is required (off by default, spike finding), and
#    -documentRoot serves this directory's static/ so the page and the DDS bridge share
#    one process/port.
$NDDSHOME/bin/rtiwebintegrationservice \
    -cfgFile "$OUT_DIR/wis_config.xml" -cfgName mesh_dashboard \
    -listeningPorts 8080 -enableWebSockets \
    -documentRoot gui/mesh_dashboard/static

# 3. Start (or already have running) a router_main mesh whose control-role process uses
#    control_lan as its admin participant (control-platform.yaml's default for --role
#    control with --admin-participant control_lan) -- this dashboard reads THAT process's
#    ActRouterMeshStatus.

# 4. Open http://localhost:8080/ in a browser.
```

Cleanup note (same gotcha the spike found): `rtiwebintegrationservice` is a shell wrapper
around a child process (`rtiwebintegrationserviceapp`) that actually holds the port — kill
both, and don't rely on `pkill -x` against either name (Linux truncates `comm` to 15 bytes,
so exact-name matching silently misses both).

## Directory layout

- `config/wis_config.xml.template` — production WIS config (`control_lan` domain `20`, the
  `ActRouterMeshStatus` topic), with `__ROUTER_ADMIN_TYPES__`/`__NDDSHOME__` placeholders.
- `config/generate_wis_config.sh` — regenerates the types XML from the IDL and splices the
  final config; the only place that DDS-XML type description is produced.
- `static/index.html`, `static/mesh_graph.js` — the dashboard page.
- `static/vendor/vis-network.min.js` — vendored (pinned 9.1.13, no CDN dependency at
  deploy time) graph-rendering library.

## Known limitations (v1 scope, not bugs)

- Single vantage point (`control_lan`) — see "Trade-off accepted" above. Deploying one WIS
  instance per node (reading each node's own admin LAN participant) is the natural next step
  if per-node siloed views are wanted instead of/alongside this one.
- Presence only — no RTT/loss edge coloring (Option C, separately tracked).
- No team-membership grouping/coloring on the graph yet (the "network isolation"
  visualization item in the implementation-plan.md roadmap section — not designed yet).
- A router only known via another router's own roster (not the observer's direct peer list)
  renders as a gray placeholder node.

## A real bug this scope change caught

Building the (now-retired) WAN version first surfaced a genuine WIS-config bug worth keeping
in mind for any future topic/type change: giving `register_type` a **local alias name that
differs from the real struct name** (done there only to dodge an XML-DOM `addChild`
collision with the topic's own name) silently broke SEDP type matching — the reader
advertised type name `RouterHealthType`, the real writer advertised `RouterHealth`, and
Connext logged `type names ... do not match` and never matched (verbosity 4 required to see
it; it's otherwise silent). This config's topic name (`ActRouterMeshStatus`) and struct name
(`RouterMeshStatus`) already differ, so no alias — and no bug class — is needed here.
