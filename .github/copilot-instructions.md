# Repo context & guardrails for AI assistants

This repo is developed on an AWS instance. The checkout is on the instance's normal
filesystem.

## Runtime artifact safety (READ FIRST before running anything)

`debug/logs`, `debug/pcap`, and per-node directories such as `debug/platform_30_debug` are
generated-artifact roots. Use the per-node directories for current container logs and
`debug/test_reports/<test_id>.*` for compact retained summaries; clear raw artifacts before
each run and keep source/config files separate from artifacts.

Use `/tmp/...` (or `mktemp -d`) for disposable working directories, FIFOs, SQLite/DWH files,
and other lock-sensitive transient state. Do not leave processes holding runtime files open
across forced termination; use the owned harness lifecycle for cleanup.

## DDS / Connext runtime hygiene

- **Prefer UDPv4-only transport** for single-host test rigs
  (`<participant_qos><transport_builtin><mask>UDPv4</mask></transport_builtin>`). Then a
  `SIGKILL`ed participant cannot leak `/dev/shm` shared-memory segments.
- After kill-based tests, clean up: `pkill -x state_reader; pkill -x state_writer` (use
  `-x` exact-name match — `pkill -f` can match your own shell), and check `/dev/shm` for
  stray `RTI*`/`dds*` segments.
- Loopback UDP works for co-located test processes; isolate concurrent tests by DDS
  **domain id**.
- **Keep every domain id <= 232.** RTPS maps a domain to a UDP port as `PB + DG*D`
  (`7400 + 250*D`). That exceeds 65535 at **D = 233** and then wraps mod 65536, landing on
  an arbitrary port — sometimes in the privileged <1024 range, where the bind fails with
  `RTIOsapiSocket_bindWithIP: OS bind() failure ... Permission denied`. Measured here:
  `232 -> 65400` (ok), `233 -> 114`, `1023 -> 1006`, and `1046` gives a WAN port range of
  `[268900, 269149]` that matches no real traffic at all.
  An earlier version of this note quoted "~5900–6000" as the ceiling; that was **one
  observed instance of the wrap landing low, not the limit**, and following it cost a full
  e2e run (2026-08-11: `E2E_DOMAIN_BASE = 1000` failed exactly this way). Above 232 the
  failure is not a threshold you can stay under — it is a wrap that can land anywhere, so
  a domain id can look fine and silently mis-bind or capture nothing.
  Live domains in this repo: `20` (control_lan), `30–99` (platform_lan, one per platform),
  `200` (WAN). The e2e suite allocates from `101–199` (`conftest.E2E_DOMAIN_BASE`);
  `0–19`, `21–29` and `201–232` are the other free windows.

## Mesh debugging safety

- Treat a running mesh as immutable during diagnosis: inspect logs, bridge JSON, and code
  read-only first. Do not patch a generated config or manually kill/relaunch one router in a
  live mesh.
- Use `harness_v2/scripts/run_mesh.sh up` and `down` as the only normal mesh lifecycle
  commands. The runner owns its PIDs and cleanup; do not bypass it with ad hoc restarts.
- Reproduce a configuration failure in a fresh, isolated `/tmp` workdir with **one** platform
  first. Scale to a multi-boat mesh only after that reproduction is understood and torn down.
- Before starting any new mesh, verify no prior mesh processes and no `/dev/shm/RTI*` or
  `/dev/shm/dds*` segments remain. After every teardown or VM freeze, repeat that check before
  another DDS run.
- Prefer unit tests and builds for controller/UI diagnostic changes. Do not launch a live mesh
  solely to validate code that can be exercised in-process.
- For EMANE RF Pipe discovery tests, preserve the validated pathloss-event invocation in
  `harness_v2/scripts/run_container_baseline.sh`: use the range form
  `emaneevent-pathloss ... "${source_nem}:${target_nem}" 0`. The installed utility also
  documents target/reference controls, but validate packet counters before changing the
  harness's nominal RF setup.
- In this installed EMANE utility, the valid ascending range form `low_nem:high_nem` creates
  the bidirectional receiver/transmitter matrix for that contiguous NEM range; reversed ranges
  such as `2:1` are invalid, and wide ranges such as `1:4` affect every NEM pair in the range,
  not only the selected endpoints. For GUI one-way impairment, use the validated pair-specific form
  `emaneevent-pathloss -i eth0 -g 224.1.2.8 -p 45702 <tx_nem> <db> -t <rx_nem> -r <tx_nem>`.
  The same form with `0` resets that direction. A running container with zero
  `emane_mac_rx_*` and zero Domain 200 discovery packets is an RF/event path failure, not
  evidence of a DDS QoS mismatch.
