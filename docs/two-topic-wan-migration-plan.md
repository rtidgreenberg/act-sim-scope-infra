# Two-topic WAN migration plan

Date: 2026-11-08

Status: Migration draft, with the first SDK qualification checks completed in
§3.3 and the whole-structure DynamicData adapter spike completed in §3.4.
Production IDL, QoS, routing, simulator, and live-mesh migration are not
implemented. The isolated fixtures do not change the deployed contract.

Implementation investigation: 2026-11-08. Connext AI was consulted, then its
load-bearing claims were checked against the installed 7.7.0 generator/binding.
The roadmap below incorporates those results; it is not a completed migration.

## Draft in plain terms

Keep the existing applications and their LAN topics. Change the router's WAN
boundary: select a declared union branch from a mapping, copy the **whole existing
structure** into Event or Status, and restore that structure at the other end.
Use shared WAN endpoints, not one new WAN writer for every old topic.

The basic copy/filter/lifecycle mechanism is proven in §3.4. The work below turns
that proof into a production feature with controller ownership, live routing
updates, health, restart handling, diagnostics, and a tested rollout.
**Field mapping and redesigning command payloads are deferred.** This draft
does not authorize a live cutover or change running routers.

## 1. Target and boundaries

Use exactly two shared DDS topic names on the WAN, used by C2 and every platform:

- **Event**: a typed union of commands, acknowledgments, and discrete reports.
- **Status**: a typed union of latest-state reports, including RouterHealth.

There are no separate C2Event/C2Status or PlatformEvent/PlatformStatus WAN topics.
Node identity and role are sample fields, not topic-name suffixes. All
router-originated WAN status, including health and detailed diagnostics, belongs
in Status. Do not leave RouterHealth as a permanent third WAN topic.

This is a WAN contract change. Existing local application topics and LAN-only
router administration, mesh snapshots, journals, and link statistics can remain
behind adapters. Two topic names do not mean two endpoints: independent writers
or readers on the same topic may be needed to preserve QoS and lifecycle behavior.
Record their actual count rather than promising two writers/readers per boat.

Preserve the current C2/platform functions, routing audiences, mission workflow,
status-mode controls, presence, passive RTT, and delivery evidence. This is not
a simultaneous redesign of radio routing, discovery protocol, or status cadence.
Historical spike workloads and reports retain their original contracts.

For this first conversion, keep every union branch's existing IDL structure
unchanged, including command octets and existing keys. Reading key/audience/audit
fields to populate the envelope is necessary metadata handling, not a
field-by-field payload transformation. Do not make a field-mapping engine or
per-command-operation schema redesign a prerequisite.

Keep the existing UDPv4/unicast WAN discovery design and one WAN participant
per node. This conversion does not switch middleware or introduce WAN-to-WAN
forwarding. Continue generic same-topic routing for unaffected LAN routes.

The current source of truth is [ActTypes.idl](../harness_v2/datamodel/ActTypes.idl),
with routes in [control-platform.yaml](../router/config/control-platform.yaml)
and fixed QoS in [act_qos_profiles.xml](../harness_v2/qos/act_qos_profiles.xml).

## 2. Payload migration map

Union case names below are working names, not generated IDL.

| Current WAN message | Target | Union case / handling |
|---|---|---|
| ControlCommand | Event | Command: whole `control_command`; existing `command_type`/octet payload and execution semantics unchanged |
| ActTeamAssignment | Event | AssignTeam: whole `TeamAssignment`; preserve current broadcast to platforms |
| ActPlatformStatusMode | Event | SetStatusMode: whole `PlatformStatusMode`; preserve selection by `platform_node` |
| PlatformCommandAck | Event | CommandAck: whole `control_command_ack`; preserve current platform-to-control delivery and original destination |
| ContactReport | Event | ContactReport: whole `contact_report`; preserve update/lost and required lifecycle behavior |
| PlatformInitStatus | Status | PrimaryNavigation: whole `platform_init_status` |
| PlatformData | Status | EnvironmentalData: whole `platform_data`; preserve team scope and original key |
| PlatformMissionStatus | Status | Mission: whole `platform_mission_status` |
| PlatformWaypointStatus | Status | Waypoint: whole `platform_waypoint_status` |
| PlatformDetailStatus | Status | Detail: whole `platform_detail_status` |
| PlatformDebugStatus | Status | Debug: whole `platform_debug_status` |
| PlatformThrusterStatus | Status | Thruster: whole `platform_thruster_status` |
| PlatformPowerStatus | Status | Power: whole `platform_power_status` |
| ActRouterDiagnostics, when exported over WAN | Status | RouterDiagnostics: whole `RouterDiagnostics`; existing source-side debug gate |
| RouterHealth, from either role | Status | RouterHealth: whole `RouterHealth`; preserve compact health and passive RTT fields |

Do not replace typed variants with an opaque byte package merely to reduce
topic count. The union contains declared existing structures, not a catch-all
serialized blob. Keeping the existing octet member inside `control_command`
does not create such a catch-all: its decoding remains the existing application's
responsibility. Splitting commands into operation-specific structures is a later,
separately versioned change. Never reinterpret an acknowledgment as execution.

All platform status variants share Status. Keep their existing source-side
enable/disable behavior: primary and health remain available independently of
debug; mission reporting follows the current mission lifecycle; debug/detail
remain optional. Avoid publishing every union case on every tick.

Additional C2 status variants are not required to complete this migration.
C2's existing RouterHealth uses the same RouterHealth case as platforms.

## 3. Common envelope and independent instances

Use shared top-level fields for audience selection, identity, and auditing.
**Event/status kind is the union discriminator, not a second top-level field.**
Final IDL names, bounds, case values, and extensibility still require qualification.

| Field group | Purpose |
|---|---|
| Source node, source router where relevant, and role | Identify the origin and distinguish C2/platform flows without changing topic names |
| Union discriminator, inside the payload union | Identify and select the variant; do not duplicate it as an envelope kind |
| Scope, destination, and team identity as applicable | Support addressed, global, team, and C2-only audiences |
| Logical instance identity | Distinguish sensors, contacts, missions, or devices where the existing payload requires it |
| Original topic/mapping identity | Select an allowed inverse mapping without losing wildcard aliases; not an arbitrary remote topic-creation request |
| Membership version | Identify which assignment a team-scoped publication belongs to |
| Run/session identity, sequence, and timestamps | Prevent restart collisions and preserve delivery/freshness evidence |
| Schema version | Define compatibility and explicit rejection of unsupported contracts |

**Selected Status key:** `source_node + source_router + instance_id`. Make
`instance_id` variant-qualified: for example, `navigation/primary`,
`environmental/sensor-7`, and `health/router`. The user selected this design
after the 7.7 qualification. It preserves kind solely as the union discriminator.
The variant prefix is part of the stable instance identity, not a separately
selectable kind field. Application sources must use a documented stable
`source_router` value rather than alternate between empty and named identities.

