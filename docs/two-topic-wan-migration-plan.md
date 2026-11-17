# Two-topic WAN migration plan

Date: 2026-11-08

Status: Opt-in production implementation and schema version 3 live deployment
validated by the targeted gates in §3.27. The earlier full schema version 2 gate
is recorded in §3.18; native application lifecycle source qualification is
recorded in §3.20. Live lifecycle acceptance remains separate from source
qualification. The canonical configuration still defaults to legacy.
The dated checkpoints below preserve earlier investigation states; general
application lifecycle/restart/load qualification remains a separate open gate.
The selected three-topic baseline from the
[retained comparison](wan-topic-tradeoffs-2026-11-12.md) is integrated in the main
router and deployed on the seven-node mesh; all five live mission workflows
passed after isolated qualification (§3.40).
The deployed physical names are now **Event / State / Telemetry** (§3.41);
§3.40 retains the earlier names and baseline evidence.

The [native instance lifecycle implementation plan](two-topic-native-lifecycle-plan.md)
records forwarding data and key-only dispose/unregister notifications through
the same existing routes, including the completed isolated source gate in §3.20.
Live lifecycle acceptance and the wider restart/load gates remain separate.

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

The [pre-refactor baseline](two-topic-refactor-baseline.md) records the local
checkpoint commits, fresh-build results, and explicit browser/live-dashboard
coverage gaps. Use its tag as the implementation comparison point.

## 1. Target and boundaries

Use exactly two shared DDS topic names on the WAN, used by C2 and every platform:

- **Event**: a typed union of commands, acknowledgments, and discrete reports.
- **Status**: a typed union of latest-state reports, including RouterHealth.

There are no separate C2Event/C2Status or PlatformEvent/PlatformStatus WAN topics.
Node identity and role are sample fields, not topic-name suffixes. All
router-originated WAN status, including health and detailed route diagnostics,
belongs in Status. Reliable router configuration/filter snapshots use the
RouterConfig Event branch (§3.37). Do not leave RouterHealth as a permanent third WAN topic.

This is a WAN contract change. Existing local application topics and LAN-only
router administration, mesh snapshots, journals, and link statistics can remain
behind adapters. Two topic names do not mean two endpoints: independent writers
or readers on the same topic may be needed to preserve QoS and lifecycle behavior.
Record their actual count rather than promising two writers/readers per boat.

Preserve the current C2/platform functions, routing audiences, mission workflow,
status-mode controls, presence, passive RTT, and delivery evidence. This is not
a simultaneous redesign of radio routing, discovery protocol, or status cadence.
Historical spike workloads and reports retain their original contracts.

Keep every union branch's existing IDL structure unchanged, including command
octets, except the approved ContactReport LAN key/source rule in §3.17.
Reading key/audience/audit
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

The branches below are now declared in the production IDL and validated by the
[exact-topic catalog](../router/src/core/WanCodec.cxx). Legacy runtime routing
still uses the original topics; adding these types does not activate the migration.

| Current WAN message | Target | Union case / handling |
|---|---|---|
| ControlCommand | Event | `command` (1): whole `control_command`; existing `command_type`/octet payload and execution semantics unchanged |
| ActTeamAssignment | Event | `team_assignment` (2): whole `TeamAssignment`; preserve current broadcast to platforms |
| ActPlatformStatusMode | Event | `status_mode` (3): whole `PlatformStatusMode`; preserve selection by `platform_node` |
| PlatformCommandAck | Event | `command_ack` (4): whole `control_command_ack`; preserve current platform-to-control delivery and original destination |
| ContactReport | Event | `contact_report` (5): whole `contact_report`; preserve update/lost and required lifecycle behavior |
| RouterConfig (router-originated) | Event | `router_config` (6): full reliable filter/config snapshot, independent session/sequence validation; no application LAN route |
| PlatformInitStatus | Status | `navigation` (1): whole `platform_init_status` |
| PlatformData | Status | `data` (2): whole `platform_data`; preserve team scope and original key |
| PlatformMissionStatus | Status | `mission` (3): whole `platform_mission_status` |
| PlatformWaypointStatus | Status | `waypoint` (4): whole `platform_waypoint_status` |
| PlatformDetailStatus | Status | `detail` (5): whole `platform_detail_status` |
| PlatformDebugStatus | Status | `debug` (6): whole `platform_debug_status` |
| PlatformThrusterStatus | Status | `thruster` (7): whole `platform_thruster_status` |
| PlatformPowerStatus | Status | `power` (8): whole `platform_power_status` |
| ActRouterDiagnostics, when exported over WAN | Status | `diagnostics` (9): whole `RouterDiagnostics`; existing source-side debug gate |
| RouterHealth, from either role | Status | `health` (10): whole `RouterHealth`; preserve compact health and passive RTT fields |

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
The v1 IDL, exact mappings, bounds, and discriminator values are implemented
and qualified as described in §3.6. Future schema evolution remains a separate gate.

| Field group | Purpose |
|---|---|
| Source ID and role | Identify the configured node and distinguish C2/platform flows without changing topic names |
| Union discriminator, inside the payload union | Identify and select the variant; do not duplicate it as an envelope kind |
| Scope, destination, and team identity as applicable | Support addressed, global, team, and C2-only audiences |
| Logical instance identity | Distinguish sensors, contacts, missions, or devices where the existing payload requires it |
| Original topic/mapping identity | Select an allowed inverse mapping without losing wildcard aliases; not an arbitrary remote topic-creation request |
| Membership version | Identify which assignment a team-scoped publication belongs to |
| Run/session identity, sequence, and timestamps | Prevent restart collisions and preserve delivery/freshness evidence |
| Schema version | Define compatibility and explicit rejection of unsupported contracts |

**Selected Event/Status key (schema version 2):**
`source_id + original_topic + instance_id` (§3.16 supersedes §3.12; §3.17 defines LAN contact keys).
`source_id` is the configured system/node name, such as `Platform_30`, not the
internal router process name. `original_topic` identifies the allowed mapping;
`instance_id` is the raw catalog-selected payload identity, not an encoded
branch/topic/key string. ContactReport uses its existing `contact_id`; its LAN
DDS key is `(source, contact_id)`, with source filled from WAN `source_id` on
receive rather than trusted from the application payload. Other payload
key annotations remain unchanged. The union discriminator still selects the
payload branch; session remains non-key metadata.

Use one shared identity rule in encoders, decoders, lifecycle handling, and
audit consumers. Reject an original topic, payload branch or payload instance
identifier that disagrees with the catalog/envelope for valid data. Keep the
mapping and discriminator values stable across schema versions. Do not key timestamps, sequences, team identity,
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

**Approved v1 identity rule:** both families use the three outer keys.
The instance ID is `branch/<topic-byte-length>:<topic>/<key-byte-length>:<key>`.
The current fifteen types each have exactly one string key; catalog construction
checks the actual DDS key annotations. Event uses this stable identity too, not
a new instance for each command ID; required Event endpoints must retain KEEP_ALL.
Final queue/admission/deduplication budgets remain integration/load gates.

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

RouterHealth producer -> shared Status writer
Shared Status reader -> application decoder + presence/RTT -> existing LAN mesh summaries
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

Use required-Event and shared-Status writer/reader classes. The approved §3.12
simplification removes the dedicated health class: presence pins the same Status
pair used by application routes, including when those routes are idle or disabled.
Event endpoints remain demand-created. Extra endpoints require a separately
approved QoS/cache/isolation reason; they are not part of this design.

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

### 3.6 Production schema/codec checkpoint (2026-11-08)

Implemented [WanEvent/WanStatus](../harness_v2/datamodel/ActTypes.idl),
regenerated the committed XML, and added the entity-free
[WanCodec](../router/src/core/WanCodec.hpp). No legacy payload structure, active
route, participant profile selection, factory, controller, or health producer
has switched to the new WAN contract.

The contract uses MUTABLE envelopes/unions, explicit union member IDs (ten times
the discriminator), and `schema_version = 1`. Valid-data decode rejects other
versions, mismatched envelope/branch/key combinations, and noncatalog topics.
Exact topic names only are supported. Wildcard aliases and composite/non-string
keys are not implicitly accepted. Lifecycle key decode uses only the three outer
keys and needs neither a payload nor the nonkey original-topic/version fields.
Empty original string keys remain valid; malformed or noncanonical encodings fail.

| Original key | Cases | Effective current bound |
|---|---|---|
| `source` | Eleven application cases (all except assignment/mode and router cases) | 100 bytes |
| `platform_node` | TeamAssignment, PlatformStatusMode | 255 bytes |
| `router` | RouterDiagnostics, RouterHealth | 255 bytes |

Correction to the earlier "unbounded" assumption: this installed generator
converts bare IDL strings to `stringMaxLength="255"` in XML. The new source
identities and audience strings preserve that existing effective limit.
`instance_id` is explicitly 512 bytes, sufficient for the longest catalog
branch/topic plus a complete 255-byte original key and both length prefixes.
Tests exercise 255-byte identities/keys, 100-byte application keys, delimiter
characters, empty keys, and key-only recovery. No tighter legacy input limit was
introduced. Future additions must recheck this capacity rather than truncate keys.

Audience selectors are additive: `control`, `global`, `destination`, and the
full `team_ids` sequence (maximum 16 entries, matching current controller state).
These fields express routing interests, not authorization. The codec copies
caller-provided metadata; reading current controller state, deriving audience
rules, and updating interests are still Phase 3/4 work. Identity/protected
partition entries must retain their existing matching meaning in that wiring.

Catalog QoS classes are `wan_event` for the five Event cases, `wan_status` for
the nine application/diagnostic Status cases, and `router_health` for health.
Those names do not assert that endpoint sharing/health isolation is already wired.
The new opt-in XML profile `wan_two_topic_participant` inherits the legacy WAN
profile and raises only `contentfilter_property_max_length` to 16384 bytes.
The legacy profile remains unchanged. A 16-slot OR expression exceeded the
default 256-byte property limit; the new profile supports the qualified
16-by-16 exact-membership expression. EMANE-specific profile selection is still
an integration task; do not replace its transport settings with this base profile.

Verified in fresh Connext 7.7.0 containers:

- Router and generated C++ rebuilt; **6/6 entity-free C++ tests passed**, including
  [all-case codec tests](../router/test/test_wan_codec.cxx). Every field is populated
  recursively, including command bytes and nested router diagnostic/health
  structures; whole payload equality, all metadata, stable keys, empty/full team
  sets, and explicit rejection paths are checked. Compiled and XML envelope types
  compare equal.
- **25/25 real DDS qualification tests passed** (55.89 s, no skips): the original
  nine checks, fifteen production envelope wire round trips/stable-key recoveries,
  and a production Status CFT with 16 outgoing memberships and 16 receive interests.
  Empty/short sequences and an inactive health branch did not leak unwanted DATA;
  first/last-slot matches arrived. Protocol push counters showed zero unwanted
  DATA and filter changes converged to the new team. This is not an atomic barrier.
- Temporary containers/builds/wheels were removed; no live mesh was launched.

This checkpoint is schema/conversion qualification, **not production cutover**.
Contract acceptance budgets, shared ownership/teardown, aggregated audience
filters, health timing/isolation, restart/load, future-schema compatibility,
and rollback remain gates. The complete Python router/browser baseline was not
rerun for these inactive schema/codec additions.

### 3.7 Shared adapter checkpoint (2026-11-08)

Implemented [WanAdapterFactory](../router/src/core/WanAdapterFactory.hpp) in the
production core and linked it into `router_main`, but **it is not instantiated
or selected there**. Legacy routing remains active; the configuration selector
and controller/readiness integration are still Phase 4.

The factory owns shared WAN writers/readers by catalog QoS class, shared LAN
source readers and destination writers by participant/original topic, and
generation-checked logical bindings. Source aliases merge additive audiences
into one WAN publication; destination aliases produce one intended LAN output.
Removing one binding preserves other users; the last reference detaches handlers
before closing entities. Metadata providers must read immutable snapshots.
Restored LAN publications and locally originated WAN frames are suppressed to
prevent feedback. Physical WAN endpoints register statistics once, independently
of logical forwarding counters.

[AsyncWaitSetDispatcher](../router/src/core/AsyncWaitSetDispatcher.cxx) now rejects
duplicate and null-condition attachments, rolls back partial attachment, and
tracks conditions through blocking detach barriers. Failed detach is explicit
and retains remaining ownership rather than closing a still-attached runtime.
Late source errors after the last logical unbind retain physical endpoint context.

Verified with [the isolated adapter runner](../router/qualification/run_adapter_checks.sh):

- Fresh router/pilot build and **7/7 C++ tests passed**, including duplicate
  attachment preservation, partial rollback, and detach during an active handler.
- **7/7 Python DDS checks passed**: four
  [production adapter pilots](../router/test_e2e/test_wan_adapter_pilot.py) and
  three legacy command/status/same-node regressions. Pilots exercise production
  codec -> DDS -> codec -> original LAN payload equality, including the 8192-byte
  command, shared navigation/power endpoints, audience merging, alias deduplication,
  individual/last-reference removal, re-enable, stale generations, construction
  failure cleanup, active-traffic teardown, and feedback suppression.
- **25/25 union qualification tests passed** (57.18 s, no skips). The team
  intersection test now matches every one of the sixteen receive-interest
  parameters and every outgoing membership position, closing the earlier
  first/last-slot-only coverage gap.
- Owned temporary containers/builds/wheels were removed. Compact summaries remain
  under `debug/test_reports`; no live mesh/browser or full Python baseline ran.

This is an **inactive adapter checkpoint**, not a completed migration. Pilot CFTs
test endpoint plumbing; they are not the final aggregate role/global/addressed/team
selection rules. Invalid-data samples are explicitly reported as
`instance lifecycle forwarding is not enabled in this pilot`; they are not
forwarded. Complete lifecycle/restart/multiple-producer behavior remains Phase 5.
Wire-learned original-type readiness, controller match/completion events,
applied-state metadata/CFT updates, resource budgets, health/RTT integration,
load/priority behavior, evidence consumers, and rollback-tested cutover remain gates.

### 3.8 Controller and interest checkpoint (2026-11-09)

The opt-in production path is now wired through
[WanControllerFactory](../router/src/core/WanControllerFactory.cxx) and
[WanInterests](../router/src/core/WanInterests.cxx). Set
`router.wan_contract: two_topic_union`; omission still selects `legacy`.
Canonical production YAML remains on legacy. This is a verified controller
checkpoint, **not a fleet cutover or completion of the migration**.

