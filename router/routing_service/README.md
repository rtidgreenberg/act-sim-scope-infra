# Parallel Routing Service gateway

This is the in-repository implementation of the ACT Routing Service processor.
The existing C++ router remains the default. Nothing is migrated to
`references/legacy_harness` until the replacement passes the existing behavior
and deployment gates.

**Status: working data-plane milestone, not full router parity.** The launcher
requires `--data-plane-only` and refuses to imply that health or monitoring
parity is present.

## Architecture

Each node runs one always-running multi-input/multi-output TopicRoute:

- Static original-type LAN inputs and outputs, selected by node role.
- Exactly one Event reader/writer and one Status reader/writer.
- One `ActGatewayPlugin` processor; Routing Service creates and owns its DDS
  entities. No standalone custom-router forwarding engine runs underneath it.
- The production [codec](../src/core/WanCodec.cxx), [audience rules](../src/core/WanInterests.cxx),
  [configuration parser](../src/config/RouteConfigParser.cxx),
  [IDL](../../harness_v2/datamodel/ActTypes.idl), and
  [QoS XML](../../harness_v2/qos/act_qos_profiles.xml) are reused.

Empty and `default` LAN endpoint QoS aliases resolve to the production
`router_default_reader`/`router_default_writer` profiles. Named aliases still
require an entry in `qos_profiles`; periodic Status inputs retain `lan_status_1s`.

The processor merges source aliases, packs/unpacks whole structures, restores
authoritative contact sources, and forwards native dispose/unregister only for
materialized output instances. Key recovery happens while the input loan is
held. Periodic Status bindings are always enabled, regardless of old
primary/detail/debug route gates. Static readers remain present for initially
disabled Event bindings; those samples are drained without forwarding.

Callbacks consume at most 32 samples per input per pass. A periodic callback
continues draining without depending on another arrival. This bounds individual
input work; it does not establish a command-latency guarantee under overload.
LAN output publications are ignored by their own participant to prevent feedback.
WAN content filters exclude the local origin. Own WAN publications and
subscriptions are ignored on both sides, so local readers are not reliable
writer destinations.

## Agreed simplified scope (2026-11-12)

- Keep sessions, routes and endpoints static. Normal operation does not require
  Routing Service remote administration or compatibility with the old router's
  route-management commands.
- Platforms publish all available telemetry locally by default. Control selects
  the desired nodes, teams and original report types through reader content
  filters, rather than commanding primary/detail/debug producer modes.
- Apply a one-second `TIME_BASED_FILTER.minimum_separation` to periodic-status
  **LAN input readers**, before processor packing and WAN publication. The
  existing external `ACT_QOS_LIB::lan_status_1s` profile expresses this policy.
  It means at most one accepted update per second **per original keyed
  instance**, not one aggregate processor invocation per second or a guaranteed
  periodic/latest-value snapshot.
- Use that same one-second LAN-input policy for all periodic telemetry. Do not
  add WAN-reader time throttling or per-original-topic WAN rate controls; WAN
  CFTs select scope only. The WAN time-filter experiment below is retained as
  evidence, not selected for implementation.
- Do not throttle discrete Event traffic, application commands or their ACKs.
  Health keeps its separately specified cadence and presence behavior.
- Historical queries and Persistence Service are deferred.
- Keep the existing web server/bridge unchanged. The Routing Service variant
  must publish the same externally consumed topics, DDS types, keys and field
  semantics as the custom router, including router status, health/presence,
  mesh summaries, link statistics and diagnostics. Controller journals are
  explicitly deferred; journal-driven views are not part of this parity gate.
  Static-route status must truthfully describe the configured topology; dropping
  old route-management commands does not remove these publication contracts.

All-on periodic forwarding and the one-second LAN-input policy are implemented.
The existing platform simulator already publishes its full set of telemetry
tasks. Live scope changes and the existing server-consumed monitoring
publications are still outstanding. WAN reader time filtering is qualified
separately below but is deliberately not enabled.

## Build