Use one shared identity rule in encoders, decoders, lifecycle handling, and
audit consumers. Reject an instance prefix that disagrees with the discriminator
for valid data. Keep both the prefix mapping and union discriminator values
stable across schema versions. Do not key timestamps, sequences, team identity,
membership version, or an ever-changing payload.

Do **not** annotate the union member `@key`, or use
`switch (@key StatusKind)`. In this 7.7 install, the first incorporates changing
payload data into the key; the second is rejected by the generator. See §3.1.

With per-instance KEEP_LAST(1), navigation, debug, environmental sensors, and
health must retain independent latest values. Preserve invalid-data/key recovery,
dispose/unregister, and writer-loss handling where required. An absent optional
variant is not automatically a failed router or a lost message.

For invalid-data lifecycle samples, recover the outer keys, including the
variant-qualified instance ID; do not read an absent payload to discover kind.
Define cleanup for removed sensors/missions and source restart. A key that
includes every new mission/contact ID needs an explicit bounded retention policy.

Event command IDs and audit sequences are separate from Status latest-value
keys. Define event retention and deduplication without allowing a newer unrelated
command to overwrite a required earlier command.

**Draft identity rule for the whole-structure conversion:** qualify the instance
ID with variant + original topic + canonical original DDS key, as proven by the
spike. Keep the selected three outer Status keys. Propose the same outer tuple
for Event initially, with KEEP_ALL retaining required events rather than creating
a new instance for every command ID. Review Event's final resource/retention
contract before freezing this proposal.

Encode all actual original DDS key fields, with an unambiguous encoding and
documented bounds. Do not assume fields such as `sensor_id`, `thruster_id`, or
`contact_id` are keys merely because their names look like identities.
Preserve the existing local key contract; any device/contact-key redesign is a
separate behavior change. Physical publication GUIDs are ownership/restart facts,
not a replacement for stable logical keys.

The spike's string and instance-ID bounds are not production defaults. Derive
envelope bounds from supported node/router names, topic names, and complete
original keys so valid existing inputs are not newly rejected. Treat composite
and non-string keys as explicit codec work, not an implicit string conversion.

### 3.1 Verified 7.7 findings and remaining limits

Qualification used `connext:7.7.0`, rtiddsgen 4.7.0, and the installed
`rti.connextdds` binding in disposable `--network none` containers. Sources and
generated files were under `/tmp`; participant QoS came from the existing
external [bootstrap XML](../spikes/c2_peer_bootstrap/qos.xml), with UDPv4 loopback
and domain 201 inside the private network namespace. No running mesh was touched.

| Check | Observed result | Roadmap consequence |
|---|---|---|
| `union Payload switch (@key Kind)` | Generator rejects `@key is not supported` | A literal discriminator-only DDS key is unavailable through this syntax |
| Outer `@key Payload payload`, without discriminator annotation | Changing the active payload value changes the registered instance handle | Never key the whole payload for latest-state storage |
| Unkeyed payload, identical outer keys | Navigation and health have the same instance handle | Outer instance IDs must distinguish variants |
| Unkeyed payload, IDs `1/primary` and `2/primary` | Different variants have different handles; changed values within one variant keep the same handle; key recovery returns `2/primary` | Supports the selected variant-qualified identity design |
| `payload._d = 1` and `payload._d = 'PROBE_NAVIGATION'` | CFT creation and local sample selection succeed | Discriminator selection does not require a duplicate kind field |
| `payload._d = %0`, changed from `"1"` to `"2"` | Local reader switches from navigation to health | Initial local result; §3.3 also checks propagation to a remote UDP writer |
| Bare enum label `PROBE_NAVIGATION` | CFT creation fails with Invalid symbol | Use verified numeric parameters or quoted enum labels |
| `payload.navigation > 50`, or `payload._u.navigation > 50`, guarded by `_d` | Initial int32-branch reproduction fails for both MUTABLE and FINAL | Not a general unsupported-feature conclusion; §3.3 proves selection inside a production struct branch |
| Generated Modern C++ union | `_d()` and branch getters/setters; setters select the branch and inactive getters throw | Struct fields remain direct members, but union access uses branch methods |
| Python DynamicData union | Loan access through `loan.data`, with `discriminator_value` reporting the selected label | Verify actual generated bindings separately if choosing them instead of DynamicData |

These initial CFT tests used a local writer/reader. They are **not** WAN captures
and do not prove writer-side bandwidth suppression, remote update completion,
congested KEEP_LAST behavior, or full dispose/unregister delivery.
Key registration/recovery is not a substitute for those lifecycle tests.

The [7.7 Extensible Types guide](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/extensible_types_guide/extensible_types/XTypes_Intro.htm)
explicitly lists marking a union discriminator as a key as unsupported. Its
[SQL reference, §45.5.6](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/SQL_Filter_Expression_Notation.htm)
describes `_d` and `_u` paths, but that prose did not settle the initial
nested-branch failure. The follow-up in §3.3 found a working struct-branch
path without `_u`; qualify each intended final predicate/type. Do not generalize
the initial reproduction into a claim that union-content filtering is unsupported.

### 3.2 Optional per-session keys and disposal

If separate DDS instances per publishing session are desired, add stable
`session_id` to the outer key:
`source_node + source_router + session_id + instance_id`. The payload union
remains unkeyed. This is an optional extension, not an automatic change to the
selected key above.

A follow-up isolated 7.7 test registered otherwise identical health samples
under sessions A and B and obtained distinct instance handles. Recovering A's
outer keys, resolving its handle, and disposing that handle produced an
invalid-data `NOT_ALIVE_DISPOSED` notification for A on an unfiltered reader.
No union payload was needed to resolve the handle. The installed Python
`dispose_instance` overload takes an instance handle, not a DynamicData sample.
§3.3 also tests disposal delivery to a health-only CFT reader; it does not yet
cover every session/restart/unregister lifecycle combination.

Dispose each registered instance in the session; there is no single
session-wide disposal operation. Track the session's bounded instance set.
Disposal marks state rather than promising immediate history/resource removal:
define unregister and reader-cache purge policy separately in XML. A crashed
writer cannot perform its own cleanup, so receiver expiration/restart handling
must not depend solely on a final dispose. Per-session keys also create new
instances on restart; include that cost in resource-limit and cleanup tests.

### 3.3 Required check results (2026-11-08)

**Nine tests passed in two consecutive complete runs** through the owned
[qualification runner](../router/qualification/run_checks.sh). The final run
completed in 34.41 seconds. The latest compact result is
[two_topic_union_qualification.xml](../debug/test_reports/two_topic_union_qualification.xml).

The [tests](../router/qualification/test_two_topic_qualification.py) generate a
temporary [union fixture](../router/qualification/fixtures/two_topic_qualification.idl)
containing the existing production navigation, RouterHealth, command, and
acknowledgment structs. They create two DDS participants using the production
XML WAN/endpoint profiles, with physical topic names Event and Status. They are
SDK qualification, not tests of a migrated router or every final typed command.

