# Implementation Plan

> **Reframe banner (authoritative — see [Thesis & Tenets](thesis-and-tenets.md)).** This plan
> predates several decisions; where it conflicts, the tenets win. Specifically:
> - **ISC is out of scope.** Old Phase 10 ("keyed lifecycle mirroring") is **not** ISC recovery
>   — it is application-driven **meta-sample mirroring** (`dispose`/`unregister`) plus
>   **presence-driven reset**, and it is now **high confidence** (proven by `spikes/isc_recovery/`),
>   not medium. No read-retain / re-assert-on-`ALIVE`.
> - **Add a Presence/Health phase** (`RouterHealth` topic, roster, presence-driven reset) — see
>   [Presence & Health](presence-and-health.md). It slots after the core route mechanics.
> - **Add a Link-Metrics Capture phase** (`LinkStatsCollector`, per-peer LAN
>   `ActRouterLinkStats`, `RouterLinkProbe` RTT) immediately **after** the Presence/Health
>   phase — it reuses the roster/D15-tag GUID→router join and the WAN entities. **Capture
>   only**; health inference is gated on a netem link-degradation correlation experiment
>   (its own `spikes/` entry) — see [link-health.md](link-health.md) and D14. netem here is
>   the experiment's ground-truth generator, not a relay feature (Tenet 3 stands).
> - **`dynamic_data` is the default**; `serialized_cdr` (old Phase 9) stays a **late, opt-in,
>   eligibility-gated** optimization (no filter / no lifecycle).
> - **Admin command/status is LAN-local** (resolved open question, [command-status.md](command-status.md)).
> - **Impairment is the network's job** (EMANE/netem), not a router phase.
> - Recommended near-term build order is P0→P4 in [Thesis & Tenets](thesis-and-tenets.md)
>   "Consequences for the build"; the slices below remain a valid finer-grained breakdown.

## Phased High-Confidence Slices

The safest implementation path is a sequence of vertical slices. Each slice should produce
running code, a narrow test/demo, and a decision about whether the next slice is still worth
building. Avoid building a broad Routing Service clone in the early phases; prove the ACT
route engine one behavior at a time.

| Phase | Slice | Confidence | Proves | Stop / pivot signal |
|---|---|---|---|---|
| 0 | Build skeleton and admin IDL | High | executable, generated admin types, config file loading, logging, test harness shape | cannot reliably build/run Connext 7.7 C++ executable in the harness |
| 1 | Controller-owned state without DDS data routes | High | immutable snapshots, route state machine, command idempotency, disabled route status | state transitions become hard to reason about before DDS is involved |
| 2 | Static generated-type discovery smoke | High | participants, discovery cache, type/QoS summaries, status publication | discovery metadata is insufficient or unstable for route matching |
| 3 | One discovered route with explicit QoS | High | writer discovery creates reader/writer, attaches `ReadCondition`, forwards one topic | dynamic attach/detach or entity lifetime is unreliable |
| 4 | Role-aware control/platform route | High | one YAML route runs opposite sides on control/platform nodes; command path works | role abstraction creates ambiguous endpoint ownership |
| 5 | LAN `auto` QoS and output readiness | Medium-high | route waits for discovered LAN reader/writer and resolves compatible QoS | auto-match requires too many DDS policies for POC confidence |
| 6 | Command/status control loop | High | `ENABLE_ROUTE`, `DISABLE_ROUTE`, full status snapshots, duplicate command handling, controller event/decision journal | status, commands, or debug observability introduce racey state changes |
| 7 | Platform status/events replacement | High | control receives `PlatformStatus`, `PlatformCommandAck`, and `ContactReport` without Routing Service | ACT topic/type mapping diverges from the planned route model |
| 8 | Team partition route | Medium-high | `SET_PARTICIPANT_PARTITION` recreates affected entities and `PlatformData` crosses team scope | participant/partition changes cannot be made predictable enough |
| 9 | Serialized-CDR fast path | Medium | pass-through route avoids app-level field materialization where supported | Connext API surface is too awkward; keep `dynamic_data` first and revisit |
| 10 | Keyed lifecycle mirroring | Medium | one keyed route mirrors dispose and no-writers with recovered keys | key recovery in generic mode is not reliable; require generated-type lifecycle routes |
| 11 | Harness replacement | Medium-high | one platform then one control node runs without Routing Service | container/startup sequencing needs more infrastructure than the POC budget allows |

