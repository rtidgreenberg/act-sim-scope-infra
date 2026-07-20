# Implementation Plan

> **Reframe banner (authoritative — see [Thesis & Tenets](thesis-and-tenets.md)).** This plan
> predates several decisions; where it conflicts, the tenets win. Specifically:
> - **ISC is out of scope.** The keyed-lifecycle phase (now **Phase 12**; "Phase 10" in
>   pre-D72 text) is **not** ISC recovery — it is application-driven **meta-sample
>   mirroring** (`dispose`/`unregister`) plus **presence-driven reset**, and it is
>   **high confidence** (proven by `spikes/isc_recovery/`), not medium. No read-retain /
>   re-assert-on-`ALIVE`.
> - **The Presence/Health phase is numbered: it is Phase 8** (`RouterHealth` topic, roster,
>   `ActRouterMeshStatus`) — see [Presence & Health](presence-and-health.md) and D72. The
>   presence-driven *reset action* lands with the lifecycle mirror in Phase 12, where the
>   keyed fixture and instance bookkeeping live.
> - **The Link-Metrics Capture phase is Phase 9** (`LinkStatsCollector`, per-peer LAN
>   `ActRouterLinkStats`, `RouterLinkProbe` RTT) — immediately after Presence/Health,
>   reusing the roster/D15-tag GUID→router join and the WAN entities. **Capture
>   only**; health inference is gated on a netem link-degradation correlation experiment
>   (its own `spikes/` entry) — see [link-health.md](link-health.md) and D14. netem here is
>   the experiment's ground-truth generator, not a relay feature (Tenet 3 stands).
> - **`dynamic_data` is the default**; `serialized_cdr` (now Phase 11; "Phase 9" in pre-D72
>   text) stays a **late, opt-in, eligibility-gated** optimization (no filter / no
>   lifecycle).
> - **Admin command/status is LAN-local** (resolved open question, [command-status.md](command-status.md)).
> - **Impairment is the network's job** (EMANE/netem), not a router phase.
> - The numbered phase sequence below is the **single build ordering** (D72) — earlier
>   drafts' informal "P0→P4" shorthand is retired; [Thesis & Tenets](thesis-and-tenets.md)
>   "Consequences for the build" states consequences, not an ordering.

## Phased High-Confidence Slices

The safest implementation path is a sequence of vertical slices. Each slice should produce
running code, a narrow test/demo, and a decision about whether the next slice is still worth
building. Avoid building a broad Routing Service clone in the early phases; prove the ACT
route engine one behavior at a time.

> **Renumbering (D72, 2026-07-16).** With Milestone 2 closed, the two banner phases
> (Presence/Health, Link-Metrics Capture) took numbered slots between the old Phases 7 and 8.
> Mapping for anything written before D72 — including decision entries D1–D71, which always
> use the OLD numbers:
>
> | Old number | New number | Slice |
> |---|---|---|
> | — (banner) | **8** | Presence & Health |
> | — (banner) | **9** | Link-Metrics Capture |
> | 8 | **10** | Team partition route |
> | 9 | **11** | Serialized-CDR fast path |
> | 10 | **12** | Keyed lifecycle mirroring + presence-driven reset |
> | 11 | **13** | Harness replacement |