- A successful `emaneevent-pathloss` exit status is not sufficient validation: the incomplete
  target form `source db --target target` accepted an impairment command yet removed Platform
  31/32 DDS peers. Validate EMANE RX/drops, Domain 200 discovery/data counters, route delivery
  percentage, and all expected `PRESENCE_ALIVE`/`ROUTER_OK` peers after every GUI impairment
  and reset. A good live GUI check is: apply `100 dB` to one direction, observe that only the
  selected route's delivery drops, reset that same direction to `0 dB`, wait for the delivery
  window to roll forward, and confirm delivery returns to 100% with all unrelated peers alive.
- RF Pipe pathloss is not a smooth application-delivery percentage knob in the current config:
  with `txpower=0 dBm`, `bandwidth=1 MHz`, `systemnoisefigure=4 dB`, and the packaged PCR
  curve, `90 dB` still maps to about 100% probability of reception while `100 dB` maps to the
  lossy region. See [`docs/emane/pathloss-scale-and-gui-control.md`](../docs/emane/pathloss-scale-and-gui-control.md)
  before interpreting or changing dashboard dB controls.
- For predictable dashboard latency, loss, and bandwidth controls, use CommEffect, not raw
  RF Pipe pathloss. CommEffect must be present as a shim in each generated EMANE NEM, and this
  installed EMANE version accepts `defaultconnectivitymode` (not `defaultconnectivity`) plus
  `enablepromiscuousmode`. Keep `defaultconnectivitymode=on` so nominal traffic flows before
  the first CommEffectEvent; with it off, startup traffic can drop as `No Profile`. Validate
  event processing with `shim0 EventReceptionTable` event `103` on every affected NEM. Reset
  with zero values; `unicast=0` and `broadcast=0` mean no bitrate limit. After a receiver
  processes a CommEffect event, omitted transmitters can be dropped as `No Profile`, so GUI
  pair controls must seed zero-effect profiles for all known transmitter NEMs on each affected
  receiver before overlaying the selected pair's impairment. See
  [`docs/emane/pathloss-scale-and-gui-control.md`](../docs/emane/pathloss-scale-and-gui-control.md)
  for the validated command form and status checks.
- Treat CommEffect state as directional (`tx_nem -> rx_nem`), even when the GUI presents a
  bidirectional pair. A bidirectional apply must record and reset both directional profiles;
  otherwise a later one-way reset can leave the reverse path impaired while the dashboard shows
  only the last zeroed command. This especially affects reliable DDS because ACKNACK/NACK and
  heartbeat repair traffic needs the reverse WAN path to get through.
- Delivery dashboard counts are sequence-matched app-level audit facts from `events.jsonl`, not
  inferred packet counters: sent/received keys are `(run_id, topic, source_node, sequence)` plus
  receiver. `ControlCommand` and `PlatformCommandAck` use a 15 s grace window before missing
  samples count as lost; periodic status/report topics use 2 s. A 100% loss row under sustained
  bidirectional impairment can be real app-level non-delivery while DDS repair traffic is itself
  impaired, not necessarily a dashboard accounting bug.
- When an EMANE experiment disrupts mesh discovery, do not attempt incremental manual repairs
  or recreate one router. Recreating only `control-20` can leave DDS discovery empty even when
  EMANE telemetry is fresh. Use the owned `run_container_baseline.sh down` then `up` lifecycle
  to rebuild the nominal RF matrix and wait for every peer in `/api/mesh_status`; process uptime
  or the harness smoke check alone does not prove all platforms rediscovered.
- The WAN discovery design is explicit unicast UDPv4: Domain 200 uses `initial_peers`, the
  WAN transport mask is `UDPv4`, and `WAN_RECEIVE_MULTICAST=0`. When peers disappear, first
  compare EMANE TX/RX and `traffic_stats.jsonl` discovery counters, then inspect Connext
  matched-participant warnings; do not treat repeated `Failed to get discovered_participant_data`
  messages as the root cause until RF receive traffic is present.
- A fixed EMANE `subid` combined with `excludesamesubidfromfilterenable=on` is suspicious,
  but changing sub-IDs alone did not restore this mesh. Keep the sub-ID experiment separate
  from pathloss-event and RF-interface diagnostics so A/B results remain attributable.