| Required check | Evidence | Result |
|---|---|---|
| Independent variant/sensor instances | Changing navigation's value retains its handle; navigation, health, and a second sensor have distinct handles and three managed instances | Passed |
| Actual sender-side discriminator/audience filtering | 60 updates with an unwanted variant, destination, or team produce zero additional pushed application DATA samples; one selected update produces one and arrives | Passed |
| Remote CFT parameter update | Changing interest from navigation to health produces health delivery; 20 subsequent unwanted navigation updates produce zero additional pushed DATA | Passed for this pair |
| Selection inside a union's production struct branch | `payload._d = %0 AND payload.navigation.speed_knots > %1`: low-speed navigation and the inactive health branch produce zero pushed DATA; selected navigation arrives | Passed |
| Required events survive a backlog | Under 100% private-loopback packet loss, 20 alternating command/ack samples sharing one instance remain in KEEP_ALL writer history; after reset, all 20 are received and acknowledged | Passed |
| Health deadlines while unrelated Status continues | Four combinations cover ordinary/compatible application QoS and CFT-only/fixed-partition health endpoints; missing health reports its instance handle during five seconds of application traffic, then resumes delivery | Passed |
| Disposal through a health-only CFT | An outer-key-resolved health handle produces NOT_ALIVE_DISPOSED on the remote filtered reader | Passed |

The working branch syntax in this Python/XML-loaded rig is
`payload.navigation.speed_knots`, **not** `payload._u.navigation.speed_knots`.
The latter fails CFT creation with Invalid symbol. Numeric discriminator/filter
parameters and guarded inactive-branch behavior were checked at both delivery
and sender protocol-counter level.

The reliable event test creates a real unacknowledged-sample backlog by dropping
UDP packets, not by merely leaving the application reader unread. It then proves
repair delivers every event. It does not prove unlimited queue capacity, outage
tolerance, application execution, or final event deduplication rules.

Health data deadline detection works even with a compatible non-health writer
matched to the same reader. Automatic writer liveliness remains alive during
that data outage, as expected: **missing health data is not writer death**.
Retain this distinction in the PresenceMonitor migration. These tests do not
prove migrated presence/RTT code, kill/restart behavior, or final fleet scheduling.

Isolation: the runner uses `--network none`, UDPv4, domains 201 upward with a
232 ceiling, a read-only checkout, and temporary generated types/caches. NET_ADMIN
is granted only inside its private container for bounded loopback impairment,
which is reset in `finally`. Missing pytest is downloaded by a separate setup
container and installed offline in the test container. No live mesh was started.
Raw scratch files and owned containers are cleaned; only compact results remain.

Run:

```bash
bash router/qualification/run_checks.sh
```

The first four requested SDK behavior checks are complete. Still required during
implementation: all final variants/predicates, filter-resource and queue limits,
fleet/team transition rules, lifecycle/restart cases, migrated router/RTT and
operator/audit regressions, then EMANE delivery and wire-cost comparisons.

### 3.4 Whole-structure adapter results (2026-11-08)

The standalone [DynamicData adapter spike](../spikes/two_topic_union_adapter/README.md)
passed all checks in **two consecutive fresh builds/runs** using Connext 7.7.0
and production XML QoS. Its second full build/run took 8.68 seconds, not a
measured message-latency figure. Run it with:

```bash
bash spikes/two_topic_union_adapter/run.sh
```

This exercises actual original-topic DDS input, whole-structure packing into a
declared Event/Status union branch, WAN DDS delivery, whole-structure extraction,
and publication of the original topic/type. Six real UDPv4 participants span
private domains 201, 203, and 202. No field mapping, production-router changes,
or live mesh are involved.

| Check | Observed result |
|---|---|
| Full structure conversion | Navigation, RouterHealth, ControlCommand, command ack, and a wildcard navigation alias preserve every payload/envelope value |
| Nontrivial payloads | 8192 command octets, boundary-length strings, sixteen health team entries, and nested peer/route sequences compare equal after decoding |
| Shared WAN endpoints | Exactly Event/Status; one Event writer, separate application/health Status writers, and two WAN readers |
| One intended publication | Five original inputs produce exactly five valid WAN receives, five sender pushes, and five original-topic outputs |
| Mapping validation | Ten explicit rejection cases cover unmapped/ambiguous topics, schema/discriminator mismatches, inactive/unmapped branches, inconsistent keys, and bounds |
| Writer-side content/audience filtering | Twenty unwanted unions produce zero additional pushed DATA; the next selected union adds exactly one and is restored |
| Lifecycle through both adapters | Five dispose notifications, revival of each instance, and five NO_WRITERS notifications preserve original keys and stable handles; unregister removes every output binding |

The whole-copy setter and reverse getter are **runtime-proven**, not merely
compile-checked:
`payload.value<DynamicData>(branch, original)` and
`payload.value<DynamicData>(branch)`. This is not a zero-copy promise.

The prototype preserves `original_topic` as envelope metadata so a wildcard
mapping remains invertible. Its variant-qualified instance ID also contains
the length-prefixed original topic and original local string key, preventing
aliases from overwriting each other. The production C++ router currently uses
explicit topic names; wildcard selection is new spike logic, not an existing
production conversion facility.

Lifecycle handling recovers original and WAN keys without reading invalid
union payloads. The output adapter uses bindings established by earlier valid
samples; first-observed invalid states without a binding fail explicitly.
Restart reconstruction, multiple source writers per logical instance, bounded
resource policy, final composite-key encoding, and crash/session behavior remain
integration checks.

The final sender counter reports **21 pushed DDS samples: 11 valid publications
plus 10 lifecycle notifications**. Do not confuse that aggregate counter with
the eleven valid WAN deliveries. The compact evidence is
[two_topic_union_adapter.txt](../debug/test_reports/two_topic_union_adapter.txt).

This qualifies the proposed legacy-topic conversion boundary. It does **not**
complete production factory/controller wiring, loop guards for a deployed
bidirectional mesh, all final variants, dedicated health receivers/PresenceMonitor/RTT,
or radio-cost measurements. Typed command-operation conversion remains outside
this first whole-structure migration.

### 3.5 Target production architecture

```text
Existing application LAN topics
    -> discovered/mapped source bindings
    -> whole-structure encoder + original identity/audience metadata
    -> shared Event / Status WAN endpoint classes
    -> receiver interests / writer-side CFT suppression
    -> checked inverse mapping
    -> existing destination LAN topics

RouterHealth producer -> dedicated health class on Status
Status health reader -> existing presence/RTT logic -> existing LAN mesh summaries
```

Proposed responsibilities, not new files to create blindly:

1. **Mapping catalog and codec.** One validated catalog selects family, union
   branch, expected original type, original-key encoding, allowed destination
   topic, and endpoint class. Whole-structure pack/unpack is the only payload
   conversion. Existing configuration contains exact topic names; retain those
   initially. Add wildcard entries only where needed, with no first-match
   precedence and explicit rejection of overlaps/unknown types.
2. **Shared WAN ownership.** A process-scoped owner reuses the node's WAN
   participant and owns Event/Status Topics, Publishers/Subscribers, writers,
   readers/CFTs, physical match state, and protocol-counter registration.
   Key endpoint reuse by actual participant, family, XML QoS class, and any
   justified fixed partition class, not by legacy topic or route.
3. **Logical source/output bindings.** Keep controller-visible legacy route
   names, source enable gates, counters, and errors. A source binding acquires
   the shared endpoint; it does not own/close that writer. Destination bindings
   retain original LAN type/topic and LAN QoS. Shared conditions and source
   binding conditions have distinct owners.
4. **Routing-interest state.** Derive source audience metadata and receive
   CFT interests from enabled logical routes and authoritative team state.
   A publication may have more than one audience; account for the original
   C2 and global-navigation exports without emitting duplicate WAN samples.
5. **Lifecycle/ownership registry.** Track canonical logical instances,
   original keys, input publication ownership, decoder bindings, and restart
   sessions with bounded cleanup. Recover invalid-data keys separately from
   valid-data decoding. The spike's previously-seen binding cache is not a
   complete restart implementation.

Start with required-Event, application-Status, and dedicated health-Status
writer/reader classes. The production health reader must be separate from the
ordinary application decoder; do not copy the spike's common Status reader
unchanged. Additional endpoints require a documented QoS/cache/isolation reason.
Create only the classes actually used by that node's role. Two WAN topic names
are mandatory; three endpoint classes is a starting design, not a promised
final fleet-wide writer/reader count.

Keep shared ownership on the existing controller strand and use the dispatcher's
blocking detach barrier before releasing the final endpoint reference. Async
handlers must never retain a stale route generation or close another route's
shared writer.

For navigation's overlapping source routes, share an ingress subscription when
input participant, original topic/type, and reader behavior are identical.
Disabling its C2 export must not disable its still-active global export.
Combine the enabled audiences into one WAN publication. If genuinely different
input readers are required, reconcile their observations using preserved
publication/audit identity rather than assuming two reads are two logical sends.

Prevent loops by excluding this router's restored LAN writers from source
bindings and suppressing its own WAN publications before decode. Do not treat
different DDS participants as sufficient loop protection. Multiple permitted
LAN destinations must not create duplicate output writers/publications for
the same local target.

## 4. Audience selection and runtime changes

Use ContentFilteredTopics (CFTs) for sample selection. Keep the selection fields
outside union branches where they are genuinely shared. Variant selection uses
the discriminator. A qualified example is `payload._d = %0`, with `"1"` as the
parameter for the reproduction's navigation label; production values must come
from the agreed enum mapping. Representative audience and struct-branch
predicates passed §3.3; final combinations still need their own tests.
The following are audience rules, not fully
validated production SQL expressions:

- A platform receives ControlCommand/StatusMode events selected by the current
  destination rules. Acknowledgments retain the current platform-to-control
  route and destination metadata; any further narrowing requires baseline
  recipient evidence, not an assumed equivalence.
- TeamAssignment retains the current unfiltered control-to-platform broadcast.
  `platform_node` identifies the assignment subject; it must not silently become
  the only transport recipient. Addressed-only distribution or a new roster
  protocol is a later, separately approved change.
- C2 receives the platform status/report kinds it requires.
- Platforms receive global navigation and approved team status; unrelated teams
  and C2-only diagnostics are excluded.
- Preserve current health coverage as an explicit reader rule. Do not infer
  platform-to-platform health delivery just because every node uses Status.
- Preserve same-node suppression and prevent gateway loops. Relaying a sample
  must not rewrite its original audit identity.

On assignment, update the platform's reader filter parameters and its outgoing
team tags. Define a bounded transition protocol with command IDs, membership
versions, applied/rejected acknowledgments, and timeouts. A local filter update
or transport acknowledgment is not proof that every remote writer has updated.

For the first conversion, use the existing internal team-control command path.
Once that command is applied, the router updates its local team state; subsequent
team-scoped samples are stamped from that state, and its receive CFT interests
are updated through the controller. Do not introduce a fleet-wide atomic
switchover barrier for periodic status. Allow eventual convergence: queued samples
can retain old metadata, old-team delivery and new-team gaps can briefly occur,
and accepted/in-flight samples need not be retroactively withdrawn. Required
commands/acknowledgments retain their separate delivery policy. Global and
addressed traffic do not become team-scoped merely because membership changed.

Choose and document how old-team cached/in-flight samples are handled. Test
late packets and rapid repeated assignments. If all platforms need a shared
roster, explicitly provide that state; addressed AssignTeam events do not
automatically distribute every assignment to every boat.

Normalize audience metadata without rewriting the original payload. The envelope
must express simultaneous C2/global exports and any supported set of team
memberships. Do not assume a single `team_id` is sufficient merely because the
spike used TEAM_A. Qualify the final multi-audience/team predicates on installed
7.7 before committing their schema; preserve any supported multi-team behavior.

Writer-side filtering is a mandatory bandwidth check: verify supported predicates,
sufficient remote-reader-filter resources, and compatible delivery transport
settings. Receiving then discarding an unwanted sample does not satisfy it.
In particular, discriminator filters are not automatically key-only filters:
the discriminator is not a DDS key in the selected contract. Budget evaluation
and filter resources for the actual reader count, not only the two topic names.
Retain dynamic endpoint discovery for filter-update propagation; do not switch
to static endpoint discovery as part of this change.

For selection inside struct branches, start with the proven §3.3 path and test
each final field/type. Do not silently replace sender filtering with receiver-side
discard or duplicate payload fields into an envelope merely to bypass a failure.

Coarse static pub/sub partitions remain an option for separating endpoint classes,
especially health from application traffic. They are not the dynamic team
selection mechanism for mixed variants in one writer. Filters and partitions
are not security authorization.

## 5. RouterHealth becomes a Status case

This is a transport/type change, not removal of health functionality. Migrate
[PresenceMonitor](../router/src/core/PresenceMonitor.hpp) and its implementation
to publish and consume the RouterHealth branch of Status. Continue producing
LAN mesh summaries and collecting WAN protocol statistics.

Preserve the invariants in the
[passive RTT plan](cpp_router/router-health-passive-rtt-plan.md):

- Health remains periodic even with no mission/application traffic and debug off.
- Preserve heartbeat session/sequence and peer acknowledgment session/sequence.
- RTT uses local monotonic durations and the peer's receipt-to-publication age;
  no clock synchronization, immediate echo, or new probe topic is introduced.
- Preserve direct-writer/session attribution, stale-session rejection, restart
  recovery, bounds, and freshness/validity reporting.