| Phase | Slice | Confidence | Proves | Stop / pivot signal |
|---|---|---|---|---|
| 0 | Build skeleton and admin IDL | **Done** | executable, generated admin types, config file loading, logging, test harness shape | cannot reliably build/run Connext 7.7 C++ executable in the harness |
| 1 | Controller-owned state without DDS data routes | **Done (D1–D11, D21–D26)** | immutable snapshots, route state machine, command idempotency, disabled route status | state transitions become hard to reason about before DDS is involved |
| 2 | Static generated-type discovery smoke | **Done (D12–D20, D27–D30)** | participants, discovery translation, type/QoS summaries, status publication | discovery metadata is insufficient or unstable for route matching |
| 3 | One discovered route with explicit QoS | **Done (D34)** | writer discovery creates reader/writer, attaches `ReadCondition`, forwards one topic | dynamic attach/detach or entity lifetime is unreliable |
| 4 | Role-aware control/platform route | **Done (D38)** | one YAML route runs opposite sides on control/platform nodes; command path works | role abstraction creates ambiguous endpoint ownership |
| 5 | LAN `auto` QoS and output readiness | **Done (D45)** | static asymmetric QoS matches by RxO construction; writer derives deadline/liveliness from matched readers | residual RxO mismatches (ownership/durability/liveliness) turn out to be common on the target LAN |
| 6 | Command/status control loop | **Done (D57/D58)** | `ENABLE_ROUTE`, `DISABLE_ROUTE`, full status snapshots, duplicate command handling, controller event/decision journal | status, commands, or debug observability introduce racey state changes |
| 7 | Platform status/events replacement | **Done (D65/D67/D69/D70/D71)** | the verbatim production `control-platform.yaml` runs as a two-process pair without Routing Service; DDS is the matching authority (D64/D66) | ACT topic/type mapping diverges from the planned route model |
| 8 | Presence & Health | **Done (D75/D76)** | `RouterHealth` heartbeat, per-router roster with ALIVE/STALE/DEAD, LAN `ActRouterMeshStatus` aggregate, the D74 identity flip | ~~liveliness/lease mechanics don't produce a stable DEAD signal ahead of participant purge~~ disproven by `spikes/presence/` (DEAD 2.6–5.3 s, purge trailing 11–16 s) |
| 9 | Link-Metrics Capture | **DONE (D82, 2026-07-20)** — capture only; `LinkStatsCollector` ships (D14/D18/D81 realized); *inference* stays gated on the correlation experiment | per-peer reliable-protocol counters + app-ack RTT published on the LAN, nothing new on the WAN except the probe pair — proven by `test_link_stats.py` + `test_link_stats_wire_frugal` | ~~matched-endpoint statuses or app-ack don't attribute per peer~~ resolved: per-peer name attribution via the discovery DB, e2e-confirmed |
| 10 | Team partition route | Medium-high — **needs its readiness pass first** (the least-prepared numbered phase; see the section's gap list) | `ADD_PARTICIPANT_PARTITION`/`REMOVE_PARTICIPANT_PARTITION` (D83, multi-valued set) applies in place and `PlatformData` crosses/stops crossing team scope **or** a direct peer's own identity partition, observable as matched-count transitions | participant/partition changes cannot be made predictable enough |
| 11 | Serialized-CDR fast path | Medium — **opt-in, off the critical path** (Tenet 7) | pass-through route avoids app-level field materialization where supported | Connext API surface is too awkward; keep `dynamic_data` and drop the optimization |
| 12 | Keyed lifecycle mirroring + presence-driven reset | **High** (mechanism proven by `spikes/isc_recovery/`; needs a deliberately keyed fixture, Tenet 8) | one keyed route mirrors dispose/unregister with recovered keys; peer-DEAD unregisters that peer's instances downstream | key recovery in generic mode is not reliable; require generated-type lifecycle routes |
| 13 | Harness replacement | Medium-high; inherently last | one platform then one control node runs without Routing Service | container/startup sequencing needs more infrastructure than the POC budget allows |

### Execution protocol for implementing sessions

Every remaining phase follows the loop that delivered Phases 0–7. An implementing session
should:

1. **Find the highest `D<n>`** at the bottom of [design-decisions.md](design-decisions.md) —
   it is the single authority; where any doc (including this one) conflicts, the decision log
   wins until reconciled.
2. **Never implement a phase whose readiness pass hasn't been pinned.** A readiness pass
   (D21–D24, D54–D56, D59–D63, D66 are the models) resolves each listed open fork as a new
   accepted D-entry *before* code. Forks marked "user decision" below need the user;
   recommended defaults are stated so the ask is a confirmation, not an open design session.
3. **Spike Connext-hard assumptions first** (`spikes/<name>/` with `PLAN.md`, Python driver,
   runner per repo convention) — and treat the connext MCP as a hint only; the build/run is
   the arbiter (see `docs/connext-ai-issues/`).
4. **Map every evidence bullet 1:1 onto a named test** before coding (D56 pattern); "done"
   must be checkable, not asserted.
5. Run the Python e2e suite **from the repo root** (`python3 -m pytest router/test_e2e -q`;
   relative `harness/act` paths break otherwise and leak `router_main` processes), keep
   runtime files off the vboxsf share, and check `/dev/shm` after kill-based tests.
6. After implementing, run an independent `/code-review` over the diff (D43/D44 precedent —
   a second pass finds real gaps even after a clean first review), then pin the
   implementation D-entry with its evidence and reconcile the docs it lists.

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
  matching/auto-QoS may read; `origin_router` comes from the participant identity join
  (`participant_name`/`role_name` since the Phase 8 flip — D74/D76; originally the
  `user_data` tag); same-node
  router publications are ignored at discovery (D15); per-topic
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
>
> **Status: shipped and test-verified (D34).** `router/test/test_route_forward.cxx` proves
> end-to-end forwarding across two participants/domains from real discovery, `ROUTE_ENABLED`
> on the status stream, and D32 teardown on source loss (stable over repeated runs). Building
> this surfaced and fixed a latent endpoint-loss bug in the Phase 2.5 dispatcher (D33).

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
- `AsyncWaitSetDispatcher` as the sole owner of route `ReadCondition` attach/detach.
  Teardown uses the blocking `detach_condition()` as the barrier (D32): on the controller
  strand, `detach_condition` → `cond.close()` → close input reader → close output writer,
  then post `TopicTeardownComplete` only after the entity bundle is closed. `unlock_condition`
  is never called, so a route's forwarding handler is never dispatched concurrently.

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

> **Contract pinned (D35–D37).** Forwarded payloads use **DynamicData** loaded from a DDS-type
> XML at runtime (`QosProvider` → `DynamicType` by name), beside the Phase 3 generated-type lane
> which stays for admin/fast-path routes (D35). The data model is **reference-only** — the router
> is a generic relay coupled to no application type, so Phase 4 authors its own clean example
> DDS-type XML (`act_types.xml` is illustrative, not a dependency). Config is parsed with
> **yaml-cpp** (FetchContent), not a hand-rolled nested parser (D36). Content-filter shape is
> validated: nested `<field>.destination = %0`, string param quoted as `"'Platform_30'"`,
> `ContentFilteredTopic<DynamicData>` supported. Confidence **high** with the internal build
> order below (D37): prove the Connext-hard pieces (DynamicData forwarding, then CFT) before the
> config plumbing.
>
> **Status: shipped and test-verified (D38).** `test_dynamic_forward` (DynamicData forward +
> content filter + D32 teardown) and `test_route_config` (both role selections + filter-param
> substitution from `control-platform.yaml`) pass; 8/8 targets green.

Deliver (in D37 order):

- **DynamicData route runtime/factory** beside the Phase 3 typed one: `TypeResolver` gains
  `get_dynamic_type(name)` (loads a DDS-type XML via `QosProvider`); `Topic<DynamicData>` from the
  resolved `DynamicType`; reader/writer `<DynamicData>`; same D31.4 create-order and D32 teardown.
  Ship a small router-authored example DDS-type XML (the data model is reference-only — D35), so
  the load shape is controlled by us.
- **ContentFilteredTopic** on the input reader: `<field>.destination = %0`, node-name parameter
  substituted at creation, re-targetable via `filter_parameters()`.
- `RouteConfigParser` (yaml-cpp): full `routes:`/`participants:`/QoS-section parsing (Phase 0's
  identity reader stays identity-only; Phases 1–3 use fixtures — D10), with
  `source_side`/`destination_side` selected by local `node.role`.
- matching config loaded by both control and platform router instances.

Evidence:

- DynamicData forwarding smoke: `control_command` (loaded from XML) forwards across two
  participants/domains and tears down on source loss (D32) — the Phase 3 evidence, re-proven for
  the DynamicData payload path.
- control-side router selects `control_lan -> control_wan`.
- platform-side router selects `platform_wan -> platform_lan`.
- Platform_30 receives commands addressed to Platform_30 and rejects/filters commands for
  Platform_31.

### Phase 5: LAN Auto QoS And Output Readiness

> **Contract pinned (D39–D40, amended by D42).** Reader-side `auto` derivation is
> **deleted, not implemented**: route input readers use one fixed weakest-request profile
> (`BEST_EFFORT` + `VOLATILE` + defaults, `DataRepresentation` union), which matches every
> discovered writer by RxO construction — reader-side QoS immutability stops mattering.
> Output writers offer a fixed strong baseline (`RELIABLE` + `TRANSIENT_LOCAL` — the TL
> offer already *is* the durability auto-match, D42) and derive two policies from the
> matched local readers: **deadline** (mutable → adapted in place via `set_qos`) and
> **liveliness** kind + lease at creation (D42; `AUTOMATIC` honored automatically by the
> middleware, `MANUAL` kinds by forwarded writes plus `LIVELINESS_CHANGED`-driven
> `assert_liveliness()` propagation from the input leg).
> Residual immutable mismatches — Ownership (equality RxO), durability above
> `TRANSIENT_LOCAL`, presentation, late-joiner liveliness stronger than created — are
> warned via the incompatible-QoS statuses with `last_policy_id`, first-resolved-wins
> (D20 precedent), never auto-adapted.
> Confidence **high** (D40); the F10→F6 factory/alias unification and F1/F4/F3
> rebuild-leak/abort prerequisites are **done** (D41,
> [phase3-4-code-review.md](phase3-4-code-review.md)).
>
> **Status: shipped and test-verified (D45).** The readiness gate is output-side only
> (`output_uses_auto_qos` — refines the old both-sides predicate), XML QoS-alias lookup
> is re-pinned to Phase 7 (its first consumers), and the quiet-MANUAL liveliness
> residual is documented. `test_auto_qos` proves all five evidence items end-to-end;
> 9/9 targets green.

Deliver:

- the fixed weakest-request input-reader profile and strong-offer output-writer baseline
  in `QosResolver` (alias override still honored when a route names one), landing once in
  the unified `RouteEntityFactory` skeleton (D41).
- output readiness: writer creation gated on ≥1 compatible discovered local reader (the
  D20/D22 matched sets already know); at creation the writer derives liveliness
  (kind = max requested, lease = min requested — D42) and deadline (min requested period)
  from the matched readers; a stricter deadline from a later reader is tightened in place
  via `set_qos`.
- upstream-liveliness propagation for `MANUAL`-kind routes: `LIVELINESS_CHANGED` on the
  input reader's `StatusCondition` → `assert_liveliness()` on the output writer while
  upstream is alive (D42).
- `REQUESTED_INCOMPATIBLE_QOS` / `OFFERED_INCOMPATIBLE_QOS` enabled on route-entity
  `StatusCondition`s attached to the `AsyncWaitSet` → route warning / status reason naming
  the failing policy (closes F5's silent no-match structurally).
- status fields that explain resolved reader/writer QoS summaries.
- history and resource limits always come from QoS aliases/defaults — they are **not
  propagated in discovery** and can never be derived from discovered endpoints (D19,
  reconfirmed against 7.7).

Evidence:

- route waits rather than creating an output writer when no compatible local reader
  exists, and activates automatically once one appears.
- `BEST_EFFORT` and `VOLATILE` application writers match the route input reader and
  forward (the F5 case that previously showed `ROUTE_ENABLED` with zero samples).
- a local reader requesting `AUTOMATIC` liveliness with a finite lease matches the route
  writer created after it (derived lease ≤ requested) and observes liveliness with no
  router-side asserts (D42).
- an EXCLUSIVE-ownership writer (or a reader requesting `TRANSIENT` durability) produces
  a loud incompatible-QoS warning naming the policy; route status carries a useful reason.
- a later local reader with a tighter deadline is accommodated in place via `set_qos` —
  no entity recreation, no teardown cycle.

### Phase 6: Command/Status Control Loop

> **IMPLEMENTED — Phase 6 complete (6a = D57, 6b = D58).** Contract pinned (D46–D49). The command-handling state machine
> (`ENABLE_ROUTE`/`DISABLE_ROUTE`, duplicate-`command_id` replay, unknown-route reject,
> idempotent re-enable/re-disable, `ERROR` re-arm) is **already implemented and tested**
> in Phase 1 (`test_controller_phase1.cxx`) behind the `IStatusPublisher` seam; Phase 6 is
> real DDS wiring around that existing logic, not new state-machine design. Four forks
> resolved: D46 trims `ControllerJournalEventKind` to today's real event set (drops
> `ROUTE_DATA_READY`/`SHUTDOWN_REQUESTED`, adds `TOPIC_QOS_WARNING`); D47 filters commands
> by `target_node`/`target_router` via a ContentFilteredTopic (not an app-level check),
> reusing the D37/D43 quoting pattern; D48 pins command/ack QoS to
> `RELIABLE + VOLATILE + KEEP_LAST(16)`; D49 pins the journal writer to
> `RELIABLE + KEEP_LAST(256)` with an unlimited reliable send window (validated 7.7 —
> never blocks the controller thread) plus `RELIABLE_WRITER_CACHE_CHANGED_STATUS` backlog
> monitoring from day one. The debug-mode recorder toggle stays test-only for this phase —
> `router_main` is now wired to run real config-driven routes (D50), but that wiring has no
> command/status admin channel yet (Phase 6's own scope), so there is still no config
> surface to hook a real CLI/config flag into until this phase lands.
>
> **Sliced 6a/6b (D54, readiness pass 2026-07-14).** 6a is the command/ack/status loop —
> real-DDS wiring around the already-tested state machine; 6b is the controller journal —
> the only part needing a new seam (`IControllerJournal`, D55) through the green Phase 1
> controller. Evidence bullets are mapped 1:1 onto named Python e2e tests (D56).

**Slice 6a — command/status/ack control loop. IMPLEMENTED (D57).** Deliver:

- `CommandReader` on the LAN admin participant: a **ContentFilteredTopic** on
  `target_node`/`target_router` (D47), `RELIABLE + VOLATILE + KEEP_LAST(16)` (D48), posting
  accepted commands to the controller as `CommandReceived`.
- real ack writer — `DdsStatusPublisher::publish_ack` (today a no-op) becomes a live
  `RouterCommandAck` writer, `RELIABLE + VOLATILE + KEEP_LAST(16)` (D48).
- `ENABLE_ROUTE`, `DISABLE_ROUTE`, and duplicate-`command_id` handling (all already in the
  Phase 1 controller); aggregate `RouterStatus` publication after accepted changes;
  late-joiner catch-up comes from status durability (D26).
- New Python e2e test `router/test_e2e/test_router_admin_commands.py` + config
  `router/config/e2e_admin_commands.yaml` (one route `enabled: false`).

Evidence (6a, D56):

- **E1** disabled route appears in startup `RouterStatus` as `ROUTE_DISABLED` with no
  entities.
- **E2** `ENABLE_ROUTE` moves it to `ROUTE_WAITING_FOR_DISCOVERY` or `ROUTE_ENABLED`
  depending on discovery readiness; `ack.accepted`; `state_revision` bumps.
- **E3** `DISABLE_ROUTE` detaches read conditions, closes route entities (topics
  `TOPIC_IDLE`), forwarding stops; `ack.accepted`.
- **E4** duplicate `command_id` returns the original ack byte-for-byte and does not
  increment `state_revision`.
- **E-CFT** a command addressed to a different `target_node`/`target_router` never changes
  route state and draws no ack (the D47 CFT drops it before the callback).

**Slice 6b — controller journal. IMPLEMENTED (D58) — Phase 6 complete.** Deliver:

- `IControllerJournal` seam on `RouterController` (nullable; Phase 1 tests pass `nullptr`,
  D55) and its real implementation `ControllerJournalPublisher` on the LAN admin
  participant: `RELIABLE + KEEP_LAST(256)` with the reliable send window `LENGTH_UNLIMITED`
  (D49) so `write()` never blocks the controller thread; backlog watched via a
  `RELIABLE_WRITER_CACHE_CHANGED_STATUS` `StatusCondition` → `journal_falling_behind` log.
  The writer is always created with the command/status plumbing; the recorder reader exists
  only in debug mode (= a matched reader), so without one the journal produces no data-sample
  traffic beyond normal DDS endpoint discovery.
- New Python e2e test `router/test_e2e/test_controller_journal.py`.

Evidence (6b, D56):

- **E5** with the journal reader matched, every processed controller event records the input
  event, controller decision/outcome, pre/post `state_revision`, affected route/topic delta,
  and requested factory/status actions; with no reader matched, route behavior and status
  publication are unchanged. The D49 backlog signal is wired but not force-produced —
  `journal_falling_behind` is asserted **absent** under normal load; real backpressure
  verification is deferred to a future stress/soak phase.

### Phase 7: Platform Status And Events Replacement

> **Contract pinned (D59–D63); readiness pass 2026-07-14.** The original stub silently
> embedded two deferred library features and two never-wired mechanisms — none earning the
> table's "High" rating on their own. The readiness pass sliced the phase and validated its
> three Connext-hard assumptions up front (`ask_connext_question`, 2026-07-14): multi-XML
> `QosProvider` alias resolution, partition+CFT orthogonality, and discovery `type_name()`
> feeding `QosProvider::extensions().type()`. Confidence **high** with the slice order below.
> Delivering all four slices runs the real production `control-platform.yaml` (Milestone 2);
> afterward D50's blocker list collapses to only the team-partition items (now Phase 10).
> **PHASE 7 COMPLETE (D71, 2026-07-15):** 7a ✓ 7m ✓ 7b ✓ 7c ✓ 7d ✓ — the production
> config runs end to end as a two-process pair (`test_control_platform_full.py` /
> `test_detail_status_toggle.py`), and D50's blocker list is now Phase-8-only.
>
> **Pivot (D64, 2026-07-14) — read before implementing 7b/7c.** The design later shifted to
> **create-and-observe**: the router has no local type objects, learns each topic's type
> **inline from discovery** (`data.type`, rti_view model — spike-proven, `spikes/type_discovery/`),
> builds both legs from it, and lets **DDS own matching** (`matched_publications()`), retiring
> the controller's topic-name matching and the D39/D51 readiness gate. This **reshapes 7c**
> (type object from the wire, not a `type_name()`→catalog lookup) and makes **7b** nearly free
> (a partition mismatch is just a zero matched-count, not a false-green). Type acquisition +
> CFT-on-wire-type are spike-proven (`spikes/type_discovery/`, high), and the matching-authority
> refactor is now **behavior/API-proven** (`spikes/matched_endpoints/`, 4/4: partition mismatch
> → zero matches, false-green dissolved). The **readiness pass is complete (D66,
> 2026-07-15)**: the refactor is its own slice **7m**, landing before 7b/7c —
> StatusCondition-driven `TopicMatchChanged` events, matched counts + `match_reason` in
> status, creation gate and regression-teardown edge retired, matched-endpoint maps demoted
> to derivation/diagnosis input, first-learned-wins type authority per topic per process.
> C++ call surface compile-verified (`spikes/matched_endpoints/cpp_compile_check.cxx`).
> **Implement from D66**; D61/D62's original mechanisms are superseded where D66 says so.
>
> **7a delivered (D60/D65, 2026-07-14).** Implemented in the shipped router (not just the
> spike): `router_main` builds one `QosProvider` over `qos_libraries:` (via
> `rti::core::create_qos_provider_ex` — the real C++ construction path; D60's own snippet
> didn't compile, see D65), shared by `QosResolver` (endpoint `reader_qos:`/`writer_qos:`
> aliases) and `ParticipantRegistry` (participant `qos:`). A named alias fully specifies the
> endpoint and short-circuits D39/D42 auto-derivation and the D51 gate, as designed. A startup
> preflight resolves every declared alias — route `reader_qos`/`writer_qos` **and** participant
> `qos:` profiles — against the loaded XML before any DDS entity exists,
> failing fast on a bad profile name (the `lan_status_1hz` class of bug — already fixed in
> `control-platform.yaml`). Proof: `router/test_e2e/test_qos_alias_route.py` +
> `config/e2e_qos_alias.yaml` (E1/E2), real production QoS libs/aliases, one router process,
> route forwards end-to-end with the named profiles' actual resolved QoS on status. Full
> existing e2e suite + `ctest` re-ran clean. See D65 for the implementation decision.

Deliver (slice order per D66: 7a ✓ → 7m → 7b → 7c → 7d):

- **7a — QoS-alias XML resolution (D60).** The deferred D45 work. Parse `qos_profiles:` (the
  `wan_event → WAN_QOS_LIB::event_qos` indirection, currently unparsed); build a `QosProvider`
  over `qos_libraries` via `QosProviderParams::url_profile`; `QosResolver` resolves named
  aliases (`provider.datareader_qos("LIB::profile")` / `datawriter_qos(...)`); widen
  `is_resolvable_qos_alias` to any alias in the loaded map; apply participant `qos:` in
  `ParticipantRegistry`. A named alias fully specifies the endpoint and short-circuits the
  D39/D42 auto-derivation and the D51 readiness gate (`output_uses_auto_qos()` stays
  `.empty()`).
- **7m — Matching-authority refactor (D64/D66). DELIVERED (D67, 2026-07-15).**
  StatusCondition-driven `TopicMatchChanged` events from the route entities' own
  `SUBSCRIPTION_MATCHED`/`PUBLICATION_MATCHED` statuses (the D39/D45 `RouteTopicRuntime`
  callback pattern); `input_matched`/`output_matched` counts + `match_reason` in
  state/status/IDL; creation gate and the regression-teardown edge retired (zero matches is
  an observable status, entities persist); matched-endpoint maps demoted to
  derivation/diagnosis input; Phase 1 unit tests migrated to the new contract. Types still
  come from XML in this slice (create directly on enable); the wire-type wait lands in 7c.
  Proof: E-M (`test_create_and_observe.py`) + migrated e2e suite 15/15 twice + ctest 4/4;
  see D67 for two migration findings (a shipped test's dissolved false-green; journal
  consumers need KEEP_ALL).
- **7b — Publisher/Subscriber partition application (D61 as refined by D64/D66).
  DELIVERED (D69, 2026-07-15).** Applied `publisher_partition`/`subscriber_partition` to
  the per-build `Publisher`/`Subscriber` — rides 7m: a partition mismatch is the created
  entity's held-zero matched count + `match_reason`, **not** an incompatible-QoS event.
  Plus (user-directed) **runtime per-route partition change**: new `SET_ROUTE_PARTITION`
  command applies the embedded spec's endpoint partitions in place via pub/sub `set_qos`
  (D15 — automatic rematch, no rebuild) and re-mints the RouteView for future builds.
  Proof: `test_wan_partition.py` + `config/e2e_partition.yaml` (E3 + runtime retarget,
  QoS summaries byte-identical across the change); e2e 16/16 twice, ctest 4/4. See D69.
- **7c — Per-topic wire-type acquisition (D62 as reshaped by D64/D66). DELIVERED (D70,
  2026-07-15).** `router.type_name` retired; `DiscoveryDispatcher` reads each discovered
  endpoint's inline COMPLETE type object (`data->type()`) and posts `TypeResolved`;
  entities are created per topic when its type arrives (first-learned-wins per topic per
  process; learn-from-ANY-non-ignored-endpoint refines D64's learn-from-LAN — see D70).
  One `DynamicRouteFactory` serves all types. Proof: `test_platform_events.py` (E4) — two
  types from one process, one of them built programmatically in Python and present in NO
  XML; ignored same-node endpoints teach no types; e2e 17/17 twice, ctest 4/4. See D70.
- **7d — Full `control-platform.yaml` end-to-end. DELIVERED (D71, 2026-07-15; Phase 7
  COMPLETE).** Control-node + platform-node `router_main` pair on the verbatim production
  config; `control_command`, `platform_primary_status`, and `platform_events` all cross
  the WAN; `platform_detail_status` toggled via the Phase 6 `ENABLE_ROUTE` loop. Includes
  the D63 counter path: `RouteTopicRuntime::forwarded()` →
  `TopicRouteState.samples_forwarded` via a 1 s `RefreshCounters` tick (posted by
  `DrainThread`, pulled through `IEntityFactory::forwarded_count`) that republishes
  `RouterStatus` **without** bumping `state_revision` (the one sanctioned exception to
  D5, so counters are observable in steady state; never journaled; counters are entity
  facts cleared with the build). Proof: `test_control_platform_full.py` (E5/E6) +
  `test_detail_status_toggle.py` (E7); e2e 19/19 twice, ctest 4/4. See D71.

Evidence (mapped 1:1 to named Python e2e tests — D59, E3/E-M reshaped by D66):

- **E1/E2** (7a) a route using `wan_status`/`wan_event` aliases forwards; the participant
  `*_wan_udpv4_qos` profile is applied → `test_qos_alias_route.py` + `config/e2e_qos_alias.yaml`.
- **E-M** (7m) an enabled route on an empty domain reaches `TOPIC_FORWARDING` with both
  matched counts 0 and `match_reason` set, then matches and forwards when the peer appears
  (counts advance in status); full existing e2e suite stays green →
  `test_create_and_observe.py`.
- **E3** (7b) a PLATFORM-partitioned route matches only a PLATFORM-partitioned reader; a
  mismatched/empty-partition reader never matches (held-zero matched count + `match_reason`,
  no incompatible-QoS event — D66) → `test_wan_partition.py` + `config/e2e_partition.yaml`.
- **E4** (7c) `platform_events` forwards both `PlatformCommandAck` and `ContactReport` from a
  single `router_main` process → `test_platform_events.py`.
- **E5** (7d) full `control-platform.yaml` control+platform pair: all three enabled routes
  cross the WAN without Routing Service → `test_control_platform_full.py`.
- **E6** (7d/D63) `RouterStatus` shows per-route `samples_forwarded` advancing for an active
  route (asserted within E5).
- **E7** (7d) `ENABLE_ROUTE platform_detail_status` starts detail-status flow from the target
  only → `test_detail_status_toggle.py`.

### Phase 8: Presence & Health

> **PHASE 8 COMPLETE (D76, 2026-07-16).** Readiness pass D75 (presence spike PASSED, all
> checklist items pinned), implementation delivered per the lists below: `PresenceMonitor`
> shipped (heartbeat via the controller's 1 s `PresenceTick`, roster off the designed
> signals, LAN `ActRouterMeshStatus` aggregate), the D74 identifier flip landed
> (`participant_name`/`role_name`; `user_data` no longer set or read), IDL in
> `RouterAdminTypes.idl`, `presence_participant` config key (scalar or per-role map).
> Evidence: `test_presence_roster.py` (E-P1–E-P4 in one flow) + re-proofs in
> `test_discovery_smoke.py`/`test_same_node_ignore.py`; ctest 4/4, e2e 20/20 twice.
> **Scope split (D72):** this phase delivered presence *awareness* (heartbeat, roster,
> mesh aggregate). The presence-driven *reset action* (peer DEAD → `unregister_instance`
> downstream) lands in Phase 12, which owns the keyed fixture and per-peer instance
> bookkeeping the reset needs — unkeyed demo topics make the reset a no-op, so proving it
> here would be theater.

**Readiness pass — pin these as D-entries before code:**

1. ~~Decide D53~~ **Decided (D74, 2026-07-16): full replacement.** `participant_name`
   (`EntityName`) IS the router identifier — `name = "<node>/<router>"`,
   `role_name = "act.router"` as the detection sentinel; the D15 `user_data` tag retires.
   **The flip ships with this phase**: `DiscoveryDispatcher`'s tag extraction reads
   `participant_name()`, and the roster is built directly on the new field (no dual-field
   interim). The existing `test_same_node_ignore.py` re-proves the D15 ignore contract off
   the new field (probe poses via `participant_name` instead of `user_data`).
2. ~~Run the presence spike~~ **Run and PASSED (D75, 2026-07-16; `spikes/presence/`,
   stable 4/4).** DEAD via `RouterHealth` liveliness at 2.6–5.3 s, participant purge
   trailing at 11.1–15.8 s — the D16 ordering observed with 6–12 s margin (the dead peer's
   data writer was still matched at the moment of DEAD); purge then drove `NO_WRITERS` on
   the co-tested data topic; the LAN `ActRouterMeshStatus` aggregate tracked
   ALIVE→DEAD; STALE (deadline-missed, ~2 s) never escalated to DEAD over a hold past the
   liveliness lease. **All D74 identity checks green** (name/role readable at discovery,
   detection on `role_name` alone, roster join across kill/restart GUID change); the only
   residual is the manual Admin Console display check. Findings for the implementation:
   purge latency is lease-*order* not lease-exact (never a timing contract), and a real
   crash cascades STALE→DEAD (deadline fires before the lease) — normal, not an anomaly.
3. ~~Pin the demo numbers~~ **Pinned (D75):** heartbeat **1 s**, DEADLINE **2 s**,
   `RouterHealth` liveliness lease **3 s** (`AUTOMATIC`), WAN participant lease **10 s** /
   assert **3 s**. Any retune MUST keep `participant lease > liveliness lease` (D16);
   record both knobs next to each other.
4. ~~Pin the IDL landing spot~~ **Pinned (D75):** the presence types land in the existing
   `router/admin/RouterAdminTypes.idl` through the existing rtiddsgen path (no second IDL
   file/codegen run for one topic family).
5. ~~Pick the lease regime~~ **Pinned (D75):** moderate lease for the POC; the long-lease +
   GUID-supersede `ignore()` path stays deferred until rediscovery-churn suppression is a
   demonstrated need.

Deliver (all DELIVERED, D76):

- ✅ `PresenceMonitor` module in the `RouterInstance` composition: publishes this router's
  compact `RouterHealth` heartbeat on the WAN participant (QoS per the design doc:
  `RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1)`, finite `AUTOMATIC` liveliness, DEADLINE);
  subscribes to peers'; maintains the roster (`ALIVE`/`STALE`/`DEAD` off liveliness-lost,
  deadline-missed, `NOT_ALIVE_NO_WRITERS`).
- ✅ the heartbeat tick posted through the controller's event machinery (`PresenceTick`
  from `DrainThread` — a separate 1 s knob beside the D71 `refresh_period`; heartbeat
  writes never bump `state_revision` and are never journaled — telemetry, D5).
- ✅ LAN `ActRouterMeshStatus` writer: the aggregated connected-router list, republished on
  roster change (peer appears/disappears, presence transition, or a peer's `state_revision`
  advances).
- ✅ the D74 identifier flip: participants set `EntityName`
  (`name = "<node>/<router>"`, `role_name = "act.router"`); `DiscoveryDispatcher` detection
  keys on `role_name`; `user_data` is no longer set or read.
- ✅ roster correlation `router_id → current participant GUID` (off the heartbeat writer's
  publication data), exposed via `PresenceMonitor::participant_guid_of` for future
  consumers (Phase 9's rollup join, Phase 12's reset). **Superseded (D79/D81):** `router_id`
  retires (name-only identity, one router per node — the presence types re-key by name and
  gain `config_hash` per D80, an IDL/code rework committed before Phase 9), and peer
  attribution goes through the middleware discovery DB
  (`matched_*_participant_data`) rather than any roster join — `participant_guid_of` is
  dropped; the roster is purely the presence authority. **The rework landed 2026-07-17**
  (evidence re-proven as E-R1..E-R6, see the D79 addendum; the E-P bullets below record
  the original `router_id`-keyed run and stand as history).

Evidence (all in `test_presence_roster.py` unless noted; ran green twice with the full
suite, 20/20):

- **E-P1** two `router_main` processes see each other on `RouterHealth`; each publishes an
  `ActRouterMeshStatus` naming the other ALIVE with a fresh `last_seen_delta`.
- **E-P2** SIGKILL one router: the survivor marks it DEAD within the liveliness window and
  republishes the mesh aggregate; the WAN participants run the default 100 s participant
  lease, so a DEAD within seconds is liveliness-driven, demonstrably preceding purge (D16
  ordering; the purge backstop itself was measured in `spikes/presence/`); `/dev/shm`
  stays clean (UDPv4-only rule).
- **E-P3** a probe asserting liveliness but withholding heartbeats past the deadline is
  marked STALE, not DEAD, and no teardown of anything occurs (STALE is a policy flag, not
  an action).
- **E-P4** the returning router (restart, new GUID) re-enters the roster as ALIVE under the
  same `router_id` (fresh `heartbeat_seq` proves it is the new incarnation).
- **E-P5** `test_same_node_ignore.py` passes against the D74 field: a probe posing as a
  same-node router via `participant_name` (`role_name = "act.router"`) is ignored; a plain
  app writer (any display name, no sentinel `role_name`) still routes.
  `test_discovery_smoke.py` likewise detects the router by `role_name` + name prefix.

### Phase 9: Link-Metrics Capture

> **DELIVERED 2026-07-20 (D82).** `LinkStatsCollector` ships beside `PresenceMonitor`:
> per-peer reliable-protocol counters (registration enumeration via `collect_wan_stats` on
> route WAN legs + the `RouterHealth` bellwether) rolled up by peer NAME from the discovery
> DB, app-ack RTT on the dedicated `RouterLinkProbe` topic (the codebase's first, contained,
> `DataWriterListener`), published per-peer on the LAN `ActRouterLinkStats` + a log line, on
> a third config-fixed `DrainThread` tick (`link_stats_period_ms`), active only when presence
> is. Capture-only — no `state_revision` interaction, health *inference* still deferred to the
> netem correlation experiment. Evidence: `test_link_stats.py` (E1 counters advance +
> name-attributed, E2 `rediscovery_in_interval`, E3 RTT ~1 Hz, E4 no revision bump) +
> `test_link_stats_wire_frugal` (dumpcap/port-range: probe pair on the WAN, telemetry stream
> LAN-only); ctest 4/4, e2e 23/23. The IDL dropped the sketch's writer-global gauges that 7.7
> does not expose per-matched-endpoint (compile-verified; see D82).
>
> **Design pinned (D14/D18), capture-only by charter; readiness pass pinned (D81,
> 2026-07-17).** [link-health.md](link-health.md) is the contract: per-peer
> reliable-protocol counters + app-ack RTT, published raw on the LAN; **no thresholds, no
> classification, no health inference** — that stays gated on the netem correlation
> experiment (its own `spikes/` entry, and it gates only *inference*, never this phase).
> The full call surface was header-verified in the D81 pass (statuses, probe QoS knobs,
> app-ack listener). **Both implementation gates are CLEARED (2026-07-17):** (a) the
> D79/D80 wire rework landed (name-keyed presence types + `config_hash` — one wire
> change), and (b) `spikes/link_probe/` PASSED 3/3 — app-acks 10/10 per peer at 1 Hz with
> exact discovery-DB name attribution, ms-scale RTTs on `lo`, and the `KEEP_LAST(1)` ×
> `APPLICATION_AUTO` retention interaction is benign (`write()` never blocks behind a
> non-taking peer; replaced-never-taken samples produce NO app-ack, so no stale-ack
> filtering is needed; ack `sample_identity.sequence_number` is the 1-based RTPS seq —
> join send-times by that, not payload seq). The `ReadCondition` echo fallback is
> retired unused. Phase 9 is ready to implement.

Deliver:

- `LinkStatsCollector` beside `PresenceMonitor`: config-fixed 1 s poll (`link_stats_period`
  in YAML beside `heartbeat_period`, constant per run), reads matched-endpoint protocol
  statuses on every registered WAN writer/reader × peer, computes its own interval deltas
  from totals (never trusts `_change` fields; sole reader of these statuses; a negative
  delta = counter reset on rematch → re-baseline + `rediscovery_in_interval`, D81), rolls
  up per peer **name** resolved from the middleware discovery DB
  (`matched_*_participant_data(handle)` → `participant_name`, D81 — no roster join, no
  pre-heartbeat gap). Endpoints reach the collector by registration: a
  `collect_wan_stats` virtual on `RouteTopicRuntimeBase` (typed-only status getters) +
  `PresenceMonitor`'s `RouterHealth` pair (the mandatory idle-mesh bellwether); register
  at build / unregister at close on the controller strand. Collector active only when
  presence is active.
- `RouterLinkProbe` WAN topic pair (app-ack RTT): `VOLATILE`, no liveliness, fixed send
  window with per-sample piggyback HB, zero probe-reader ACK delay, ~1 Hz — the QoS shape in
  [link-health.md](link-health.md), never enabled on data routes. App-ack delivery is
  **listener-only** (D81): a minimal listener on the probe writer alone
  (`datawriter_application_acknowledgment` mask) pushes into a mutex-guarded accumulator
  the collector drains on its tick; listener reset before writer close (D31/D32
  discipline). The codebase's first — and only — listener.
- per-peer `ActRouterLinkStats` on the LAN (IDL sketch in the design doc; keys = name pair
  per D79, types land in `RouterAdminTypes.idl` per D75/D81) plus one structured log line
  per interval (`event=link_stats`), `rediscovery_in_interval` stamped from discovery
  events and from delta resets.
- no `state_revision` interaction (D5): link stats are a telemetry stream.

Evidence:

- under steady route traffic between two routers, `ActRouterLinkStats` samples advance
  (`pushed_samples`, `heartbeats_sent`, reader-side `samples_received`) with the peer
  correctly attributed by router **name** (→ `test_link_stats.py`).
- an interval containing a peer (re)match is stamped `rediscovery_in_interval` (the
  `TRANSIENT_LOCAL` replay burst is flagged, not misread as repair traffic).
- `rtt_count`/`rtt_*_us` populate from the probe at ~1 Hz; the probe pair is the only new
  WAN traffic the phase adds (checked with the `spikes/partition_retarget/`
  dumpcap/GuidPrefix attribution pattern; the D81 call surface also extends
  `spikes/matched_endpoints/cpp_compile_check.cxx`).
- polling the collector never disturbs other consumers (statuses read only by the
  collector; deltas self-computed) and never bumps `state_revision`.

### Phase 10: Team Partition Route

> **Needs its readiness pass most — the least-prepared numbered phase.** The gap between
> plan text and code is wide and verified (2026-07-16):
>
> - **flat routes are silently dropped**: `platform-team.yaml` writes routes as top-level
>   `input:`/`output:` with no `source`/`destination` role pair; `parse_route_config` only
>   handles the role-pair form and `continue`s past anything else
>   (`router/src/config/RouteConfigParser.cxx`). **Resolution flipped by D80:** the flat
>   form is *retired*, not legitimized — `platform-team.yaml`'s routes get rewritten in the
>   role-pair form inside the single system-wide config; the parser keeps one route shape
>   and upgrades the silent `continue` to a hard parse error.
> - **`participant_partition` is a struct field, not a feature**: it exists on
>   `ParticipantState` and is copied into status, but is neither parsed from YAML nor applied
>   at participant creation. **Superseded by D83 (2026-07-20):** the field becomes a
>   multi-valued set (protected node-identity entry + team + ad-hoc direct-peer joins), not
>   a scalar — see item 2 below.
> - **`inherit_participant`** (`platform-team.yaml`'s endpoint-partition sentinel) —
>   **retired by D73 (2026-07-16)**: team scope is the participant partition alone; the
>   config and configuration doc drop the sentinel when this phase lands.
> - **`SET_PARTICIPANT_PARTITION`** has been parsed-and-rejected since D7. **Retired before
>   shipping by D83**: replaced by `ADD_PARTICIPANT_PARTITION`/`REMOVE_PARTICIPANT_PARTITION`
>   (set-membership commands, not replace-all).
> - **A platform node keeps 2 separate WAN participants (D85, 2026-07-20, reverts D84's
>   single-shared-participant idea):** `platform_wan` (control↔platform) and `team_wan`
>   (platform↔platform) stay distinct — a merge was considered and rejected: it would trade
>   away today's SEDP-level discovery suppression between them (they currently share no
>   participant partition and so exchange zero endpoint discovery with each other) for a
>   topology simplification with no concrete resource constraint driving it.
>
> **What D64/D66/D69 already give this phase for free:** endpoint pub/sub partitions are
> applied and runtime-mutable (`SET_ROUTE_PARTITION`, `RouteTopicRuntime::set_partitions`,
> D69); in-place participant partition change via `set_qos` is validated 7.7 (D15
> side-finding); and the phase's hardest evidence — "moving one platform out of the team
> stops delivery after rediscovery" — is directly observable as matched-count transitions on
> live entities (D66/D67), no rebuild-and-infer. This is a much smaller phase than the old
> "recreate/reconcile" wording suggested.

**Readiness pass — pin these as D-entries before code:**

1. ~~Flat-route form semantics~~ **Decided (D80): the flat form is retired.** The old
   recommendation ("materialize unconditionally on any node that loads the config") was
   justified by the per-node `platform-team` config; under D80's single system-wide config
   a flat route would materialize on C2 too. Team routes are rewritten in the role-pair
   form (platform↔platform); the parser keeps exactly ONE route shape, and a route without
   a role pair becomes a hard parse error instead of a silent skip.
2. **`participant_partition` — decided as THE team-scope AND direct-peer-join mechanism
   (D73, generalized to multi-valued by D83):** parse it on `participants:` entries as a
   **set**, not a scalar — every WAN-facing participant's set always contains a **protected**
   default entry, `"${node.name}"` (D80 substitution; never removable by command), plus
   optional config-seeded team entries. Applied at participant creation as
   `DomainParticipantQos.partition` built from the full name set (D83 — header-confirmed
   `dds::core::policy::Partition` is `StringSeq`-valued and applies to
   `DomainParticipant`/Publisher/Subscriber alike, matching on non-empty set intersection,
   not exact equality). Mutated in place — one name added or removed per command — via
   `ADD_PARTICIPANT_PARTITION`/`REMOVE_PARTICIPANT_PARTITION` and participant `set_qos`
   (D15 — automatic rematch; same mechanism as D73, just per-name instead of whole-value).
   Removing the protected identity entry, or removing an absent name, is an accept-path
   reject, not a silent no-op. **WAN participant construction also sets
   `discovery_config.builtin_discovery_plugins = SPDP2 | SEDP` (D78)** — measured spikes
   replaced D73's MCP-sourced "announcement-paced, slower than SEDP" claim with real
   numbers: SPDP2 rematches ~11–20ms typically (vs. plain SPDP's 86–684ms) and uses less
   steady-state bandwidth than even default SPDP, but only after a probabilistic,
   undocumented-duration post-match settle window — the accept path and e2e must tolerate
   that window, not assume the fast path is available immediately after a peer is first
   discovered. LAN participant construction is unchanged (plain SPDP; D78 — LAN never does
   a partition retarget). Non-overlapping participant partitions suppress WAN endpoint
   discovery entirely — the phase e2e must confirm this empirically (out-of-team peers
   exchange no endpoint records). Recreate-on-change is retired even as a fallback; pinned
   fallbacks if the settle window proves unacceptable for the demo: **option D**
   (endpoint-only fan-out through the D69 `set_partitions` path, D73) or shortening
   `participant_liveliness_assert_period` on plain SPDP (D78 — works, but costs ~14x
   continuous bandwidth mesh-wide, forever).
3. ~~`inherit_participant` sentinel semantics~~ **Decided (D73): the sentinel is retired.**
   Route endpoints on `team_wan` use the default partition; the participant gate alone
   scopes the team (kept as designed — D84's merge that would have required reopening this
   was considered and reverted, D85). The parser hard-errors on unknown partition sentinels
   rather than ignoring them. The `SET_ROUTE_PARTITION`-precedence question is moot (no
   inherited endpoints exist).
4. **`ADD_PARTICIPANT_PARTITION`/`REMOVE_PARTICIPANT_PARTITION` accept path (D83)**:
   validate participant name + target name → reject if ADD would exceed the bounded
   sequence, if REMOVE targets the protected identity entry, or if REMOVE targets a name
   not currently present → apply in-place `set_qos` (full current set ± the one name) →
   ack. *Ack timing decision (unchanged from D73):* ack on successful apply, **not** on
   rematch — rematch is asynchronous and already observable via matched counts/`match_reason`
   (D66); an ack that waits on discovery would hang on the empty-team case that is this
   phase's own first evidence bullet. Idempotent same-value command (ADD of an
   already-present name, REMOVE already applied) follows D8.
5. **Evidence→test map** (D56 pattern): the bullets below land in
   `router/test_e2e/test_team_partition.py` against the single system-wide config (D80 —
   the team routes rewritten in role-pair form, `team_wan.participant_partition` defaulting
   to `{"${node.name}"}`), claiming the POC rows "Team disabled by default" and "Team
   assignment"; unit coverage for the parser's no-role-pair-is-an-error and
   sentinel-error rules extends `test_route_config`. **D83 adds one evidence case beyond
   team join/leave**: a direct peer tap — Platform_30 `ADD_PARTICIPANT_PARTITION`s
   Platform_31's own identity name (no shared team) and `PlatformData` starts crossing
   between exactly those two nodes; `REMOVE` reverses it. Confirm or adjust test names when
   pinning.

Deliver:

- `platform_team_to_wan` and `wan_team_to_platform` concrete routes parsed from the
  system-wide config's role-pair form (D80 — the old flat `platform-team.yaml` shape is
  retired).
