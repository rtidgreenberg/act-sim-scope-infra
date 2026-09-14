# Zenoh vs ACT Router: Feature Gaps and Tradeoffs

This document compares the ACT C++ DDS router in this repository with the upstream Eclipse Zenoh router, `zenohd`. It identifies capabilities present in Zenoh that the ACT router does not currently provide, while also recording areas where the ACT router is stronger or more specialized.

The comparison is based on:

- The ACT implementation inventory in [router-feature-inventory.md](cpp_router/router-feature-inventory.md).
- The upstream Zenoh inventory in [zenoh-router-feature-inventory.md](zenoh-router-feature-inventory.md).
- ACT's [router README](../router/README.md) and [end-to-end test coverage](../router/test_e2e/README.md).
- Upstream [`zenohd`](https://github.com/eclipse-zenoh/zenoh/tree/main/zenohd), [`zenoh`](https://github.com/eclipse-zenoh/zenoh/tree/main/zenoh), and [`plugins`](https://github.com/eclipse-zenoh/zenoh/tree/main/plugins).

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
- Native WebSocket/HTTP integrations through plugins.

### Zenoh router disadvantages

- DDS interoperability is not automatic; it depends on an external DDS or ROS 2 bridge plugin.
- Plugin compatibility is tightly coupled to Rust version, Zenoh version/commit, and feature set.
- Dynamic key-expression routing is less explicit than ACT's reviewed role/route configuration for a fixed mission network.
- Enabling REST, admin writes, storage, and bridge plugins increases the attack and operational surface.
- A Zenoh deployment must assemble and govern plugins to reproduce ACT's domain-specific command, mesh, and delivery-audit behavior.
- Zenoh's generic model does not preserve all DDS QoS, keyed lifecycle, ownership, and discovery semantics without deliberate bridge behavior.
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
