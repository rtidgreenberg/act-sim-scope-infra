# Replacing the ACT Router with RTI Routing Service and Processor Plugins

**Date:** 2026-11-06

**Target:** RTI Connext / Routing Service 7.7.0

**Status:** Investigation and recommended proof-of-concept; not an approved migration or a runtime-equivalence claim.

## 1. Executive conclusion

**The proposed replacement is technically credible. The two central hypotheses are supported:**

1. **Custom topics:** a processor can generate samples for configured DDS outputs, including ACT health, mesh, status, journal, and diagnostic topics. Periodic callbacks allow generation independently of application-data arrival.
2. **Protocol statistics:** a processor can obtain the actual DDS reader/writer behind its route ports. Those endpoints expose the per-matched-peer protocol statistics already used by the custom router.

The second point is stronger than a documentation-only inference: a compile-and-link probe against the installed **7.7.0** SDK successfully accessed Routing Service endpoints and instantiated the repository's existing [WAN polling helpers](../../router/src/core/WanStatsPoll.hpp).

**Recommendation:** prototype **Routing Service as the routing/entity-lifecycle engine, route processors for endpoint-local telemetry and forwarding extensions, and a small ACT management/compatibility layer for control and aggregate state**.

Do not frame this as either "Routing Service cannot expose our metrics" or "a processor replaces the entire router." The evidence supports a middle ground:

- Routing Service removes the need to own the general route engine, discovered-type routing, and much DDS entity creation/destruction machinery.
- Processors recover observability at the real DDS termination points.
- ACT command semantics, protected team partitions, revisioned state, presence interpretation, and dashboard/audit contracts remain application-specific work.

**The largest unresolved risks are control-plane equivalence and processor forwarding semantics, not access to protocol counters.**

## 2. Evidence and verification boundary

### Work performed

- Reviewed current configuration, route runtime, discovery, controller, presence, link-statistics code, IDL, feature inventory, and relevant test coverage.
- Consulted **RTI Connext AI twice**: first on feasibility/API support, then on processor ownership, custom forwarding, auto routes, participant partitions, and timer-driven output.
- Read the official Routing Service manual, Processor API, configuration, administration, monitoring, and Shapes Processor documentation.
- Inspected installed headers and the Routing Service schema inside the existing `connext:7.7.0` Docker image.
- Compiled and linked an isolated API probe with the existing WAN polling implementation. No DDS participants were started.
- Synchronized and checked the shared [known-wrong RTI AI answers](../connext-ai-issues/connext-ai-issues.md). No existing entry specifically covered Routing Service processor endpoint access.

### Evidence levels used below

| Label | Meaning |
|---|---|
| **API verified** | Installed 7.7.0 headers and successful compile/link establish the API surface. |
| **Documented** | Official manual/API describes the behavior; not exercised in this investigation. |
| **Design proposal** | An application design derived from documented capabilities, not a shipped implementation. |
| **Runtime gate** | Must be demonstrated on the exact target configuration before claiming migration readiness. |

The supplied `current` manual identified itself as **Routing Service 7.7.0** when retrieved. `current` URLs and the public examples repository can change; pin the product version and example revision for implementation.

The available Docker SDK architecture was **`x64Linux4gcc8.5.0`**, not the historical host architecture recorded elsewhere in this repository. The API probe used the image's headers and libraries consistently.

**Not performed:** a live Routing Service deployment, a loaded processor-plugin run, schema validation of a complete replacement configuration, delivery/impairment experiments, performance measurements, or the full router test suite. Therefore this report proves API feasibility, not runtime parity.

## 3. What the replacement must actually preserve

The current implementation is more than a sample copier. Its relevant contracts are visible in:

- [Production configuration](../../router/config/control-platform.yaml): role-selected LAN/WAN legs, targeted command filters, control broadcasts, team traffic, and separate navigation scope.
- [Router controller](../../router/src/core/RouterController.hpp): serialized state changes, enable/disable, partition mutation, command acknowledgements, and revisioned status.
- [Discovery dispatcher](../../router/src/core/DiscoveryDispatcher.hpp): endpoint/type discovery and same-node-router ignore.
- [Route runtime](../../router/src/core/RouteRuntime.hpp): data forwarding, match/QoS reporting, counters, partition updates, and WAN-leg polling.
- [Presence monitor](../../router/src/core/PresenceMonitor.hpp): WAN `RouterHealth`, ALIVE/STALE/DEAD roster, bounded transitive peer views, and LAN mesh publication.
- [Link statistics collector](../../router/src/core/LinkStatsCollector.hpp): endpoint-local protocol measurements, per-peer/per-topic rollups, and probe application-acknowledgement RTT.
- [Canonical IDL](../../harness_v2/datamodel/ActTypes.idl): application and infrastructure types consumed by simulators and the dashboard.
- [Feature inventory](router-feature-inventory.md): useful implementation inventory, but subordinate to current source where they differ.

