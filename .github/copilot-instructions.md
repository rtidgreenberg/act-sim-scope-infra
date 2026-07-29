# Repo context & guardrails for AI assistants

This repo is developed inside a VM. The primary working directory
(`/media/sf_VM_SHARED/ACT/act-sim-scope-infra`) is a **VirtualBox shared folder
(`vboxsf`)** mounted from the host. It does **not** provide full POSIX filesystem
semantics. Getting this wrong has corrupted the VM filesystem before.

## Filesystem safety (READ FIRST before running anything)

The shared folder (`/media/sf_VM_SHARED/...`, type `vboxsf`) is fine for **source code,
config, and executables (read + exec)**. It is **unsafe for runtime files**:

- **No named pipes** — `mkfifo` fails with `Operation not permitted`.
- **Unreliable `mmap` + file locking** — **SQLite** databases (e.g. Connext **durable
  writer history**: `*.db`, `*.db-shm`, `*.db-wal`) can corrupt or hang. This is the most
  likely cause of past VM filesystem damage. Never point DWH / any SQLite file at the share.
- Files held open by a process across a `SIGKILL` can leave the share in a bad state.

**Rule: write ALL runtime artifacts to a local filesystem, never the share.**
- Local ext4 is at `/` and `/tmp`; `/dev/shm` is tmpfs. Use `/tmp/...` (or `mktemp -d`)
  for FIFOs, SQLite/DWH files, logs, and per-test working dirs.
- Keep the build tree's *sources* on the share, but `cd` runtime processes into a local
  working dir so relative output files (like `isc_dwh.db`) land locally.
- A quick guard: `mkfifo "$DIR/.probe"` and abort if it fails — that proves `$DIR` is local.

## DDS / Connext runtime hygiene

- **Prefer UDPv4-only transport** for single-host test rigs
  (`<participant_qos><transport_builtin><mask>UDPv4</mask></transport_builtin>`). Then a
  `SIGKILL`ed participant cannot leak `/dev/shm` shared-memory segments.
- After kill-based tests, clean up: `pkill -x state_reader; pkill -x state_writer` (use
  `-x` exact-name match — `pkill -f` can match your own shell), and check `/dev/shm` for
  stray `RTI*`/`dds*` segments.
- Loopback UDP works for co-located test processes; isolate concurrent tests by DDS
  **domain id**.
- **Domain IDs above ~5900–6000 can hit the default port-mapping ceiling** on this install
  and fail with `RTIOsapiSocket_bindWithIP: Permission denied` (the computed port lands in
  the privileged <1024 range). Keep ad hoc spike/test domain ids in the low thousands or
  below (existing spikes use domains from ~60 up to ~5900) rather than picking arbitrary
  large numbers.

## Connext environment (this VM)

- `NDDSHOME=/home/rti/rti_connext_dds-7.7.0`, arch **`x64Linux4gcc7.3.0`**, Connext
  **7.7.0**, `rtiddsgen` 4.7.0, license at `$NDDSHOME/rti_license.dat`.
- CMake pattern: add `${CONNEXTDDS_DIR}/resource/cmake` to `CMAKE_MODULE_PATH`,
  `find_package(RTIConnextDDS "7.7.0" REQUIRED COMPONENTS core)`, generate types with
  `connextdds_rtiddsgen_run(... LANG "C++11" ...)`, link `RTIConnextDDS::cpp2_api`.
  Reference build: `relay/cpp/CMakeLists.txt`.
- Modern C++ (C++11) API. Proven entity/QoS/`key_value()` patterns live in
  `relay/cpp/isc_relay.cxx`.
- **Generated types use DIRECT public data members, not accessors.** Connext 7.7 follows the
  updated OMG IDL-to-C++11 mapping, so `rtiddsgen` emits struct fields as public members with
  `{}` initializers — write `s.target_node = "x";` / `s.routes.push_back(r);` / `s.routes.at(0)`,
  **not** the old getter/setter pairs (`s.target_node("x")`). Sequences are vector-like
  `omg::types::bounded_sequence<T, N>` (unbounded IDL sequences default to cap `100`). Verified
  against the `router/admin/RouterAdminTypes.idl` codegen.

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

Experimental proofs live in their own folder (e.g. `relay/`, `spikes/isc_recovery/`) with a
`PLAN.md`, sources, QoS, a runner, and a `README.md`. Runners must place working dirs on a
local fs per the rules above.

## Test harnesses

Two test harnesses are available for debugging and verification:

- **C++ unit tests** (`router/test/`): in-process controller/state-machine tests with fakes
  for all DDS seams. Run via `ctest --test-dir router/build --output-on-failure`. Fast,
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

- **Live mesh** (`harness_v2/scripts/run_mesh.sh`): launches a full N-platform router mesh
  (control + platform routers + platform sims + platform_control processes) with optional
  WIS + dashboard (`--with-dashboard`). Useful for manual debugging and the standalone
  `test_team_assignment_e2e.py` script. Logs in `/tmp/act_mesh_run/`. Tear down with
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