Use the supported Connext 7.7.0 environment, with Routing Service installed:

```bash
cmake -S router -B /tmp/act-rs-build \
  -DBUILD_ROUTING_SERVICE_VARIANT=ON \
  -DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0
cmake --build /tmp/act-rs-build \
  --target act_gateway_processor act_rs_config -j2
```

Outputs are `routing_service/libact_gateway_processor.so` and
`routing_service/act_rs_config` under that build directory. This does not rebuild
or overwrite the live mesh binary. Python launch/configuration requires PyYAML,
already used by the repository.

## Configure and run

Run from the repository root with an offline copy of the production YAML:

- Select `router.wan_contract: two_topic_union`.
- Select `ACT_QOS_LIB::wan_two_topic_participant` for the WAN participant alias.
- Select `ACT_QOS_LIB::wan_union_status` for `wan_status`.
- Set `router.admin_participant` to the local-role LAN participant.
- Enable the desired Event bindings in that copy. Periodic Status bindings are
  enabled automatically; old reporting-mode gates do not suppress them.

For example:

```bash
python3 router/routing_service/run.py \
  --config /tmp/platform.yaml --role platform --node-name Boat30 --name rs \
  --build-dir /tmp/act-rs-build/routing_service \
  --team TEAM_A --data-plane-only
```

`--team` supplies initial audience membership without changing discovery
partitions; repeat it for multiple exact IDs. Live membership changes are not
implemented yet. `--render /tmp/gateway.xml` writes the complete configuration
without starting DDS. All fixed QoS is copied from external production XML,
including input instance retention. Three numeric DDS sentinel spellings are
normalized to schema-equivalent symbolic values for Routing Service validation;
their policy values are not retuned.

### Asynchronous priority configuration

The WAN writers use opt-in `rs_wan_event` and `rs_wan_status` XML profiles.
Their two `DDSPRIFLTR` channels share the `act_rs` highest-priority-first flow
controller on the existing WAN participant. The launcher composes only the
controller properties into that participant; discovery/transport settings,
including EMANE settings when selected, remain inherited from its profile.

| Class | Sample priority | Default original topics |
| --- | --- | --- |
| Event / High | 400 | ControlCommand, PlatformCommandAck, ActTeamAssignment, ActPlatformStatusMode |
| Status / High | 300 | RouterHealth |
| Event / Normal | 200 | ContactReport |
| Status / Normal | 100 | All other Status cases |

Data, dispose and unregister all carry the configured priority. Both channels
accept priority zero in their Normal band, but the processor stamps the
explicit values above. FIFO is preserved within a band. Priorities are static
for a service run; live band migration with queued lifecycle samples is not
implemented.

Optional root-level YAML settings override the defaults:

```yaml
routing_service:
  async_bandwidth_bps: 2000000
  topic_priorities:
    ContactReport: normal
    RouterHealth: high
```

Only exact production catalog topic names and `high`/`normal` are accepted.
Invalid settings fail before service startup. The bandwidth must be a positive
32-bit integer, at least 2,000,000 and divisible by 800: the external XML uses
one token per 10 ms and at least the spike's 2,500 bytes per token. Lower-rate
fragment/burst sizing is not qualified by this implementation. The default is
a nominal shared **2 Mbps DDS-data pacing budget**, not a radio-wide cap:
discovery and other traffic also consume bandwidth.

Treat this as a system-level **DDS flow-controller budget**, distinct from
the radio/link capacity setting. The Config screen must expose the configured
and effective budget, units and restart requirement, backed by the same
validated setting rather than a separate UI-only value. A future environment
override should feed that same resolver; precedence and validation must be
explicit. Config-screen integration and an environment override are not yet
implemented. Current changes take effect on service restart, not by retuning
the running mesh.

No third contact topic is added. ContactReport remains reliable `KEEP_ALL` on
Event; Status remains best effort `KEEP_LAST(1)`. These additions do not change
legacy/custom-router profile selection. Strict priority does not prevent
starvation under unlimited higher-class load.