- Capture timing at the actual health publication boundary. Wrapping or queuing
  health must not invalidate the remote wait subtraction.
- Data from other Status variants must not refresh the health instance deadline
  or hide loss of the health writer.

Start qualification with a dedicated health writer/reader on the shared Status
topic, retaining the effective health XML profile. Test whether a health-only
CFT is enough for correct endpoint matching, deadline/liveliness attribution, and
writer binding. If it is not, isolate health endpoints with a fixed partition on
Status. This still uses exactly two WAN topics.

Neither a dedicated writer nor a partition alone proves isolation from automatic
participant-level liveliness. Test health stopped while application Status
continues, and distinguish missing health data from a dead DDS participant.
Any necessary change to presence/liveliness policy is an explicit, separately
approved behavior change authored in XML, not an accidental consequence of
topic consolidation.

The current XML health profile specifies best effort, transient local,
KEEP_LAST(1), a 2-second deadline, and a 3-second automatic liveliness lease.
Snapshot effective settings in the test environment; older design prose has
different timings. Preserve the effective baseline, not stale documentation.
Do not silently force health to use Event reliability or generic Status QoS.

There is also an active code/profile discrepancy to resolve before cutover:
[PresenceMonitor.hpp](../router/src/core/PresenceMonitor.hpp) contains 8-second
deadline/12-second lease constants, and
[PresenceMonitor.cxx](../router/src/core/PresenceMonitor.cxx) uses the 8-second
threshold for its coalesced-peer sweep, while XML supplies the 2/3-second DDS
profile. Capture current single-peer and multiple-peer STALE/DEAD behavior and
agree one consistent policy. A native deadline test alone is not qualification
of the production presence state machine. Do not silently retune health as a
side effect of the conversion.

Health must not compete behind optional debug bursts without a tested scheduling
policy. A later attempt to merge all Status writers is a separate optimization,
accepted only after the independent health/lifecycle tests pass.

## 6. QoS, types, and wire-cost controls

Author all fixed endpoint and discovery QoS in external XML profiles. Select
profiles through existing QosProvider/QosResolver paths. Preserve required event
delivery separately from latest-state behavior; distinguish per-sample priority
from reliability, durability, history, deadline, and liveliness policies.

Freeze an endpoint-class matrix before implementation: at least required-event,
application-status, and health-status behavior. Split further only where existing
delivery/cache requirements demand it. A shared topic cannot give one writer
different reliability/history policies per union case. Preserve existing
per-sample priority, flow-controller byte/burst budgets, and overwrite behavior
where applicable; test interference between cases under backlog.

Use the planned hashed TypeIdentifier/TypeObject-v2 approach and agreed schemas.
Verify effective settings and captures: using hashes alone does not prove that
additional inline type metadata is absent. Preloading may avoid lookup, but does
not remove SEDP endpoint announcements or filter updates. Use the
[known-wrong-answer log](connext-ai-issues/connext-ai-issues.md) and the installed
7.7 behavior when checking type propagation.

Measure actual serialized sizes per variant, including the envelope. A union
sample carries one branch, but its type describes all branches. Establish bounds
for health peer lists, command payloads, diagnostics, and logical instances.
Preserve compact heartbeat packing; do not reintroduce full route diagnostics
into every health sample.

Two topic names do not prove lower bandwidth. Record endpoint counts, TypeLookup
traffic, startup, settled idle, data load, membership changes, and restart
separately. Count discovery maintenance and repair traffic as well as DATA.

## 7. Implementation sequence

Each step requires the preceding checks before integration proceeds. Conversion
unit tests can be developed alongside type qualification, but do not begin the
WAN cutover while filtering, keys, or health isolation remain unresolved.
Keep incompatible legacy and new WAN contracts isolated; do not disguise a
mixed deployment as a successful rollout.

| Phase | Deliverable | Required checks before proceeding |
|---|---|---|
| 1. Freeze the production contract | Capture current routes, recipients, source gates, effective QoS/health behavior, and workload/resource budgets; review mapping and key rules | Every WAN flow in §2 accounted for; unchanged LAN interfaces; broadcast/team/global semantics recorded |
| 2. Add production unions and codec | Add final Event/Status envelope types to ActTypes.idl, regenerate XML/C++, validate the mapping catalog, and implement whole-structure pack/unpack | All fifteen listed cases compare equal; original keys/aliases survive; bad mappings/types/bounds fail explicitly |
| 3. Add shared WAN adapters | Separate logical bindings from shared endpoint ownership; build source encode and destination decode runtimes using existing strand/barriers | Isolated command/navigation pilots pass; one intended send/output; route detach does not close another route's endpoint |
| 4. Wire controller/configuration and interests | Add explicit contract selection, logical-to-physical discovery/type readiness, aggregate CFT updates, team/source tags, and existing admin operations | Every source/destination route works in both roles; rapid updates, old-team samples, enable/disable, and teardown races behave as specified |
| 5. Complete lifecycle and load behavior | Replace the spike's limited bindings with bounded key/ownership/restart handling; preserve event retention, latest-state replacement, priorities, and flow control | Multi-writer loss, first-observed invalid states, crash/restart, slow receivers, optional bursts, and resource exhaustion have tested outcomes |
| 6. Move presence and health | Publish/read the health Status case at the existing timing boundary; retain dedicated health classes, session binding, passive RTT, and LAN summaries | Single/coalesced-peer STALE/DEAD behavior, idle health, unrelated Status traffic, loss/recovery, and RTT/restart tests pass |
| 7. Update evidence and supported clients | Keep logical route/audit meaning while reporting physical Event/Status endpoints; preserve operator APIs and account for supported direct-WAN clients | Mission/team/mode/debug, delivery/grace, health, physical stats, and zero-double-count assertions pass |
| 8. Qualify and cut over | Rebuild, run targeted/full gates, compare equal workloads, switch the compatible mesh together, and verify rollback | Exactly Event/Status on WAN; all required logical cases delivered; agreed performance/resource budgets and rollback pass |

The nine SDK checks and whole-structure spike already passed; reuse them as
regression evidence. Do not repeat exploratory qualification instead of wiring
the production seams. Extend their checks to the final schema and new behavior.

**Phase 1 output:** a reviewed mapping/endpoint matrix and acceptance budget.
Include route enable defaults, input/output LAN QoS, multiple team membership,
overlapping navigation audiences, original DDS keys, metadata bounds, the
health timing discrepancy, queue/instance/filter limits, and event retention.
Measure current baseline failures as well as passes; historical test counts are
not a substitute for a current rebuilt baseline.

Propose a process configuration selector, `wan_contract: legacy | two_topic_union`,
with `legacy` remaining the default during development. This is a draft interface,
not a currently supported config field. New-mode config validation rejects
unmapped WAN routes and legacy physical WAN publications. Intermediate feature
tests use isolated fixtures; they are not mixed-contract deployments on Domain 200.