### Important distinction: implementation versus aspirations

The [thesis](thesis-and-tenets.md) describes lifecycle mirroring and presence-driven instance reset as desired semantics. The current [forwarding loop](../../router/src/core/RouteRuntime.hpp) still skips invalid samples; the inventory explicitly lists keyed dispose/unregister mirroring as unimplemented.

Consequently, Routing Service's built-in lifecycle propagation is a potential **improvement**, not proof that the current router already preserves those semantics. If a custom processor is installed, that improvement must itself be tested.

The historical argument that Routing Service does not provide endpoint protocol metrics also needs qualification: **its built-in monitoring is not those metrics, but its Processor API exposes the DDS endpoints from which those metrics can be collected.** This finding revisits that rationale without changing the adopted architecture.

## 4. Capability and delta matrix

| Current need | Routing Service / processor mechanism | Remaining ACT work or gate |
|---|---|---|
| LAN/WAN DDS bridge | **Documented:** DDS connections, DomainRoutes, Sessions, TopicRoutes. | Translate the role-aware configuration; retain domain and transport topology. |
| Wire-learned application types | **Documented:** discovered types, `route_types`, and AutoTopicRoutes. | Verify TypeObject/type-lookup settings against actual app writers, including late type arrival. Do not assume identical first-learned-type policy. |
| Multiple dynamic application topics | **Documented:** filtered AutoTopicRoute templates instantiate concrete routes. | Preserve allowlists; prevent infrastructure topics from entering application routes accidentally. |
| SQL destination filtering | **Documented:** input content filters; mutable parameters through administration. | Preserve node substitutions and keyed target fields. Filter-expression changes require the documented disabled-update path. |
| Explicit XML endpoint QoS | **Documented:** participant, Session publisher/subscriber, reader/writer QoS. | Reuse external ACT profiles. Preserve WAN discovery, reliability, resource limits, and health timing. |
| Current asymmetric/derived auto-QoS | No demonstrated equivalent to the current endpoint derivation algorithm. | Either port the narrow policy or explicitly approve XML-profile-only behavior. Endpoint access alone does not prove safe live adaptation. |
| Route enable/disable | **Documented:** administration state operations. | Map ACT command IDs and route names to service resources; reconcile observed state before reporting success. |
| Route publisher/subscriber partitions | **Documented:** mutable Session publisher/subscriber QoS; administration includes a partition-update example. | Session scope differs from the current per-route parents. Use appropriate Session boundaries so one route change does not retarget unrelated routes. |
| Participant discovery-scope partitions | **Documented; original implementation found:** connection/participant UPDATE accepts mutable participant QoS, and the legacy ACT admin client submits team partition changes through it. | Control/platform orchestration owns team policy and persistence and submits the intended partition set through administration. Resource paths, generated-route effects, and team rediscovery remain a **runtime gate**. |
| Same-node relay loop prevention | **Documented:** service `group_name` causes built-in DDS participants to ignore others in that group. | Use a node-local group, not a mesh-global group. Test equivalence to the current `act.router` identity/ignore rules. |
| Forwarded count and match/QoS summaries | **API verified:** processor writes and native endpoints; built-in resource monitoring is also available. | Preserve ACT status fields and definitions; do not substitute service throughput for destination delivery. |
| Per-peer/per-topic protocol metrics | **API verified:** native route endpoints plus existing WAN polling helpers. | Port baselines, rematch handling, peer attribution, route identity, and aggregate publication. |
| Application-ack RTT probe | Existing DDS approach is reusable in principle. | Dedicated probe route/endpoints; validate acknowledgement listener ownership and teardown. A processor timer alone is not an RTT implementation. |
| Custom heartbeat and mesh topics | **API verified:** configured output access, sample creation/write, periodic callback. | Port roster, timing, bounded peers, revision rules, and independent WAN/LAN cadences. |
| ACT command/ack idempotency | Not an ACT-specific Routing Service feature. | Retain application deduplication, targeting, protected-partition checks, error handling, and truthful acknowledgements. |
| Revisioned status and controller journal | Custom configured outputs can publish these types. | Preserve transition/revision semantics, causal command context, decisions, and reasons. |
| WAN RTPS delivery verification | Existing passive WAN capture and wire-delivery aggregation are outside the router. | Preserve capture/node mapping and discovery-based endpoint metadata for Routing Service writers. Application/RouterHealth audit hooks are not required for the current WAN Delivery metric. |
| Dispose/unregister propagation | **Documented:** built-in TopicRoute propagation, defaults enabled. | A custom processor controls forwarding; pass appropriate metadata and test keyed lifecycle behavior explicitly. |
| End-to-end instance-state consistency | Not solved merely by changing relay implementation. | Remains outside the demonstrated replacement scope; retain [ISC findings](isc-findings.md). |
| Packet/air-interface accounting | Not equivalent to Routing Service throughput or DDS protocol status. | Retain packet capture, traffic monitor, and EMANE telemetry. |
| Participant-scoped Connext network capture | **Documented; header inspected:** in-process Network Capture can record selected local participants, including discovery and shared-memory traffic. | Enable capture before participant creation; an embedded host is the straightforward integration point. No standard standalone Routing Service capture switch was established. |

