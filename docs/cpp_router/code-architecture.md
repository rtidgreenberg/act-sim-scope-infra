# Code Architecture

## Copy And Latency Model

Connext AI guidance for a C++ router: use **Modern C++ DynamicData serialized-buffer
forwarding** as the primary low-latency pass-through path. RTI documents DynamicData
"skip serialization/deserialization" mode for recording and bridging applications: the
reader can expose the received serialized CDR buffer and the writer can publish a supplied
serialized CDR buffer.

This is the right default assumption for this architecture:

- **Serialized-CDR forwarding** can avoid payload deserialization into generated types or
  normal DynamicData fields, and avoid re-serializing a materialized object on output.
- It is **not true zero-copy end-to-end**. The router still terminates one DDS writer/reader
  relationship and republishes through its own output writer.
- Reader loans are not transferable writer loans. A loaned sample from the input reader
  cannot simply be handed to the output writer.
- FlatData and Zero Copy Transfer over Shared Memory are useful type/transport
  optimizations for colocated large-data paths, but they are not a generic router handoff
  mechanism across participants or domains.

Route forwarding modes:

| Mode | Use when | Tradeoff |
|---|---|---|
| `serialized_cdr` | Same logical type in/out; no payload inspection or transform | Lowest CPU path; cannot inspect fields without deserializing |
| `dynamic_data` | Need runtime type support plus field access, filtering, key extraction, or transform | More flexible; likely deserializes/re-serializes payload |
| `generated_type` | Hot fixed-schema route or lifecycle route needs typed key/state handling | Fast and type-safe; not generic across arbitrary ACT XML types |

**`dynamic_data` is the default forwarding mode** (revised — see
[Thesis & Tenets](thesis-and-tenets.md) Tenet 7). It is correct for every route and is what
the `spikes/isc_recovery/` relay proved. `serialized_cdr` is an **opt-in, late optimization**,
allowed only on eligible routes: same logical type in/out, **no reader-side content filter**,
and **no meta-sample lifecycle mirroring** (both need field access). `generated_type` is for
hot fixed-schema routes. Connext 7.7 is the pinned target for the CDR-buffer forwarding and
TypeObject v2 / TypeLookup assumptions in this document.

## AsyncWaitSet Implementation Architecture

Each router instance is a small runtime around a route registry, discovery dispatcher, and one
`AsyncWaitSet`:

```text
RouterInstance
  ConfigLoader
  ParticipantRegistry
  RouteRegistry
  DiscoveryDispatcher
  EntityFactory
  AsyncWaitSetDispatcher
  CommandHandler
  StatusPublisher
  PresenceMonitor        # RouterHealth pub/sub + roster; presence-driven reset (Tenet 4/5)
  LinkStatsCollector     # WAN link metric capture: protocol-status polling + RTT probe (D14)
  Log                    # one structured stream; Connext logger bridged in (below)
```

- `ConfigLoader` reads YAML and selects the local side of role-aware routes. It does not
  create route readers or writers.