The new mode requires the XML catalog and production QoS classes, one local-role
WAN participant using `wan_two_topic_participant` or its `_emane` variant,
discovery-independent team state, exact catalog topics in `dynamic_data` routes,
and unpartitioned endpoints on the admin LAN. Startup rejects unsupported
contracts/configurations, source LAN filters and untranslated legacy filters.
Presence/health configuration is explicitly rejected pending Phase 6.
The [router README](../router/README.md#opt-in-two-topic-wan-migration) describes
the opt-in configuration boundary.

Original topic types are still learned from the wire and must equal the XML
catalog type before adapters bind. Stamped logical readiness, failures and
teardown flow through the controller. Match/QoS observations use actual shared
endpoints: LAN counts remain original-topic-specific; WAN counts describe class
connectivity, **not evidence that a remote original-topic producer is enabled**.

Enabled source aliases share one original-topic ingress and publish one union
with their combined audiences. Metadata providers capture immutable applied-state
snapshots; replacing a source's providers is synchronized against forwarding.
Team membership commands update aggregate CFTs and subsequent outgoing metadata
without changing WAN discovery partitions. Receiver expressions preserve per-topic
control/global/addressed/exact-team interests and reject self-origin frames.
WAN route-partition changes translate to supported audience selections; LAN
partition changes and unsupported control-audience changes reject explicitly.
Failed commands restore prior controller state; partial native filter updates
have rollback, with explicit shutdown/fault reporting if rollback itself fails.

Team/peer interests must be exact, not `*?[]` patterns. Audience values retain the
255-byte bound; apostrophes and NULs are rejected explicitly in this opt-in path
rather than accepting an assignment that cannot select its data. The user approved
rejecting apostrophe-containing names instead of adding an encoding scheme.
This does not change original payload keys or legacy partition-name behavior.
Both combined source-alias team audiences and aggregate receive interests are
checked against the sixteen-name limit before applying them. The 16384-byte
filter-property budget is checked before native updates.

Two empirical findings are retained in the regression design:

- On this Connext 7.7 installation, a full sixteen-slot sequence predicate
  followed by its union discriminator admitted a nonmember. Discriminator-first
  clauses rejected it correctly. Production expressions use the qualified order;
  mixed control/global/team expression replacement and sender push counters are
  tested. Do not assume arbitrary equivalent SQL rewrites behave identically.
- The WAN publication observer must create its built-in publication reader before
  routers begin discovery. Creating it after application traffic returned no
  publication evidence. Starting it first verifies the actual discovered topic
  names are exactly **Event** and **Status**, without weakening that assertion.

Verification on 2026-11-09:

- Fresh Connext router/pilot build; **8/8 C++ tests passed**.
- [Adapter/controller runner](../router/qualification/run_adapter_checks.sh):
  **11/11 DDS tests passed** (81.70 s): four adapter pilots, four three-node
  controller tests and three legacy regressions.
- Controller tests preserve full LAN payload equality for all fourteen application
  cases, including the 8192-byte command; verify physical WAN topic names;
  exercise team joins/leaves, exact TEAM_A/TEAM_AB isolation, topic-specific
  interests, source-alias enable gates, rejected-update rollback, addressed
  commands, broadcast assignments, status mode, and wrong-type containment.
- [Union qualification](../router/qualification/run_checks.sh):
  **28/28 passed** (60.68 s), no skips. This includes all sixteen receive-interest
  parameters, all outgoing slots, mixed predicate updates and negative push counts.
- Whitespace and changed Python/shell syntax checks passed. Owned test containers,
  builds and wheels were removed; no DDS processes or RTI/dds shared-memory remnants
  remained. Compact reports are under `debug/test_reports`.

The full Python/browser baseline and live mesh were not rerun. No new commits or
pushes were made. Phase 5 remains lifecycle/restart/multiple-producer ownership,
retention, priority/load and bounded-resource behavior. Presence/RTT, evidence
consumers, compatibility and rollback-tested cutover remain later gates; open
acceptance/resource budgets are not closed by this checkpoint.

### 3.9 Full regression rerun (2026-11-09)

Fresh configure/build and the complete router regression suite were rerun after
the Phase 4 checkpoint. The owned temporary test image derived from `connext:7.7.0`
included pytest, Playwright and Chromium (installed after missing-import checks).
Connext, CMake/CTest, pytest imports and an actual browser launch were verified in
the same container used for testing. The checkout was read-only, with writable
temporary build/log overlays and an isolated network namespace.

| Gate | Result |
|---|---|
| Fresh configure/build | Passed |
| C++ suite via `router/run_tests.sh` | **8/8 passed**, 0.28 s |
| Complete `router/test_e2e` suite | **263 passed, 5 skipped**, plus **156 passed subtests**, 392.41 s |
| Union qualification through its owned runner | **28/28 passed**, no skips, 62.08 s |
| Previously skipped offline browser checks | **18/18 now pass** |

The Python suite collected 268 top-level tests. Its JUnit contains 424 entries
because it additionally records the 156 passed subtests; do not describe these
as 424 independent top-level tests. The complete router run includes the new
adapter/controller checks and the legacy router, mission contract/navigation,
offline mission UI/map, traffic and delivery regressions.

All five remaining skips are in the live mission workflow suite and require
`MISSION_DASHBOARD_URL` against an owned five/six-platform mesh. No live EMANE
mesh was launched, so actual fleet mission execution, reassignment, blackout/
recovery and live GUI behavior remain unverified in this rerun. In particular,
this is not full-mission qualification of the new two-topic mode, whose
presence/health integration remains Phase 6.

An additional direct qualification invocation initially refused the backlog
impairment test because its required isolation marker was absent. It was rerun
through `router/qualification/run_checks.sh`, which supplies the private namespace,
marker and required capability; all 28 tests passed. No source fix was needed.

Compact evidence:

- [C++ summary](../debug/test_reports/phase4_full_cpp.txt).
- [Full router JUnit](../debug/test_reports/phase4_full_router.xml) and
  [summary](../debug/test_reports/phase4_full_router.txt).
- [Final qualification JUnit](../debug/test_reports/phase4_full_qualification.xml).

The pre-refactor baseline remains unchanged historical evidence. Temporary
containers, image, build and raw logs are removed after the run; compact reports
remain ignored by Git. No runtime source changes, commits or pushes accompany
this verification.

### 3.10 Live EMANE mission regression (2026-11-09)

After the full regression rerun, the persistent router binary was rebuilt from
current source and the canonical containerized EMANE lifecycle launched control
plus five platforms (Platform_30 through Platform_34), with the live dashboard.
The mesh used **legacy WAN mode**: the new two-topic mode still requires Phase 6
health/presence integration before this live gate can qualify it.

All five tests in
[the live mission workflow suite](../router/test_e2e/test_mission_workflow.py)
ran against `MISSION_DASHBOARD_URL=http://127.0.0.1:8080` using real Chromium,
GUI/API commands and DDS mission/status traffic, without mocked responses:

| Workflow | Result |
|---|---|
| WAN blackout and INIT/terminal recovery | Passed |
| Automatic reporting and independent target toggles | Passed |
| Drag/team/validate/go and cross-tab DDS state | Passed |
| Multiple teams, boats and missions at demo speed | Passed |
| Live team goal inheritance and reassignment | Passed |

Result: **5 passed, zero skipped, zero failures**, 386.08 s. This closes the five
live skips from the earlier full regression run as a separate legacy-mode gate;
it does not change that earlier run's recorded counts. The multi-team scenario
verified all five boats orbiting, sustained movement, independent cancellation,
return-to-base and mission replacement. Browser checks also verified rendered
boat/map state, with no page errors or external requests.

Post-test checks confirmed all five peers `PRESENCE_ALIVE` / `ROUTER_OK`, the EMANE
controller available and no active impairments. Fixtures canceled missions and
restored original teams and clocks. At the user's request the mesh remains
running for inspection on dashboard port 8080, owned by Compose project
`act-emane-container-baseline` and workdir `debug/mesh_runs/emane`. Recheck current
runtime ownership before resuming; use the canonical lifecycle for teardown:

```bash
CONNEXT_SHARED_DIR=/home/dgreenberg/rti_connext_dds-7.7.0 \
  harness_v2/scripts/run_mesh.sh down --emane \
  --workdir /home/ssm-user/act-sim-scope-infra/debug/mesh_runs/emane
```

Evidence: [live JUnit](../debug/test_reports/phase4_live_missions.xml) and
[browser screenshots](../debug/test_reports/phase4_live_mission_screenshots/).
Disposable host pytest dependencies were removed; active mesh runtime logs remain
under the per-node debug directories. No runtime source changes, commits or pushes
accompany this verification. Phase 5 has not started.

### 3.11 Health on Status checkpoint (2026-11-09)

The health transport slice of Phase 6 was brought forward at the user's request;
Phase 5 remains unstarted. In `two_topic_union` mode,
[PresenceMonitor](../router/src/core/PresenceMonitor.cxx) now packs the whole
RouterHealth structure with the production codec into Status's `health` branch.
It preserves canonical instance keys, heartbeat session/sequence and audit
timestamps. A dedicated health writer and discriminator-only health reader retain
the unchanged `router_health` XML profile (best effort, transient local,
KEEP_LAST(1), 2 s deadline and 3 s automatic liveliness). No fixed DDS policy is
constructed in code, no third WAN topic or new probe endpoint is added.

The monitor still owns health publication independently of application routes;
configuration accepts presence on the local WAN participant and rejects explicit
RouterHealth application routes. Legacy mode keeps the RouterHealth topic.
LAN mesh summaries, diagnostics, team state, delivery-audit records and
direct-writer/session RTT attribution remain on their existing paths. RTT
acknowledgments and local send reservations are stamped after envelope conversion,
immediately before publication; protocol statistics identify the physical Status
topic in the new mode.

Verified with fresh builds in a disposable, network-isolated Connext container:

- **8/8 C++ tests passed**.
- **13/13 DDS tests passed** (195.63 s): all five two-topic controller tests,
  legacy presence/roster tests and legacy link-statistics tests.
- An additional strengthened health test passed (18.65 s), asserting nonzero
  per-peer physical send/receive counters as well as passive RTT.
- All fourteen application cases still round-trip unchanged while health is
  enabled; their discovered WAN publication names are exactly Event and Status.
- Idle health creates only Status. Envelope identity/session/sequence/timestamps
  match the health payload; both peers appear ALIVE on the LAN.
- A live writer withholding health becomes STALE while application Status
  continues, and does not become DEAD merely because the deadline passed.
  Closing that health writer produces DEAD despite the application writer.
  A killed router becomes DEAD, while the other boat stays ALIVE; restarting
  under the same name restores ALIVE with a new health session.

Evidence: [DDS JUnit](../debug/test_reports/status_health.xml),
[protocol/RTT JUnit](../debug/test_reports/status_health_protocol.xml) and
[C++ summary](../debug/test_reports/status_health_cpp.txt).

The user's existing legacy EMANE mesh was not modified or restarted. Builds and
raw test logs were temporary; the deployed `router/build` binary was not replaced
under running routers. Tear down the owned mesh and rebuild before deploying this
change. Live two-topic mission workflows have not yet been run. Application
lifecycle/retention/resource gates, health congestion isolation, and the existing
8/12-second code-constant versus 2/3-second XML timing discrepancy remain open;
this checkpoint does not silently change presence policy or complete all Phase 6
qualification. No commits or pushes accompany this change.

### 3.12 Shared Status simplification (2026-11-09)

The user approved replacing §3.11's dedicated health endpoint class with **one
Status writer and one Status reader per router**, shared by health and
application routes. Presence pins that pair through idle/disabled application
routes; its receive filter includes health alongside current application
interests. Physical protocol counters register once, while presence retains
passive RTT and unchanged LAN summaries, diagnostics and health audit records.

Both envelopes now have exactly two keys: `source_id + instance_id`. The router
stamps the configured node name as `source_id` and preserves the canonical
variant/topic/original-key instance ID. Session stays non-key. Original LAN
payload fields, including fields named `source_node`, are unchanged. IDL and
committed generated XML agree.

The new XML `wan_union_status` profile is best effort, volatile, KEEP_LAST(1),
with a 2-second deadline and infinite liveliness. Its
`act.presence.dead_after_stale_ms=30000` property supplies the presence countdown.
Legacy profiles and the default legacy WAN contract remain unchanged.

A direct DataReaderListener resolves only the supplied deadline instance and
ignores nonhealth branches. Health misses queue STALE using the callback time;
the mesh tick applies it within 0.5 seconds, avoiding LAN DDS writes inside the
native listener. Thirty seconds after that first STALE, the source becomes DEAD.
Repeated misses do not restart the timer. Any valid received Status from the
source restores ALIVE and cancels it. This is roughly 32 seconds from the last
health sample when no other Status arrives, not 30 seconds from the last sample.
There is deliberately no coalesced-instance sweep.

A native closure probe found NO_WRITERS with **zero deadline misses** after a
graceful writer close. The user therefore also approved RouterHealth
NO_WRITERS/dispose starting the same STALE/countdown path, not immediate DEAD.
This adds neither endpoints nor a finite liveliness lease. Application lifecycle
forwarding remains a separate Phase 5 gate.

Verification used fresh isolated Connext builds, not the live mesh binary:

- **8/8 C++ tests passed**.
- **16 DDS regressions passed**, plus the strengthened health test passed on its
  focused rerun (**114.68 s**). The rerun corrected a Python test API call to
  pass an instance handle to `dispose_instance`; no production fix was needed
  for that failure.
- Health verifies one physical Status publication/subscription per router,
  envelope identity/audit, idle traffic, passive RTT and physical counters,
  ignored nonhealth deadlines, application recovery, no premature DEAD,
  repeated-miss countdown stability, disposal, graceful NO_WRITERS, crash and
  same-name/new-session recovery.
- **20/20 production qualification cases passed** (**29.48 s**, eight unrelated
  historical/backlog cases deselected), including all fifteen union branches,
  stable two-key handles and sixteen-team native writer-side suppression with
  the actual `wan_union_status` profile.
- Installed QosProvider assertions verified BE/volatile/KEEP_LAST(1), matching
  2-second deadlines, infinite reader/writer liveliness and the 30000 ms property.

Evidence: [health JUnit](../debug/test_reports/shared_status_health.xml),
[production qualification JUnit](../debug/test_reports/shared_status_qualification.xml),
[C++ summary](../debug/test_reports/shared_status_cpp.txt), and
[combined check summary](../debug/test_reports/shared_status_checks.txt).
This is a focused gate, not a new full-suite or live two-topic mission run.
The retained five-platform legacy EMANE mesh was not restarted or modified;
all five peers remained ALIVE/ROUTER_OK with no active impairments. The in-use
`router/build` binary was not overwritten. Use the owned teardown/build/up
lifecycle for a later deployment.

### 3.13 Deployed shared-pair live mission gate (2026-11-09)

The user authorized replacing the retained legacy mesh and running the full
mission-level gate. The owned `down --emane` action stopped that mesh before
rebuilding the persistent router binary. The launcher now supports
`--wan-contract two_topic_union`: both control/platform renderers select the
union participant profiles and `wan_union_status`, and audit readiness recognizes
the new factory's interest-application events. Legacy remains the default.

The fresh five-platform EMANE mesh was launched with:

```bash
CONNEXT_SHARED_DIR=/home/dgreenberg/rti_connext_dds-7.7.0 \
  harness_v2/scripts/run_mesh.sh up --emane --platforms 5 \
  --wan-contract two_topic_union --with-dashboard --test-id shared-status-missions
```

Native Domain 200 discovery, checked both before and after the mission run,
showed exactly Event/Status and **one writer plus one reader for each family on
each of the six routers**. Health has no additional endpoint pair.

All five real Chromium mission workflows passed: **5/5, zero skips, 388.67 s**:

- Directional 100% blackout, reliable/best-effort wire loss, unaffected-peer
  delivery, and compact Init terminal-state recovery after GUI reset.
- Automatic reporting-mode transitions and independent target toggles.
- Drag/select/team/validate/go, cross-tab DDS state, command/ACK delivery,
  50 ms CommEffect, cancellation, return-to-base and reassignment.
- Five boats in two concurrent teams, 1 km/2 km target orbits, sustained motion,
  separation/water-only navigation, clustering and map/graph consistency.
- Live team-goal inheritance and reassignment.

The mission harness now accepts `MISSION_WAN_CONTRACT=two_topic_union` for
physical wire-row expectations, retaining legacy defaults. Consolidated Event
assertions retain explicit command/ACK directions. Cleanup resets impairments
and waits for availability before cancellation; blackout assertions bind both
initial and recovered reports to the current command's mission ID, not cached
reports from an earlier run. These were test-harness corrections, not relaxed
mission behavior or simulated DDS responses.

Additional verification: fresh build and **8/8 C++ tests**, launcher rendering
**1 test plus 4 subtests**, final live browser with six graph nodes, five
available boats, current script revisions and no page errors. Native SDK
matched-participant-data attribution warnings were observed; this is not a
claim of warning-free logs or completion of all Phase 5/load gates.

Evidence: [mission JUnit](../debug/test_reports/shared_status_live_missions.xml),
[endpoint inventory](../debug/test_reports/shared_status_live_endpoints.json),
[screenshots](../debug/test_reports/shared_status_live_mission_screenshots/),
[final browser checks](../debug/test_reports/shared_status_live_browser.json),
[launcher JUnit](../debug/test_reports/shared_status_launcher.xml),
[C++ summary](../debug/test_reports/shared_status_live_cpp.txt), and
[run summary](../debug/test_reports/shared_status_live_missions.txt).

**Current deployed state:** the new two-topic shared-pair mesh remains running
on port 8080 in the owned EMANE workdir. All five peers are ALIVE/ROUTER_OK;
missions are cancelled, original unassigned teams and 1x clocks restored,
controller available and impairments empty. No commits or pushes were made.

### 3.14 Source activity versus cached health freshness

The shared Status presence policy is unchanged: only a RouterHealth deadline or
health lifecycle loss starts STALE and its 30-second DEAD countdown; any valid
Status from that source restores ALIVE and cancels the countdown.

Presence now keeps distinct last-Status and last-RouterHealth receipt clocks.
Direct mesh peer ages, locally published direct heartbeat references, and dead
peer retention use source activity. Cached secondhand `RouterHealth.peers_seen`
ages advance from the last RouterHealth receipt instead. Application traffic
cannot make an old health topology fresh; a new health report replaces that
cached summary and restarts its elapsed-health age. Legacy health-only behavior
and the DDS schemas/profiles are unchanged.

A fresh isolated build passed **8/8 C++ tests** and **2/2 native DDS regressions**
(131.23 seconds), covering shared Status health/countdown/restart behavior and
legacy presence. The strengthened shared test checks monotonically increasing
cached edge ages through 32 seconds of application-only traffic, fresh direct
source ages in both mesh and outgoing health summaries, and an age reset after
new RouterHealth. The same test failed against the pre-fix deployed binary
because cached edge ages decreased with application updates.

Evidence: [DDS JUnit](../debug/test_reports/status_topology_freshness.xml) and
[C++ summary](../debug/test_reports/status_topology_cpp.txt).
The live mesh was neither modified nor restarted; its in-use binary still
predates this freshness fix. At this checkpoint, the startup collector lifetime,
queued health-loss ordering, and presence-only Status QoS validation findings
remained open; the next checkpoint closes them.

### 3.15 Remaining review findings resolved

- **Startup collector lifetime:** the link-stats owner is declared before the
  dispatcher and adapter owners, so it outlives every runtime unregister during
  exception unwinding as well as normal shutdown. The native adapter pilot uses
  the same ownership order. A unit regression throws with a pinned runtime before
  the AsyncWaitSet starts and verifies that the registry is empty before it dies.
- **Queued health-loss ordering:** the bounded queue now serializes receipt-time
  recording and cancellation with loss insertion. Both application Status and
  valid RouterHealth cancel the old loss epoch. A miss, recovery, then closure
  before a mesh tick retains the new closure instead of suppressing it behind an
  obsolete miss. Repeated misses within an unrecovered epoch still keep the first
  timestamp, and misses while STALE do not restart the countdown. Deterministic
  unit tests also cover recovery after a tick has drained the older miss,
  per-source isolation, capacity handling and cancellation without another miss.
- **Presence-only Status QoS:** one shared validation path checks the catalog
  profile for both application routes and presence. Event-only or route-free
  configurations with presence reject missing/legacy `wan_status` mappings.
  Correct profiles pass for control and platform roles; Event-only configurations
  without presence need no unused Status alias. Legacy validation is unchanged.

A fresh isolated Connext build passed **9/9 C++ tests** and **12/12 native DDS
tests** (205.06 seconds): all five two-topic controller cases, three legacy
presence cases, and four native adapter cases. This includes the cached-topology
freshness regression from the preceding checkpoint and the existing shared
health, lifecycle, countdown and restart checks.

An additional linker-wrapped constructor test injected a failure in the actual
router startup immediately after shared Status/presence pinning. With the fixed
main object, unwinding reached the normal fatal-error handler and exited **1**.
Relinking the same test with the previous main object reproduced **SIGSEGV
(-11)**. No production failure-injection hook was added.

Evidence: [DDS JUnit](../debug/test_reports/shared_status_review_fixes.xml),
[C++ summary](../debug/test_reports/shared_status_review_cpp.txt), and
[startup failure comparison](../debug/test_reports/shared_status_startup_unwind.json).
All four reported findings are now fixed in source. The live five-platform EMANE
mesh and its persistent binary were not modified or restarted, so these review
fixes require a later owned rebuild/restart before they affect the running mesh.
No commits or pushes were made.

### 3.16 Explicit three-key identity and initially unkeyed contacts

The user selected `(source_id, original_topic, instance_id)` as the WAN instance
identity. Both envelopes now key these three explicit fields. The router supplies
the system-level `source_id`; the catalog supplies `original_topic`; the payload
identity field supplies the generic `instance_id` without a branch/topic prefix.
Session, sequence, timestamps and audience remain non-key metadata.

For ContactReport, the identity field is the existing `contact_id`, not `source`.
At this checkpoint the contact payload was unkeyed on the LAN; §3.17 supersedes
that LAN-key decision. No new payload field is
added or renamed. `source` remains ordinary system-origin data, so C2 applications
can identify contacts by `(source, contact_id)`. Multiple contacts from one system
and the same contact ID from different systems have distinct WAN identities.
This does not create per-contact LAN DDS instances. The codec preserves complete
payloads, and contact run/sequence/timestamp audit metadata remains populated
independently of the chosen identity field.

This is **schema version 2**. The committed XML is regenerated from the IDL.
Key-only lifecycle handling reads the explicit original-topic key; full decode
checks the version, family, branch and payload instance identity. Version 1
encoded IDs are no longer the production contract. Upgrading requires rebuilding
and restarting routers and application participants together; the retained live
mesh has not been changed and still runs the earlier schema.

Fresh isolated validation passed:

- 9/9 C++ tests after rebuilding the router and regenerated types.
- 14/14 native DDS integration tests in 215.95 seconds, including multi-contact
  delivery, source preservation, shared Status presence and legacy forwarding.
- 21/21 selected production qualification cases in 28.97 seconds; eight historical
  fixture cases were deselected. Native instance handles distinguish contacts,
  systems and original topics while remaining stable across non-key updates.
- Generated XML confirms exactly three keys on each WAN envelope and no keys on
  the original ContactReport type.

Retained evidence: [C++ results](../debug/test_reports/wan_three_key_cpp.txt),
[DDS results](../debug/test_reports/wan_three_key_dds.xml), and
[qualification results](../debug/test_reports/wan_three_key_qualification.xml).
These are source/build validation results, not a deployment or live mission rerun.

### 3.17 Router-owned LAN contact origins

The user selected `(source, contact_id)` as the ContactReport LAN DDS key.
Both are existing fields; no `source_id` field is added to the application type.
The originating router still sets the WAN envelope's system-level `source_id`;
`instance_id` remains the raw `contact_id`.

On receive, full-payload decode copies the contact structure and then overwrites
its `source` with the envelope's `source_id` before LAN publication. Key-only
decode reconstructs the same `(source, contact_id)` identity. The receiving
router must not substitute its own system identity or trust the producer's
payload source. Producers may leave source empty. All other payload and audit
fields remain unchanged. Origins exceeding the existing 100-byte LAN source
bound are explicitly rejected rather than truncated.

This makes multiple contacts from one system and equal contact IDs from different
systems distinct native DDS instances at C2. Producer source changes and audit
updates do not change the C2 instance. Router stamping is application logic, not
field-level DDS access control. At this checkpoint, this extended the undeployed
schema version 2 change and required a coordinated participant rebuild/restart.
The running schema version 1 mesh was unchanged until §3.18.

Fresh isolated validation passed 9/9 C++ tests, 14/14 native DDS integration
tests in 217.06 seconds, and 21/21 selected production qualification cases in
27.59 seconds (eight historical fixture cases deselected). The native contact
case observes three distinct C2 DDS instance handles for two contacts from one
system and a reused contact ID from another, with producer source initially
empty. Subsequent updates with forged producer sources retain those same C2
handles and have the router-assigned origins. Codec coverage verifies full
payload preservation except source, key-only source reconstruction, producer
sample immutability, and the 100-byte boundary.

Retained evidence: [C++ results](../debug/test_reports/contact_lan_source_cpp.txt),
[DDS results](../debug/test_reports/contact_lan_source_dds.xml), and
[qualification results](../debug/test_reports/contact_lan_source_qualification.xml).
At this checkpoint the live five-platform mesh remained healthy and unchanged;
this was not a deployment or live mission rerun.

### 3.18 Full regression gate and schema version 2 live deployment

The final review's stale IDL comment was corrected to describe the ContactReport
LAN key and router-owned source restoration. The persistent router binary was
rebuilt, and all six participants were restarted through the owned EMANE
lifecycle with five platforms and `--wan-contract two_topic_union`.

The final automated gate passed with zero failures, errors, or skips:

- **9/9 C++ test targets**.
- **283 Python/router/DDS/browser tests plus 169 subtests**, 509.62 s.
- **29/29 full WAN qualification cases**, 64.64 s.
- **87 spike tests plus 63 subtests** across bootstrap, MAC accounting, and
  WAN data-plane tradeoffs.

The first qualification attempt lacked the required isolation guard. The complete
rerun used the existing qualification runner's isolated network namespace and
NET_ADMIN capability, not a bypassed guard or host-network impairment.

The first live mission attempt passed four workflows and exposed a pre-existing
simulator defect: the terminal return-to-base tick reported COMPLETE with the
last movement step's nonzero average speed. With explicit user approval, both
completion paths and cancellation now immediately publish zero navigation speed
and their terminal navigation state. Six regression checks failed before the
fix and passed afterward. The full automated gate above was repeated after this
fix; no live assertions were weakened.

All **five unchanged live browser mission workflows passed**, zero skips,
**365.59 s**: blackout/Init recovery, automatic reporting and target toggles,
drag/team/validate/go with cross-tab DDS and return-to-base, concurrent
multi-team missions, and live goal inheritance/reassignment. Fresh screenshots
were visually inspected, including the rendered two-team orbit scenario.

Post-test API checks confirmed five available/team-confirmed platforms, all five
control peers ALIVE/ROUTER_OK, cancelled missions, zero speed, unassigned teams,
1x clocks, and an available controller with no impairments. The schema version 2
mesh remains running on port 8080 in `debug/mesh_runs/emane`. All six actual router
executables matched the rebuilt binary during deployment verification. Native
matched-participant-data warnings remain; this is not a claim of clean logs or
complete application disposal/restart/load qualification.

Retained local evidence: [C++](../debug/test_reports/contact_schema2_full_cpp.txt),
[Python](../debug/test_reports/contact_schema2_full_python.xml),
[qualification](../debug/test_reports/contact_schema2_full_qualification.xml),
[bootstrap](../debug/test_reports/contact_schema2_spike_bootstrap.xml),
[accounting](../debug/test_reports/contact_schema2_spike_accounting.xml),
[tradeoffs](../debug/test_reports/contact_schema2_spike_tradeoffs.xml),
[red regression](../debug/test_reports/contact_schema2_terminal_speed_before.xml),
[green regression](../debug/test_reports/contact_schema2_terminal_speed_after.xml),
[live missions](../debug/test_reports/contact_schema2_live_missions.xml),
[screenshots](../debug/test_reports/contact_schema2_mission_screenshots/), and
[deployed binary hashes](../debug/test_reports/contact_schema2_deployed_binaries.json).

### 3.19 Native application lifecycle route gate (2026-11-10)

The requested dispose/unregister verification ran against the current schema
version 2 production binary in a disposable, network-isolated Connext container,
without touching the live EMANE mesh. Four native cases cover ContactReport/Event
and PlatformInitStatus/Status, each with full LAN-to-WAN-to-LAN routing and direct
WAN injection to isolate the destination adapter.

**All four acceptance cases failed**, 65.02 s, with no setup errors or skips.
Native source observers see `NOT_ALIVE_DISPOSED` and `NOT_ALIVE_NO_WRITERS`, but
neither transition crosses the LAN-to-WAN adapter. Direct WAN injection delivers
both native states to the WAN observer, but neither reaches the destination LAN.
Source writer autodispose-on-unregister was verified false, so unregister was
not accidentally tested as dispose. Initial valid delivery and ALIVE revival
after each operation succeed on every observed leg, retaining reader handles.
The existing multi-source/multi-contact identity positive control also passed
in the initial selected run.

Router logs explicitly report `instance lifecycle forwarding is not enabled in
this pilot`. This is a confirmed application lifecycle implementation gap on
both adapter boundaries, not a DDS transport failure or evidence that the earlier
spike's lifecycle implementation was integrated. RouterHealth has separate
lifecycle processing; its previous checks do not qualify application instances.
No production behavior was changed by this test-only request. The new tests
remain deliberately failing, not xfailed; the full gate must not be called green
until this gap is implemented. Unseen invalid instances, multiple-writer ownership,
restart cleanup and overload remain additional lifecycle/hardening work.

Retained local evidence: [JUnit](../debug/test_reports/routed_instance_lifecycle.xml)
and [per-leg native observations](../debug/test_reports/routed_instance_lifecycle_summary.json).
Test source:
[application lifecycle gate](../router/test_e2e/test_two_topic_controller.py).

The subsequent [native reader-purge spike](../spikes/reader_instance_purge/README.md)
ran 21 isolated cases with zero validation failures. Installed 7.7.0 rejects
finite 2/5-second reader-instance delays at reader construction. With zero delay,
C++ key lookup and native relay work while the take loan is held (also tested
for 2100 ms), but lookup fails immediately after loan return. Infinite retention
preserves lookup through the 5500 ms observation window. These results refine
the [implementation plan](two-topic-native-lifecycle-plan.md); they do not
implement either production adapter or clear the four failing acceptance cases.

### 3.20 Native lifecycle implementation and isolated regression gate (2026-11-10)

The user approved infinite reader-instance retention, deferring automatic
purging. Production adapters now forward application dispose/NO_WRITERS through
the same route bindings, codec key conversion, and physical output writers as
valid data. The output operation is native `dispose_instance()` or
`unregister_instance()` after `lookup_instance()`; lifecycle alone never creates
an output instance or publishes a blank valid payload.

The XML `ACT_QOS_LIB::router_lifecycle_reader` profile authors both infinite
reader-instance delays. `QosResolver` composes just those values into each
two-topic input reader, preserving route sample-purge settings and all other
policies. Shared legacy profiles are unchanged. No fallback key cache,
destination-delivery ledger, DDS writer-count replica, or new public factory
binding API was added. Existing mutexes, route generations and detach barriers
remain authoritative.

Input handling also accounts for final non-ALIVE state accompanying queued valid
samples: data is forwarded first, followed by one lifecycle operation per input
instance in that taken batch. This temporary batch grouping is not a persistent
instance registry. Per-sample/lifecycle errors are diagnosed without discarding
the rest of the batch. Logical valid-data counters exclude lifecycle operations.
RouterHealth's native lifecycle and deadline behavior remain separate and passed
the existing health/restart gate.

The user explicitly approved the empirically tested alias policy: different
producer `source` keys for the same boat/contact ID represent one logical contact.
Either alias's dispose/NO_WRITERS affects that canonical instance, just as their
regular samples update it. Independent alias ownership is not provided.

Fresh source build and final isolated gates:

- **9/9 C++ targets passed**, including XML retention composition and preservation
  of the original route reader QoS.
- **280 router/dashboard Python tests plus 177 subtests passed**, with **25 skips**,
  in three separate safe-domain-budget pytest processes.
- **29/29 WAN qualification cases passed**, including the reliable pending-backlog
  packet-impairment test in its private container network namespace.
- **One additional dispose-then-unregister native test passed** after the batched
  suite was collected. It compares output behavior with actual native input
  notifications rather than synthesizing a suppressed DDS transition.

The router/dashboard skips are 20 offline browser checks without Playwright in
the disposable runtime and five mission checks without a live mission URL.
No browser/live-mission gate is claimed for this implementation.
The first single-process broad attempt hit the existing 52-triple allocator
limit and lacked the qualification isolation flag; its failures were resolved
by the final batching and correctly isolated qualification run, not by raising
domain IDs, weakening tests, or changing production QoS.

The four formerly red cases now pass for Event and Status in both directions.
Additional native tests cover shared aliases/route removal, multiple writers on
one input key, non-key filter updates, never-materialized outputs, rapid queued
data/lifecycle, malformed-input recovery, and canonical contact alias semantics.
See the [implementation plan](two-topic-native-lifecycle-plan.md) and
[e2e guide](../router/test_e2e/README.md).

Local evidence: [C++](../debug/test_reports/native_lifecycle_cpp.txt),
[summary and four passing per-leg observations](../debug/test_reports/native_lifecycle_summary.json),
[router batch 1](../debug/test_reports/native_lifecycle_router_batch_1.xml),
[batch 2](../debug/test_reports/native_lifecycle_router_batch_2.xml),
[batch 3](../debug/test_reports/native_lifecycle_router_batch_3.xml),
[WAN qualification](../debug/test_reports/native_lifecycle_qualification.xml),
and [dispose/unregister sequence](../debug/test_reports/native_lifecycle_dispose_unregister.xml).
The historical failing lifecycle reports in §3.19 are preserved.

The build lived under an owned `/tmp` workspace mounted over the container's
build path; the persistent router binary was not overwritten. No live deployment
or mission run was performed. During qualification, the previously running six
mesh containers were observed stopped; this implementation workflow did not
restart them. Inspect current runtime ownership before any later deployment.
Unseen keys, resource-pressure replacement/churn, automatic purge, durable
restart/replay, and live deployment remain separate gates.

#### 3.20.1 Lifecycle-only finalization gate (2026-11-10)

Reviewed and qualified the lifecycle changes independently of the uncommitted
writer-filter observations, health-schema changes, and dashboard configuration
work. The tested source snapshot contains only the native lifecycle adapter,
XML retention composition, related tests, and reader-purge spike.

Fresh Connext 7.7.0 build and isolated results:

- **9/9 C++ targets passed.**
- **23/23 native adapter/controller tests passed**, including both lifecycle
  boundaries, shared routes, multiple writers, alias identity, queued data,
  dispose/unregister transitions, malformed input, and health/restart behavior.
- **250 router/dashboard regression tests plus 171 subtests passed; 24 skipped**:
  19 optional offline browser checks lack Playwright in the disposable runtime,
  and five live mission checks lack `MISSION_DASHBOARD_URL`.
- **29/29 WAN qualification cases passed.**
- **21 reader-purge cases passed**, including eight expected finite-delay
  rejections. Infinite instance retention remains the selected adapter policy.
- All four per-leg application lifecycle cases passed again with fresh native
  observations retained separately; this repeat is not added to the 273 distinct
  passing Python tests above.

Every DDS container used private network/IPC namespaces, read-only source
mounts, and temporary build/runtime storage. The existing Control_20 plus six
platform EMANE mesh was observed running and was not stopped, rebuilt, or
reconfigured. This source finalization does not qualify live lifecycle delivery,
mission workflows, restart/replay, unseen instances, or sustained resource churn.
The canonical legacy default is unchanged.

Retained local evidence: [C++](../debug/test_reports/native_lifecycle_final_cpp.txt),
[native adapter/controller](../debug/test_reports/native_lifecycle_final_targeted.xml),
[regressions](../debug/test_reports/native_lifecycle_final_regression.xml),
[WAN qualification](../debug/test_reports/native_lifecycle_final_qualification.xml),
[reader purge](../debug/test_reports/native_lifecycle_final_purge.json), and
[per-leg observations](../debug/test_reports/native_lifecycle_final_per_leg.xml).

### 3.21 Writer-observed filters integrated into main (2026-11-10)

The isolated RouterHealth filter-observation work is integrated into the main
working tree alongside the native lifecycle implementation and read-only Config
tab. Existing schema version 2 identities, adapter lifecycle handling and QoS
composition are preserved. No commit, push or live deployment was performed.

Each shared Event/Status writer polls its matched readers before the health
heartbeat and reports system identity, expression, parameters and explicit
poll/truncation errors. The dashboard's **Observed WAN filters** section shows
the reporting router's per-topic/per-reader observations, including its own
local WAN writers. These are discovery observations, not filter-execution
acknowledgments. Full expressions repeat in health traffic; congestion/load
qualification remains open.

The LAN mesh snapshot carries local and peer health ages. REST cache age and
browser elapsed time prevent cached observations from appearing newly received;
the display marks reports older than eight seconds stale. The local snapshot
also preserves the exact transmitted heartbeat acknowledgment fields. A native
regression failed before that fix and passed afterward, comparing complete
same-session/same-sequence local and WAN health samples with nonzero acknowledgments.
The new browser test follows the existing optional-Playwright skip convention.

Fresh integrated source was snapshotted into owned `/tmp` storage and built with
Connext 7.7.0 in a disposable image containing pytest, Playwright and Chromium.
Regenerated XML exactly matched the source checkout. Final results:

- **9/9 C++ targets passed.**
- **304 router/dashboard/DDS/browser tests plus 179 subtests passed; five live
  mission tests skipped** because `MISSION_DASHBOARD_URL` was not supplied.
  No offline browser tests were skipped.
- **29/29 WAN qualification cases passed**, including isolated packet impairment.
- **87 spike tests plus 63 subtests passed** across bootstrap, packet accounting
  and WAN tradeoffs.
- **21 native reader-purge cases validated**, including the eight expected SDK
  rejections of finite reader-instance delays.
- **Nine whole-structure adapter PASS groups and ten expected rejections.**

All DDS test containers used private network/IPC namespaces. Router tests ran in
separate legacy/union pytest processes, reserving 33 and 39 domain triples,
respectively, below the existing allocator limit. An earlier arbitrary batch
boundary initialized cached helper QoS under the union fixture's peer locators,
which failed a later production-QoS equality subtest; grouping the peer
environments separately resolved this without changing assertions or QoS.
The initial broad attempt also missed one contact at its WAN observer; that
test passed both an isolated rerun and the complete final rerun. These results
do not establish that the contact observer test is flake-free.

Compact local evidence: [C++](../debug/test_reports/writer_filter_main_cpp.txt),
[legacy/dashboard batch](../debug/test_reports/writer_filter_main_router_1.xml),
[union/lifecycle/filter batch](../debug/test_reports/writer_filter_main_router_2.xml),
[WAN qualification](../debug/test_reports/writer_filter_main_qualification.xml),
[bootstrap](../debug/test_reports/writer_filter_main_bootstrap.xml),
[accounting](../debug/test_reports/writer_filter_main_accounting.xml),
[tradeoffs](../debug/test_reports/writer_filter_main_tradeoffs.xml),
[reader purge](../debug/test_reports/writer_filter_main_reader_purge.json),
and [adapter spike](../debug/test_reports/writer_filter_main_adapter.txt).
Initial broad-run failures and the contact rerun are retained under the same
`writer_filter_main_` report prefix.

Only the private build was rebuilt; persistent router binaries were not
overwritten. No mesh was running at the initial ownership check, and none was
launched or restarted. The new IDL/XML requires coordinated rebuilding of all
consumers before deployment. Live missions and health congestion/load remain
separate, unexecuted gates for this integrated change.

### 3.22 Six-platform integrated deployment (2026-11-10)

At the user's request, the stopped canonical mesh was removed through its owned
lifecycle, the persistent router was rebuilt from the integrated working tree,
and **9/9 C++ targets passed**. The canonical EMANE launcher then started Control_20
plus **Platform_30 through Platform_35** with `--wan-contract two_topic_union` and
the dashboard on port 8080. All seven running router executable hashes match the
new persistent binary:
`2b193de90b5ff333f67b97fae38a8327a6ab3ba76ef45ebb30fc5470f14f8c44`.

All six peers are `PRESENCE_ALIVE` / `ROUTER_OK`. Every router reports complete,
successful Event and Status observations, each with seven named matched readers.
The EMANE controller is available; an explicit reset succeeded and a subsequent
read confirmed empty impairments. Network telemetry includes all seven nodes.

Headless Chromium rendered all seven nodes with the current graph asset revision
and a live WebSocket. Actual canvas clicks opened both platform and control
inspectors, each showing fourteen reader-filter rows across Event/Status, with
expressions and parameters and no stale indicator. No browser page or console
errors were recorded. The integrated VS Code browser could not reach its own
localhost; verification used Chromium on the mesh host instead.

The mesh is **left running**, Compose project `act-emane-container-baseline`,
workdir `debug/mesh_runs/emane`, run ID `container-20261110T175121-3299274`.
Use the owned `run_mesh.sh down --emane --workdir` lifecycle before rebuilding
or reconfiguring it. No teams or missions were changed by browser verification.
Live mission workflows and health congestion/load were not executed; native
matched-participant warning cleanliness is not claimed.

Local evidence: [readiness](../debug/test_reports/six_platform_filters_readiness.json),
[C++](../debug/test_reports/six_platform_filters_cpp.txt),
[browser checks](../debug/test_reports/six_platform_filters_browser.json),
[overview](../debug/test_reports/six_platform_filters_overview.png),
[platform inspector](../debug/test_reports/six_platform_filters_peer.png), and
[control inspector](../debug/test_reports/six_platform_filters_control.png).

### 3.23 Full browser/mission gate: live failures remain (2026-11-10)

The full integrated working tree at lifecycle commit `e5a0f92` was frozen into
an owned temporary snapshot, including the uncommitted filter-observation and
dashboard changes. Source hashes remained unchanged during testing. The private
Connext build regenerated the IDL XML and matched the checked-in XML exactly.
All isolated DDS tests used private network/IPC namespaces, not the live mesh.

Passing isolated results, with **zero skips**:

| Gate | Result |
|---|---|
| Fresh C++ build and unit targets | 9/9 |
| Router/dashboard regressions, including 21 actual Chromium checks | 304 tests + 179 subtests |
| WAN qualification | 29 cases |
| Bootstrap, RTPS accounting and route-tradeoff spikes | 87 tests + 63 subtests |
| Delivery-audit script tests | 4 tests |
| Native reader-retention matrix | 21 cases; eight expected finite-delay rejections |
| Whole-structure adapter | Nine PASS groups; ten expected input rejections |

This totals **424 isolated Python tests plus 242 subtests**, separately from
the C++ targets and native spike executables. Browser dependencies were installed
only in disposable test images. Three tradeoff report tests initially failed
because they write beneath their source directory; the unchanged suite passed
against a writable disposable copy. Those original failures are retained.

**The live gate is not green.** The first five-workflow run against the retained
six-platform mesh produced five failures and three teardown errors. Preflight
evidence confirms Platform_30 already had the manual DEBUG reporting override,
which invalidated the automatic-reporting assumptions of the first two checks.
Later command/status and cleanup timeouts were also observed.

The user explicitly authorized a coordinated owned-lifecycle restart into
automatic reporting mode. All six peers, unassigned teams, 1x clocks, zero speeds
and empty impairments were verified before rerunning the unchanged tests:

- **Three passed:** blackout/Init terminal recovery, automatic reporting/target
  toggles, and drag/team/validate/go with cross-tab DDS checks.
- **Two failed, with two teardown errors:** the concurrent six-platform mission
  did not reach ORBIT on every platform within 180 seconds; the following goal
  inheritance/reassignment check timed out waiting for team assignment.
  Cancellation acknowledgment waits also timed out during both cleanups.
- An independent goal-inheritance run on another clean six-platform mesh passed
  its complete test body, but still failed teardown waiting for cancellation
  acknowledgments. This is **one passed body and one error**, not a passing gate.

No assertions were weakened, no timeouts were relaxed, and no product source
changes were made to turn these failures into passes. The cause of the remaining
fleet/acknowledgment failures is unresolved; health congestion/load and live
qualification remain open.

After retaining failure evidence, the owned lifecycle returned the mesh to a
fresh nominal state. The retained run is
`container-20261110T215311-3807778`, Control_20 plus Platform_30 through Platform_35,
dashboard port 8080, `two_topic_union`. All six peers are ALIVE/ROUTER_OK, teams
are unassigned, clocks are 1x, speeds are zero, reporting is automatic, and
impairments are empty. All seven executable hashes still equal the §3.22
persistent binary; it was not rebuilt or overwritten. A final real browser check
rendered seven graph nodes, a live WebSocket and the Config tab without page or
console errors. Native matched-participant warning cleanliness is not claimed.

Local evidence: [complete summary](../debug/test_reports/full_mission_gate_20261110_summary.json),
[clean full live rerun](../debug/test_reports/full_mission_gate_20261110_live_retry.xml),
[independent inheritance run](../debug/test_reports/full_mission_gate_20261110_inheritance.xml),
[failure state](../debug/test_reports/full_mission_gate_20261110_retry_failed_state.json),
[mission screenshots](../debug/test_reports/full_mission_gate_20261110_retry_screenshots/),
[final readiness](../debug/test_reports/full_mission_gate_20261110_postflight.json),
and [final browser](../debug/test_reports/full_mission_gate_20261110_final_browser.json).
This supersedes §3.22's unexecuted-mission status, not its deployment evidence.

### 3.24 Mission freshness and live gate restored (2026-11-11)

The user reported `Holding: shared locations stale` during an ordinary mission.
Read-only diagnosis followed the global `PlatformInitStatus` location path,
not team sensor traffic. Raw WAN packet payloads matched the original audit
timestamps: Platform_32 wrote navigation to the WAN within about 1 ms, but
Platform_31 received those samples 6.5-8.6 seconds after publication. Peers
remaining ALIVE did not establish location freshness.

The canonical EMANE virtual transport had its own `bitrate=1M` limiter.
The installed EMANE 1.5.3 manifest confirms that this limits **combined receive
and send throughput**, separately from RF Pipe's radio transmit rate. Measured
combined delivered transport throughput was about 968 kbps against that 1 Mbps
cap, while the radio queue did not explain the multi-second delay.

The fix in [node-entrypoint.sh](../docker/connext-7.7/node-entrypoint.sh) sets
only the virtual transport bitrate to `0`, disabling this extra host TAP cap.
The RF Pipe rate remains **1 Mbps**; operator CommEffect controls, the navigator's
**2.5-second stale threshold**, and the **300-foot collision buffer** are unchanged.
No DDS QoS, schema, relay behavior, or persistent router binary was changed.
The new transport regression failed before the fix and passed afterward.

The user authorized an owned six-platform teardown/relaunch and the complete
unchanged live mission suite:

| Gate | Result |
|---|---|
| Entrypoint shell syntax | PASS |
| Transport/radio contracts, navigation safety and actual waypoint browsers | 22/22 |
| All five unchanged live mission/browser workflows | 5/5, 385.37 s; zero skips or cleanup errors |
| Postflight global-location delivery | 36 flows, 720 samples over 20 s; maximum age 0.456 s |
| Final read-only live browser | Six platforms, correct compact-waypoint asset revision, zero page/console errors or server writes |

Platform_32 to Platform_31 delivery after the fix had a 25 ms median and 76 ms
maximum in the initial 20-second measurement, instead of seconds-old samples.
Concurrent two-team ORBIT convergence, inheritance/reassignment, and cancellation
ACK cleanup all passed without relaxing assertions or timeouts. This resolves
the live failures in §3.23; its isolated 424-test/242-subtest gate is historical
evidence, not a newly repeated full isolated suite. Phase 5 fleet-scale load and
resource-budget qualification remain open.

Retained run: `container-20261111T001725-4096008`, Control_20 plus Platform_30-35,
`two_topic_union`, dashboard port 8080. Native introspection verified transport
bitrate `0` and RF Pipe datarate `1000000`; the persistent router hash still equals
§3.22. All six peers were ALIVE/ROUTER_OK with empty impairments at postflight.
The user ran a new Platform_32 mission after test cleanup; that operator activity
was deliberately preserved, not reset to the test fixture's baseline. Use the
owned lifecycle and inspect current user activity before further testing.
Native matched-participant warning cleanliness is not claimed.

Local evidence: [preflight](../debug/test_reports/mission_stale_transport_preflight.json),
[initial delivery measurement](../debug/test_reports/mission_stale_transport_after.json),
[targeted tests](../debug/test_reports/mission_stale_fix_targeted.xml),
[live workflows](../debug/test_reports/mission_stale_fix_live.xml),
[live screenshots](../debug/test_reports/mission_stale_fix_live_screenshots/),
[postflight](../debug/test_reports/mission_stale_fix_postflight.json),
and [final browser](../debug/test_reports/mission_stale_fix_final_browser.json).

### 3.25 User-approved 2 Mbps radio rate (2026-11-11)

The user requested a 2 Mbps radio rate, explicitly retaining the disabled
virtual-interface cap and authorizing a mesh restart. The canonical container
entrypoint now generates RF Pipe `datarate=2M`. PHY bandwidth remains 1 MHz;
CommEffect controls and navigation safety thresholds are unchanged.

The owned lifecycle restarted all seven nodes into retained run
`container-20261111T003138-4127828`, Control_20 plus Platform_30-35,
`two_topic_union`, dashboard port 8080. Native introspection of every node
verified radio `datarate=2000000`, virtual transport `bitrate=0`, and PHY
`bandwidth=1000000`. All six peers were ALIVE/ROUTER_OK with empty impairments.

Validation: entrypoint shell syntax and both transport/radio contract tests
passed. All 36 global-location flows delivered 720 samples over 20 seconds,
maximum age 0.021 seconds, below the unchanged 2.5-second stale threshold.
The read-only live browser rendered six platforms without page/console errors
or server writes. No persistent router rebuild was required. The full five
mission workflows in §3.24 passed at 1 Mbps; they were not rerun for this rate
change. The experimental feasibility harness is unchanged.

Evidence: [config tests](../debug/test_reports/radio_2mbps_config.xml),
[preflight](../debug/test_reports/radio_2mbps_preflight.json),
[readiness](../debug/test_reports/radio_2mbps_readiness.json),
[location freshness](../debug/test_reports/radio_2mbps_freshness.json),
and [live browser](../debug/test_reports/radio_2mbps_browser.png).

### 3.26 Readable original-topic WAN filters (2026-11-11)

The user selected `original_topic` equality instead of numeric union-discriminator
predicates for two-topic WAN CFTs and approved a coordinated live mesh restart.
Event/Status application selectors and the shared Status health clause now use
the original LAN topic names. The union discriminator and wire schema are unchanged:
the codec sets topic/discriminator from its existing catalog and rejects mismatches.
Identical topic sets share grouped audience selectors, preserving the 16-entry
team set and 16384-byte filter-property budget without repeating each topic set.
Source suppression, global navigation, broadcast and addressed commands, and health
interests are retained.

The dashboard presents short remote reader names, topic-selector badges and
distinct parameter values. Each reader's **View SQL filter** expands the full
identity, line-broken SQL and indexed parameters independently, retaining its
expansion across live updates. Unknown/old filters remain inspectable without
inventing a discriminator-name mapping. The former **raw team_partition** label is
now **reported team names**: the fixed `SHARED` participant partition remains,
but team changes update CFT interests and outgoing metadata, not DDS partitions.
Partition-named route configuration fields still express audiences in this mode.

Validation: **29/29 native WAN qualification cases** (69.01 s), **9/9 fresh C++
tests**, **14/14 targeted DDS controller/filter-observation regressions** (219.52 s),
and **5/5 targeted browser regressions** passed. Qualification checks exact
topic-name filtering for all fifteen branches, nonmember writer push counters,
empty/short/full team sequences, all sixteen interest/sequence slots and updates.
The persistent router was rebuilt after owned teardown and again passed 9/9 C++.
The adapter pilot also uses named-topic predicates: **11/11 DDS pilot regressions**
passed (41.97 s), including native lifecycle forwarding, filter replacement,
detach barriers and malformed-key containment. Unknown topics are now filtered
before decoding; malformed admitted keys still generate adapter errors without
stopping subsequent valid samples.
The owned six-platform EMANE mesh was relaunched in two-topic mode on port 8080,
retained run `container-20261111T013325-72469`.
All six peers are ALIVE/ROUTER_OK; the original TEAM_A (32/33/34) and TEAM_B
(30/31/35) assignments were restored and controller impairments are empty.
Live health telemetry exposed 98 reader observations, all named-topic filters
without `payload._d`. The live browser verified all fourteen reader cards,
current script revision, named topic badges, independently collapsed SQL and
expansion persistence with no page errors. Full mission workflows were not rerun.
Native matched-participant-data warnings and an initial disabled admin-writer
warning remain; this is not a clean-log or full-suite claim.

Evidence: [C++ gate](../debug/test_reports/original_topic_filters_cpp.txt),
[DDS gate](../debug/test_reports/original_topic_filters_dds.xml),
[adapter pilot](../debug/test_reports/original_topic_filters_pilot.xml) and
[WAN qualification](../debug/test_reports/two_topic_union_qualification.xml).

### 3.27 Unified destination IDs and team-addressed commands (2026-11-11)

The user selected a shared `audience.destination_ids` list on both Event and
Status, capped at **16 total IDs**, and explicitly enabled commands addressed to
a team as well as an individual. This replaces the old scalar destination plus
team-ID list. Names share the existing exact-match namespace; an ID may identify
a platform or a team. No wildcard interpretation or extra node/team type tag is
introduced. Global/control flags remain additive and TeamAssignment remains a
broadcast with its original assignment subject.

The WAN schema version is **3**. The three instance keys, topic names, union
discriminators and original LAN payload structures are unchanged. The codec
rejects version 2 valid-data frames; a coordinated participant rebuild/restart is
required, never a rolling mixed-schema deployment. Key-only lifecycle recovery
continues to use the unchanged keys, without requiring nonexistent payload data.

Outbound addressed commands copy their existing destination/target into the
shared list; team-scoped status copies current membership IDs. Receive CFTs select
`original_topic` and match any destination slot against the receiver's own ID or
current team interests. Source suppression, 16-slot matching, alias deduplication
and the 16384-byte filter-property budget remain enforced. The simulator and
mesh-control helper read local RouterStatus membership to recognize team-targeted
ControlCommand and PlatformStatusMode payloads, keeping the original payloads
unchanged. External application consumers need equivalent destination handling.
The dashboard still issues mission commands per platform for its existing
per-platform validation and ACK correlation; no command-octet redesign or new
dashboard command-publication API is introduced.

Source validation passed: **9/9 fresh C++ targets**, **125 targeted
DDS/application tests** (272.81 s), **29/29 native WAN qualification cases**
(68.54 s), and **5/5 targeted browser regressions**. The DDS/application gate
covered the two-topic controller, writer-observed filters, adapter pilot and
mission contract, including the actual simulator command-read/ACK loop.

The user-authorized owned teardown/rebuild/relaunch deployed schema version 3
to Control_20 plus Platform_30-35, retaining run
`container-20261111T152950-1705974` in `debug/mesh_runs/emane`, dashboard port 8080.
The persistent router/generated types were rebuilt while the mesh was stopped;
9/9 C++ targets passed again. The latest predeployment memberships were restored:
TEAM_B on 30/31/35; 32/33/34 unassigned. All six peers report
`PRESENCE_ALIVE` / `ROUTER_OK`; the controller reports no impairments.

Live verification checked all **98 writer-observed reader filters** across the
seven routers: readable original-topic selection, all sixteen destination slots
for platform receivers, exact current membership parameters, and no old audience
fields. Chromium confirmed the current v4 asset, Topic filters / Audience filters
labels, independently collapsed SQL cards and expansion persistence across live
refreshes, with no page errors. A benign `STATUS_REQUEST` addressed to TEAM_B
received accepted individual ACKs from **30/31/35 only**, not 32/33/34.
Compact local evidence is retained in
`debug/test_reports/destination_ids_{cpp.txt,dds.xml,live_filters.json,live_command.json}`
and `debug/test_reports/two_topic_union_qualification.xml`.
The full regression suite and five live mission workflows were **not rerun**
for schema version 3; do not inherit their earlier schema version 2 result.

### 3.28 System-timeout environment policy and two-minute retest (2026-11-11)

Source now uses `WAN_TIMEOUT_SEC` (default 300 seconds) as the final two-topic
presence window since the last valid received Status and the WAN discovery
lease. `WAN_HB_PERIOD_SEC` defaults to 3 seconds; the retry count is derived as
`ceil(timeout / heartbeat)`, default 100. Conflicting explicit retries and
invalid inputs fail clearly. Both C++ direct startup and Python launch/config
paths resolve policy before loading the unchanged-template XML profiles.
Compose forwards policy inputs to every node; the host launcher no longer
hardcodes a separate 10-second repair window and 100-second discovery lease.

The shared Status reader carries `act.presence.dead_timeout_sec` from the same
XML variable. DEAD is measured from last Status, not from STALE. The 2-second
health deadline and infinite WAN writer liveliness remain; legacy health
liveliness behavior is unchanged. Shared-Status DEAD peers now remain visible
for 60 seconds after death rather than being immediately pruned by an older
last-contact retention age. The read-only Config tab shows the new timing,
heartbeat period and retries, while retaining support for the old running
dashboard's startup snapshot.

Fresh private router build and actual native XML checks passed. Default policy
resolved to 300/3/100; a 301/7 input resolved to 43 retries. The one-platform
Compose render propagated both inputs and rejected invalid policy without
starting containers. The actual C++ process rejected conflicting retries before
creating DDS entities. C++ deadline tests cover 299/300 seconds and recovery;
the real-router health/RTT/dispose/restart regression passed with an explicit
32-second test-only timeout (115.58 seconds). Host browser checks and native
configuration tests passed; the missing browser/native dependencies were covered
in the complementary environments rather than counted as executed skips.

Two actual-router ContactReport/Event cases passed (251.38 seconds), including
a 120.012-second blackout with all contact state restored. Peers remained STALE
without DEAD/pruning and recovered ALIVE about 0.99 seconds after restoration.
The native serial repeat recovered all contact state and command ACKs, kept
readers active, and drained a peak 12,721-sample backlog to zero.

**The native performance gate still fails.** Serial current-state convergence
was 9.98 seconds, fresh-command RTT reached 8.49 seconds, and workload scheduling
lag reached 75.34 ms. An earlier concurrent run also exceeded convergence time
(11.89 seconds, 10.77-second command RTT, 95.95-ms lag). Thresholds were not
relaxed; both healthy controls passed. This addresses the prior 160-ghost /
121-missing-ACK abandonment, not bounded recovery latency, flow control,
prioritization, fleet capacity or radio qualification.

Detailed evidence and limits: [policy retest](../spikes/instance_churn_reconnect/README.md#two-minute-outage-with-the-300-second-policy-2026-11-11).
The persistent router binary and retained schema-3 EMANE mesh were **not**
overwritten or restarted. Rebuild/deploy code and XML together through the
owned lifecycle before claiming fleet adoption; full current live missions
and load qualification remain open.

### 3.29 Native asynchronous topic-priority experiment (2026-11-12)

The isolated churn spike now compares production asynchronous Event publishing,
XML HPF pacing with equal priorities, per-sample topic priorities, and independent
SQL channels inside the same Event writer. Commands and ACKs default to 100;
ContactReport data and disposals default to 10. CLI overrides are keyed by
`original_topic`. Fixed scheduling/token/channel QoS remains in spike XML;
production router code/QoS and the retained mesh are unchanged.

Four native on-demand ordering assertions show that raising sample priority
alone does not reorder the single FIFO, whereas per-topic channels deliver
a newly queued high-priority command before 400 contacts. Inverting its priority
puts it last. All 401 samples arrive exactly once and contact ordering remains
intact. Eleven host tests, native XML/controller introspection and a same-key
dispose/revival control with independent 200/150/5 priorities pass.

Four serial 120-second outage runs each pass their healthy control but **fail
their outage acceptance gate**. Unpaced production recovers all state/ACKs,
but convergence is 10.99 seconds and worst fresh-command RTT is 9.16 seconds.
The equal-priority and sample-priority paced runs receive no fresh-command ACKs
by the end and leave 5,408 / 6,152 unacknowledged samples. Per-topic channels
acknowledge all 180 commands (39 strictly after restoration), with a worst
fresh-command RTT of 2.39 seconds, still over the 2-second target.

The channel run still has 7,296 unacknowledged samples and incorrect terminal
contact state when observation ends. None of the paced cases converges within
the unchanged window; none is silently passed or coalesced. No reader becomes
inactive. The one-token, 2,500-byte/10-ms allowance is a nominal 2 Mbps DDS
ceiling per participant, not measured usable radio capacity; whole-token
consumption, packet size, multiple destinations and repair affect throughput.

This proves limited native scheduling behavior, not a production solution.
The channel catalog covers only the spike's three logical Event topics.
Actual-router integration, complete branch/lifecycle/filter coverage, reliable
repair/recovery capacity, starvation/resource budgets, health interference
and current fleet/live missions remain open. See the [experiment and retained
evidence](../spikes/instance_churn_reconnect/README.md#asynchronous-topic-priority-experiment-2026-11-12).

### 3.30 Generic priority bands, configuration-only topic selection (2026-11-12)

The isolated spike adds `priority_bands`: two static XML channels use the
installed `DDSPRIFLTR` filter with `@priority >= 100` and `@priority < 100`.
Topic-to-priority defaults live in the spike's configuration helper, with
CLI overrides. The write/dispose path stamps that value; DDS selects the
band without topic names in its channel XML. The existing per-topic mode
remains available as a comparison.

Twelve host tests and twelve native checks pass: single/topic-channel controls,
band boundaries including priority 0, same-band FIFO, and remote CFT
write/dispose/revive at priorities 0 and 100. A configuration-only inversion
of contacts versus command/ACK priorities passes the 24-second revival control.
This proves high versus normal selection, not arbitrary ordering within a band
or safe live migration between bands with outstanding lifecycle operations.

The healthy control passes; the 120.007-second outage **still fails acceptance**.
All 180 commands and 39 fresh probes are acknowledged, but worst fresh RTT is
2.17 seconds, above 2 seconds. Contacts do not converge: 159 ghosts, 160 missing
identities and 7,296 pending reliable samples remain. No reader becomes
inactive and producer lag is 3.21 ms. The nominal controller budget and all
thresholds are unchanged; this is not a qualified recovery-capacity solution.

Status remains separate, with no global lower-than-Event ordering or health
priority guarantee. The production Config tab is not wired to edit the spike's
map; there is no production deployment. See [design, commands and evidence](../spikes/instance_churn_reconnect/README.md#simplified-priority-bands-2026-11-12).

### 3.31 Native recovery packing and stronger Event-history gate (2026-11-12)

Historical measurements below precede the self-ignore correction in §3.32.
Their extra local-recipient overhead is not the corrected production baseline.

Isolated two-minute outage measurements allow 180 seconds of terminal drain
without relaxing the original 10-second recovery/2-second command RTT targets.
Equal nominal 2 Mbps coarse/fine token controllers converge current contacts
in **72.664/71.828 seconds**, with worst fresh RTT **2.023/2.565 seconds**.
Both receive all 14,561 valid source contact writes and all 180 command ACKs,
but still fail latency acceptance. Smaller token granularity is not a measured
recovery improvement. A larger burst allowance has only a healthy control.

Native batching (four samples/2048 data bytes, 10-ms flush, timestamp resolution
zero) converges final contact state in 64.627 seconds but worsens fresh RTT to
15.846 seconds and observes only 2,161 of 14,561 valid writes. **Reject it.**
The final-state oracle permitted absent already-disposed instances and therefore
masked missing history. Event now explicitly requires every valid source write
sequence, including seeded state; invalid lifecycle notifications do not count.
A separate 12-second outage reproduces 696 missing batched writes, while the
unbatched control observes all 1,441 writes. Neither outage passes the original
command RTT budget; healthy controls pass. Sixteen host tests and sixteen native
scheduling/lifecycle checks pass. The missing-history cause is not isolated.

Bounded RTPS captures show mostly single disposals and occasional paired
disposals in packets, not application-level disposal aggregation. They also
show extra ordinary ContactReport DATA sent to the local ACK-only participant
under both topic-SQL and priority-band MultiChannel, then filtered locally.
Ordinary non-MultiChannel data mostly avoids this copy; disposal copies remain.
This is topology-specific evidence, not a universal MultiChannel/CFT rule.
Qualify sender filtering/fan-out before adopting the priority prototype.

Next targets are usable recovery capacity, explicit command/health/recovery
budgets, and separately measured repair tuning. Do not silently drop retained
Event history or introduce latest-state/tombstone semantics. Status stays
outside the Event controller. Raw captures are separate under `debug/pcap`;
frozen reports and retrospective sequence analysis remain under
`debug/test_reports`. No production/router/live mesh changes are deployed.
See [commands, evidence and limitations](../spikes/instance_churn_reconnect/README.md#recovery-pacing-disposal-packing-and-batching-2026-11-12).

### 3.32 Two-sided native WAN self-ignore (2026-11-12)

Production already calls `dds::pub::ignore` for its own WAN writers; the native
spike omitted ignore. Tests establish that publication ignore suppresses local
application delivery but does not remove local readers as writer destinations.
Complementary `dds::sub::ignore` removes those destinations. Production now
applies it when creating enabled WAN readers and after deferred Status startup
enablement. The spike applies both directions and requires one remote match.
Remote delivery remains intact; no extra topic, partition or fixed QoS change
is introduced. This resolves the extra local traffic reported in §3.31 without
establishing a general MultiChannel filtering defect.

Comparable four-second healthy controls reduce source Event DATA-bearing UDP
bytes from 331656 to 166292, with no pending reliable samples. Publication-only
ignore leaves 565 pending samples. The corrected two-minute outage receives
all 14,561 valid contact writes and all 180 command ACKs; worst fresh RTT is
13.645 ms over 39 probes. Contacts converge after 60.342 s and the terminal
queue drains at 60.498 s. **The 10-second contact gate still fails.**
Corrected batching also still fails: 720 of 1,441 valid writes are missing in
a 12-second outage, and fresh RTT exceeds 2 seconds.

Validation passes: 16 host tests, 19 native scheduling/lifecycle/self-ignore
checks, 9 freshly built C++ tests, 11 production adapter DDS cases and one
controller all-application-case test. Failed initial environment attempts
(missing Python dependencies/read-only test log path) are retained; targeted
tests subsequently pass with restored dependencies/private logs. This is not
a full-suite or live fleet qualification. Builds use disposable storage;
the running mesh and its deployed binary are unchanged. See
[commands, evidence and corrected measurements](../spikes/instance_churn_reconnect/README.md#correct-native-self-ignore-on-both-sides-2026-11-12).

### 3.33 Larger repair-response experiment (2026-11-12)

The isolated `priority_bands_repair` XML profile changes only
`max_bytes_per_nack_response` from 128 KiB to 512 KiB. Corrected self-ignore,
nominal 2 Mbps pacing, priority bands, KEEP_ALL history and heartbeat/timeout
policy remain unchanged. Native effective QoS confirms the requested value.
The same two-minute outage observes all 14,561 valid contact writes and all
180 command ACKs. Healthy control passes, but contact convergence is 61.580 s
versus the corrected baseline's 60.342 s. Worst fresh RTT is 2.315 s versus
13.645 ms. Contact recovery still fails the original 10 s gate; the candidate
also fails the 2 s command gate. Do not adopt it. One run each does not establish
causation for the RTT difference.

Both captures have the same full request bitmaps in frames containing normal
channel ACKNACKs: 62 x 256 bits and one x 128 bits. The bigger response cap
does not enlarge repair requests; this is consistent with the original cap
not binding, not direct per-response-byte proof. Measure request-window/cadence
and pacing next. Seventeen host tests and 24 native controls pass, including
candidate priority boundary, lifecycle and self-ignore checks.

Event remains KEEP_ALL. KEEP_LAST(1) writer depth also counts lifecycle
notifications; it is not an independent dispose policy or per-channel retention
setting, and cannot be applied only to contact state within the shared command
writer. No production QoS, binary or live mesh is changed. See
[commands, comparison and references](../spikes/instance_churn_reconnect/README.md#larger-repair-response-limit-2026-11-12).

### 3.34 KEEP_LAST(1) writer experiment (2026-11-12)

The isolated `priority_bands_last1` profile inherits the 512 KiB repair
candidate and overrides only writer history. Native QoS confirms KEEP_LAST
depth 1 on contacts/commands and ACK writers; readers remain KEEP_ALL.
Priorities, pacing, self-ignore and the two-minute outage workload are unchanged.

Final current contact state is correct and receipt-triggered convergence is
12.929 s; worst fresh command RTT is 13.726 ms over 39 probes. But only 60/180
commands are acknowledged, and only 4,489/14,561 valid contact writes are
observed. Peak unacknowledged count falls to 2,628 and final count is zero.
An empty queue is not proof of complete delivery: KEEP_LAST overwrites older
samples of the same key, including commands. Required-write, ACK and 10 s
current-state gates still fail. No production QoS/live change is made.

The original run allowed 180 s final observation and still had 120 missing ACKs
and 10,588 missing valid writes. Its 100-ms convergence sampling was aligned
near producer ticks, reporting 39.985 s while missing intervening current states.
The probe now also evaluates current-state convergence after receipt processing,
preserving the oracle and thresholds. The refined repeat allows 10 s final
observation after the long run established missing operations; its measured
recovery precedes this final allowance. Reports retain distinct run/method labels;
do not treat all differences as caused by timing-instrumentation changes.

Eighteen host tests and 32 native controls pass. Queued seeded-instance tests
prove latest update replacement, dispose replacing pending writes, and revive
replacing pending dispose. Commands share an instance key in this workload,
so changing the shared writer wholesale is not a viable required-event policy.
This does not qualify late joining/restart or permanent tombstone retention.
See [profiles, probe correction and evidence](../spikes/instance_churn_reconnect/README.md#keep_last1-latest-state-experiment-2026-11-12).

### 3.35 Isolated Status / Contacts / Event alternative (2026-11-12)

The user authorized a [three-topic spike](../spikes/three_topic_wan/README.md)
after shared-writer KEEP_LAST lost required commands (§3.34). Status retains
best-effort latest-state behavior; Contacts uses reliable KEEP_LAST(1) on its
own writer/reader; Event retains reliable KEEP_ALL commands and ACKs.
The prototype reuses schema-3 WanEvent on Contacts and Event, leaving production
IDL, mapping, router binaries and the running two-topic mesh unchanged.

Each participant has one writer/reader per physical topic, verified through
native endpoint policies and matched-publication discovery metadata.
Publication and subscription self-ignore leave exactly one remote match.
Independent Event/Contacts writers use one shared XML-configured HPF controller
per participant at nominal 2 Mbps, with sample priorities 100/10 and **no
MultiChannel**. Status remains outside that pacing budget. The prior 512 KiB
repair cap is retained for this experimental comparison, not adopted in production.

Twenty host tests, nine new native controls and all 32 prior native controls
pass. Controls cover actual history policies, exact endpoints, self-ignore,
priority reversal, update/dispose/revive replacement and deliberate missing-ACK
rejection. The old mixed KEEP_LAST case still rejects required-write/Event loss.

With the same 20 s baseline / 120 s blackout / 40 s continued-production workload:

- All **180 commands and 180 ACKs** arrive, with no duplicate/unexpected IDs.
  The 121 probes issued while impairment was active are all acknowledged within
  0.673 s after restoration. Worst fresh-command RTT is **14.124 ms**.
- First current-state catch-up is **19.110 s**, so the original **10 s gate
  fails**. Final contact state is correct; aggregate reliable backlog drains to
  zero. 4,049/14,561 valid contact writes are observed; intermediate contact
  updates may coalesce under this explicitly different latest-state contract.
- Producer lag peaks at 20.902 ms. First restored health receipt is after
  0.984 s; maximum restored health age is 3.106 ms. This is native sample-receipt
  evidence, not production presence/health or fleet qualification.
- The matched 60 s healthy reference passes with all 60 command/ACK pairs.
  Captures explicitly label combined Source Event/Contacts DATA, not per-topic
  bandwidth or a complete RF budget.

The negative recovery gate remains in JSON/JUnit and the nonzero exit status.
This proves required-event preservation for the bounded prototype workload,
not faster convergence than every previous configuration, complete restart or
permanent tombstone behavior. The new reader history, queue layout and endpoint
inventory differ from the lossy shared-writer experiment; do not infer a single
cause for timing differences. Evidence and remaining scope are in the spike.

#### 3.35.1 Controlled recovery timing investigation

The original 12.929/19.110 s observations did not isolate reader history,
MultiChannel, input XML changes or run variability. Closed captures show the
19 s run has an extra 5.815 s contact repair-request gap; total time in long
request gaps grows by 5.891 s, accounting for most of the 6.181 s difference.
Source application DATA continues during the pauses.

Two repeats from common frozen input/image snapshots produce:

| Layout | Reader history | MultiChannel | Contact catch-up |
|---|---|---|---|
| Original mixed KEEP_LAST writer | KEEP_ALL | Priority bands | 7.675 / 11.418 s, but 120 / 122 missing command ACKs |
| Three-topic | KEEP_ALL | Priority bands | 14.655 / 16.310 s; all 180 command/ACK pairs |
| Three-topic | KEEP_ALL | None | 13.585 / 13.110 s; all 180 pairs |
| Initial three-topic prototype | KEEP_LAST(1) | None | 13.869 / 19.331 s; all 180 pairs |

Thus an unchanged configuration itself spans more than five seconds. Removing
MultiChannel is not required and is not proven to explain the old jump.
A separate paired experiment retains MultiChannel/KEEP_ALL readers and changes
only Contacts heartbeat suppression from 62.5 ms to zero. Candidate catch-up is
12.227/11.781 s, still with roughly three-second request gaps; unchanged paired
controls are 12.646/28.210 s. The slow control also breaches the 2 s command RTT
budget at 2.254 s. Do not omit that negative result or claim a complete fix.

All ten three-topic full-outage trials preserve 180 command/ACK pairs, correct
final state and zero pending samples, but fail the original 10 s state gate.
The two sequential-case batches overlap on this shared VM in private network
namespaces; two repeats are not a dedicated-host statistical benchmark.
Twenty-two host tests and nine native controls pass. No production settings
are changed by this investigation. The original no-MultiChannel prototype is
left intact; `bands_all` exposes the least-change MultiChannel comparison.
See [methods, traces and retained negative gates](../spikes/three_topic_wan/README.md#why-129-s-became-191-s-controlled-investigation).

### 3.36 Sparse optional health diagnostics (2026-11-12)

Superseded by the user's reliable Event requirement in §3.37; retained below
as the earlier best-effort implementation/evidence record.

Live Platform_30 WAN capture measured a 20,548-byte serialized Status health
sample at 1 Hz with six recipient copies. Its reader-filter SQL/parameters
contained 17,137 bytes of text. The compact-health work in commit `108adfb`
predates the writer-observed filter diagnostics added in `1554f6f`; repeating
those diagnostics accounts for most of the later heartbeat growth.

`RouterHealth.wan_writer_filters` is now optional in IDL and regenerated XML.
The sender polls every heartbeat but includes a snapshot only on its first
successful publication, semantic changes, or a 30-second refresh. Poll timestamps
alone do not count as changes; failed writes do not advance the send baseline.
This keeps diagnostics on the existing best-effort Status health branch, not a
new reliable configuration event or additional WAN endpoint. These observations
describe runtime reader interests, not operator configuration.

Omission retains the last snapshot only within the same health session.
Present-empty explicitly clears it; a new session without diagnostics discards
the old snapshot. LAN mesh summaries retain complete cached details and report
independent snapshot ages, so compact heartbeats cannot make old details appear
fresh. The browser marks these snapshots stale after 35 seconds; old routers
without age metadata retain the previous 8-second health-age fallback.
Best-effort snapshots can be lost. A late reader may wait until the next refresh,
or longer under loss; this is not guaranteed delivery of every change.

Fresh private builds passed all **9 C++ targets**, **4 filter DDS tests**,
**3 additional DDS regressions** (health/RTT/stale/dead/restart, every application
branch, and legacy presence), and **5 browser checks**. DDS tests ran in isolated
network-disabled containers, not against the retained mesh. The 34-second
cadence check saw 33 compact samples and one snapshot. Size evidence:

- Native constructed 14-reader RouterHealth: 19,888 bytes with details versus
  520 bytes omitted; optional absent/present/empty CDR round trips also pass.
- Minimal three-router DDS Status: 1,716 bytes with details versus 1,144 compact.
- Current live Platform_30 health content, reserialized offline with updated
  IDL: 20,216-byte RouterHealth versus 1,304 bytes with diagnostics omitted,
  saving 18,912 bytes. These are health-body sizes, not the earlier full WAN
  envelope measurement. Large unsigned IDs were preserved through typed setters
  because the native JSON parser rejected their numeric JSON representation.

Compact evidence is retained locally under `debug/test_reports/optional_health_*`.
The dashboard still renders old deployed filter data correctly. **Router/IDL
changes are not deployed:** all seven live containers and their deployed binary
are unchanged. Deployment needs an owned coordinated rebuild/restart with
consistent generated types; mixed old/new participant compatibility, fleet
bandwidth reduction, full regression/load and live mission gates are not claimed.

### 3.37 Reliable RouterConfig on Event (2026-11-12)

The user explicitly requested reliable filter-change delivery as a typed router
configuration message on Event, replacing §3.36's best-effort health snapshots.
`RouterConfig` is now Event union branch `router_config` (discriminator 6,
member ID 60), with router identity, config hash, health session, snapshot
sequence, timestamp, state revision and the full writer-observed filter list.
The exact catalog now has 16 cases. Existing branch IDs, application contracts
and health/RTT transport remain unchanged.

Presence binds an internal config observer to the existing shared Event
writer/reader using external `wan_event` RELIABLE/KEEP_ALL QoS. It creates no
additional WAN topic or per-config endpoint. Presence-only nodes now retain
both Event and Status pairs; controller interest replacement and route teardown
preserve the internal config subscription even without application destinations.
Event deferred-startup publication/subscription ignore mirrors Status, avoiding
a local reader destination. Application routes cannot claim RouterConfig.

The sender polls discovery each heartbeat, publishing the first snapshot,
semantic changes and a 30-second full refresh. Timestamp-only changes do not
publish; failed writes are logged without advancing the sequence/baseline.
Every WAN health heartbeat omits filter details. The optional health field now
exists only for LAN mesh cached views, preserving dashboard consumers and their
independent snapshot-age behavior. This is sampled configuration state, not an
exhaustive audit of intermediate changes between discovery polls.

Receivers validate payload/envelope identity and audit metadata, cache config
before matching health arrives, and apply only matching-session snapshots.
The cache is bounded by the existing peer cap and two sessions per router.
Duplicate/older sequences cannot roll back or freshen the cache; an empty list
clears it. A new health session cannot inherit old details. Config reception
does not assert ALIVE, reset health deadlines, or contribute heartbeat RTT.
Malformed config metadata is warned and dropped without poisoning Event routing.

Reliability uses the existing Event policy, not a new unlimited-delivery promise.
Durability remains VOLATILE; late/rejoining readers recover a new full refresh,
not old history. A successful write means accepted into the DDS queue, not
operator acceptance. Retention, inactive-reader retries, backpressure and
participant lifetime still bound delivery. No fixed QoS policy was retuned.

Verification:

- Fresh private Connext 7.7.0 / `x64Linux4gcc8.5.0` build: **9/9 C++ targets**,
  including the extended catalog's whole-structure/CDR/type-equality checks.
- **8/8 isolated DDS cases**, zero skips (243.63 s): compact-health cadence,
  config-before-health/session/empty/replay handling, filter changes and reader
  departure/restart, unchanged all-application routing, shared health/RTT/
  stale/dead/restart, and legacy presence. The final malformed-config/cache
  regression was rebuilt and rerun separately: **1/1 passed** (9.41 s).
- Native publication discovery verifies RELIABLE config writers. An explicitly
  owned `--network none --cap-add NET_ADMIN` test drops 100% of loopback packets
  for 32 seconds and recovers a config whose send timestamp is inside the loss
  interval within 12 seconds of restoration, before the next refresh.
  Run this case only with `ROUTER_CONFIG_LOSS_TEST=1` in an isolated network.
- Stable 34-second observation: 33 compact Status heartbeats (1,144 CDR bytes)
  and one reliable Event config (1,496 bytes) in the minimal three-router rig.
  These are not deployed fleet measurements.
- **5/5 Chromium dashboard checks** preserve the LAN cached-details/freshness
  view and traffic graph. No frontend asset change is required.

Compact evidence is local under `debug/test_reports/router_config_*`.
The existing parallel IDL-generation build race was avoided with a final serial
private rebuild; production CMake was not changed. This source gate did not
modify the live mesh; the subsequent authorized deployment is recorded in §3.38.
Mixed-type interoperability, fleet load/command interference and full live
mission qualification remain separate gates.

### 3.38 Reliable RouterConfig deployment (2026-11-12)

The user authorized deployment and restart. The owned EMANE lifecycle stopped
all seven nodes before replacing the persistent router binary. A fresh serial
Connext 7.7.0 / `x64Linux4gcc8.5.0` build passed **9/9 C++ targets**, then
Control_20 and Platform_30 through Platform_35 restarted together with regenerated
types, `two_topic_union`, and the dashboard on port 8080. No rolling mixed-type
restart was attempted. The retained workdir is `debug/mesh_runs/emane`.

This deployment also activates the previously tested two-sided native self-ignore
correction (§3.32) and unified timeout policy (§3.28): `WAN_TIMEOUT_SEC=300`,
`WAN_HB_PERIOD_SEC=3`, 100 derived reliable-reader retries. The live Config API
and router startups confirm the policy. Experimental Contacts topics, KEEP_LAST,
repair-response limits and priority profiles from the isolated spikes were not
selected.

Restored operator settings:

- TEAM_B: Platform_30, Platform_31 and Platform_35.
- TEAM_A: Platform_32, Platform_33 and Platform_34.
- All six simulation clocks: **20x**; all platforms idle at zero speed.
- All six peers **PRESENCE_ALIVE / ROUTER_OK**; EMANE controller available,
  impairments empty. No active mission was present before restart; transient
  completed mission history was not restored.

A bounded 38-second capture on Platform_30's owned EMANE interface included
noninitial IP fragments so large samples could be reassembled. Offline native
DynamicData decoding, with no live DDS observer added, verified **287 unique
samples**: 39 Status health samples and two Event config samples from each of
seven routers. Every health omitted `wan_writer_filters`; every config contained
both writer families. Platform health is **1,648 CDR bytes**, versus the earlier
20,548-byte live sample; Control_20 health is 1,416 bytes. Full platform config
is 16,772 bytes, Control_20 config 18,924 bytes. These are serialized sample
sizes, not per-destination UDP/IP overhead or RF airtime.

Forty seconds of dashboard traffic observations (41 samples), including the
30-second config refresh cycle, report **1.219 Mbps mean fleet WAN data TX**
across all seven nodes (0.891-4.206 Mbps sampled range). All samples report
complete, nonstale capture/classification and all seven contributors. The
pre-restart single sample was 7.397 Mbps; differing observation windows and the
simultaneous self-ignore fix prevent treating this as a controlled single-change
benchmark or a per-platform measurement.

Host-local Chromium selected Platform_30, rendered both Event/Status filter
families with six remote readers each, expanded actual SQL, and reported zero
page errors. The screenshot confirms the restored team audience and independent
snapshot age. The separate integrated browser still could not reach VM localhost;
this is not evidence that local REST or the deployed dashboard is unavailable.

Compact evidence: `debug/test_reports/router_config_deploy_{wire,traffic,browser}.json`,
`router_config_deploy_dashboard.png`, and `router_config_deploy_native.log`.
The mesh remains running for inspection. No commit/push or full live mission,
fleet-load, congestion/interference, or mixed-type qualification was performed.

### 3.39 RouterConfig cache review corrections (2026-11-12)

Review reproduced two diagnostic-cache defects in isolated real DDS runs:
evicting a health-current session lost its sequence watermark, allowing an old
snapshot to overwrite a newer authoritative empty snapshot; config-only router
identities that never entered the roster could fill all 256 cache slots and
permanently prevent a new healthy peer from caching config.

The source fix retains the current health session when evicting another session.
It also evicts the oldest config identity absent from the roster when admission
reaches capacity, using the latest accepted receipt across that identity's
sessions. Eviction logs `router_config_cache_evicted`. Roster peers remain
protected until existing dead-peer pruning; if no unseen entry is available,
`router_config_cache_full` still rejects admission explicitly. Limits remain
256 identities and two sessions per identity. There is no new expiry timer,
IDL/XML change, QoS policy, endpoint, or routing-filter change.

Permanent parameterized tests cover the original early-config/session/empty/
malformed-input behavior, replay after several session admissions and a current
session change, unchanged snapshot age after rejected replay, and a 256-identity
config-only population followed by protected-peer replay and fresh config-before-
health admission.

Verification:

- Deployed pre-fix binary, private network: **1 passed, 2 failed**, demonstrating
  both permanent regressions.
- Fresh serial private Connext 7.7.0 / `x64Linux4gcc8.5.0` build: **9/9 C++ targets**.
- Final config DDS suite: **7/7 passed**, zero skips (123.10 s), including the
  strengthened cache regressions, unchanged compact-health cadence, reader
  departure/rejoin and repair of a config sent during 32 seconds of 100% loss.
- Shared Status health/RTT/stale/dead/restart and legacy presence regressions
  passed in the initial combined run. That run had eight passes and one
  existing filter-test startup race: its first config revision was older than
  the final discovery revision. The same failure reproduced on the deployed
  binary. The test now requires all expected WAN matches and config/RouterStatus
  revision agreement before taking its baseline, preserving the strict later
  revision-equality assertion. The corrected test passed against both binaries,
  and the final seven-case run was clean.

Compact evidence is local under `debug/test_reports/router_config_cache_*`.
All DDS runs used owned network namespaces and temporary state. The persistent
router executable and the seven-node mesh from §3.38 were not overwritten or
restarted; these fixes are **source only**, pending an owned coordinated rebuild/
restart. No commit/push, full fleet-load or live mission gate is claimed.

### 3.40 Selected three-topic router baseline (2026-11-13)

The selected baseline is `three_topic_union`, with default reader heartbeat
suppression, not the zero-suppression candidate. The C++ implementation keeps
envelope schema version 3 and application LAN contracts intact; only ContactReport's physical WAN
selection changes from Event to Contacts. Both physical topics carry WanEvent;
Status retains WanStatus and the existing health policy.

- Event: reliable KEEP_ALL; Contacts: reliable writer KEEP_LAST(1), reader
  KEEP_ALL, default 62.5 ms suppression.
- Event/Contacts share an external-XML nominal 250,000 B/s highest-priority-first
  token bucket (2,500-byte token every 10 ms), with the tested priority bands
  and 512 KiB repair-response cap. Event sends, including RouterConfig, use
  priority 100; contact data, dispose and unregister use 10.
- Status does not join this pacing budget. Health-versus-periodic Status priority
  remains a separate optimization.
- Physical mapping is separate from the codec catalog. Three-topic Event filters
  exclude ContactReport, Contacts filters select only ContactReport, and shared
  endpoints preserve native two-sided self-ignore, route generations, detach
  barriers and alias deduplication. Contacts keeps one writer/reader pair even
  on nodes with no active contact interest.
- The existing mixed `platform_events` logical route does not split. Its
  `wan_event` hint remains valid; catalog-selected Contacts uses `wan_contacts`.
  Legacy and two-topic selections keep their profiles and transport mapping.
- The canonical EMANE launcher exposes `--wan-contract three_topic_union`;
  Config/Delivery and physical filter/statistics evidence include Contacts.

The user approved expanding the administrative `wan_writer_filters` sequences
in RouterHealth's LAN cache and RouterConfig from two entries to three. IDL
and committed XML are regenerated; native round-trip tests cover three entries.
Without that expansion, every three-topic RouterConfig publication explicitly
failed its bounded sequence capacity. This is a type-bound change requiring
the coordinated rebuild/restart, not a rolling mixed-type deployment.

The selected spike recovered in **19.119 s median (19.098-20.302 s)** across
three 120-second outages, retaining all 180 commands/ACKs in each. It did not
meet the ten-second target. A third topic with KEEP_ALL alone did not improve
recovery; independent latest-contact retention is the measured benefit.
These are isolated workload results, not deployed fleet-load guarantees.

Source gate so far: fresh supported Connext build and **9/9 C++ targets passed**;
**16/16 three-topic production DDS tests passed** (257.85 s), zero skips.
The gate covers all application mappings, exactly three physical writer/reader
pairs per node, keys/origin, dispose/unregister/revive, aliases, team/addressed/
broadcast rules, rejected types, shared health/restart, compact health/reliable
config and the selected native QoS. A three-second stopped receiver recovered
all 180 ACK Events, the newest contact state and a previously seen contact's
dispose. Its LAN producer/observer also use KEEP_ALL so the test cannot overwrite
Events in a 16-sample test queue before measuring WAN retention.

Launcher/display/config unit gate: **54 passed plus 159 subtests; four browser
checks skipped** in the disposable native test image. Both launcher transports
and all three contract selectors were rendered. Earlier combined development
runs retained failures: two-slot diagnostics, insufficient test observer history,
missing copied harness helper, and exhaustion of reserved domain triples.
Final DDS suites use separate owned network namespaces rather than raising the
232 domain ceiling.

Completed regression gates: **18 two-topic/controller/config tests passed**
(290.67 s), with one opt-in packet-loss skip; that case then passed separately
with explicit NET_ADMIN in its private namespace (39.06 s). **Three legacy
presence/QoS/config-drift tests passed** (23.56 s). The four image-only browser
skips passed on host Playwright (3.73 s).

Deployment used the owned lifecycle: stop all seven containers, rebuild the
persistent router and pass all nine C++ targets, then relaunch Control_20 and
Platform_30-35 with `--wan-contract three_topic_union`. The deployed binary SHA256
is `473f2b0c00dabfd7c438db37a58b9bc6e7d8075cfcc86c47b31bab5984ace65b`.
Native discovery confirms exactly one Event, Contacts and Status writer/reader
pair on every router. All seven live configuration caches report all three
writer families. Browser verification confirms the three-topic label, six QoS
rows, Contacts KEEP_LAST(1), the current script revision and zero page errors.

The [initial live mission gate](../debug/test_reports/three_topic_baseline_initial_live_failures.xml)
retained **two failures and three passes**:

- The wire decoder accepted DATA/DATA_FRAG but silently omitted RTI DATA_SESSION
  packets used by the reliable writers. A bounded capture showed 100 such
  packets. The decoder now accepts that header and uses the physical wire/session
  sequence for delivery and repair identity, not its additional virtual sequence.
  New parser and requested-repair regressions failed before the fix and pass
  afterward; all **100/100 captured packets** decode with their CDR payloads.
  Host wire/launcher regressions: **42 passed plus 107 subtests**, with 16
  SDK/binding-dependent skips. The owned mesh was stopped/relaunched to reload
  the Python monitor; its qualified C++ binary and QoS were unchanged.
- The team mission test assumed TEAM_A initially had no members, but the
  preserved operator state already assigned three other boats to it. The fixture
  now temporarily clears team membership through the supported API and confirms
  convergence, then restores the original teams/clocks even after a failure.
  No mission routing assertion was weakened.

Both targeted workflows then passed (46.83 s). The final complete live browser
gate is **5/5 passed, zero skips, 355.79 s**:
[JUnit](../debug/test_reports/three_topic_baseline_live_missions.xml) and
[screenshots](../debug/test_reports/three_topic_baseline_mission_screenshots/).
The mesh remains running on port 8080. Final checks confirm all six peers
PRESENCE_ALIVE/ROUTER_OK, every mission CANCELLED with zero speed, original
TEAM_A/TEAM_B assignments and 20x clocks restored, and empty impairments.

Wire capture coverage includes all seven nodes with zero invalid records.
This is not a clean-log or complete-attribution claim: startup ActRouterStatus
not-enabled writes are diagnosed/skipped, historical native matched-participant
warnings are not qualified as resolved, and ambiguous SEDP attribution can leave
TX classification incomplete.
The selected baseline still misses the ten-second recovery target; health
priority, fleet-load/admission budgets and broader lifecycle qualification remain
open. These targeted gates do not constitute a fresh full-suite result.

### 3.41 Semantic WAN topic names (2026-11-13)

The user approved renaming the actual three-topic WAN topics to match the
[delivery-semantics slide](slides/event-state-telemetry.md), including dashboard,
tests/docs and a coordinated live rebuild/restart after qualification:

| Earlier physical name | Semantic physical name | Unchanged delivery policy |
|---|---|---|
| Event | Event | Required occurrences; reliable KEEP_ALL |
| Contacts | State | Current ContactReport instance; reliable writer KEEP_LAST(1), reader KEEP_ALL, native lifecycle |
| Status | Telemetry | Fresh periodic observations and health; best effort, freshness first |

This affects `three_topic_union` only. Legacy names and the two-topic Event /
Status contract are unchanged. The whole-structure payload catalog, schema
version 3, LAN topics/types, keys, audience rules, priorities, pacing, suppression
and health timing remain unchanged. The internal `WanEvent`/`WanStatus` types
and existing `wan_contacts`/`wan_status` QoS aliases are deliberately retained:
State uses the original WanEvent contact branch; Telemetry uses WanStatus.
Physical topic selection is separate from those historical implementation names.

No transient-local durability, State catalog, generic telemetry model or new
payload classification is introduced. Historical comparison reports/spikes retain
the names actually measured; their policy findings still apply to this rename.
The slide's Markdown/PDF/PowerPoint now identify the implemented names while
retaining the separate bootstrap/lifecycle qualifications.

Mixed old/new three-topic participants will not exchange State/Telemetry topics.
Deploy through the owned all-node stop/build/up lifecycle, never an incremental
router restart or edit to generated live configs.

Completed source gate: a fresh supported build and **9/9 C++ targets**, then
**16/16 three-topic DDS tests** (256.40 s, zero skips). These cover exact semantic
publication/subscription names, all application branches, source identity,
native lifecycle, retained Event/latest State, audiences, health/restart and
compact health/reliable configuration. **Three targeted two-topic regressions
passed** (166.09 s): unchanged Event/Status endpoint names and application routes,
health/restart, and configuration transport. Native launcher/dashboard/wire
checks: **56 passed plus 163 subtests**, four image-only browser skips; all four
then passed with host Playwright (3.59 s). These are targeted gates, not a fresh
full-suite claim.

The owned lifecycle stopped all seven containers, rebuilt the persistent router
and passed **9/9 C++** again, then relaunched Control_20 and Platform_30-35.
Deployed SHA256:
`8dfdfcd5afd692c569c2296495ab00e332098293f507aee98913220b0eeafe44`.
[Native discovery](../debug/test_reports/semantic_wan_discovery.json) confirms
exactly one Event, State and Telemetry writer/reader pair per router, with no
Contacts/Status application endpoints. All seven live configuration snapshots
contain those three families. Actual browser DOM checks verify six QoS rows,
State KEEP_LAST(1), best-effort Telemetry, Telemetry presence/recovery labels,
the updated chart asset, and the three names in **Observed WAN filters**.
No browser page errors were observed.

The final live mission gate is **5/5 passed, zero skips, 377.12 s**:
[JUnit](../debug/test_reports/semantic_wan_live_missions.xml),
[mission screenshots](../debug/test_reports/semantic_wan_mission_screenshots/),
[configuration](../debug/test_reports/semantic_wan_config.png) and
[observed filters](../debug/test_reports/semantic_wan_filters.png).
The mesh remains running at port 8080 in `debug/mesh_runs/emane`.
[Final checks](../debug/test_reports/semantic_wan_final_state.json) confirm all
six peers ALIVE/ROUTER_OK, missions CANCELLED at zero speed, original teams and
20x clocks restored, empty impairments, seven capture contributors and zero
invalid wire records. Incomplete SEDP TX attribution and earlier log caveats
remain; the rename does not settle health priority, transient-local bootstrap,
the ten-second recovery target or broader load/lifecycle gates.

### 3.42 Review fixes: failed binds and timeout summary (2026-11-13)

The full review found two reproduced regressions; both are fixed and deployed:

- A failed first destination bind could change the retained State reader's
  receive filter before LAN writer creation failed. Destination binding now
  allocates its binding record, output and target contribution before committing
  shared interests. The filter update is the final fallible step, under the
  receiver mutex; failed setup removes staged contributions and output leases.
  Successful filter updates use non-allocating swaps for the cached policy.
  The native pilot now supports three-topic mode and checks missing-alias and
  invalid-filter failures, unchanged filters/resource counts, then successful
  retry and exact ContactReport delivery with authoritative source restoration.
- The configuration summary selected `Last Status to DEAD` but omitted
  `Last Telemetry to DEAD`. Both labels are now supported alongside the legacy
  countdown. Browser coverage checks the Telemetry card and all three cards;
  `config_tab.js` uses cache revision `20261113-telemetry-timeout-v4`.

Both regressions failed before the fixes. Qualification passed **9/9 C++ targets**
and **30/30 native DDS tests**, zero skips (337.71 s): all adapter pilots, the
three-topic controller module and two targeted two-topic routing/interest cases.
The host display/parser/policy run passed 33 checks and skipped two SDK-only
checks; those exact two passed separately in the supported Connext container.
Browser coverage ran on the host with real Chromium. DDS tests used a private
network-isolated container and a separate build, never the running mesh.

The user explicitly approved interrupting active ORBIT missions on Platform_30,
Platform_31 and Platform_35. The owned all-node down/build/up lifecycle rebuilt
the persistent router and passed **9/9 C++** again. The deployed binary is
byte-identical to the isolated qualified build, SHA256:
`dd4ad95de3c04a71fccc222c36894996c4a8af1b82fd8ba935fe557d7b57ce36`.

Native discovery confirms one Event/State/Telemetry writer and reader per router.
Live Chromium confirms the new script revision, all three summary cards including
the 300-second Telemetry timeout, six QoS rows and zero page errors.
The unchanged live contact-reporting/independent-target workflow passed
**1/1, zero skips** (25.97 s). This is a targeted live gate, not another full
five-workflow run. Final checks confirm six peers ALIVE/ROUTER_OK, original teams
and 20x clocks restored, all missions CANCELLED at zero speed, and no impairments.
The mesh remains running on port 8080 in `debug/mesh_runs/emane`.

Retained evidence: [DDS JUnit](../debug/test_reports/wan_review_fixes_dds.xml),
[C++ gate](../debug/test_reports/wan_review_fixes_cpp.txt),
[live mission JUnit](../debug/test_reports/wan_review_fixes_live_mission.xml),
[native discovery](../debug/test_reports/wan_review_fixes_discovery.json),
[browser assertions](../debug/test_reports/wan_review_fixes_browser.json),
[configuration screenshot](../debug/test_reports/wan_review_fixes_config.png)
and [final state](../debug/test_reports/wan_review_fixes_final_state.json).
Earlier SEDP/log caveats and broader ownership/load/health-priority gates remain.

### 3.43 Exhaustive-review follow-up corrections (2026-11-13)

The user requested fixes for all four findings from the 96-path open-change review:

- The three-topic cross-writer priority control now orders samples by the native
  `SampleInfo.reception_timestamp`, not the order of separate reader polls.
  Cross-reader timestamp ties fail explicitly. This measures middleware commit
  order, not wire arrival or reliable repair. Offline regressions cover both
  priority directions, reversed poll order and ambiguous timestamps. All
  **9/9 native controls passed**, including both priority directions:
  [results](../debug/test_reports/three_topic_wan_20261113T175724-8653/results.json).
  Earlier priority-reversal claims based on polling order are superseded; the
  historical recovery measurements and retained comparison reports are unchanged.
- Both churn entry points now share the supported per-topic priority-mode
  catalog, including `priority_bands_repair` and `priority_bands_last1`.
  Production and equal-priority FIFO still reject overrides. Parser/worker
  regressions verify acceptance, rejection and propagation without creating DDS
  entities. **38/38 offline tests passed in the SDK environment, zero skips**;
  the host ran the same suite with one SDK-import skip covered by that run.
  Actual isolated healthy workloads passed for
  [repair with command priority 200](../debug/test_reports/instance_churn_reconnect_20261113T175832-789748/results.json)
  and [KEEP_LAST with contact priority 100](../debug/test_reports/instance_churn_reconnect_20261113T175839-176498/results.json).
  Native write counters confirm the overridden priorities were used. These are
  CLI smoke checks, not new outage/recovery or safe Event-retention qualifications.
- Current router/presence guidance now correctly says the cache corrections
  from §3.39 were included in subsequent coordinated deployments. Historical
  checkpoint wording remains unchanged.
- The [telemetry-options slide](slides/telemetry-typed-or-catalog.md) has shorter,
  equivalent wording. PDF and PowerPoint were regenerated with the existing
  renderer and visually checked. The single-page PDF has no out-of-page text
  and **28.34 CSS pixels** between the bottom text and reserved logo box,
  exceeding the 12-pixel check. PowerPoint remains one slide with matching notes.

No production router source, IDL, QoS policy, running binary or live configuration
changed. All DDS controls used owned network-isolated containers. The retained
seven-node EMANE mesh was not restarted or modified; the deployed baseline and
open recovery/load/health-priority qualification limits remain unchanged.

### 3.44 Routing Service three-topic data plane (2026-11-13)

The parallel Routing Service processor now supports the user-approved
Event / State / Periodic contract, retaining its earlier Event / Status mode.
Event and State reuse WanEvent; Periodic reuses WanStatus. No IDL changed.
ContactReport is State/Normal; required Event retention and periodic policies
remain separate. All writers share `act_rs`, with the approved preference:
Event/High > Periodic/High > State/High > Event/Normal > State/Normal >
Periodic/Normal (600/500/400/300/200/100).

A fresh Connext / Routing Service 7.7.0 build passed **36 acceptance tests,
zero skips** (154.97 s), across both contracts, and **18 configuration/launcher
checks**. Three separately opt-in time-filter checks were skipped. Acceptance
covers all fourteen application payloads, physical endpoint sharing, native
lifecycle, filtering/recovery, writer policies and six-class middleware
reception ordering. The initial combined run's directory collisions and
domain-budget exhaustion were fixed in the shared test fixtures before the
complete passing rerun. Both documented role renders have ten ports and pass
the installed service XSD; all eight guide Bash blocks pass syntax checks.

See the [processor README](../router/routing_service/README.md),
[setup guide](routing-service-c2-platform-setup.md),
[remaining plan](routing-service-three-topic-plan.md) and
[acceptance JUnit](../debug/test_reports/rs_three_topic_0480d1ea.xml).
The processor still requires YAML and the fixed catalog; XML-only startup,
optional off-by-default Periodic input filtering, health/config generation,
monitoring parity and wider recovery/load qualification remain open.
Periodic is not the deployed custom router's Telemetry name. All tests used
owned private network namespaces; no live deployment, binary overwrite or
mesh reconfiguration occurred.

### 3.45 Release scope and XML mapping reuse (2026-11-13)

The user selected a manual bundle for v1: prepared types/QoS/Routing Service
XML, XML-authored typed mappings, per-node environment files, start/stop scripts
and a setup/check guide. Mapping includes selected fields/subtrees, nested
structures, sequences and arrays, generic instance/routing assignments and
explicit destination reconstruction. It is not limited to today's ACT catalog.
V2 adds discovery/import, a mapping editor and an isolated route-test app that
produces the same bundle; it is not a second runtime configuration authority.

The installed 7.7.0 Assign library/example and official tutorials were checked.
Reuse its destination/source XML property convention and extension framework,
but the documented primitive-only mapper excludes arrays/sequences and cannot
replace the required typed subtree/collection/envelope engine. Processor versus
custom Transformation placement needs native qualification. XML-only startup
and general mapping remain implementation work, not completed by §3.44.

The [setup guide](routing-service-c2-platform-setup.md#agreed-release-split),
[processor plan](routing-service-three-topic-plan.md) and
[product-gap record](product-gaps.md) now reflect this release split. Existing
health/monitoring/parity gates remain required; no runtime source or live
deployment changed in this scope clarification.

### 3.46 Processor-only XML mapping research and plan (2026-11-13)

The user selected keeping typed mappings inside the existing processor, using
Routing Service XML/Assign-style destination-to-source conventions, rather
than adding a Transformation plugin. The
[draft XML mapping plan](routing-service-xml-mapping-plan.md) covers the grammar,
schema distribution, keys/lifecycle, audience/filter registry, XML-only launch,
v1 packaging and v2 app boundary.

An entity-free C++11 probe in the installed Connext 7.7.0 container passed four
whole-member owning-copy cases: a struct, bounded primitive sequence, 2 x 3
array and sequence of structs. No DDS entities were created. Installed headers
also confirm processor properties become a string map, so duplicate XML names
must be checked before map conversion. General collections, optional/union
behavior, reconstruction, ownership and native mapped routes remain gated.

This is feasibility research and a draft plan, not implemented XML-only startup
or general schema mapping. No production runtime source, binary or live mesh
changed for this work; prior integration/load/recovery limits still apply.

### 3.47 V1 simplified to full original types in unions (2026-11-13)

The user superseded the selected-field/subtree proposal: v1 will bind each
original LAN topic/type to a union branch containing that **complete type**.
All nested structures, sequences, arrays, optional fields and keys travel
intact, with the existing agreed authoritative source restoration retained.
Generic envelope instance/routing metadata and keyed-instance lifecycle remain
required, but per-payload-field selection, renaming, partial reconstruction and
default rules are deferred.

The [processor-only plan](routing-service-xml-mapping-plan.md),
[three-topic plan](routing-service-three-topic-plan.md),
[setup guide](routing-service-c2-platform-setup.md) and
[product-gap record](product-gaps.md) now reflect this simpler scope. The
processor reuses whole-value pack/unpack; there is no new Transformation plugin
or general field-copy engine in v1. V2 initially automates full-type binding,
bundle generation and testing; selected-field mapping is optional future work.
XML-only startup and configurable full-type bindings remain unfinished.
This update changes documentation only; the live mesh is untouched.

### 3.48 Logical channel names for XML bindings (2026-11-13)

The user selected **channel** as the single user-facing setting:
`event_high`, `periodic_high`, `state_high`, `event_normal`, `state_normal`,
`periodic_normal`, retaining sample priorities 600/500/400/300/200/100.
The planned XML binding uses `act.binding.<id>.channel`; family and priority
are derived, not independently configured.

These are logical scheduling channels on the existing Event/State/Periodic
topics, not six topics, new union branches or additional endpoint pairs.
Native XML MultiChannel bands and receive-family dispatch remain unchanged.
The [binding plan](routing-service-xml-mapping-plan.md) and release/setup docs
record this naming decision. This is a documentation/proposed-interface update;
the prototype YAML settings, runtime code and live mesh were not changed.

### 3.49 Discovery-driven type preparation and v1 binding choices (2026-11-13)

The user confirmed native grouped XML properties, identical LAN topic names
and full types at both ends, one existing string field for instance identity,
and preservation of control/addressed/team/global routing. Core binding fields
are channel, topic name and instance-ID field; manually authored LAN reader/
writer QoS references remain on native ports. Start with the reference workload
and agree measured release limits before shipping.

The v1 preparation workflow is **capture -> generate shared XML -> start both
routers**. Users do not hand-author original application type definitions.
A new headless capture/export/generation helper must resolve complete types,
compose stable full-type union branches and distribute the shared catalog
before endpoints start. Router runtime then consumes the frozen bundle without
the helper. V2 adds the setup/test app over that engine.

Auto-QoS remains recorded as qualification-first LP-9; v1 still uses manual
profiles. Discovery supplies types, not automatic union construction or QoS
selection. XML-only startup, preparation and generic full-type bindings remain
unimplemented in the prototype. The plans/setup/product-gap docs reflect these
choices; no runtime source or live mesh changed.

### 3.50 Explicit schemas now; discovery-driven setup later (2026-11-13)

The user retained the typed-union/full-original-type design and clarified the
release order: explicitly define application types, shared union branches,
bindings and QoS now; add discovery/capture and setup generation later.
This supersedes §3.49's capture/generation helper as a v1 prerequisite.
Existing identity, channel, audience and manual-QoS decisions remain unchanged.

Both ends receive the matching frozen type/binding contract before startup.
Later tooling must produce that same deployment bundle rather than introduce
another runtime configuration authority. The mapping plan, setup guide,
processor plan/README and LP-9 now reflect this split. No runtime source,
binary or live mesh changed; XML-only startup and generic bindings remain
implementation work.

### 3.51 XML-only processor deployed and mesh-qualified (2026-11-14)

The user authorized replacing the owned mesh with the Routing Service processor.
The deployment now consumes native Routing Service XML: explicit grouped binding
properties, a complete original-type/union XML asset, native LAN QoS references
and external shared-family QoS. The YAML helper is development preparation only;
`run.py --xml FILE` does not load YAML or regenerate configuration. See the
[processor README](../router/routing_service/README.md) for the exact grammar,
direct startup and preflight commands.

The processor uses the production presence/session/RTT and physical protocol
statistics implementations through service-owned endpoints. Health shares
Periodic; no dedicated WAN health pair is added. Native admin team changes
update existing receive filters and subsequent outgoing metadata. Initial and
updated platform membership preserve the protected node identity, including an
unassigned platform. The existing aggregate sixteen-name limit still applies.

The user explicitly selected **always-on periodic reporting**. The dashboard
shows ALL, removes misleading mode controls and rejects unsupported mode changes.
The five existing browser workflows now verify that policy explicitly, including
continued detailed terminal reporting after blackout recovery; legacy assertions
remain for the custom backend. Fixtures hide scenario targets before/after each
workflow, and the blackout test requires a complete rolling capture window before
asserting loss. Unknown capture coverage is not treated as measured loss.

Two real deployment findings were fixed before scaling: Routing Service plus
EMANE exceeded the default 32-property participant capacity, and unassigned
platforms initially omitted their protected identity from health. The opt-in
`rs_priority_participant` XML profile now authors capacity 64; renderer composition
preserves other WAN base settings. Actual DDS tests also caught inherited
one-second filtering; unconfigured Periodic LAN inputs now pass high-rate data,
with per-key filtering only when explicitly selected.

Fresh results in the supported Connext 7.7.0 container:

- **9/9 C++ targets passed.**
- **40 processor acceptance tests passed**, covering both contracts, complete
  application forwarding, lifecycle, priorities, actual default/opt-in filtering,
  team data isolation, production monitoring and health deadline/recovery.
- **237 configuration/UI/control tests plus 245 subtests passed**, zero skips.
  The three private-network native time-filter checks were explicitly enabled.
- **8 legacy presence/link-statistics regressions passed.**
- **5/5 live browser mission workflows passed**, zero skips, **387.42 s**.
  They include real one-way blackout/reset, command/ACK and terminal recovery,
  reporting/targets, route editing, multi-team missions and live reassignment.
  Browser checks also verified the current JS asset, XML Config view and
  always-on graph context menu.

The retained mesh is Control_20 plus Platform_30 through Platform_35, backend
`routing_service`, contract `three_topic_union`, in `debug/mesh_runs/emane`,
dashboard port 8080. Live physical-writer observations show exactly Event,
State and Periodic on all seven routers, each matched to one reader on each of
the other six routers. All six peers are ALIVE/ROUTER_OK; RTT/protocol rows are
valid. Missions are cancelled, teams unassigned, clocks 1x, speed zero, scenario
targets hidden and controller impairments empty. Routing Service still emits
startup WARNING records; do not describe the logs as warning-free.

Recreate through the owned lifecycle, never by editing live generated XML:

```bash
CONNEXT_SHARED_DIR=/home/dgreenberg/rti_connext_dds-7.7.0 \
harness_v2/scripts/run_mesh.sh up --emane --platforms 6 \
  --router-backend routing_service --wan-contract three_topic_union \
  --workdir "$PWD/debug/mesh_runs/emane" --with-dashboard
```

Use the matching `down --emane --workdir "$PWD/debug/mesh_runs/emane"` before a
binary/type/configuration rebuild. Compact evidence is retained under
`debug/test_reports/rs_processor_*`.

This qualifies the current **fixed ACT catalog**, not an arbitrary external
type registry or discovery-driven schema setup. Auto-QoS, future schema
compatibility, full restart/multiple-producer ownership and bounded-load/recovery
release gates remain separate work. The custom backend remains the default and
rollback option; no commit or push was requested.

### 3.52 Platform mission CFTs deployed and qualified (2026-11-16)

The approved processor increment is implemented and deployed through the owned
EMANE lifecycle. Application topic names are now **Mission**, **Team** and
**PlatformStatus**, replacing ControlCommand, ActTeamAssignment and
PlatformInitStatus. Payload types/fields are unchanged. RouterHealth adds
`receive_profile`, `receive_state` and `receive_filter_error`; matching IDL/XML
and participants were rebuilt/restarted together.

`act.receive_policy=mission` prepares platform readers with Init predicates
before accepting traffic. Init/IDLE/COMPLETE/CANCELLED select Mission and Team
from C2 plus required internal traffic. TRANSIT/ORBIT/RTB/HOLDING select
ActiveMission: retain those inputs and add same-team PlatformMissionStatus and
ContactReport, plus fleet-global PlatformStatus positions. The user approved
the navigation addition after live testing exposed the existing
`WAITING_PEERS` collision-avoidance dependency. No new navigation topic or
weakened safety check was introduced. An unassigned active platform receives
navigation but no team-scoped mission/contact data.

Only valid local PlatformMissionStatus drives the policy. Peer reports do not
become local authority or loop back onto WAN. Timestamp/source validation,
transactional team/profile updates and explicit error reporting are implemented.
Whole-platform restart resets Init without replaying the old selection.
Outgoing reports remain always on; C2 report reception, the three shared WAN
pairs, channels/priorities and fixed XML QoS remain unchanged. Explicit
`act.receive_policy=static` retains catalog acceptance coverage; the minimal
setup recipe selects that mode explicitly.

Live qualification caught and fixed a dangling source iterator: observing a
local mission update replaced the model during its source-map traversal.
The pump now retains independent topic keys and reacquires bindings.
A production-preparation red/green regression executes 24 transitions, checks
immediate forwarding and continued health, and rejects sample/callback errors.
The pre-fix binary failed; the rebuilt binary and final live mesh pass.
Browser qualification also now waits for actual home-position/speed convergence
after COMPLETE: independent status topics are not an atomic snapshot.

Fresh executed gates:

- **9/9 C++ targets passed.**
- **34 native policy/configuration tests plus 55 subtests passed**, including
  state/team/source/staleness, fleet-global navigation, lifecycle, writer-side
  filter evidence, the model-replacement regression and complete target
  simulator/controller/Routing Service restart while C2 and another platform
  remain running.
- **36 two-/three-topic static acceptance tests passed** after the callback fix.
  Run these separately from the policy group: combining all native selectors
  exhausts the existing 52 reserved domain triples. Do not raise the domain limit.
- **165 offline UI/mission/workload tests plus 60 subtests passed.**
- **119 renamed legacy-route/configuration/mission tests plus 5 subtests passed**;
  **8 legacy presence/link-statistics regressions passed**. Groups overlap;
  do not sum them as a unique full-suite count.
- **5/5 live browser mission workflows passed**, zero skips, **375.89 s**.
  Startup/terminal Init, active-profile diagnostics, always-on reporting,
  current JS revision, real blackout/reset, home return, multi-team orbits
  and goal inheritance/reassignment are covered.

The retained mesh is Control_20 plus Platform_30-35, backend `routing_service`,
contract `three_topic_union`, dashboard port 8080, workdir
`debug/mesh_runs/emane`. Final checks: six peers ALIVE/ROUTER_OK, Init/CANCELLED,
teams unassigned, clocks 1x, speed zero, scenario targets hidden, controller
impairments empty. Each platform's native writer observations show exactly
Event/State/Periodic with six matched readers and six filter records per writer.
No `rs.sample.error` or processor callback failure appears in any of the seven
current router logs. Existing startup warnings are not claimed resolved.
One coordinated fleet startup hit simulator readiness timeout; the owned
teardown/relaunch succeeded, without a speculative configuration change.

Compact evidence: `debug/test_reports/mission_cft_native_policy.xml`,
`mission_cft_native_static.xml`, `mission_cft_live_missions.xml`,
`mission_cft_offline.xml`, `mission_cft_legacy_routes.xml`,
`mission_cft_legacy_monitor.xml`, `mission_cft_cpp.txt`,
`mission_cft_runtime.json` and `mission_cft_screenshots/`.
The browser screenshot of Init explicitly separates receive selection from
the ALL outgoing-report badge.

This is not a full router-suite or generalized release claim. Queue draining
and sustained rapid-transition stress remain unqualified. Rollback-failure
coverage uses injected callbacks, not native DDS faults. Generic registry,
future schema compatibility, fleet-scale load/admission and broader
multiple-producer recovery remain separate work. No commit or push was requested.

### 3.53 Processor review fixes qualified, not deployed (2026-11-16)

The five findings from the follow-on review are resolved in source:

1. Outgoing ACK and health diagnostics respect their 255-byte DDS bounds.
   Full errors remain logged, including rollback-failure information. A
   valid-sized rejected command can no longer poison subsequent heartbeats
   with an oversized stored diagnostic.
2. Only valid received Status data refreshes presence. Nonhealth lifecycle
   notifications cannot revive a stale peer; health NO_WRITERS/dispose uses
   the intended loss/countdown path rather than legacy immediate-DEAD handling.
3. Application audit expectations use node-owned `receive_policy` history,
   emitted for startup/restart Init and successfully committed profile/team
   changes. Navigation eligibility is evaluated at the send timestamp, not
   the final profile. Missing or malformed history is inconclusive evidence.
   Dashboard application diagnostics use the same history; primary WAN wire
   Delivery metrics remain independent. Existing static audit rules are retained.
4. The whole-structure adapter spike's mapping and wildcard alias now use
   PlatformStatus and Mission. Startup resolves every exercised topic before DDS.
5. Prepared XML preflight and native endpoint startup validate LAN ownership
   and domain as well as payload type. LAN DDS identities are now
   `node/router/participant`, or a deterministic SHA-256 form when this exceeds
   255 bytes; WAN identities are unchanged. The bounded form preserves previously
   valid long node/router names instead of silently tightening their limits.

Executed isolated gates:

- Fresh supported Connext 7.7.0 processor/config-helper build.
- **38 configuration/launcher tests plus 59 subtests passed**, zero skips.
- **20 native mission/monitor tests passed**, zero skips; the strengthened
  nonhealth lifecycle case also passed independently.
- **36 static two-/three-topic native acceptance tests passed**, zero skips,
  after the LAN identity changes.
- The actual-record audit consumer integration passed; four real native records
  were independently parsed through the shared history and report recipient
  resolver, including team removal without a profile change and restart Init.
- A name-boundary regression passed with **four subtests**, verifying C++/Python
  agreement and actual native startup for the bounded identity.
- **27 dashboard offline tests: 24 passed, 3 skipped** on the host; **14
  application-audit tests** and **2 mesh-launcher checks** passed. Host skips
  require the Connext binding or optional browser environment; they are not
  presented as completed browser/native coverage.
- Adapter spike: **9 PASS groups and 10 expected rejections**, fresh private
  build and run, including wildcard alias and native lifecycle checks.
- Four expected-red native fault replays demonstrated the original ownership,
  oversized-diagnostic and lifecycle defects. Python syntax, shell syntax and
  whitespace checks passed.

Test groups overlap and must not be added as a unique full-suite count.
Compact evidence is under `debug/test_reports/rs_review_runtime_*`, including
the four actual policy records in `rs_review_runtime_audit_records.jsonl`.
Disposable builds and DDS participants used private container network namespaces,
not the retained mesh's host network or live binaries.

**No deployment was performed.** The seven-node mesh and its generated XML/binaries
remain at checkpoint 3.52 and were checked healthy. Do not start the new plugin
with old prepared LAN identities: use the owned teardown, rebuild, regenerate
and coordinated relaunch. No new live mission/browser gate or full router-suite
gate is claimed. Queue-draining, sustained-load and native rollback-fault limits
from checkpoint 3.52 remain open. No commits or pushes were requested.

### 3.54 Independent native processor completed and deployed (2026-11-17)

The user authorized the complete existing regression gate, coordinated
replacement and all five live mission workflows. The retained seven-node mesh
now uses the native shared WAN hub with independent one-way LAN activation and
native auto-QoS, including checkpoint 3.53 and the independent-hub review fixes.
Routing Service owns every endpoint. Exactly one Event/State/Periodic
reader/writer pair per gateway was verified by native discovery; there is no
extra WAN participant, intermediate bus or plugin backlog.

The [completion acceptance record](router-processor-completion-plan.md#completion-acceptance-record-2026-11-17)
contains scope, exact build identities, exercised rollback and reproducible
evidence. Final results, with overlapping groups kept separate:

- **10/10 C++ targets passed**, fresh private and persistent builds matching.
- **386 retained-scope nonlive router cases plus 284 subtests passed**, merging
  the complete module ladder with final affected reruns by test ID.
- **74 native processor cases passed**: 18 two-topic, 8 auto-QoS, 30 monitoring/
  policy/mesh and 18 three-topic. Runs were split to respect domain reservations.
- **56 configuration/native time-filter unittest cases passed**, including all
  three explicitly enabled opt-in cases.
- **30 dashboard cases plus 24 subtests**, **29 WAN qualification cases**,
  **38 selected workload cases plus 37 subtests**, and the adapter spike's
  **9 PASS groups / 10 expected rejections** passed.
- **All five unchanged live Chromium/DDS/EMANE mission workflows passed**,
  zero skips, **380.53 s**, on the final deployed binary.

Release preflight fixed expected loss being misreported as permanent failure:
BEST_EFFORT `lost_by_writer` gaps and three-topic State KEEP_LAST(1)
supersession are rate-limited warnings when no new rejection accompanies them.
Reliable Event history loss, rejections and other loss reasons remain errors.
Real packet-loss regressions verify both WAN contracts, final ContactReport
sequence delivery and unchanged C2 health. An earlier five-workflow pass was
not accepted as final when log auditing exposed the State classification bug;
the mesh was stopped, rebuilt, regenerated and the complete live gate repeated.

Internal `ActRouterDiagnostics` no longer waits for an application LAN input to
match its own monitor. It retains LAN publication and uses the existing codec/
shared WAN writer directly, preserving source enablement, audience and priority,
without an extra endpoint or queue. Native red/green coverage requires actual
WAN diagnostics without an external LAN producer. Default platform diagnostics
go to C2, not other platforms. Two stale test fixtures were repaired without
weakening their behavioral assertions.

The old matching bundle was actually launched, verified with six healthy peers
and old hashes, then stopped through its own lifecycle. Final retained run:
`container-20261117T181119-2947503`, Compose project
`act-emane-container-baseline`, Control_20 plus Platform_30–35, dashboard 8080,
workdir `debug/mesh_runs/emane`. All seven deployed hashes match the qualified
build. Every router, including C2, publishes ROUTER_OK; six platform peers are
ALIVE, missions CANCELLED / receive Init, zero speed, unassigned teams, 1x clocks,
hidden targets and empty impairments. Browser graph/configuration and actual
assets were inspected. Final logs have zero structured/native ERROR lines;
expected loss warnings remain visible. The mesh remains running under its owner.

An **additional legacy routed-churn spike is not fully green**: three cases pass,
but BEST_EFFORT Status outage disposal leaves 41 ghosts. The archived old
`router_main` reproduces the same 41 ghosts. This known limitation is not hidden,
fixed or claimed as new processor behavior. Generic setup/export/monitoring,
new numerical sustained-load/recovery guarantees, persistence, exactly-once and
atomic fleet-wide filter barriers remain outside this acceptance.

Compact evidence: `debug/test_reports/processor_release_20261117/`, including
final consolidated/native/live JUnit, red/green logs, screenshots, postflight,
native all-node inventory, qualified/deployed hashes, link-stat parity and
rollback records. Earlier allocator/setup errors and expected-red evidence are
retained separately. Disposable qualification state and the stopped rollback
checkout were removed; the matching rollback archive remains. The shared
Connext correction log was published with explicit approval and pinned by
parent commit `381a57d`; no parent implementation branch was pushed.

### 3.55 Diagnostics failure isolation qualified, not deployed (2026-11-17)

The final review found that rethrowing an internal diagnostics failure aborted
the monitoring tick before advancing its heartbeat timer or collecting link
statistics. Persistent failure therefore flooded heartbeats at the 10 ms pump
cadence and starved link updates. Diagnostics now retain the original route
error/timeout accounting and explicit `rs.diagnostics.error` logging without
aborting those schedules.

Real native writer-instance exhaustion reproduced the failure in both WAN
contracts. **10 C++ targets and four focused native cases passed**, zero skips,
including both fault cases, normal internal forwarding and existing health/
protocol/team behavior. See the [follow-up record](router-processor-completion-plan.md#final-review-follow-up--diagnostics-failure-isolation-2026-11-17)
and local `debug/test_reports/processor_diagnostics_isolation_20261117/`.
Private qualification state was removed. The checkpoint 3.54 mesh/binaries
remain untouched; this is source qualification, not deployment or a new live
mission gate.

## 4. Audience selection and runtime changes

Use ContentFilteredTopics (CFTs) for sample selection. Keep the selection fields
outside union branches where they are genuinely shared. Production variant
selection uses `original_topic = 'PlatformData'` (§3.26); the codec verifies
agreement with the union discriminator. Older discriminator-based examples remain
historical qualification evidence, not the current production filter policy.
Representative audience and struct-branch
predicates passed §3.3; final combinations still need their own tests.
The following are audience rules, not fully
validated production SQL expressions:

- A platform receives ControlCommand/StatusMode events selected by the current
  destination rules: its own ID or a current team ID in `audience.destination_ids`
  (§3.27). Acknowledgments retain the current platform-to-control
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
spike used TEAM_A. The bounded exact team-intersection predicate passed §3.6;
the combined role/global/addressed/per-route predicates still require their own
qualification before runtime integration.

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

The transport and focused presence/RTT slice is implemented and verified in
§3.11. The load/isolation and timing-alignment gates below remain applicable.

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
- Other Status variants must not refresh the native health-instance deadline.
  Source-level presence intentionally accepts any Status, as specified below.

The user explicitly approved the shared-Status policy in §3.12:
`wan_union_status` is best effort, volatile, KEEP_LAST(1), with a 2-second deadline
and infinite liveliness. There is no separate health reader/writer or finite
writer liveliness lease in this opt-in mode. Native deadline accounting remains
per instance, but source presence intentionally resets on **any received Status**.
The direct listener only handles the supplied RouterHealth instance, not a
general missed-instance sweep. Coalesced callbacks can report a different branch.
Health NO_WRITERS/dispose also starts the same STALE/30-second countdown;
otherwise graceful writer closure cancels native deadline monitoring.
Congestion/isolation/load qualification remains open; a shared queue is an
intentional simplification, not a promise of health-priority isolation.

The unchanged legacy `router_health` profile remains transient local with a
2-second deadline and 3-second automatic liveliness lease.

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
| 6. Move presence and health | Share the Status pair; preserve publication timing, session binding, passive RTT, and LAN summaries; use the approved deadline/countdown policy (§3.12) | Idle health, exact endpoint counts, health-only callbacks, application recovery, graceful close/dispose, 30-second countdown, loss/recovery, and RTT/restart pass; load/isolation remains open |
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

The process selector is now `router.wan_contract: legacy | two_topic_union`,
with `legacy` remaining the default during development (implemented in §3.8).
New-mode config validation rejects
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
`(run_id, wan_family, source_id, publishing_session, variant,
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

- Final envelope bounds, union case names, schema evolution, and resource review.
  Event/Status use the explicit three-key version 2 identity in §3.16.
  ContactReport uses `(source, contact_id)` on the LAN and `contact_id` on the
  WAN, with receive-side source restoration as described in §3.17.
  No payload field mapping or typed-operation redesign is introduced.
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