The installed schema also contains Session `auto_configure_publisher_qos` and `auto_configure_subscriber_qos` flags. Their existence is **not** evidence of equivalence to the custom router's DataReader/DataWriter auto-QoS derivation; no recommendation here depends on them.

## 5. Custom topics in processors

### Supported model

A processor belongs to a Route and can access that Route's configured inputs and outputs. Those ports can use different topics/types, so diagnostics need not have the same type as the forwarded application payload.

For a diagnostic output represented as DynamicData:

```cpp
auto output = route.output<dds::core::xtypes::DynamicData>("diagnostic_output");
auto sample = output.create_data();
// Populate members defined by the configured diagnostic type.
output.write(sample);
```

The output type and participant registration must exist. Use the existing generated ACT XML types rather than inventing duplicate schemas.

There are two useful arrangements:

1. **Explicit multi-output TopicRoute:** application forwarding and a diagnostic output belong to one processor.
2. **Separate ACT infrastructure TopicRoute:** health/admin inputs and diagnostic/status outputs share an infrastructure processor, independently of application route activation.

The second arrangement is preferable for node-wide health and status. It avoids creating a health/status writer for every application topic and keeps health alive when an application route is disabled or has not discovered its type.

AutoTopicRoutes generate one-input/one-output concrete routes. Do not assume their templates can add an arbitrary extra diagnostic port to each generated route. Collect copied telemetry from those route processors and publish aggregate diagnostics elsewhere.

### Periodic generation

Implement `Processor::on_periodic_action(Route&)` and configure `<periodic_action>` at Route or Session scope. The Route setting overrides the Session default. Notifications occur only while the Route is RUNNING.

- Heartbeat publication must not depend on application-data callbacks.
- Preserve the current independent approximately 1 s WAN heartbeat and 0.5 s LAN mesh cadences.
- Use XML-defined types and `IMMEDIATE` creation for infrastructure ports that must exist without external publishers/subscribers.
- `<enable_data_on_inputs>false</enable_data_on_inputs>` disables data-driven notifications for a timer-only route; do not set it on a route that must also consume health/admin data.
- The installed schema permits zero inputs in a TopicRoute. **Runtime acceptance and scheduling of a complete output-only configuration were not tested**; a normal health/admin infrastructure route already has useful inputs.

The verified Modern C++ timer accessor is `route.period(duration)` / `route.period()`. Do not derive C++ method names directly from C-style or older manual wording.

### What "create custom topics" does not mean

The Processor API is not a general service-topology builder. It does not expose a public operation to add arbitrary ports or new Routes to its owning Route.

Known infrastructure topics should be configured. Discovery-driven application routes should use AutoTopicRoutes. Runtime resource creation/update should use administration or a management layer, not synchronous self-reconfiguration from a processor callback.

### What the Shapes example proves

The supplied [Shapes Processor tutorial][tutorial] demonstrates aggregation and splitting using configured DDS ports. The [public example README][shapes] also discusses the shared-library/plugin model, named port access, and cache/history considerations.

It is useful scaffolding for plugin construction, but it is **not** an example of heartbeat generation, protocol-status polling, topology management, or ACT command semantics. Those use other documented API surfaces.

## 6. Protocol-statistics collection in processors

### Exact verified endpoint access

```cpp
using dds::core::xtypes::DynamicData;

auto reader =
    route.input<DynamicData>("application_input").dds_data_reader();
auto writer =
    route.output<DynamicData>("application_output").dds_data_writer();

auto input_participant = reader.subscriber().participant();
auto output_participant = writer.publisher().participant();
```

`TypedInput::dds_data_reader()` and `TypedOutput::dds_data_writer()` are public 7.7.0 methods. They return Modern C++ DDS endpoint handles and throw if the port is not a DDS stream. They are not speculative `get_native()` accessors.

With these handles, the existing [WAN polling helpers](../../router/src/core/WanStatsPoll.hpp) compile and link unchanged:

```cpp
router::poll_reader_wan_stats(
    reader, reader_baselines, sink, route_name, topic_name);
router::poll_writer_wan_stats(
    writer, writer_baselines, sink, route_name, topic_name);
```

This includes per-matched-publication/per-matched-subscription status getters and participant-name attribution, not merely aggregate reader/writer totals.

### Measurements available at this layer

The current helpers collect:

- Writer-side pushed/pulled samples and fragment bytes, received NACKs/NACK fragments, sent heartbeats, and remote rejection counters.
- Reader-side received/duplicate samples, received heartbeats, sent NACKs, rejection counters, and uncommitted-sample gauges.
- Peer attribution through matched endpoint participant data.
- Per-peer and per-route/topic rollups.

Retain the cumulative-total baseline approach. Treat a new endpoint generation, rematch, or counter reset as a new baseline; preserve the current first-observation/rematch behavior rather than reporting a historical total as one interval's traffic. Remove retired baselines and avoid negative/underflowed deltas.

Keep one owner of polling state per endpoint. Do not rely on middleware `*_change` fields when other code may read/reset status change state.

### Ownership and safe aggregation

Installed header warnings are explicit: the returned DDS handles are usable only while the associated Input/Output remains enabled, and must be invalidated before disable. They are borrowed access to service-owned entities, not permission to close/delete those entities.

Recommended flow:

1. Each application-route processor acquires/uses native endpoint handles within its own lifecycle.
2. Its periodic callback computes owned per-peer/per-topic interval records.
3. It submits **copied numbers, strings, identities, and timestamps** to a bounded, synchronized node-local accumulator.
4. An infrastructure processor or management publisher emits the aggregate ACT telemetry.
5. Port disable removes the contribution and invalidates handles before the service destroys the endpoint.

Do not share `Route`, `Input`, `Output`, loaned samples, or live endpoint wrappers with another processor or a central polling thread. The Processor API forbids passing Route/port objects between processors. Copy values instead.

Although participant traversal and built-in-reader lookup compile, **do not consume service-owned built-in reader samples or replace their listeners without a separate support/runtime investigation**. Prefer matched/discovered-entity lookup APIs for attribution; competing discovery consumption could interfere with the service.

### Metrics that remain distinct

| Measurement | Authority |
|---|---|
| Service/route throughput and processing latency | Routing Service monitoring |
| DDS endpoint repair, duplicate, heartbeat, rejection, and backlog behavior | Native DDS protocol-status polling |
| Probe application-acknowledgement RTT | Dedicated DDS probe logic |
| WAN source-interface TX to destination-interface RX delivery | Existing passive RTPS capture and wire-delivery aggregator |
| Application-level consumption, if separately needed | Existing application/RouterHealth audit stream; not the current WAN Delivery metric |
| RTPS/UDP packet totals and discovery/data split | Traffic monitor / bounded packet capture |
| EMANE transmitted/received/dropped traffic | EMANE instrumentation |

The monitoring manual defines `bytes_per_sec` as serialized sample bytes **excluding RTPS/UDP protocol headers**. It must not be relabeled as WAN wire bandwidth.

A successful local `Output::write` does not establish WAN receipt. The current dashboard verifies **WAN source-interface TX to destination-interface RX** by matching domain, physical writer GUID, RTPS sequence, source IP, and destination IP in passive captures. It reconstructs IP/RTPS fragments and collapses duplicate/repair copies per destination node.

Preserve the existing [RTPS parser](../../debug/scripts/rtps_delivery.py), [wire-delivery aggregator](../../gui/mesh_dashboard/server/wire_delivery.py), and capture-coverage checks in the [dashboard bridge](../../gui/mesh_dashboard/server/mesh_bridge.py). Routing Service's writers will have their own physical GUIDs; discovered topic/reliability metadata must be captured for those endpoints rather than retaining old writer identities or inferring reliability from topic names.

The metric establishes complete WAN interface receipt, not DDS-reader acceptance or application consumption. Incomplete capture coverage must continue to produce Unknown rather than apparent loss. The separate application audit path may remain useful, but **processor-level send/receive audit hooks are not a replacement gap for the current Delivery view**.

### Network capture versus participant discovery logging

A follow-up RTI AI consultation, the [Network Capture manual][network-capture], and installed `rti/util/network_capture.hpp` establish the following:

- `rti::util::network_capture::enable()` must run before creating the participants to be captured.
- Capture can subsequently start/stop/pause/resume globally or for a selected local participant.
- The utility records participant traffic to pcap-based files, including RTPS discovery and shared-memory traffic. It does not capture all network-interface traffic.
- The controlling calls run inside the process containing those participants. An external utility process cannot attach to another process's participants through this API.
- An embedded Routing Service host can initialize capture before constructing/starting the service. A processor callback that first enables capture after its route participants already exist is too late.
- No documented standard Routing Service 7.7.0 XML/administration switch for this initialization was established. For standalone Routing Service, bounded interface capture is the simpler existing option.
- Capture control must be serialized, stopped before final shutdown, and disabled after participant destruction. It is a debug facility with potential performance/storage overhead; impose an owned duration/storage policy.

These are documented capabilities and header findings, not a successful Routing Service capture run.

For compact logs such as "participant discovered", "participant lost", GUID, name, and visible endpoints, use **discovery metadata**, not pcap as the primary event database. Matched/discovered-entity lookups or a separately owned discovery observer can supply that view. The observer only sees entities allowed by its discovery configuration; it is not automatically a global mesh roster.

Do not take discovery samples from, or replace listeners on, service-owned built-in readers without a verified extension contract. Packet capture remains valuable for explaining *why* discovery failed or what RTPS traffic was exchanged, but neither capture nor discovery alone replaces the ACT heartbeat's ALIVE/STALE/DEAD policy.

## 7. The forwarding caveat: this is not a passive observer plugin

The [Controlling Data chapter][processing] explicitly describes basic forwarding as the built-in default processor. Installing a custom processor gives the plugin responsibility for forwarding.

Therefore, attaching a timer-only `NoOpProcessor` to an application route would stop normal forwarding. A telemetry-enabled application processor must explicitly take/read and write samples.

For application forwarding:

- Use the processor's Input/Output APIs, including the metadata-capable `write(data, info)` overload.
- Do not discard invalid `SampleInfo` indiscriminately if lifecycle mirroring is part of the contract.
- Do not bypass output transformations or adapter behavior by replacing normal `output.write(...)` calls with native DDS writes merely to collect statistics.
- Test the interaction with `propagate_dispose`, `propagate_unregister`, original timestamp, and original identity settings. Passing SampleInfo is necessary to retain relevant metadata, but is **not by itself a proof of complete semantic equivalence**.
- Validate serialization/type compatibility, filtering, keyed instances, late joiners, backpressure, and errors.

No public built-in-forwarding delegate was established by this investigation. Do not rely on internal `detail::ProcessorForwarder` code as a supported delegate.

This is still substantially smaller than owning the whole route engine, but it means the migration is not "add telemetry callbacks and leave everything else untouched."

## 8. Recommended replacement structure

```text
Per-node gateway
|
+-- RTI Routing Service
|   +-- Existing LAN/WAN topology and external ACT QoS
|   +-- Explicit TopicRoutes / filtered AutoTopicRoutes
|   +-- Application processors: forwarding + endpoint-local measurements
|   +-- ACT infrastructure route: health/admin/probe/telemetry ports
|
+-- ACT management and compatibility layer
|   +-- Role-aware configuration translation and resource-name mapping
|   +-- Standard Routing Service administration for service-owned changes
|   +-- Standard monitoring subscription / dashboard resource mapping
|   +-- Compact ACT health summaries
|   +-- Optional legacy ActRouterCommand/status/journal compatibility
|   +-- Shared copied-record aggregation and presence policy
|
+-- Existing control/platform orchestration
|   +-- Team/identity policy, desired membership, and persistence
|   +-- Partition-update requests and restart reconciliation
|
+-- Existing passive RTPS delivery verification, traffic monitor, EMANE, dashboard
+-- Optional separate application audit
```

### Standalone versus embedded

**Start the proof-of-concept with standalone Routing Service plus a processor shared library**, using an isolated harness-owned configuration. This exercises the normal supported deployment and limits lifecycle changes.

For the eventual single-node gateway, an **embedded service plus a thin ACT host** is attractive: it can retain a single owned process lifecycle, shared copied telemetry, and the current command/status entry points.

Embedding is documented, but does not turn the processor into an unrestricted controller or establish an API equivalent to every remote-administration operation. Choose the administration interface after proving resource updates and acknowledgement behavior. Avoid rebuilding the current entity factory underneath the embedded service.

### Code worth preserving or extracting

- [WanStatsPoll](../../router/src/core/WanStatsPoll.hpp) and [LinkStatsSink](../../router/src/core/LinkStatsSink.hpp): endpoint polling and metric representations.
- Presence/mesh policy from [PresenceMonitor](../../router/src/core/PresenceMonitor.hpp), adapted to service-owned ports.
- ACT command/idempotency/revision semantics from [RouterController](../../router/src/core/RouterController.hpp), without retaining its DDS entity-construction engine wholesale.
- [Canonical IDL](../../harness_v2/datamodel/ActTypes.idl), external [QoS profiles](../../harness_v2/qos/act_qos_profiles.xml), and the existing audit/dashboard contracts.

