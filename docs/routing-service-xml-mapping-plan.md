# Processor-only XML union bindings: v1 research and implementation plan

Date: 2026-11-13.

Status: **implementation plan with a partial ACT-catalog implementation**.
XML-only startup and explicit grouped binding properties are implemented; the
[processor README](../router/routing_service/README.md) is the authoritative
executable property grammar. The surface inventory and phased gates below
describe the original plan, not a claim that every gate has passed. Arbitrary
application-type registries, discovery-driven setup and release load/recovery
qualification remain unfinished.
The user simplified v1 to **a union branch containing the full original type**.
This supersedes the earlier selected-field/subtree mapping proposal and its
`tx.copy` / `rx.copy` examples. Field selection/renaming, partial reconstruction
and default rules are deferred, not v1 requirements.
Research included current source, installed Connext / Routing Service 7.7.0
headers/examples, official documentation and an entity-free native C++ probe.
The original research changed no runtime. Subsequent implementation and mesh
qualification are recorded in the [migration history](two-topic-wan-migration-plan.md).

## 1. Recommendation and release boundary

Extend the **existing processor plugin**. Do not modify RTI's shipped Assign
library and do not add a separate Transformation plugin for v1.

Use standard Routing Service XML property bags to bind original LAN topics/types
to logical channels and union branches, with explicit instance/routing metadata.
Copy the entire original DynamicData value into the selected branch and restore
that same complete type on receive. No general field-copy engine or Assign
transformation is needed for v1.

- **V1:** explicitly define the application types and shared full-type unions,
  then ship that type XML, QoS XML, per-node Routing Service XML,
  manually authored full-type bindings, environment files, the tested processor and
  start/stop scripts. Include a setup guide, manifest and safe manual checks.
- **V2:** add discovery/capture and generation to a Python-backed binding and
  test workflow that produces the same bundle. No second runtime
  configuration language.

V1 must not require YAML configuration, the YAML helper, development tools or
the future setup app. XML-only configuration now works for the fixed ACT catalog;
an external arbitrary full-type registry remains unfinished. Existing
health/monitoring/parity gates are not waived.

Retain Event / State / Periodic, their three shared writer/reader pairs, the
approved six priorities and explicit XML-authored delivery policies. Mapping
does not add per-original-topic WAN endpoints or inherit application QoS onto
the WAN. Keep optional Periodic LAN input filtering off by default in v1.

### Confirmed setup decisions

- Use native Routing Service XML properties, grouped per binding; no separate
  custom binding-file parser. The logical binding fields are `channel`,
  `topic_name` and `instance_id`. Group prefixes distinguish multiple bindings
  in the processor's single property map; they are not application data fields.
- Keep the same original LAN topic name and complete type at both ends.
- `instance_id` names one existing string field. No numeric/composite encoding
  or automatic key guessing in v1.
- Keep the existing control, addressed, team and global recipient rules.
  Channel chooses scheduling/family, not recipients.
- Manually author LAN reader/writer QoS profiles and reference them on the
  native port declarations. Do not duplicate those references as a second
  processor-property authority. WAN QoS stays shared by family.
- **Define now; discover/setup later:** explicitly supply the application types,
  full-type union branches, bindings and QoS for v1. A discovery/capture and
  generation helper is deferred, not a v1 prerequisite. Runtime consumes the
  frozen bundle; runtime discovery/bootstrap of new union branches is not v1.
- Start qualification with the current reference workload. Agree measured
  release limits before shipping; existing failed load/recovery gates remain open.

## 2. Research: what is verified