- `ParticipantRegistry` creates the DomainParticipants needed by the selected route sides
  and admin topics. Participants are created **disabled**, builtin discovery readers and
  conditions installed, then enabled — builtin readers are lazily created in 7.7, so this is
  the no-loss discovery startup order (D12). Every router participant sets
  `user_data = act.router=<node>/<router>` so router-originated endpoints are identifiable
  in discovery (D15). WAN participants are **never multi-homed** — with multiple physical
  networks it creates one WAN participant per network, interface-pinned via
  `allow_interfaces_list` (D18; today's single network is the `N = 1` case).
- `DiscoveryDispatcher` watches discovered publications and subscriptions on those
  participants. It is backed by the builtin participant/publication/subscription readers
  with `ReadCondition`s on the `AsyncWaitSet` (no listeners) and is a **translator, not a
  cache** (D30): it `take()`s builtin samples, applies the ignore/tag rules, and posts
  events whose payload is a **copy of the builtin topic data itself**
  (`PublicationBuiltinTopicData` / `SubscriptionBuiltinTopicData` — validated copyable
  value types) plus an `origin_router`/`ignored` sidecar; there is no hand-rolled
  endpoint-record struct and no dispatcher-side endpoint store (D27/D30 — the builtin readers'
  own KEEP_LAST(1)-per-instance caches are the only current-state store; switch to
  `read()` if an ad-hoc query need ever appears). The D19 captured subset survives as the
  rule for what matching/auto-QoS may read from a record — history/resource_limits are
  never discoverable. The discovered `DynamicType` is optional until TypeLookup resolves
  it — natively, via the builtin data's `type()` field; a later builtin update for the
  same GUID is simply another upsert event — LAN participants set `request_types_filter`
  so types are requested without a local endpoint match (D12/D13). The only dispatcher-side
  state is the small **participant table** (GUID → `act.router` tag/name, from the
  `DCPSParticipant` reader), serving the same-node ignore decision (D15) and the
  presence/link-stats GUID→router join (D14); participant loss is dispatcher-internal — the
  controller reacts only to per-endpoint losses (D30). A discovered publication whose
  participant carries a **same-node** `act.router` tag is recorded/logged with its
  `origin_router`, then locally ignored (`dds::pub::ignore`) so no route reader can ever
  match it; remote routers' WAN writers stay eligible — they are the expected route inputs
  (D15). Endpoint removal is **DDS-native and uniform** (D28): a remote participant's
  removal — graceful or lease-expiry purge — disposes its endpoints' builtin instances
  too, so `EndpointLost` is posted per observed builtin instance-state transition, one
  code path for graceful exit, SIGKILL purge, and single-endpoint deletion (the D16
  app-level fan-out is the fallback if the Phase 2 smoke disproves per-endpoint delivery);
  lease tuning: short LAN lease, WAN lease ordered after the `RouterHealth` presence
  window (D16). Ownership boundary (D22): endpoint→route-topic matching and the per-topic
  matched-endpoint sets live in the controller (`TopicRouteState`) — the dispatcher never sees
  route specs.
- `RouteRegistry` stores every configured route for the instance, including disabled routes
  and routes waiting on discovery. Desired-enabled plus discovery readiness determines
  whether DDS entities exist.
- `EntityFactory` creates topics, readers, writers, content-filtered topics, read
  conditions, and serialized-CDR forwarding helpers after discovery supplies type and QoS
  input. Every route output DataWriter is locally ignored at creation
  (`dds::pub::ignore(participant, writer.instance_handle())`, before the first write), so
  the router's own forwarded output can never re-enter a route input reader on the same
  participant — this kills the same-participant echo loop (e.g. `PlatformData` in and out
  of the team router's LAN participant) by construction (D15).
- `AsyncWaitSetDispatcher` owns the `AsyncWaitSet`; it attaches route read conditions as
  routes become active and detaches them when routes are disabled, rediscovered, or rebuilt.
- `CommandHandler` reads `RouterCommand` through a **ContentFilteredTopic**
  (`target_node = %0 AND target_router = %1`, this router's own identity as the CFT
  parameters, D47) — a misdirected command never reaches the callback. `command_id`
  idempotency stays app-level in the controller (D4/D8; DDS cannot do that part). QoS:
  `RELIABLE + VOLATILE + KEEP_LAST(16)` (D48). Accepted mutations are posted to
  `RouterController` as `CommandReceived`.
- `StatusPublisher` emits one aggregate `RouterStatus` sample after startup, accepted
  commands, discovery-driven activation/deactivation, and errors; it includes the presence
  roster. Writer QoS (LAN): `RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1)` — late joiners get
  the current snapshot from durability; publication is change-driven only, never periodic,
  and observer-side aliveness rides the writer's liveliness (D26).
- `ControllerJournalPublisher` emits one LAN-local analysis sample per processed controller
  event with the input event, controller decision/outcome, pre/post revision, affected
  route/topic delta, and requested side effects. Its writer is always created with the
  admin/status plumbing, but the recorder reader exists only in debug mode; with no matched
  reader, the journal produces no event-log data traffic beyond normal DDS endpoint
  discovery. QoS (D49): `RELIABLE + KEEP_LAST(256)` with the reliable send window
  `LENGTH_UNLIMITED` — `write()` never blocks the controller thread even under a slow
  matched debug reader (old unacknowledged samples are overwritten instead). Backlog is
  watched via a `StatusCondition` on `RELIABLE_WRITER_CACHE_CHANGED_STATUS`
  (`PUBLICATION_MATCHED_STATUS` only reports attach/detach, not keeping-up), logged as
  `journal_falling_behind`; this is observability-only and never feeds back into route
  control.
- `PresenceMonitor` publishes this router's compact `RouterHealth` summary over the WAN and
  subscribes to peers', maintaining the `router_id → {state, last-seen, participant GUID, summary}`
  roster. It republishes the aggregated connected-router list over the LAN on `ActRouterMeshStatus`.
  On a peer declared `DEAD` it posts a presence-reset event to `RouterController` (unregister that
  peer's forwarded instances). Only the compact summary crosses the WAN — never liveliness, never
  the full route table. See [Presence & Health](presence-and-health.md).
- `LinkStatsCollector` captures per-peer WAN link metrics (D14,
  [link-health.md](link-health.md)): on a config-fixed tick (~1 s) it is the **sole reader**
  of the WAN endpoints' matched-endpoint protocol statuses (reads reset `_change`
  fields/changed-flags; it polls cumulative totals and computes its own interval deltas),
  rolls them up per peer `router_id` (GUID join via the presence roster / D15 tag), and
  publishes per-peer `ActRouterLinkStats` on the **LAN** plus one structured log line per
  interval. It also owns the `RouterLinkProbe` writer/reader (app-ack RTT, probe-only QoS).
  Capture only — no health classification (presence remains the health authority); metric
  deltas never bump `state_revision`.
- `Log` is the single structured log stream. The Connext logger is bridged into it at startup
  via `rti::config::Logger::instance().output_handler(...)` so middleware messages arrive tagged
  `source=connext` alongside `source=router`. The handler is `noexcept`, never calls back into
  Connext, and is fast/thread-safe (may fire from multiple middleware threads). Connext verbosity
  is config-driven (`WARNING` baseline, per-category overrides).

The key implementation rule is **discovery before DDS entity construction**. A desired route
does not create its route DataReader until the input writer is discovered. It does not create
an `auto` LAN output DataWriter until a compatible local reader is discovered or an explicit
writer QoS is configured. This keeps LAN-side QoS matching driven by the applications that
actually appear in the domain.

## Controller And State Ownership

Use a central `RouterController` as the only class allowed to mutate router state. DDS
callbacks, command handlers, discovery listeners, and active route read conditions should
not directly edit route status. They should emit typed events to the controller.

```text
RouterController
  owns MutableRouterState
  owns RouteRegistry
  owns ParticipantRegistry
  owns EntityFactory
  owns AsyncWaitSetDispatcher
  owns StatusPublisher
  drains ControllerEvent queue

RouteRuntime
  holds shared_ptr<const RouteView>
  owns active DDS route entities for one route/topic
  forwards data from AsyncWaitSet callbacks
  reports RouteEvent observations to RouterController

StatusPublisher
  reads shared_ptr<const RouterStatus>   # the generated type IS the snapshot (D25)
  writes aggregate RouterStatus samples
```

The state model should be copy-on-write at the router level:

```text
MutableRouterState
  node_name
  router_name
  router_id
  state_revision
  entity_generation_counter  # one global counter; RouteView mints and topic entity
                             # builds take stamps from it (D23)
  participants: map<string, ParticipantState>
  routes: map<string, RouteState>
  command_history: bounded map<command_id, RouterCommandAck>

RouterStatus (generated)
  the status snapshot: built by the controller at each state_revision bump from
  MutableRouterState, immutable once built — no separate snapshot/view classes (D25);
  internal-only facts (matched sets, generation stamps, command history) stay off it

RouteView
  immutable desired route spec plus resolved local active side
  immutable topic list and endpoint policy references
  entity_generation          # stamp from the global counter at mint (D23); no
                             # state_revision — see design-decisions.md D6

RouteState
  desired: RouterRouteSpec
  operational_state          # lifecycle; DERIVED over topic states (D11); "was forwarding"
                             # memory (DEGRADED) lives here
  topics: map<topic_name, TopicRouteState>
  route_view_generation      # stamp of the current RouteView (D23)
  aggregate counters         # sums of per-topic counters
  last_error                 # route-wide errors only; per-topic errors live on the topic

TopicRouteState              # one per configured topic on the route — D11
  matched_endpoint_sets      # matched input writers (and auto-QoS output readers) for this
                             # topic; maintained by the CONTROLLER from discovery events
                             # carrying builtin-data copies — the dispatcher stores no endpoint
                             # records (D22/D27/D30)
  discovery_facts            # derived from the sets (D20): input_writer_seen ⇔
                             # matched-writer set non-empty; ditto output_reader_seen;
                             # plus type_resolved, qos_resolved
  entity_generation          # stamp taken at this topic's last entity build (D23);
                             # stale-stamped operations/completions are discarded
  discovery_state            # pure rollup of this topic's facts, no memory — D1/D11
  topic_state                # IDLE / CREATING / FORWARDING / TEARING_DOWN / ERROR (sticky)
  resolved_type_name
  resolved_reader_qos_summary
  resolved_writer_qos_summary
  counters
  last_error
```

The rule of thumb is: **routes read state, the controller writes state**. A route runtime may
own fast-path DDS entities and route-local counters, but it does not decide global route
state. When it observes something meaningful, it posts an event such as
`SampleForwarded`, `WriteFailed`, `EndpointLost`, `LifecycleMirrored`, or
`RouteEntityError`.
The controller folds that event into `MutableRouterState`, increments `state_revision` when
the externally visible state changes, builds a new `RouterStatus` snapshot (D25), and asks
`StatusPublisher` to publish it.

For sample counters, use one of two POC-safe options:

- simple path: route runtimes post sampled counter deltas to the controller after each
  dispatch batch;
- faster path: route runtimes keep atomics for hot counters, and the controller samples
  them when building a status snapshot.

Do not let the status topic read arbitrary mutable route objects. Status is always derived
from the controller's snapshot, which prevents half-applied command/discovery transitions
from leaking into `RouterStatus`.

There is no status-view adapter layer (D25): the controller builds the generated
`RouterStatus` directly from `MutableRouterState` at each revision bump, and that struct is
the immutable snapshot — one shape from controller to tests to wire. Route runtimes may hold
`shared_ptr<const RouteView>` for forwarding decisions, but never read status; status is the
controller's outward report, not the route's working state.

## Controller Event Model

The controller should process these event categories on one serialized strand or queue:

| Event | Source | Controller action |
|---|---|---|
| `CommandReceived` | command reader | validate command id (events are post-admission — node/router targeting already happened via the CFT, D24/D47), update desired state, publish ack, reconcile route |
| `PublicationDiscovered` | discovery dispatcher | upsert per-topic matched sets (D22), warn on unexpected origin for the leg (D29), try to activate matching desired-enabled routes |
| `SubscriptionDiscovered` | discovery dispatcher | update output readiness for `auto` writer QoS routes |
| `EndpointLost` | discovery dispatcher or route runtime | mark route degraded/waiting, detach conditions, close or rebuild entities |
| `RouteDataReady` | `AsyncWaitSetDispatcher` | dispatch to route runtime, then fold counter/error deltas into state |
| `TopicEntitiesReady` | entity factory (fake in Phase 1) | if the generation stamp is current: topic → `TOPIC_FORWARDING`, derive route state (D11); stale stamp → discard (D21/D23) |
| `TopicTeardownComplete` | entity factory (fake in Phase 1) | if the stamp is current: topic → `TOPIC_IDLE`, drive `DEGRADED → RESOLVING\|WAITING` per D2; stale stamp → discard (D21/D23) |
| `RouteEntityError` | entity factory or route runtime | topic-scoped (`topic_name` set): that topic → `TOPIC_ERROR`, siblings unaffected (D11/D21); route-wide (`topic_name` empty): route → `ROUTE_ERROR`; store `last_error`, publish status |
| `ShutdownRequested` | signal/main thread | quiesce intake, detach waitset conditions, close entities and participants |

This keeps DDS callback threads shallow. They translate DDS notifications into controller
events and return quickly. The controller can then perform entity creation/destruction in a
known order and publish a coherent status snapshot.

For debug analysis, the controller also writes a controller journal record after each
processed event. The record is observability only: it captures the input event, the
decisions/actions taken, and the pre/post externally visible state, but it never feeds back
into route control. The journal writer exists in normal builds; the recorder reader is
debug-mode only, so event-log data traffic appears only when recording is requested.

## Proposed C++ Module Layout

The POC can stay in one executable, but the code should be split by ownership boundary:

```text
router_main.cxx
  parse args, load config path, construct RouterController, run until signal

config/
  RouterConfig.hpp/.cxx
  RouteConfigParser.hpp/.cxx
  QosAliasTable.hpp/.cxx

core/
  RouterController.hpp/.cxx
  RouterState.hpp/.cxx
  RouterEvents.hpp
  RouteRegistry.hpp/.cxx
  ParticipantRegistry.hpp/.cxx

dds/
  DiscoveryDispatcher.hpp/.cxx
  TypeResolver.hpp/.cxx
  QosResolver.hpp/.cxx
  EntityFactory.hpp/.cxx
  AsyncWaitSetDispatcher.hpp/.cxx
  DynamicForwarder.hpp/.cxx

routes/
  RouteRuntime.hpp/.cxx
  TopicRouteRuntime.hpp/.cxx
  InstanceStateMirror.hpp/.cxx

admin/
  CommandReader.hpp/.cxx
  StatusPublisher.hpp/.cxx
  ControllerJournalPublisher.hpp/.cxx
  RouterAdminTypes.idl
```

Keep `RouterController` independent of YAML and DDS API details where practical. Discovery
arrives as raw controller events; entity creation and status publication stay behind
interfaces, which makes route-state tests possible without running DDS.

## Core Class Responsibilities

| Class | Owns | Does not own |
|---|---|---|
| `RouterController` | mutable state, event queue, lifecycle ordering, status revision | hot sample forwarding loop internals |
| `RouteRegistry` | desired route specs and selected active-side route views | DDS readers/writers |
| `ParticipantRegistry` | DomainParticipants and participant-level partition generation | per-route read/write conditions |
| `DiscoveryDispatcher` | builtin-reader translation, participant tag table, ignore rules (D30) | route state transitions, endpoint-record storage |
| `TypeResolver` | type lookup order and DynamicType registration | command/status state |
| `QosResolver` | alias expansion and auto-match compatibility summaries | participant creation |
| `EntityFactory` | Topic/DataReader/DataWriter/ReadCondition creation and teardown helpers | global state mutation |
| `AsyncWaitSetDispatcher` | `AsyncWaitSet` and attached conditions | route status mutation |
| `RouteRuntime` | one active route/topic forwarding path and route-local fast counters | desired-state changes |
| `StatusPublisher` | generated status writer and snapshot serialization | direct reads from mutable route objects |
| `ControllerJournalPublisher` | generated controller event/decision journal writer | route control decisions, blocking controller progress indefinitely |

## Concurrency Rules

- `RouterController` is the single writer for `MutableRouterState`.
- The `ControllerEvent` queue is **MPSC**: `AsyncWaitSet` dispatch and middleware threads
  post events; only the controller strand drains (D12).
- DDS listener callbacks and `AsyncWaitSet` handlers must be short. They post events and
  return.
- Create, attach, detach, and close route DDS entities from the controller strand or through
  an executor owned by the controller.
- `AsyncWaitSetDispatcher` serializes condition attach/detach operations and rejects stale
  operations by checking the per-topic generation stamp (D23).
- `RouteRuntime` can use atomics for hot counters, but all externally visible operational
  state changes go through controller events.
- Published `RouterStatus` snapshots and `RouteView`s are immutable once built (D25).
- Shutdown is ordered: stop command intake, stop new discovery events, detach read
  conditions, stop `AsyncWaitSet`, close route entities, close admin entities, close
  participants.
