# Routing Service processor: architecture and development plan

Date: 2026-11-14.

**Latest completion work (2026-11-16):** follow the
[processor completion and acceptance plan](router-processor-completion-plan.md)
for typed unions, native LAN auto-QoS, independent topic activation, retained
monitoring and the unchanged five-mission gate. The static all-in-one route
ownership described below is the implemented baseline, not a decision to
discard the subsequent auto-QoS work. Final shared WAN ownership is an explicit
gate in the completion plan; the isolated spike is not production integration.

Status: **current architecture and agreed development direction**.
The fixed-ACT XML-only processor is implemented and reference-mesh-qualified.
Platform mission-state receive filtering is implemented and reference-mesh-qualified
as of 2026-11-16. The retained six-platform mesh uses the new topic names and
Init/ActiveMission policy. Exact evidence and remaining limits are in
[migration checkpoint 3.52](two-topic-wan-migration-plan.md#352-platform-mission-cfts-deployed-and-qualified-2026-11-16).

The subsequent [review fixes](two-topic-wan-migration-plan.md#353-processor-review-fixes-qualified-not-deployed-2026-11-16)
are implemented and isolated-test-qualified, but are **not deployed** to the
retained mesh. A future coordinated deployment must regenerate prepared XML
for the stricter LAN participant identity checks.

This document consolidates the current conversation. It is the entry point for
processor development; linked documents provide implementation details and
historical evidence rather than a second, competing architecture.

## 1. Decisions and scope

| Area | Current decision |
| --- | --- |
| Runtime | RTI Routing Service owns static DDS endpoints; one processor performs routing/conversion and application-specific policy |
| Deployment | Prepared native XML, explicit type/union definitions, bindings and manually authored QoS |
| Payload | A union branch carries the complete original DynamicData value; restore the same original topic/type |
| WAN topics | Exactly Event, State and Periodic, with one shared writer/reader pair per node per family |
| Scheduling | A binding's logical channel selects family and priority; it does not create an endpoint |
| Runtime selection | Change platform-side ContentFilteredTopic (CFT) interests, not routes or QoS |
| Selection authority | The platform's local mission state determines its receive filters |
| Whole-platform restart | Start in Init with minimum required C2 data; do not restore the previous mission filter selection |
| Outgoing reports | Preserve the approved always-on platform reporting behavior |
| Deferred setup | Discover/import types and generate the same prepared bundle later |

There is **no application request protocol**, RequestData/ReleaseData operation,
lease, requester registry or request replay. In this design, "requesting data"
means selecting it through the platform's reader CFT.

The latest platform-side decision supersedes earlier suggestions to make C2
drive mission-dependent receive selection. It does not require removing the
existing C2 readers or their audience filters. C2's current receive behavior
remains unchanged.

## 2. System architecture

### Application topic naming

The requested application names are **Mission**, **Team** and **PlatformStatus**.
The confirmed migration mapping is:

| LAN topic | Direction | Former LAN topic | Purpose |
| --- | --- | --- | --- |
| Mission | C2 to platform | ControlCommand | Mission instructions and control |
| Team | C2 to platform | ActTeamAssignment | Team assignment |
| PlatformStatus | Platform to C2 and active fleet peers | PlatformInitStatus | Platform status and collision-avoidance positions, not an Init-only report |
| PlatformMissionStatus | Platform to C2 and selected same-team mission peers | PlatformMissionStatus | Mission progress/state reports; no additional rename agreed |
| ContactReport | Platform to C2 and selected same-team mission peers | ContactReport | Contact reports; no additional rename agreed |

Platform startup receive selection is Mission and Team, plus required internal
traffic. Active missions additionally select PlatformMissionStatus and
ContactReport from other platforms in the same team, and fleet-wide PlatformStatus
positions for collision avoidance. Keep C2 receiving those
reports as before; extend static source audiences/platform receive bindings as
needed, rather than relaying the reports back through C2.
These names do not replace Event/State/Periodic or create extra WAN
endpoints. Active sources, configurations and tests now use the new topic names.
Payload type names remain `control_command`, `TeamAssignment` and
`platform_init_status`; this is not a payload redesign. Rebuild/redeploy the
matching configuration and participants together.

### 2.1 Data path

```text
C2 application
  original LAN topic + complete original type
       |
       v
C2 Routing Service static LAN input
       |
       v
Processor: validate binding, copy whole value, stamp WAN metadata
       |
       v
Shared Event / State / Periodic writer
       |
       | existing DDS transport and explicit family QoS
       v
Platform's existing shared WAN reader + CFT
       |
       v
Processor: validate envelope, decode complete original value
       |
       v
Static original-topic LAN writer -> platform application

Platform local mission state
       |
       v
State-to-receive-profile policy
       |
       v
Update platform CFT interests; do not rebuild the data path
```

The platform-to-C2 direction uses the same binding/codec/shared-endpoint
architecture. Mission-dependent platform receive selection does not switch off
the platform's local report producers or its outgoing report bindings.

### 2.2 Ownership

| Component | Responsibility |
| --- | --- |
| Routing Service | Create/own configured participants, static input/output endpoints and processor lifecycle |
| [GatewayModel](../router/routing_service/GatewayModel.cxx) | Validate deployment properties, bindings, channel/identity rules and derived interests |
| [GatewayProcessor](../router/routing_service/GatewayProcessor.cxx) | Consume input loans, pack/unpack values, forward native lifecycle operations and account for results |
| [WanCodec](../router/src/core/WanCodec.cxx) | Whole-value envelope conversion, validated catalog and key translation |
| [WanInterests](../router/src/core/WanInterests.cxx) | Existing audience/address/team predicates and outgoing metadata |
| [GatewayMonitor](../router/routing_service/GatewayMonitor.cxx) | Service-owned monitoring, production presence/RTT/protocol statistics and current team-filter updates |
| Platform mission controller | Own the local mission state; it is not replaced by router policy |
| New receive-policy logic | Translate authoritative local state into validated platform CFT interests on the serialized processor path |

Prefer integrating the new receive policy into the processor's existing
processing/update path. Do not add a second forwarding engine, WAN request
service or independent filter-control loop.

## 3. Type, binding and identity contract

Both ends receive the matching frozen type/binding contract before startup.
The union carries the complete original structure, including collections, keys,
timestamps and audit fields. There is no selected-field mapper, command-payload
redesign or binary fallback in this scope.

The WAN instance key remains:

```text
source_id + original_topic + instance_id
```

Source identity is the configured originating system/node. Instance identity
comes from the configured existing string field, with the current catalog's
validation and key-recovery rules. Preserve the approved authoritative source
restoration, including ContactReport; do not substitute the receiving node.
Kind remains the union discriminator, not a duplicate envelope field.

| Physical topic | Current DDS type | Initial application classification |
| --- | --- | --- |
| Event | WanEvent | Commands, ACKs, assignments and internal configuration |
| State | WanEvent | ContactReport |
| Periodic | WanStatus | Reports and shared health/diagnostics |

These are the current schema-3 types, not new WanState/WanPeriodic definitions.
Incompatible type/schema changes require coordinated preparation, rebuild where
needed, and participant restart. Do not promise that DDS extensibility alone
makes arbitrary union/schema changes compatible.

**Implementation boundary:** the current codec remains fixed to the ACT catalog.
Explicit XML properties do not yet provide a generic arbitrary-type registry.
Generalizing that registry is separate processor work; discovery-driven setup
does not remove the need to distribute a compatible prepared WAN contract.

References:

- [Executable native XML grammar](../router/routing_service/README.md)
- [Whole-type XML binding plan](routing-service-xml-mapping-plan.md)
- [IDL source of truth](../harness_v2/datamodel/ActTypes.idl)
- [Prepared deployment and release split](routing-service-c2-platform-setup.md)

## 4. QoS and scheduling

All fixed QoS remains external XML. LAN reader/writer ports reference manually
authored profiles; WAN policy is shared by family, not inherited independently
from each original topic.

| Logical channel | Physical family | Current priority |
| --- | --- | --- |
| event_high | Event | 600 |
| periodic_high | Periodic | 500 |
| state_high | State | 400 |
| event_normal | Event | 300 |
| state_normal | State | 200 |
| periodic_normal | Periodic | 100 |

Retain the current Event reliable KEEP_ALL policy, State latest-per-instance
writer policy and Periodic best-effort KEEP_LAST(1) policy. Exact policies,
resource settings and shared flow-controller configuration belong to
[QoS XML](../harness_v2/qos/act_qos_profiles.xml), with rationale in the
[three-topic plan](routing-service-three-topic-plan.md).

Mission state changes **selection**, not family, priority, publish cadence,
bandwidth or QoS. Optional original-topic Periodic LAN input time filtering is
a separate, off-by-default configuration feature. It is not a request-rate
protocol or an initial-snapshot mechanism.

Automatic QoS is deferred pending qualification; see
[product gap LP-9](product-gaps.md).
Do not equate configured priority with bounded latency or starvation protection.

## 5. Platform mission-state receive policy

### 5.1 Startup and operation

1. Prepare the platform WAN readers with **Init CFT interests before application
   traffic can be accepted**. Avoid a broad-filter startup window.
2. Remain in the startup selection until the chosen local state source supplies
   a valid state under the agreed policy.
3. Map local mission-state changes to named receive profiles.
4. Compose profile membership with existing audience, address, team and
   source-suppression rules; never broaden those rules accidentally.
5. Apply updates to the existing CFTs and expose the applied profile and errors.

C2 does not issue a filter-profile command. The platform derives its selection
from its own mission state. Existing commands needed to initiate/control a
mission must remain available in the agreed minimum set.

Init names the startup receive profile, not the PlatformStatus topic.
PlatformStatus flows from platform to C2 and is not included in the
platform's default WAN receive interests. Local state observation is a separate
LAN input, not a subscription to another platform's reports.

Health/internal traffic keeps its required behavior on the shared endpoints.
An application-data selection must not accidentally disable the health branch.
Display receive profile separately from the existing ALL outgoing-report badge.

### 5.2 Confirmed receive policy

| Local state/profile | C2 application data | Additional peer data |
| --- | --- | --- |
| Init; IDLE, COMPLETE or CANCELLED | Mission and Team | None |
| ActiveMission; TRANSIT, ORBIT, RTB or HOLDING | Mission and Team | Fleet-wide PlatformStatus positions; PlatformMissionStatus and ContactReport from other platforms in the same team |

Required internal traffic is preserved in both profiles. There is one shared
active-Mission selection, not a different topic set for each active phase.
These two extra data classes come **directly from same-team peers**, not C2.

Use valid **local PlatformMissionStatus** as the state authority. Peer mission
reports delivered to the platform application must never drive its own receive
profile. Invalid updates are diagnosed without broadening the last successfully
applied selection. Startup remains Init before valid local state arrives.

The user specified mission status and contact reports; map those to the existing
PlatformMissionStatus and ContactReport contracts. No additional rename or
payload redesign is implied.

Implementation must preserve existing C2 delivery while adding the peer team
audience and static platform LAN outputs as needed. Team membership and mission
profile must jointly determine peer selection. An unassigned active platform
receives no team-scoped mission/contact data. It still receives fleet navigation
positions: live tests exposed the existing `WAITING_PEERS` collision-avoidance
dependency, and the user approved this active-only PlatformStatus exception.
Init remains Mission/Team plus internal traffic.

Ordering/staleness handling and coherent filter-update/rollback mechanics are
implemented and tested. Native fault injection, queue-draining semantics and
sustained rapid-transition stress remain separate gates.

The detailed increment is in
[platform mission-state CFT plan](platform-mission-cft-plan.md).

## 6. Transition, instance lifecycle and restart semantics

Filter changes are not a fleet-wide atomic barrier. Specify/test queued samples,
rapid state changes and interaction with team updates. One update must not
overwrite another policy dimension. Failed changes must not be reported as
success; rollback failure must be visible.

Changing a filter is not an application dispose operation. Preserve native
write/dispose/unregister semantics and input-loan key recovery. Do not add
synthetic lifecycle messages or dispose data merely because it is no longer
selected. Initial snapshot/replay behavior requires an explicit requirement and
qualification; do not assume filter expansion alone supplies historical state.

The [native lifecycle plan](two-topic-native-lifecycle-plan.md) provides reusable
principles and evidence. Its historical two-topic names/schema and counts are
not the current three-topic deployment specification. Multiple-producer
ownership and complete restart/recovery behavior remain separate gates.

The required restart case is **the whole platform system**, including its local
mission-state controller/producer:

```text
whole platform teardown/restart
    -> local Init state
    -> Init receive filters
    -> discovery/health recovery
    -> new valid local state transitions
```

Do not restore the previous mission filter selection. A router-only restart
while the controller remains in an active mission is a separate case requiring
an explicit decision; it is not evidence of whole-system Init reset.

## 7. Development sequence and acceptance gates

| Phase | Deliverable | Required exit evidence |
| --- | --- | --- |
| 1. Freeze receive policy | Mission/Team Init selection, active fleet navigation plus same-team mission/contact data, active/terminal mapping and local authority | Functional policy confirmed, including the user-approved navigation dependency |
| 2. Names, bindings and XML/model support | Three agreed topic renames; peer source audiences/static LAN outputs; validated profiles; Init predicates at startup; C2 reception unchanged | Catalog/configuration/audit/UI tests and native prepared-XML checks, including invalid configuration |
| 3. Local state integration | State observation and coherent CFT updates on the existing processor path | Actual included/excluded C2 delivery across transitions, without route or endpoint changes |
| 4. Transition/lifecycle hardening | Team/state composition, stale/repeated updates, failures and queued-sample behavior | Targeted native DDS tests, stable endpoint identity/counts and visible failures |
| 5. Whole-platform restart | Owned isolated platform-stack teardown/restart while C2 and another platform remain running | Init reset, no old profile replay, health recovery and subsequent mission transitions |
| 6. Reference-mesh deployment | Coordinated owned rollout and current browser workflows plus new receive-policy checks | All existing gates pass; actual filter/protocol evidence and nominal post-test mesh state |
| 7. Generalized processor release | Generic explicitly configured full-type registry and agreed resource/recovery limits | Non-ACT whole-type/key/filter qualification and measured lifecycle/load/recovery budgets |
| Later setup automation | Discovery/XML import, compatible union/binding bundle generation and qualified LAN QoS assistance | Same deployment contract; no competing runtime configuration authority |

The approved processor policy is now `act.receive_policy=mission`, defaulting
platform startup to Init. Explicit `static` preserves full-catalog acceptance and
minimal prepared-bundle tests. The custom backend remains the launcher default
and rollback option. Preserve always-on outgoing reports in processor mode.

Phases 1-3, 5 and 6 have executed evidence. Phase 4 has native state/team,
lifecycle, rollback-callback and model-replacement checks, but not a complete
queue/stress/native-fault gate. Phase 7 and later setup automation remain open.

The general registry track is not required to demonstrate this policy on ACT,
but is required before claiming the processor accepts arbitrary configured
application types. Runtime discovery/generation remains later work, not a
prerequisite for the prepared-XML implementation.

## 8. Verification and deployment discipline

Build and test against the supported Connext 7.7.0 environment. Start with
model/config tests, then actual DDS selection/transition tests in an owned private
network namespace. Test included/excluded data, not just changed filter text.
Check native reader/writer observations and protocol counters as well as LAN
delivery; do not claim sender-side bandwidth savings from a receiver-side drop.

Reuse:

- [Processor/monitor acceptance](../router/test_e2e/test_routing_service_mesh.py)
- [Processor contract acceptance](../router/test_e2e/test_routing_service.py)
- [Three-topic acceptance](../router/test_e2e/test_three_topic_routing_service.py)
- [Five live mission workflows](../router/test_e2e/test_mission_workflow.py)
- [Owned mesh lifecycle](../harness_v2/scripts/run_mesh.sh)

The recorded baseline is
[migration checkpoint 3.51](two-topic-wan-migration-plan.md#351-xml-only-processor-deployed-and-mesh-qualified-2026-11-14):
9 C++ targets, 40 processor checks, 237 related configuration/UI/control tests
plus 245 subtests, 8 legacy monitoring regressions and all 5 live browser
workflows passed. These establish the preceding reference implementation.
For the new policy, checkpoint 3.52 records 9 C++ targets, 70 native/configuration
tests plus 55 subtests, and 5/5 live browser workflows (375.89 s), with
full-platform restart and actual filter evidence.

Before live binary/type/configuration changes, use the owned teardown, rebuild,
prepare/validate and coordinated relaunch. Do not patch generated live XML or
manually restart an individual router. Whole-platform restart qualification
needs an owned isolated stack fixture, not an ad hoc repair of the live mesh.

After live tests, verify health, filters, delivery and reset experiment state.
Retain compact evidence and preserve the rollback backend.

## 9. Reference map and next action

| Reference | Use |
| --- | --- |
| [Processor README](../router/routing_service/README.md) | Implemented capabilities, exact property grammar and launch commands |
| [Three-topic plan](routing-service-three-topic-plan.md) | Family/QoS/priority rationale and broader release gates |
| [XML binding plan](routing-service-xml-mapping-plan.md) | Whole-type registry design and configuration validation |
| [Setup guide](routing-service-c2-platform-setup.md) | Prepared bundle, installation and v1/v2 split |
| [Platform CFT plan](platform-mission-cft-plan.md) | Detailed platform-local selection and restart increment |
| [Migration history](two-topic-wan-migration-plan.md) | Historical decisions, verified checkpoints and superseded approaches |
| [Product gaps](product-gaps.md) | Auto-QoS and other qualified/deferred product constraints |

**Next action:** settle the remaining queue/stress/native-fault acceptance and
generalized-registry/resource/recovery gates before a wider processor release.
Do not turn this policy into a request protocol or producer-mode controller.
