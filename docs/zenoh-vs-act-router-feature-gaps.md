# Zenoh vs ACT Router: Feature Gaps and Tradeoffs

This document compares the ACT C++ DDS router in this repository with the upstream Eclipse Zenoh router, `zenohd`. It identifies capabilities present in Zenoh that the ACT router does not currently provide, while also recording areas where the ACT router is stronger or more specialized.

The comparison is based on:

- The ACT implementation inventory in [router-feature-inventory.md](cpp_router/router-feature-inventory.md).
- The upstream Zenoh inventory in [zenoh-router-feature-inventory.md](zenoh-router-feature-inventory.md).
- ACT's [router README](../router/README.md) and [end-to-end test coverage](../router/test_e2e/README.md).
- Upstream [`zenohd`](https://github.com/eclipse-zenoh/zenoh/tree/main/zenohd), [`zenoh`](https://github.com/eclipse-zenoh/zenoh/tree/main/zenoh), and [`plugins`](https://github.com/eclipse-zenoh/zenoh/tree/main/plugins).

**Boat-network update (2026-11-08):** the composite-instance, last-value, and
radio-queue comparison below supplements the earlier general feature inventory.
It does not claim a new full audit of either implementation or a measured
Connext-versus-Zenoh bandwidth result.

## Executive Comparison

| Area | ACT DDS router | Zenoh router | Result |
|---|---|---|---|
| Primary protocol | Connext DDS/RTPS | Zenoh protocol | Different protocol families, not a drop-in feature comparison |
| Routing model | Explicit YAML route graph with DDS participants/topics | Dynamic key-expression routing across sessions | ACT is more explicit; Zenoh is more general and dynamic |
| Runtime roles | `control` and `platform` | Router, peer, and client | ACT is domain-specific; Zenoh supports broader deployments |
| Data model | DDS types, QoS, partitions, ContentFilteredTopics | Key expressions, payloads, declarations, queries | ACT preserves DDS semantics; Zenoh has broader data operations |
| Discovery | DDS builtin discovery, inline TypeObjects, optional SPDP2/SEDP | Scouting plus session declarations | Zenoh has broader session discovery; ACT is DDS-native |
| Query/storage | Not implemented as router features | Queryables, `get`, storage/query via plugins | Major ACT gap |
| Transports | UDPv4-only router participants in the current design | TCP, UDP, TLS, QUIC, WebSocket, Unix sockets, serial, multilink, optional shared memory | Major ACT gap |
| Extensions | Internal C++ seams; no runtime plugin ABI | Dynamic plugin manager and backend API | Major ACT gap |
| Administration | DDS command/status topics, route controls, journal | Admin space plus optional REST/plugin control | Different strengths; ACT has richer domain-specific control evidence |
| Health | Presence, mesh roster, stale/dead transitions, link metrics | Runtime/admin/plugin status, transport/security facilities | ACT is stronger for the current operational mission; Zenoh is broader |
| Security | Connext QoS/configuration; no comparable router-level auth surface documented | TLS, public-key/password authentication features, admin permissions | Major ACT gap |
| Lifecycle fidelity | Live samples only; keyed dispose/unregister mirroring deferred | Native Zenoh semantics, not DDS lifecycle semantics | Different models; ACT has a known DDS gap |
| Composite instance identity | DDS composite keys; proposed WAN unions use outer identity fields | Encode identity components in a hierarchical key | Both can represent independent instances; different representation |
| Last stored value per instance | Per-instance DDS history through configured QoS | Configured storage returns the latest value per matching key | Zenoh does support last-value storage; it is not absent |
| Replace stale state waiting for radio transmission | KEEP_LAST and flow control provide relevant DDS mechanisms; qualify actual queued/in-flight behavior | No equivalent per-key pending-transmission replacement established in reviewed docs/config | Zenoh storage is not proof of this bandwidth behavior |
| Select by identity versus payload | DDS keys/CFTs; sender-side selection inside a production struct union branch verified in the qualification rig | Native key-expression selection; arbitrary payload predicates need application/custom logic | Key selection is not equivalent to a DDS content filter; final DDS predicates still require tests |
| Schema-drift protection | Discovery-time reader/writer type compatibility checks, governed by configured type-consistency policy | Native routing does not provide the equivalent payload-schema compatibility check | DDS supplies a built-in guard; Zenoh applications/bridges must supply their own contract |
| Per-instance freshness | Deadline monitoring separately for each known active instance | Liveliness tokens indicate availability; per-key data freshness needs application policy | One instance's updates need not hide another instance's missed deadline |
| Per-instance lifecycle and ownership | Distinct instance states and optional exclusive-owner arbitration | Put/delete and liveliness primitives; equivalent lifecycle/arbitration needs application/custom logic | DDS supplies integrated object-state rules, not just key/value operations |

## Boat-Network Comparison: Instances, Last Values, and Radio Backlog

For this use case, distinguish three requirements:

1. Identify navigation, health, and each sensor as separate instances.
2. Retain the latest known value of each instance for a later lookup.
3. Avoid spending scarce radio bandwidth sending obsolete updates that are
   still waiting to be transmitted.

Zenoh can do the first two. The third is the specific tradeoff that must not
be hidden by saying "Zenoh has a latest-value cache."

### Composite identity and selection

A Zenoh key can represent a composite instance:

```text
status/boat30/router1/session42/navigation/primary
status/boat30/router1/session42/health/router
```

Subscriptions select identity components with key expressions:

| Selection | Example |
|---|---|
| All status from boat30 | `status/boat30/**` |
| Navigation from all boats, routers, and sessions | `status/*/*/*/navigation/**` |
| All instances in one boat/router/session | `status/boat30/router1/session42/**` |

The application defines and maintains that naming convention. These are not
DDS-declared composite key fields, but they can represent the same logical
identity. Native routing selects those key components, not arbitrary fields
inside an opaque payload. Query parameters are passed to a queryable; they
are not automatically router-enforced payload predicates for subscriptions.

The proposed ACT [two-topic WAN contract](two-topic-wan-migration-plan.md)
keeps variant kind as the union discriminator and uses stable,
variant-qualified outer instance IDs. That is a draft migration, not the
current implemented contract. Session-specific keys are an optional extension.

The [Connext 7.7 qualification results](two-topic-wan-migration-plan.md#33-required-check-results-2026-11-08)
now verify actual sender-side discriminator/audience filtering and the
struct-branch predicate `payload.navigation.speed_knots > %1`, guarded by the
discriminator. The tested Python/XML-loaded rig uses no `_u` in that path.
This corrects the earlier unresolved branch-filter status; it is not a claim
that every final predicate or the production router migration is complete.

### DDS benefit: per-instance lifecycle, history, and freshness

DDS manages each keyed instance as an object with its own state and history.
Zenoh can represent the same objects with composite keys, but its native
put/delete and liveliness-token primitives do not automatically provide the
same integrated rules.

| Requirement | DDS/Connext benefit | Zenoh comparison |
|---|---|---|
| Distinguish removal from loss of publishers | Separate ALIVE, NOT_ALIVE_DISPOSED, and NOT_ALIVE_NO_WRITERS states | A key delete or token disappearance needs application-defined meaning and association with stored data |
| Keep independent history | KEEP_LAST(N) keeps N samples for each instance, subject to resource limits; navigation need not overwrite health or another sensor | Latest-value storage per key is supported, but is not equivalent to DDS endpoint history and pending-transmission behavior |
| Detect a stale individual source | Deadline is checked separately for each known active instance, even when other instances update | Token availability is not proof of fresh data for every key; equivalent per-key freshness needs application logic or a qualified extension |
| Arbitrate primary/backup sources | With EXCLUSIVE ownership, eligible writers compete for ownership per instance using writer strength | Equivalent source arbitration is application/custom behavior, not a consequence of key matching |
| Manage removal and cached state | Explicit dispose/unregister operations and configurable lifecycle cleanup distinguish state transitions from cache removal | Applications must connect deletes, token changes, and storage/session cleanup according to their chosen contract |

**RouterHealth example:** give each boat/router's health a distinct key. If
Boat30's known health instance misses the configured deadline, DDS can report
that instance even while Boat31's health and unrelated Status instances continue
arriving. Map the reported instance handle back to the boat/router/session.
This protects against unrelated traffic making a stale health source look fresh.

A reader deadline means **no update was received in time**, not proof that the
router stopped sending: network loss or delay can cause the same result.
Monitoring a peer that has never produced a known instance requires a separate
startup/expected-peer check. Writer liveliness is also distinct from per-instance
data freshness; it must not be used as a substitute for health-update monitoring.

**Scope limitation:** these DDS policies are normally configured on an endpoint
and then applied separately to its instances. One reader's deadline period
applies to all its instances; it cannot freely assign one period to health and
another to navigation. Different deadline/history/reliability requirements may
still need separate endpoints on the same Status topic. Two topic names do not
promise two writers and two readers.

Disposal marks an instance's state; it does not promise immediate memory or
history removal. Unregister removes a writer's responsibility; with automatic
disposal disabled, NO_WRITERS reflects loss of all known writers for that
instance, not just one redundant source. Configure and test lifecycle policies,
reappearance, restart, and cleanup explicitly.

These are native DDS benefits, **not a claim that ACT already forwards every
lifecycle transition**. The current generic runtime skips invalid-data samples,
and the [WAN migration plan](two-topic-wan-migration-plan.md) requires explicit
lifecycle handling at the adapter boundary. Zenoh can reproduce application
behavior, but doing so adds lifecycle, freshness, and arbitration machinery to
build, govern, and test.

Sources: [Connext 7.7 instance QoS](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/QoSConfigurationInstances.htm),
[instance states](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/InstanceState.htm),
and [Zenoh 1.10.2 liveliness](https://docs.rs/zenoh/1.10.2/zenoh/liveliness/index.html).

### Schema drift: DDS checks compatibility before matching

**This is a DDS advantage for the boat network.** Connext checks advertised
reader and writer types during discovery under the configured type-consistency
policy. Incompatible schema changes can prevent matching rather than leave
applications to discover the mismatch after a message arrives. Compatible
schema evolution can still be allowed: this is not necessarily an
identical-schema requirement. See the
[Connext 7.7 type-compatibility overview](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/extensible_types_guide/extensible_types/XTypes_Intro.htm).

Zenoh can carry typed unions or schema-based messages; it does not require
unstructured binary packages. One subscription can also receive several types,
with the application selecting a decoder by key or agreed metadata. However,
successful native Zenoh key matching/routing does not establish that the sender
and receiver payload schemas are compatible. A decoder's validation after
receipt is not the same as a discovery-time compatibility check.

**Zenoh con:** reproducing this guard requires an application/bridge schema
contract, supported-version or schema-identity checks, an explicit compatibility
policy, and clear rejection/reporting of unsupported combinations. Merely
including a version or hash in a payload is not enough unless it is checked.
This adds engineering and operational responsibility, not an inability to
use typed data.

For either design, test a deliberately incompatible field/union change and a
permitted schema evolution. Verify the actual policy and available type
information, rather than assuming every schema change is rejected. Schema
compatibility also does not validate the operational meaning of a speed,
command, or mission.

### Last value per composite instance: supported by Zenoh

With a configured Zenoh storage, each concrete composite key can have its own
latest stored value. Queries return the latest values for matching keys.
Navigation and health therefore do not have to overwrite each other.
Ordinary pub/sub alone is not a promise of retained state for later queries.

**Do not list "no last value per instance" as a Zenoh disadvantage.**
The difference is how retention is configured and retrieved, not whether it
is possible. See the upstream [storage description](https://zenoh.io/docs/manual/abstractions/#storage).

### Zenoh con: stored latest value is not queued-sample replacement

Suppose navigation updates 10, 11, and 12 arrive while the radio is busy.
For latest-state traffic, the desired behavior is to replace obsolete pending
updates and send 12 when a transmission slot becomes available, without
replacing an unrelated health or sensor instance. Commands need separate
retention rules; this policy must not discard required events.

Keeping only update 12 in a Zenoh storage does **not** establish that updates
10 and 11 have been removed from outgoing transport queues. In the reviewed
[Zenoh 1.10.2 configuration](https://github.com/eclipse-zenoh/zenoh/blob/1.10.2/DEFAULT_CONFIG.json5),
transmission queues contain batches by priority. Congestion controls describe
waiting/blocking or dropping a publication when no batch is available;
adaptive batching groups messages. None of those documented settings establishes
"replace the older pending publication for this same composite key."

**Cost for the boat deployment:** this behavior needs a pinned-release
qualification and, if absent, an application/gateway scheduler that holds the
latest unsent state per key before handing it to the transport. Once handed
off, storage replacement alone is not evidence that a queued message can be
withdrawn. Do not assert that Zenoh can never provide this behavior; no equivalent
built-in mechanism was established in the reviewed material.

Connext's per-instance KEEP_LAST history and flow controllers are relevant
mechanisms for the desired behavior, not a blanket promise that every lower-level
buffer or already transmitted packet can be replaced. Qualify history,
reliability/repair, resource limits, and scheduling together. The ACT router's
known lifecycle-forwarding gap also remains separate from DDS's native features.

### Qualification before a middleware choice

Compare equal workloads under a link slower than the offered status load:

- Track at least navigation, health, and two independent sensor instances.
- Record application update IDs, actual transmitted updates, received state
  age, total bytes, and command outcomes.
- Verify that obsolete pending updates are replaced per instance, not that
  publications are merely dropped across a priority queue.
- Verify storage lookup separately from transmission behavior.
- Include disconnect/reconnect and session cleanup; do not infer DDS
  dispose/unregister semantics from a Zenoh delete operation.

No Connext-versus-Zenoh run in this repository has established the comparative
bandwidth or freshness result. Latest-value storage alone is not enough to
choose the radio data plane.

## ACT Router Strengths

The ACT router is not simply a smaller Zenoh. It has several capabilities and qualities that are valuable for a controlled DDS mission network.

### DDS-native semantics

ACT routes actual DDS participants and endpoints. It understands DDS discovery, type objects, partitions, ContentFilteredTopics, QoS compatibility, liveliness, and matching. This avoids introducing a protocol bridge when the surrounding system is already Connext DDS.

Zenoh can bridge DDS through an external plugin, but that adds another protocol boundary and makes the resulting semantics dependent on the bridge configuration and supported mappings.

### Explicit, reviewable route policy

ACT's YAML configuration describes the participant and route topology explicitly. Routes can be restricted by role, participant, topic, partition, and SQL content filter. This makes the intended control-to-platform and platform-to-platform data paths easy to audit.

Zenoh's key-expression declarations and dynamic routing are more flexible, but the resulting data path is less naturally represented as a fixed, domain-specific route table.

### Mission-specific runtime control

ACT has tested controls for enabling and disabling routes, changing route partitions, changing participant partition membership, and targeting commands to a specific router. It also publishes command acknowledgements, state revisions, resolved QoS summaries, and controller journal records.

These features are directly aligned with the repository's dashboard and simulation workflows. Zenoh's admin space is more general, but it does not by itself provide this ACT-specific command/state model.

### Mesh and delivery observability

ACT provides explicit router presence and mesh status, including `ALIVE`, `STALE`, and `DEAD` peer states, configuration-hash drift detection, forwarded-sample counters, WAN protocol counters, and application acknowledgement RTT.

This gives the current system an operational story tailored to diagnosing route delivery between platforms. A Zenoh deployment would need to map its admin/runtime data and plugins into equivalent mission-level health views.

### Dynamic DDS type learning

ACT can build DynamicData forwarding routes from inline COMPLETE SEDP TypeObjects without requiring a generated C++ type for every application payload. That is useful when the set of routed DDS application types changes within a known DDS environment.

This does not equal Zenoh's generic payload/key-expression model, but it is a meaningful DDS-specific capability.

## Feature Gaps in the ACT Router

The following are capabilities available in Zenoh or its standard plugin ecosystem that the ACT router does not currently provide.

### 1. Generic Zenoh protocol interoperability

**Gap:** ACT speaks Connext DDS/RTPS. It does not implement the Zenoh protocol or provide a Zenoh session/client API.

Zenoh supports native Zenoh publishers, subscribers, queryables, peers, clients, and routers. ACT applications must be DDS participants and use the configured DDS domains and types.

**Impact:** ACT cannot directly serve Zenoh-native applications, and integrating with Zenoh deployments requires an external bridge or a new protocol adapter.

**Priority:** High if the system must connect DDS and Zenoh ecosystems; irrelevant if the environment is intentionally DDS-only.

### 2. Router, peer, and client deployment modes

**Gap:** ACT has `control` and `platform` roles. It does not provide Zenoh-style generic `router`, `peer`, and `client` session modes.

**Impact:** ACT is less reusable for edge clients, application peers, and infrastructure topologies outside the current control/platform model. It also cannot use one binary/configuration model to cover the broader Zenoh deployment patterns.

**Priority:** Medium to high for reuse; low for the current fixed mission topology.

### 3. Key-expression routing

**Gap:** ACT routes configured DDS topics and types. It does not provide hierarchical Zenoh key expressions, wildcard declarations, or dynamic matching across arbitrary application resources.

**Impact:** Every new ACT data family requires DDS type/discovery compatibility and route configuration. Zenoh applications can use a uniform naming space and dynamically declare matching resources.

**Priority:** High for general-purpose data infrastructure; not a defect for a deliberately typed DDS control network.

### 4. Queryables and distributed request/reply

**Gap:** ACT currently forwards live samples. It does not expose Zenoh-style `get`, queryable, reply, or distributed query semantics.

**Impact:** Applications cannot ask the router or remote resources for current state through a generic query path. They must implement request/reply as application-defined DDS topics or add another service.

**Priority:** High if state inspection, lookup, or request/reply is required.

### 5. Storage and data-at-rest integration

**Gap:** ACT has no storage-manager plugin, storage backend API, or built-in route to persistent/in-memory key-expression storage.

**Impact:** Historical data, durable state, and queryable storage must be implemented outside the router. ACT's transient-local QoS is not equivalent to a general storage/query service.

**Priority:** High for data-at-rest or replay use cases; low for pure live forwarding.

### 6. Transport diversity and multi-link connectivity

**Gap:** The current ACT router design is UDPv4-only for its router participants. It does not provide Zenoh's standard transport family and multilink model.

Missing or not exposed as equivalent ACT router capabilities include:

- TCP.
- TLS transport.
- QUIC.
- WebSocket transport.
- Unix socket streams.
- Serial transport.
- Shared-memory transport.
- A generic multi-link connection model.

**Impact:** ACT is less suitable for mixed networks, NAT traversal patterns, browser/HTTP-adjacent connectivity, serial links, encrypted links, and local high-throughput shared-memory paths.

**Priority:** High for heterogeneous or hostile networks; low when every participant is co-located or connected through controlled UDPv4 paths.

### 7. Multicast scouting and generic session discovery

**Gap:** ACT relies on DDS participant discovery and configured domains/participants. It does not provide Zenoh multicast scouting for discovering routers and peers independently of DDS application discovery.

**Impact:** ACT requires DDS-specific discovery behavior and configuration. It is not a general session fabric for non-DDS clients.

**Priority:** Medium, depending on whether non-DDS session discovery is needed.

### 8. Runtime plugin system

**Gap:** ACT has internal C++ interfaces and templated factories, but no dynamic plugin loader, plugin search path, required-plugin semantics, or runtime plugin lifecycle API.

**Impact:** Adding REST, storage, protocol bridges, or specialized transports requires modifying and rebuilding the router rather than deploying a compatible plugin.

**Priority:** High for product extensibility; low for a tightly controlled embedded deployment.

### 9. REST, WebSocket, and web-server access

**Gap:** ACT exposes administration and data-plane observations through DDS topics and repository-specific dashboard services. It does not ship a general REST plugin, WebSocket remote API, or HTTP key-expression web server.

**Impact:** Web applications and language-neutral clients need a separate adapter or dashboard bridge. They cannot directly use ACT as an HTTP-accessible data service.

**Priority:** High for external integrations and browser clients; low if all consumers are DDS-native.

### 10. Storage backend/plugin ecosystem

**Gap:** ACT has no equivalent to Zenoh's storage manager and backend trait ecosystem.

**Impact:** Database, file, cache, and custom storage integrations are outside the router and cannot be added independently of the router build.

**Priority:** Medium to high for productization.

### 11. Authentication and encrypted transport options

**Gap:** The ACT router documentation and current runtime model do not expose equivalents to Zenoh's TLS, public-key authentication, username/password authentication, or a general session security configuration.

**Impact:** Security depends largely on the Connext deployment, network isolation, QoS/XML configuration, and surrounding infrastructure. The router does not provide a comparable portable authentication surface across supported transports.

**Priority:** High for deployment beyond a controlled lab or isolated network.

### 12. Generic admin-space introspection

**Gap:** ACT publishes structured status, presence, link metrics, and journal topics, but does not expose a generic hierarchical admin resource space.

**Impact:** Operators and tools must know ACT-specific DDS types and topics. A generic client cannot browse runtime resources using a common admin-space model.

**Priority:** Medium. ACT's typed status is more constrained but also more explicit and testable for its target workflow.

### 13. Built-in HLC timestamping

**Gap:** ACT does not add a Zenoh-style Hybrid Logical Clock timestamp to every routed sample that lacks one.

**Impact:** Cross-router ordering and event correlation depend on application fields, DDS source timestamps, or external telemetry. This complicates correlation when payloads do not carry a common logical time.

**Priority:** Medium for distributed tracing and event ordering.

### 14. Complete DDS sample lifecycle forwarding

**Gap:** This is an ACT-specific implementation gap rather than a Zenoh feature transplant. ACT's own test documentation identifies serialized-CDR forwarding and keyed dispose/unregister mirroring as not implemented.

**Impact:** Keyed DDS state and lifecycle transitions may not be preserved across the route in all cases.

**Priority:** High when keyed state, ownership, dispose, or unregister semantics matter.

### 15. General-purpose query/storage APIs instead of application-defined DDS topics

**Gap:** ACT's control/status design uses purpose-built DDS IDL types and topics. It does not offer a general API for applications to declare resources, query them, or attach storage without adding new IDL and router configuration.

**Impact:** The system is strongly typed and auditable, but feature expansion requires coordinated IDL, generated code, QoS, and test changes.

**Priority:** Medium to high for evolving products.

## Operational and Engineering Tradeoffs

### ACT router advantages

- Strong DDS interoperability without a bridge.
- Explicit route topology and role policy.
- Typed commands, acknowledgements, status, and journals.
- Mission-specific mesh health and delivery telemetry.
- Runtime route and partition control already demonstrated by end-to-end tests.
- C++/Connext implementation can directly use DDS QoS and discovery primitives.
- Native DDS per-instance history and deadline monitoring, with distinct
  disposed/no-writers states and optional exclusive ownership; gateway lifecycle
  forwarding still needs completion.
- Smaller conceptual surface when the entire system is DDS and the topology is known.

### ACT router disadvantages

- Locked to the Connext DDS/RTPS ecosystem and its operational assumptions.
- UDPv4-only transport design limits deployment choices.
- No generic query, storage, REST, WebSocket, or plugin surface.
- New functionality usually requires C++ changes, generated types, configuration, and a rebuild.
- Static route configuration is less adaptive than Zenoh's key-expression/session model.
- Current auto-QoS WAN readiness deadlock requires explicit production QoS aliases.
- Serialized-CDR fast path and keyed lifecycle forwarding remain incomplete.
- Security and authentication capabilities are not exposed as a comparable router-level feature set.
- Runtime operation depends on Connext 7.7.0, generated types, XML/QoS assets, and environment setup.

### Zenoh router advantages

- Protocol-independent-feeling data fabric through key expressions rather than fixed DDS topic/type definitions.
- Native pub/sub plus query/get and queryable request/reply.
- Router, peer, and client modes support a wider range of topologies.
- Broad transport selection and optional shared memory.
- Dynamic plugin system for REST, storage, bridges, and other integrations.
- Built-in admin space and runtime configuration surface.
- TLS and authentication feature support.
- Storage-manager and backend ecosystem for data-at-rest use cases.
- Composite-instance key paths and latest-value-per-key retrieval through configured storage.
- Native WebSocket/HTTP integrations through plugins.

### Zenoh router disadvantages

- DDS interoperability is not automatic; it depends on an external DDS or ROS 2 bridge plugin.
- Plugin compatibility is tightly coupled to Rust version, Zenoh version/commit, and feature set.
- Dynamic key-expression routing is less explicit than ACT's reviewed role/route configuration for a fixed mission network.
- Enabling REST, admin writes, storage, and bridge plugins increases the attack and operational surface.
- A Zenoh deployment must assemble and govern plugins to reproduce ACT's domain-specific command, mesh, and delivery-audit behavior.
- Zenoh's generic model does not preserve all DDS QoS, keyed lifecycle, ownership, and discovery semantics without deliberate bridge behavior.
- Latest-value storage is supported, but equivalent per-composite-key replacement
  of stale pending radio transmissions was not established. Qualify it or budget
  application/gateway scheduling; a latest stored value is not proof of queue replacement.
- Native key-expression routing selects identity encoded in the key, not
  arbitrary payload fields. DDS-style content selection needs deliberate
  application/custom behavior rather than being assumed from key matching.
- No native DDS-style reader/writer payload-schema compatibility guard during
  discovery. Typed serialization is possible, but schema-drift detection,
  compatibility policy, and rejection/reporting become application/bridge work.
- Per-instance stale-data detection, disposed-versus-unavailable meaning,
  publisher arbitration, and session/storage cleanup require a deliberate
  application/custom contract. Put/delete and liveliness tokens are useful
  building blocks, not automatic equivalents of DDS instance QoS and lifecycle.
- External plugins have independent release cadence, support boundaries, and potentially different licensing details.

## Prioritized Gap Backlog

| Priority | Gap | Reason |
|---|---|---|
| P0 | Complete keyed lifecycle and serialized forwarding | Correctness risk for DDS stateful data |
| P0 | Add transport security/authentication strategy | Required for deployment outside a trusted network |
| P1 | Add a supported plugin or adapter boundary | Enables REST, storage, bridges, and custom integrations without core rebuilds |
| P1 | Add query/request-reply primitives | Avoids modeling every lookup operation as custom DDS topics |
| P1 | Add transport options beyond UDPv4 | Required for heterogeneous, encrypted, or constrained links |
| P1 | Add persistent/in-memory storage integration | Required for replay, history, and data-at-rest workflows |
| P2 | Add generic admin/resource introspection | Reduces coupling between tools and ACT-specific IDL |
| P2 | Add HLC-style event timestamping | Improves cross-router correlation and ordering |
| P2 | Add a generic client/peer mode | Broadens reuse beyond control/platform deployments |
| P3 | Add REST/WebSocket access | Useful for browser and external application integration |
| P3 | Add scouting independent of DDS discovery | Useful for non-DDS clients and simpler deployment discovery |

The priorities assume the goal is to broaden the ACT router into a general data-infrastructure product. For a DDS-only mission network, P0 should focus on lifecycle correctness and security, while many P1-P3 items may remain intentionally out of scope.

## Decision Guidance

### Keep and extend the ACT router when

- The system of record is Connext DDS.
- DDS QoS, partitions, keyed state, and discovery semantics are requirements.
- The control/platform topology is known and should be explicitly auditable.
- Mission-specific commands, mesh health, and delivery evidence are more important than generic application integration.
- The deployment can remain on controlled UDPv4 infrastructure.

### Consider Zenoh or a hybrid when

- Non-DDS applications, browsers, databases, or edge clients must participate directly.
- Query, storage, replay, or data-at-rest features are first-class requirements.
- Multiple transports, encrypted links, WebSockets, or shared-memory paths are required.
- A dynamic plugin ecosystem is more valuable than a single tightly controlled binary.
- The system needs router/peer/client topologies rather than only control/platform roles.

### Hybrid architecture option

A pragmatic hybrid is to retain the ACT router for DDS-native control/platform traffic and introduce Zenoh at a defined integration boundary:

1. Keep ACT responsible for DDS QoS, partitions, mission commands, presence, and route audit.
2. Add a DDS-to-Zenoh bridge at selected topics rather than replacing every DDS route.
3. Use Zenoh for query, storage, REST/WebSocket, or non-DDS consumers.
4. Define which semantics are preserved, translated, or intentionally dropped at the bridge boundary.
5. Test bridge behavior for timestamps, reliability, durability, keyed lifecycle, filtering, and failure recovery.

The bridge boundary must be treated as a semantic contract. It should not be assumed that a DDS topic and a Zenoh key expression are interchangeable merely because both can carry serialized data.

## Bottom Line

The ACT router's largest missing features are Zenoh's general-purpose data-fabric capabilities: generic key-expression routing, query and storage services, transport diversity, dynamic plugins, web integrations, and portable security controls.

The ACT router's strongest differentiators are DDS-native fidelity and mission-specific operational control. Replacing it wholesale with `zenohd` would gain breadth but would require rebuilding or bridging the typed command, QoS, mesh-health, and delivery-audit behavior already implemented here. A hybrid approach is the lowest-risk path when both DDS fidelity and broader data access are required.