## Slice Details

### Phase 0: Build Skeleton And Admin IDL

Deliver:

- `router_main` executable with command-line config path and router instance name.
- `RouterAdminTypes.idl` generated into the build.
- basic structured logging and a no-DDS unit test target.
- sample control/platform and platform/team YAML files checked into the harness area.

Evidence:

- executable starts, validates config path, prints router identity, and exits cleanly.
- generated command/status types compile in a standalone test.

### Phase 1: Controller-Owned State Without DDS Data Routes

> **Contract pinned** — transition table, `discovery_state` rollup, `state_revision`
> semantics, idempotency bounds, and the Phase-1 fake seams are decided in
> [design-decisions.md](design-decisions.md) D1–D7; the review completions (redundant-command
> idempotent accept, `RESOLVING` abort edge, fixture-only route source) in D8 and D10;
> per-topic activation and per-topic status (route active when at
> least one topic is ready; `operational_state` derived over topic states) in D11; the
> implementation-readiness completions (named per-topic completion events, controller-owned
> matching and matched sets, global generation stamps, command admission seams) in D21–D24;
> the simplicity pass (the status snapshot is the generated `RouterStatus`; `TRANSIENT_LOCAL`
> status durability) in D25–D26.

Deliver:

- `RouterController`, `MutableRouterState`, `RouteState`, and `RouteView`. The status
  snapshot is the generated `RouterStatus` built at each revision bump — there are no
  separate snapshot/view classes (D25).
- the typed `ControllerEvent` queue drained on one strand — producer-side thread-safe from
  the start (MPSC, D12) — with `EntityFactory` and `StatusPublisher` behind interfaces and
  faked in tests (D3); per-topic completion events
  `TopicEntitiesReady` / `TopicTeardownComplete` and topic-scoped `RouteEntityError` (D21).
  Discovery events carry raw endpoint records; matching and the per-topic matched-endpoint
  sets are controller logic (D22), stamped by the global entity-generation counter (D23).
- route state machine implementing the D2 transition table: `operational_state` lifecycle
  guarded by the `discovery_state` rollup of per-route discovery facts (D1); `ROUTE_ERROR`
  sticky until command re-arm (D2).
- bounded command history: acks cached for accepted **and** rejected commands, FIFO 256,
  dedup on `command_id` per router (D4). `ENABLE_ROUTE`/`DISABLE_ROUTE` handled;
  `UPDATE_ROUTE`/`SET_PARTICIPANT_PARTITION` parsed-and-rejected (D7).
- global `uint64 state_revision` with the D5 increment predicate; per-route revision stamps.

Evidence:

- unit tests cover startup snapshot, disabled routes visible in status, enable route waits
  for discovery, duplicate command returns prior ack (no revision bump), rejected-command
  ack replay, and route error stores `last_error` and stays sticky until re-arm.
- transition-table conformance tests drive every D2 edge via synthetic controller events,
  including `ENABLED -> DEGRADED -> RESOLVING|WAITING` through the teardown-complete event
  and `RESOLVING -> WAITING_FOR_DISCOVERY` on discovery regression via the fake factory's
  pending-resolve completion (D8).
- redundant `ENABLE_ROUTE` with a **new** `command_id` on an already-enabled route returns
  an idempotent accept with no revision bump (D8).
- a two-topic fixture route proves per-topic activation (D11): the route reaches `ENABLED`
  when only its first topic is ready; the second topic later joins in place with no
  route-level transition (revision bumps, `topic_status` updates); one topic's creation
  failure lands in `TOPIC_ERROR` without stopping the forwarding sibling; the route derives
  `ERROR` only when all topics are errored.
- losing one of two matched input writers for a topic updates the matched set with **no**
  rollup change and no revision bump; the last writer's loss regresses the rollup — proven
  with raw endpoint-record events, since matching and set maintenance are controller logic
  (D20/D22).