- `team_wan.participant_partition` parsed as a set (protected `"${node.name}"` identity
  entry + optional config-seeded team entries), applied at creation, runtime-mutable via the
  `ADD_PARTICIPANT_PARTITION`/`REMOVE_PARTICIPANT_PARTITION` accept path (replacing the D7
  reject) — the team-scope AND direct-peer-join mechanism (D73, generalized by D83).
  `team_wan` (and other WAN participants) built with `SPDP2 | SEDP`; LAN participants
  unchanged (D78). `team_wan` stays a participant separate from `platform_wan` (D85 —
  reverted the D84 single-shared-participant idea).
- `platform-team.yaml` and [configuration.md](configuration.md) updated: the
  `inherit_participant` sentinel removed (D73); unknown partition sentinels are a parse
  error.

Evidence:

- Platform_30 and Platform_31 (two `platform-team` `router_main`s) do not exchange
  `PlatformData` with node-specific partitions — held-zero matched counts + `match_reason`,
  not silence-and-hope (D66) — **and exchange no WAN endpoint discovery** (the D73
  suppression claim confirmed empirically, e.g. no peer endpoint records in the discovery
  log).
- after `ADD_PARTICIPANT_PARTITION team_wan=TEAM_A` to both, `PlatformData` crosses; acks
  return on apply; matched counts advance after rediscovery settles — allow for SPDP2's
  probabilistic post-match settle window (D78) rather than asserting a fixed bound.