## Mesh dashboard frontend debugging (lessons learned)

- **Treat static JS as cache-sensitive.** After changing `gui/mesh_dashboard/static/*.js`, bump
  the version query in `gui/mesh_dashboard/static/index.html` script tags (for example
  `mesh_graph.js?v=...`, `traffic_chart.js?v=...`, `operator_tabs.js?v=...`) so the browser
  does not keep executing stale code.
- If the UI still shows old labels/behavior (for example "Resolution" after a rename to
  "Mode"), verify which asset revision is actually loaded in the page before changing logic.
  In browser devtools/automation, inspect `document.querySelectorAll('script[src]')` and
  confirm the expected `?v=` token is present.
- For dashboard layout edits, treat `#graph` and `#traffic-panel` as coupled. A fixed
  `#traffic-panel` height with a mismatched `#graph` bottom offset creates blank gaps or
  overlap. Keep them synchronized.
- Prefer content-sized bottom panels for WAN charts and compute `#graph` bottom spacing from
  the rendered panel height (minus status bar height), then update that spacing on resize.
- After merging stacked WAN charts into a single canvas, remove obsolete fixed-height
  assumptions and validate there is no empty panel space at the bottom.
- When validating GUI fixes, verify both source and runtime behavior:
  1. no syntax/lint errors,
  2. expected script revision loaded,
  3. live DOM/layout metrics (`getBoundingClientRect`) match intent,
  4. interactive labels/context menus show updated wording.
- For the Experiment tab specifically, verify the live browser has loaded the current
  `operator_tabs.js?v=...`, source/destination options come from `/api/mesh_status`, the
  controller status comes from `/api/emane_controller`, and apply/reset behavior is validated
  through the GUI path, not only by manually running `emaneevent-pathloss` in a container.
- After changing Delivery tab metric semantics, update the definitions text box below the table
  in `gui/mesh_dashboard/static/index.html` in the same change and browser-verify the rendered
  labels. Current definitions: `Sent` is rolling-window sends, `Received` is rolling-window
  destination receives, and `Lost` is only messages outside the topic grace period that have not
  been received.

## Debugging Workflow

Use the repository debug tree as the first place to look for runtime evidence:

- `debug/logs/mesh/`: canonical mesh application logs, including `control.log`,
  `platform<ID>.log`, simulator and mesh-control logs, `mesh_bridge.log`, and
  `traffic_monitor.log`.
- `debug/logs/network_monitor/traffic_stats.jsonl`: append-only WAN traffic samples from
  the dashboard monitor. The monitor watches DDS domain `200` only and splits discovery
  and user-data packet counts and bytes.
- `debug/logs/journal/control.jsonl` and `platform<ID>.jsonl`: JSONL samples captured by
  the canonical mesh lifecycle from each node's journal/status subscriber, containing
  `ActRouterControllerJournal` route decisions and `ActRouterStatus` snapshots.
- `debug/logs/router_e2e/<test-name>/`: rendered e2e configs and subprocess logs. Test
  failures expose the exact `log_path` to inspect.
- `debug/pcap/<application>/`: raw packet captures. Use `debug/pcap/router/` for the
  planned in-process Connext network capture and application-specific subdirectories for
  other capture tools.
- `debug/scripts/`: diagnostic tools; keep generated output in the matching `debug/logs/`
  or `debug/pcap/` application directory.

Recommended triage order:

1. Check `debug/logs/mesh/` for process startup, shutdown, DDS discovery, and router
   errors.
2. Check `debug/logs/journal/router_journal.jsonl` for the controller event, decision,
   pre/post `state_revision`, and status-publish action.
3. Check `debug/logs/network_monitor/traffic_stats.jsonl` for WAN discovery versus
   user-data activity and whether dashboard posting succeeded.
4. Run `python3 debug/scripts/dds_type_probe.py --domain 20 --wait 5` when a LAN endpoint or
  inline TypeObject is missing. Use the WAN traffic monitor for domain `200`; do not infer
  WAN discovery health from a probe that is not configured for the WAN discovery protocol.
5. Use the live dashboard only after confirming the bridge log and local HTTP endpoint;
   a blank Dev Tunnel page can be a forwarding problem rather than a WebSocket failure.
