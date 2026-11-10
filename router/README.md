# ACT C++ Router

Buildout of the dynamic DDS router described in [`docs/cpp_router/`](../docs/cpp_router/).
Start with [Thesis & Tenets](../docs/cpp_router/thesis-and-tenets.md) (authoritative), then
the [Implementation Plan](../docs/cpp_router/implementation-plan.md).

## Status: Phases 0-5 shipped, Phase 6 next

Phases 0-5 (build skeleton, controller state machine, discovery, one discovered route,
role-aware DynamicData routes with content filtering, DDS-native asymmetric auto QoS) are
shipped and test-verified (`docs/cpp_router/design-decisions.md` D1-D45). `router_main` is
wired to run them for real (D46):

- `router_main` — parses `--config <path> [--name <name>] [--role <role>]
  [--node-name <name>]`, builds the configured DDS participants and routes
  (`RouteConfigParser` -> `TypeResolver` -> `ParticipantRegistry` -> `DynamicRouteFactory`
  -> `RouterController` -> `DiscoveryDispatcher`), and runs until `SIGINT`/`SIGTERM`.
- `admin/RouterAdminTypes.idl` — the admin command/status types, transcribed from
  [command-status.md](../docs/cpp_router/command-status.md); generated at build time.
- `src/core/Log.hpp` — the single structured log stream (logfmt, `source=router`). The
  Connext logger bridge (`source=connext`) is added in a later DDS phase.
- `src/config/RouteConfigParser.{hpp,cxx}` — full route/participant/type YAML parsing
  (yaml-cpp), with role-aware source/destination-side selection.
- `config/control-platform.yaml` — the production-shape system-wide config (D80) matching
  [configuration.md](../docs/cpp_router/configuration.md): every route family
  (control<->platform and, since Phase 10/D87, platform<->platform team) in one file,
  runnable by `router_main` as literally authored. `config/platform-team.yaml` is kept
  only as a Phase-0 identity-reader fixture — its own flat-shape routes:/participants:
  are historical and unexercised by `RouteConfigParser` (the flat route shape is retired,
  D80/D87).
- `config/e2e_control_command.yaml`, `config/e2e_platform_status.yaml` — trimmed,
  `router_main`-runnable fixtures (single type, `qos: ""`) used by the Python e2e suite
  (`test_e2e/`).
- Tests: 8 C++ test binaries under `test/` (run via `run_tests.sh`), plus the Python suite
  under `test_e2e/`. (The build tree may still hold binaries for retired targets —
  `test_route_forward`, `test_dynamic_forward`, `test_runtime_spine`, `test_auto_qos`,
  `test_discovery_smoke` — whose coverage moved to `test_e2e/` under D52. Only targets
  registered with `add_test` belong to the current gate.)

## Build & test

### Opt-in two-topic WAN migration