Do not reuse whole classes blindly. Current constructors, wait-set ownership, listeners, and shutdown methods assume the custom router owns DDS entities; those assumptions must be removed at the processor boundary.

## 9. High-risk compatibility details

### Partition and Session scope

The current route runtime owns a Publisher/Subscriber per build. Routing Service creates Session-level Publisher/Subscriber entities for participating DDS connections.

A Session partition update can therefore affect multiple routes. Partition-isolated Sessions, or another explicitly verified configuration arrangement, are needed for independently retargetable route families.

Participant partitions are a separate matter: they constrain discovery scope. Preserve platform identity membership, protected control wildcard membership, team scoping, and the separate `NAVIGATION` path. Do not flatten all traffic into a globally visible default partition.

The administration manual permits mutable participant QoS updates, but exact live participant-partition behavior remains a gate. If a change requires restart/recreation, report that interruption explicitly rather than silently changing the current UX.

### Original ACT administration path: reuse it

Follow-up source inspection found the original [remote administration client](../../references/legacy_harness/tools/remote_admin/src/remote_admin.cxx). Its `send_team_update` submits a ServiceAdmin `UPDATE_ACTION` targeting the platform service's `dr` / `team_wan` participant, with an XML participant-QoS partition update. It waits for a reply and checks the return code. The same client enables/disables the detail-status Session through a state update.

**Use Routing Service administration for these service-owned operations, not custom processor QoS setters.** A separate ACT command protocol is needed only if preserving that external interface is a migration requirement; it is not inherently necessary for a Routing Service deployment.

The original request supplies one partition element, whereas the current router preserves node identity and supports multiple team/tap entries. Adapt the administration client to submit the intended complete protected partition set and verify the target service's resource paths. This is direct source evidence of the original design, not a live validation against the current 7.7.0 topology.

**Ownership clarification:** team assignment logic, identity-membership rules, desired-state persistence, and restart reconciliation remain in the control/platform orchestration layer. Routing Service applies the requested partition QoS and reports the administration result; neither it nor a processor should become a second team-state authority. Preserving this orchestration behavior is an integration requirement, not a missing team-management feature in Routing Service.

### Refined gap scope after adopting standard administration and monitoring

The custom management layer need not reproduce all existing commands, status topics, deduplication, and journal state machines. Those become compatibility requirements only if existing clients must retain that interface.

Use standard Routing Service monitoring on the LAN for detailed service/route state, rates, and processing latency, with readers for RTI's monitoring types. Detailed monitoring can be debug-enabled. Keep compact WAN presence active independently. Use custom protocol telemetry only for measurements absent from standard monitoring.

The core remaining custom work is:

1. Endpoint-local protocol collection and safe copied-record aggregation.
2. Compact ACT presence/mesh semantics and selected health summaries.
3. Forwarding logic on processor-equipped routes, with the approved metadata/lifecycle contract.
4. Wiring existing orchestration partition requests to administration and preserving independently retargetable Session boundaries; team policy and persistence stay outside the router.
5. Dashboard mapping for RTI monitoring types and preserving the existing external RTPS wire-delivery verification path; no new processor audit hooks are required for WAN Delivery.
6. Optional application-ack probe RTT, discovery-event logging, and in-process capture integration.

Exact reproduction of the current auto-QoS derivation is also optional if the migration deliberately standardizes on explicit external profiles. Such changes require explicit agreement and application compatibility tests, rather than being silently treated as equivalent.

### Command truthfulness and revisioning

A Routing Service administration reply is not automatically an ACT command acknowledgement. The compatibility layer must distinguish request acceptance from observed application of the requested state.

Preserve:

- Target node/router validation and command-ID deduplication.
- Rejection of protected-partition removal.
- Revision changes only for externally visible state transitions.
- Match/QoS counters without revision inflation from periodic telemetry.
- Journal causality and explicit failure reasons.
- Error reporting and rollback/reconciliation for partial service updates.

### Presence and identity

Use the existing health type and authoritative presence transitions, not process uptime or a stale cached health summary.

Maintain the current 8 s STALE deadline and 12 s DEAD liveliness lease unless deliberately changed in both external QoS and policy. Retain independent LAN mesh refresh, dead-peer retention, bounded peer lists, and transitive-edge aging.

Verify that Routing Service participant naming can preserve the externally consumed router identity and `act.router` role marker. Configure loop-prevention grouping per node; a common group across all nodes would suppress intended router-to-router discovery/communication.

### Callback execution and lifecycle

