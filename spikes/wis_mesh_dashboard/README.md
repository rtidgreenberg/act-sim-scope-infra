# wis_mesh_dashboard — RouterHealth over RTI Web Integration Service

Proves whether a real `router_main` mesh's `RouterHealth` heartbeats (mesh-wide presence:
nodes + who-sees-who edges) show up over RTI Web Integration Service's REST and WebSocket
APIs, per [PLAN.md](PLAN.md). **Both do — this spike PASSED all four checks.**

## Result: PASS/PASS/PASS/PASS

Two back-to-back runs of `./run_spike.sh` from the repo root, each on a fresh domain-1202
mesh, both ended:

```
[run_spike] ==== RESULTS ====
[run_spike] XML validates + WIS config spliced: PASS
[run_spike] WIS starts against it:              PASS
[run_spike] REST returns real RouterHealth data: PASS
[run_spike] WebSocket push arrives:              PASS
```

`run_spike.sh` is **idempotently re-runnable**: each run does `mktemp -d` for a fresh local
tmp working dir, generates its own types XML + spliced WIS config there, and the cleanup
trap kills every process it started (by PID, not by name — see "Gotcha" section below) and
reports a stray-`/dev/shm` check. Two consecutive runs both exited 0 with all four PASS and
left no stray `router_main`/`rtiwebintegrationservice*` processes or `/dev/shm` segments
behind.

## What was actually run

```bash
cd /home/rti/act-sim-scope-infra
./spikes/wis_mesh_dashboard/run_spike.sh
```

Internally (see `run_spike.sh` for the literal commands), in order:

1. `rtiddsgen -convertToXml -d <tmp> router/admin/RouterAdminTypes.idl`
2. Python splice of the generated `<types>...</types>` block into
   `config/wis_config.xml.template`'s `__ROUTER_ADMIN_TYPES__` placeholder, written to
   `<tmp>/wis_config.xml`.