6. For an EMANE mesh with no peers, inspect `debug/<node>_debug/traffic_stats.jsonl` and
  `debug/<node>_debug/logs/emane.log` before changing DDS QoS. Confirm nonzero RF RX,
  Domain 200 discovery counters, and the generated `WAN_PEER*`/`WAN_RECEIVE_MULTICAST`
  values. The router's `Failed to get discovered_participant_data` warnings are often a
  downstream symptom of missing or unstable RF discovery traffic.

The `/clear` prompt removes generated logs and captures while preserving `.gitkeep`
directory markers. Stop the associated mesh, test, or capture process before clearing.

## Connext environment (this VM)

- `NDDSHOME=/home/rti/rti_connext_dds-7.7.0`, arch **`x64Linux4gcc7.3.0`**, Connext
  **7.7.0**, `rtiddsgen` 4.7.0, license at `$NDDSHOME/rti_license.dat`.
- CMake pattern: add `${CONNEXTDDS_DIR}/resource/cmake` to `CMAKE_MODULE_PATH`,
  `find_package(RTIConnextDDS "7.7.0" REQUIRED COMPONENTS core)`, generate types with
  `connextdds_rtiddsgen_run(... LANG "C++11" ...)`, link `RTIConnextDDS::cpp2_api`.
  Reference build: `spikes/isc_recovery/relay/cpp/CMakeLists.txt`.
- Modern C++ (C++11) API. Proven entity/QoS/`key_value()` patterns live in
  `spikes/isc_recovery/relay/cpp/isc_relay.cxx`.
- **Generated types use DIRECT public data members, not accessors.** Connext 7.7 follows the
  updated OMG IDL-to-C++11 mapping, so `rtiddsgen` emits struct fields as public members with
  `{}` initializers — write `s.target_node = "x";` / `s.routes.push_back(r);` / `s.routes.at(0)`,
  **not** the old getter/setter pairs (`s.target_node("x")`). Sequences are vector-like
  `omg::types::bounded_sequence<T, N>` (unbounded IDL sequences default to cap `100`). Verified
  against the `router/admin/RouterAdminTypes.idl` codegen.

## Docker Connext setup and test workflow

- Build the supported image from `docker/connext-7.7/`:
  `docker compose -f docker/connext-7.7/compose.yaml build`. The image installs the RTI
  7.7.0 SDK from RTI's Debian repository and `rti.connext==7.7.0` from PyPI; it does not
  require a host Connext SDK.
- The fixed host directory `/home/dgreenberg/rti_connext_dds-7.7.0` is **license-only**.
  It must contain `rti_license.dat` and is mounted at `/shared:ro`; use
  `RTI_LICENSE_FILE=/shared/rti_license.dat`. Do not use this directory as a Docker build
  context or assume it contains headers, `rti_versions.xml`, or the SDK.
- After adding a user to the Docker group, start a new shell. Until then use
  `sg docker -c '...'`; do not use `sudo docker`.
- For a disposable unit-test/build container, mount the checkout writable because CTest
  writes `router/build/Testing/Temporary/LastTest.log`:
  ```bash
  sg docker -c 'docker run --rm \
    -v "$PWD":/workspace \
    -v /home/dgreenberg/rti_connext_dds-7.7.0:/shared:ro \
    -w /workspace \
    -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
    -e RTI_LICENSE_FILE=/shared/rti_license.dat \
    connext:7.7.0 sh -lc "cmake --build router/build -j2 && bash router/run_tests.sh"'
  ```
- For e2e validation, mount the checkout read-only and keep all caches, logs, pytest state,
  and temporary files under `/tmp`. Set `PYTHONPYCACHEPREFIX=/tmp/pycache`, `TMPDIR=/tmp`,
  and run pytest with `-p no:cacheprovider` so `.pytest_cache` is not written to the share.
  Install pytest only inside the disposable container if the image does not already provide it.
- Before and after either test mode, check for `pytest`, `router_main`, and mesh processes and
  for `/dev/shm/RTI*` and `/dev/shm/dds*`. Use host networking for DDS e2e tests, reserve
  domains through `router/test_e2e/conftest.py`, and never run e2e tests against a live mesh.
- Rebuild `router/build` after C++ changes; stale binaries can report behavior from older
  source. The verified clean baseline is `bash router/run_tests.sh` with 4/4 tests passing
  and `python3 -m pytest -q -p no:cacheprovider router/test_e2e` with 27 tests passing.

## Validate Connext specifics — don't guess

