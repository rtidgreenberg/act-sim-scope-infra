# C++ Dynamic DDS Router

> Alternate architecture exercise for the ACT simulation work. The current
> [roadmap](../roadmap.md) keeps Routing Service for bulk traffic and uses the ISC router
> only for state-critical bypass routes. This document set scopes the bare-minimum
> proof-of-concept for replacing Routing Service itself with a small, user-owned C++ DDS
> message router.

## Document Set

- [Thesis & Tenets](thesis-and-tenets.md): **read first** — the reframed foundation (why a custom relay, what it is/isn't, and the tenets every other doc traces to). Where docs conflict, this one wins.
- [Configuration](configuration.md): route YAML, QoS aliases, participant roles, and config validation.
- [Command And Status](command-status.md): DDS admin topics, command schema, status schema, and route states.
- [Code Architecture](code-architecture.md): C++ ownership model, controller/state design, AsyncWaitSet architecture, modules, and concurrency rules.
- [Runtime Behavior](runtime-behavior.md): startup, discovery-driven route activation, dynamic readers/writers, forwarding, and instance-state mirroring.
- [Implementation Plan](implementation-plan.md): phased implementation slices, tests, acceptance criteria, open questions, and roadmap relationship.
- [Design Decisions Log](design-decisions.md): running per-phase decision log (starts where the tenets stop) — currently the Phase 1 contract: state model, transition table, revisioning, idempotency, and test seams (D1–D7).
- [Connext Investigation Review](connext-investigation-review.md): Connext 7.7 API findings, design decisions, concerns, required spikes, and confidence updates.
- [ISC Findings & Path Forward](isc-findings.md): the Instance State Consistency investigation and **why ISC is not used** (Scenario A vs B, CORE-13337, the intermediary gap), with spike evidence — the basis for the decision in Tenet 2.
- [Presence & Health](presence-and-health.md): system-level (router/link) presence via one `RouterHealth` topic instead of per-topic liveliness; membership roster with dead/stale detection; assume-present + presence-driven reset; long-mission discovery-DB hygiene.
- [Link Metrics](link-health.md): capture-first per-peer WAN link metrics from reliable protocol statistics (NACKs, repair traffic, send-window backpressure, RTT probe via app-ack on a dedicated `RouterLinkProbe` topic); health *inference* deferred until a link-degradation correlation experiment (D14); multi-network posture: one WAN participant per network, never multi-homed (D18).
- [Mission Orchestrator (early concept capture)](orchestrator-design.md): a separate per-node process, not a router decision — single arbiter of control is the platform itself; C2 sends mission state (not commands) over a regular route; C2 liveliness comes free from the existing presence roster since C2 is just another router-role mesh node. Not yet a design-decisions.md entry.

## Goal

Build a minimal C++ router process that can stand in for the ACT per-node Routing Service for
the core simulation flows.

Target Connext version: **RTI Connext Professional 7.7**. The design assumes Connext 7.7
DynamicData, TypeObject v2, and on-demand TypeLookup behavior.

- load role-aware route definitions from YAML;
- create DDS DomainParticipants plus DynamicData or generated-type readers/writers from
  discovered endpoint matches;
- forward samples between local LAN domains and the WAN domain, and mirror application
  lifecycle (dispose/unregister) "meta" samples through the relay;
- accept runtime commands on a DDS control topic to enable, disable, add, remove, and
  update routes;
- provide **router/link presence awareness** (a `RouterHealth` topic) and **per-peer link
  metric capture** (reliable-protocol statistics + RTT probe, published on the LAN as
  `ActRouterLinkStats` — capture only, see [Link Metrics](link-health.md)).

> **Note (reframe):** an earlier draft cited preserving DDS Instance State Consistency (ISC)
> as a goal. That is **no longer the driver** — see [Thesis & Tenets](thesis-and-tenets.md)
> and [ISC Findings](isc-findings.md). The value is observability and control; ISC is out of
> scope.

The POC should prove the replacement shape, not reproduce every Routing Service feature.
The winning test is simple: run the ACT control and platform simulators without Routing
Service and still move the same command, status, event, and team topics through the node
gateway.

## Why Try This

A terminating relay is unavoidable to bridge ACT's segmented LAN/WAN domains while altering
traffic — Routing Service is already such a relay. So the question is **custom relay vs. RS**,
and the justification is **observability and control**, not semantic preservation (full
rationale in [Thesis & Tenets](thesis-and-tenets.md)):

- **No black box** — we own and can reason about every forwarding decision, vs. RS's opaque
  XML engine.
- **Inspectable, live-mutable YAML** config instead of a large XML Routing Service config.
- **Network capture** — the Modern C++ API exposes `rti::util::network_capture` (pcap of DDS
  traffic, incl. shared memory) that the Python binding lacks.
- **Router/link presence awareness** via liveliness callbacks on a `RouterHealth` topic.
- **Per-writer/reader protocol statistics** to assess DDS link health — the relay is
  the natural measurement point; RS and the network layer don't provide this. Scoped
  capture-first in [Link Metrics](link-health.md) (D14/D18): metrics ship with the router;
  their health *meaning* is assigned after a link-degradation correlation experiment.
- ACT-specific runtime control (team changes, detail-status, route toggles) on one control
  topic.

This is not a claim of production-equivalence to Routing Service. It is a deliberately small
experiment to find the minimum useful, observable, controllable substitute.

## POC Boundary

In scope:

- Static YAML config at startup.
- Per-router command input topic for route state and partition updates.
- Per-router route status topic that publishes current per-route state after accepted
  command changes.
- Modern C++ implementation targeting Connext 7.7, with DynamicData routes using
  TypeObject v2 / TypeLookup or the ACT XML type file as a deterministic fallback.
- **`dynamic_data` is the default forwarding mode.** `serialized_cdr` (skip-deserialization)
  is an opt-in, eligibility-gated optimization (no reader-side filter, no lifecycle) deferred
  to a late phase — see [Thesis & Tenets](thesis-and-tenets.md) Tenet 7.
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
- Meta-sample lifecycle mirroring: valid sample forwards as write; application `dispose`
  mirrors as dispose; `no-writers` mirrors as unregister (key recovered via `key_value()`).
  This is application-driven lifecycle only — **not** ISC (out of scope, Tenet 2).
- **System-level presence**: a `RouterHealth` topic with liveliness gives router/link presence;
  WAN data topics carry **no** liveliness. On a peer declared DEAD, the relay unregisters that
  peer's instances (presence-driven reset). See [Presence & Health](presence-and-health.md).
- Basic metrics and logs (one structured stream with the Connext logger bridged in): route
  created, sample forwarded, lifecycle mirrored, command accepted/rejected, status published.

Out of scope for the first POC:

- **Instance State Consistency (ISC) / cross-relay instance-state recovery** — investigated and
  deliberately dropped (Tenet 2, [ISC Findings](isc-findings.md)).
- **Link impairment (latency/loss/jitter)** — that is the emulated network's job (EMANE/netem),
  not the relay's (Tenet 3).
- **Per-topic liveliness across the WAN** — replaced by system-level presence (Tenet 4).
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
| Discovery event source | The router needs writer/reader discovery before it can build endpoints | Start with built-in publication/subscription readers or Connext discovery listeners per participant; hide the exact API behind `DiscoveryDispatcher` |
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
