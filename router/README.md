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

An independent [Routing Service processor variant](routing_service/README.md) is
being developed in parallel. Its single-route data plane has a native acceptance
gate; full health/control/telemetry and existing-suite parity are not complete.
It does not replace this executable or modify the retained live deployment.

The default remains `router.wan_contract: legacy`. The experimental
`router.wan_contract: two_topic_union` selects shared **Event** and **Status**
WAN topics with whole-structure adapters; application LAN topics/types remain
unchanged. See the [migration checkpoint](../docs/two-topic-wan-migration-plan.md#38-controller-and-interest-checkpoint-2026-11-09).

Both shared WAN contracts are opt-in:

The additional opt-in `router.wan_contract: three_topic_union` selects
**Event, State and Telemetry**. It uses the same schema-3 envelopes: State
transports the existing ContactReport branch of WanEvent, not a new LAN type.
Use `ACT_QOS_LIB::wan_three_topic_participant` (or `_emane`), map `wan_event`
to `ACT_QOS_LIB::wan_three_topic_event`, `wan_contacts` to
`ACT_QOS_LIB::wan_union_contacts`, and keep the shared `wan_status` profile below
for Telemetry. These internal QoS aliases and `WanEvent`/`WanStatus` type names
are retained for compatibility; they do not determine the physical topic names.
The canonical EMANE launcher accepts `--wan-contract three_topic_union`.

Event retains KEEP_ALL; State uses writer KEEP_LAST(1), reader KEEP_ALL,
and default 62.5 ms reader heartbeat suppression. The two reliable writers
share an XML highest-priority-first controller with a nominal 250,000 B/s
budget and priority-band MultiChannel. Required Events, including RouterConfig,
carry priority 100; contact writes and lifecycle notifications carry 10.
Telemetry remains outside that controller; health-priority tuning is not included.
State has a retained physical pair, including when a node has no contact
receive interest. Filters and endpoint statistics remain physical-topic-specific.
Mixed legacy Event routes may keep their `wan_event` hint: the validated
catalog selects State independently, without splitting logical routes.
This is a wire-topic rename from the earlier Event/Contacts/Status baseline,
requiring coordinated restart of all three-topic participants. It does not
change retention, durability, payload branches, keys or application LAN topics.

The [measured tradeoffs](../docs/wan-topic-tradeoffs-2026-11-12.md) selected this
baseline for correctness and its tighter observed recovery range, not because
it meets the still-open ten-second recovery target.

For the two-topic option:

- Add `wan_contract: two_topic_union` under `router`.
- Point the WAN participant QoS alias at
  `ACT_QOS_LIB::wan_two_topic_participant` (or its `_emane` variant).
- Point `wan_status` at `ACT_QOS_LIB::wan_union_status`.
- Keep `router.presence_participant` on the local WAN participant to enable
  health/presence. Its `health` branch shares the single Status writer/reader
  with application traffic. Omitting it still disables presence.

Startup validates the Status QoS alias whenever application routes or presence
require Status, including Event-only and presence-only configurations.

Receive filters use readable `original_topic = 'PlatformData'` predicates,
combined with the existing control/global/destination/team audiences and source
suppression. The codec keeps original topic and union discriminator consistent
and rejects mismatched received frames. Identical topic sets share an OR of
audience selectors to avoid repeating long topic names within the filter budget.
Health is selected by `original_topic = 'RouterHealth'` on the shared Status
reader in two-topic mode, or Telemetry reader in three-topic mode.
The fixed `SHARED` participant partition remains; legacy partition-named route
fields become audience selectors in two-topic mode, and team changes update CFTs
and source metadata rather than DDS participant/endpoint partition QoS.

WAN schema version 3 replaces separate audience destination/team fields with
`audience.destination_ids`: at most 16 exact IDs, each naming a node or team.
ControlCommand destinations and PlatformStatusMode targets may now name a team;
each member receives the unchanged original payload. A node always retains its
own ID interest. Global/control flags remain additive and TeamAssignment remains
broadcast. The simulator and mesh-control helper use local RouterStatus membership
to recognize team-addressed commands; external consumers must do likewise.
The dashboard still publishes mission commands per platform for its existing
per-platform validation and ACK tracking. This does not redesign command octets.
Version 2 and 3 require a coordinated rebuild/restart, not a rolling mixed-schema
deployment; the codec explicitly rejects version 2 data frames.

`RouterConfig.wan_writer_filters` reports each shared WAN writer's observed
content-filter expression and parameters per matched reader, identified by its
`<node>/<router>` participant name. It is sampled on the controller strand before
each health heartbeat; team changes therefore become visible from the remote
writer's perspective without creating endpoints or changing forwarding/lifecycle
behavior. These are discovery observations, not an acknowledgment that writer-side
evaluation is active. Poll errors and capacity truncation are explicit; departed
readers disappear from the next successful snapshot.

These observations use the `router_config` union branch (6) of Event and its
existing RELIABLE/KEEP_ALL writer/reader. Health stays on Status and never carries
the details. Config snapshots include router/config identity, health session,
independent snapshot sequence, timestamp and state revision. Full snapshots are
sent on first publication, content changes (excluding poll timestamps), and every
30 seconds for late readers/rejoin recovery under the existing VOLATILE durability.
There is no extra WAN topic or endpoint. Failed writes are logged and do not
advance the baseline. Receivers cache early configs until matching-session health,
reject old/duplicate sequences, and treat an empty list as an authoritative clear.
A new health session cannot inherit old details. Normal DDS reliability repairs
loss for matched active readers; it does not guarantee persistence, historical
replay, operator acceptance or delivery after the existing inactivity limits.
Rebuild/redeploy all affected type consumers after regenerating the IDL/XML.
The retained seven-node mesh received the coordinated rebuild/restart on
2026-11-12; see the [deployment checkpoint](../docs/two-topic-wan-migration-plan.md#338-reliable-routerconfig-deployment-2026-11-12).
The LAN mesh aggregate also includes the observer's own last published health and
local-monotonic health ages so a dashboard can inspect its own WAN writers.
Its optional `RouterHealth.wan_writer_filters` contains the last cached reliable
config, preserving the dashboard's LAN shape without repeating it over WAN.
Separate `wan_writer_filters_age_ms` (peer) and
`observer_wan_writer_filters_age_ms` (observer) measure snapshot age; `-1` means
no snapshot. Compact heartbeats and unrelated Status traffic do not refresh it.

The [cache review fixes](../docs/two-topic-wan-migration-plan.md#339-routerconfig-cache-review-corrections-2026-11-12)
protect the current health session's replay watermark during session eviction.
At the unchanged 256-identity limit, the oldest config-only identity absent from
the roster is evicted, with a warning, rather than permanently blocking new peers.
Roster entries remain protected, and each identity still holds at most two sessions.
These corrections were included in the later coordinated three-topic deployments;
see the [current deployment checkpoint](../docs/two-topic-wan-migration-plan.md#342-review-fixes-failed-binds-and-timeout-summary-2026-11-13).

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

### System timeout and static WAN QoS

Set these policy inputs before launching the mesh or `router_main`:

| Environment variable | Default | Meaning |
|---|---:|---|
| `WAN_TIMEOUT_SEC` | 300 | Final two-topic presence timeout since the last received Status; also the WAN participant discovery lease |
| `WAN_HB_PERIOD_SEC` | 3 | Reliable DDS heartbeat period, not the RouterHealth publication period |
| `WAN_HB_RETRIES` | 100, derived | `ceil(WAN_TIMEOUT_SEC / WAN_HB_PERIOD_SEC)`; not an independent policy input |

For example, `WAN_TIMEOUT_SEC=600 WAN_HB_PERIOD_SEC=3` derives 200 retries.
The canonical Compose launcher carries the two inputs to every node; the node
entrypoint, host launcher, Python configuration reader, and direct C++ router
startup resolve the same policy before loading QoS. Inputs must be positive
32-bit integers. A conflicting explicitly supplied retry count fails startup
rather than silently creating a shorter repair window.

The XML remains a static template. Its configuration variables feed the actual
DDS profiles, including `act.presence.dead_timeout_sec` on the shared Status
reader. The two-topic health deadline remains 2 seconds and data-writer
liveliness remains infinite. STALE is an early warning; DEAD is measured from
the last Status, not from the warning. Any valid received Status restores ALIVE.
DEAD peers remain visible for another 60 seconds before roster removal.
Legacy RouterHealth retains its existing finite-liveliness presence behavior.

Retry count times heartbeat period is a nominal inactivity budget, not an exact
DDS wall-clock timer or an unlimited delivery guarantee. Queue limits, retained
history, disconnection/restart behavior, and congestion still require qualification.
Event remains reliable/volatile; this change does not enable transient-local
durability or alter resource budgets.

Changes apply at process startup. Use a coordinated owned-lifecycle restart;
the read-only Config tab neither edits nor applies settings. Its timeout and
retry rows describe configured values, not live verification of every node.
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
