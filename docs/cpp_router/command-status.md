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

Controller journal topic (LAN/debug recorder): `ActRouterControllerJournal` — one sample per
processed controller event containing the input event, controller decision/outcome,
pre/post `state_revision`, affected route/topic delta, and requested side effects. The
journal writer is created with the admin/status plumbing so instrumentation is always
available; the recorder/subscriber exists only in debug mode. With no matched debug reader,
the topic produces no event-log data traffic beyond normal DDS endpoint discovery.

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

Command reader / ack writer QoS (LAN, D48): `RELIABLE + VOLATILE + KEEP_LAST(16)` on both
`ActRouterCommand` and `ActRouterCommandAck`. No durability: duplicate `command_id` replay
is already handled at the app layer by the controller's bounded ack cache (D4), so a
resent command gets its cached ack republished regardless of DDS-history availability.
Depth 16 absorbs a startup burst of commands without needing `KEEP_ALL`. The command
reader is built on a **ContentFilteredTopic** (`target_node = %0 AND target_router = %1`,
this router's own identity as the CFT parameters) so a misdirected command never reaches
the reader callback at all — D47, reusing the D37/D43 quoting pattern (no ambiguity here:
the parameters are known `std::string` identity fields, not YAML scalars).

Controller journal writer QoS (LAN, debug analysis, D49): `RELIABLE + KEEP_LAST(256)` with
the reliable send window set to `LENGTH_UNLIMITED` (`BuiltinQosLib::Generic.KeepLastReliable`
pattern, validated 7.7) — `write()` never blocks the controller thread even with a slow
matched debug reader; under sustained backpressure the oldest unacknowledged samples are
overwritten rather than the call stalling. Backlog is observed via a `StatusCondition` on
`RELIABLE_WRITER_CACHE_CHANGED_STATUS` (high/low watermark on the unacknowledged count),
logged as `journal_falling_behind` — `PUBLICATION_MATCHED_STATUS` only reports whether a
debug reader is attached, not whether it is keeping up. It is an analysis stream, not state;
late joiners use `RouterStatus` for current state and a live recorder for event history.

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
    SET_PARTICIPANT_PARTITION
};

// Trimmed to exactly today's ControllerEventKind set (D46): no ROUTE_DATA_READY (no such
// controller event exists; per-sample journaling would be a firehose) or SHUTDOWN_REQUESTED
// (shutdown is procedural, not a queued event); TOPIC_QOS_WARNING added (D39/D45).
enum ControllerJournalEventKind {
    JOURNAL_COMMAND_RECEIVED,
    JOURNAL_PUBLICATION_DISCOVERED,
    JOURNAL_SUBSCRIPTION_DISCOVERED,
    JOURNAL_ENDPOINT_LOST,
    JOURNAL_TOPIC_ENTITIES_READY,
    JOURNAL_TOPIC_TEARDOWN_COMPLETE,
    JOURNAL_ROUTE_ENTITY_ERROR,
    JOURNAL_TOPIC_QOS_WARNING
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

struct ControllerJournalRecord {
    string target_node;
    string target_router;
    uint32 router_id;
    string status_id;
    uint64 event_sequence;
    int64 timestamp_unix_nanos;
    ControllerJournalEventKind event_kind;
    uint64 pre_state_revision;
    uint64 post_state_revision;
    boolean state_changed;
    string route_name;
    string topic_name;
    string command_id;
    string endpoint_guid;
    uint64 entity_generation;
    string decision;
    string reason;
    string action;
    string payload_json; // compact affected state delta + detailed event fields
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
the raw endpoint inventory lives in the structured log.

## Required POC Commands

| Command | Meaning | POC effect |
|---|---|---|
| `ENABLE_ROUTE` | Mark an existing route desired-enabled | if required endpoints are already discovered, create readers/writers, attach read conditions, and mark enabled; otherwise leave it waiting on discovery |
| `DISABLE_ROUTE` | Stop forwarding an existing route | detach conditions, close per-topic readers/writers, mark disabled |
| `UPDATE_ROUTE` | Replace or patch one active-side route definition | reconcile runtime to the supplied `RouterRouteSpec`; covers topic list, endpoint QoS aliases, forwarding mode, filters, and lifecycle flags |
| `SET_PARTICIPANT_PARTITION` | Change the participant-level partition on a named participant role, currently `team_wan` | update participant status and recreate affected readers/writers that inherit the participant partition; this is the generic form of team assignment |

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
