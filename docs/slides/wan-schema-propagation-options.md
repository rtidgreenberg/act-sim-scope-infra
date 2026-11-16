---
marp: true
theme: rti
paginate: true
size: 16:9
title: "Two WAN payload architectures"
description: "Generated typed unions versus fixed envelopes with binary payloads"
footer: "RTI Connext | WAN schema cost unmeasured | 2026-11-16"
---

<!-- _class: compact -->

# Two WAN payload architectures

| | Typed unions | Binary payloads |
|---|---|---|
| **WAN schema** | Envelope + all declared payload branches | Fixed envelope + serialized payload bytes |
| **Application types** | Generate from LAN; discover over WAN or preload XML | Local LAN discovery or preload; no WAN payload schema |
| **Inspection / CFTs** | Typed branches; filter discovered labels / supported fields | Outer metadata only; decode payload locally |
| **New payload type** | Version/rebuild shared union contract | Same outer schema; verify payload type + encoding |
| **Evidence** | Command union spikes passed (one Event) | Zenoh source pattern; our Connext path untested |

**Both retain Event / State / Periodic (currently Telemetry), plus priority.**

> **Binary avoids WAN application schemas, not all schemas.** The fixed envelope and compatible local payload definitions are still required.

<!--
Companion record: ../wan-schema-propagation-options.md.
Evidence: ../../spikes/discovered_union_catalog/README.md.

This slide records the 2026-11-16 discussion; it does not change the production
wire contract or rename Telemetry. Periodic is the conversational name for the
freshness-first delivery family. Exactly three shared WAN names are intended;
priority is separate and does not create per-priority topic names.

There are two primary payload architectures, not four unrelated choices:
runtime discovery and preloaded/generated XML are schema-distribution variants
of typed unions; an optional State catalog adds inventory but is not required.
The alternative is a fixed envelope containing serialized original payload
bytes and metadata. No application union graph needs to cross the WAN in that
design, but the outer DDS schema remains fixed and both original applications
still need compatible types. Local LAN discovery or preload can supply the
receiving router's payload type if it decodes/introspects the bytes.

The binary path is proposed for our Connext gateway, not locally tested.
Read-only Zenoh DDS bridge source inspection at commit
0e226b35064af2fd877a9e8af8a8e788203cca17 found dds_takecdr -> serialized Zenoh
payload -> ddsi_serdata_from_ser_iov / dds_writecdr. DdsEntity.type_info is
serde(skip); create_topic uses local type information or a blob-topic path.
Sources, pinned to that commit:
https://github.com/eclipse-zenoh/zenoh-plugin-dds/blob/0e226b35064af2fd877a9e8af8a8e788203cca17/zenoh-plugin-dds/src/dds_mgt.rs#L71-L82
https://github.com/eclipse-zenoh/zenoh-plugin-dds/blob/0e226b35064af2fd877a9e8af8a8e788203cca17/zenoh-plugin-dds/src/dds_mgt.rs#L413-L450
https://github.com/eclipse-zenoh/zenoh-plugin-dds/blob/0e226b35064af2fd877a9e8af8a8e788203cca17/zenoh-plugin-dds/src/route_zenoh_dds.rs#L381-L423
https://github.com/eclipse-zenoh/zenoh-plugin-dds/blob/0e226b35064af2fd877a9e8af8a8e788203cca17/zenoh-plugin-dds/src/dds_mgt.rs#L580-L609

This is not a claim that Zenoh transports no metadata, infers unknown field
schemas, or implements the same three-topic DDS topology. Connext binary
serialization/forwarding, representation/type identity, keyed lifecycle,
unknown types, priority behavior and WAN cost require a separate spike.
Opaque payload fields are not directly visible to DDS CFTs; topic/source/
audience selection can use fixed outer metadata. No payload schema distribution
is needed on WAN only when compatible payload knowledge is available locally.

Both spikes used three separate processes, no generated application classes,
and no application type XML in either gateway. Only the fixture loaded the
original command definitions. The gateway discovered complete LAN structures
and built an Event envelope/union at startup before creating its WAN writer.

The catalog path sent 36 inputs, restored six full original commands and
measured native Event push deltas of 3/3/0. State held source, revision, WAN
topic/envelope name, original topic/type names, branch names and labels;
it did not contain full structural definitions or TypeObjects.

Discovery-only mode sent 60 inputs and measured 3/3/0/0/6 native push/receive
counts for Move, Mode, none, incompatible same-name Mode, and both.
It discovers topic-named branches with sparse labels 17/42 and exact local
subscription types, rejects an unmapped same-type reader, and reconstructs
whole original LAN payloads. There are no State endpoints, explicit topic
selection messages or per-sample original_topic field in that mode.
Harness phase/count synchronization remains. Reader replacement and local
discovery convergence occur at controlled phase boundaries, not in a qualified
production continuous-rebinding loop.

No native discovery packet capture or startup/late-join/restart type-byte
measurement was made. Do not claim a schema graph crosses in one packet or
that it is always TypeLookup rather than inline. Preloading matching types
does not by itself suppress full type advertisements; configure and verify
the effective behavior with real captures. Discovery/filter/protocol traffic
still remains. The deployed mesh was untouched.

The Python setup app, complete type export, fleet/C2 contract merge, stable
labels/member IDs, manifest/version policy, three-family grouping and priority
scheduling are proposed, not implemented by this spike. Both ends need the
agreed wire contract, while local enabled routes and receive interests may
differ. New branches require versioning/replacement; selecting existing
branches does not modify the union schema. Fixed QoS remains XML-authored.

Other discussed approaches are documented in the companion:
- Native per-application DDS topics with DynamicData preserve direct schema
  discovery and introspection but are a different WAN topology.
- Publishing full encoded type definitions on State was raised, not tested;
  that still distributes schemas and needs bootstrap/version/decode rules.
- A fixed measurement/value catalog was a telemetry alternative, not a
  qualified general-command solution.
- A generic recursive value model for commands was mentioned but not designed.
- An opaque binary envelope requires original schemas/codec to inspect inner
  commands; standard DDS tools see the outer fields and bytes.
-->