**RouterHealth is assigned and tested as Status/High, but this data-plane
milestone does not yet generate health or implement presence.** The native
four-class scheduler test injects real health-branch samples; it is not a
replacement for health/presence integration. Processor execution priority,
journals and existing status/diagnostic publication parity remain separate.

The launcher resolves the same WAN timeout/heartbeat/retry inputs as the current
router, uses a disposable configuration directory, forwards termination to its
owned service process group, waits for shutdown, and removes transient state.
The first SIGINT or SIGTERM starts a ten-second shutdown grace period; repeated
signals do not extend it. If the service remains running, the launcher reports
forced termination on stderr, sends SIGKILL to its owned process group, and
reaps the service before removing transient state.
The current native launcher expects one installed Routing Service architecture.
Node-local service grouping must not be changed into fleet-wide grouping.

Never point development tests at the retained live mesh. Use private network
namespaces, UDPv4, and the existing safe-domain allocator.

## Initial forwarding milestone (historical)

On Connext / Routing Service 7.7.0:

- Shared library builds and loads into actual Routing Service processes.
- Six native tests pass, without skips: all fourteen production application
  payload cases, whole-payload equality, exact shared WAN endpoint counts across
  three nodes, Event and Status dispose/unregister, addressed-recipient exclusion,
  and malformed-schema rejection followed by valid-data recovery.
- Five renderer unit tests pass.
- The existing nine C++ test targets pass in the same fresh isolated build.
  These shared-code regressions are not a substitute for router e2e parity.

Commands inside an owned private test container:

```bash
PYTHONPATH=router/routing_service python3 -m unittest test_config test_launcher
ACT_RS_BUILD_DIR=/tmp/act-rs-build/routing_service \
  python3 -m pytest -q -p no:cacheprovider router/test_e2e/test_routing_service.py
ROUTER_BUILD_DIR=/tmp/act-rs-build bash router/run_tests.sh
```

The native tests use the existing production case catalog, probe utilities,
domain allocator and configuration renderer, with initial common-team membership.
The current tests deliberately leave periodic route gates disabled and verify
that all periodic cases still forward.
Without `ACT_RS_BUILD_DIR`, these optional native tests skip explicitly.

## Priority implementation gate (2026-11-12)

Fresh isolated Connext / Routing Service 7.7.0 build:

- **18 native processor/configuration tests passed**, no skips, 80.87 seconds.
  Includes all fourteen application payloads, exact two-topic WAN endpoint
  counts, normal/high Event lifecycle, Status lifecycle, malformed-frame
  recovery, priority defaults/overrides/rejections and actual 1 Hz LAN input.
- A known unsent backlog on the real XML profiles verifies
  Event/High > Status/High > Event/Normal > Status/Normal and FIFO within each
  class, using two writers sharing one on-demand controller.
- In actual three-node Routing Service runs, a High ACK arrived at position
  **21** in a 200-contact backlog; the same ACK configured Normal arrived at
  position **200**. Both runs received all 200 contact sequences in order.
  These are scheduling observations, not a radio/outage latency guarantee.
- **7 renderer tests, 3 native time-filter checks and 9 freshly built existing
  C++ targets passed.**

Routing Service's schema required explicit empty `multicast_settings` in each
channel, although native QosProvider accepted omitted defaults. Fixed that in
the XML; channel priority/retention semantics are unchanged. Initial failed
runs also exposed a missing writable log overlay and test-driver assumptions
about reader discovery and one sample per token. Final tests wait for discovery
and use native reception timestamps to check cross-topic ordering.

Rebuilt plugin/config binaries are retained under `router/build/routing_service`.
Compact JUnit and build/test summaries are under `debug/test_reports`.
The live EMANE mesh, its deployed custom-router binary and the destination
submodule were not changed. This is not full-suite, live-mission or complete
monitoring/health parity.

## WAN time-filter qualification (2026-11-12)