### 7.1 Schema and conversion work

- Keep [ActTypes.idl](../harness_v2/datamodel/ActTypes.idl) as the only source
  of truth. Generate committed XML with rtiddsgen and rebuild the router.
  Do not hand-maintain a second WAN type definition in Python or YAML.
- Assign stable discriminator values and member IDs. Test proposed extensibility
  with old/new readers and writers, including added cases, unknown cases,
  missing required members, and bounds. `@mutable` is not a promise that an old
  receiver understands a new union case.
- Preserve source, command/contact identity, timestamps, sequence/session,
  audience, and lifecycle meaning across both directions. Keep converters
  testable without creating DDS participants. Copy existing command structures
  unchanged; leave payload decoding and operation-specific schema redesign out
  of this migration.
- Define Event history/resources and deduplication separately. Do not use
  KEEP_LAST(1) on a shared event instance where it can overwrite pending commands.
- Validate catalog entries against the actual declared branch type, discriminator,
  key codec, inverse topic, and XML class. Start with exact topic names from the
  current config, not an unconstrained wildcard/discovery feature.
- Preload the final WAN types from the committed model. A WAN envelope type is
  not learned by treating an original LAN publication as its schema. Require
  exact original/branch schemas initially; any looser compatibility policy needs
  its own qualified whole-copy/evolution tests.
- Expand entity-free codec tests to every case, field/boundary value, unsupported
  schema, composite key, alias collision, and incomplete lifecycle key. Follow
  with real UDP tests of the final generated XML and profiles.

### 7.2 Router endpoint and adapter work

The current [RouteEntityFactory](../router/src/core/RouteEntityFactory.hpp)
uses the same template type for input and output. The
[DynamicRouteFactory](../router/src/core/DynamicRouteFactory.cxx) also resolves
types by topic name. A legacy local topic and a WAN envelope have different
names and shapes: **this migration is not a set of YAML topic renames**.

The whole-structure path and shared writer classes now have an executable
proof in §3.4. Production implementation is still required; preserve its exact
type/discriminator checks, invertible topic identity, and explicit lifecycle
handling rather than copying only the successful valid-data path.

- Add an explicit WAN conversion boundary while retaining generic same-type
  routing for unaffected LAN routes. It needs distinct input/output type and
  topic resolution, not an assumption that the WAN type can be learned from
  the legacy publication.
- Share WAN writers/readers by justified endpoint class and route interest;
  do not recreate the old endpoint explosion under two names. Specify ownership,
  reference lifetime, duplicate-output prevention, counters, and teardown.
- Use one agreed Event and Status type registration per WAN participant.
  Qualify reuse between health and adapter entities, especially if mixing
  generated typed entities with DynamicData. Reuse the WAN participant rather
  than creating a separate participant for each legacy source.
- Decode each accepted WAN case to the existing destination LAN contract.
  Preserve identity and same-node/loop guards. Account for C2 and global
  navigation exports together so consolidation does not accidentally publish
  the same logical navigation update twice.
- Explicitly handle required dispose/unregister metadata at the adapter
  boundary. The current [runtime pump](../router/src/core/RouteRuntime.hpp)
  skips invalid samples; merely reusing it would not preserve lifecycle delivery.
- Acquire/release shared endpoints separately from per-route runtime close.
  Give physical endpoints their own lifecycle generation; retain logical
  route-generation stamps on bindings/callbacks. Creation failure affects the
  appropriate bindings with explicit errors, not success-shaped readiness.
- Update discovery/readiness for different input/output topic names. A source
  route follows its original LAN publication plus the shared WAN class; a
  destination route follows the known WAN type plus its allowed original LAN
  output. Preserve the existing local-consumer readiness/retention contract;
  a WAN match or transport acknowledgment is not local delivery or execution.
- Register physical WAN stats once per endpoint owner, not once per logical
  route reference. Keep per-route valid/lifecycle counts separate and avoid
  assigning one shared writer's aggregate count to every union branch.
- Pilot real command and navigation paths before enabling all mappings. Verify
  disable/re-enable, last-reference close, failed creation, own-writer exclusion,
  and callback teardown under active traffic.

**Phase 5 lifecycle contract:** cover graceful unregister/dispose, input writer
loss while the router stays alive, route disable/removal, multiple producers of
one logical instance, receiver/source restart, and previously unseen key-only
states. Forward the actual original instance state; losing one publication must
not be invented as loss of the last producer. Define how original keys can be
reconstructed or reconciled after restart. Do not silently drop a lifecycle
notification just because the spike's in-memory binding would be missing.

Bound registries, DDS histories, deduplication state, and pending delivery
resources. Test admitted events through a real unacknowledged backlog, plus
queue exhaustion and slow receivers. Optional debug/status bursts must not
starve admitted commands or health. On overload, expose the chosen rejection,
timeout, or degraded state; do not add unlimited queues or silent event drops.

### 7.3 Runtime filtering and team changes

[RouteRuntime](../router/src/core/RouteRuntime.hpp) holds a CFT but currently
exposes partition updates, not an equivalent filter-parameter update seam.
[AsyncWaitSetDispatcher](../router/src/core/AsyncWaitSetDispatcher.hpp) and
the factory forward partition changes today.

- Extend the existing controller/action, factory, dispatcher, and runtime
  path for parameter changes. Use the established ownership/teardown barriers
  and generation checks; do not mutate DDS objects from an unrelated API thread.
- Keep expression structure stable where possible; validate parameter sets
  before application. Surface a missing runtime, rejected predicate, resource
  exhaustion, or stale update as an explicit result, not a successful no-op.
- Carry command identity and membership version through reader interests and
  outgoing source tags. Define the local application order and what
  "applied" acknowledges. It must not imply every remote writer has already
  switched; use a separately defined barrier if that guarantee is required.
- Choose bounded old-team cache/in-flight treatment and roster distribution.
  Test rapid reassignment, stale acknowledgments, restarted sources, and
  teardown during an update, without conflating membership version with a key.
- Aggregate interests by shared reader class before applying them. Adding or
  removing one route must not overwrite another active route's filter.
- Preserve logical ADD/REPLACE/ENABLE/DISABLE route and participant/team commands.
  Resolve their WAN bindings through the validated catalog and reject unknown
  mappings/QoS combinations through the existing command-ack/error path.
  Do not require operators to send union payloads just to control the router.
- Preserve primary/detail/debug source gating and always-ready destination
  behavior. Removing the final optional route releases its binding, not the
  health endpoint or an unrelated route's shared writer.

### 7.4 Health, consumers, and measurement work

- [Router configuration](../router/config/control-platform.yaml), route factories,
  runtimes, QoS resolver, and [PresenceMonitor.cxx](../router/src/core/PresenceMonitor.cxx).
  Health should produce its Status case directly at the existing timing boundary,
  not take an extra generic queue hop that changes passive RTT accounting.