| Finding | Evidence | Consequence |
| --- | --- | --- |
| Assign maps primitive fields, including strings and nested primitive leaves; arrays/sequences are excluded | Official 7.7 tutorials; installed `librtirsassigntransf.so` and `shapes/topic_bridge_w_transf2.xml` inspected | Do not use Assign for full-type encapsulation; reuse the current processor/codec copy pattern |
| Processor properties are a string-to-string map | Installed `rti/routing/PropertySet.hpp`: `std::map<std::string,std::string>`; property names must be unique | Namespace properties by binding/direction; reject duplicates before conversion to the map |
| Input/output ports expose stream/type information and `create_data()` | Installed `rti/routing/processor/Input.hpp` and `Output.hpp` | Validate configured mappings against actual endpoint types and create owned output samples |
| DynamicData supports aggregate copies, loans and collection APIs | Installed `rti/core/xtypes/DynamicDataImpl.hpp` | Copy the complete original value, including its nested collections |
| Whole-member owning copies worked for four shapes in native C++ | Freshly compiled entity-free 7.7.0 probe, described below | Begin with native copy operations, not handwritten serialization or an element-by-element mapper |
| The current processor recovers lifecycle keys before returning the input loan | Current processor source and its earlier acceptance gate | Preserve this ordering and seen-instance policy while changing the codec |

### Native copy probe

In an owned `--network none` container using `connext:7.7.0`, compiled a C++11
program against the installed SDK. It loaded test types from XML and used
`value<DynamicData>(member)` followed by `value<DynamicData>(member, selected)`
on an owned destination. Read-back equality passed for:

1. A nested struct containing a long.
2. A bounded primitive sequence, bound 4, length 3.
3. A multidimensional long array, dimensions 2 x 3.
4. A bounded sequence of structs, length 1.

**Result: 4/4 passed; no DDS entities created.** Contrary to a conservative
assumption that a collection always needs element-wise traversal, the native
whole-member operation worked for these collection cases too. This is not a
generic mapper qualification. For v1, qualify full original types containing
empty/shrinking sequences, nested collections, optional members and unions,
plus mismatched branches, loan lifetimes and DDS lifecycle. Renamed payload
fields and selected-subtree copies are deferred.

The research consulted Connext AI but treated installed headers and the
compiled probe as the arbiter. The shared known-issues record was synced and
checked; the AI answer's uncertainty about collection copies was not treated
as an unsupported-feature claim.

## 3. Current code: changes needed beyond an XML parser

| Surface | Current coupling | Planned change |
| --- | --- | --- |
| [GatewayModel](../router/routing_service/GatewayModel.cxx) | Reads YAML, aliases, route parser and fixed catalog; settings/teams are YAML too | One validated deployment model populated from XML properties and actual port/type metadata |
| [GatewayProcessor](../router/routing_service/GatewayProcessor.cxx) | Requires `config` path, parses teams as YAML, names LAN ports from catalog topic names | Bind explicit XML port names; run the compiled mappings without a YAML path |
| [WanCodec](../router/src/core/WanCodec.cxx) | Fixed topic/type/branch/instance-field table; assumes whole original payload and string identity | Configurable typed registry and pack/unpack/key operations; retain the ACT catalog as an explicit reference configuration |
| [WanInterests](../router/src/core/WanInterests.cxx) | CFT cases enumerate the fixed table; addressing and audit fields depend on ACT names | Use the same registry for filter cases and declared metadata paths; retain reserved/internal semantics |
| [ConfigMain](../router/routing_service/ConfigMain.cxx) | YAML helper emits another YAML model | Development-only translation if retained; not invoked or required in deployment |
| [Launcher](../router/routing_service/run.py) | Reads YAML, invokes helper and regenerates XML on every start | Start prepared XML directly; keep existing owned-process shutdown behavior |
| [QoS library](../harness_v2/qos/act_qos_profiles.xml) | Fixed policies already external; Periodic input filtering is currently selected unconditionally by helper | Preserve explicit family profiles; make input filtering a per-original-topic opt-in |
| Acceptance/config tests | Mostly prove the fixed ACT catalog | Add configuration-only, entity-free mapping and native mapped-route tests |

Changing only `GatewayProcessor` would leave hidden catalog assumptions in
keys, addressing and filters. Conversely, do not globally replace the custom
router's codec/catalog while developing the parallel processor. Reuse shared
helpers where safe and isolate the new registry until regressions justify
broader extraction.