A `connext` MCP server is available. Use it instead of relying on memory for Connext APIs,
QoS, and behavior:
- `ask_connext_question` for API/feature/version behavior (cite the answer).
- `validate_xml_code` for QoS XML (schema-checks and fixes).
- `validate_modern_cpp_code` for C++11 Connext API code.

**The MCP is a strong hint, NOT ground truth — the build/a run/introspection is the arbiter.**
It has been wrong repeatedly on this install (Python-binding surface, QoS-field names, XML
schema, discovery/type behavior). Therefore:
- **Before** relying on an MCP answer for anything load-bearing, first sync the doc, then
  check [`docs/connext-ai-issues/connext-ai-issues.md`](../docs/connext-ai-issues/connext-ai-issues.md)
  for a matching known-wrong entry. That path is a git submodule
  (`rtidgreenberg/connext-ai-issues`) **shared across repos** — its content can move forward
  without this repo's pinned commit knowing, so pull before trusting it:
  `git submodule update --init` if it's empty, then `cd docs/connext-ai-issues && git pull`
  to fetch entries other repos may have added.
- **Verify** MCP claims against the build, a runnable spike, `dir()`/`grep` of the actual
  headers/`rti.connextdds` binding, or `$NDDSHOME/doc` — never present an unverified MCP claim
  as fact.
- **When the build/empirical evidence contradicts the MCP, append an entry** to
  `docs/connext-ai-issues/connext-ai-issues.md` (newest at bottom: date, tool, claimed, actual,
  verified). This needs a two-part sync so other repos and this repo's own history both pick
  it up:
  1. `cd docs/connext-ai-issues && git commit -am "..." && git push` (commit/push inside the
     submodule itself).
  2. Back in the parent repo, `git add docs/connext-ai-issues && git commit` (and push) to
     re-pin this repo's submodule pointer to that new commit — otherwise a fresh clone or
     `submodule update` here would reset back to the old entry.
- **After every response that used or asserted an MCP Connext claim, cross-check it against
  `docs/connext-ai-issues/connext-ai-issues.md`** before presenting it.

Instance-state consistency (ISC) findings validated against 7.7.0 (see
`docs/cpp_router/`): native `RECOVER_INSTANCE_STATE_CONSISTENCY` recovers only the
**same-physical-writer reconnect** case (Scenario A). Recovery across a **restarted writer
(new physical GUID, same virtual GUID)** is **not** shipped in 7.7 (Scenario B; CORE-13337
for infrastructure services) — it requires durable writer history + app-level state
republish, and is the F3 feature under design.

## Wire-level verification (tshark/dumpcap)

For discovery/protocol claims the MCP can't settle (message timing, message size,
periodic-vs-event-driven behavior, which builtin entity sent what) — capture the real
traffic instead of trusting a description. Verified working on this VM without sudo:
`dumpcap` already carries `cap_net_raw,cap_net_admin` (`getcap $(which dumpcap)` to
confirm) and this user is in the `wireshark` group, so `dumpcap -i lo -w out.pcap` and
`tshark -r out.pcap ...` both run unprivileged. **Never run `tshark`/`dumpcap` without
`-r <file>` or `-w <file>`/`-c <count>`/`-a duration:N`** — an unbounded live capture on a
real interface (not `lo`) will pick up unrelated host traffic and run forever.

