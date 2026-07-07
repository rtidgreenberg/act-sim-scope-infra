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

The POC should default pass-through routes to `serialized_cdr` and fall back to
`dynamic_data` or `generated_type` only when the route needs field access, transformation,
or lifecycle/key handling. Connext 7.7 is the pinned target for the CDR-buffer forwarding
and TypeObject v2 / TypeLookup assumptions in this document.

## AsyncWaitSet Implementation Architecture

Each router instance is a small runtime around a route registry, discovery index, and one
`AsyncWaitSet`:

```text
RouterInstance
  ConfigLoader
  ParticipantRegistry
  RouteRegistry
  DiscoveryIndex
  EntityFactory
  AsyncWaitSetDispatcher
  CommandHandler
  StatusPublisher
```

- `ConfigLoader` reads YAML and selects the local side of role-aware routes. It does not
  create route readers or writers.
- `ParticipantRegistry` creates the DomainParticipants needed by the selected route sides
  and admin topics.
- `DiscoveryIndex` watches discovered publications and subscriptions on those participants
  and records topic name, type identifiers, registered type name, partitions, and QoS.
- `RouteRegistry` stores every configured route for the instance, including disabled routes
  and routes waiting on discovery. Desired-enabled plus discovery readiness determines
  whether DDS entities exist.
- `EntityFactory` creates topics, readers, writers, content-filtered topics, read
  conditions, and serialized-CDR forwarding helpers after discovery supplies type and QoS
  input.
- `AsyncWaitSetDispatcher` owns the `AsyncWaitSet`; it attaches route read conditions as
  routes become active and detaches them when routes are disabled, rediscovered, or rebuilt.
- `CommandHandler` parses command samples, performs cheap target/idempotency checks, and
  posts accepted mutations to `RouterController`.
- `StatusPublisher` emits one aggregate `RouterStatus` sample after startup, accepted
  commands, discovery-driven activation/deactivation, and errors.

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
  reads shared_ptr<const RouterStateSnapshot>
  writes aggregate RouterStatus samples
```

The state model should be copy-on-write at the router level:

```text
MutableRouterState
  node_name
  router_name
  router_id
  state_revision
  participants: map<string, ParticipantState>
  routes: map<string, RouteState>
  command_history: bounded map<command_id, RouterCommandAck>

RouterStateSnapshot
  immutable copy of MutableRouterState at one state_revision

RouteView
  immutable desired route spec plus resolved local active side
  immutable topic list and endpoint policy references
  current state_revision

RouteState
  desired: RouterRouteSpec
  operational_state
  discovery_state
  resolved_type_name
  resolved_reader_qos_summary
  resolved_writer_qos_summary
  active_entity_generation
  counters
  last_error
```

The rule of thumb is: **routes read state, the controller writes state**. A route runtime may
own fast-path DDS entities and route-local counters, but it does not decide global route
state. When it observes something meaningful, it posts an event such as
`SampleForwarded`, `WriteFailed`, `EndpointLost`, `LifecycleMirrored`, or `RouteEntityError`.
The controller folds that event into `MutableRouterState`, increments `state_revision` when
the externally visible state changes, creates a new `RouterStateSnapshot`, and asks
`StatusPublisher` to publish it.

For sample counters, use one of two POC-safe options:

- simple path: route runtimes post sampled counter deltas to the controller after each
  dispatch batch;
- faster path: route runtimes keep atomics for hot counters, and the controller samples
  them when building a status snapshot.

Do not let the status topic read arbitrary mutable route objects. Status is always derived
from the controller's snapshot, which prevents half-applied command/discovery transitions
from leaking into `RouterStatus`.

The status-facing object can be a small immutable adapter rather than the full controller
state:

```text
RouteStatusView
  route_name
  desired spec
  operational state
  discovery summary
  resolved type/QoS summaries
  counters
  last_error

RouterStatusView
  node/router identity
  participant summaries
  vector<RouteStatusView>
```

`StatusPublisher` converts `RouterStatusView` into the generated DDS `RouterStatus` type.
Route runtimes may hold `shared_ptr<const RouteView>` for forwarding decisions, but should
not hold `RouteStatusView`; status is the controller's outward report, not the route's
working state.

## Controller Event Model

The controller should process these event categories on one serialized strand or queue:

| Event | Source | Controller action |
|---|---|---|
| `CommandReceived` | command reader | validate target and command id, update desired state, publish ack, reconcile route |
| `PublicationDiscovered` | discovery index | update discovery cache, try to activate matching desired-enabled routes |
| `SubscriptionDiscovered` | discovery index | update output readiness for `auto` writer QoS routes |
| `EndpointLost` | discovery index or route runtime | mark route degraded/waiting, detach conditions, close or rebuild entities |
| `RouteDataReady` | `AsyncWaitSetDispatcher` | dispatch to route runtime, then fold counter/error deltas into state |
| `RouteEntityError` | entity factory or route runtime | mark route `ROUTE_ERROR`, store `last_error`, publish status |
| `StatusRequested` | command reader or timer | publish current snapshot without changing revision unless state changed |
| `ShutdownRequested` | signal/main thread | quiesce intake, detach waitset conditions, close entities and participants |

This keeps DDS callback threads shallow. They translate DDS notifications into controller
events and return quickly. The controller can then perform entity creation/destruction in a
known order and publish a coherent status snapshot.

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
  DiscoveryIndex.hpp/.cxx
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
  RouterAdminTypes.idl
```

Keep `RouterController` independent of YAML and DDS API details where practical. It should
depend on interfaces such as `DiscoveryIndex`, `EntityFactory`, and `StatusPublisher`, which
makes route-state tests possible without running DDS.

## Core Class Responsibilities

| Class | Owns | Does not own |
|---|---|---|
| `RouterController` | mutable state, event queue, lifecycle ordering, status revision | hot sample forwarding loop internals |
| `RouteRegistry` | desired route specs and selected active-side route views | DDS readers/writers |
| `ParticipantRegistry` | DomainParticipants and participant-level partition generation | per-route read/write conditions |
| `DiscoveryIndex` | discovered endpoint cache and match notifications | route state transitions |
| `TypeResolver` | type lookup order and DynamicType registration | command/status state |
| `QosResolver` | alias expansion and auto-match compatibility summaries | participant creation |
| `EntityFactory` | Topic/DataReader/DataWriter/ReadCondition creation and teardown helpers | global state mutation |
| `AsyncWaitSetDispatcher` | `AsyncWaitSet` and attached conditions | route status mutation |
| `RouteRuntime` | one active route/topic forwarding path and route-local fast counters | desired-state changes |
| `StatusPublisher` | generated status writer and snapshot serialization | direct reads from mutable route objects |

## Concurrency Rules

- `RouterController` is the single writer for `MutableRouterState`.
- DDS listener callbacks and `AsyncWaitSet` handlers must be short. They post events and
  return.
- Create, attach, detach, and close route DDS entities from the controller strand or through
  an executor owned by the controller.
- `AsyncWaitSetDispatcher` serializes condition attach/detach operations and rejects stale
  operations by checking `active_entity_generation`.
- `RouteRuntime` can use atomics for hot counters, but all externally visible operational
  state changes go through controller events.
- `RouterStateSnapshot`, `RouteView`, and `RouterStatusView` are immutable once published.
- Shutdown is ordered: stop command intake, stop new discovery events, detach read
  conditions, stop `AsyncWaitSet`, close route entities, close admin entities, close
  participants.
