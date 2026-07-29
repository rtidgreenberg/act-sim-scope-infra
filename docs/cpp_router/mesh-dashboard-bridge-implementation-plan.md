# Mesh Dashboard Bridge — Implementation Plan

> Standalone plan for replacing RTI Web Integration Service (WIS) as the data-bridge behind
> `gui/mesh_dashboard/` with a small purpose-built Python service. Companion to
> [gui-visualization.md](gui-visualization.md) (Phase 16's original design/investigation) and
> [implementation-plan.md](implementation-plan.md) (Phase 16 as shipped) — this document does
> not modify either; it is scoped entirely to the bridge replacement.

## Why replace WIS

Phase 16 (`gui/mesh_dashboard/`) shipped against WIS as the DDS↔HTTP bridge. Three real,
reproduced problems with that dependency motivate a replacement, found across two sessions
of running the live dashboard against a real mesh:

1. **`base_type`/`RTITypes::base_type` XML collision.** `harness_v2/datamodel/ActTypes.idl`'s
   global `struct base_type` collides with an RTI-internal reserved name
   (`RTITypes::base_type`) inside WIS's XML config parser
   (`RTIXMLObject_addChild: XML object with name '::RTITypes::base_type' already exists`).
   Reproduces every time `wis_config.xml.template` is regenerated fresh against the real,
   unmodified IDL. Workaround in use: a `/tmp`-scoped renamed-copy-and-splice dance
   (`sed 's/\bbase_type\b/act_base_type/g'` + `rtiddsgen -convertToXml` + regex-splice into
   the WIS config) reapplied by hand on every mesh relaunch — never fixed at the source
   because the rename is repo-wide (many structs nest `base_type`) and out of scope here.
2. **REST GET seed silently returns `[]` despite valid, current data sitting in the
   reader.** Documented at length in `gui/mesh_dashboard/README.md`: reproduced repeatedly,
   including immediately after a fresh WIS restart with a freshly-matched reader; ruled out
   query-parameter/state-mask causes (tried wide-open masks) and QoS/config causes (a plain
   `rti.connextdds` Python reader on WIS's own generated QoS/topic gets the data instantly,
   every time). WIS's own log shows `Read 0 DynamicData samples from DataReader's cache`.
   Root cause unconfirmed — WIS is closed-source.
3. **Write-endpoint verb inconsistency.** Empirically, a bare POST with an unwrapped JSON
   body (`{"platform_node": "...", "team_name": "..."}`) is what actually succeeds (HTTP
   204) against the real running service; `{"data": {...}}` gives `Unknown member: data`
   (422) and PUT gives `Invalid UPDATE_OPERATION` (422) — despite `mesh_graph.js` and even
   WIS's own manual disagreeing on which verb/body shape is correct. A closed-source service
   whose own documentation doesn't match its own behavior is not a dependency worth
   debugging further.

Beyond these three bugs: WIS itself is a shell-wrapper (`/bin/sh`) around a child
`rtiwebintegrationserviceapp` process (sometimes plus a grandchild Java telemetry helper),
which has made teardown unreliable in practice (`run_mesh.sh down` has twice reported a PID
killed that was still alive, requiring manual `pgrep -af rtiwebintegrationservice` + `kill -9`
follow-up on every layer, since `pkill -x` silently misses Linux's 15-byte-truncated `comm`
names for both wrapper and child).

## Design principle: don't replicate WIS's wire protocol

WIS's REST1 (`sampleFormat=json`) GET/POST contract and its WebSocket bind/HELLO handshake
(`POST /dds/v1/websocket_connections` → open `ws://.../dds/websocket/{name}` → send a
non-JSON HELLO header block → send a JSON `{"kind":"bind",...}` frame → receive
`{"kind":"b_push","body":{"read_sample_seq":[...]}}` frames) is closed-source, undocumented
in places, and it is what produced bug #3 above. Since this plan owns **both** ends of the
wire (the new server and `gui/mesh_dashboard/static/mesh_graph.js`), the simplest path is to
define a small, obvious contract instead of reverse-engineering WIS's — not "clone WIS", but
"replace WIS's job for this one dashboard as simply as possible."

## Architecture

One Python process, one port, no XML service-config file, no wrapper/child-process pair:

```
gui/mesh_dashboard/server/mesh_bridge.py
```