## 4. XML contract: full-type bindings only

Keep the standard hierarchy:

`processor -> property -> value -> element -> name/value`.

Do not add custom `<mapping>` children to Routing Service's XSD. Do not embed
YAML, JSON, script expressions or another XML document inside property values.
Namespaced properties represent one versioned full-type binding contract.
There are no per-payload-field copy properties in v1.

### Proposed properties

| Group | Purpose |
| --- | --- |
| `act.config.version` | Version of our property grammar, independent of WAN schema version |
| `act.node.*` | Node/role/startup identity, using supported XML configuration variables |
| `act.types.uri` | Prepared type XML; must agree with types registered by Routing Service |
| `act.binding.<id>.topic_name` | Original LAN topic name, also used in the existing `original_topic` envelope key |
| `act.binding.<id>.channel` | One of the six logical channels below; determines physical WAN family and publication priority |
| `act.binding.<id>.branch` | Branch whose type must equal the full original LAN type; derive/check its label/type from XML |
| `act.binding.<id>.tx.port` / `.rx.port` | Explicit local LAN input/output port names; only configured directions are active |
| `act.binding.<id>.instance_id` | Name/path of one existing string field used as instance identity and restored on key-only receive |
| `act.binding.<id>.origin.destination` | Optional existing LAN field restored from authoritative envelope source, as ContactReport does today |
| `act.binding.<id>.audience.*` / `.audit.*` | Declared addressing/audit sources, not guesses based on topic names |

These are **proposed custom properties**, not an existing RTI schema or working
configuration. Freeze names only after the first native vertical slice.

### User-facing channels

The user selected one **channel** setting instead of separate family/priority
settings. Use these exact names:

| Channel | Physical WAN topic | Sample priority |
| --- | --- | --- |
| `event_high` | Event | 600 |
| `periodic_high` | Periodic | 500 |
| `state_high` | State | 400 |
| `event_normal` | Event | 300 |
| `state_normal` | State | 200 |
| `periodic_normal` | Periodic | 100 |

Derive family and High/Normal scheduling class from this single value; do not
accept conflicting family/priority properties in the new XML grammar. Reject
unknown channel names. ContactReport defaults to `state_normal`; reserved
RouterHealth remains `periodic_high`.

These are logical configuration/scheduling channels, not six physical topics,
six reader/writer pairs or new union branches. Keep three WAN pairs and the
existing two XML-configured DDS MultiChannel bands per family. No new envelope
field or High/Normal receive-filter semantics are introduced. A received frame
is still dispatched by its family, logical topic tag and union branch.

The current prototype's YAML High/Normal settings are unchanged by this
proposed XML naming update.

### Illustrative binding entry

The local LAN port already declares its topic and registered type in Routing
Service XML. The processor property bag might select its WAN branch as follows:

```xml
<value>
  <element>
    <name>act.binding.sensor.tx.port</name>
    <value>lan_in_sensor_reports</value>
  </element>
  <element>
    <name>act.binding.sensor.topic_name</name>
    <value>SensorReport</value>
  </element>
  <element>
    <name>act.binding.sensor.channel</name>
    <value>periodic_normal</value>
  </element>
  <element>
    <name>act.binding.sensor.branch</name>
    <value>sensor_report</value>
  </element>
  <element>
    <name>act.binding.sensor.instance_id</name>
    <value>sensor_id</value>
  </element>
</value>
```

For example, the original SensorReport contains keyed `sensor_id` and a
`readings` sequence. The `sensor_report` union branch contains the **whole
SensorReport**, not a new payload with renamed `samples`. The receiver binds
an explicit LAN output with that same complete type and restores it unchanged,
apart from already-agreed authoritative source restoration where applicable.
The branch must exist in both ends' prepared WAN type XML; it is not in the
current ACT catalog. These property entries alone are not runnable configuration.