Three native Connext 7.7.0 checks passed in an isolated, UDPv4-only container
(12.79 seconds), using the committed schema-version-3 `WanStatus` type and
production `wan_union_status` endpoints. Five existing renderer tests also pass.
The [test profiles](fixtures/wan_time_filter.xml) only add reader minimum
separations; they do not change production profiles or the running mesh.

| Check | Measured result |
| --- | --- |
| One shared reader, one-second separation | Four independent keys each received 4 samples from 161 writes per key over 3.3 seconds. |
| Two CFT readers selecting different `original_topic` values | Navigation received 5 samples at one-second separation; mission received 3 at two-second separation over 4.3 seconds. |
| Unfiltered control | All four keys received all 64 writes per key over 1.3 seconds. |

Accepted-sample timestamp gaps are checked against the configured periods, not
just total counts. The first check varies each key component independently:
`source_id`, `original_topic` and `instance_id`. Equal source/instance IDs with
different original topics therefore do not throttle each other.

**Conclusion:** a shared WAN reader can time-filter each full DDS instance
independently, because `original_topic` is already part of the key. However, its
`minimum_separation` is one reader-wide value: neither SQL CFT clauses nor XML
`topic_filter` (which matches the actual DDS topic name, `Status`) assign
different periods to different field values within that reader.

Different per-original-topic periods work with **separate CFT readers on the
same Status topic**, each loading its own XML reader profile. This needs no new
WAN topic name, but it does break the current exactly-one-Status-reader-per-node
design. Staying with that design means a uniform WAN-reader period, different
periods on the separate original-type LAN inputs, or additional processor
scheduling. No such architecture change is made by this qualification.

For these keyed samples the writer still pushed **every write**: 644 pushes
despite only 16 accepted samples in the one-second test, and 422 pushes in the
split-reader test. WAN-reader time filtering limits processing/delivery, **not
WAN transmission**. Keep the agreed pre-WAN LAN-input throttle for bandwidth
reduction. Applying a period to the shared reader also applies it to health
instances; keep the health/deadline implications explicit. The tested one- and
two-second separations fit the production two-second reader deadline.

Reproduce without connecting to the live mesh:

```bash
docker run --rm --network none --hostname localhost \
  -v "$PWD:/workspace:ro" \
  -v /home/dgreenberg/rti_connext_dds-7.7.0:/shared:ro \
  -w /workspace -e RTI_LICENSE_FILE=/shared/rti_license.dat \
  -e PYTHONPYCACHEPREFIX=/tmp/pycache -e ACT_RS_TBF_ISOLATED=1 \
  connext:7.7.0 python3 router/routing_service/test_wan_time_filter.py
```

The [native checks](test_wan_time_filter.py) require the explicit isolation flag.
The initial test driver drained only once per publication interval; its
unfiltered control exposed overwrites in the production `KEEP_LAST(1)` cache.
The corrected driver drains every millisecond while publishing approximately
50 Hz, leaving the endpoint QoS unchanged.

Reference: [Connext 7.7 TIME_BASED_FILTER](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/TIME_BASED_FILTER_QosPolicy.htm).

## Native route monitoring versus application status

Routing Service 7.7 provides configuration, state-change and periodic monitoring
on `rti/service/monitoring/config`, `rti/service/monitoring/event` and
`rti/service/monitoring/periodic`. Its installed `ServiceCommon.idl` defines
`EntityEvent.state`; `RoutingServiceMonitoring.idl` supplies Route, Input and
Output resource records. Thus basic configured-route/port state and throughput
do not require rebuilding the custom router's state machine or journal.

These are not the application's `ActRouterStatus` topic or `RouterStatus` type.
Keeping existing subscribers unchanged still requires a compatibility publisher
for that contract, deriving fields from native monitoring, endpoint information,
static configuration and processor counters as appropriate. One native
multi-port TopicRoute does not automatically describe every logical original-topic
mapping inside the processor, nor prove end-to-end delivery. Native monitoring
can be enabled independently of remote administration.

This distinction was checked against the installed 7.7.0 IDL and
[monitoring documentation](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/services/routing_service/monitoring.html);
native monitoring integration is not yet runtime-qualified in this variant.
Do not implement controller journal machinery in the current scope.