- a `TopicEntitiesReady` / `TopicTeardownComplete` / `RouteEntityError` event carrying a
  stale generation stamp is discarded with no state change (D21/D23).
- `ENABLE_ROUTE`/`DISABLE_ROUTE` naming an unknown route returns a cached reject with no
  revision bump (D24). `CommandReceived` events are post-admission — target/wildcard
  matching is tested where it lives, in Phase 6 (D24).
- honesty note: DDS-dependent behavior (real resolve timing, real endpoint loss) is
  simulated by the fakes; Phases 2–3 implement the same D2 contract against Connext.

### Phase 2: Static Generated-Type Discovery Smoke

> **Contract pinned** — discovery mechanics (builtin readers + `ReadCondition`s on the
> `AsyncWaitSet`, disabled-participant no-loss startup, GUID-keyed upserts (the dispatcher-side
> cache since deleted by D30), MPSC event
> queue) and LAN-side type learning (`request_types_filter`, async TypeObject v2 arrival,
> optional-until-resolved type) are decided in [design-decisions.md](design-decisions.md)
> D12–D13; loop safety (participant tagging + `ignore_publication` self/same-node rules) in
> D15; lease tuning + purge handling in D16 (its fan-out since demoted to fallback by
> D28); status surfacing (no endpoint inventory in
> `RouterStatus`, real LAN `StatusPublisher` this phase) in D17; the QoS-summary captured
> subset in D19; config-driven participants, set-derived discovery facts, and the
> type-conflict policy in D20. This resolves this phase's confidence-investigation row.
> The simplicity pass (Tenet 9) is pinned in D27–D30: the endpoint record is the builtin
> topic data itself, endpoint removal is native per-endpoint, the expected-origin warning
> is a discovery-time matching rule, and the dispatcher collapses to a translator plus a
> participant table.

Deliver:

- `ParticipantRegistry` with participants purely from config, per instance
  (`control-platform`: LAN + WAN; `platform-team`: LAN + team-WAN); admin endpoints ride
  the LAN participant — there is no admin participant (D20).
- `DiscoveryDispatcher` backed by the builtin participant/publication/subscription readers via
  `ReadCondition`s on the `AsyncWaitSet` (D12) — a translator plus a small participant
  table (GUID → `act.router` tag/name), with **no endpoint-record cache**; removals ride
  the builtin readers' native per-endpoint instance transitions (D28/D30).
- endpoint records **are the builtin topic data values** (`PublicationBuiltinTopicData` /
  `SubscriptionBuiltinTopicData`) plus an `origin_router`/`ignored` sidecar — no
  hand-rolled record struct (D27); the D19 captured subset is the rule for what
  matching/auto-QoS may read; `origin_router` comes from the participant `user_data` tag
  join; same-node router publications are ignored at discovery (D15); per-topic
  matched-endpoint **sets**, not booleans (D20).

Evidence:

- start a tiny generated-type writer and reader in separate processes/domains.
- the real LAN `StatusPublisher` publishes `RouterStatus` samples (write-only; command
  reader stays Phase 6; `RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1)` so a late-joining
  observer receives the current snapshot on match — D26): matching route candidates are
  visible as per-topic
  `discovery_state` progressions, observable with any LAN subscriber (e.g. `rti_view`),
  and the full discovered-endpoint inventory — including `origin_router` and ignored
  endpoints — appears in the structured log, not in `RouterStatus` (D17). No route
  readers/writers are created yet.
- the smoke writer's endpoint appears **before** its type (asynchronous TypeLookup); the
  type resolves on a later builtin update of the same instance and the topic's discovery
  rollup walks `NONE → PARTIAL → READY` (D13).
- a writer from a second participant tagged `act.router` on the same node is recorded with
  its `origin_router` and ignored — it never appears as a route input candidate (D15).
