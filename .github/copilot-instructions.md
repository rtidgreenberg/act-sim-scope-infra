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

## Spikes

Experimental proofs live in their own folder (e.g. `relay/`, `spikes/isc_recovery/`) with a
`PLAN.md`, sources, QoS, a runner, and a `README.md`. Runners must place working dirs on a
local fs per the rules above.
