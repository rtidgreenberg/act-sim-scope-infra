# C++ Dynamic DDS Router

> Alternate architecture exercise for the ACT simulation work. The current
> [roadmap](../roadmap.md) keeps Routing Service for bulk traffic and uses the ISC router
> only for state-critical bypass routes. This document set scopes the bare-minimum
> proof-of-concept for replacing Routing Service itself with a small, user-owned C++ DDS
> message router.

## Document Set

- [Configuration](configuration.md): route YAML, QoS aliases, participant roles, and config validation.
- [Command And Status](command-status.md): DDS admin topics, command schema, status schema, and route states.
- [Code Architecture](code-architecture.md): C++ ownership model, controller/state design, AsyncWaitSet architecture, modules, and concurrency rules.
- [Runtime Behavior](runtime-behavior.md): startup, discovery-driven route activation, dynamic readers/writers, forwarding, and instance-state mirroring.
- [Implementation Plan](implementation-plan.md): phased implementation slices, tests, acceptance criteria, open questions, and roadmap relationship.
- [Connext Investigation Review](connext-investigation-review.md): Connext 7.7 API findings, design decisions, concerns, required spikes, and confidence updates.

## Goal

Build a minimal C++ router process that can stand in for the ACT per-node Routing Service for
the core simulation flows.

Target Connext version: **RTI Connext Professional 7.7**. The design assumes Connext 7.7
DynamicData, TypeObject v2, and on-demand TypeLookup behavior.

- load role-aware route definitions from YAML;
- create DDS DomainParticipants plus DynamicData or generated-type readers/writers from
  discovered endpoint matches;
- forward samples between local LAN domains and the WAN domain;
- accept runtime commands on a DDS control topic to enable, disable, add, remove, and
  update routes;
- preserve keyed instance lifecycle for routes that require true DDS instance-state
  convergence.

The POC should prove the replacement shape, not reproduce every Routing Service feature.
The winning test is simple: run the ACT control and platform simulators without Routing
Service and still move the same command, status, event, and team topics through the node
gateway.

## Why Try This

Routing Service already solves most routing mechanics, but the ACT work has two pressures
that make a tiny custom router worth exploring:

- Instance State Consistency is not carried through Routing Service. A standalone router
  with real DDS endpoints on both legs can preserve true `instance_state` for keyed state
  topics.
- The ACT demo values inspectability. A C++ router with a YAML route file is easier to
  explain, instrument, and mutate live than a large XML Routing Service configuration.
- Runtime control can be made ACT-specific: team changes, detail-status enablement, and
  route toggles can share one control topic rather than relying on Routing Service remote
  admin resources.

This is not a claim that a custom router is production-equivalent to Routing Service. It is
a deliberately small experiment to find the minimum useful substitute.

## POC Boundary

In scope:

- Static YAML config at startup.
- Per-router command input topic for route state and partition updates.
- Per-router route status topic that publishes current per-route state after accepted
  command changes.
- Modern C++ implementation targeting Connext 7.7, with DynamicData routes using
  TypeObject v2 / TypeLookup or the ACT XML type file as a deterministic fallback.
- Serialized-CDR forwarding fast path for pass-through routes, using DynamicData
  skip-serialization/deserialization mode where supported.
- Routes define one input participant, one output participant, a topic list, and a QoS
  pattern.
- Exact topic names in route topic lists; no regex expansion in the first POC.
- Domain-to-domain forwarding for the ACT LAN/WAN patterns.
- Content filter for `ControlCommand.msg.destination`.
- Publisher/subscriber partition QoS for CONTROL and PLATFORM traffic, plus team WAN
  participant-level scoping.
- Predefined QoS profile aliases, so command/event/status behavior is named once and
  assigned by route endpoints.
- Optional LAN QoS auto-match, so local readers/writers can inherit compatible QoS from
  discovered endpoints or local defaults instead of being specified on every route.
- Route enable and disable without process restart.
- Instance lifecycle mirroring: valid sample forwards as write; dispose mirrors as dispose;
  no-writers mirrors as unregister when the key can be recovered.
- Basic metrics and logs: route created, sample forwarded, lifecycle transition mirrored,
  command accepted or rejected, router status published.

Out of scope for the first POC:

- Routing Service auto-topic-route parity.
- Regex route expansion.
- Multi-output fanout from one reader.
- Transform plugins.
- Durable persistence, replay, or store-and-forward.
- Built-in flow controllers and full TransportPriority scheduling.
- XML generation compatibility with Routing Service.
- True zero-copy router handoff across different DomainParticipants or domains.
- Security.
- Full remote-admin compatibility.
- Production discovery scaling, CDS, or static peer management.

## Technical Implementation Gaps To Close

The architecture is small enough for a POC, but these details need deliberate design before
coding starts:

| Gap | Why it matters | POC decision |
|---|---|---|
| Central state ownership | Discovery callbacks, command samples, route read conditions, and status publishing all want to observe or change route state | Use one `RouterController` as the only writer to mutable router state; route runtimes receive read-only views and report events back |
| Discovery event source | The router needs writer/reader discovery before it can build endpoints | Start with built-in publication/subscription readers or Connext discovery listeners per participant; hide the exact API behind `DiscoveryIndex` |
| Type resolution order | Dynamic route creation still requires a registered type or `DynamicType` | Resolve in order: generated type support, loaded XML, discovered TypeObject/TypeLookup; fail route if no type is available before deadline |
| QoS auto-match algorithm | `auto` LAN endpoints need deterministic compatibility rules | Copy the minimum compatible policies from the discovered peer, then apply route/topic overrides; log the resolved profile/policy set in status |
| Output-side readiness | A discovered input writer is not enough if the route's output side uses `auto` QoS | For `auto` output writer QoS, wait for a compatible local reader; explicit output QoS can create the writer immediately |
| AsyncWaitSet mutation safety | Read conditions are added and removed while discovery and commands are running | Attach/detach only through `AsyncWaitSetDispatcher`; detach before closing readers; never close a reader from inside its own read callback |
| Endpoint loss and flapping | DDS discovery can briefly lose and regain endpoints | Debounce loss for the POC with a short route-level grace period, then mark `ROUTE_WAITING_FOR_DISCOVERY` and rebuild if QoS/type changed |
| Command idempotency | Control commands may be retried over reliable DDS | Track `command_id` per target router and return the previous ack for duplicate accepted/rejected commands |
| Status consistency | Operators need one coherent route table, not partial per-route fragments | Publish immutable aggregate `RouterStatus` snapshots derived from the controller state revision |
| Partition changes | Team changes affect participant-level discovery and inherited endpoint partitions | For `team_wan.participant_partition`, recreate affected participant-scoped entities and re-run discovery matching |
| Key recovery | Serialized pass-through may not expose key fields for dispose/unregister | Use `dynamic_data` or generated-type mode for lifecycle-mirroring routes until key recovery is proven for serialized CDR |
| Backpressure and slow writers | Reliable WAN writers can block or accumulate samples | First POC records write failures and DDS return codes; production flow control remains out of scope |
| Shutdown ordering | Attached conditions and DDS entities have ownership dependencies | Stop command intake, detach conditions, stop the `AsyncWaitSet`, close route entities, then close participants |
| Test harness | The router replaces infrastructure that was previously XML-driven | Add tests for discovery activation, disabled route status, duplicate commands, partition update, endpoint loss, and one lifecycle-mirroring route |

## ACT Routes To Replace First

The ACT submodule uses these channels in
[harness/act/config/routing/routing_service_config.xml](../../harness/act/config/routing/routing_service_config.xml):

| ACT flow | Topics | Current Routing Service behavior | POC router behavior |
|---|---|---|---|
| Control to platform commands | `ControlCommand` | control LAN to WAN partition `CONTROL`; platform WAN to platform LAN with content filter on `msg.destination` | one role-aware control/platform route selected differently on control and platform instances |
| Platform primary status | `PlatformStatus` | platform LAN to WAN partition `PLATFORM`; WAN to control LAN | one role-aware control/platform route selected differently on platform and control instances, initially enabled |
| Platform detail status | route variable currently `NULL`; detail route disabled by default | platform detail-status session enabled by remote admin | role-aware route is defined and disabled, so it appears in status before control command enables it |
| Platform events | `PlatformCommandAck`, `ContactReport` | platform LAN to WAN partition `PLATFORM`; WAN to control LAN | one role-aware control/platform route, initially enabled |
| Control events | `ContactReport` | control LAN to WAN partition `CONTROL`; platform WAN to LAN | optional role-aware control/platform route if needed for the scenario |
| Platform team traffic | `PlatformData` | platform LAN to `team_wan`; participant-level partition initially node-specific such as `PLATFORM_30`, changed to uppercase team name at runtime | route pair using a team participant partition updated by control command |

The ACT DynamicData types come from
[harness/act/node_sim/datamodel/act_types.xml](../../harness/act/node_sim/datamodel/act_types.xml):

- `control_command` on topic `ControlCommand`
- `control_command_ack` on topic `PlatformCommandAck`
- `platform_status` or `platform_primary_status` on topic `PlatformStatus`
- `platform_detail_status` on the detail-status topic selected by the private override
- `platform_data` on topic `PlatformData`
- `contact_report` on topic `ContactReport`

## Process Model

Run one router process per configured routing instance. The POC uses two instance classes:

- `control-platform`: runs on the control node and on each platform node. It carries
  command, status, and event traffic between control and platforms.
- `platform-team`: runs on each platform node. It carries platform-to-platform team traffic
  and owns the `team_wan.participant_partition` group scope.

Control node:

```text
Control app domain 20 <-> control-platform router <-> WAN domain 200
```

Platform node:

```text
Platform app domain 30+n <-> control-platform router <-> WAN domain 200
Platform app domain 30+n <-> platform-team router    <-> team WAN participant on WAN domain 200
```

Across the two instance classes, the router owns the same logical participant roles that Routing Service owned:

- `control_lan`
- `control_wan`
- `platform_lan`
- `platform_wan`
- `team_wan`

For the POC, each router instance creates only the participants referenced by route sides
that match its local role. A platform node runs both router instances; a control node runs only the
`control-platform` instance. The control/platform config can define both roles; the local
`node.role` selects which participant definitions are instantiated and which side of each
role-aware route runs.