- killing the smoke writer ungracefully (SIGKILL) removes its endpoints only after the
  participant lease expires; the smoke measures the stock default lease and the chosen
  short-LAN-lease timing (D16). Removal arrives as **native per-endpoint** `NOT_ALIVE`
  transitions on the builtin endpoint readers — the smoke counts them and confirms one per
  owned endpoint for both SIGKILL and graceful exit (D28; the D16 dispatcher fan-out returns as
  fallback only if this cardinality check fails). Graceful exit disposes promptly.

### Phase 3: One Discovered Route With Explicit QoS

> **Connext-confirmed execution shape** — validated against Connext 7.7 Modern C++ via
> `ask_connext_question` (2026-07-09) and pinned in D31: Phase 3 is not another synthetic
> controller test. It first builds the thin real runtime spine needed for one route, then
> adds dynamic entities and forwarding on top of that spine.

Deliver:

- thin real runtime spine from Phase 2's contracts: config-created participants,
  builtin participant/publication/subscription readers attached before participant enable,
  discovery records posted to the controller, and a real LAN `StatusPublisher`.
- generated-type `TypeResolver` fast path for explicit-QoS routes: discovered
  `topic_name` + registered `type_name` matching local generated support is sufficient
  **construction readiness**, not full remote schema-equivalence proof. DynamicType /
  TypeLookup validation remains the later stronger path.
- explicit-QoS `QosResolver` minimum path: aliases/defaults supply history and resource
  limits; no auto-QoS propagation yet.
- `EntityFactory` minimum path for one topic: create route input `DataReader` and output
  `DataWriter` after controller discovery readiness, call
  `dds::pub::ignore(participant, writer.instance_handle())` on the route output writer
  before any write and before attaching input conditions, then report
  `TopicEntitiesReady` with the controller-issued generation stamp.
- `AsyncWaitSetDispatcher` as the sole owner of route `ReadCondition` attach/detach;
  teardown detaches or otherwise quiesces the condition before closing DDS entities and
  posts `TopicTeardownComplete` only after the entity bundle is closed.

Evidence:

- publish one sample on the input side and receive it on the output side using real
  discovery, real controller events, real status publication, and real generated DDS
  entities.
- an unexpected-origin endpoint (a router-tagged writer matching the LAN input leg) is
  warned loudly at discovery time by the expected-origin rule in controller matching —
  there is no per-sample origin check (D15/D29).
- route transitions `WAITING_FOR_DISCOVERY -> RESOLVING -> ENABLED` in status.
- stopping the writer detaches the condition, closes route entities in order, and moves the
  route through degraded to waiting or resolving according to current discovery.
- repeated create/attach/forward/detach/close cycles do not crash, do not dispatch after
  close into closed handles, and stale completion events are ignored by generation.

### Phase 4: Role-Aware Control/Platform Route

Deliver:

- `RouteConfigParser`: full `routes:`/`participants:`/QoS-section YAML parsing (Phase 0's
  identity reader stays identity-only; Phases 1–3 use fixtures — D10).
- YAML selection for `source_side` / `destination_side` based on local `node.role`.
- `control_command` route with platform-side content filter on `msg.destination`.
- matching config loaded by both control and platform router instances.

Evidence:

- control-side router selects `control_lan -> control_wan`.
- platform-side router selects `platform_wan -> platform_lan`.
- Platform_30 receives commands addressed to Platform_30 and rejects/filters commands for
  Platform_31.

### Phase 5: LAN Auto QoS And Output Readiness

Deliver:

- LAN `auto` reader QoS derived from discovered local writers.
- LAN `auto` writer QoS delayed until compatible local readers are discovered.
- status fields that explain resolved reader/writer QoS summaries.
- history and resource limits always come from QoS aliases/defaults — they are **not
  propagated in discovery** and can never be derived from discovered endpoints (D19).

Evidence:

- route waits rather than creating a mismatched output writer when no local reader exists.
- route activates automatically once the compatible LAN reader appears.
- incompatible discovered endpoints produce `ROUTE_ERROR` or waiting status with a useful
  reason.

### Phase 6: Command/Status Control Loop

Deliver:

- command reader and ack writer on the LAN admin participant.
- `ENABLE_ROUTE`, `DISABLE_ROUTE`, and duplicate command handling; late-joiner catch-up
  comes from status durability.