- `REMOVE_PARTICIPANT_PARTITION team_wan=TEAM_A` on one platform stops delivery: matched
  counts regress to zero on live entities, forwarding stops, no entities are torn down.
- a direct peer tap without a shared team: `ADD_PARTICIPANT_PARTITION team_wan=Platform_31`
  on Platform_30 (naming Platform_31's own protected identity entry) makes `PlatformData`
  cross between exactly those two nodes; `REMOVE_PARTICIPANT_PARTITION` reverses it (D83).
- a duplicate `ADD`/`REMOVE` (name already present / already absent) returns an idempotent
  accept with no revision bump (D8); `REMOVE` targeting the protected `"${node.name}"` entry
  or a name not currently present is rejected, not silently accepted.

### Phase 11: Serialized-CDR Fast Path

> **Opt-in, eligibility-gated, off the critical path (Tenet 7)** — the POC is not judged on
> this optimization (D72 removed it from the acceptance criteria). Implement only if a
> throughput need is demonstrated on a bulk pass-through route. Eligibility: no reader-side
> content filter, no lifecycle/instance-state needs (both require field access). The open
> question below (eligibility × keyed lifecycle) must be answered in its readiness pass if
> this phase ever goes active.

Deliver:

- `serialized_cdr` forwarding mode for compatible pass-through routes.
- fallback to `dynamic_data` when serialized forwarding cannot be used, with the reason
  logged and visible in status.
- status/logging that identify which forwarding path each route uses.

Evidence:

- a generated smoke topic forwards without app-level field materialization in the router.
- status reports forwarding mode; an ineligible route (CFT or lifecycle) demonstrably
  falls back with a labeled reason.

### Phase 12: Keyed Lifecycle Mirroring + Presence-Driven Reset

> **High confidence** — the mechanism was proven by `spikes/isc_recovery/` (meta-sample
> mirroring via `key_value()` recovery; this is *not* ISC — no read-retain, no
> re-assert-on-ALIVE; Tenet 2). Two prerequisites the readiness pass must supply:
> **(a) a genuinely keyed route fixture** — ACT payload types are unkeyed *for demo only*
> (Tenet 8), and the reference-only data model (D35) must add a keyed type deliberately
> (the Phase 7c programmatic-type pattern in `test_platform_events.py` shows how to author
> one in the test itself); **(b) the Phase 8 roster**, which triggers the reset path.

Deliver:

- one keyed `dynamic_data` lifecycle route with `mirror_instance_state: true`: dispose and
  unregister forwarded through `key_value()` recovery on the input reader; key cache by
  reader instance handle; `lifecycle_events_forwarded` counter finally advances (it has
  been reserved-zero since D71).
- unrecoverable keys logged and counted, never silently dropped.
- **presence-driven reset** (Tenet 5, deferred from Phase 8): per-peer
  `peer name → {instance keys}` bookkeeping in the mirror (name-only identity, D79); on
  roster DEAD (never STALE),
  `unregister_instance()` that peer's instances on the output writer → downstream
  `NOT_ALIVE_NO_WRITERS`; reversible on peer return (re-write → `ALIVE`).

Evidence:

- downstream reader observes matching `NOT_ALIVE_DISPOSED` / `NOT_ALIVE_NO_WRITERS`
  transitions for app-driven dispose/unregister (POC row "Meta-sample lifecycle")
  → `test_lifecycle_mirror.py`.
- a peer router declared DEAD unregisters exactly that peer's instances downstream; a
  returning peer re-writes and instances recover as data (POC row "Presence reset")
  → `test_presence_reset.py`.
- STALE never triggers the reset; participant purge remains the trailing backstop.
- unrecoverable-key samples are counted and logged, and forwarding of valid data is
  unaffected.

### Phase 13: Harness Replacement

> Inherently last. Four items land here from earlier phases and belong on its checklist:
>
> 1. **Transport-override decision** (progress-review F4): `ParticipantRegistry` imposes
>    UDPv4-only *after* loading the named profile — correct for this VM, silent for a real
>    deployment. Pin a D-entry: config-gate the override (dev-VM safety flag) or log loudly
>    when the loaded profile's transport mask differs.
> 2. **Env-var ownership** (F5): the 14 templated QoS-lib env vars are owned by
>    `conftest.set_wan_qos_env()` in tests; the container start scripts must own and
>    validate the set in deployment, documented in one deployment-facing place.
> 3. **Startup sequencing**: the D52 disabled-startup ordering (participants created
>    disabled → conditions attached → `aws.start()` → `enable_all()`) under container
>    orchestration.
> 4. **Re-evaluate the stop criterion** ("stop if replacing RS requires reimplementing broad
>    RS features") — explicitly, now that Milestone 2 is closed.
> 5. **Config distribution** (D80): how the single system-wide config reaches every node is
>    deployment's job and lands here — the router only reports what it loaded
>    (`config_hash` in the heartbeat); drift detection is C2's view of mismatched hashes.

Deliver:

- container/start scripts for one platform running its single router instance (one router
  per node, D79; one system-wide config, D80).
- control node using the same system-wide config, materializing the control side.
- Routing Service removed from the POC node stack for the tested routes.

Evidence:

- ACT quick-start works without Routing Service for one control and one platform.
- two-platform team scenario works for command, status, events, and `PlatformData`.

### Phase 14: Command Relay (Multi-Hop Gossip Delivery)

> **Proposed, not yet scheduled.** Motivated by a sender (e.g. C2) having no direct/healthy
> link to a target router while intermediate routers do — today's `RouterCommand` delivery
> is a **ContentFilteredTopic** (`target_node = %0 AND target_router = %1`,
> [command-status.md](command-status.md)) with no relay path. Design and rationale pinned in
> D86; full writeup [command-relay.md](command-relay.md). Readiness items before this phase
> can start: (a) confirm no phase ahead of it depends on relay delivery (none currently do —
> every phase through 13 assumes direct command delivery); (b) a small spike building the
> `RouterHealth`/`peers_seen` graph-assembly + path computation standalone, before wiring it
> into `RouterController`, mirroring how other phases de-risk mechanism before shipping it.

Deliver:

- graph assembly from `RouterHealth.peers_seen` (D77) at the sender, kept live off heartbeats
  — no new WAN traffic, reuses data already published.
- path computation (plain reachability to start) from the assembled graph to
  `target_node`/`target_router`, treating edges as directed (no symmetry assumption, per
  [presence-and-health.md](presence-and-health.md)'s asymmetric-edge case).
- new `RouterCommand` fields: `relay_path` (ordered router names), `relay_hop_index`,
  `relay_requested`, `hop_count` — additive, direct delivery unchanged.
- relay-forward logic in `RouterController`: forward when addressed as next hop
  (`relay_path`) or in flood mode (`hop_count > 0`), deduped by `command_id`.
- sender retry ladder reusing the existing `RouterCommandAck`/`command_id` contract
  unchanged: direct → graph-directed relay → flood, escalating on unacked retries. Only the
  target router acks; relay hops are transparent.
- relay-hop diagnostics via `ActRouterControllerJournal` (command_id, from/to, hop position)
  for path traceability after the fact.

Evidence:

- three-router chain (sender → relay → target, no direct sender↔target link): a command
  addressed to the target arrives and is acked, having actually traveled through the relay
  (confirmed via journal logging, not inferred).
- graph-directed relay is attempted before flood when the graph shows a path; flood only
  triggers when the graph has no path or the graph-directed attempt times out unacked.
- a duplicate command (same `command_id` re-delivered, e.g. via retry racing a slow ack)
  is not double-executed at the target and is not infinitely re-forwarded by relays.
- a command sent when a direct sender↔target link exists behaves identically to today's
  unicast delivery — this phase adds no regression to the direct-delivery path.

## Confidence-Increasing Investigations

These investigations should be short spikes, not new architecture phases. Each one should
produce a small executable, test, or written API note that either raises the slice confidence
or narrows the fallback path.

| Slice | Current confidence | Investigation | Confidence increases if | Fallback if not |
|---|---|---|---|---|
| Phase 2: discovery dispatcher | High — **resolved (D12/D13)** | ~~Compare built-in publication/subscription readers vs Connext discovery listeners~~ Decided: builtin readers + `ReadCondition`s on the `AsyncWaitSet`; endpoint fields validated against 7.7; LAN `request_types_filter` required for type learning | topic name, registered type name/type id, partition, and QoS summaries are available without fragile internal assumptions | use the API with the most stable metadata even if it is less elegant |
| Phase 3: dynamic entity lifecycle | High — **resolved (D31/D32)** | ~~Write a tiny program that creates a reader/writer after discovery, attaches a `ReadCondition` to an `AsyncWaitSet`, then detaches and closes repeatedly~~ Decided: `detach_condition()` is a documented **blocking barrier** (in-flight handler has returned on success); per-condition dispatch is serialized (never call `unlock_condition`); pinned close order detach→close-cond→close-reader→close-writer on the controller strand | repeated attach/detach/close cycles do not race, leak, or callback after close | serialize all attach/detach/close on the controller strand and avoid aggressive rebuilds |
| Phase 5: LAN `auto` QoS | High — **resolved (D39), shipped (D45)** | ~~Capture QoS from actual ACT LAN endpoints and reduce it to the minimum compatible policy set~~ Decided: no reader-side derivation at all — weakest-request input readers match every writer by RxO construction; writer derives deadline (mutable in place) and, per D42, liveliness kind+lease at creation (fixed TL offer is already the durability auto-match); immutability table, ownership-equality RxO, liveliness RxO/assert mechanics, and incompatible-QoS status detection validated against 7.7 (the data model is reference-only per D35, so "actual ACT endpoints" was stale — the phase tests against router-authored endpoints with deliberately heterogeneous QoS) | a small deterministic subset of policies is enough for `ControlCommand`, `PlatformStatus`, and `PlatformData` | require explicit LAN QoS aliases for first POC routes and keep `auto` as POC-plus |
| **Phase 8: presence mechanism** | High — **spike RUN and PASSED (D75, 2026-07-16), stable 4/4** | ~~`spikes/presence/` (Python driver): N `RouterHealth` publishers on WAN participants; SIGKILL one → peers mark DEAD inside the liveliness window and **before** participant purge (D16 ordering observed); LAN `ActRouterMeshStatus` aggregate updates; purge then drives `NO_WRITERS` on a co-tested data topic; force STALE via `assert_liveliness` + withheld heartbeats~~ Observed: DEAD 2.6–5.3 s (3 s lease), purge trailing 11.1–15.8 s (10 s lease, lease-*order* not lease-exact), STALE ~2 s never escalating, mesh aggregate tracking — see `spikes/presence/README.md` | ~~DEAD/STALE/purge arrive in the designed order with the designed latencies~~ **They did, every run, with 6–12 s of D16 margin** | (not needed) |
| Phase 10: team partition changes | Medium-high — mechanism decided (D73: participant partition only; sentinel retired); in-place `set_qos` validated (D15) | No separate spike needed: remaining readiness items are the role-pair rewrite of the team routes in the system-wide config (D80 — flat form retired) + the accept path; the phase e2e must empirically confirm the D73 SEDP-suppression claim and observe rematch as matched-count transitions (D66/D67) | rediscovery and delivery are predictable after node-specific partition to `TEAM_A` and back — including announcement-paced join latency acceptable for the demo | pinned fallback (D73): endpoint-only fan-out via the shipped D69 `set_partitions` path; same evidence tests, different mechanism |
| Phase 11: serialized-CDR fast path | Medium — **investigate only if the phase goes active** (opt-in per Tenet 7) | Build a standalone Connext 7.7 C++ pass-through for one generated type using DynamicData serialized-buffer APIs | the reader can access the CDR buffer and the writer can publish it without field materialization | keep `dynamic_data` (the default) and drop the optimization |
| Phase 12: keyed lifecycle mirroring | **High** — dispose/no-writers propagation and `key_value()` recovery proven by `spikes/isc_recovery/` | Residual check only: `key_value()` recovery on a **DynamicData** route in the shipped relay (the spike used its own harness), against the deliberately keyed fixture the readiness pass authors (Tenet 8) | downstream reader observes matching instance states and keys recover reliably in the shipped runtime | require generated-type route runtimes for lifecycle-sensitive topics |
| Phase 13: harness replacement | Medium-high | Replace Routing Service for one non-critical ACT route in container startup while leaving the rest unchanged | startup ordering, peer discovery, logs, and cleanup are understandable in compose/scripts | run the router sidecar in observe-only/status-only mode before removing Routing Service |
| Phase 9 → health *inference* (not capture) | Capture design pinned (D14/D18); metric *meaning* unproven | netem correlation-experiment spike (own `spikes/` entry, Python driver): sweep delay/jitter/loss/rate/blackout one axis at a time against recorded `ActRouterLinkStats` + the ground-truth schedule; also empirically verify per-locator counter attribution and app-ack RTT probe behavior ([link-health.md](link-health.md)) | specific metrics demonstrably track specific impairments with usable lag and noise floor → a follow-up decision pins thresholds/classification feeding the `RouterHealth` rollup | metrics stay raw telemetry; no health inference ships; presence remains the only health authority |
| Cross-cutting: router identifier scheme — **decided (D74), spike-verified (D75)** | Decided and verified: full replacement — `name = "<node>/<router>"`, detection on `role_name == "act.router"`; `user_data` retires with the Phase 8 flip | ~~Verification rides the presence spike~~ Done (D75): `participant_name()` readable off the builtin sample at discovery, detection on `role_name` alone excludes a display-named app, roster GUID join across kill/restart — all green; **residuals:** the manual Admin Console display check, and `test_same_node_ignore.py` re-proving the D15 ignore contract off the new field post-flip (a Phase 8 evidence item) | loop-safety and rollup joins work identically off the new field in the shipped router | documented-and-accepted residual only: an app deliberately claiming `role_name = "act.router"` is impersonation and gets ignored by same-node routers |

Investigation order (updated at D72 — the original hardest-first list is done: attach/detach
D31/D32, auto QoS D39/D45, partition mutability D15, plus the Phase 7 spikes): ~~(1) the
presence spike~~ **done (D75)**; ~~(2) the D53 prototype~~ **done — the identity checks rode
the presence spike (D74/D75)**; ~~(3) `spikes/link_probe/`~~ **done (2026-07-17, PASSED
3/3 — Phase 9's gate cleared, see the Phase 9 banner)**; next:
**the netem correlation experiment** any time after Phase 9 ships capture (it gates
only inference); then **the Phase 11/12 investigations only when those phases go active** —
Phase 11 is opt-in, and Phase 12's residual check rides the phase itself. Phase 10 needs
decisions, not a spike (its remaining items are D80's config rewrite + the accept path).

## Recommended First Milestone

**Milestone 1 — done.** Stopped after Phase 3: a real Connext executable with
controller-owned state, discovery, dynamic endpoint creation, `AsyncWaitSet` attachment, and
one forwarded topic, without ACT harness complexity.

**Milestone 2 — done (2026-07-15/16, D71).** Phases 4 through 7: role-aware control/platform
routes plus command and status behavior. The verbatim production `control-platform.yaml`
runs as a two-process pair without Routing Service (`test_control_platform_full.py`,
`test_detail_status_toggle.py`; e2e suite 19/19). The RS-replacement stop criterion was due
for explicit re-evaluation at this close (carried onto Phase 13's checklist).

**Milestone 3 — Phases 8 through 13**, in the numbered order: Presence/Health (8) →
Link-Metrics Capture (9) → Team partitions (10) → then 12 (lifecycle + presence reset) and
13 (harness replacement), with 11 (serialized CDR) opt-in and off the critical path. 8
before 10 is deliberate: Phase 10's partition-change evidence benefits from the roster
existing, and the presence-driven reset (via 12) is on the acceptance-criteria path. Each of
8 and 10 starts with its readiness pass per the execution protocol.

## Confidence Notes

- Delivered and test-verified (Phases 0–8): controller state, discovery, explicit- and
  alias-QoS forwarding, DynamicData + wire-learned types, create-and-observe matching,
  command/status/journal loop, the full production `control-platform.yaml` pair, and
  presence/health (Phase 8, D76 — heartbeat/roster/mesh + the D74 identity flip).
- High confidence, not yet built: keyed lifecycle mirroring (Phase 12 — mechanism proven
  by `spikes/isc_recovery/`; the old "Medium" rating predates that spike and the tenets
  reframe).
- Medium-high confidence: team partition route (Phase 10 — mechanics validated, but the
  config surface needs its readiness pass), link-metrics capture (Phase 9 — API surface
  validated, unspiked in the shipped process), ACT harness replacement (Phase 13).
- Medium confidence: serialized-CDR buffer forwarding (Phase 11) — exact Connext 7.7 Modern
  C++ buffer-API ergonomics; deliberately opt-in and off the critical path (Tenet 7).

Do not block any phase on the Phase 11 optimization. `dynamic_data` is the default
forwarding mode everywhere (Tenet 7).

## POC Tests

| Test | Phase / status | Setup | Pass condition |
|---|---|---|---|
| Config smoke | control-platform **done** (7d); team routes → Phase 10 | Load the single system-wide config (D80) on one platform node — one router process per node (D79) | the router instance materializes exactly its role's routes; disabled routes appear in status and stay closed |
| Control command path | **Done** (7d, `test_control_platform_full.py`) | Control sim publishes `ControlCommand` to Platform_30 | Platform_30 receives only commands addressed to it |
| Platform status path | **Done** (7d, `test_control_platform_full.py`) | Platform_30 publishes `PlatformStatus` | Control receives status through router |
| Events path | **Done** (7c/7d, `test_platform_events.py` + E5) | Platform publishes `PlatformCommandAck` and `ContactReport` | Control receives both topics |
| Team disabled by default | Phase 10 | Platform_30 and Platform_31 start with unique `team_wan.participant_partition` values | no platform-to-platform `PlatformData` crosses (held-zero matched counts, D66) |
| Team assignment | Phase 10 | Send `ADD_PARTICIPANT_PARTITION team_wan=TEAM_A` to both platform routers | `PlatformData` crosses between both platforms |
| Direct peer tap | Phase 10 (D83) | Send `ADD_PARTICIPANT_PARTITION team_wan=Platform_31` to Platform_30 (no shared team) | `PlatformData` crosses between exactly Platform_30 and Platform_31 |
| Detail status toggle | **Done** (7d, `test_detail_status_toggle.py`) | Send `ENABLE_ROUTE platform_detail_status` | control starts receiving detail status from target only; router publishes updated status |
| Router command/status | `ENABLE_ROUTE` **done** (Phase 6); `ADD_PARTICIPANT_PARTITION`/`REMOVE_PARTICIPANT_PARTITION` → Phase 10 | Send `ADD_PARTICIPANT_PARTITION`, `REMOVE_PARTICIPANT_PARTITION`, or `ENABLE_ROUTE` | command ack is returned and status topic reports new state revision with the full route table |
| Serialized forwarding smoke | Phase 11 — **optional/stretch** (Tenet 7; not on the acceptance path) | Route `PlatformStatus` with `forwarding_mode: serialized_cdr` | sample arrives downstream without app-level field materialization in the router |
| Meta-sample lifecycle | Phase 12 | App disposes/unregisters a keyed instance | downstream sees matching `NOT_ALIVE_DISPOSED` / `NOT_ALIVE_NO_WRITERS` (meta-mirror, not ISC — no reconnect recovery) |
| Presence reset | Phases 8 + 12 (roster in 8; reset action in 12) | A peer router declared `DEAD` on `RouterHealth` | relay unregisters that peer's instances downstream; a returning peer re-writes and they recover as data |

## Acceptance Criteria

The exercise is useful if:

- ACT quick-start works without Routing Service for one control and one platform.
- Multi-platform team assignment works without Routing Service for two platforms.
- Detail status can be enabled and disabled over the router control topic. *(Met — 7d, E7.)*
- Each router publishes a status sample after accepted command changes. *(Met — Phase 6.)*
- At least one keyed lifecycle route mirrors dispose and no-writers transitions.
- The YAML is short enough to explain in a demo and maps directly to ACT routes.
- Failure modes are explicit: bad route commands are rejected and acknowledged, not silent.

Stretch (explicitly **not** required for the POC to be judged useful — Tenet 7 / D72):

- Pass-through routes can use the Connext 7.7 C++ serialized-CDR forwarding mode (Phase 11,
  opt-in).

Stop the POC if replacing Routing Service requires reimplementing broad RS features before
the ACT flows work. The point is to find the narrow ACT-specific route engine, not to clone
Routing Service.

## Open Questions

- ~~What is the smallest Connext 7.7 Modern C++ API surface needed for TypeLookup-driven
  `DynamicType` creation?~~ **Resolved (D64/D70):** types are read inline from SEDP
  discovery (`data->type()`), no TypeLookup dance — shipped in 7c. The serialized-CDR
  forwarding API surface remains open, scoped to Phase 11 only.
- For lifecycle routes, is key recovery practical in `serialized_cdr` mode, or should those
  routes explicitly use `dynamic_data` / `generated_type` mode? (Phase 11 × Phase 12
  eligibility interaction — answer in Phase 11's readiness pass if it goes active.)
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
  participant discovery). In-place change is the premise for the team-partition phase (now
  Phase 10); recreate is the fallback.
- Should route definitions eventually support multi-output fanout, or should ACT keep one
  route per input/output pair for clarity?
- ~~Should the D15 `user_data` router-tag mechanism be replaced by `participant_name`
  (ENTITY_NAME) as the router identifier?~~ **Resolved (D74, 2026-07-16): full
  replacement.** `name = "<node>/<router>"`, detection keyed on
  `role_name == "act.router"`; SHIPPED with Phase 8 (spike verification rode the presence
  spike and PASSED — D75; flip delivered in D76). See D53 for the original scoping and
  D74 for the decision.

## Relationship To Current Roadmap

This exercise is more aggressive than the current Phase 8 plan (the ACT program roadmap's
phase numbering, unrelated to this document's phases). Current plan:

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