- One `dds.DomainParticipant` on `control_lan` (domain 20), built with the same
  proven-correct pattern used by `router/test_e2e/util/dds_probe.py` and
  `spikes/type_discovery/type_discovery_spike.py`: construct `dds.DomainParticipantQos()`
  directly (not the `default_participant_qos` property), set
  `.transport_builtin = dds.TransportBuiltin.udpv4`, load types via
  `dds.QosProvider(harness_v2/datamodel/gen/ActTypes.xml)`. This is the **real, unmodified**
  generated types file — no XML service-config parsing of the type definitions happens here,
  so bug #1 (`base_type` collision) cannot occur; it is specific to WIS's config-file DOM
  parser, not to loading the same IDL/XML via `QosProvider`.
- One `DynamicData` `DataReader` on topic `ActRouterMeshStatus` (`RouterMeshStatus` type),
  QoS matching the current `MeshStatusReaderQos` (`VOLATILE_DURABILITY_QOS` +
  `BEST_EFFORT_RELIABILITY_QOS`, per D100).
- One `DynamicData` `DataWriter` on topic `ActTeamAssignment` (`TeamAssignment` type), QoS
  matching `TeamAssignmentWriterQos` (`VOLATILE_DURABILITY_QOS` + `RELIABLE_RELIABILITY_QOS`).
- A background poll loop (`reader.take()` every ~150 ms — the same poll-not-listener pattern
  already used throughout `router/test_e2e/`) that:
  - converts each new `DynamicData` sample to a plain `dict` via one small recursive helper
    (struct member → dict entry, sequence/array member → list, primitive member → value),
  - updates an in-process `{router_id: latest_dict}` cache — this is what replaces WIS's
    internal (buggy) reader cache for the REST "seed" path, closing bug #2,
  - broadcasts the new sample to every connected WebSocket client.
- HTTP/WebSocket layer (`aiohttp` — one dependency, gives a static file server, REST routes,
  and WebSocket support in a single asyncio app; see "Open decisions" for the stdlib-only
  fallback):
  - `GET /` and static asset routes → serves `gui/mesh_dashboard/static/` unchanged.
  - `GET /api/mesh_status` → JSON array of the current cache (replaces the WIS REST1 GET
    seed).
  - `GET /ws` → plain WebSocket upgrade; server pushes
    `{"type": "sample", "data": {...}}` per new sample. No HELLO text handshake, no
    `bind_datareader` frame, no separate connection-creation POST — replaces the entire WIS
    bind protocol with a single always-on push socket.
  - `POST /api/team_assignment`, body `{"platform_node": "...", "team_name": "..."}` → one
    write on the `ActTeamAssignment` writer, `204` on success. Closes bug #3 by defining
    exactly one verb/body shape.

### `mesh_graph.js` changes

`WIS_APP`/`WIS_PARTICIPANT`/`WIS_SUBSCRIBER`/`WIS_READER`/`WIS_PUBLISHER`/`WIS_WRITER` and
the `READER_URI`/`WRITER_URI`/`REST_URL`/`WS_CREATE_URL` constants, `seedFromRest()`,
`connectWebSocket()`/`openSocket()`, and `publishTeamAssignment()` are replaced by the
simplified contract above — net **less** JS (the WIS HELLO/bind handshake code is deleted
outright, not replaced 1:1). `ingestSampleArray()`'s per-sample field access
(`data.observer_node`, `data.peers[...]`, etc.) is unaffected, since the bridge's JSON `dict`
conversion preserves the same field names/shape as the DDS type.

## Phases

### Phase A — DDS core (no HTTP)

- `mesh_bridge.py`: participant + reader + writer + `DynamicData → dict` helper + poll loop
  populating the in-memory cache.
- Evidence: run standalone against a live mesh (`run_mesh.sh up --platforms 1`), print the
  cache to stdout, confirm it matches a manual `dds_probe.py`-style read of the same topic —
  no HTTP or browser involved yet.
- Stop/pivot signal: if `DynamicData → dict` conversion can't cleanly handle a nested/sequence
  field actually present in `RouterMeshStatus` (e.g. `peers` array of structs), resolve that
  before moving on — it blocks every later phase.

### Phase B — HTTP/WebSocket layer

- Add the `aiohttp` app: `/`, `/api/mesh_status`, `/ws`, `/api/team_assignment`.
- Evidence: exercise with `curl` (`GET /api/mesh_status`, `POST /api/team_assignment`) and a
  raw Python `websockets` client against `/ws` — confirm push messages arrive on a live
  `ActRouterMeshStatus` change (e.g. SIGKILL a platform router, observe the presence
  transition pushed within one poll interval). No browser yet.