Callbacks for one Route are serialized; different Routes may run concurrently through Session thread pools. Shared ACT state therefore still needs synchronization.

Keep callbacks bounded. Do not synchronously disable/reconfigure the owning Route from inside its callback. Queue control requests to the management layer, avoid lock inversion with service administration, and expose telemetry backlog/errors.

## 10. Proof-of-concept and acceptance gates

These are proposed acceptance criteria, not measured results.

| Stage | Exercise | Required outcome |
|---|---|---|
| 1. Smallest routing replacement | One isolated control/platform pair; targeted command, return status, and one broadcast route. | Existing route tests pass using the new engine; one authoritative path per flow, no duplicate forwarding. |
| 2. Custom output | ACT infrastructure processor with configured types and periodic outputs. | Heartbeat/status samples reach a test reader without application publishers; no application-route/type dependency. |
| 3. Endpoint metrics | Poll actual WAN ports and map matched peers. | All expected peers are attributed; nominal/repair counters are observable; first baseline and disable/re-enable cause no negative or historical-total spikes. |
| 4. Forwarding semantics | Built-in route versus custom processor, then keyed write/dispose/unregister and late joiner. | Approved application contract holds; differences are documented before rollout. |
| 5. Administrative parity | Enable/disable, repeat command ID, route partition change, team membership, protected-entry removal. | Existing ACT acknowledgements/status remain compatible; rejected mutations do not leak partial state; unrelated routes remain unchanged. |
| 6. Presence and wire verification | Peer silence/rejoin; independent mesh timer; Routing Service endpoint discovery and WAN captures. | Correct ALIVE/STALE/DEAD sequence; heartbeat/mesh cadence within approved tolerance; captured sends/complete receipts join under the new writer identities. |
| 7. Impairment and reset | Owned containerized EMANE lifecycle; nominal, directional impairment, bidirectional impairment, reset. | Selected path changes as expected; all expected peers recover; complete reliable flows recover after the grace/window interval. |
| 8. Performance and soak | Same payloads, route counts, QoS, offered load, and capture filters for both engines. | Meet agreed latency/throughput/CPU/memory targets; repeated route flaps show no retained endpoint handles or growing baselines. |

Concrete proposed checks:

- **Nominal reliable WAN delivery:** 100% of unique captured source sends have complete destination-interface receipts after the observation grace period and drain, with valid capture coverage at both ends.
- **Wire-verification semantics:** preserve physical-writer/sequence/address matching, fragment completion, per-node deduplication, and discovery-derived reliability groups. Preserve 15 s observation grace for Reliable and 2 s for other groups; missing discovery remains Unknown QoS. Preserve request-confirmed retry/recovery measurement and Unknown results when capture coverage is incomplete.
- **Lifecycle stress:** at least 100 route disable/re-enable cycles with zero stale-handle access and no monotonically growing registry/baseline count after each drain.
- **Partition isolation:** zero deliveries to forbidden team recipients and zero unintended changes to unrelated route partitions.
- **Performance:** agree numerical hop-latency, CPU, memory, and throughput budgets before benchmarking; no performance advantage is claimed here.

Adapt the existing [router e2e tests](../../router/test_e2e/) to select the gateway backend rather than assuming equivalence from a Shapes demonstration. Prioritize control-command, full control/platform, admin-command, team/mesh partition, same-node-ignore, presence, link-statistics, and RTPS wire-delivery coverage.

For runtime work, use isolated domains no higher than 232, `/tmp` transient state, UDPv4-only single-host rigs, and the owned harness lifecycle. Do not run replacement tests against a live mesh or manually swap one running router.

## 11. Deployment and commercial checks

Before choosing this architecture for production:

- Confirm Routing Service runtime and deployment/redistribution entitlements for the intended machines/containers.
- Confirm the SDK, shared libraries, processor ABI, target compiler architecture, and service version are matched.
- Confirm whether the current image includes all needed runtime components rather than assuming header availability proves deployment readiness.
- Package plugin libraries and externally authored XML/QoS/types predictably.
- Preserve readable license mounts and the existing lifecycle's error/log paths.
- Keep administration reachable only through the intended management topology; enabling remote administration is a deployment decision, not a default assumption.
- Check the exact service patch level and release notes before testing type-routing, filtering, and custom processors.

No commercial entitlement or production-support guarantee was established by the AI consultation or API probe.

## 12. Verification appendix

The isolated probe:

- Derived a processor from `rti::routing::processor::NoOpProcessor`.
- Overrode `on_periodic_action(Route&)`.
- Obtained configured DynamicData inputs/outputs and their native DDS endpoints.
- Traversed Subscriber/Publisher to DomainParticipant.
- Used discovered-participant enumeration and built-in-reader lookup.
- Instantiated both existing WAN polling helpers.
- Called native writer liveliness assertion.
- Created/wrote a configured custom output sample and compiled `route.period(...)`.

