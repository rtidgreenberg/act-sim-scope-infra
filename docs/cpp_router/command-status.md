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

This is distinct from the one liveliness-bearing WAN topic, **`RouterHealth`**, which carries
router/link presence across the mesh — see [Presence & Health](presence-and-health.md). The
LAN-local `RouterStatus` additionally surfaces the **presence roster** (connected routers with
ALIVE/STALE/DEAD + last-seen delta) for local observability.

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
    DESCRIBE
};

enum RouterRouteOperationalState {
    ROUTE_DISABLED,
    ROUTE_WAITING_FOR_DISCOVERY,
    ROUTE_RESOLVING,
    ROUTE_ENABLED,
    ROUTE_DEGRADED,
    ROUTE_ERROR
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

struct RouterRouteStatus {
    string route_name;
    RouterRouteSpec desired;
    RouterRouteOperationalState state;
    string state_revision;
    string caused_by_command_id;
    uint64 samples_forwarded;
    uint64 lifecycle_events_forwarded;
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
    string state_revision;
    sequence<RouterParticipantStatus> participants;
    sequence<RouterRouteStatus> routes;
};
```

`RouterRouteSpec` is the concrete active-side route shape used by runtime status and route
execution. The role-aware YAML form with `source_side` / `destination_side` is accepted by
startup config and by `ADD_ROUTE`; each router instance selects the side matching its local
`node.role` and materializes DDS entities only after discovery finds the needed endpoints.

`RouterCommandAck` is the immediate result of accepting or rejecting a command.
`RouterStatus` is the primary current-state publication and is keyed by `target_node` and
`target_router`. Publish one full `RouterStatus` sample after accepted route changes so every
subscriber sees a coherent route table in one message. The `routes` sequence includes every
route defined for this router instance after local-side selection, including disabled routes,
routes waiting on discovery, and routes that could not start. `router_id` is a numeric
identifier for compact correlation with harness logs or generated node IDs. `participants`
reports the router's current participant names, domains, participant-level partitions, and
QoS aliases. `RouterRouteStatus` is the per-route entry inside the `routes` sequence.

## Required POC Commands

| Command | Meaning | POC effect |
|---|---|---|
| `ENABLE_ROUTE` | Mark an existing route desired-enabled | if required endpoints are already discovered, create readers/writers, attach read conditions, and mark enabled; otherwise leave it waiting on discovery |
| `DISABLE_ROUTE` | Stop forwarding an existing route | detach conditions, close per-topic readers/writers, mark disabled |
| `UPDATE_ROUTE` | Replace or patch one active-side route definition | reconcile runtime to the supplied `RouterRouteSpec`; covers topic list, endpoint QoS aliases, forwarding mode, filters, and lifecycle flags |
| `SET_PARTICIPANT_PARTITION` | Change the participant-level partition on a named participant role, currently `team_wan` | update participant status and recreate affected readers/writers that inherit the participant partition; this is the generic form of team assignment |
| `DESCRIBE` | Report current routes and state | publish ack plus current `RouterStatus` containing the route list |

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
  route was enabled but lost a matched endpoint or write path; attempting rediscovery/rebuild

ROUTE_ERROR
  route cannot activate without config, type, QoS, or entity changes
```

State transitions should carry `caused_by_command_id` when a command triggered the change,
and should carry a concise `last_error` when discovery, type resolution, QoS compatibility,
or entity creation fails.