### Grammar and validation rules

- XML property names must be unique. Check the original processor property
  entries in launcher/bundle preflight, before RTI converts them to `PropertySet`.
  The processor cannot recover duplicate entries from an already collapsed map.
  Qualify native loader behavior too; direct unsupported launch paths must not
  be advertised as equally validated.
- Reject unknown properties, unresolved variables, missing ports/types/branches,
  empty bindings, conflicting definitions and unsupported grammar versions.
- Validate the six supported channels and their family/priority resolution;
  physical ports and union branches must agree with the derived family.
- Payload binding is all-or-nothing. Reject selected-field copy properties,
  field renaming, slicing, transformations and omitted-field/default rules in
  v1 rather than silently ignoring them.
- Identity/addressing metadata paths remain explicit and validated. They do
  not authorize rewriting arbitrary payload fields or reserved envelope keys.
- Require exact full-type compatibility, including collection
  bounds/dimensions, member IDs, enum/union definitions and optional metadata.
  No implicit narrowing, truncation, stringification or sequence/array conversion.
  Type assignability alone does not establish semantic mapping compatibility.
- Local transmit/receive ports are explicit; a send binding does not imply a
  return route. Both directions use the same full branch type, not inverse
  per-field mappings.

## 5. Runtime design

### A. Load and compile once

Build one immutable mapping/registry snapshot at startup. Resolve property
groups, full types, union branches, keys, metadata and
directions into validated operations. Cross-check actual port types when ports
are available and finish validation before enabling forwarding.

Do not parse metadata paths or build DynamicTypes for every sample. Exact caching
of member indexes versus native hierarchical names follows measurement and
ownership rules, not a premature optimization. No hot schema/mapping updates.

### B. Forward and reconstruct owned samples

Read source loans without modifying them. Create an owned WAN frame, stamp
router-owned metadata, select the declared union branch and copy the complete
original sample. On receive, validate schema/family/tag/branch and restore that
same complete type to the declared LAN output.

Reuse the current full-value union pack/unpack pattern, with native whole-value
copy and exact type checks. Qualify preservation of nested optional presence,
unions and collections without constructing a general field-mapping engine.
Do not introduce opaque octets as a fallback. This is a complete typed copy,
not a zero-copy or allocation-free promise.

Create/reset destination data so absent fields and shorter sequences cannot
retain values from a previous sample. Preserve nested union discriminators and
optional presence. There are no omitted source fields to reconstruct in v1.

### C. Preserve identity and lifecycle

Keep envelope keys `source_id + original_topic + instance_id`. Source identity
comes from the originating node, not producer content or the receiving node.
Use `topic_name` as the original topic tag, preserving existing ACT tags.
The source and destination LAN topic names and full types are identical in v1.
Topic aliases/renaming are deferred.

Compile identity separately from payload copies. Every destination DDS key
must be recoverable from key-only envelope information and the declared key
codec, not from optional payload fields. Payload identity, when also present,
must agree. Preserve the existing ContactReport authoritative-source behavior.

Keep the current instance bound (512 bytes) and reject overflow without
truncation. V1 accepts one existing string identity field, not numeric/composite
encoding. All destination DDS keys must still be recoverable from that identity
and approved authoritative source restoration; reject unsupported key layouts.
A destination key must not merge independent originating sources;
require source-aware keys or a proven globally unique identity contract.
Do not silently change application IDL keys.

Retain loan-safe native key recovery, physical-writer registration and the
current materialized-instance dispose/unregister policy. Mapping does not
solve multi-producer ownership, unseen instances or restart persistence. Test
those explicitly and publish supported/rejected cases before release.

### D. Keep routing and delivery independent

The registry drives both source metadata and original-topic CFT cases. General
application bindings must not call ACT-only `destination`, `run_id` or other
fields unless the XML declares them. Preserve team/addressed/global/control
selection, alias merging, source suppression, feedback protection and reserved
health/config handling.