- Filter to DDS-RTPS traffic with `-Y rtps` (tshark's RTPS dissector understands SPDP,
  SEDP, and RTI's SPDP2 builtin discovery messages).
- Useful fields via `-T fields -e <field>`: `frame.time_epoch` (wall-clock, for bucketing
  against Python-side `time.time()` timestamps — not `time.monotonic()`), `frame.len`
  (wire size), `rtps.sm.id` (submessage type), `rtps.sm.wrEntityId` (which builtin writer
  sent it), `rtps.guidPrefix.src`/`.dst` (sender/receiver GUID prefix).
- **`rtps.guidPrefix.src` is a message-header field — one value per packet, not per
  submessage** (unlike `rtps.sm.id`/`rtps.sm.wrEntityId`, which can repeat if a packet
  bundles multiple submessages). Attribute captured traffic to specific participants by
  filtering on GuidPrefix (obtain a running participant's own prefix from
  `str(participant.instance_handle)[:24]` in the Python binding) — this avoids
  double-counting a packet across its submessages, which per-submessage fields would risk.
- Worked example: `spikes/partition_retarget/bandwidth_compare.py` captures on `lo` with
  `dumpcap`, parses with `tshark -T fields`, and attributes bytes to two test participants
  by GuidPrefix to compare steady-state bandwidth and per-event wire cost across discovery
  configurations (see `spikes/partition_retarget/README.md`'s "Wire-level bandwidth"
  section for the pattern and results).

## Spikes

Experimental proofs live in their own folder (e.g. `spikes/isc_recovery/relay/`) with a
`PLAN.md`, sources, QoS, a runner, and a `README.md`. Runners must place working dirs on a
local fs per the rules above.

## Test harnesses

Two test harnesses are available for debugging and verification:

Before claiming the full test suite is green, verify the environment that is actually running
the commands. A valid full router gate needs `cmake`/`ctest`, `pytest`, and an importable
`rti.connextdds` binding in the same shell/container that runs the tests. Do not treat stale
prebuilt binaries as a full-suite substitute: binaries compiled with `/workspace/...` sample
paths will fail config tests when run from `/home/ssm-user/...` unless rebuilt or executed from
the matching mounted path. The Connext Docker image is optional, not assumed; use it only after
confirming the image exists or Docker daemon access is available, then bind the current checkout
as `/workspace`, rebuild inside that environment, and run the tests there.

- **C++ unit tests** (`router/test/`): in-process controller/state-machine tests with fakes
  for all DDS seams. Run via `router/run_tests.sh`. **Do not use
  `ctest --test-dir router/build`** — `--test-dir` needs CMake >= 3.20 and this VM has
  3.16.3, where the flag is ignored, ctest scans the cwd, finds nothing, prints "No tests
  were found!!!" and **exits 0**; `run_tests.sh` cds into the build tree and fails a
  zero-test run instead of passing it. Fast,
  no DDS entities created.

- **Python e2e suite** (`router/test_e2e/`): launches real `router_main` subprocess pairs
  (control-role + platform-role) and drives DDS traffic through them from Python using
  `router/test_e2e/util/dds_probe.py`. Uses `conftest.py` fixtures (`router_pair`,
  `unique_domains`, `admin_types_xml`, etc.) and per-test YAML configs under
  `router/config/e2e_*.yaml` with domain-placeholder isolation. Run via
  `pytest router/test_e2e/ -v` from the repo root. Covers: route forwarding (every route),
  admin command/ack/status loop, QoS alias resolution, auto-QoS, content-filter drop,
  same-node ignore, presence/health/mesh, team partitions, link stats, discovery startup.
  Key utility: `dds_probe.Probe` (UDPv4-only participant), `AdminChannel` (status reader +
  command writer + ack collector), `write_until_seen` (poll-with-timeout).

- **Type probe** (`debug/scripts/dds_type_probe.py`): standalone diagnostic —
  `python3 debug/scripts/dds_type_probe.py --domain 20 [--topic ActTeamAssignment]
  [--wait 5]` — lists every discovered publication/subscription on a domain (topic, type,
  owning participant, name) and whether its inline TypeObject resolved. Surfaces exactly
  the fact `DiscoveryDispatcher.maybe_learn_type()` gates on, so a topic stuck in
  `TOPIC_IDLE` because no publication propagates an inline TypeObject (e.g. WIS writers)
  is immediately visible instead of requiring log-grepping.

- **Live mesh** (`harness_v2/scripts/run_mesh.sh`): launches a full N-platform router mesh
  (control + platform routers + platform sims + platform_mesh_control processes) with optional
  WIS + dashboard (`--with-dashboard`). Useful for manual debugging and the standalone
  `test_team_assignment_e2e.py` script. Logs in `debug/logs/mesh/` and generated lifecycle
  state in the selected workdir. Tear down with
  `run_mesh.sh down`.

## Data model

All DDS types (application payload + router admin/status/presence) are defined in a single
IDL source of truth: `harness_v2/datamodel/ActTypes.idl`. The generated XML
(`harness_v2/datamodel/gen/ActTypes.xml`) is committed alongside and must be regenerated
after any IDL change:
```
$NDDSHOME/bin/rtiddsgen -convertToXml -d harness_v2/datamodel/gen harness_v2/datamodel/ActTypes.idl
```
The router's C++ codegen (`router/CMakeLists.txt`) generates from the same IDL at build
time. All YAML configs, Python scripts, and WIS reference the generated XML.