The default remains `router.wan_contract: legacy`. The experimental
`router.wan_contract: two_topic_union` selects shared **Event** and **Status**
WAN topics with whole-structure adapters; application LAN topics/types remain
unchanged. See the [migration checkpoint](../docs/two-topic-wan-migration-plan.md#38-controller-and-interest-checkpoint-2026-11-09).

Starting from the production configuration, the isolated controller tests:

- Add `wan_contract: two_topic_union` under `router`.
- Point the WAN participant QoS alias at
  `ACT_QOS_LIB::wan_two_topic_participant` (or its `_emane` variant).
- Point `wan_status` at `ACT_QOS_LIB::wan_union_status`.
- Keep `router.presence_participant` on the local WAN participant to enable
  health/presence. Its `health` branch shares the single Status writer/reader
  with application traffic. Omitting it still disables presence.

Startup validates the Status QoS alias whenever application routes or presence
require Status, including Event-only and presence-only configurations.

Schema version 2 envelope keys are `source_id + original_topic + instance_id`.
The router stamps `source_id` from the configured system/node name, not the
internal router process name. `instance_id` is the raw payload identity field,
not a branch/topic-prefixed encoding; session remains non-key metadata.
ContactReport uses its existing `contact_id` as `instance_id`. Its LAN DDS key is
`(source, contact_id)`. On WAN-to-LAN decode, the receiving router overwrites
`source` with the envelope's originating `source_id`, including key-only recovery;
it never substitutes its own node identity or trusts the producer's payload source.
Local producers may leave `source` empty. The existing LAN source bound is 100
bytes; longer WAN origins are explicitly rejected for ContactReport. All other
contact fields are preserved. Other payload key annotations
and all payload fields are unchanged. This replaces schema version 1 and
requires a coordinated participant rebuild/restart, not a mixed-version mesh.
Shared Status uses XML-authored best-effort, volatile, KEEP_LAST(1), a 2-second
deadline and infinite liveliness. The direct deadline listener checks only the
reported instance: a RouterHealth miss queues STALE for the next mesh tick
(at most 0.5 seconds later), then DEAD after 30 seconds without further Status.
Further misses do not restart that countdown. Any valid received Status from
the source restores ALIVE and cancels it, atomically clearing any older queued
loss so a subsequent closure starts a new countdown. There is no general deadline-instance
sweep; a coalesced callback reporting another branch is deliberately ignored.
RouterHealth NO_WRITERS/dispose notifications start the same countdown, because
graceful writer closure cancels native deadline monitoring. They do not mark
the source DEAD immediately or require a finite liveliness lease.

Direct source ages track any valid Status; cached secondhand topology ages track
only the last RouterHealth receipt. Application updates do not freshen old
health summaries.

The owned EMANE launcher supports this configuration directly (legacy remains
the default):

```bash
CONNEXT_SHARED_DIR=/home/dgreenberg/rti_connext_dds-7.7.0 \
  harness_v2/scripts/run_mesh.sh up --emane --platforms 5 \
  --wan-contract two_topic_union --with-dashboard
```

Stop an existing mesh through the owned `down --emane` action and rebuild the
router before changing modes; do not patch generated configs in a running mesh.

The current opt-in boundary requires one local-role WAN participant, the
configured XML type catalog and QoS library, `dynamic_data` WAN routes with exact
catalog topics/production WAN class profiles, and an unpartitioned admin LAN.
Wire-learned original types must equal their catalog definitions. Team membership
updates change envelope audiences and CFTs, not WAN discovery partitions.
Existing enable/disable, team membership and supported WAN route-partition
commands remain available; unsupported LAN partition/control-audience changes
reject explicitly.

Names must be exact (no `*?[]` patterns), fit the existing 255-byte bound, and
contain no apostrophes or NULs. Both the combined source-alias team audience and
aggregate receive interests are bounded to sixteen names. These restrictions
apply to the opt-in audience path, not legacy partition behavior.

WAN match counts describe shared endpoint-class connectivity, not availability
of each remote original-topic producer; LAN matches remain topic-specific.
Health transport, presence transitions, health restart recovery, passive RTT and
protocol counters are verified; see the
[health checkpoint](../docs/two-topic-wan-migration-plan.md#311-health-on-status-checkpoint-2026-11-09).
Application dispose/unregister now follows the same bound routes and shared
output writers as data, using native key translation and lifecycle operations.
Two-topic input readers compose infinite NO_WRITERS/DISPOSED instance retention
from `ACT_QOS_LIB::router_lifecycle_reader`; other reader policies and legacy
profiles are unchanged. Automatic instance purging is deferred. Infinite
retention remains subject to DDS resource limits and instance replacement.
Producer aliases that normalize to the same boat/contact ID share one contact
lifecycle; independent alias ownership is not provided.
See the [lifecycle implementation plan](../docs/two-topic-native-lifecycle-plan.md).

Unseen-instance/restart/load qualification, health congestion isolation/timing
alignment, supported clients and cutover/rollback gates remain open. Do not mix
incompatible WAN contracts or use this as a fleet cutover.

Rebuild and run the isolated controller gate (C++ tests and DDS
adapter/controller/legacy regressions; requires the owned mesh to be stopped):

```bash
bash router/qualification/run_adapter_checks.sh
bash router/qualification/run_checks.sh
```

### Legacy build and full suite

Connext 7.7.0, arch `x64Linux4gcc7.3.0` (see the repo `CLAUDE.md`).

```bash
export NDDSHOME=/home/rti/rti_connext_dds-7.7.0
cd router
cmake -B build -DCONNEXTDDS_ARCH=x64Linux4gcc7.3.0
cmake --build build
./run_tests.sh
```

**Use `run_tests.sh`, not `ctest --test-dir build`.** `--test-dir` requires CMake >= 3.20;
this VM has 3.16.3, which ignores the flag, scans the current directory, prints `No tests
were found!!!` — and **exits 0**. A gate using it reports success having run nothing.
`run_tests.sh` cds into the build tree (works on any ctest) and fails a zero-test run.

`router_main` must be run with **cwd = repo root**, not `router/` — the `types.xml`/
`qos_libraries` paths in the config files are repo-root-relative:

```bash
# from the repo root
./router/build/router_main --config router/config/e2e_control_command.yaml --role control
./router/build/router_main --config router/config/e2e_control_command.yaml --role platform
```

Python end-to-end tests (build first, then from the repo root):

```bash
pytest router/test_e2e -v
```

Building in-tree on the vboxsf share is fine (sources + executables). Per the repo
filesystem rules, any **runtime** artifacts (SQLite/DWH, FIFOs, logs) must go to a local
fs — the identity unit test and the Python e2e suite already write their temp fixtures
under `/tmp`.
