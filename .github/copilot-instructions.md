# Repo context & guardrails for AI assistants

This repo runs on a full Ubuntu filesystem. Runtime diagnostics belong in the repository's
`debug/` folder:

- Logs: `debug/logs/<application>/`
- Packet captures: `debug/pcap/<application>/`
- Diagnostic scripts: `debug/scripts/`

Generated logs and captures are Git-ignored.

## DDS / Connext runtime hygiene

- **Prefer UDPv4-only transport** for single-host test rigs
  (`<participant_qos><transport_builtin><mask>UDPv4</mask></transport_builtin>`). Then a
  `SIGKILL`ed participant cannot leak `/dev/shm` shared-memory segments.
- After kill-based tests, clean up: `pkill -x state_reader; pkill -x state_writer` (use
  `-x` exact-name match — `pkill -f` can match your own shell), and check `/dev/shm` for
  stray `RTI*`/`dds*` segments.
- Loopback UDP works for co-located test processes; isolate concurrent tests by DDS
- **Keep every domain id <= 232.** RTPS maps a domain to a UDP port as `PB + DG*D`
  an arbitrary port — sometimes in the privileged <1024 range, where the bind fails with
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
  solely to validate code that can be exercised in-process.

## Connext environment (this VM)

- `NDDSHOME=/home/rti/rti_connext_dds-7.7.0`, arch **`x64Linux4gcc7.3.0`**, Connext
  **7.7.0**, `rtiddsgen` 4.7.0, license at `$NDDSHOME/rti_license.dat`.
- CMake pattern: add `${CONNEXTDDS_DIR}/resource/cmake` to `CMAKE_MODULE_PATH`,
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
  `RTI_LICENSE_FILE=/shared/rti_license.dat`. Do not use this directory as a Docker build
  context or assume it contains headers, `rti_versions.xml`, or the SDK.
- After adding a user to the Docker group, start a new shell. Until then use
  `sg docker -c '...'`; do not use `sudo docker`.
- For a disposable unit-test/build container, mount the checkout writable because CTest
  ```bash
  sg docker -c 'docker run --rm \
    -v "$PWD":/workspace \
    -v /home/dgreenberg/rti_connext_dds-7.7.0:/shared:ro \
    -w /workspace \
    -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
    -e RTI_LICENSE_FILE=/shared/rti_license.dat \
    connext:7.7.0 sh -lc "cmake --build router/build -j2 && bash router/run_tests.sh"'
  ```
- For e2e validation, keep caches and pytest state under `/tmp`, but write application logs
  and captures under `debug/logs/<application>/` and `debug/pcap/<application>/`. Set
  `PYTHONPYCACHEPREFIX=/tmp/pycache`, `TMPDIR=/tmp`,
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

## Debug Artifact Locations

Use these paths first when investigating a running or recently stopped application:

- Live mesh: `debug/logs/mesh/` (`control.log`, `platform<ID>.log`, simulator,
  mesh-control, dashboard, and traffic-monitor logs).
- Router e2e tests: `debug/logs/router_e2e/<test-name>/` (rendered configs and subprocess
  logs; each test process exposes its exact `log_path` in failures).
- Router journal subscriber: `debug/logs/journal/router_journal.jsonl` when running
  `debug/scripts/router_journal_subscriber.py`; each line includes the DDS topic and JSON
  sample for `ActRouterControllerJournal` or `ActRouterStatus`.
- Router network capture: `debug/pcap/router/` when Connext network capture is enabled.
- Tool- or spike-specific captures: `debug/pcap/<application>/`.

The mesh launcher creates and redirects its application logs automatically. E2E fixtures do
the same for test subprocesses. Standalone scripts need explicit redirection to their
application directory, for example:

```bash
python3 debug/scripts/dds_type_probe.py --domain 20 \
  > debug/logs/dds_type_probe/probe.log 2>&1
```

Create the application directory before redirecting output. Use the `clear-debug-artifacts`
prompt after stopping the associated processes to remove generated logs and captures.

To capture the router's route-change ledger and current status over DDS:

```bash
python3 debug/scripts/router_journal_subscriber.py --domain 20
```

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

- **Domain traffic monitor** (`debug/scripts/domain_traffic_monitor.py`): live RTPS traffic
  diagnostic using `tshark` — `python3 debug/scripts/domain_traffic_monitor.py
  --domains 20,21 --interval 2`. It classifies discovery and user-data packets by DDS
  domain, appends interval samples to `debug/logs/network_monitor/traffic_stats.jsonl`, and
  posts the same traffic statistics to the mesh dashboard bridge. It does not create a DDS
  participant, so it does not add discovery traffic to the capture.

- **Router journal subscriber** (`debug/scripts/router_journal_subscriber.py`): DDS diagnostic
  subscriber for route-change decisions and current router status —
  `python3 debug/scripts/router_journal_subscriber.py --domain 20`. It captures
  `ActRouterControllerJournal` and `ActRouterStatus` samples as JSONL in
  `debug/logs/journal/router_journal.jsonl` for offline troubleshooting.

- **Live mesh** (`harness_v2/scripts/run_mesh.sh`): launches a full N-platform router mesh
  (control + platform routers + platform sims + platform_mesh_control processes) with optional
  WIS + dashboard (`--with-dashboard`). Useful for manual debugging and the standalone
  `test_team_assignment_e2e.py` script. Logs in `debug/logs/mesh/`. Tear down with
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