Keep exactly three WAN pairs. For the reference deployment Event/State reuse
WanEvent and Periodic reuses WanStatus. A custom application needs a
prepared union branch containing its full type, not a new physical WAN topic. Today's State-only
ContactReport catalog is the reference configuration, not an automatic catalog
of all future application types.

Preserve Event reliable retention, State latest-value policy and Periodic
best-effort policy, plus 600/500/400/300/200/100 ordering and shared controller.
All fixed QoS stays in XML; priorities, budgets and input filters remain
explicit. Mapping success proves neither loss-free overload nor bounded outage
recovery, and latest state does not promise every lifecycle transition.

### E. Surface errors

Configuration errors abort activation with binding, direction, property/path,
expected type and actual type. Do not skip bad mappings or fall back to the
ACT catalog. Runtime invalid data produces a diagnosed rejected sample, not a
partial output or success-shaped default.

Use existing logs/status surfaces; specify how startup failures reach the
launcher even if Routing Service itself remains alive with an inactive route.
Never report ready merely because the service process exists.

## 6. Prepare and distribute the shared types

The latest user decision is **define everything explicitly now, add discovery
and setup later**. This supersedes capture/generation as a v1 prerequisite.
For v1, supply the original type definitions, shared envelopes/full-type union
branches and registry explicitly, together with bindings and manual QoS.
Validate and distribute the matching contract to both nodes before startup.
Use existing IDL/XML definitions where available; no discovery/export helper
is required to ship this first version.

Later, automate preparation of that same bundle:

1. Capture the selected topics' complete type graphs from the relevant LANs.
   Record registered names, complete definitions, member IDs/keys, bounds,
   optional/union metadata and provenance. Local discovery does not see a
   remote LAN; merge captures so each end receives all required branch types.
2. Resolve and validate every selected type. Missing metadata, incompatible
   definitions sharing a name or unsupported export must fail visibly. Existing
   XML may be imported for offline cases instead of re-creating schemas.
   Do not infer definitions from sample values.
3. Generate original-type XML, the shared envelopes/unions containing full
   original types, and the branch registry/manifest. Native DDS discovery
   supplies application types; it does not automatically compose our WAN unions
   or choose stable branch labels.
4. Validate and distribute the same generated contract to both nodes. Retain
   manually selected LAN QoS and original-topic bindings. Start Routing Service
   only after the finished type/configuration bundle passes preflight.

The headless capture/export/generation helper and setup/test app are later
work; the current fixed ACT XML/catalog does not already provide them.
Router runtime must not require those tools after preparation.
There are no reduced payload structures or per-field reconstruction rules.

This is the router-layer trade-off with an opaque binary payload: a binary
envelope can carry bytes without the router knowing their original type.
Typed unions need their branch types before endpoints are created; capture
and generation remove manual authoring, not this schema-preparation step.
Binary payload consumers still need the original schema/serialization contract
to interpret those bytes, and DDS tools see the outer byte field rather than
the embedded application structure. This plan retains typed unions.

Keep stable explicit branch labels/member IDs; never derive them from route
ordering. Retain reserved internal branches and their meanings. Validate
allowed family/tag/branch combinations from the registry on both directions.
Adding a custom application mapping must eventually require XML changes only,
not another compiled C++ topic switch.

Ship a shared contract manifest covering wire types, labels/IDs, mapping
semantics, keys and runtime/plugin requirements. Local enabled routes can
differ, but both nodes must agree on each shared wire contract. Compare full
declared types, not only registered names.

Mutable/extensible DDS types do not remove this coupling. Require the same
qualified contract for v1; incompatible changes use coordinated stop/start and
rollback. A manifest hash is an offline/preflight check, not an implemented
peer handshake. Do not claim matching endpoints have automatically compared
mapping semantics or add a new wire field/topic without a separate decision.

## 7. Phased implementation and acceptance gates

### Phase 1 - Freeze the contract with bounded API probes