Compilation used:

```text
g++ -std=c++11 -DRTI_UNIX -DRTI_LINUX -DRTI_64BIT -fPIC
    -I$NDDSHOME/include -I$NDDSHOME/include/ndds
    -I$NDDSHOME/include/ndds/hpp -I/workspace/router/src/core
    -c processor_api_probe.cxx
```

The resulting object linked into a shared library with `--no-undefined` against `rtiroutingservice`, `rtirsinfrastructure`, `nddscpp2`, `nddsc`, and `nddscore`, plus the Linux system libraries.

**Result: PASS**, against `/opt/rti.com/rti_connext_dds-7.7.0` in `connext:7.7.0`. The probe did not instantiate a service or invoke DDS at runtime, and the resulting library was not loaded as a plugin.

RTI AI's answers were treated as leads, not sole authority. Native endpoint access, participant traversal, and timer naming were resolved against installed headers/build output. Administration and default-forwarding claims were checked against the manual. Unproven runtime behavior is labeled above.

## 13. Sources

### Official RTI references

1. [Routing Service manual, supplied entry point][manual].
2. [Software Development Kit: Library, Adapter, Processor, and Transformation APIs][sdk].
3. [Processor API architecture, threading, constraints, and development requirements][processor-api].
4. [Input.hpp source: native DDS reader accessor and lifetime][input-api].
5. [Output.hpp source: native DDS writer accessor and lifetime][output-api].
6. [Controlling Data: built-in forwarding and custom processors][processing].
7. [Configuration: routes, auto routes, creation modes, types, timers, lifecycle, filters, QoS, and groups][configuration].
8. [Remote Administration: resource operations and mutable/immutable updates][administration].
9. [Monitoring: service metrics and serialized-sample byte definitions][monitoring].
10. [Shapes Processor tutorial, supplied example][tutorial].
11. [Shapes Processor public example README][shapes].
12. [Routing Service usage and library deployment][usage].
13. [Connext Network Capture lifecycle and shared-memory support][network-capture].

### Local implementation evidence

The links in Sections 3, 6, and 8 identify the principal source files. The installed evidence came from `include/rti/routing/processor/{Input,Output,Route,Processor}.hpp`, Core DDS headers, `resource/schema/rti_routing_service.xsd`, and the successful isolated compile/link.

**Bottom line:** proceed with a bounded replacement spike. Custom topics and endpoint protocol collection are viable processor responsibilities. Retain a narrow ACT control/compatibility layer, and make forwarding semantics, partition scope, and runtime parity the go/no-go gates.

[manual]: https://community.rti.com/static/documentation/connext-dds/current/doc/manuals/connext_dds_professional/services/routing_service/index.html
[sdk]: https://community.rti.com/static/documentation/connext-dds/current/doc/manuals/connext_dds_professional/services/routing_service/sdk.html
[processor-api]: https://community.rti.com/static/documentation/connext-dds/current/doc/api/routing_service/api_cpp/group__RTI__RoutingServiceProcessorModule.html
[input-api]: https://community.rti.com/static/documentation/connext-dds/current/doc/api/routing_service/api_cpp/Input_8hpp_source.html
[output-api]: https://community.rti.com/static/documentation/connext-dds/current/doc/api/routing_service/api_cpp/Output_8hpp_source.html
[processing]: https://community.rti.com/static/documentation/connext-dds/current/doc/manuals/connext_dds_professional/services/routing_service/controlling_data.html
[configuration]: https://community.rti.com/static/documentation/connext-dds/current/doc/manuals/connext_dds_professional/services/routing_service/configuration.html
[administration]: https://community.rti.com/static/documentation/connext-dds/current/doc/manuals/connext_dds_professional/services/routing_service/remote_admin.html
[monitoring]: https://community.rti.com/static/documentation/connext-dds/current/doc/manuals/connext_dds_professional/services/routing_service/monitoring.html
[tutorial]: https://community.rti.com/static/documentation/connext-dds/current/doc/manuals/connext_dds_professional/services/routing_service/tutorials.html#section-tutorials-exampleshapesprocessor
[shapes]: https://github.com/rticommunity/rticonnextdds-examples/tree/master/examples/routing_service/shapes_processor
[usage]: https://community.rti.com/static/documentation/connext-dds/current/doc/manuals/connext_dds_professional/services/routing_service/usage.html
[network-capture]: https://community.rti.com/static/documentation/connext-dds/current/doc/manuals/connext_dds_professional/users_manual/users_manual/NetworkCapture.htm
