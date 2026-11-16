# Quickstart

WAN router setup and inspection tool plan.

Date: 2026-11-16.

**Status: proposed tooling, not an implemented quickstart command or deployment.**
Use RTI Doctor as the likely foundation. No live-mesh change is authorized by
this plan.

## Goal

Run a Python terminal UI on a boat or C2, configure discovery peers, discover
local DDS topics and types, and add routes one topic at a time. Assign each
topic to a logical channel that determines its WAN family and priority, generate a
validated XML deployment bundle, deploy matching contracts, and inspect the link.

The initial workflow targets **typed unions with generated/preloaded schemas**.
The [schema propagation comparison](wan-schema-propagation-options.md) retains
the binary-envelope alternative; it is not silently selected by this plan.

## Starting point

- [connext_autonomy_reference](https://github.com/rtidgreenberg/connext_autonomy_reference).
- [RTI Doctor](https://github.com/rtidgreenberg/connext_autonomy_reference/tree/main/tools/rti_doctor):
  [README](https://github.com/rtidgreenberg/connext_autonomy_reference/blob/main/tools/rti_doctor/README.md)
  and [CLI reference](https://github.com/rtidgreenberg/connext_autonomy_reference/blob/main/tools/rti_doctor/CLI_REFERENCE.md).
- [Discovered-union command spikes](../spikes/discovered_union_catalog/README.md).
- [Typed XML preparation plan](routing-service-xml-mapping-plan.md#6-prepare-and-distribute-the-shared-types).

Doctor documents terminal participant/topic/endpoint navigation, type-resolution
diagnostics and sample reading using discovered types. Evaluate reuse of that
foundation before building a separate browser. Complete type XML export, the
peer editor, route wizard, bundle generation and deployment integration are
proposed additions, not verified existing Doctor capabilities.

## Operator workflow

```text
Project / node -> Domains and profiles -> Peers -> Discover topics
    -> Add topic -> Direction -> Channel -> Mapping checks
    -> Pending routes -> Add another / Edit / Remove
    -> Import C2/boat captures -> Generate XML -> Validate / Isolated test
    -> Preview deployment -> Deploy / Roll back -> Inspect link
```

### 1. Create or open a link project

Record node identity, boat/C2 role, LAN domain(s), WAN domain, transport and
existing QoS profiles. Keep domain IDs within 0-232. Separate the shared wire
contract from node-specific transport, peers and enabled-route settings.

Saving or editing a project does not mutate a running router.

### 2. Configure peers

Add C2 and any required boat gateway discovery peers one at a time. Use
addresses reachable through the actual WAN transport, not guessed LAN or
container addresses. Show the selected interface/transport and effective
discovery settings, then generate node-specific initial-peers XML.

Validate entries and diagnose missing discovery separately from unresolved
types. A peer list is not an application-destination list, authorization rule
or forwarding-hop plan. Listing only C2 does not automatically make C2 an
application relay.

### 3. Start passive discovery

Attach Doctor's discovery/inspection foundation using the intended profiles.
Show participants and endpoints without automatically publishing test samples.
Active probes, captures, connectivity checks and delivery tests require explicit
operator action and an owned safe test environment.

Do not patch generated configuration or restart one router inside a live mesh.

### 4. Browse topics and inspect types

For each topic show local readers/writers, registered type name, type-resolution
status, keys and inspectable fields. Obtain complete type graphs, including
member IDs, bounds, nested structures, unions, sequences and arrays.

Distinguish a visible topic/type name from an available complete definition.
Missing metadata is a diagnosed blocker; never infer a schema from sample
values. Qualify complete export support in the installed Python binding.

Capture discovered endpoint QoS as well as types, on both relevant LANs.
Record producer offerings and consumer requirements separately, including
partition information. Discovery is not a complete export of every local
queue/resource policy. Allow an explicit offline endpoint-QoS manifest when
applications are unavailable; report unresolved requirements rather than
silently assuming defaults.

### LAN QoS is a required bundle input

For each configured direction, prepare the Routing Service LAN input
DataReader and destination LAN output DataWriter QoS. Select compatible
external XML profiles using captured endpoint requirements and operator intent;
do not blindly copy a writer's policies onto a reader or vice versa.
Multiple endpoints with differing requirements need explicit compatibility
checks and visible conflict handling. Queue/history/resource budgets require
deliberate configuration, not inference from type XML.

The [current renderer](../router/routing_service/run.py) explicitly sets
`auto_configure_qos=false` and references configured port profiles.
The [2026-11-16 native qualification](../router/routing_service/README.md#native-lan-auto-qos-qualification)
proved reliability/durability inference and exact-payload forwarding with our
processor using `ON_DOMAIN_MATCH`. Finite deadline requirements still needed
an explicit policy. With `IMMEDIATE`, enabled cases did not create the LAN
endpoint within the observation window. With `ON_DOMAIN_MATCH`, one absent
configured LAN input prevented the shared processor route from starting and
forwarding the other topic.

Reuse native inference where qualified rather than duplicating it, but resolve
that shared-route startup barrier before enabling it in generated deployments.
The renderer remains unchanged. Multiple-endpoint conflicts, replacement and
the full policy precedence rules still require qualification.

This setup workflow must export complete schemas **and** generate explicit LAN
QoS/profile bindings. Application LAN compatibility must not alter the shared
WAN family policies, channels or flow-controller configuration.

### 5. Add one topic

Choose:

- Original LAN topic and discovered type.
- Direction: local producer export, local consumer import, or both where allowed.
- Logical channel: `event_high`, `periodic_high`, `state_high`,
  `event_normal`, `state_normal` or `periodic_normal`.
- Instance-key handling and audience/destination metadata mappings.
- LAN input-reader/output-writer QoS profile selections and compatibility status.

Use the [agreed channel mapping](routing-service-xml-mapping-plan.md#user-facing-channels):
derive the physical Event/State/Periodic topic and sample priority from the
channel, rather than asking for independently conflicting settings. The existing
development router's Telemetry name is distinct from the proposed XML contract;
do not rename a running deployment.

Reuse validated mappings where available. Do not guess command semantics from
field names. Preserve the [shared asynchronous flow-controller design](routing-service-three-topic-plan.md#confirmed-configuration-choices)
and the two XML-configured DDS MultiChannel High/Normal bands per family.
The six logical channels select sample priorities within that design; they
do not create six WAN topics or writer/reader pairs. Reliability/history/durability
remain family/writer policy choices, not independent per-branch settings.

### 6. Review and add the next topic

Show a pending-route table with topic/type, direction, channel, derived family
and priority, mapping and validation status. Allow inspect, edit and remove
before adding another.

These are preparation-time edits, not live union mutations. Receive interests
are separate from the complete shared union schema.

### 7. Merge boat and C2 captures

Import required definitions from C2 and other boats, including C2-originating
commands. A local boat cannot discover a remote LAN it cannot see.

Resolve conflicting definitions explicitly. Generate one agreed shared contract
with stable discriminator labels/member IDs, not incompatible unions built
independently from discovery-arrival order.

### 8. Generate and preview the deployment bundle

Include:

- Original DDS type XML and the three union/envelope definitions.
- Original-topic/branch bindings and enabled routes.
- External XML QoS and scheduling selections.
- LAN reader/writer QoS profiles and their native port references, with a
  report of captured requirements, selected policies and unresolved conflicts.
- Node-specific peer lists and transport settings.
- A versioned contract manifest and rollback reference.

Present one bundle even if the existing runtime format splits types, routes
and QoS across XML files/includes. Preview effective peers, routes and schema
changes before saving. No new runtime configuration grammar is fixed here.

### 9. Validate and run an isolated test

Load generated types/QoS using the installed Connext environment and validate
the router configuration. Check type identities/conflicts, mappings, supported
domain IDs, explicit resource budgets and matching shared manifests.
Validate effective LAN input/output QoS against the intended producers and
consumers at both ends; a matching shared WAN contract does not prove LAN
compatibility. Verify native endpoint matching and intended delivery in the
isolated test.

Offer a separately confirmed, isolated test with one selected route before
scaling. Verify actual peer discovery and original payload equality; process
startup alone is not success. Do not test DDS routes against the retained mesh.

### 10. Deploy matching contracts

Both boat gateway and C2 need the agreed wire definitions. Enabled local routes,
receive interests and initial-peers lists may differ.

Preview the affected nodes and require deployment confirmation. Retain the
previous matching bundle for rollback. Use coordinated owned lifecycle
teardown/rebuild/relaunch for schema changes; never overwrite active binaries
or edit running generated configuration.

### 11. Inspect and debug the link

Compare expected peers with discovered participants. Inspect types/branches,
reader/writer compatibility, route status and effective receive interests.
Keep discovery, type-resolution, routing and actual delivery failures distinct.

Changing interest in existing branches does not require a new union definition.
Adding a payload type or moving it between families requires a contract update
and coordinated endpoint replacement/versioning. Active delivery/priority tests
remain explicit operator actions.

## Implementation sequence and gates

| Phase | Deliverable | Acceptance gate |
|---|---|---|
| 1. Doctor reuse and capture | Identify reusable discovery/UI components; export/import complete type graphs and available endpoint QoS | Round-trip representative nested, keyed, sequence/array and union definitions; identify missing QoS evidence; reject incomplete/conflicting types |
| 2. Project, peers and route wizard | Persist node profiles, peer entries and one-at-a-time topic/channel selections | Save/reopen preserves selections and derived priorities; invalid peers/domains/channels/mappings reject visibly; no live writes |
| 3. Contract and XML generation | Deterministic shared union contract, LAN QoS profiles/port bindings, existing-format runtime XML and manifest | Same inputs produce the same contract; real Connext loading, LAN compatibility checks and runtime preflight pass; existing bundle remains intact on failure |
| 4. Isolated setup/debug workflow | Import both-end captures, run an owned route test and show diagnostics | Correct peers, exact payload restoration, native filter push counts, expected priority behavior and clean teardown |
| 5. Deployment and rollback integration | Confirmed coordinated activation and previous-bundle restore | Both-end contract consistency, verified delivery, explicit failure reporting and tested rollback |

Preserve the agreed scheduling mechanism and nominal shared 2 Mbps starting
budget. Settle remaining resource/admission limits and measurable priority
acceptance thresholds before their gates; qualify command latency, starvation,
and repair traffic under congestion rather than treating priority labels as proof.

## Scope and evidence boundaries

- The existing spikes prove one Event union, including discovery-only local
  reader selection. They do not prove this UI, complete export, three-family
  generation, priority scheduling or deployment workflow.
- Fixed DDS QoS remains XML-authored. Runtime identity and routing selections
  are not permission to move fixed transport policies into Python.
- Preloading types does not alone prove full metadata is absent on WAN.
  Measure startup, late join and restart traffic before claiming a saving.
- Application lifecycle, restart, multi-producer ownership, schema evolution,
  security and continuous discovery-driven rebinding need their own gates.
- Exactly three shared WAN names remain the intent; priority does not create
  additional per-priority topics.