- Stop/pivot signal: if `aiohttp`'s WebSocket broadcast to multiple concurrent clients drops
  or blocks under the DDS poll-thread's lock, reconsider the poll-loop-to-broadcast handoff
  (e.g. an `asyncio.Queue` bridged from the poll thread via `call_soon_threadsafe`) before
  Phase C.

### Phase C — `mesh_graph.js` contract swap

- Replace the WIS-specific constants/functions listed above with the simplified contract.
- Evidence: Playwright verification against a live mesh + dashboard (per the
  `run-mesh-dashboard` skill) — screenshot on load, confirm seeded nodes render, click a node
  for the detail panel, confirm no console errors, then kill a platform router and confirm
  the live WebSocket-driven presence update renders without a page reload.
- Stop/pivot signal: any behavior regression versus the WIS-backed dashboard (team-ring
  rendering, node detail panel, team-filter chips) — those are pure client-side features
  over the same sample shape and must be unaffected.

### Phase D — Swap into `run_mesh.sh` / docs

- Replace the `--with-dashboard` code path's WIS launch (and the `base_type`
  workaround/`generate_wis_config.sh` step it currently requires) with
  `python3 gui/mesh_dashboard/server/mesh_bridge.py --domain 20 --port 8080`.
- Update `gui/mesh_dashboard/README.md`'s run instructions to describe the bridge instead of
  WIS, and retire (or clearly mark historical) the WIS-specific troubleshooting sections that
  no longer apply.
- Evidence: a full `run_mesh.sh up --platforms N --with-dashboard` / `down` cycle with no
  manual `base_type` workaround and no manual `pgrep`/`kill -9` follow-up needed for teardown.

## Open decisions

- **HTTP/WebSocket library — decided: `aiohttp`.** Already installed in this environment
  (3.10.11) — no new dependency to approve/install.
- **Poll interval** — 150 ms, unchanged from the proposal; not re-tuned, no observed issue
  against `EDGE_DECAY_WINDOW_MS = 3000`.
- **`base_type` rename** — deliberately out of scope, per the original reasoning below.

## Implementation status (2026-07-29)

**Shipped, all four phases, verified against a live `run_mesh.sh up --with-dashboard`
mesh (2 platforms):**

- `gui/mesh_dashboard/server/mesh_bridge.py` (Phases A+B) — verified via `curl`/a raw
  `websockets` client: REST seed returns real `ActRouterMeshStatus` JSON, `/ws` pushes new
  samples live, `/api/team_assignment` write returns 204 and is observable on the wire.
- `gui/mesh_dashboard/static/mesh_graph.js` (Phase C) — WIS-specific constants/handshake
  deleted; REST/WS/write URLs point at the bridge; Playwright-verified against a live
  2-platform mesh (nodes/edges render, live WebSocket push updates the status bar and
  graph with no reload, no console errors).
- `harness_v2/scripts/run_mesh.sh` (Phase D) — `--with-dashboard` now launches
  `mesh_bridge.py` directly (single process, single PID in `pids.txt`); the `base_type`
  workaround, `generate_wis_config.sh` call, and wrapper/child PID-polling dance are gone
  from the launch path. Full `down` → `up --platforms 2 --with-dashboard` cycle verified
  clean (no stray `/dev/shm` segments, dashboard reachable immediately).
- `gui/mesh_dashboard/README.md` updated: "How it works"/"Running it against a real mesh"
  describe the bridge; the WIS-era investigation record (protocol details, the two real
  WIS bugs) is kept as historical record, explicitly marked as no longer how the dashboard
  runs.

**One real finding during implementation, not anticipated by the original design section
above:** `QosProvider(types_xml)` itself hit the *exact same*
`RTIXMLObject_addChild: ... '::RTITypes::base_type' already exists` collision the WIS
config parser hits — but only when launched from `run_mesh.sh`, not standalone. Root
cause: `run_mesh.sh` exports `NDDS_QOS_PROFILES` (including this same `ActTypes.xml`) for
`platform_control.py`'s benefit; Connext auto-loads that at process init, so the bridge's
own explicit `QosProvider(types_xml)` call parsed the identical file a **second time** in
the same process — the collision is triggered by double-loading the same types XML in one
process, not specifically by WIS's XML-config-parsing path as originally assumed. Fixed by
`_qos_provider_for()` in `mesh_bridge.py`: if `NDDS_QOS_PROFILES` already includes the
exact same (resolved) path, reuse `QosProvider.default` instead of re-parsing. Confirmed
fixed by a full `down`/`up --with-dashboard` cycle plus a Playwright-driven visual check.