### Per-original-topic status requirement

Each logical original-topic entry in `ActRouterStatus` must identify its WAN
family (`Event` or `Status`) and its configured priority, alongside processor
forwarding/lifecycle counts and errors. The family identifies the shared WAN
endpoint, not a second physical Routing Service TopicRoute. Matching and
protocol counters remain shared-endpoint facts, not branch-specific evidence.

The selected priority model has two levels, **High** and **Normal**, within
each family: Event/High, Event/Normal, Status/High and Status/Normal. Each
original topic is assigned a level; Event is not implicitly High and Status is
not implicitly Normal. These are priority classes, not four WAN topic names
or a decision to create additional endpoints.

The selected asynchronous transmission ordering is Event/High, Status/High,
Event/Normal, then Status/Normal. Put RouterHealth in Status/High. Use
asynchronous writers sharing a highest-priority-first flow controller, with
High/Normal MultiChannel separation within each family. Fixed policies belong
in external XML; original-topic assignments supply the sample priorities.
Processor execution scheduling is a separate, deferred layer. This ordering
improves health's preference over normal traffic but cannot guarantee health
progress under continuously saturated Event/High traffic. The implementation
and its default assignments are described above; fleet capacity, outage recovery
and health-generation integration remain unqualified.

A third WAN topic for reliable `KEEP_LAST(1)` contact state is deferred:
the selected implementation keeps ContactReport on Event/Normal with
`KEEP_ALL`. A future separate contact topic would separate latest-contact
retention from Event's reliable `KEEP_ALL` retention. History depth is per
complete key, not one contact sample for the whole writer. Superseded updates
may be replaced without delivering every observation; a writer's dispose also
counts toward history depth. This may reduce disposal delay from same-contact
update backlogs, but does not bound delays across many contacts, transmission
queues or outages. Qualify write/dispose/revive and remote final-state recovery
before claiming a disposal-latency improvement. This is not persistence.

The current `RouterRouteTopicStatus` type has neither an explicit WAN-family
field nor a priority field. Plan an additive, explicitly typed extension rather
than encoding these values in names or diagnostic strings. Publish priority
assignments from the validated model described above; do not present configured
priority as proof of latency.
Existing topic names and fields remain the compatibility baseline. Showing the
new fields in the dashboard requires checking bridge serialization and UI
support; unchanged existing views do not imply automatic new-field rendering.
This publication/schema work is not implemented yet.

## Remaining parity gates

The migration is **not complete** until retained-scope behavior tests can run
against this backend. Deliberately retired management/mode behavior must be
identified explicitly rather than reported as unchanged parity:

- Broader unchanged application-command/ACK and live-mission acceptance.
  All-on periodic forwarding and one-second LAN input now have targeted gates.
- Config-screen integration of the system-level DDS flow-controller budget;
  an environment override is a later configuration enhancement.
- Live membership changes, outgoing metadata revisions, fixed-expression filter
  parameter updates, rejection/rollback, and remote writer-side suppression.
- Health on the same Status endpoints, presence transitions, restart/session
  handling, passive RTT, LAN mesh summaries and independent publication cadence.
- Protocol statistics, writer-observed filters, diagnostics and existing
  server-consumed status publications, with unchanged topic/type/key
  contracts. Validate against the existing web server/bridge without backend
  changes; do not shift compatibility work into the server. Controller journals
  and journal-driven views are deferred.
- Exact remote original-type validation, wrong-type containment, multi-producer
  lifecycle/alias stress, unknown keys, and queued final-state behavior.
- Backend selection for the existing e2e suite; legacy/dynamic-route test contracts
  must be accounted for explicitly, not silently skipped as “equivalent.”
- Existing qualification, recovery/load budgets and unchanged live mission tests.

No full Python-suite, live-mission, performance, or deployability parity is
claimed. The existing recovery/prioritization limitations remain limitations;
changing the routing framework does not qualify them away.