- [Platform sim](../harness_v2/sims/platform_sim.py),
  [control sim](../harness_v2/sims/control_sim.py), and
  [platform mesh control](../harness_v2/scripts/platform_mesh_control.py).
  Prefer unchanged local topic interfaces for the first WAN cutover; change
  applications only where conversion, identity, or audit requirements demand it.
- [Dashboard bridge](../gui/mesh_dashboard/server/mesh_bridge.py), mission/status
  consumers, source-mode logic, and static UI. Preserve REST/WS response shapes
  through adapters where possible; version intentional API changes and bump
  changed static-script query versions.
- WIS/template and auxiliary client bindings that still use legacy types must
  be inventoried and updated if supported. The current bridge's DDS writers are
  the primary operator path; do not mistake historical WIS examples for it.
- [Delivery audit](../harness_v2/sims/delivery_audit.py), expectation compilation,
  [delivery QoS display](../gui/mesh_dashboard/server/delivery_config.py),
  [wire delivery](../gui/mesh_dashboard/server/wire_delivery.py), and packet
  monitors/classifiers.

### 7.5 Production change map

These are the concrete integration surfaces, not a direction to replace the
controller or rewrite unrelated routing.

| Existing surface | Planned change |
|---|---|
| [ActTypes.idl](../harness_v2/datamodel/ActTypes.idl), [generated XML](../harness_v2/datamodel/gen/ActTypes.xml), [router build](../router/CMakeLists.txt) | Add final unions/envelopes using unchanged existing structs; regenerate the single model; include codec tests/types in the build |
| [RouteConfigParser](../router/src/config/RouteConfigParser.hpp), [parser implementation](../router/src/config/RouteConfigParser.cxx), [system config](../router/config/control-platform.yaml) | Parse/validate contract and mapping catalog; keep role-aware side selection, LAN interfaces, original route names, and source gates |
| [router_main](../router/src/router_main.cxx), [ParticipantRegistry](../router/src/core/ParticipantRegistry.hpp), [QosResolver](../router/src/core/QosResolver.hpp), [XML QoS](../harness_v2/qos/act_qos_profiles.xml) | Wire shared endpoint lifetime on the existing WAN participant; resolve fixed policies externally and validate class compatibility |
| [DynamicRouteFactory](../router/src/core/DynamicRouteFactory.cxx), [RouteEntityFactory](../router/src/core/RouteEntityFactory.hpp), [TypeResolver](../router/src/core/TypeResolver.hpp) | Introduce different input/output schemas/topics and codec bindings; retain the same-type lane for unaffected routing |
| [RouterController](../router/src/core/RouterController.cxx), [RouterState](../router/src/core/RouterState.cxx), [RouterEvents](../router/src/core/RouterEvents.hpp), [Interfaces](../router/src/core/Interfaces.hpp), [DiscoveryDispatcher](../router/src/core/DiscoveryDispatcher.cxx) | Translate physical WAN discovery/readiness/matches into logical route state; apply aggregate interests and stamped failures through existing seams |
| [RouteRuntime](../router/src/core/RouteRuntime.hpp), [AsyncWaitSetDispatcher](../router/src/core/AsyncWaitSetDispatcher.hpp) | Add encode/decode and lifecycle runtimes, shared-reference release, aggregate filter updates, loop guards, and bounded teardown |
| [PresenceMonitor](../router/src/core/PresenceMonitor.cxx), [HeartbeatRtt](../router/src/core/HeartbeatRtt.hpp) | Bind health directly to Status while preserving publication timing, peer/session attribution, and LAN summary behavior |
| [LinkStatsCollector](../router/src/core/LinkStatsCollector.hpp), [WanStatsPoll](../router/src/core/WanStatsPoll.hpp), audit/dashboard surfaces in §7.4 | Poll each physical endpoint once; retain logical per-case/route reporting and distinguish valid DATA from lifecycle counters |
| [configuration guide](cpp_router/configuration.md), [router guide](../router/README.md), [mesh lifecycle](../harness_v2/scripts/run_mesh.sh) | Document supported config/mapping semantics and rollback; switch supported launch paths only after the gates pass |

Use small reviewable implementation changes: schema/codec first; ownership and
pilots next; then controller/interests, lifecycle/load, health, consumers, and
rollout. Keep `legacy` regression tests working throughout. A completed phase
means its deliverable and tests passed, not merely that its files were edited.

## 8. Preserve measurement meaning

Physical topic grouping becomes Event/Status, but logical reporting must still
distinguish command, acknowledgment, navigation, environmental data, health, and
debug cases. Otherwise a successful high-rate status branch could mask failed
commands or health.

Version application audit identities. The target logical-send key is
`(run_id, wan_family, source_node, source_router, publishing_session, variant,
instance_id, sequence)`, plus receiver identity for a receipt. Define how legacy
audit records translate and which fields are emitted at the actual publication
boundary. Do not join independent producer counters solely by Event/Status.
Prevent duplicate counts if both LAN producers and WAN adapters log one send.

Expected recipients come from authoritative destination/team state at send time,
not from current membership at report time. Preserve grace policies per logical
case; do not assign one grace window to every Status variant. Intentional changes
to existing grace semantics require separate documentation and tests.

Wire-level RTPS identity remains writer GUID plus RTPS sequence and network
attribution; it is distinct from application sequence identity. Generic packet
counts cannot identify a union branch without decoding or an explicit mapping.
Do not present transport receipt as command execution.

See [delivery audit framework](delivery-audit-framework.md) and
[traffic accounting design](traffic-accounting-design.md). Keep historical
reports unchanged and label legacy/new contracts separately.

## 9. Acceptance tests and rollout

First use DDS-free controller/mapping-model tests and entity-free DynamicData
codec tests, then focused Connext integration tests in an isolated environment.
Do not launch or modify a live mesh for this draft.
Future DDS runs use the owned harness, reserved domains at or below 232, and
bounded captures, with before/after process and shared-memory checks.

Required behavior checks:

1. Every Event/Status case round-trips, with bounds, discriminator/envelope
   identity consistency, independent keys, invalid-data handling, and explicit
   errors. Verify the selected branch prefix against the discriminator for valid
   data, and recover variant identity from keys for invalid data.
2. A debug update cannot replace navigation or health; one sensor cannot replace
   another; latest-value and event retention tests pass under congestion.
3. Targeted commands/team/mode operations reach only intended receivers;
   global navigation and exact team environmental recipients remain correct.
4. Filter changes, repeated assignments, restart, and delayed old-team samples
   obey the chosen transition rules. Actual unwanted sender transmissions are
   absent in the filtering qualification.
5. Health remains visible with application traffic stopped. With health stopped
   but other Status traffic active, existing STALE/DEAD behavior still occurs.
6. Passive RTT stays valid across loss/recovery, writer recreation, duplicate
   identity, stale sessions, and wall-clock changes; protocol stats remain available.