3. Two `router_main` processes, cwd = repo root, against
   `config/e2e_presence_fixed_domains.yaml`:
   ```bash
   router/build/router_main --config spikes/wis_mesh_dashboard/config/e2e_presence_fixed_domains.yaml \
       --role platform --admin-participant platform_lan
   router/build/router_main --config spikes/wis_mesh_dashboard/config/e2e_presence_fixed_domains.yaml \
       --role control --node-name Control_20 --name e2e-control-role --admin-participant control_lan
   ```
   (identity strings match `router/test_e2e/conftest.py`'s `router_pair` fixture exactly.)
4. `rtiwebintegrationservice -cfgFile <tmp>/wis_config.xml -cfgName mesh_dashboard -listeningPorts 8080 -enableWebSockets`
5. `curl` the REST endpoint (below), retrying up to 6x at 1s intervals for a sample with a
   real `<node>/<router>`-shaped `router` field and non-empty `peers_seen`.
6. `curl -X POST http://localhost:8080/dds/v1/websocket_connections` to create a named
   WebSocket connection, then `ws_probe.py` (a from-scratch RFC 6455 client — see "No
   WebSocket tooling installed" below) to connect, HELLO-handshake, BIND to the
   `RouterHealthReader` resource, and wait up to 8s for a `b_push` sample.
7. Kill both `router_main` PIDs and the WIS process tree, check `/dev/shm`, print PASS/FAIL.

## Working URLs (reusable as-is)

- **REST**: `GET http://localhost:8080/dds/rest1/applications/MeshDashboardApp/domain_participants/MeshParticipant/subscribers/MeshSubscriber/data_readers/RouterHealthReader?sampleFormat=json&removeFromReaderCache=false`
- **WebSocket connection creation**: `POST http://localhost:8080/dds/v1/websocket_connections`, header `Content-Type: application/dds-web+json`, body `[{"name": "MeshWsConn"}]` (array-wrapped — see contradiction #2 below).
- **WebSocket connect**: `ws://localhost:8080/dds/websocket/MeshWsConn` (note: `/dds/websocket/`, not `/dds/v1/` — see contradiction #1).
- Real sample observed over both paths, e.g.: `{"router": "Platform_30/e2e-presence", "role": "platform", ..., "peers_seen": [{"router": "Control_20/e2e-control-role", "presence": "PRESENCE_ALIVE"}]}`.

## Where reality contradicted the two "established facts"

### Fact #1 (rtiddsgen -convertToXml output) — CONFIRMED, no adjustment needed to the types themselves

`rtiddsgen -convertToXml -d <dir> router/admin/RouterAdminTypes.idl` produces exactly what
was claimed: a clean `<dds><types>...</types></dds>` document, flat (no IDL module
wrapper), with `<struct name="RouterHealth">` present verbatim, `router` marked `key="true"`,
`peers_seen` a `nonBasic` sequence of `RouterPeerRef`. The splice (grab everything between
`<types>` and `</types>` inclusive, paste into the WIS config's placeholder) worked with
**zero changes** to the generated XML.

However, one **WIS-config-authoring** issue surfaced that has nothing to do with
`-convertToXml`'s output: WIS's `<register_type name="...">` and `<topic name="...">` share
the **same per-domain XML-DOM namespace**. The natural-looking config —
`<register_type name="RouterHealth" type_ref="RouterHealth"/>` next to
`<topic name="RouterHealth" register_type_ref="RouterHealth"/>` — fails with:
```
RTIXMLObject_addChild: XML object with name '::RouterAdminDomainLibrary::MeshWanDomain::RouterHealth' already exists
```
Fix: give `register_type` a distinct local alias name (`RouterHealthType`) while keeping
`type_ref="RouterHealth"` (the real struct name) and the `<topic name="RouterHealth">` name
unchanged (it has to match the actual DDS topic name `PresenceMonitor.cxx` publishes on).
See `config/wis_config.xml.template`'s comment at the `domain_library` block.

### Fact #2 (REST/WebSocket URL shapes) — REST confirmed; WebSocket assumption was WRONG on three points

**REST** matched exactly as assumed: `GET /dds/rest1/applications/{app}/domain_participants/{dp}/subscribers/{sub}/data_readers/{dr}`. No surprises.

**WebSocket** did **not** match the assumed shape
(`ws://<host>:8080/dds/v1/{connectionName}`, "bound to the DataReader resource URI after
connecting"). The real protocol, per the actual shipped manual page
(`$NDDSHOME/doc/manuals/connext_dds_professional/services/web_integration_service/using_websocket_api.html`,
Sections 6.1–6.2) and confirmed empirically against the running service:

1. **WebSockets are disabled by default.** `rtiwebintegrationservice` refused every
   WebSocket-related request with HTTP 404 (both the connection-creation POST and the WS
   connect itself) until launched with `-enableWebSockets`. Nothing in the assumed facts
   mentioned this flag.
2. **The connect URL prefix is `/dds/websocket/<name>`, not `/dds/v1/<name>`.**
   `/dds/v1/websocket_connections` is only the REST endpoint used to *create* the named
   connection resource; the actual `ws://` URL you connect to is
   `ws://<host>:<port>/dds/websocket/<name>`. There is no implicit "bind to a DataReader URI
   by connecting" — binding is a separate, explicit step (point 4).
3. **The manual's own example body for connection creation is wrong** (or at least
   incomplete). Table 6.1 in `using_websocket_api.html` shows `Example Body { "name" :
   <websocket_name> }` (a bare JSON object). Sending that body verbatim (`{"name":
   "MeshWsConn"}`) got HTTP 422 `INVALID_INPUT`. The request only succeeds (HTTP 204) with
   the body **array-wrapped**: `[{"name": "MeshWsConn"}]` — which matches the older shipped
   ShapesDemo JS example
   (`$NDDSHOME/resource/template/rti_workspace/examples/web_integration_service/websockets/simple_shapes_demo/js/shapes_demo.js`),
   not the current manual's own documented example.
4. **Binding to a DataReader is an explicit post-connect handshake, not implicit in the
   URL.** After the WS upgrade, the client must first send a HELLO message (a
   colon-separated, CRLF-terminated string with **all four** fields — `Content-Type`,
   `Accept`, `OMG-DDS-API-Key` (present, even if empty — a client that omits it entirely
   gets back `"HELLO FAIL: Malformed message"`, confirmed by testing), `Version`), wait for
   `HELLO_OK`, then send a JSON `{"kind": "bind", "body": [{"bind_kind":
   "bind_datareader", "bind_id": "...", "uri": "<REST resource path>"}]}` message. Only
   after that does the server start pushing `{"kind": "b_push", "bind_id": "...", "body":
   {"read_sample_seq": [...]}}` messages.

**Net effect**: the WebSocket half of "established fact #2" was wrong on the URL shape, the
required enablement flag, and (per the manual itself) the connection-creation body shape.
These should be corrected in `docs/cpp_router/gui-visualization.md` and this spike's
`PLAN.md` (not touched here per instructions), and are candidates for a
`docs/connext-ai-issues/connext-ai-issues.md` entry (also not touched here — flagging for
whoever does that pass).

## No WebSocket tooling installed

Neither the Python `websockets` package nor a `wscat`-like CLI client is installed on this
VM (`python3 -c "import websockets"` fails; `which wscat` finds nothing). Rather than skip
the check, `ws_probe.py` implements just enough of RFC 6455 by hand — HTTP Upgrade
handshake, masked client→server text frames, unmasked server→client frame decoding
(text/ping/close) — plus WIS's HELLO/BIND handshake described above. It has no external
dependencies beyond the Python 3 standard library.

## Other operational gotchas found while building this

- **`rtiwebintegrationservice` is a `/bin/sh` wrapper**, not the server itself — it execs a
  child process, `rtiwebintegrationserviceapp` (visible in `ps aux` under
  `$NDDSHOME/resource/app/bin/x64Linux4gcc8.5.0/`), which is what actually holds the DDS
  participant and the listening socket. Killing only the wrapper's PID leaves the real
  server (and port 8080) running — verified directly (a "killed" WIS kept answering `curl`
  afterward). `run_spike.sh`'s cleanup finds the child via `pgrep -P <wrapper_pid>` and kills
  both.
- **`pkill -x` doesn't work against either process name here.** Linux truncates the `comm`
  field used for `-x` matching to 15 bytes; both `rtiwebintegrationservice` (24 chars) and
  `rtiwebintegrationserviceapp` (27 chars) exceed that, so an exact-name `pkill -x` silently
  matches nothing for either. `run_spike.sh` tracks and kills explicit PIDs instead (per the
  repo rule of `-x` or explicit PIDs, never `pkill -f`).
- **XML comments can't contain a literal `--`.** An early draft of
  `config/wis_config.xml.template` used `--` as an em-dash-style separator inside an XML
  comment describing the splice step; this is illegal per the XML spec and broke parsing
  only *after* splicing (the comment itself parses fine standalone but the well-formedness
  checker in `run_spike.sh` caught it as "not well-formed (invalid token)"). Fixed by
  rewording the comment.
- **Careful with placeholder tokens inside their own explanatory comments.** An early draft
  of the template *described* the `__ROUTER_ADMIN_TYPES__` placeholder by writing its exact
  name in prose right above it. Since the splice step is a plain `str.replace()`, it replaced
  *both* occurrences — clobbering the comment with the entire types block. Fixed by wording
  the comment without repeating the literal token.

## Config summary

- `config/e2e_presence_fixed_domains.yaml`: `router/config/e2e_presence.yaml` with domains
  fixed at `control_lan=1201`, `wan=1202`, `platform_lan=1203` (checked nothing else was
  bound to these before picking them; low thousands per the repo's domain-id ceiling rule).
- `config/wis_config.xml.template`: one `domain_library` (`RouterAdminDomainLibrary`,
  domain `MeshWanDomain` @ 1202) registering `RouterHealth`, one `web_integration_service`
  (`mesh_dashboard`) with `MeshDashboardApp` → `MeshParticipant` → `MeshSubscriber` →
  `RouterHealthReader`. No custom QoS: `PresenceMonitor`'s heartbeat QoS (RELIABLE +
  TRANSIENT_LOCAL + KEEP_LAST(1) + DEADLINE 2s + AUTOMATIC liveliness 3s) is RxO-compatible
  with WIS's default (BEST_EFFORT/VOLATILE/no-deadline) reader QoS, confirmed by the reader
  actually matching and receiving data with no QoS block added.
- `run_spike.sh`: orchestrator described above.
- `ws_probe.py`: standalone RFC 6455 + WIS-handshake client, no non-stdlib dependencies.

## Logs from the verifying runs

Left in place per instructions (not deleted): `/tmp/wis_mesh_dashboard.UrihCr/` and
`/tmp/wis_mesh_dashboard.YzlLeP/` (two consecutive successful runs). Each contains
`RouterAdminTypes.xml`, `wis_config.xml`, `router_control.log`, `router_platform.log`,
`wis.log`, `rest_responses.log`, `ws_create_response.log`, `ws_probe.log`. A fresh run
creates a new `/tmp/wis_mesh_dashboard.XXXXXX/` and prints its path as the last line of
output.