Freeze the full-type property grammar with one string identity field and
explicitly supplied type/union XML. Discovery capture/export and automatic union
generation are later gates, not prerequisites for this phase. Extend native tests
to complete application types containing mixed nested collections,
empty/shrinking sequences, supported primitive kinds, strings, unions and
optionals. Verify read-only loan and owning-copy lifetimes.

Qualify property duplicate handling, actual endpoint type inspection, XML
variables, native loading of type/QoS/service files and startup-error propagation.
Do not assume a custom XML include mechanism. Choose and test native supported
file composition or an explicitly flattened deployment XML.

**Gate:** reproducible positive/negative matrix; checked XML examples; no
unqualified collection fallback or silent duplicate resolution. Selected-field
properties are explicitly rejected.

### Phase 2 - XML-only ACT vertical slice

Introduce the typed startup model and parse it from processor properties.
Represent the current ACT mapping catalog in XML; preserve its exact semantics.
Launch the prepared service XML directly without `act_rs_config`, YAML files
or YAML parsing for teams/settings. Keep development translation separate.

**Gate:** existing two-/three-topic payload, filtering, priority and lifecycle
tests pass with XML-only startup. Missing YAML/helper files are deliberately
tested. No change to the live custom router or its deployed contract.

#### TypeObject v2 and debug-tool gate before customer types

First qualify the known full-type union contract, then introduce customer
application types/bindings in the following phases:

- Demonstrate actual TypeObject v2 propagation/resolution and complete named
  type definitions for the configured endpoints. Do not infer this from
  mutable types, successful name-only matching or a tool's cached XML.
  Record whether metadata was discovered or explicitly supplied. Check the
  real union/type graph, not just a small standalone type.
- Verify compatible endpoints match under the configured type-consistency
  policy and a deliberately incompatible type is rejected. XML/XSD validity
  is a separate configuration check; TypeObject checks do not validate
  application routing, identity or binding semantics.
- Inspect envelope keys, union labels/active branch, nested original types,
  sequence/array shapes and readable member names using the intended DDS
  debug tools. Inspect representative received samples too. If a tool needs
  supplied XML, document that mode separately from discovery-only inspection.
- Retain whole-payload round-trip, key/lifecycle and binding-negative tests.
  Endpoint matching and a readable type tree are not substitutes for them.

**Gate:** reproducible type-compatibility and tool-inspection evidence before
customer type/binding qualification. No current TypeObject-v2/tool coverage
claim is implied by the earlier four entity-free copy probes.

### Phase 3 - Configurable whole-type union packing

Replace the fixed catalog in the processor path with validated XML full-type
bindings. Reuse whole-value pack/unpack and key recovery, not a new general
copy/reconstruction engine. Add a custom application type with sequences,
arrays and nested structures as one complete prepared union branch.

**Gate:** original LAN -> union branch -> destination LAN whole-payload
equality, including keys and collection shapes. Empty collections and shorter
successive samples do not leak old data. Inputs remain unchanged. Reject a
branch whose type differs from the original LAN type.

### Phase 4 - Generalize registry, metadata and filters together

Replace ACT-name assumptions in the processor path with registry lookups.
Build CFT case selection and addressing/audit operations from that same model.
Add at least one explicitly defined non-ACT application contract, using the same LAN
topic name and full payload type at both ends.

**Gate:** adding another compatible application mapping needs only prepared
type/binding XML, not C++ edits. Three-node native tests verify whole-payload equality,
exactly three WAN pairs, no wrong-family/branch delivery, recipient selection,
source suppression and alias deduplication.

### Phase 5 - Keys, lifecycle and failure qualification

Exercise full/key-only recovery, dispose/unregister/revive, same-key updates,
multiple originating nodes, key bound limits and conflicting payload identity.
Test multiple producers, router/writer restart, late observers and rejected
maps; qualify or explicitly constrain unsupported ownership cases.