- aggregate `RouterStatus` publication after accepted changes.
- controller event/decision journal writer on the LAN admin participant. The writer is
  always created with the command/status plumbing, but the recorder/subscriber is launched
  only in debug mode; without a matched debug reader, the journal produces no data-sample
  traffic beyond normal DDS endpoint discovery.

Evidence:

- disabled detail-status route appears in startup status with no entities.
- `ENABLE_ROUTE` moves it to waiting or enabled depending on discovery readiness.
- `DISABLE_ROUTE` detaches read conditions and closes route entities.
- duplicate `command_id` returns the original ack and does not increment state revision.
- with debug recorder enabled, every processed controller event records the input event,
  controller decision/outcome, pre/post `state_revision`, affected route/topic delta, and
  requested factory/status actions; with no recorder reader matched, route behavior and
  status publication are unchanged.

### Phase 7: Platform Status And Events Replacement

Deliver:

- `platform_primary_status` route.
- `platform_events` route for `PlatformCommandAck` and `ContactReport`.
- explicit WAN `PLATFORM` partition handling.

Evidence:

- control receives primary status and events from one platform without Routing Service.
- status snapshots show sample counters advancing per route.

### Phase 8: Team Partition Route

Deliver:

- `platform_team_to_wan` and `wan_team_to_platform` concrete routes.
- `team_wan.participant_partition` state and `SET_PARTICIPANT_PARTITION` command.
- recreate/reconcile behavior for entities that inherit participant partition.

Evidence:

- Platform_30 and Platform_31 do not exchange `PlatformData` with node-specific partitions.
- after both receive `TEAM_A`, `PlatformData` crosses between them.
- moving one platform out of the team stops delivery after rediscovery/rebuild.

### Phase 9: Serialized-CDR Fast Path

Deliver:

- `serialized_cdr` forwarding mode for compatible pass-through routes.
- fallback to `dynamic_data` when serialized forwarding cannot be used.
- metrics/logging that identify which forwarding path each route uses.

Evidence:

- `PlatformStatus` or a generated smoke topic forwards without app-level field
  materialization in the router.
- status reports forwarding mode and write failures.

### Phase 10: Keyed Lifecycle Mirroring

Deliver:

- one `dynamic_data` or `generated_type` lifecycle route with `mirror_instance_state: true`.
- key cache by reader instance handle.
- dispose/unregister forwarding.

Evidence:

- downstream reader observes matching dispose and no-writers state transitions.
- unrecoverable keys are logged and counted rather than silently dropped.

### Phase 11: Harness Replacement

Deliver:

- container/start scripts for one platform running both router instances.
- control node using `control-platform` router instance.
- Routing Service removed from the POC node stack for the tested routes.

Evidence:

- ACT quick-start works without Routing Service for one control and one platform.
- two-platform team scenario works for command, status, events, and `PlatformData`.

## Confidence-Increasing Investigations

These investigations should be short spikes, not new architecture phases. Each one should
produce a small executable, test, or written API note that either raises the slice confidence
or narrows the fallback path.