7. Dashboard mission/team/debug actions, app audit, wire audit, and LAN admin
   behavior preserve their defined outputs. No success-shaped fallback for an
   unsupported union or missing evidence.
8. After cutover, Domain 200 discovery and bounded captures show exactly Event
   and Status; no RouterHealth or legacy application WAN topic remains. Obtain
   positive delivery evidence for both topic families and required logical cases.
9. Two logical navigation audiences produce one source WAN publication; disabling
   either audience leaves the other working. Shared endpoint stats are counted
   once and route/type enable gates remain independent.
10. Slow/nonreceiving peers, optional debug bursts, multiple instances, restarts,
    and resource limits preserve the agreed command/health/freshness behavior.
    Record CPU/memory and actual serialized/envelope sizes alongside total bytes.

Reuse the current focused tests in
[router/test](../router/test),
[router/test_e2e](../router/test_e2e), and
[dashboard tests](../gui/mesh_dashboard/tests). In particular, extend health/RTT,
full-router, team/mode, mission, same-node, delivery, and traffic-accounting tests.
Rebuild in a verified Connext 7.7 environment before targeted integration tests;
run the full router checks only after the targeted checks succeed.

Start integration extensions with
[control-command routing](../router/test_e2e/test_control_command_route.py),
[platform status](../router/test_e2e/test_platform_status_route.py),
[same-node exclusion](../router/test_e2e/test_same_node_ignore.py),
[full control/platform routing](../router/test_e2e/test_control_platform_full.py),
[team assignment](../router/test_e2e/test_team_assignment_e2e.py),
[status modes](../router/test_e2e/test_status_resolution_e2e.py),
[presence](../router/test_e2e/test_presence_roster.py), and
[link stats](../router/test_e2e/test_link_stats.py). Add the missing shared-owner,
final-schema, first-observed lifecycle, multi-producer, restart, and overload
coverage rather than assuming these existing tests cover it.

Future full-router gate commands, in the same verified/rebuilt environment:

```bash
bash router/run_tests.sh
python3 -m pytest -q -p no:cacheprovider router/test_e2e
```

Use the runner rather than a zero-test `ctest --test-dir` invocation on the old
host CMake. Run DDS tests through the owned isolated lifecycle and reserved
domains, with no live mesh. These commands are implementation gates, not tests
executed for this documentation-only draft.

Only after isolated qualification, validate the containerized EMANE topology
through the owned lifecycle and actual GUI path. Compare equal workloads and
windows with complete, zero-drop captures. Document total bytes, peaks, first
delivery, state age, command outcome timing, and health/RTT behavior. A bandwidth
saving is a measured result, not a prerequisite assumed from topic count.

Keep the legacy deployment/config available for rollback. Cut over a compatible
group together or explicitly design/version a translation boundary; do not leave
dual-published legacy topics in the accepted two-topic WAN.

Prepare rollback before deployment: retain the matching legacy binary, schema,
config, and image; stop the owned mesh, select the compatible legacy set, and
restart the whole group. Recheck peers, audiences, health, and operator delivery.
Define how admitted pending commands are drained, reported, or reconciled before
either switch; do not blindly replay them or promise cross-contract history.
Missing cases, health regressions, incorrect recipients/duplicates, or breached
agreed budgets stop rollout and trigger the tested rollback.

## 10. Remaining decisions and integration checks

- Final envelope fields/bounds, union case names, schema evolution, and key layout.
  Status uses the selected variant-qualified IDs; the draft proposes the same
  canonical variant/topic/original-key tuple for Event, pending retention/resource
  review. No field mapping or typed-operation redesign in the first conversion.
- Final audience and branch-content CFT expressions and filter-resource budget
  for each endpoint; §3.3 proves representative sender filtering, not every
  final variant or fleet-scale interest combination.
- Membership transition/roster policy and acknowledgment of applied changes.
  Preserve current TeamAssignment broadcast and supported multiple audiences/
  memberships; do not narrow them accidentally while normalizing metadata.
- Status endpoint/QoS layout, health isolation, and event retention requirements.
- Instance disposal and cache cleanup for optional or departed sources.
- Consistent health XML/code timing and coalesced-peer behavior, based on a
  measured baseline rather than either set of existing constants alone.
- Audit versioning, per-case grace rules, rollout compatibility, and measurable
  latency/freshness/size budgets.

These are required implementation checks, not reasons to add permanent WAN topics.

### 10.1 Risk controls

| Risk | Mitigation and required evidence |
|---|---|
| Shared queues let one class hurt another | XML endpoint classes, retained priorities/flow budgets, bounded resources; Phase 5 slow-peer/debug-burst tests and Phase 6 health isolation |
| Copy/envelope overhead outweighs discovery savings | Measure sizes, CPU/memory, idle/startup/update/repair bytes and freshness against the same legacy workload; Phase 8 budgets |
| Different QoS is silently flattened | Reviewed endpoint matrix; reject incompatible shared-class configuration or add a justified class; no per-case reliability/history promise from one writer |
| Aliases, producers, or restarts corrupt instance identity | Canonical original keys, stable variant/topic IDs, bounded ownership/reconciliation; multi-producer and unseen lifecycle tests |
| Schema or mapping drift loses fields | Exact branch-type checks, stable case/member IDs, versioned rollout, all-case full equality and explicit unsupported-data errors |
| Shared ownership breaks enable/disable or duplicates navigation | Separate logical bindings/physical generations; last-reference barriers, aggregate interests, and exact publication/output-count tests |
| Recipient changes leak or omit team/global data | Preserve broadcast and supported audience sets; test both roles, overlapping audiences, multi-team state, and delayed assignments |
| Topic aggregation hides failures | Logical per-case audit/grace, physical stats registered once, health-only processing, and no transport-ack-as-execution reporting |
| Rollout mixes contracts or replays commands | Explicit mode validation, compatible group switch, command reconciliation and a rehearsed owned-lifecycle rollback |

## 11. Evidence and reference points

- Connext AI supplied implementation guidance and follow-up answers; the
  unsupported key annotation and unverified branch syntax were corrected against
  empirical results in the shared
  [known-wrong-answer log](connext-ai-issues/connext-ai-issues.md).
- [7.7 key annotations](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/Using_BuiltinAnnotations.htm)
  describe nested aggregate key composition; read alongside the version-specific
  [unsupported features](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/extensible_types_guide/extensible_types/XTypes_Intro.htm).
- [7.7 SQL notation](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/SQL_Filter_Expression_Notation.htm)
  and [filter execution conditions](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/Where_Filtering_is_Applied.htm)
  guide the remaining writer-side qualification; a successful local CFT is
  not bandwidth evidence.
- Baseline presence/RTT invariants are in the
  [passive RTT plan](cpp_router/router-health-passive-rtt-plan.md), and logical
  delivery meaning is in the [audit framework](delivery-audit-framework.md).
