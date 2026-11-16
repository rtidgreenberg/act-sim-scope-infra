# WAN schema propagation options and setup workflow

Date: 2026-11-16.

Status: discussion record, isolated spike evidence, and proposed setup workflow.
This does not change the production wire contract, configuration or running mesh.

## Summary

**Generic gateway code does not mean schema-free communication.** A receiver
must obtain the payload structure through discovery, a deployment bundle, or
an application-specific schema/decoder path.

There are now **two primary payload architectures** to compare:

| | Typed unions with generated schemas | Binary payloads in fixed envelopes |
|---|---|---|
| WAN contract | Envelope plus the original structures as union branches | Fixed routing/identity metadata plus serialized payload bytes |
| Application definitions | Generated from LAN discovery; learned over WAN or distributed as XML | Learned on each local LAN or preloaded; no original application type graph needs to cross WAN |
| Generic inspection | DDS tools can inspect the typed branches | DDS tools see the outer envelope; inner inspection needs a local payload type/decoder |
| WAN CFTs | Select branch labels and supported typed fields | Select known envelope fields such as original topic, source and destination; not arbitrary inner fields |
| Adding a payload type | Update/version the shared union contract | Outer type can stay unchanged; original type identity, encoding and compatibility still need validation |
| Evidence here | Whole-command union spikes passed | Zenoh DDS bridge source inspected; our Connext binary-envelope path is not yet spiked |

**"No WAN application schema" does not mean "no schema."** The binary option
still has a fixed DDS envelope schema. The original producers/consumers still
need compatible payload definitions, and the receiving router needs a local
definition if it decodes or introspects the payload. Opaque forwarding is
different from deserializing application fields.