| Slice | Current confidence | Investigation | Confidence increases if | Fallback if not |
|---|---|---|---|---|
| Phase 2: discovery dispatcher | High — **resolved (D12/D13)** | ~~Compare built-in publication/subscription readers vs Connext discovery listeners~~ Decided: builtin readers + `ReadCondition`s on the `AsyncWaitSet`; endpoint fields validated against 7.7; LAN `request_types_filter` required for type learning | topic name, registered type name/type id, partition, and QoS summaries are available without fragile internal assumptions | use the API with the most stable metadata even if it is less elegant |
| Phase 3: dynamic entity lifecycle | High, but concurrency-sensitive | Write a tiny program that creates a reader/writer after discovery, attaches a `ReadCondition` to an `AsyncWaitSet`, then detaches and closes repeatedly | repeated attach/detach/close cycles do not race, leak, or callback after close | serialize all attach/detach/close on the controller strand and avoid aggressive rebuilds |
| Phase 5: LAN `auto` QoS | Medium-high | Capture QoS from actual ACT LAN endpoints and reduce it to the minimum compatible policy set for router readers/writers — bounded by the discoverable subset (D19): history/resource_limits are never in discovery and are alias-supplied by definition | a small deterministic subset of policies is enough for `ControlCommand`, `PlatformStatus`, and `PlatformData` | require explicit LAN QoS aliases for first POC routes and keep `auto` as POC-plus |
| Phase 8: team partition changes | Medium-high | Test participant-level partition change **in place via `set_qos`** (validated runtime-mutable in 7.7 — D15 side-finding) while writers/readers are active; recreate-affected-entities is the fallback | rediscovery and delivery are predictable after node-specific partition to `TEAM_A` and back | restart the `platform-team` router instance on team change for the first demo |
| Phase 9: serialized-CDR fast path | Medium | Build a standalone Connext 7.7 C++ pass-through for one generated type using DynamicData serialized-buffer APIs | the reader can access the CDR buffer and the writer can publish it without field materialization | ship first route runtime in `dynamic_data` mode and treat serialized CDR as optimization |
| Phase 10: keyed lifecycle mirroring | Medium | Test dispose/no-writers propagation with one generated keyed type and one DynamicData route using `reader.key_value()` or cached key fields | downstream reader observes matching instance states and keys can be recovered reliably | require generated-type route runtimes for lifecycle-sensitive topics |
| Phase 11: harness replacement | Medium-high | Replace Routing Service for one non-critical ACT route in container startup while leaving the rest unchanged | startup ordering, peer discovery, logs, and cleanup are understandable in compose/scripts | run the router sidecar in observe-only/status-only mode before removing Routing Service |
| Link-Metrics Capture phase (banner) | Capture design pinned (D14/D18); metric *meaning* unproven | netem correlation-experiment spike (own `spikes/` entry, Python driver): sweep delay/jitter/loss/rate/blackout one axis at a time against recorded `ActRouterLinkStats` + the ground-truth schedule; also empirically verify per-locator counter attribution and app-ack RTT probe behavior ([link-health.md](link-health.md)) | specific metrics demonstrably track specific impairments with usable lag and noise floor → a follow-up decision pins thresholds/classification feeding the `RouterHealth` rollup | metrics stay raw telemetry; no health inference ships; presence remains the only health authority |

Investigation order should be: Phase 3 attach/detach, Phase 9 serialized CDR API, Phase 10
key recovery, Phase 5 auto QoS, Phase 8 partition changes, then Phase 11 harness sequencing.
That order tests the hardest Connext API assumptions before spending time on ACT-specific
orchestration.

## Recommended First Milestone

Milestone 1 should stop after Phase 3. That gives a real Connext executable with
controller-owned state, discovery, dynamic endpoint creation, `AsyncWaitSet` attachment, and
one forwarded topic. It avoids ACT harness complexity until the core router mechanics are
proven.

Milestone 2 should cover Phases 4 through 7: role-aware control/platform routes plus command
and status behavior. At that point the router should replace the highest-value
control/platform Routing Service paths for one platform.

Milestone 3 should cover Phases 8 through 11: team partitions, serialized fast path,
lifecycle mirroring, and container harness replacement.

## Confidence Notes

- Highest confidence: controller state, YAML selection, generated admin IDL, explicit-QoS
  forwarding, command/status snapshots.
- Medium-high confidence: discovery dispatching, LAN auto-match, partition-driven team routes,
  ACT harness replacement.
- Medium confidence: serialized-CDR buffer forwarding and generic keyed lifecycle mirroring,
  because they depend on exact Connext 7.7 Modern C++ API ergonomics and type/key access.

Do not block earlier slices on the medium-confidence items. Use `dynamic_data` or
generated-type forwarding for the first working route, then optimize the pass-through path
once the discovery/controller/AsyncWaitSet lifecycle is boring.

## POC Tests