**Gate:** destination final instance state is measured, not inferred from WAN
reception. No cross-source key collision, fabricated payload on invalid data,
leaked loans or silent drops; diagnostics distinguish configuration/data/write
failures. Keep persistence/recovery claims within measured scope.

### Phase 6 - Finish and qualify the v1 bundle

Ship the tested plugin, explicitly defined shared types/unions,
manually authored QoS, role-specific XML, environment files,
manifest, start/stop entry points and updated guide. Resolve all locations from
the installed bundle/documented variables, not development checkout paths.
Validate unique properties before RTI map conversion. Keep QoS profiles the
only authority for fixed delivery policies.

Run safe manual command/response and mapped data checks on the configured pair,
then restart from the installed bundle without development tools. Retain
bounded shutdown/cleanup, invalid-config failure and rollback checks.

**Gate:** XML-only clean-install pair, no setup app/YAML/helper dependency,
truthful readiness, documented supported runtime/architecture and measured
results. Full release also requires the existing processor health/status,
team/scope, monitoring, recovery/load and mission gates; this mapping plan is
not authorization to deploy or a substitute for them.

### Phase 7 - V2 authoring and test app

Implement headless discovery/import and generation of the v1 bundle, then add a
full-type binding editor and isolated actual-route ping/pong validation.
Qualify lossless capture/export/import and stable union generation before
claiming users need no handwritten application schemas.
Reuse the v1 grammar/validator and do not maintain a second mapping engine in
the GUI. Treat generation, testing and activation as distinct operations.
Selected-field/subtree mapping is deferred optional future work, not a
prerequisite for either the v1 runtime or an initial v2 setup/test app.

## 8. Confirmed boundaries and remaining release limits

The confirmed v1 boundaries are deliberately narrow:

1. **Binding compatibility:** the union branch and LAN sample have the same
   complete type; no payload field selection, renaming or conversions.
2. **Metadata paths:** only for declared identity/addressing/audit extraction
   and approved source restoration, not arbitrary payload copy operations.
3. **Keys:** one existing string field, unchanged LAN keys and explicit
   source-uniqueness/key-only recovery checks; other identity encodings deferred.
4. **Payload preservation:** every field travels in the original type;
   preserve optional presence, unions and nested collections with no omitted-field rules.
5. **Schema artifacts:** explicitly defined, validated and packaged for v1;
   discovery-driven capture/generation and the setup/test app are deferred.
6. **Resource limits:** agree finite supported mapping count, path depth,
   collection/sample size and instance/queue budgets. Existing DDS resource
   limits and earlier failed load/recovery gates are not superseded.

Begin with the current reference workload and agree measured release limits
before shipping. Detailed property spelling and file-composition behavior still
need native qualification; the chosen workflow is not an implemented feature.

## 9. Sources and related plans

- [RTI Routing Service 7.7 tutorials: Assign transformation](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/services/routing_service/tutorials.html#example-changing-some-values-in-data).
- [7.7 processor API](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/api/routing_service/api_cpp/group__RTI__RoutingServiceProcessorModule.html).
- [7.7 PropertySet header reference](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/api/routing_service/api_cpp/PropertySet_8hpp_source.html).
- [7.7 Modern C++ DynamicData API](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/api/connext_dds/api_cpp2/classdds_1_1core_1_1xtypes_1_1DynamicData.html).
- [7.7 type consistency and assignability](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/extensible_types_guide/extensible_types/Verifying_Type_Consistency__Type_Assignabilit.htm).
- [7.7 Admin Console DDS Data Type View](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/tools/admin_console/p2_administration/features_reference/ref_views/view_data_type_view.html).
- [7.7 Routing Service configuration](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/services/routing_service/configuration.html).
- [Three-topic processor plan](routing-service-three-topic-plan.md).
- [V1/v2 setup and release split](routing-service-c2-platform-setup.md#agreed-release-split).
- [Product-gap record](product-gaps.md).
- [Verified Connext caveats](connext-ai-issues/connext-ai-issues.md).