The discussion retains exactly **three shared WAN topic names**, not one topic
per priority. We called the delivery families **Event / State / Periodic**;
the current router's physical name for the periodic family is **Telemetry**.
No live rename is authorized here. The subsequent
[XML channel design](routing-service-xml-mapping-plan.md#user-facing-channels)
uses one of six logical channels to derive family and sample priority, with
three physical Event/State/Periodic topics, High/Normal DDS MultiChannel bands,
and the [shared asynchronous flow controller](routing-service-three-topic-plan.md#confirmed-configuration-choices).

The two completed spike paths dynamically construct an Event union from
discovered command types. Both deserialize and restore complete original
commands without generated application classes or application type XML in the
gateways. The second path needs **no State catalog**: union discovery plus
matching local subscriptions supplies the mapping and interests.

However, a discovered union describes **all declared branches and their nested
type dependencies**, not just the branches selected by one receiver.
Pre-generating and deploying matching XML is one way to move that schema
distribution out of the operational WAN path while retaining typed unions.
Binary envelopes are the alternative that avoids aggregate application unions
on the WAN. Neither that binary path nor the setup tool and combined
three-family/priority integration is implemented by these spikes.

## Options explored

| Option | How the receiver obtains structure | Main benefit | Cost / limitation | Evidence |
|---|---|---|---|---|
| Native application DDS topics + DynamicData | Discover each original type | Original field introspection; no generated consumer classes | Keeps per-application WAN types/topics rather than the three shared unions | [Original type-discovery spike](../spikes/type_discovery/README.md); not the new three-topic design |
| Runtime unions, discovery only | Discover the complete envelope/union type graph | No hand-authored union XML or extra catalog mapping; whole commands stay typed | Aggregate schema crosses the WAN when not already available; startup contract still needs coordination | [Discovery-only command spike](../spikes/discovered_union_catalog/README.md#discovery-only-results-2026-11-16) |
| Runtime unions + State catalog | Same DDS type discovery, plus a catalog of names/bindings | Explicit inventory or operator-selection metadata | Catalog does not remove union type propagation; redundant if local subscriptions define interests | [Original catalog-mode spike](../spikes/discovered_union_catalog/README.md#results-2026-11-16) |
| Prepared/preloaded union XML | Capture/generate offline, distribute a matching deployment bundle to gateways | Moves schema delivery into preparation/deployment | Shared schema/version coordination; preload alone does not prove full metadata is absent on WAN | Proposed workflow below; not end-to-end implemented or wire-qualified |
| Fixed envelope with serialized payload + local schema/codec | Discover original types on the destination LAN or preload them; explicit distribution is another variant | No aggregate application union or original application type graph required on WAN | DDS tools see bytes, not embedded command fields; decoding/type validation and lifecycle support need qualification | [Zenoh source pattern](#binary-envelope-option-and-zenoh-source-evidence); our Connext path untested |

Two other ideas were distinguished from those options:

- A **measurement/value catalog** is a telemetry-model alternative, not the
  selected solution for preserving arbitrary command structures. See the
  [typed-versus-catalog telemetry slide](slides/telemetry-typed-or-catalog.md).
- A **fixed recursive structured-value model** could represent structured
  commands without deployment-specific DDS types, but would need command
  schemas, validation and tooling. It was mentioned, not designed or qualified.

Publishing **full serialized type definitions on State** was also raised, but
not tested. The actual catalog spike published names, bindings and a revision,
not TypeObjects. Explicit schema records would need a defined representation,
bootstrap ordering, dependency/version handling and integration with decoding.
They would still consume WAN bandwidth if distributed over that link.

## Binary-envelope option and Zenoh source evidence

The proposed analogue using our three DDS WAN topics is:

```text
source LAN discovery -> original DynamicType
original sample -> serialize payload + populate fixed envelope metadata
Event / State / periodic WAN topic -> fixed envelope and bytes
destination LAN discovery -> matching original DynamicType
verify type/encoding -> decode or qualified opaque forwarding -> original LAN
```

An illustrative envelope carries original topic name, source/destination,
payload schema identity, encoding/representation, instance identity and bytes.
This is not a frozen IDL. Topic/type names alone do not prove compatibility;
unknown or incompatible payloads need explicit rejection. The receiver can use
a type learned from its own local reader rather than learning all remote union
branches over the WAN.

The sender's configured family/priority is independent of that representation.
WAN filters can select the fixed topic/source/audience metadata without decoding
the payload. Required payload fields for additional WAN filtering would need
intentional metadata extraction or a different design; a DDS CFT does not see
inside an opaque byte sequence.

### What was found in Zenoh

Read-only inspection of `eclipse-zenoh/zenoh-plugin-dds` at
[`0e226b35064af2fd877a9e8af8a8e788203cca17`](https://github.com/eclipse-zenoh/zenoh-plugin-dds/tree/0e226b35064af2fd877a9e8af8a8e788203cca17)
found:

- The DDS-to-Zenoh listener uses `dds_takecdr` and publishes serialized sample
  bytes under the route's Zenoh key:
  [DDS-to-Zenoh path](https://github.com/eclipse-zenoh/zenoh-plugin-dds/blob/0e226b35064af2fd877a9e8af8a8e788203cca17/zenoh-plugin-dds/src/dds_mgt.rs#L413-L450).
- The reverse path builds serialized DDS sample data from the received bytes
  and calls `dds_writecdr`, rather than decoding every application field:
  [Zenoh-to-DDS path](https://github.com/eclipse-zenoh/zenoh-plugin-dds/blob/0e226b35064af2fd877a9e8af8a8e788203cca17/zenoh-plugin-dds/src/route_zenoh_dds.rs#L381-L423).
- The endpoint record includes topic/type names and QoS, but `type_info` is
  marked `serde(skip)`, excluding it from serialized discovery records:
  [endpoint metadata](https://github.com/eclipse-zenoh/zenoh-plugin-dds/blob/0e226b35064af2fd877a9e8af8a8e788203cca17/zenoh-plugin-dds/src/dds_mgt.rs#L71-L82).
- Topic creation uses discovered type information when available; without it,
  it calls `cdds_create_blob_topic`:
  [typed/blob topic creation](https://github.com/eclipse-zenoh/zenoh-plugin-dds/blob/0e226b35064af2fd877a9e8af8a8e788203cca17/zenoh-plugin-dds/src/dds_mgt.rs#L580-L609).

This is evidence of **serialized forwarding without carrying an aggregate union
schema through Zenoh**, not automatic schema inference from payload bytes.
It is not a proof of all Zenoh configurations, end-to-end schema validation,
or equivalent Connext APIs and behavior. Zenoh's key-based routing is also not
the same topology as three shared DDS envelope topics.

### Required binary-path spike

Verify Connext serialization/serialized I/O, local-type lookup and exact payload
reconstruction with no application types propagated over the WAN. Include
representation/encapsulation compatibility, schema-identity checks, unknown
types, key/instance preservation, dispose/unregister, restart handling and
native metadata CFT push counts. Qualify queue/history/priority behavior for
all three families separately. Measure wire metadata and payload overhead
rather than assuming a bandwidth saving from the source comparison.

## What union discovery provides

The sender first learns complete LAN types and builds a union before creating
its WAN writer. The receiver discovers that finished type and can inspect:

```text
discriminator label -> branch name -> complete original payload type

17 -> Cmd_Mode -> SpikeModeCommand
42 -> Cmd_Move -> SpikeMoveCommand
```

In the discovery-only spike, branch names equal original topic names. Labels
are integers, not topic-name strings; an enum discriminator was not needed.
Branch names must be valid type-member identifiers. Arbitrary topic names need
validation and an explicit reversible mapping if they cannot be used directly.

All declared labels/types are part of the type graph even if some branches
never carry samples. Five distinct payload types in a union mean all five
definitions plus nested dependencies, not necessarily five packets or one
single transfer. One application sample carries only its selected branch.
Shared dependent types need not be interpreted as separate complete copies
for every branch.

The receiver does not independently recreate the union in discovery order.
It reads the advertised definition. Independent senders still need agreed
labels, member IDs, definitions and deterministic construction. The spike's
sorted-name/arithmetic labels are a fixture, not a production evolution rule.
Adding a branch does not mutate an already-created endpoint's type in place.

## Runtime subset selection and filtering

The receiver matches discovered branches to **alive local application readers**
by topic name and exact discovered `DynamicType`. Registered type names alone
are insufficient. The spike uses conservative equality, not general XTypes
assignability, and diagnoses/excludes mismatches.

It translates the matching names into labels for a content-filtered topic
(CFT), for example:

```sql
(payload._d = 17 OR payload._d = 42)
AND destination = 'Platform_30'
```

Each WAN family can have its own selected-branch filter. Filters select data
samples; they do not trim the union's advertised schema. Priority remains
the sender's configured scheduling choice, independent of receiver interest.
Priority does not replace reliability, history, durability or other writer
policies, and union branches cannot each choose a different writer reliability.
Fixed DDS policies belong in external XML profiles.

The spike recreates a filtered reader at controlled phase boundaries. It does
not qualify in-place CFT updates, continuous discovery-driven rebinding under
traffic, atomic membership changes, or treatment of all queued/in-flight data.
Local partition/CFT/QoS negotiation and authorization are not generalized by
topic/type equality. Interests are not permissions.

## Proposed Python setup and deployment workflow (typed-union option)

The detailed operator workflow and implementation gates are maintained in the
[Quickstart plan](quickstart.md): reuse RTI Doctor, configure peers, discover
types, add topics one by one with a logical channel, merge boat/C2 captures,
generate and validate XML, then deploy and inspect through the owned lifecycle.

This is automatic schema preparation, not elimination of deployment-specific
wire types. The peer editor, route wizard, complete XML export/generation and
deployment integration remain proposed additions, not verified existing
Doctor features.

## WAN-cost qualification required

Do not equate preloading with disabling discovery or zero schema traffic.
Discovery announcements, identifiers, reader-filter updates and protocol
maintenance still exist. Type information may be available inline or through
lookup depending on effective configuration; these spikes do not distinguish
those paths or measure their bytes.

Before claiming a WAN saving, capture the real link and compare runtime
discovery with prepared types for startup, late join, restart and settled
operation. Verify whether complete metadata is still advertised or requested,
including by monitoring tools, and how unknown/mismatched schemas fail.
Consult the [verified Connext correction log](connext-ai-issues/connext-ai-issues.md)
rather than assuming identifier-only propagation from a preload setting.

## Evidence and remaining work

Connext 7.7.0; three separate processes in owned private network/IPC namespaces;
no production changes or live-mesh interaction:

| Spike path | Source commands | Selected receives / native Event DATA pushes | Result |
|---|---:|---|---|
| Catalog | 36 | Move 3/3; Mode 3/3; none 0/0 | Complete original LAN equality; late retained catalog; two rejection checks |
| Discovery only | 60 | Move 3/3; Mode 3/3; none 0/0; incompatible 0/0; both 6/6 | Complete original LAN equality; sparse-label reflection; same-name structural rejection; no State endpoints |

The no-State path also excludes an unmapped local topic using an otherwise
valid payload type, rejects wrong destinations/duplicates, uses no explicit
topic-selection message, and omits per-sample `original_topic` metadata.
Test progress messages and phase/read-count barriers remain; no routing map
or schema is passed through them.

These are **one-Event-union proofs**, not a completed three-topic deployment.
The binary alternative has source-inspection evidence only, not a local
Connext implementation or an executed binary-path spike.
Still open: setup/export tooling, complete fleet contract generation,
three-family grouping and priority qualification, bounded load/resources,
schema updates, stable multi-sender compatibility, security, lifecycle/restart
behavior, production continuous-interest handling and measured WAN schema cost.

See the [spike README and reproduction commands](../spikes/discovered_union_catalog/README.md),
the [typed XML preparation plan](routing-service-xml-mapping-plan.md#6-prepare-and-distribute-the-shared-types),
and the [one-slide comparison](slides/wan-schema-propagation-options.md).

## Tooling reference: RTI Doctor

- Repository: [connext_autonomy_reference](https://github.com/rtidgreenberg/connext_autonomy_reference).
- Candidate starting point: [RTI Doctor](https://github.com/rtidgreenberg/connext_autonomy_reference/tree/main/tools/rti_doctor),
  with its [README](https://github.com/rtidgreenberg/connext_autonomy_reference/blob/main/tools/rti_doctor/README.md)
  and [CLI reference](https://github.com/rtidgreenberg/connext_autonomy_reference/blob/main/tools/rti_doctor/CLI_REFERENCE.md).

The user expects to likely use RTI Doctor as the basis for the WAN link
setup/introspection tool. Evaluate reuse before creating a separate terminal UI:
discover local topics/types, select topics one by one, assign a WAN family and
priority, inspect the resulting contract, and generate the XML/type/configuration
deployment bundle.

This records the intended tooling direction, not a completed reuse assessment.
Type export, bundle generation and compatibility with this router still need
verification; no dependency or code integration is introduced here.