| Test | Setup | Pass condition |
|---|---|---|
| Config smoke | Load both platform YAML files for one platform node | both router instances select their routes; disabled routes appear in status and stay closed |
| Control command path | Control sim publishes `ControlCommand` to Platform_30 | Platform_30 receives only commands addressed to it |
| Platform status path | Platform_30 publishes `PlatformStatus` | Control receives status through router |
| Events path | Platform publishes `PlatformCommandAck` and `ContactReport` | Control receives both topics |
| Team disabled by default | Platform_30 and Platform_31 start with unique `team_wan.participant_partition` values | no platform-to-platform `PlatformData` crosses |
| Team assignment | Send `SET_PARTICIPANT_PARTITION team_wan=TEAM_A` to both platform routers | `PlatformData` crosses between both platforms |
| Detail status toggle | Send `ENABLE_ROUTE platform_detail_status` | control starts receiving detail status from target only; router publishes updated status |
| Router command/status | Send `SET_PARTICIPANT_PARTITION` or `ENABLE_ROUTE` | command ack is returned and status topic reports new state revision with the full route table |
| Serialized forwarding smoke | Route `PlatformStatus` with `forwarding_mode: serialized_cdr` | sample arrives downstream without app-level field materialization in the router |
| Meta-sample lifecycle | App disposes/unregisters a keyed instance | downstream sees matching `NOT_ALIVE_DISPOSED` / `NOT_ALIVE_NO_WRITERS` (meta-mirror, not ISC — no reconnect recovery) |
| Presence reset | A peer router declared `DEAD` on `RouterHealth` | relay unregisters that peer's instances downstream; a returning peer re-writes and they recover as data |

## Acceptance Criteria

The exercise is useful if:

- ACT quick-start works without Routing Service for one control and one platform.
- Multi-platform team assignment works without Routing Service for two platforms.
- Detail status can be enabled and disabled over the router control topic.
- Each router publishes a status sample after accepted command changes.
- Pass-through routes can use the Connext 7.7 C++ serialized-CDR forwarding mode.
- At least one keyed lifecycle route mirrors dispose and no-writers transitions.
- The YAML is short enough to explain in a demo and maps directly to ACT routes.
- Failure modes are explicit: bad route commands are rejected and acknowledged, not silent.

Stop the POC if replacing Routing Service requires reimplementing broad RS features before
the ACT flows work. The point is to find the narrow ACT-specific route engine, not to clone
Routing Service.

## Open Questions

- What is the smallest Connext 7.7 Modern C++ API surface needed for TypeLookup-driven
  `DynamicType` creation plus serialized-CDR forwarding?
- For lifecycle routes, is key recovery practical in `serialized_cdr` mode, or should those
  routes explicitly use `dynamic_data` / `generated_type` mode?
- ~~Should route command topics live on the existing ACT admin domain or a router-private
  control domain?~~ **Resolved:** neither — admin command/status reuse the router's **local LAN
  participant** (local per-node control, independent of WAN health), partition-ready for future
  WAN remote admin. See [command-status.md](command-status.md).
- Do we need route-level flow control in the POC, or is QoS-only prioritization enough for
  the first degraded-link exercise?
- ~~Can participant-level or publisher/subscriber partition changes be applied in place safely
  in Modern C++, or is recreate-on-change the right first implementation?~~ **Resolved
  (validated 7.7 — see [design-decisions.md](design-decisions.md) D15 side-finding):** both
  pub/sub and participant-level partitions are runtime-mutable via `set_qos` with automatic
  rematching, no entity recreation required (participant-level propagates more slowly via
  participant discovery). In-place change is the premise for Phase 8; recreate is the fallback.
- Should route definitions eventually support multi-output fanout, or should ACT keep one
  route per input/output pair for clarity?

## Relationship To Current Roadmap

This exercise is more aggressive than the current Phase 8 plan. Current plan:

```text
Routing Service carries bulk traffic; ISC router bypasses RS for state-critical topics.
```

Replacement exercise:

```text
Dynamic route router carries ACT traffic; Routing Service is removed from the POC node stack.
```

If the exercise works, the roadmap can split into two options:

- conservative path: keep Routing Service and use the router only where LP-1 forces it;
- user-owned path: replace Routing Service in the demo harness with the dynamic router and
  keep Routing Service as the production comparison baseline.
