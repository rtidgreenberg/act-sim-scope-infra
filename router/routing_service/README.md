# Parallel Routing Service gateway

This is the in-repository implementation of the ACT Routing Service processor.
The custom C++ router remains available; the retained mesh below explicitly
selects Routing Service. Nothing is migrated to
`references/legacy_harness` until the replacement passes the existing behavior
and deployment gates.

**Status: the XML-only, fixed-ACT processor, full native monitoring and platform
mission receive policy are implemented and mesh-qualified for the reference workload.** Full startup is the
launcher default. `--data-plane-only` explicitly disables ACT monitoring.
Neither mode restores the retired route-management/reporting-mode controls.

The current [architecture and development plan](../../docs/routing-service-processor-architecture-and-plan.md)
consolidates the latest decisions. Platform-local mission-state CFTs start in
Init and reset to Init after a whole-platform restart. ActiveMission additionally
receives fleet-global peer PlatformStatus navigation positions and same-team
peer PlatformMissionStatus/ContactReport; Init does not select those reports.

For a minimal C2/platform connection, see the
[step-by-step setup guide](../../docs/routing-service-c2-platform-setup.md).
It separates the current executable three-topic recipe from the proposed
scan/import, GUI mapping and generated-configuration workflow.

**Release scope:** v1 explicitly defines application types, full-type unions,
QoS and bindings, shipping them with environment files, start/stop scripts and
a guide. Discovery/capture, automatic bundle generation and the setup/test app
are deferred to later work over the same contract, not v1 prerequisites.
Runtime consumes prepared native XML without YAML or the development helper.
Bindings are explicit. Existing ACT deployments retain their fixed catalog and
application policy. Prepared non-ACT full-type branches are also supported with
`act.receive_policy=static` and `act.monitoring=false`; this bounded data-plane
path does not yet include generic monitoring. Newly prepared bundles and the
2026-11-17 retained deployment use independently activated native LAN routes
with native auto-QoS.
See the [release split](../../docs/routing-service-c2-platform-setup.md#agreed-release-split).
The selected mapping implementation extends this processor, not a separate
Transformation plugin; see the
[XML mapping research and phased plan](../../docs/routing-service-xml-mapping-plan.md).
Each branch carries the complete original type. Selected-field/subtree mapping
is deferred; no general field-copy engine is required for v1.
XML bindings select one logical channel (`event_high`, `event_normal`,
`state_high`, `state_normal`, `periodic_high` or `periodic_normal`), deriving
family and priority together. The three physical topics and prototype YAML
development settings remain supported.

**2026-11-24 source-only QoS naming:** Periodic profiles are now `wan_periodic`,
`wan_union_periodic` and `rs_wan_periodic`; the configuration alias is
`wan_periodic`. Update older source configurations, rebuild development helpers
and regenerate deployment bundles before using these names. Legacy/two-topic `Status` wire topics and
`WanStatus` payload types are unchanged. Best-effort Periodic profiles no longer
carry `disable_preemptive_nack`; reliable Event/State suppression remains.
Retained captures/bundles use their original names and are not rewritten.
No persistent binary or live mesh is changed by this source rename.
Fresh qualification passes 10 C++ targets, 57 targeted Python checks and five
real-DDS forwarding/filtering checks. One unchanged browser check skips for
missing Playwright. Sixteen resolved WAN profile comparisons confirm unchanged
writer QoS and reliable-reader QoS; only the redundant Periodic reader property
is removed.

### Production QoS boundary

[act_qos_profiles.xml](../../harness_v2/qos/act_qos_profiles.xml) contains runtime
profiles only: shared participant/endpoint bases, LAN administration/monitoring,
legacy and two-topic support, the current three-topic policies, and the six-topic
IPv6 policies. The `rs_priority_participant` controller and `rs_wan_*` /
`rs_three_topic_*` profiles are runtime dependencies, not test profiles; the
six-topic writers also inherit the three-topic bases.

Test-only profiles remain in [fixtures](fixtures/), and experimental profiles
remain in their own spikes, including [LBED QoS](../../spikes/routing_service_lbed/qos.xml).
Production preparation does not load those files unless they are explicitly
added to its QoS library list. The production XML comment/section cleanup on
2026-11-24 changes no profile names, inheritance, configuration defaults or
QoS values. It does not deploy or retune the router.
Native review resolves all 47 production profiles and confirms 235 unchanged
participant/reader/writer/publisher/subscriber QoS results. All 29 WAN-policy
and renderer checks pass, with no skips.

## Runtime correctness safeguards

The isolated [LBED spike](../../spikes/routing_service_lbed/README.md) verifies
native Routing Service forwarding of fixed binary envelopes on three WAN topics
with either classic SPDP + LBED or SPDP2 + LBED, with zero SEDP exchanges.
Its dynamic-CFT follow-up verifies receiver selection after runtime parameter
changes, but **not writer-side suppression with LBED**: all offered samples
cross the WAN, unlike the SEDP control. It is not a drop-in replacement for our
WAN-saving audience filters. ACT processor integration, production endpoint
catalogs and lifecycle/loss qualification remain separate. No mesh configuration
or deployed discovery mode is changed.

The isolated [binary-envelope compression spike](../../spikes/binary_wan_compression/README.md)
verifies native LZ4/zlib forwarding, automatic decompression and sender-side
metadata filtering, including a compressed Event runtime-interest update.
Structured binary fixtures shrink substantially; random/already-compressed
bytes do not. Bzip2 emits native errors and is rejected. This is not ACT
binary-codec integration, a radio-performance guarantee or a deployed QoS change.

The [open WAN investigations](../../docs/open-wan-investigations.md) track
compression with the current asynchronous/shared-controller configuration,
runtime RTPX suppression, conditional WAN ServiceRequest-channel removal and
automatic envelope-key derivation from discovered topic keys.

The source-only [startup optimization](../../docs/startup-optimization.md)
retains application-reader preemptive-request suppression, adds 0–1 second
SEDP/SPDP2 configuration response windows, and spaces owned container gateway
launches across one second. It is isolated-qualified, not deployed; regenerate
native XML through the owned lifecycle before adoption. Discovery requests are
not claimed to be suppressed.

Prepared XML must give the LAN participant the DDS identity
`act.node/act.router/act.lan_participant` in
`domain_participant_qos/participant_name/name`. The renderer supplies it.
Native startup checks every application LAN input/output's actual participant
identity and domain against `act.lan_participant` / `act.lan_domain`, in addition
to its type. Launcher preflight also rejects mismatched LAN participant references
and domains. The 2026-11-17 deployment includes these checks and regenerated XML.
When the combined LAN identity exceeds DDS's 255-byte name limit, it becomes
`act.lan.sha256/` plus the SHA-256 digest of the three UTF-8 components, each
prefixed by its decimal byte length and `:`. Renderer, preflight and native checks
use the same rule; supported node/router names are not shortened or rejected.

Rejected commands, receive-policy failures (including rollback failures), and
route errors are bounded to the DDS diagnostic fields' 255-byte limit. Full
errors remain in the structured router log; shortened diagnostics retain an
explicit `ROLLBACK FAILED` indication. Invalid Periodic/Status lifecycle samples
never count as valid traffic: health loss starts the configured STALE countdown,
nonhealth lifecycle does not revive peers, and valid Status traffic recovers them.

With monitoring enabled, platform mission-policy startup and successfully
committed profile/team changes append `receive_policy` records to the existing
`NODE_DEBUG_DIR/events.jsonl`. Run identity uses `ACT_RUN_ID`, falling back to
`NODE_DEBUG_DIR/run_id`. Records contain `run_id`, local `node` / `source_node`,
`topic: "PlatformStatus"`, a process-local `sequence`, equal positive
`sent_at_ns` / `recorded_at_ns`, `receive_profile`, and the committed nonidentity
`teams` list. Startup/restart emits Init before subsequent transitions; unchanged
profile/team pairs, failed changes, static policies and control nodes emit none.
An append failure is explicitly logged. This is committed receive-policy history,
not a claim that already queued deliveries were removed atomically.

Native loss accounting distinguishes the family policies. BEST_EFFORT
`lost_by_writer` gaps and three-topic State `lost_by_writer` replacement are
rate-limited warnings when no new rejection accompanies them. State intentionally
uses writer KEEP_LAST(1); this is not required Event history. Rejections, other
loss reasons and reliable Event history loss remain explicit errors.

WAN reader profiles now set
`dds.data_reader.protocol.disable_preemptive_nack=true` in external XML.
Event/State startup preemptive ACKNACKs are suppressed; normal heartbeat
responses, positive acknowledgments and loss repair remain enabled. The
BEST_EFFORT Status/Periodic profiles inherit the same property without changing
their delivery policy. This source configuration change requires coordinated
mesh regeneration/restart; it has not been applied to the retained live mesh.
See the [packet-capture spike](../../spikes/preemptive_acknack/README.md).

## Retained deployment (2026-11-17)

The owned seven-node mesh uses the independent native hub/LAN architecture
described below. Run `container-20261117T181119-2947503`, Compose project
`act-emane-container-baseline`, Control_20 plus Platform_30–35, dashboard 8080
and workdir `debug/mesh_runs/emane` are the current deployment identities.
Final gates, rollback rehearsal and remaining limits are recorded in the
[completion acceptance record](../../docs/router-processor-completion-plan.md).
The final build passed 10 C++ targets, 74 native processor cases and all five
live mission workflows (380.53 s, zero skips). All seven routers, including C2,
publish ROUTER_OK; expected loss warnings remain visible without ERROR lines.
Compact local evidence is under `debug/test_reports/processor_release_20261117/`.
The 2026-11-16 results below are historical, not the current build's test counts.

With ACT monitoring enabled, `ActRouterDiagnostics` is an internal producer,
not an application LAN input waiting for its own monitor to match. The monitor
keeps publishing the original `RouterDiagnostics` LAN sample and also passes it
through the existing codec/shared Periodic writer. It preserves configured
enable flags, audiences and priority without an extra endpoint or queue.
Its truthful input match count is zero and its readiness reason identifies the
internal producer. Default platform diagnostics target C2; C2 selects them and
other platforms do not. `--data-plane-only` retains the ordinary application
binding rather than this internal path.

**Qualified source follow-up, not yet deployed:** internal diagnostics failures
remain explicitly logged and counted, but no longer abort heartbeat/link-stat
scheduling. Native persistent-failure regressions pass in both WAN contracts;
see the [follow-up record](../../docs/router-processor-completion-plan.md#final-review-follow-up--diagnostics-failure-isolation-2026-11-17).
The retained deployment above still uses the pre-fix binary.

## Historical mesh evidence (2026-11-16)

The verified mesh is **retained running** on Control_20 and Platform_30–35,
dashboard port **8080**, with canonical workdir `debug/mesh_runs/emane`.
Its backend is `routing_service`, WAN contract `three_topic_union`, with
**always-on reports** and prepared XML-only runtime configuration.
Current compact evidence is retained under `debug/test_reports/`:
`mission_cft_runtime.json`, `mission_cft_native_policy.xml`,
`mission_cft_native_static.xml`, `mission_cft_live_missions.xml` and browser
screenshots. These are local generated evidence, not committed source.

Final recorded gates:

- **9/9 C++ regression targets passed.**
- **70 native processor/configuration tests plus 55 subtests passed**:
  34 policy/configuration/mesh tests and 36 static compatibility tests.
- **165 offline tests plus 60 subtests passed.**
- **119 legacy/routes/configuration tests plus 5 subtests passed.**
- **8 legacy monitoring tests passed.**
- **5/5 live browser workflows passed**, zero skips, 375.89 seconds.
- An owned one-platform route → cancel → RTB → COMPLETE check passed before
  the six-platform gate. All seven final router logs were verified free of the
  processor callback failure reproduced and repaired by the model-swap regression.

Post-test state: all six platform peers `Init` / `CANCELLED`,
`PRESENCE_ALIVE` / `ROUTER_OK`, teams unassigned,
clocks at 1x, zero speed, targets hidden and impairments cleared. These results
qualify the fixed-ACT reference deployment, not a generic registry, discovery/
setup generation, automatic QoS selection or agreed fleet/load/resource limits;
those remain open. Each platform peer's health contains three WAN writer
observations with six native reader filters each. Native coverage includes
whole-platform restart, all active/terminal phases, fleet-global navigation
across teams/unassigned, same-team mission/contact selection, and 24 model-swap
transitions with continued forwarding/heartbeats and clean callback logs.
Queued-sample draining and sustained rapid-transition/load stress remain
unqualified; these results do not establish atomic queue removal or fleet-wide
filter barriers. Rollback failure coverage uses injected faults through the
production transaction path, not induced native DDS failures.

Owned lifecycle reference—the mesh is already running, so do not launch a
second copy:

```bash
CONNEXT_SHARED_DIR=/home/dgreenberg/rti_connext_dds-7.7.0 \
  harness_v2/scripts/run_mesh.sh up --emane --platforms 6 \
  --router-backend routing_service --wan-contract three_topic_union \
  --with-dashboard --workdir "$PWD/debug/mesh_runs/emane"
```

**Run the owned teardown before any binary/configuration changes, then
rebuild/prepare/relaunch as one coordinated operation. Never patch generated live
configs, rebuild live binaries or restart an individual router.**

```bash
CONNEXT_SHARED_DIR=/home/dgreenberg/rti_connext_dds-7.7.0 \
  harness_v2/scripts/run_mesh.sh down --emane \
  --workdir "$PWD/debug/mesh_runs/emane"
```

## Architecture

Newly prepared bundles use one native session with an always-on WAN hub route
and independent one-way LAN binding routes:

- Static original-type LAN binding definitions, selected by node role. Each
  application input or output has its own discovery-triggered route using
  `ON_DOMAIN_MATCH` and native `auto_configure_qos=true`.
- The hub uses `IMMEDIATE` ports and explicit XML QoS, with auto-QoS disabled.
  Health/monitoring and available bindings run even when other applications
  have not appeared. Missing bindings remain pending, not falsely active.
- In `three_topic_union` mode, exactly one Event, State and Periodic
  reader/writer pair, including State when no contact binding is enabled.
- The existing `two_topic_union` mode retains its Event/Status pairs.
- One `ActGatewayPlugin` processor instance per route shares node state and
  native port handles. Routing Service creates and owns all DDS entities.
  There is no additional WAN participant, intermediate DDS bus or plugin backlog.
- The production [codec](../src/core/WanCodec.cxx), [audience rules](../src/core/WanInterests.cxx),
  [configuration parser](../src/config/RouteConfigParser.cxx),
  [IDL](../../harness_v2/datamodel/ActTypes.idl), and
  [QoS XML](../../harness_v2/qos/act_qos_profiles.xml) are reused.

Previously prepared combined-route XML remains supported. It has one processor
without independent-runtime role properties; it retains the old startup barrier.
The retained mesh no longer uses this older layout.
Dashboard configuration tables aggregate these native binding routes and mark
auto-QoS. Their policies are configured XML snapshots, not live inferred values.

Empty and `default` LAN endpoint QoS aliases resolve to the production
`router_default_reader`/`router_default_writer` profiles. Named aliases still
require an entry in `qos_profiles`. There is no automatic one-second Periodic
input filter. LAN auto-QoS does not infer every policy: finite output deadlines,
retention and local resource budgets still need explicit XML. Development translation replaces the
old inherited `lan_status_1s` default with its unfiltered `lan_status` base unless
that topic is explicitly selected in `periodic_input_profiles`.

Physical topic names and payload types are independent:

| Three-topic WAN name | DDS type | Payload branches |
| --- | --- | --- |
| Event | WanEvent | Commands, ACKs, assignments and reserved RouterConfig |
| State | WanEvent | ContactReport only |
| Periodic | WanStatus | Existing periodic reports, diagnostics and reserved health |

These are unchanged schema-3 union types, not new WanState/WanPeriodic types.
The processor rejects wrong-family frames after decoding; CFT interests also
select original topics by physical family. In full mode, health and RouterConfig
share these existing pairs; there are no hidden WAN monitoring endpoints.
The deployed custom router calls the last topic Telemetry; do not mix it with
Periodic on a live deployment.

The processor merges source aliases, packs/unpacks whole structures, restores
authoritative contact sources, and forwards native dispose/unregister only for
materialized output instances. Key recovery happens while the input loan is
held. Periodic Status bindings are always enabled, regardless of old
primary/detail/debug route gates. Already-created inputs for disabled Event
bindings drain samples without forwarding; absent applications remain pending.

The hub selects native application WAN history by original topic and consumes it only when
that topic's LAN output exists. Required events can therefore wait in DDS history
for a late consumer without blocking unrelated topics or adding a plugin queue.
Native history/resource limits still apply. Admission rejection and write
timeouts/errors are diagnosed; failed output writes have no plugin retry/replay.
Forwarded counters mean submission, not confirmed application delivery.

Callbacks consume at most 32 samples per LAN input or WAN original topic per
pass. Monitored Status/Periodic inputs first observe at most 32 unread samples,
validating their schema, payload and transport family before notifying presence.
Only samples read by that observation pass can then be consumed, so forwarding
cannot bypass observation; pending LAN outputs retain the observed DDS history.
Lifecycle notifications still reach the health-loss callback, and valid health
samples are not processed twice. A periodic callback continues draining without
depending on another arrival. These bounds do not establish a command-latency
guarantee under overload.
LAN output publications are ignored by their own participant to prevent feedback.
WAN content filters exclude the local origin. Own WAN publications and
subscriptions are ignored on both sides, so local readers are not reliable
writer destinations.

## Selected v1 scope

The three-topic data plane, six priority classes, explicit XML configuration,
optional per-topic LAN input filtering and service-owned monitoring are
implemented. Deployment/load/recovery gates in the
[remaining deployment plan](../../docs/routing-service-three-topic-plan.md)
remain separate.

- Keep session, route and endpoint definitions static; native discovery activates
  the independent LAN endpoints. Normal operation does not require
  Routing Service remote administration or compatibility with the old router's
  route-management commands.
- Platforms publish all available telemetry locally by default. The latest
  mission-dependent selection direction is platform-side receive CFTs driven by
  local state, not C2-driven route or producer-mode commands. Existing C2 receive
  behavior is unchanged; the new policy is not implemented yet.
- Keep Periodic reports always on, independent of old primary/detail/debug
  gates. Input filtering is a separate **optional, off-by-default** per-topic
  LAN-reader choice. `ACT_QOS_LIB::lan_status_1s` is an available one-second
  profile, not an automatically selected policy. Its minimum separation is
  per original keyed instance, not a periodic/latest-value snapshot guarantee.
- Do not add WAN-reader time throttling or per-original-topic WAN rate
  controls; WAN CFTs select scope only. Event/State LAN input time filtering is
  explicitly rejected. The WAN time-filter experiment below is historical
  evidence, not the selected architecture.
- Do not throttle discrete Event traffic, application commands or their ACKs.
  Health keeps its separately specified cadence and presence behavior.
- Historical queries and Persistence Service are deferred.
- Keep existing application monitoring contracts. The Routing Service variant
  publishes the same externally consumed topics, DDS types, keys and field
  semantics as the custom router, including router status, health/presence,
  mesh summaries, link statistics and diagnostics. Controller journals are
  explicitly deferred; journal-driven views are not part of this parity gate.
  Static-route status must truthfully describe the configured topology; dropping
  old route-management commands does not remove these publication contracts.

The existing platform simulator publishes its full telemetry task set. The
processor retains team/audience updates and transactional model validation.
These controls do not restore route-management or report-mode gating. Full
monitoring reuses production presence/link collection with service-owned
endpoints. WAN reader time filtering remains deliberately disabled.

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
or overwrite the live mesh binary. Development YAML translation requires
PyYAML and `act_rs_config`; prepared XML launch needs neither.

## Configure and run

Run from the repository root with an offline copy of the production YAML:

- Select `router.wan_contract: three_topic_union`.
- Select `ACT_QOS_LIB::wan_three_topic_participant` for the WAN participant alias
  (or its `_emane` variant for the owned EMANE environment).
- Select `ACT_QOS_LIB::wan_three_topic_event` for `wan_event` and
  `ACT_QOS_LIB::wan_union_contacts` for `wan_contacts`.
- Select `ACT_QOS_LIB::wan_union_periodic` for `wan_periodic`.
- Set `router.admin_participant` to the local-role LAN participant.
- Enable the desired Event/State bindings in that copy. Periodic bindings are
  enabled automatically; old reporting-mode gates do not suppress them.

For the retained two-topic mode, use `two_topic_union`, the
`wan_two_topic_participant` profile and `wan_event: ACT_QOS_LIB::wan_event`;
`wan_periodic` still resolves to `ACT_QOS_LIB::wan_union_periodic`.

For example:

```bash
python3 router/routing_service/run.py \
  --config /tmp/platform.yaml --role platform --node-name Boat30 --name rs \
  --build-dir /tmp/act-rs-build/routing_service \
  --team TEAM_A --data-plane-only
```

`--team` supplies initial audience membership without changing discovery
partitions; repeat it for multiple exact IDs. Live membership changes are
handled by the monitor's supported team-control path; old route-management
commands are not restored. `--render FILE` writes the complete native XML and
a sibling `FILE_STEM.types.xml` registry without starting DDS. All fixed QoS is
copied from external production XML,
including input instance retention. Three numeric DDS sentinel spellings are
normalized to schema-equivalent symbolic values for Routing Service validation;
their policy values are not retuned.

### Prepared XML launch and preflight

Prepare a deployment bundle using the development helper, or author the same
native Routing Service XML directly:

```bash
python3 router/routing_service/run.py \
  --config platform.yaml --role platform --node-name Boat30 --name rs \
  --build-dir router/build/routing_service --team TEAM_A \
  --render bundle/platform.xml

python3 router/routing_service/run.py --xml bundle/platform.xml --validate-only
python3 router/routing_service/run.py --xml bundle/platform.xml
```

`--render FILE` persists that XML and `FILE_STEM.types.xml` beside it. The XML
embeds the prepared types and QoS; the processor also loads the companion type
registry through `act.types_uri`. Generated registry and processor-library paths
are absolute: update them deliberately if relocating the bundle. Deployment
requires the matching compiled plugin, SDK/runtime and readable type registry,
not the source YAML, `act_rs_config`, PyYAML or discovery/setup tooling.

Without `--render`, development launch uses an owned temporary XML/type
directory, honoring `TMPDIR` and cleaning it after the owned service exits.
It does not write into a read-only checkout. Direct `--xml` launch writes no
configuration files. `--service-name NAME` selects a non-default native service
name; the default is `ACTGateway`.

Preflight checks the original property entries before RTI converts them to a
map: duplicate/unknown/missing properties, unresolved processor values, binding
ports/types and unsupported Event/State input time filtering fail explicitly.
It creates no DDS entities and is not a replacement for the native schema,
codec/type and endpoint activation checks performed at startup. Launch through
this wrapper to retain duplicate-property checking; a direct RTI executable
invocation is not an equally preflight-validated path.

Full mode has `act.monitoring=true`. For prepared XML, `--data-plane-only`
requires an already-authored `act.monitoring=false`; it does not rewrite the
bundle. Node/role/router/team command-line overrides belong only to development
`--config` translation. Prepared XML supplies those values itself.

### Exact processor-property grammar (v1)

Properties are native `<processor><property><value><element>` entries, each
containing one `<name>` and one `<value>`. Names are case-sensitive and unique.
Processor values must be concrete at wrapper preflight; QoS configuration
variables remain native XML configuration. Unknown fields, selected-field
copies, transformations and omitted-field/default rules are rejected.

Required globals:

| Property | Value / constraint |
| --- | --- |
| `act.version` | Exactly `1` |
| `act.types_uri` | Prepared type XML path/URI; absolute paths recommended |
| `act.node` | Originating system/node identity, not the router process name |
| `act.role` | `control` or `platform` |
| `act.router` | Router half of the node/router identity |
| `act.wan_contract` | `three_topic_union`, or retained `two_topic_union` |
| `act.monitoring` | Lowercase `true` or `false` |
| `act.lan_participant` | Native local LAN participant name |
| `act.wan_participant` | Native WAN participant name, distinct from LAN |
| `act.admin_participant` | Must equal `act.lan_participant` |
| `act.lan_domain`, `act.wan_domain` | Decimal DDS domain IDs, each 0–232, matching those participants |

Generated independent bundles repeat the same globals and binding definitions
on every processor, adding `act.runtime_mode=independent`. The hub declares
`act.route_role=hub` and no `act.route_topic`. Each one-port LAN route declares
`act.route_role=tx` or `rx`, plus `act.route_topic` equal to its literal original
topic name. There must be exactly one hub, one session and unique direction/topic
registrations. Only these role/topic properties differ between processors.
WAN and `monitor_*` ports belong to the hub.

Optional globals:

- `act.teams`: semicolon-separated initial exact team names; empty/omitted means
  no teams. Semicolons are list delimiters, with no escaping syntax. Empty or
  duplicate entries reject.
- `act.async_bandwidth_bps`: default `4000000`; positive 32-bit integer at least
  2,000,000 and divisible by 800. The native XML flow-controller values must
  express the intended budget; this property is not runtime QoS construction.

Each unique group `act.binding.ID.` binds one catalog topic. There is one group
per original topic; multiple legacy YAML aliases are merged during translation.

| Group suffix | Requirement |
| --- | --- |
| `topic_name` | Exact original LAN/catalog topic, unchanged on receive |
| `type` | Exact catalog payload type; must match native port type |
| `branch` | Exact prepared full-type union branch (fixed for built-in ACT topics) |
| `instance_id` | Existing string-identity field, not a literal instance value; custom types require this as their sole DDS key |
| `channel` | One supported family/priority channel, consistent with the catalog |
| `tx.port` | Native LAN **input** port for LAN-to-WAN forwarding |
| `rx.port` | Native LAN **output** port for WAN-to-LAN forwarding |
| `tx.recipients`, `rx.recipients` | Required for each configured direction |
| `tx.enabled`, `rx.enabled` | Optional lowercase startup `true`/`false`; default `true` |

At least one direction is required. A transmit binding does not imply a receive
binding. Direction settings without that direction's port reject. Ports must
be unique and cannot use reserved `wan_in_`, `wan_out_` or `monitor_` prefixes.
Default generated LAN names remain `lan_in_TOPIC` and `lan_out_TOPIC`; explicit
LAN names are supported. WAN ports remain `wan_in_FAMILY`/`wan_out_FAMILY`.
Monitoring's static route summaries name the logical directions `ID.tx` and
`ID.rx`, not the original merged YAML route names.

Recipients are semicolon-separated unions of:

| Selector | Meaning |
| --- | --- |
| `control` | Control audience flag |
| `global` | Global audience flag |
| `addressed` | Existing payload destination; only Mission / ActPlatformStatusMode |
| `teams` | Current team interests plus protected node/direct-peer identity |
| `exact:NAME` | One exact destination node/team |
| `none` | No audience selected; must be the sole selector, and does not mean disabled |

Names retain the existing exact-name, 255-byte and aggregate 16-name checks;
patterns, apostrophes and NULs reject. Team updates rebuild source/destination
interests with rollback if validation rejects them. They do not change native
WAN discovery partitions or reintroduce report-mode/route-management controls.

For three-topic mode, supported channels are `event_high=600`,
`periodic_high=500`, `state_high=400`, `event_normal=300`, `state_normal=200`,
`periodic_normal=100`. ContactReport selects State; periodic ACT cases select
Periodic. Explicit RouterHealth bindings require `periodic_high`. Two-topic
mode uses Event/Status with `event_high=400`, `periodic_high=300`,
`event_normal=200`, `periodic_normal=100`; `state_*` rejects.

Example binding fragment inside the native processor's property `<value>`:

```xml
<element><name>act.binding.contacts.topic_name</name><value>ContactReport</value></element>
<element><name>act.binding.contacts.type</name><value>contact_report</value></element>
<element><name>act.binding.contacts.branch</name><value>contact_report</value></element>
<element><name>act.binding.contacts.instance_id</name><value>contact_id</value></element>
<element><name>act.binding.contacts.channel</name><value>state_normal</value></element>
<element><name>act.binding.contacts.tx.port</name><value>lan_in_ContactReport</value></element>
<element><name>act.binding.contacts.tx.recipients</name><value>control;teams</value></element>
<element><name>act.binding.contacts.rx.port</name><value>lan_out_ContactReport</value></element>
<element><name>act.binding.contacts.rx.recipients</name><value>teams</value></element>
```

This is a fragment, not a runnable service by itself. Declare matching native
LAN ports/types, manually selected reader/writer profiles, participant type
registrations and the three WAN reader/writer pairs. Keep
`auto_configure_qos=false`. Full monitoring additionally declares the
`monitor_command` LAN input and `monitor_ack`, `monitor_status`, `monitor_mesh`,
`monitor_links`, `monitor_diagnostics` LAN outputs. Internal RouterConfig uses
Event and RouterHealth uses Periodic/Status; their WAN CFT cases must retain
`source_id <> %0`. No extra WAN monitoring pair is permitted.

**Prepared schema boundary:** ACT-only configurations still validate the complete
[ACT catalog](../src/core/WanCodec.cxx), with unchanged whole-payload copies and
authoritative ContactReport source restoration. Built-in ACT topic/type/branch/key
definitions cannot be overridden. Custom bindings use the same grouped properties
and frozen `WanEvent`/`WanStatus` envelope layout. Their named payload branch must
have exactly the declared original type, not merely an assignable type, and one
nonnegative, non-default union label. Missing/wrong branches, conflicting labels
or member IDs, duplicate bindings and unsupported keys reject before forwarding.
No application IDL or generated application classes are needed.

For custom types, `event_*` and `state_*` select branches in `WanEvent`;
`periodic_*` selects branches in `WanStatus`. The channel still chooses the
physical family and priority. The configured node is the envelope source, the
configured existing string key becomes the raw instance ID, and explicit
`control`, `global`, `teams` or `exact:NAME` recipients supply audiences.
ACT-only addressed-command extraction is not inferred for custom commands.
Custom payloads are copied whole; no audit/source fields are guessed or rewritten.
Envelope audit fields stay at their defaults on this bounded custom path.
Custom bindings currently require static receive policy and disabled monitoring;
they use the same shared native hub and independent LAN activation.

Explicit binding IDs are local labels, separate from literal topic names, and
are preserved when preparing or reloading a bundle. For example, binding ID
`command` can carry topic `devices.commands`; the dot stays in `topic_name`,
not in the property-group ID. Development preparation retains its existing
ACT topic-based IDs.

Registry validation also checks envelope capacity before forwarding: at least
255 characters for source/topic/role/run strings, 512 for the instance ID,
and 16 audience names of at least 255 characters each. These are the existing
envelope limits, not new payload fields. Payload instance keys must be strings
with declared capacity no greater than the supported 512-character instance ID.
Undersized envelopes and oversized/unbounded instance keys reject at startup
with a capacity diagnostic rather than fail on later valid traffic.

[`fixtures/full_type_bindings.xml`](fixtures/full_type_bindings.xml) is an
entity-free registry fixture with a non-ACT command and nested status. Build
`test_full_type_bindings` and run the `full_type_bindings`, `wan_codec` and
`wan_interests` CTest cases in a fresh private build. With
`ACT_RS_BUILD_DIR` pointing at its `routing_service` directory, run:

```bash
PYTHONPATH=router/routing_service python3 -m unittest test_config test_full_type_bindings
```

Set `ACT_RS_TEST_WORKDIR` to an owned writable work directory. Only inside an
owned private network namespace, set `ACT_RS_SCHEMA_ISOLATED=1` to also run native
processor round trips for both custom types and existing ACT command/status.
The custom checks also cover dotted topic names, rejected undersized envelopes,
and exact-boundary keys, identities, topics and full audience sets.
These tests exercise prepared XML, the bundled registry and real LAN/WAN
endpoints; they are not fleet/mission or independent late-binding qualification.

### Native LAN auto-QoS qualification

XML preflight resolves inherited time-filter durations before rejecting
Event/State input filtering. An effective zero minimum separation is allowed,
including an explicit zero override of a filtered base profile.

The user-supplied [ROUTING-1309 feature document](../../docs/routing-service-qos-auto-configuration-routing-1309.md)
is retained verbatim as a reference, not a published manual. It also contains
sections labeled "Potential Change (K Release)"; do not treat those proposals
as verified behavior in the installed version.

Initial combined-route characterization on 2026-11-16 tested a fresh Connext
7.7.0 processor build in private
`--network none` containers, with UDPv4-only participants and domains 91-106.
No live mesh configuration or binary was changed.

The [qualification runner](qualify_auto_qos.py) and its
[XML profiles](fixtures/auto_qos.xml) compare `auto_configure_qos=false/true`,
inspect native discovery QoS, and exercise actual application data through
the processor. Whole-payload equality is checked in the focused positive reruns.
Applications were created both before and after service startup.
The current independent-layout runner defaults to `ON_DOMAIN_MATCH` and rejects
`IMMEDIATE` at argument parsing. The historical `IMMEDIATE` result below describes
the earlier combined-route experiment, not a supported current runner option.

| Case | Observed result |
|---|---|
| `IMMEDIATE`, auto-QoS enabled | No application LAN endpoint or processor startup marker within the 10-second observation window, with or without explicit port profiles |
| `ON_DOMAIN_MATCH`, no explicit LAN QoS profile, reliable/transient-local producer | Input reader changed from best-effort/volatile with auto-QoS off to reliable/transient-local with it on; exact original payload reached WAN |
| `ON_DOMAIN_MATCH`, no explicit LAN QoS profile, reliable/transient-local consumer with infinite deadline | Output writer changed from reliable/volatile to reliable/transient-local; disabled control did not match, enabled case matched and restored the exact payload on LAN |
| Consumer requests a 250 ms deadline | Auto-QoS selected transient-local durability but retained an infinite writer deadline; no match |
| Explicit 100 ms writer deadline plus auto-QoS | Deadline remained explicit, durability became transient-local, and exact-payload LAN delivery passed |
| Explicit incompatible reliability/durability profile | Input profile's reliable/transient-local request remained unchanged; output reliability remained best-effort while durability became transient-local. These cases remained incompatible |
| A second configured LAN input is absent | The present input matched, but the shared processor route did not reach its startup marker or forward its data during the observation/delivery windows; this happened with auto-QoS both off and on under `ON_DOMAIN_MATCH` |

This proves native QoS inference can operate with our custom processor, not
that it copies every policy or continuously reconciles arbitrary endpoints.
The creation mode, policy-specific profile interaction and combined-route startup
barrier prevented enabling it by simply flipping a flag. The current renderer
instead separates the always-on native hub from independent LAN bindings.

The production [independent-binding acceptance module](../test_e2e/test_routing_service_auto_qos.py)
passed **8/8 cases** in a fresh private-network/private-IPC build on 2026-11-17: false/true
inference controls with applications before/after startup, exact payload
forwarding, absent-topic isolation, stable WAN handles after late activation and
producer replacement, no feedback, three same-instance required events retained
for a late consumer, and an explicit 100 ms output deadline with inferred
transient-local durability, plus standalone qualifier defaults and early rejection
of unsupported creation modes. Event-history retention uses explicit XML; it is
not claimed as inferred. Multiple conflicting producers/consumers and complete
policy/load coverage remain unqualified.

To repeat a positive comparison, run inside an owned private-network Connext
container with a fresh processor build, the checkout read-only at `/workspace`,
and its license mounted. For example, with the fresh build at `/tmp/rs-build`:

```bash
ACT_RS_AUTOQOS_ISOLATED=1 python3 -B \
  router/routing_service/qualify_auto_qos.py \
  --build-dir /tmp/rs-build/routing_service \
  --profile defaults \
  --application-profile application_infinite_deadline
```

The runner emits JSONL observations, including matching, actual delivery and
startup status; exit zero alone does not mean every characterized combination
supports forwarding. Use `--application-profile application_writer_reliable`
to expose input-side inference, `--profile deadline --direction rx` for the
finite-deadline positive case, and `--absent-input --direction tx` for the
offline-port startup check. The unprofiled and explicit incompatible-profile
matrices, focused deadline checks, positive reruns and offline-port check
executed 52 scenarios; a separate verbose probe was also run. Compact evidence
is retained locally in `debug/test_reports/rs_autoqos_summary.json`.

### Platform mission receive policy

`act.receive_policy` defaults to `mission` on platforms; control reception is
unchanged. The development YAML equivalent is `routing_service.receive_policy`.
The approved fixed profiles are:

| Profile | Local authority | Application WAN interests |
| --- | --- | --- |
| Init | Startup, IDLE, COMPLETE, CANCELLED | C2 Mission and Team |
| ActiveMission | TRANSIT, ORBIT, RTB, HOLDING | C2 Mission/Team; same-team peer PlatformMissionStatus/ContactReport; fleet-global peer PlatformStatus navigation |

Required RouterHealth/RouterConfig interests remain enabled. PlatformData and
ActPlatformStatusMode are not selected. PlatformStatus positions are selected
from all platform peers only while active, including across teams and when
unassigned. An unassigned platform has no peer mission/contact interests; its
protected node identity is not a team. Platform reporting to C2 remains always on. Peer report sources add
the team audience without duplicating their logical sends.

Preparation emits Init CFTs directly into the native XML, before readers start.
The YAML helper adds the static peer LAN outputs; explicit XML must provide
Mission, Team, PlatformStatus, PlatformMissionStatus and ContactReport output bindings and local
PlatformMissionStatus/ContactReport inputs. The processor validates these
requirements. State observation runs only on the local mission input, with
native own-publication ignore retained. `source` must match the configured node;
nonempty `source_node` must also match. Ordering uses `sent_at_ns` (or the existing
microsecond `timestamp` converted to nanoseconds) and `audit_sequence` for equal
timestamps. Invalid identity/state, missing timestamps and stale updates are
diagnosed without preventing outgoing application forwarding or widening filters.
The local timestamp watermark advances before state/filter application, so an
older valid phase cannot supersede a newer rejected update. Foreign-source
samples do not advance that watermark.

State/team updates validate all filters first and use the same serialized
transactional native CFT path. Failure rolls back every family, including the
family whose update threw. Any rollback failure is explicitly reported; applied
state/profile is not advanced. Already accepted reader samples may still drain
after narrowing; changes do not promise an atomic fleet-wide barrier or replay.
Whole-system startup always uses Init with no saved policy or request protocol.
The source pump snapshots topic names before taking loans: a policy transaction
replaces the model, so no iterator or binding reference may survive observation.

RouterHealth reports `receive_profile`, `receive_state` (startup `Init`, then last
successfully applied local phase) and `receive_filter_error`, separately from
`reporting_policy=always_on`. Successful policy/team updates clear filter errors.
For intentionally broad static data-plane acceptance or explicit legacy
compatibility, set `act.receive_policy=static` (YAML:
`routing_service: {receive_policy: static}`). This opt-in keeps existing static
receive bindings and reports `receive_profile=static`; it is not the platform
production default.

### Optional Periodic LAN input filtering

Leave native LAN readers unfiltered unless a particular topic needs filtering.
Choose an XML-authored reader profile on that topic's native `<input>`; there
is no second processor-property QoS authority. The development equivalent is:

```yaml
qos_profiles:
  periodic_1s: ACT_QOS_LIB::lan_status_1s
routing_service:
  periodic_input_profiles:
    PlatformData: periodic_1s
```

The helper emits `inputs[topic=PlatformData].profile` as the resolved profile,
and the renderer uses it as the LAN input's `datareader_qos` base. Without the
override, the inherited legacy `ACT_QOS_LIB::lan_status_1s` profile becomes
`ACT_QOS_LIB::lan_status`; other selected route reader profiles remain unchanged.
This selects existing XML profiles rather than constructing or retuning QoS in
code. Explicit native XML reader profiles remain the deployment authority.
Filtering does not turn always-on reports into old primary/detail/debug modes.
Event/State input filtering is unsupported; WAN readers are not time-throttled.

### Asynchronous priority configuration

The three WAN writers use opt-in `rs_three_topic_event`,
`rs_three_topic_state` and `rs_three_topic_periodic` XML profiles.
Their two `DDSPRIFLTR` channels share the `act_rs` highest-priority-first flow
controller on the existing WAN participant. The launcher composes the controller
properties and only `resource_limits.participant_property_list_max_length` from
`rs_priority_participant` into that participant. The external XML supplies the
capacity (currently 64); no limit is hard-coded in Python. Other resource limits,
discovery and transport settings, including EMANE interface selection, remain
inherited from the selected WAN base profile.

| Class | Sample priority | Default original topics |
| --- | --- | --- |
| Event / High | 600 | Mission, PlatformCommandAck, Team, ActPlatformStatusMode |
| Periodic / High | 500 | RouterHealth |
| State / High | 400 | Explicit ContactReport override |
| Event / Normal | 300 | Event topics explicitly assigned Normal; reserved RouterConfig defaults here |
| State / Normal | 200 | ContactReport |
| Periodic / Normal | 100 | All other periodic cases |

Event retains reliable KEEP_ALL. State inherits reliable writer KEEP_LAST(1)
and reader KEEP_ALL. Event and State cap each NACK repair response at 64 KiB
(65,536 bytes). Periodic remains best effort KEEP_LAST(1). All three
writers share the same nominal 2 Mbps controller. The retained two-topic
profiles `rs_wan_event` / `rs_wan_periodic` and their 400/300/200/100 ordering
are unchanged. Priority preferences do not guarantee starvation-free health
delivery or bounded application latency.

Data, dispose and unregister all carry the configured priority. Both channels
accept priority zero in their Normal band, but the processor stamps the
explicit values above. FIFO is preserved within a band. Priorities are static
for a service run; live band migration with queued lifecycle samples is not
implemented.

Optional root-level YAML settings override the defaults:

```yaml
routing_service:
  async_bandwidth_bps: 4000000
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
explicit. The read-only Config and Delivery APIs inspect native XML, embedded
QoS overrides, configured bandwidth, priorities and LAN filtering. An environment
override of the logical bandwidth setting is not implemented. Changes take effect on service restart, not by retuning
the running mesh.

In three-topic mode ContactReport uses State's reliable latest-value policy,
separate from Event retention. Two-topic mode still carries it on Event with
KEEP_ALL. Legacy/custom-router profile selection is unchanged.

**RouterHealth is assigned Periodic/High (Status/High in two-topic mode).**
Full mode produces health, presence, status, mesh summaries, link statistics and
diagnostics through service-owned endpoints. Scheduler-only tests that inject
health frames remain distinct from full-monitoring integration checks.
Processor execution priority and controller journals remain separate/deferred.

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

## Three-topic data-plane qualification (2026-11-13, historical)

This gate predates explicit XML configuration and the full-monitoring path.

A fresh processor/config-helper build in Connext / Routing Service 7.7.0 passed:

- **36 acceptance tests**, zero skips, across retained two-topic and new
  three-topic modes (154.97 s). Coverage includes all fourteen application
  payloads, exact physical endpoint counts, lifecycle, addressed filtering,
  malformed-frame recovery, per-topic priorities, native writer policies,
  six-class middleware reception order and State-only contact forwarding.
- **18 configuration/launcher tests**. A broader unit discovery also found
  three separately opt-in WAN time-filter checks, which were skipped.
- Both documented role renders have ten ports and pass the installed Routing
  Service XSD after variable expansion; all eight guide Bash blocks parse.

The initial combined test run exposed shared-fixture directory collisions and
domain-budget exhaustion, not processor failures. Contract-specific workdirs
and removal of unused DDS reservations from configuration-only checks fixed
the harness; the complete combined acceptance run then passed.

Run acceptance checks with `ACT_RS_BUILD_DIR` set to the fresh variant build:

```bash
python3 -m unittest discover -s router/routing_service -p test_config.py
PYTHONPATH=router/routing_service python3 -m unittest test_launcher
python3 -m pytest -q -p no:cacheprovider \
  router/test_e2e/test_routing_service.py \
  router/test_e2e/test_three_topic_routing_service.py
```

Use an owned private network namespace, not host networking against the live
mesh. The retained seven-node mesh was untouched. This is not a two-host,
fleet/load, restart/recovery or health-generation integration gate. Evidence:
[acceptance JUnit](../../debug/test_reports/rs_three_topic_0480d1ea.xml).

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
production `wan_union_periodic` endpoints. Five existing renderer tests also pass.
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
WAN transmission**. When filtering is explicitly needed, select it on the
original LAN input, before WAN publication; it is not enabled by default.
Applying a period to the shared reader also applies it to health
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
The full gateway path supplies the application contracts through its
service-owned `monitor_*` LAN ports, static configuration, native endpoint
facts and processor counters, reusing production presence/link collection on
the shared WAN ports. It does not depend on subscribing to RTI's monitoring bus.
The native hub and binding routes do not automatically describe every logical original-topic
mapping inside the processor, nor prove end-to-end delivery. Native monitoring
can be enabled independently of remote administration.

This distinction was checked against the installed 7.7.0 IDL and
[monitoring documentation](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/services/routing_service/monitoring.html);
RTI monitoring-bus integration is not selected or runtime-qualified in this variant.
Do not implement controller journal machinery in the current scope.

### Per-original-topic status requirement

Each logical original-topic entry in `ActRouterStatus` must identify its WAN
family (Event/State/Periodic, or Event/Status in two-topic mode) and its configured priority, alongside processor
forwarding/lifecycle counts and errors. The family identifies the shared WAN
endpoint; an independent native LAN route is not an additional WAN pair. Matching and
protocol counters remain shared-endpoint facts, not branch-specific evidence.

The selected priority model has two levels, **High** and **Normal**, within
each family. Each
original topic is assigned a level; Event is not implicitly High and Status is
not implicitly Normal. These are priority classes, not six WAN topic names
or a decision to create additional endpoints.

The selected three-topic asynchronous ordering is Event/High, Periodic/High,
State/High, Event/Normal, State/Normal, then Periodic/Normal. Put RouterHealth in
Periodic/High and ContactReport in State/Normal by default. Use
asynchronous writers sharing a highest-priority-first flow controller, with
High/Normal MultiChannel separation within each family. Fixed policies belong
in external XML; original-topic assignments supply the sample priorities.
Processor execution scheduling is a separate, deferred layer. This ordering
improves health's preference over normal traffic but cannot guarantee health
progress under continuously saturated Event/High traffic. The implementation
and its default assignments are described above; fleet capacity, outage recovery
and health progress under saturation remain unqualified.

The third WAN topic now separates latest-contact retention from Event's
reliable KEEP_ALL retention. History depth is per
complete key, not one contact sample for the whole writer. Superseded updates
may be replaced without delivering every observation; a writer's dispose also
counts toward history depth. This may reduce disposal delay from same-contact
update backlogs, but does not bound delays across many contacts, transmission
queues or outages. Qualify write/dispose/revive and remote final-state recovery
before claiming a disposal-latency improvement. This is not persistence.

`RouterRouteTopicStatus` carries explicit `wan_family` and
`publication_priority` fields supplied from the validated model. Do not present
configured priority as proof of latency. Existing topic names and compatible
fields remain the baseline; unchanged UI views do not imply that every added
status field is rendered automatically.

## Remaining parity gates

The migration is **not complete** until retained-scope behavior tests can run
against this backend. Deliberately retired management/mode behavior must be
identified explicitly rather than reported as unchanged parity:

- Broader retained application-command/ACK and live-mission acceptance.
  Always-on reports, optional LAN filtering and full-monitoring acceptance have
  focused tests, not a complete fleet/load gate.
- Configured/effective budget qualification under load; an environment override
  of the logical bandwidth setting remains a later enhancement.
- Team/audience convergence and rollback under fleet churn/load, including
  outgoing metadata revisions and remote writer-side suppression.
- Health on the shared Periodic/Status pair under congestion, fleet restart,
  teardown and session recovery; monitoring startup tests alone do not close this.
- End-to-end protocol-statistics, diagnostics and monitoring freshness under
  impairments. Controller journals and journal-driven views remain deferred.
- Exact remote original-type validation, wrong-type containment, multi-producer
  lifecycle/alias stress, unknown keys, and queued final-state behavior.
- Backend selection for the existing e2e suite; legacy/dynamic-route test contracts
  must be accounted for explicitly, not silently skipped as “equivalent.”
- Existing qualification, recovery/load budgets and unchanged live mission tests.

No full Python-suite, live-mission, performance, or deployability parity is
claimed. The existing recovery/prioritization limitations remain limitations;
changing the routing framework does not qualify them away.
