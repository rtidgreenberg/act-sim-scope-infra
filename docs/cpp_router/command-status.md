# Command And Status

Use DDS command/status topics instead of Routing Service remote admin for POC operations.
Each router instance has:

- a **command input topic** it subscribes to;
- a **status topic** it publishes on whenever an accepted command changes route,
  participant, filter, partition, or QoS state.

For the POC, all routers may share the same command and status topic names and filter by
`target_node` / `target_router`. If that gets noisy, the same schema can be used with
per-router topic names, for example `ActRouterCommand.Platform_30.team` and
`ActRouterStatus.Platform_30.team`.

Command topic: `ActRouterCommand`

Status topic: `ActRouterStatus`

Mesh-status topic (LAN): `ActRouterMeshStatus` — this router's aggregated view of every
connected router (the "connected router list"), built from the WAN `RouterHealth` topic. See
[Presence & Health](presence-and-health.md#aggregate-mesh-view-published-over-the-lan).

Link-stats topic (LAN): `ActRouterLinkStats` — one sample per connected peer per poll
interval carrying raw reliable-protocol link metrics (NACKs, repair traffic, send-window
backpressure, probe RTT). **Capture only** — telemetry, not health classification, and not
part of the command/ack/revision machinery (metric deltas never bump `state_revision`). See
[Link Metrics](link-health.md) (D14).

Transport (decided): the command/status topics ride the router's **local LAN participant**,
not a dedicated admin domain/participant. `DomainParticipant`s are the expensive resource;
topics/partitions are cheap — reusing the LAN participant keeps participant count minimal *and*
keeps the control plane **independent of WAN health**, so the router stays commandable and
observable during the degraded-link exercise. Control is therefore **local per node** (the ACT
harness / local orchestration issues commands to the node's router). Keep it partition-ready:
admin endpoints default to partition `*` (or a reserved `ADMIN` partition) so a future
**central/remote admin over the WAN** can be added — co-locating admin on the WAN participant
under a dedicated `ADMIN` partition — without a schema change. (This resolves the old
"admin domain vs router-private domain" question in favor of *neither*: reuse the LAN
participant.)

Status writer QoS (LAN, D26): `RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1)` — late-joining LAN
observers receive the current snapshot on match from durability, so there is no
"request current status" command and no periodic republish; publication is change-driven
only (startup + every `state_revision` bump, D17). Observer-side aliveness rides the status
writer's liveliness, not sample cadence; nothing durable crosses the WAN.

This is distinct from the one liveliness-bearing WAN topic, **`RouterHealth`**, which carries
router/link presence + a **compact status summary** across the mesh — see
[Presence & Health](presence-and-health.md). Each router aggregates the `RouterHealth` summaries
it hears from all connected routers and republishes that connected-router list over the LAN on
**`ActRouterMeshStatus`**. The split is deliberate: `RouterStatus` is this router's own full
detail (LAN, WAN-independent); `ActRouterMeshStatus` is the aggregated mesh view (LAN); only the
**compact summary** crosses the constrained WAN, never the full route table.

Type: DynamicData or a tiny generated IDL type. For the POC, generated IDL is simpler and
keeps command parsing independent of the ACT payload XML. (WAN type propagation being disabled
does not matter here — the admin type is generated and loaded on both ends.)

The control plane should use one shared route shape for desired config, command updates,
and reported status. The status topic should publish **one router-wide status sample** that
contains a sequence of current route states. That is the right fit for the constrained WAN
link because a route change produces one coherent message, observers receive the whole router
state together, and the status writer avoids sending multiple per-route samples across the
link.

## Admin IDL Sketch

```idl
enum RouterCommandKind {
    ENABLE_ROUTE,
    DISABLE_ROUTE,
    UPDATE_ROUTE,
    SET_PARTICIPANT_PARTITION,
    DESCRIBE   // reserved — dropped from the POC command set (D26); a received DESCRIBE
               // gets an unsupported-kind reject (not cached — D26)
};

enum RouterRouteOperationalState {
    ROUTE_DISABLED,
    ROUTE_WAITING_FOR_DISCOVERY,
    ROUTE_RESOLVING,
    ROUTE_ENABLED,
    ROUTE_DEGRADED,
    ROUTE_ERROR
};

enum RouterRouteDiscoveryState {
    DISCOVERY_NONE,      // required input writer not yet discovered
    DISCOVERY_PARTIAL,   // input writer seen; type, QoS, or auto-QoS output reader missing
    DISCOVERY_READY      // all prerequisites for entity creation available
};

enum RouterRouteTopicState {
    TOPIC_IDLE,          // no entities (not discovered, or route not active)
    TOPIC_CREATING,      // prerequisites ready; entity creation in flight
    TOPIC_FORWARDING,    // reader + writer + read condition live
    TOPIC_TEARING_DOWN,  // was forwarding; detach/close in progress
    TOPIC_ERROR          // entity creation/runtime failed; sticky until command re-arm
};

struct RouterRouteTopicSpec {
    string name;
    string reader_qos;
    string writer_qos;
};

struct RouterRouteEndpointSpec {
    string participant;
    string subscriber_partition;
    string publisher_partition;
    string reader_qos;
    string writer_qos;
    string filter_expression;
    sequence<string> filter_parameters;
};

struct RouterRouteSpec {
    string route_name;
    boolean desired_enabled;
    string forwarding_mode;
    RouterRouteEndpointSpec input;
    RouterRouteEndpointSpec output;
    sequence<RouterRouteTopicSpec> topics;
    boolean mirror_instance_state;
    sequence<string> key_fields;
};

struct RouterCommand {
    string target_node;
    string target_router;
    string command_id;
    RouterCommandKind kind;
    string route_name;
    RouterRouteSpec route;
    string payload_json;
};

struct RouterCommandAck {
    string target_node;
    string target_router;
    string command_id;
    string route_name;
    boolean accepted;
    string message;
};

struct RouterRouteTopicStatus {
    string name;
    RouterRouteDiscoveryState discovery_state;   // per-topic rollup of discovery facts
    RouterRouteTopicState topic_state;
    uint64 samples_forwarded;
    uint64 lifecycle_events_forwarded;
    string last_error;
};

struct RouterRouteStatus {
    string route_name;
    RouterRouteSpec desired;
    RouterRouteOperationalState state;           // derived over topic states (D11)
    RouterRouteDiscoveryState discovery_state;   // best (max) topic rollup (D11)
    sequence<RouterRouteTopicStatus> topic_status;
    uint64 state_revision;
    string caused_by_command_id;
    uint64 samples_forwarded;                    // aggregate across topics
    uint64 lifecycle_events_forwarded;           // aggregate across topics
    string last_error;
};

struct RouterParticipantStatus {
    string name;
    int32 domain;
    string participant_partition;
    string qos_profile_alias;
};

struct RouterStatus {
    @key string target_node;
    @key string target_router;
    uint32 router_id;
    string status_id;
    string caused_by_command_id;
    uint64 state_revision;
    sequence<RouterParticipantStatus> participants;
    sequence<RouterRouteStatus> routes;
};
```

`RouterRouteSpec` is the concrete active-side route shape used by runtime status and route
execution. The role-aware YAML form with `source_side` / `destination_side` is accepted by
startup config and by `ADD_ROUTE`; each router instance selects the side matching its local
`node.role` and materializes DDS entities only after discovery finds the needed endpoints.

`RouterRouteStatus.state` is the route lifecycle, **derived over the per-topic states**: a
route is active as soon as at least one topic is forwarding, and each topic's entities are
created/torn down independently as its own discovery facts change (see
[design-decisions.md](design-decisions.md) D11). `topic_status` carries the per-topic
detail — discovery rollup, `topic_state`, counters, `last_error` — so a forwarding topic
never hides a cold or errored sibling; one topic's failure is contained to `TOPIC_ERROR`
and does not stop the others. Discovery facts roll up per topic with no memory of their
own — "was forwarding" memory lives only in `ROUTE_DEGRADED` (D1); the route-level
`discovery_state` is the best (max) topic rollup. `state_revision` is a monotonic
`uint64`: one global counter, with the per-route field stamped at that route's last
externally visible change, including per-topic state changes (D5, D11).
`caused_by_command_id` is empty unless the change was directly caused by an accepted
command — discovery- and runtime-driven transitions carry no id (D8).

`RouterCommandAck` is the immediate result of accepting or rejecting a command. A
state-changing command naming an unknown `route_name` is rejected (`accepted=false`,
"unknown route") and the reject is cached like any other ack — never an implicit
`ADD_ROUTE` (see [design-decisions.md](design-decisions.md) D24, D4).
`RouterStatus` is the primary current-state publication and is keyed by `target_node` and
`target_router`. Publish one full `RouterStatus` sample after accepted route changes so every
subscriber sees a coherent route table in one message. The `routes` sequence includes every
route defined for this router instance after local-side selection, including disabled routes,
routes waiting on discovery, and routes that could not start. `router_id` is a numeric
identifier for compact correlation with harness logs or generated node IDs. `participants`
reports the router's current participant names, domains, participant-level partitions, and
QoS aliases. `RouterRouteStatus` is the per-route entry inside the `routes` sequence.

`RouterStatus` deliberately carries **no discovered-endpoint inventory**
([design-decisions.md](design-decisions.md) D17): generated sequences are bounded (default
cap 100) and endpoint churn would be truncation-prone revision noise on the
one-coherent-sample topic. Discovery visibility comes from the per-topic `discovery_state`;
the raw endpoint inventory lives in the structured log. A verbose `DESCRIBE` inventory
response is a possible future addition, out of POC scope (the POC `DESCRIBE` command itself
is dropped — D26).

## Required POC Commands

| Command | Meaning | POC effect |
|---|---|---|
| `ENABLE_ROUTE` | Mark an existing route desired-enabled | if required endpoints are already discovered, create readers/writers, attach read conditions, and mark enabled; otherwise leave it waiting on discovery |
| `DISABLE_ROUTE` | Stop forwarding an existing route | detach conditions, close per-topic readers/writers, mark disabled |
| `UPDATE_ROUTE` | Replace or patch one active-side route definition | reconcile runtime to the supplied `RouterRouteSpec`; covers topic list, endpoint QoS aliases, forwarding mode, filters, and lifecycle flags |
| `SET_PARTICIPANT_PARTITION` | Change the participant-level partition on a named participant role, currently `team_wan` | update participant status and recreate affected readers/writers that inherit the participant partition; this is the generic form of team assignment |
| `DESCRIBE` | ~~Report current routes and state~~ **dropped (D26)** | late-joiner catch-up comes from `TRANSIENT_LOCAL` status durability instead; a received `DESCRIBE` is rejected as unsupported (reject not cached — only state-changing kinds enter the history); enum value stays reserved for a possible future verbose inventory response (D17 note) |

Optional POC-plus commands:

- `ADD_ROUTE` from a YAML or JSON route payload. Role-aware payloads are allowed and are
  resolved against the receiver's local `node.role`; concrete active-side route payloads are
  accepted as-is.
- `REMOVE_ROUTE` for routes created at runtime.
- `RESET_COUNTERS` to clear per-route metrics after a test run.

## Route State Machine

Use explicit operational states so status can explain whether a route is off, waiting for
discovery, resolving type/QoS, actively forwarding, degraded, or failed:

```text
ROUTE_DISABLED
  desired_enabled=false; no route entities attached

ROUTE_WAITING_FOR_DISCOVERY
  desired_enabled=true; missing input writer, output reader, type, or compatible QoS

ROUTE_RESOLVING
  discovery match exists; resolving DynamicType, QoS, topics, filters, and entity creation

ROUTE_ENABLED
  reader/writer exist; ReadCondition attached to AsyncWaitSet; forwarding samples

ROUTE_DEGRADED
  route was enabled but lost a matched endpoint or write path; entities (partially) exist;
  teardown/rebuild in progress — distinct from WAITING_FOR_DISCOVERY, which is quiescent
  with no route entities

ROUTE_ERROR
  route cannot activate without config, type, QoS, or entity changes; STICKY — exits only
  via ENABLE_ROUTE / UPDATE_ROUTE (re-arm) or DISABLE_ROUTE, never by auto-retry
```

The authoritative (state × event → state) transition table, with `discovery_state` guards, is
in [design-decisions.md](design-decisions.md) D2 — Phase 1 tests implement that table.

State transitions should carry `caused_by_command_id` when a command triggered the change,
and should carry a concise `last_error` when discovery, type resolution, QoS compatibility,
or entity creation fails.
