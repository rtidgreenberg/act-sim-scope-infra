# Design Decisions Log

Running log of design decisions for the C++ Dynamic DDS Router. Foundational decisions
(custom relay vs. RS, ISC out of scope, presence model, admin transport, forwarding-mode
defaults) live in [Thesis & Tenets](thesis-and-tenets.md) and are not repeated here — this
log starts where the tenets stop: per-phase contracts and API/semantic choices made during
implementation. Newest decisions at the bottom. Where another doc conflicts with a decision
here, the decision wins until that doc is reconciled (reconciliation edits are listed per
decision).

Format: each decision has an ID (`D<n>`), a date, a status, the decision, the rationale, and
the docs it changes.

---

## D1 — Route state is published as two fields: `operational_state` + `discovery_state` (2026-07-07, accepted)

**Context.** [code-architecture.md](code-architecture.md) gave `RouteState` both
`operational_state` and `discovery_state`; the IDL in
[command-status.md](command-status.md) published only a single
`RouterRouteOperationalState`. Ambiguous whether discovery was a second published field or
internal bookkeeping.

**Decision.** Publish **both** fields in `RouterRouteStatus`:

- `operational_state` (`RouterRouteOperationalState`) — the route **lifecycle**: what the
  controller is doing with the route (disabled / waiting / resolving / enabled / degraded /
  error). Carries the "was forwarding" memory (`ROUTE_DEGRADED`).
- `discovery_state` (`RouterRouteDiscoveryState`, new enum) — a **pure function of current
  discovery facts**, no memory:

  ```idl
  enum RouterRouteDiscoveryState {
      DISCOVERY_NONE,      // required input writer not yet discovered
      DISCOVERY_PARTIAL,   // input writer seen, but type unresolved, QoS unresolved,
                           // or auto-QoS output reader missing
      DISCOVERY_READY      // all prerequisites for entity creation available
  };
  ```

Internally, `RouteState` keeps the raw **discovery facts** (`input_writer_seen`,
`output_reader_seen`, `type_resolved`, `qos_resolved`); `discovery_state` is rolled up from
them by a pure function, and `operational_state` transitions are **guarded** by
`discovery_state` (see D2 table). One source of truth per field: facts → discovery rollup;
lifecycle memory lives only in `operational_state`. So "enabled but output reader just
vanished" is visible as `ROUTE_DEGRADED` + `DISCOVERY_PARTIAL`, and the two fields can never
tell contradictory stories about the same fact.

**Docs changed.** `command-status.md` (IDL: new enum + field), `code-architecture.md`
(`RouteState` facts + rollup comment).

**Amended by D11** — discovery facts are tracked **per topic**; the route-level
`discovery_state` becomes the best (max) topic rollup.

**Amended by D20** — the raw facts are derived from per-topic **matched-endpoint sets**
(seen ⇔ set non-empty), not stored booleans.

---

## D2 — Operational-state transition table; `ROUTE_ERROR` is sticky until command re-arm (2026-07-07, accepted)

**Context.** The six states were listed in [command-status.md](command-status.md) but the
edges were prose; `ROUTE_ERROR` had no defined exit. Phase 1's tests need deterministic
transitions.

**Decision.** `ROUTE_ERROR` is **sticky**: nothing leaves it except an explicit operator
command (`ENABLE_ROUTE`, `UPDATE_ROUTE`, or `DISABLE_ROUTE`). No auto-retry on discovery
change, no backoff timer — deterministic, no retry storms, and errors stay visible in status
until acknowledged. This matches the existing wording "cannot activate without config, type,
QoS, or entity changes": those changes arrive as commands.

The authoritative transition table (`D` = `discovery_state`, per D1):

| From | Event (guard) | To | Side effects |
|---|---|---|---|
| `DISABLED` | `ENABLE_ROUTE` (D ≠ `READY`) | `WAITING_FOR_DISCOVERY` | ack; revision++ |
| `DISABLED` | `ENABLE_ROUTE` (D = `READY`) | `RESOLVING` | ack; revision++; begin resolve/entity creation |
| `WAITING_FOR_DISCOVERY` | discovery facts change (D → `READY`) | `RESOLVING` | revision++ |
| `RESOLVING` | resolve + entity creation succeed | `ENABLED` | attach `ReadCondition`; revision++ |
| `RESOLVING` | resolve or entity creation fails | `ERROR` | set `last_error`; revision++ |
| `ENABLED` | required endpoint lost / fatal write path failure | `DEGRADED` | detach condition; begin teardown; set `last_error`; revision++ |
| `DEGRADED` | teardown complete (D = `READY`) | `RESOLVING` | rebuild entities; revision++ |
| `DEGRADED` | teardown complete (D ≠ `READY`) | `WAITING_FOR_DISCOVERY` | revision++ |
| any except `DISABLED` | `DISABLE_ROUTE` | `DISABLED` | teardown; ack; revision++ |
| `ERROR` | `ENABLE_ROUTE` / `UPDATE_ROUTE` | `WAITING_FOR_DISCOVERY` or `RESOLVING` per D | clear `last_error`; ack; revision++ (**the only re-arm path**) |
| any | fatal `RouteEntityError` | `ERROR` | set `last_error`; revision++ |
| any | duplicate `command_id` | (no change) | return cached ack; **no** revision bump (D4) |

Notes:

- `DEGRADED` vs `WAITING_FOR_DISCOVERY`: `DEGRADED` means *was forwarding, entities (partially)
  exist, teardown/rebuild in progress*; `WAITING_FOR_DISCOVERY` means *quiescent, no route
  entities, waiting on the world*. Teardown completion is itself a controller event, so
  `DEGRADED` is an observable state (a snapshot exists for it), even in Phase 1 where the fake
  `EntityFactory` completes teardown immediately.
- Discovery-facts changes that do **not** cross a rollup boundary (e.g. `PARTIAL` with a
  different missing prerequisite) update facts but not `discovery_state`; no revision bump.

**Docs changed.** `command-status.md` (Route State Machine section points here; `ROUTE_ERROR`
description gains stickiness).

**Amended by D8** — adds the `RESOLVING` discovery-regression row and the
redundant-command (new `command_id`) row.

**Amended by D11** — `operational_state` becomes a derivation over per-topic states;
the table's discovery guards read over the topic set (`D = READY` ⇒ "≥ 1 topic READY").

**Amended by D21** — the completion events are named (`TopicEntitiesReady`,
`TopicTeardownComplete`) and `RouteEntityError` gains per-topic scope.

---

## D3 — Phase 1 includes the `ControllerEvent` queue; DDS is faked behind interfaces (2026-07-07, accepted)

**Context.** Phase 1 has no DDS, but the architecture's core rule
([code-architecture.md](code-architecture.md) Controller Event Model) is that everything
reaches the controller as typed events on one strand. Unclear whether Phase 1 builds that or
drives the state machine with direct method calls.

**Decision.** The event queue **is the Phase 1 deliverable's spine**: Phase 1 builds the
`ControllerEvent` types and the single-strand drain loop, with `DiscoveryDispatcher`,
`EntityFactory`, and `StatusPublisher` injected behind interfaces and **faked** in tests.
Tests post synthetic events (`CommandReceived`, `PublicationDiscovered`, `EndpointLost`,
`RouteEntityError`, …) and assert on the snapshot sequence. This proves the actual
concurrency architecture rather than a stand-in that gets rewritten in Phase 2/3.

Honesty note on coverage: Phase 1 *genuinely* proves the state machine, snapshotting,
revisioning, and idempotency; transitions that depend on real DDS behavior
(`RESOLVING → ENABLED` timing, real endpoint loss) are **simulated** by fakes that succeed or
fail on demand. Phase 2/3 replace the fakes; the transition table (D2) is the contract both
sides implement.

**Docs changed.** `implementation-plan.md` (Phase 1 deliverables/evidence).

---

## D4 — Command history caches acks for accepted **and** rejected commands; FIFO bound 256 (2026-07-07, accepted)

**Context.** "Bounded command history for duplicate `command_id` handling" left open what is
recorded, the bound, the dedup key, and eviction behavior.

**Decision.**

- The controller caches the `RouterCommandAck` for **every processed command, accepted or
  rejected**. A replayed rejected command returns the same cached reject — the client sees a
  stable outcome for a given `command_id` regardless of what changed in between.
- Dedup key: `command_id` alone, scoped per router instance (each router keeps its own
  history; a wildcard-targeted command is cached independently by each router that processed
  it).
- Bound: **FIFO, 256 entries**. Replay of an evicted `command_id` is treated as a **new
  command** — documented accepted risk; 256 comfortably exceeds any plausible in-flight/retry
  window for a human- or harness-driven control plane.
- Duplicates return the cached ack, cause **no state change and no `state_revision` bump**
  (D2 table, D5 predicate).
- Phase 1 exercises the reject-caching path cheaply: command kinds not yet implemented in a
  given phase (`UPDATE_ROUTE`, `SET_PARTICIPANT_PARTITION` in Phase 1) are **parsed, rejected
  with `accepted=false` and an "unsupported in this build" message, and cached** like any
  other reject.

**Docs changed.** none beyond this log (command-status.md already implies duplicate-ack
behavior; the bound and reject-caching live here).

## D5 — `state_revision` is a monotonic `uint64`; explicit increment predicate (2026-07-07, accepted)

**Context.** The IDL declared `state_revision` a `string` while
[code-architecture.md](code-architecture.md) increments it as a number; and it appears at
router scope, per-route, and on `RouteView`, without a stated relationship.

**Decision.**

- Type: **`uint64`**, monotonic, starting at 0 per process start (IDL changes from `string`
  in both `RouterRouteStatus` and `RouterStatus`). Restart detection is **not** this field's
  job — `RouterStatus.status_id` / presence handle process identity.
- Scope: **one global counter** in `MutableRouterState`. The per-route
  `RouterRouteStatus.state_revision` is a **stamp**: the global revision at that route's last
  externally-visible change. One counter, two views — not two counters.
- Increment predicate — bump exactly when externally visible state changes:
  - any route's `operational_state` or `discovery_state` changes (rollup value, not raw facts);
  - a route's desired spec changes (accepted `ENABLE_ROUTE`/`DISABLE_ROUTE`/`UPDATE_ROUTE`);
  - participant state changes (accepted `SET_PARTICIPANT_PARTITION`);
  - `last_error` changes.
- **No** bump for: counter/metric deltas (counters advance inside a revision), duplicate
  commands, status republish.

**Docs changed.** `command-status.md` (IDL types).

**Amended by D11** — the increment predicate also fires on any topic's `topic_state` or
per-topic discovery rollup change.

---

## D6 — `RouteView` is immutable spec only; staleness via `entity_generation`, not revision (2026-07-07, accepted)

**Context.** [code-architecture.md](code-architecture.md) called `RouteView` "immutable"
yet gave it "current state_revision" — a mutable-by-implication field a forwarding runtime
doesn't need.

**Decision.** `RouteView` = the immutable resolved active-side **spec** (route definition,
selected side, topic list, endpoint policy references) plus the `entity_generation` it was
minted for. No `state_revision`, no operational state — runtimes make forwarding decisions
from spec, and report observations as events; they never read lifecycle state. On any
spec-affecting change the controller mints a **new** `RouteView` with a new generation and
rebuilds the runtime; `AsyncWaitSetDispatcher` rejects stale operations by generation
(already the rule in Concurrency Rules).

**Docs changed.** `code-architecture.md` (`RouteView` block).

**Amended by D23** — `entity_generation` becomes a stamp from one global counter;
staleness is checked per topic, not per route.

---

## D7 — Phase 1 seams: concrete routes, three commands, read-only participants, no DDS status writer (2026-07-07, accepted)

**Context.** Phase 1 borrows shapes from later phases (role selection → Phase 4, DDS
command/status loop → Phase 6, partitions → Phase 8). The exact seams were unstated.

**Decision.**

- **Routes**: the controller is constructed with a list of concrete active-side
  `RouterRouteSpec`s (from the Phase 0 config parser or test fixtures). Role-aware
  `source_side`/`destination_side` selection stays in Phase 4; Phase 1 never sees it.
- **Commands**: `ENABLE_ROUTE`, `DISABLE_ROUTE`, and duplicate handling, injected as
  `CommandReceived` events (no DDS reader). `UPDATE_ROUTE` and `SET_PARTICIPANT_PARTITION`
  are parsed-and-rejected per D4.
- **Participants**: `MutableRouterState.participants` is populated read-only from config for
  status completeness; no mutation path until Phase 8.
- **Status**: `StatusPublisher` is an interface; Phase 1's implementation captures
  `RouterStatusView` snapshots for assertions (and/or logs them). The DDS `RouterStatus`
  writer arrives with the admin plumbing (Phase 2 shows discovery in status; Phase 6 completes
  the command/status loop).

**Docs changed.** `implementation-plan.md` (Phase 1 section).

**Amended by D10** — the "Phase 0 config parser" route source was a dead option (that parser
is identity-only); Phase 1 routes come from test fixtures, and `RouteConfigParser` is a
Phase 4 deliverable.

**Amended by D25/D26** — the `RouterStatusView` capture shape is deleted (the snapshot *is*
the generated `RouterStatus`); late-joiner catch-up comes from status durability.

---

## D8 — Transition-table completions: redundant commands idempotent-accept; `RESOLVING` aborts to `WAITING` on discovery regression; `caused_by_command_id` empty unless command-caused (2026-07-07, accepted)

**Context.** Phase-1 plan review found three edges the D2 table left undefined, each one a
test that could not be written until decided: a redundant `ENABLE_ROUTE`/`DISABLE_ROUTE`
arriving with a **new** `command_id` (the duplicate row only covers same-id replay, the
re-arm row only covers `ERROR`); discovery regressing below `READY` while a route is
`RESOLVING` (the table exited `RESOLVING` only via success or failure); and the
`caused_by_command_id` status field, which had no defined value for transitions not caused
by a command.

**Decision.**

- **Redundant state-changing commands are idempotent accepts.** An `ENABLE_ROUTE` targeting
  a route that is already desired-enabled (any state except `ERROR`, whose re-arm path D2
  already defines), or a `DISABLE_ROUTE` targeting a `DISABLED` route, is **accepted** with
  an ack message like "already enabled"; the ack is cached per D4; there is **no** state
  change and **no** revision bump (D5: the desired spec did not change). Commands declare
  desired state, so re-declaring it is success — and an evicted-then-retried command (D4's
  documented risk) now degrades to a harmless accept instead of a contradictory reject.
- **`RESOLVING` aborts to `WAITING_FOR_DISCOVERY` when discovery regresses.** Sticky
  `ERROR` stays reserved for genuine resolve/entity-creation failures; a transient endpoint
  flap mid-resolve never requires operator re-arm. New authoritative rows for the D2 table:

  | From | Event (guard) | To | Side effects |
  |---|---|---|---|
  | `RESOLVING` | discovery facts change (D < `READY`) | `WAITING_FOR_DISCOVERY` | abort resolve; discard partial entities; revision++ |
  | any except `ERROR` | redundant `ENABLE_ROUTE`/`DISABLE_ROUTE`, new `command_id` (route already in desired state) | (no change) | ack `accepted=true` ("already …"); cache ack; **no** revision bump |

- **Fake-seam consequence (Phase 1).** The abort row is only testable if the fake
  `EntityFactory` models resolve as a **pending** operation completed by an explicit
  test-posted event (as D2 already implies for teardown-complete). Phase 1's fakes must
  support deferred resolve completion — which is also the seam Phase 3's real asynchronous
  entity creation needs.
- **`caused_by_command_id` is empty unless the change was directly caused by an accepted
  command.** Discovery- and runtime-driven transitions (e.g. `WAITING_FOR_DISCOVERY ->
  RESOLVING`, endpoint-loss degradation) carry an empty id. No "last command that touched
  the route" persistence — that would show a stale command id on failures it did not cause.

**Docs changed.** `implementation-plan.md` (Phase 1 evidence), `command-status.md`
(`caused_by_command_id` note).

**Amended by D21** — the pending resolve/teardown completions are the named per-topic
events `TopicEntitiesReady` / `TopicTeardownComplete`.

---

## D10 — Phase 1 routes come from test fixtures; `RouteConfigParser` lands in Phase 4 (2026-07-07, accepted; amends D7)

**Context.** D7 said the Phase 1 controller is constructed with specs "from the Phase 0
config parser or test fixtures" — but the Phase 0 parser (`RouterIdentity`) is deliberately
identity-only and does not read the `routes:` sections. The parser branch was a dead
option, and no phase in the implementation plan owned `RouteConfigParser`.

**Decision.**

- Phase 1 constructs the controller with **fixture `RouterRouteSpec` lists only**;
  `router_main` is untouched and Phase 1's running-code evidence is the unit-test target
  (already the shape of its evidence list). No demo scaffolding.
- **`RouteConfigParser`** (full `routes:`/`participants:`/QoS-section parsing plus
  role-aware `source_side`/`destination_side` selection) is an explicit **Phase 4
  deliverable** — Phase 4 is where role-aware YAML selection first appears, so the parser
  lands with its first consumer. Phases 2–3 keep using fixtures/hardcoded specs.

**Docs changed.** `implementation-plan.md` (Phase 1 contract banner, Phase 4 deliverables).

---

## D11 — Per-topic activation and per-topic status; route `operational_state` is derived over topic states (2026-07-08, accepted; amends D1, D2, D5)

**Context.** Routes carry a topic *list* (`platform_events` has two), but D1's discovery
facts and D2's states were per-route, singular. Undefined: does a route wait for **all**
topics to be discoverable, or activate per topic? And status had no per-topic detail, so a
forwarding topic could hide a cold one.

**Decision.**

- **Per-topic activation.** Discovery facts (`input_writer_seen`, `type_resolved`,
  `qos_resolved`, auto-QoS `output_reader_seen`) are tracked **per topic**, each with its
  own `RouterRouteDiscoveryState` rollup. A route is **active as soon as at least one topic
  is ready**; each topic's entities (reader, writer, `ReadCondition`) are created and torn
  down independently as that topic's facts change. A topic joining or leaving an already-
  active route causes **no route-level operational transition** — it is visible in the
  per-topic status and bumps `state_revision`.
- **Per-topic entity state.** New enum `RouterRouteTopicState`:
  `TOPIC_IDLE` (no entities), `TOPIC_CREATING`, `TOPIC_FORWARDING`, `TOPIC_TEARING_DOWN`,
  `TOPIC_ERROR` (sticky until command re-arm, mirroring D2's route-level rule). Creation
  and teardown are event-bounded per topic, same as the route-level model (the Phase 1
  fakes' pending-completion seam from D8 applies per topic).
- **Route `operational_state` becomes a pure derivation** over the topic states of an
  enabled route:

  | Condition over topic states | `operational_state` |
  |---|---|
  | commanded disabled (teardown of all topics complete) | `DISABLED` |
  | all `IDLE` (or `IDLE`/`ERROR` mix with no activity) | `WAITING_FOR_DISCOVERY` |
  | ≥ 1 `CREATING`, none `FORWARDING` | `RESOLVING` |
  | ≥ 1 `FORWARDING` | `ENABLED` |
  | none `FORWARDING`/`CREATING`, ≥ 1 `TEARING_DOWN` | `DEGRADED` |
  | route-wide failure (e.g. participant missing), or **all** topics `TOPIC_ERROR` | `ERROR` |

  The D2 table remains the authoritative command/lifecycle contract with its guards read
  over the topic set: `D = READY` means "≥ 1 topic READY"; the `ENABLED → DEGRADED` trigger
  is the **last** forwarding topic losing its requirements; `DEGRADED` teardown-complete
  goes to `RESOLVING` if any topic is READY, else `WAITING_FOR_DISCOVERY`.
- **Error containment.** One topic's entity-creation or runtime failure marks **that topic**
  `TOPIC_ERROR` (with its own `last_error`) and does not stop sibling topics. Route-level
  sticky `ERROR` is reserved for route-wide failures or all topics errored. Command re-arm
  (D2's `ERROR` exit) retries errored topics — at route scope it also clears per-topic
  errors.
- **Status shape.** `RouterRouteStatus` gains `sequence<RouterRouteTopicStatus> topic_status`
  (per-topic name, discovery rollup, `topic_state`, counters, `last_error`). Route-level
  `discovery_state` becomes the **best (max)** topic rollup — consistent with "active if any
  topic is ready" — and route-level counters are aggregates across topics. `state_revision`
  bumps on any topic's `topic_state` or discovery-rollup change (extends D5's predicate).

**Rationale.** One cold or broken topic must not block the others (`platform_events`
should deliver `PlatformCommandAck` even if `ContactReport` has no writer yet); the
per-topic sequence keeps that visible instead of hidden behind a green route. Deriving the
route state keeps exactly one source of truth — topic states — so route and topic status
can never contradict.

**Phase impact.** Phase 1 implements the per-topic model directly (fixtures include a
two-topic route); Phase 3 stays single-topic; Phase 7 (`platform_events`) exercises it
against real DDS.

**Docs changed.** `command-status.md` (IDL: `RouterRouteTopicState`,
`RouterRouteTopicStatus`, `topic_status` field + semantics note), `code-architecture.md`
(`RouteState` per-topic block), `implementation-plan.md` (Phase 1 banner + evidence).

---

## D12 — `DiscoveryDispatcher` uses builtin readers with `ReadCondition`s on the `AsyncWaitSet`; disabled-participant startup; GUID-keyed upsert cache (2026-07-08, accepted)

**Context.** Phase 2 left "built-in publication/subscription readers or Connext discovery
listeners" open (also the Phase-2 confidence-investigation row;
[connext-investigation-review.md](connext-investigation-review.md) leaned listener-first
with readers as the fallback). Mechanics validated against 7.7 via `ask_connext_question`
(2026-07-08).

**Decision.**

- **Builtin readers, no listeners.** The dispatcher is fed by the builtin `DCPSPublication`,
  `DCPSSubscription`, and `DCPSParticipant` DataReaders with **`ReadCondition`s attached to
  the router's own `AsyncWaitSet`**. The dispatch handler `take()`s and posts
  `PublicationDiscovered` / `SubscriptionDiscovered` / `EndpointLost` controller events —
  uniform with route dispatch and automatically compliant with the shallow-callback rule.
  (The participant reader is included so endpoint records can be joined to their owning
  participant and participant loss can be observed.)
- **No-loss startup.** Builtin readers are created **lazily** in 7.7, so a late lookup is
  not guaranteed to replay earlier discovery. `ParticipantRegistry` therefore creates each
  participant **disabled** (factory `autoenable_created_entities = false`), looks up the
  builtin readers and attaches conditions, **then** enables the participant.
- **Cache semantics.** Builtin readers are KEEP_LAST(1) per instance — a **current-state
  cache, not an event log**. The dispatcher keys records by endpoint GUID (`BuiltinTopicKey`),
  treats samples as **upserts** (a later sample for the same instance can add data — e.g.
  the discovered type, D13), and derives removals from builtin **instance-state
  transitions** (graceful dispose now; participant-loss purge semantics are a separate
  pending decision).
- **Queue consequence.** With real discovery posting from waitset dispatch threads, the
  `ControllerEvent` queue is explicitly **MPSC** (multiple producer threads, one consumer
  strand) from Phase 2 on; Phase 1's implementation must not bake in single-threaded
  producers.

This resolves the Phase 2 confidence-investigation row.

**Docs changed.** `implementation-plan.md` (Phase 2 banner, deliverable, investigation row),
`code-architecture.md` (`ParticipantRegistry`/`DiscoveryDispatcher` bullets, concurrency rule),
`connext-investigation-review.md` (Phase 2 fallback promoted to decision).

**Amended by D16** — participant-purge fan-out into per-endpoint `EndpointLost` is defined
there (the pending purge-semantics item above is resolved).

**Amended by D28/D30** — removal handling is uniform native per-endpoint instance
transitions (the "graceful dispose now / purge pending" split is gone), and the dispatcher keeps
no endpoint cache: the upsert semantics live in the controller's matched sets, and the
builtin readers' own KEEP_LAST(1)-per-instance caches are the only current-state store.

---

## D13 — LAN participants set `request_types_filter`; discovered type is optional-until-resolved (2026-07-08, accepted)

**Context.** In 7.7 (TypeLookup + TypeObject v2 on by default), Connext requests an unknown
remote type only when a **matching local endpoint** exists or the topic matches
`DiscoveryConfigQosPolicy::request_types_filter`. The router deliberately creates no local
endpoints before type/QoS resolution ("discovery before DDS entity construction"), so on the
learning side the D1 fact `type_resolved` would **never latch** — every route would sit in
`DISCOVERY_PARTIAL` forever with no error.
[connext-investigation-review.md](connext-investigation-review.md) already pins the WAN
side: the filter must **not** be set there (the router is the type *authority* on the WAN,
not a learner; `"*"` would proactively pull every remote type across the constrained link).

**Decision.**

- **LAN participants** (where the router learns types from discovered application writers)
  set `request_types_filter` — `"*"` for the POC, narrowable to the configured route topic
  list later. **WAN/team participants keep it unset**, per the WAN type-exchange finding.
- **Asynchronous type arrival is the normal case.** The first builtin sample for an endpoint
  may carry no type; endpoint records hold an **optional** `DynamicType`
  (`PublicationBuiltinTopicData::type()` / `get_type_no_copy()`), typically populated by a
  later builtin update of the same instance (D12 upsert). While pending, the topic simply
  stays `DISCOVERY_PARTIAL` (D1) — no timeout; status explains the wait. Resolution order is
  unchanged: generated support → loaded XML catalog → discovered TypeObject/TypeLookup.
- Legacy **TypeObject v1 acceptance** (`endpoint_type_object_lb_serialization_threshold = -1`)
  is consciously **omitted** — every process on this rig is 7.7/TypeObject v2; revisit only
  if an older remote appears.
- **Reference implementation:**
  [`connext_starter_kit/tools/rti_view/rti_view/discovery.py`](https://github.com/rtidgreenberg/connext_starter_kit/blob/main/tools/rti_view/rti_view/discovery.py)
  (`configure_type_lookup_qos()`, optional-type endpoint records, type-availability scoring)
  — the same pattern proven in a working dynamic-subscription tool.

**Docs changed.** `implementation-plan.md` (Phase 2 evidence),
`code-architecture.md` (`DiscoveryDispatcher` bullet),
`connext-investigation-review.md` (LAN-side pointer in the WAN type-exchange section).

---

## D14 — Link metrics: capture and per-peer rollup only; health inference deferred to a correlation experiment (2026-07-08, accepted)

**Context.** Tenet 5 lists "(Future) per-writer/reader protocol statistics" as a
justification for the custom relay, but nothing defined what is captured or how. An
investigation validated against Connext 7.7 (Modern C++, `ask_connext_question`, 2026-07-08)
established what reliable-protocol statistics a router-to-router writer/reader pair exposes
and how a middleware-level RTT can be measured. Full findings and capture design:
[link-health.md](link-health.md).

**Decision.**

- **Capture first, infer later.** The router captures and publishes raw per-link metrics; it
  does **not** classify link health from them. Presence (`RouterHealth` DEAD/STALE) remains
  the only health authority until a controlled link-degradation experiment (netem/EMANE
  ground truth) correlates each metric with known impairment. Thresholds/classification are
  a separate future decision gated on that experiment.
- **Sources.** Per-matched-endpoint protocol statuses on WAN endpoints
  (`matched_subscription_datawriter_protocol_status` by handle/locator,
  `matched_publication_datareader_protocol_status`), `ReliableWriterCacheChangedStatus`
  (backpressure: unacked count/peak, window-full and watermark events),
  `ReliableReaderActivityChangedStatus` (inactive peers), reader `SampleLostStatus`
  distinguishing `lost_by_writer` (end-to-end loss) from local-limit reasons.
- **Rollup key: peer `router_id`** — matched endpoint handle/locator → participant GUID
  (`DiscoveryDispatcher`) → `router_id`, summed across this router's WAN endpoints per peer. Two
  sources for the GUID→router join, either sufficient: the `PresenceMonitor` roster and the
  D15 participant `user_data` tag (`act.router=<node>/<router>`), which identifies router
  participants even before/without a presence heartbeat.
- **One owner, self-computed deltas.** A new `LinkStatsCollector` module is the sole reader
  of these statuses (reads reset `_change` fields and changed-flags); it polls cumulative
  totals on a ~1 s tick and computes interval deltas itself, never trusting `_change`.
- **RTT probe via application acknowledgment on a dedicated `RouterLinkProbe` topic.**
  `APPLICATION_AUTO` + `on_application_acknowledgment` gives per-peer roundtrips from one
  writer, at the cost of an AppAck + AppAckConf message per sample per peer — negligible at
  ~1 Hz probe cadence. Probe QoS: `RELIABLE`, `VOLATILE`, `KEEP_LAST(1)`, no liveliness,
  piggyback heartbeat per sample (`heartbeats_per_max_samples == send window`, fixed
  window), zero reader `heartbeat_response_delay`. App-ack and the zero-delay QoS are
  **never** applied to data routes (per-sample handshake scales with traffic; retention
  extends to fully-ACKed; ACK-delay jitter exists to prevent ACK implosion).
  **Carrier discussed and decided (2026-07-08): dedicated topic, not `RouterHealth` reuse.**
  Reuse would put app-ack retention semantics on the presence authority (unvalidated against
  `TRANSIENT_LOCAL`/`KEEP_LAST(1)` replacement), generate bogus RTTs from durable-replay
  app-acks at peer rejoin, and entangle probe tuning with presence DEADLINE/lease
  calibration. The cost of the dedicated topic — one more WAN writer/reader pair per router
  of discovery, restart-churned GUIDs in peers' discovery DBs, `remote_*_allocation`
  headroom — is **explicitly accepted** for that isolation. `RouterHealth` QoS is untouched;
  it stays the passive stats bellwether.
- **Publication: LAN only** — per-peer `ActRouterLinkStats` samples plus one structured log
  line per interval; nothing new crosses the WAN. Metric deltas do not bump
  `state_revision` (consistent with D5).

**Resolved choices (2026-07-08 discussion).**

- **Poll period: config-fixed** (YAML, default 1 s, constant per run). No runtime command,
  no adaptive cadence — a constant cadence keeps experiment sweeps comparable, and
  `interval_ms` in the sample means command-adjustability can be added later with no wire
  change. Adaptive cadence rejected on principle: measurement cadence must not depend on
  the measured signal.
- **Granularity: per-peer rollup only.** One `ActRouterLinkStats` sample per peer per tick.
  Matched-endpoint statuses are per (topic-endpoint × peer), so per-topic detail remains
  observable and can be added later (sequence field mirroring D11's per-topic shape) if the
  experiment shows per-topic divergence matters; impairment is link-level, so per-peer is
  the correlating unit.
- **Publication: LAN topic + structured log line from the start.** The topic makes the
  correlation experiment recordable with stock DDS tooling and enables live mesh tooling;
  the log line keeps the experiment runnable offline.
- **Phasing: own thin phase immediately after the Presence/Health phase** (reuses the
  roster/tag GUID→router join and the WAN entities; keeps the presence spike small). The
  correlation experiment is its own `spikes/` entry (PLAN.md + Python netem driver, per repo
  convention) anchored to this phase; netem is the experiment's ground-truth generator, not
  a relay feature (Tenet 3).

**Docs changed.** `link-health.md` (new), README (doc-set index, goal + Why-Try-This
bullets de-futured), `thesis-and-tenets.md` (Tenet-5 pointer), `code-architecture.md`
(`LinkStatsCollector` module tree + bullet), `implementation-plan.md` (reframe-banner
bullet: capture phase after Presence/Health; correlation-experiment investigation row),
`command-status.md` (`ActRouterLinkStats` LAN topic entry), `presence-and-health.md`
(`RouterHealth` QoS-untouched/bellwether note; `RouterLinkProbe` + `ActRouterLinkStats`
topic-table rows).

---

## D15 — Loop safety via `ignore_publication`: self-ignore route output writers at creation; ignore same-node router publications at discovery (2026-07-08, accepted)

**Context.** Builtin readers hide a participant's own endpoints from *discovery*, but they
do not prevent *matching*: a route output DataWriter and a route input DataReader on the
same participant/topic/partition **do** match. The `platform-team` instance will hold both
for `PlatformData` on its one LAN participant (`platform_team_to_wan` input reader,
`wan_team_to_platform` output writer) — a forwarded sample would re-enter the outbound
route and echo across the team WAN, invisibly to the discovery dispatcher. The same-node sibling
instance's writers are likewise visible, matchable candidate inputs. Partitions **cannot**
enforce non-matching here (validated 7.7): matching is evaluated per reader-writer pair on
the shared partition strings, and router and apps both legitimately need the same partition.
Ignore semantics validated via `ask_connext_question` (2026-07-08).

**Decision.**

- **Self rule — ignore at creation.** Immediately after `EntityFactory` creates a route
  output DataWriter — after creation, **before the first write** / before handing it to the
  runtime — the owning participant ignores it:
  `dds::pub::ignore(participant, writer.instance_handle())`. Ignored publications are never
  matched by future readers either, so no route input reader on that participant can ever
  receive the router's own forwarded output, regardless of route config. Scope: route
  output writers only (admin/status/presence writers are not candidate route inputs).
- **Same-node rule — tag-driven ignore at discovery.** Every router participant sets
  `user_data = act.router=<node>/<router>` (participant name as human-readable secondary).
  In the D12 dispatch handler, a discovered publication whose owning participant carries a
  **same-node** router tag is first recorded in the dispatcher (flagged ignored — post-ignore
  builtin visibility is not normatively specified, so record before ignoring), then ignored
  via `dds::pub::ignore(participant, sample_info.instance_handle())`. This is RTI's
  documented safest call site (during builtin-sample processing). Remote routers'
  publications are untouched — on the WAN they are the *expected* route inputs.
- Endpoint records gain **`origin_router`** (empty for application endpoints), joined from
  the `DCPSParticipant` reader already in the dispatcher (D12); used for status/debug and as the
  same-node trigger. Enforcement itself is DDS-level, not route-matching logic.
- **Rejected:** `ignore_participant` — domain-wide suppression of the sibling would
  silently kill any future node-local router coordination topic and erase the sibling from
  the dispatcher. Partitions as enforcement — cannot prevent the match (see context).
  Irreversibility of ignore in 7.7 is accepted: a restarted sibling presents new handles
  and is simply re-ignored on rediscovery.
- **Origination visibility (added same session).** The tag join doubles as a
  router-name → participant-GUID mapping usable by the presence roster and the link-stats
  rollup (D14). On top of it, route runtimes perform a cheap per-sample origination check:
  each reader keeps a seen-set of `SampleInfo::publication_handle`s (one hash lookup on the
  hot path); the **first** sample from a new handle posts an `InputOriginObserved` event and
  the controller resolves the handle against the dispatcher and logs the origination (app writer
  vs `origin_router`). Each leg has an expected origin — **LAN inputs: app-originated only**
  (self/sibling are ignored; remote routers never write into this node's LAN),
  **WAN inputs: router-originated** — so deviations are warned loudly: router-origin on a
  LAN input means the ignore protection was bypassed or a cross-node route loop exists;
  app-origin on a WAN input means something is publishing directly onto the WAN domain.

**Phase 8 side-finding (recorded here, decision deferred to Phase 8).** Publisher/Subscriber
PARTITION **and** participant-level partition are **runtime-mutable** in 7.7: `set_qos`
triggers rediscovery/rematching without entity recreation (participant-level propagates via
participant discovery, so slower). `SET_PARTICIPANT_PARTITION` may therefore be a plain QoS
change; Phase 8's "recreate affected entities" is the fallback, not the premise.

**Docs changed.** `implementation-plan.md` (Phase 2 banner, deliverable, evidence; Phase 3
evidence; Phase 8 open question + investigation row), `code-architecture.md`
(`ParticipantRegistry`, `DiscoveryDispatcher`, `EntityFactory` bullets; `InputOriginObserved`
event row).

**Amended by D29** — the origination-visibility bullet's mechanism (per-reader seen-set,
`InputOriginObserved`, handle→index join) is replaced by a discovery-time expected-origin
rule inside controller matching; the per-leg policy itself is unchanged. The ignore rules
(self and same-node) are untouched.

---

## D16 — Endpoint loss is participant-lease-driven: short LAN lease, ordered WAN lease; index fans participant purge into per-endpoint loss (2026-07-08, accepted)

**Context.** Graceful shutdown disposes builtin endpoint instances promptly, but a
SIGKILLed process's endpoints stay visible until its **remote participant liveliness lease**
expires (validated 7.7; the stock default is long — the exact figure was not confirmed
numerically and is measured in the Phase 2 smoke). Meanwhile
[presence-and-health.md](presence-and-health.md) already assigns the WAN participant lease a
role: it is the mesh crash-detection knob (starting point
`BuiltinQosSnippetLib::Optimization.Discovery.Common`, 10 s lease / 3 s assert), and
participant purge is one of the DEAD triggers driving bulk instance cleanup.

**Decision.**

- **LAN participants: short lease.** Starting shape ≈ **5 s lease / 1 s assert** (final
  values pinned after the Phase 2 smoke measures the default and the actual purge timing).
  A dead local app degrades its routes in seconds, and kill-based tests are observable
  without minute-long waits.
- **WAN/team participants: presence-ordered lease.** Adopt the presence doc's starting
  point (≈ 10 s lease / 3 s assert), with the recorded **ordering constraint**:
  `WAN participant lease > RouterHealth liveliness window (≈ 2–3× heartbeat period)`.
  The presence topic is therefore always the **first and authoritative** DEAD signal; the
  DDS participant purge trails it as backstop and bulk cleanup
  (`NOT_ALIVE_NO_WRITERS` fan-out), and can never race ahead of the roster. Presence
  calibration owns the concrete values; this constraint must survive any retune.
- **DDS handles the purge; the dispatcher reacts.** A participant purge — graceful dispose *or*
  lease expiry — is fanned out by `DiscoveryDispatcher` into `EndpointLost` for **every endpoint
  owned by that participant** (the `DCPSParticipant` reader's third job, after the D15 tag
  join and D12 startup). Route topics whose discovery facts regress then transition per the
  D2/D11 tables. This resolves the purge item D12 left pending.
- **Phase 2 smoke measures reality:** the actual 7.7 default participant lease, and
  observed endpoint-removal latency for graceful exit vs SIGKILL under the chosen LAN
  values.

**Docs changed.** `implementation-plan.md` (Phase 2 evidence), `code-architecture.md`
(`DiscoveryDispatcher` purge fan-out), `presence-and-health.md` (participant-tuning ordering
constraint), D12 (amend note).

**Amended by D28** — the dispatcher fan-out is demoted to fallback: endpoint removal on
participant purge is observed natively per endpoint on the builtin readers (validated 7.7);
the Phase 2 smoke confirms the per-endpoint cardinality. Lease values and the
presence-ordering constraint are unchanged.

---

## D17 — `RouterStatus` carries no endpoint inventory; discovery visibility = per-topic `discovery_state` + structured log; real LAN `StatusPublisher` lands in Phase 2 (2026-07-08, accepted)

**Context.** Phase 2's evidence said "router status shows discovered endpoints and matching
route candidates," but `RouterStatus` has no discovered-endpoint fields, and D7 deferred the
DDS status writer to "admin plumbing." Also: codegen bounds unbounded IDL sequences at 100
entries, and a busy ACT domain churns endpoints — an inventory field would be both
truncation-prone and revision-bump noise on the one-coherent-sample status topic.

**Decision.**

- **No endpoint dump in `RouterStatus`.** The per-route/per-topic `discovery_state`
  (D1/D11) *is* the "matching route candidates" signal — a topic at `PARTIAL`/`READY` shows
  discovery found and matched it. The raw endpoint inventory (topic/type/QoS summaries,
  `origin_router`, ignored endpoints — D15) goes to the **structured log**.
- **Phase 2 stands up the real DDS `StatusPublisher`** on the LAN participant (types
  already generated in Phase 0; admin rides the LAN participant, which Phase 2 creates
  anyway). Write-only: the command **reader** stays in Phase 6 per D7. Publication cadence
  needs no new rule — discovery rollup changes already bump `state_revision` (D5), and
  status publishes on revision change, so discovery progress is externally observable
  (e.g. via `rti_view` or any LAN subscriber) as it happens.

**Docs changed.** `implementation-plan.md` (Phase 2 banner + evidence),
`command-status.md` (`RouterStatus` scope note).

---

## D18 — Multi-network peers: one WAN participant per unique network (`allow_interfaces_list`), never a multi-homed WAN participant (2026-07-08, accepted — activates when multi-network reaches the rig)

**Context.** Real deployments may reach peers over multiple physical networks (e.g. mesh
radio + SATCOM), typically separate NICs. A single WAN participant on a multi-homed node
announces unicast locators for **all** interfaces, and — validated 7.7
(`ask_connext_question`, 2026-07-08) — remote writers then send **user DATA redundantly to
every announced locator** (duplicates discarded at the receiver; locator-reachability prunes
dark paths; the OS routing table picks the egress NIC). Consequences of staying multi-homed:
near-duplicated traffic on constrained links, and broken per-path observability — writer-side
per-locator stats exist (`matched_subscription_datawriter_protocol_status(Locator)`), but
reader-side status is per-publication only and the D14 app-ack RTT probe cannot attribute
which network an ack rode. Full findings: [link-health.md](link-health.md), "Multi-network
peers".

**Decision.**

- **One WAN DomainParticipant per unique network**, pinned to its NIC/subnet via the UDPv4
  builtin transport `allow_interfaces_list` (plus `max_interface_count` as a guard). A WAN
  participant is never multi-homed. Today's single-network rig is the degenerate `N = 1`
  case — nothing changes until a second network exists.
- **Everything per-path falls out by construction**: each network participant carries its
  own `RouterHealth` writer/reader (per-path presence in the roster, keyed
  `(router_id, network)`), its own `RouterLinkProbe` pair (per-path RTT), and its endpoints'
  protocol statuses are inherently single-path — the D14 rollup key generalizes to
  `(peer router_id, network)` with **no per-locator API machinery needed**.
- **Route-level path policy becomes expressible**: a route's WAN side names the network
  participant (e.g. `wan.mesh` vs `wan.satcom`), so "this topic over mesh, that one over
  SATCOM" is config, not transport guesswork. Policy details (default network, failover
  between networks) are **not** designed here — separate decision when multi-network lands.
- **Rejected:** single multi-homed WAN participant + locator-aware stats (D14 discussion
  option b) — redundant-DATA cost stands, attribution stays writer-only, per-path RTT
  impossible; the per-locator machinery would be wasted once participant-per-network arrives.
- **Config/registry consequence (deferred to activation):** network definitions
  (name → interface allowlist) join the participant config; `ParticipantRegistry` creates
  one WAN participant per configured network; discovery/entity cost scales with network
  count — accepted, mirroring the D14 probe-topic reasoning (observability and traffic
  segregation are worth endpoints).

**Docs changed.** `link-health.md` (multi-network section: candidate positions → this
decision), `configuration.md` (never-multi-homed rule under the POC QoS rules; YAML network
definitions deferred to activation), `code-architecture.md` (`ParticipantRegistry` bullet),
`presence-and-health.md` (`RouterHealth` multi-network note: per-path presence keyed
`(router_id, network)`). Concrete network-definition YAML shape and route→network path
policy remain deferred to activation (no second network in the current rig).

---

## D19 — Endpoint-record QoS summary: pinned captured subset; history/resource_limits are NOT discoverable and must come from aliases/defaults (2026-07-08, accepted)

**Context.** Phase 2 delivers "useful QoS summaries" without saying which policies, and
Phase 5's `auto` QoS derives router endpoint QoS from discovered endpoints. Validated 7.7
(`ask_connext_question`, 2026-07-08): the builtin publication/subscription data carries many
but not all policies.

**Decision.**

- **Captured subset** (the endpoint record's QoS summary, from
  `PublicationBuiltinTopicData` / `SubscriptionBuiltinTopicData`): reliability, durability
  (+ `durability_service`), deadline, latency_budget, liveliness, ownership (+ strength),
  lifespan, destination_order (**kind only** — its other fields are not propagated),
  presentation, partition, and data representation.
- **Negative finding (load-bearing for Phase 5):** **HISTORY and RESOURCE_LIMITS are not
  propagated in discovery at all.** `auto` QoS can therefore never derive history or
  resource limits from discovered endpoints — those always come from the router's QoS
  aliases/defaults. This pre-answers part of Phase 5's investigation row: the "minimum
  compatible policy set" is bounded by what discovery can actually see (the subset above);
  anything else is alias-supplied by definition.

**Docs changed.** `implementation-plan.md` (Phase 2 deliverable; Phase 5 deliverable note +
investigation row), `code-architecture.md` (`DiscoveryDispatcher` bullet pointer).

**Amended by D27** — the captured subset is a read rule over the stored builtin topic data,
not a struct definition; the negative finding (history/resource_limits never discoverable)
stands unchanged.

---

## D20 — Phase 2 contract completions: participants from config (no admin participant); discovery facts derive from matched-endpoint sets; same-topic type-name conflicts are first-resolved-wins (2026-07-08, accepted)

**Context.** Three leftover gaps from the Phase 2 review: the deliverable still listed an
"admin" participant (stale — admin rides the LAN participant per
[command-status.md](command-status.md)); D1/D11's per-topic discovery facts are booleans,
but a topic can have several matched writers; and two discovered writers on one topic with
different type names had no defined policy.

**Decision.**

- **Participants come purely from config, per instance** — `control-platform`: LAN + WAN;
  `platform-team`: LAN + team-WAN. There is **no admin participant**: admin endpoints hang
  off the LAN participant. (Multi-network expansion per D18 when it activates.)
- **Discovery facts derive from per-topic matched-endpoint sets.** The dispatcher keeps the set
  of matched input writers (and, for auto-QoS routes, output readers) per route topic;
  `input_writer_seen` ⇔ set non-empty. Losing one of several writers updates the set with
  **no** rollup change and no revision bump (consistent with the D2 note on non-boundary
  fact changes); only the last writer's loss regresses the rollup.
- **Same-topic type-name conflict: first-resolved-wins.** The first type resolved for a
  route topic is the route's type; a subsequently discovered writer with a different type
  name on the same topic is recorded in the dispatcher, logged as a **warning**, and does not
  change the resolved type. Status shows the resolved type (`resolved_type_name`); the POC
  does not attempt multi-type topics.

**Docs changed.** `implementation-plan.md` (Phase 2 deliverable wording),
`code-architecture.md` (`TopicRouteState.discovery_facts` comment).

**Amends D1/D11** — the raw facts are now set-derived, not stored booleans.

**Amended by D22** — the matched-endpoint sets live in controller state
(`TopicRouteState`); the dispatcher keeps the GUID-keyed endpoint records.

---

## D21 — Per-topic entity-operation completion events: `TopicEntitiesReady` / `TopicTeardownComplete`; `RouteEntityError` gains topic scope (2026-07-09, accepted; amends D2, D8, D11)

**Context.** D2 makes teardown completion a controller event, D8 requires resolve
completion to be a *deferred* event the Phase 1 fakes can hold pending, and D11 makes both
per-topic — but the [code-architecture.md](code-architecture.md) event table named no
success-completion events; only `RouteEntityError` existed, and without per-topic scope.
Phase 1's transition-table conformance tests cannot be written against unnamed events.

**Decision.**

- Two new controller events, posted by `EntityFactory`/dispatcher in Phase 3+ and by the
  fake factory in Phase 1:
  - **`TopicEntitiesReady { route_name, topic_name, entity_generation }`** — entity
    creation for one topic completed; drives `TOPIC_CREATING → TOPIC_FORWARDING` and, via
    the D11 derivation, `RESOLVING → ENABLED`.
  - **`TopicTeardownComplete { route_name, topic_name, entity_generation }`** — teardown
    for one topic completed; drives `TOPIC_TEARING_DOWN → TOPIC_IDLE` and the D2
    `DEGRADED → RESOLVING | WAITING_FOR_DISCOVERY` edge.
- Failures stay on **`RouteEntityError`**, which gains
  `{ route_name, topic_name, entity_generation, error }`. **Empty `topic_name` ⇒
  route-wide failure** (route goes sticky `ERROR`); non-empty ⇒ that topic goes
  `TOPIC_ERROR` with D11 containment (siblings keep forwarding).
- Every completion/error carries the **generation stamp** it was issued for (D23); the
  controller discards events whose stamp no longer matches the target topic's current
  stamp.
- A single generic `op/outcome` completion event was **rejected**: Phase 1's conformance
  tests should map 1:1 onto D2/D11 table rows, and one event kind per row keeps the table
  and the tests eyeball-comparable.

**Docs changed.** `code-architecture.md` (event-table rows), `implementation-plan.md`
(Phase 1 deliverable + evidence).

---

## D22 — Controller owns endpoint→route-topic matching and the matched sets; `DiscoveryDispatcher` is a GUID-keyed record cache + raw event source (2026-07-09, accepted; amends D20)

**Context.** D20 said "the discovery component keeps the set of matched input writers per
route topic," while [code-architecture.md](code-architecture.md) keeps the set-derived
`discovery_facts` in `TopicRouteState` (controller state) and forbids the discovery
component from owning route state. With discovery faked as events in Phase 1 (D3), the
ownership choice decides whether matching logic is tested in Phase 1 or first written
untested in Phase 2.

**Decision.**

- Discovery events carry **raw endpoint records**: `PublicationDiscovered` /
  `SubscriptionDiscovered` upserts and `EndpointLost`, each with endpoint GUID, topic
  name, type name + resolved flag, partition, QoS summary (D19 subset), and
  `origin_router` (D15).
- The **controller** matches records against route topic specs and maintains the
  **per-topic matched-endpoint sets** inside `TopicRouteState`; facts derive from those
  sets exactly as D20 defined (seen ⇔ set non-empty; only the last endpoint's loss
  regresses the rollup). Route knowledge lives in one place, and the single-writer rule
  holds.
- **`DiscoveryDispatcher`** keeps the **GUID-keyed endpoint-record cache** (upsert semantics per
  D12/D13, purge fan-out per D16, ignore/tag handling per D15) and serves lookups (e.g.
  `InputOriginObserved` handle → origin resolution). It never sees route specs.
- **Phase 1 consequence.** The fake dispatcher is a dumb event source; tests post raw endpoint-record
  events, so matching, set maintenance, and the D20 set-boundary rules ("lose one of two
  matched writers → facts change, no rollup change, no revision bump") are genuinely
  proven in Phase 1 rather than deferred to Phase 2.

**Docs changed.** D20 (amend note), `code-architecture.md` (`DiscoveryDispatcher` bullet,
`TopicRouteState` comment), `implementation-plan.md` (Phase 1 banner + evidence).

**Amended by D27/D30** — event payloads are copies of the builtin topic data plus the
`origin_router`/`ignored` sidecar; the dispatcher-side GUID-keyed record cache is deleted (the
dispatcher keeps only the participant table). Controller-side matching, the matched sets,
and the Phase 1 fake-dispatcher consequence are unchanged.

---

## D23 — One global entity-generation counter; `RouteView` mints and topic entity builds take stamps; staleness is checked per topic (2026-07-09, accepted; amends D6)

**Context.** D6 (route-level `RouteView` generation, dispatcher stale-rejection) predates
D11's per-topic entity lifecycle. A route-scoped generation that bumps when one topic
rebuilds would falsely stale-mark a healthy sibling's in-flight operations — violating
D11's containment. Phase 1 builds the state structs, so the shape gets baked now.

**Decision.**

- `MutableRouterState` holds one **monotonic entity-generation counter**, mirroring D5's
  one-counter/many-stamps pattern for `state_revision`.
- Every `RouteView` mint and every per-topic entity build takes the **next counter value
  as its stamp** (`RouteView.entity_generation`; `TopicRouteState.entity_generation`).
- An entity operation or completion event is valid iff its stamp equals the target topic's
  current stamp; the controller and `AsyncWaitSetDispatcher` discard stale-stamped
  operations. This per-topic check replaces D6's route-level check. Global uniqueness
  means "spec change vs sibling rebuild" never needs disambiguation and there are no
  counter-reset rules.
- **Rejected:** route-level-only generation (falsely stales siblings, see context);
  per-topic independent counters (a second idiom, plus spec-vs-entity generation
  interaction rules the stamp pattern doesn't need).

**Docs changed.** D6 (amend note), `code-architecture.md` (state-model fields, dispatcher
concurrency rule).

---

## D24 — Command admission seams: `CommandReceived` is post-admission (targeting is Phase 6's); unknown `route_name` is a cached reject (2026-07-09, accepted)

**Context.** `CommandHandler` (target/wildcard checks) is not a Phase 1 deliverable,
leaving open whether the Phase 1 controller must match `target_node`/`target_router`; and
no table row defined the outcome of a state-changing command naming a route that does not
exist.

**Decision.**

- **`CommandReceived` events are post-admission.** The controller assumes the command is
  addressed to this router and ignores the target fields. Target/wildcard matching is the
  command **reader's** admission job, decided and tested in Phase 6 (it may be reader-side
  filtering or a content-filtered topic — not pre-empted here). D4's per-router ack
  caching is unaffected.
- **A state-changing command naming an unknown `route_name` is rejected**
  (`accepted=false`, "unknown route"), the reject **cached per D4**, with no state change
  and no revision bump (D5). Implicit route creation is explicitly rejected — `ADD_ROUTE`
  stays POC-plus, and a typo'd route name must fail loudly (acceptance criterion: bad
  route commands are rejected and acknowledged, not silent).

**Docs changed.** `implementation-plan.md` (Phase 1 evidence), `command-status.md`
(unknown-route reject note).

---

## D25 — The status snapshot IS the generated `RouterStatus`; the snapshot/view adapter layers are deleted (2026-07-09, accepted; amends D7 wording)

**Context.** The architecture carried four representations of the same information:
`MutableRouterState` → `RouterStateSnapshot` → `RouterStatusView`/`RouteStatusView` →
generated `RouterStatus`, i.e. three conversions between the controller's state and the
wire. Connext 7.7 generated types are plain structs with public data members and value
semantics — a fully built `RouterStatus` is already an immutable snapshot once nothing
mutates it. Simplicity lens: app-level machinery must do something the existing types
cannot (Tenet 9).

**Decision.**

- The controller keeps `MutableRouterState` for internal facts (matched-endpoint sets,
  generation stamps, command history) — those stay off the wire. On each `state_revision`
  bump it builds a generated **`RouterStatus`** directly; a `shared_ptr<const RouterStatus>`
  **is** the snapshot handed to `StatusPublisher` and cached as "current".
- `RouterStateSnapshot`, `RouterStatusView`, and `RouteStatusView` are **deleted** from the
  architecture. `RouteView` (the runtime-facing immutable spec, D6/D23) is unaffected — it
  serves forwarding, not status.
- Phase 1 tests assert on the wire type directly, so the test suite and the published
  contract cannot drift.

**Docs changed.** `code-architecture.md` (state-ownership section, status-view block
replaced, concurrency rule), `implementation-plan.md` (Phase 1 deliverables).

---

## D26 — LAN `RouterStatus` is `RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1)`; publication stays change-driven only (2026-07-09, accepted; amends D7)

**Context.** Late-joiner catch-up is a durability job and aliveness is a liveliness job;
both are DDS-native (Tenet 9). Scope check: `RouterStatus` rides the **LAN participant only**
([command-status.md](command-status.md) transport decision) and never crosses the WAN, so
durability replay traffic is loopback/LAN, one small sample per router — the constrained
link never sees it. In-memory writer history only (`KEEP_LAST(1)`), so no durable writer
history and no SQLite anywhere (vboxsf rule unaffected).

**Decision.**

- **Status writer QoS (LAN):** `RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1)`, keyed per
  `(target_node, target_router)`. Any late-joining LAN observer receives the current full
  snapshot on match, automatically.
- **Publication remains change-driven** (D17): startup plus every revision bump, nothing
  else. **No periodic republish** — every sample = one real state change, which keeps
  revision semantics clean and Phase 1's snapshot-sequence assertions exact. The
  never-delivered timer source for `StatusRequested` is removed; observer-side aliveness
  rides the status writer's liveliness (`AUTOMATIC`), and mesh presence stays
  `RouterHealth`'s job. WAN QoS is untouched by this decision.

**Docs changed.** `command-status.md` (transport/QoS note),
`implementation-plan.md` (Phase 1 banner/deliverables/evidence, Phase 2 evidence, Phase 6
slice + deliverables), `code-architecture.md` (`StatusPublisher` bullet, event table),
`thesis-and-tenets.md` (Tenet 9 records the simplicity/DDS-native lens).

---

## D27 — The endpoint record IS the builtin topic data; D19's subset becomes a read rule (2026-07-09, accepted; amends D19, D22)

**Context.** Phase 2 simplicity pass (Tenet 9, same lens as D25/D26). The design carried a
hand-rolled endpoint-record struct copying fields out of the builtin discovery data.
Validated 7.7 (`ask_connext_question`, 2026-07-09):
`dds::topic::PublicationBuiltinTopicData` / `SubscriptionBuiltinTopicData` are **copyable
value types** safe to store long-term, exposing `key()`, `participant_key()`,
`topic_name()`, `type_name()`, `partition()`, and every policy in the D19 subset as
accessors (subscription data has no `durability_service()` — writer/topic-side policy, as
expected). The RTI `type()` extension carries the discovered `DynamicType`, empty until
TypeLookup resolves it — D13's "optional-until-resolved type" is native to the builtin
data too.

**Decision.**

- There is **no `EndpointRecord` struct**. An endpoint record is a stored copy of the
  builtin topic data plus a two-field sidecar: `origin_router` (D15 tag join) and
  `ignored`. Discovery events (D22) carry exactly that.
- **D19 is reframed, not weakened:** its policy list stops being a struct definition and
  becomes the **discoverable-subset rule** — what matching/auto-QoS may legitimately read
  from a record. The Phase 5 constraint stands verbatim: history/resource_limits are never
  in discovery and always come from aliases/defaults.
- Same move as D25: one shape from DDS to controller to tests to log.

**Docs changed.** D19/D22 (amend notes), `code-architecture.md` (`DiscoveryDispatcher` bullet,
`TopicRouteState` comment), `implementation-plan.md` (Phase 2 deliverables).

---

## D28 — Endpoint removal is DDS-native per endpoint for both exit paths; the D16 fan-out demotes to fallback (2026-07-09, accepted; amends D12, D16)

**Context.** D16 made `DiscoveryDispatcher` fan a participant purge into per-endpoint
`EndpointLost` — app-level enumeration machinery. Validated 7.7 (`ask_connext_question`,
2026-07-09): on remote-participant removal — **graceful delete and lease-expiry purge
alike** — Connext removes "the remote participant, together with all its entities" from
the discovery database, and the removal is observable on the builtin
publication/subscription readers themselves, not only on `DCPSParticipant`. Caveat: the
docs do not *normatively* guarantee the exact cardinality "one `NOT_ALIVE` transition per
endpoint," so the smoke verifies it.

**Decision.**

- **One uniform removal path:** `EndpointLost` is posted per builtin endpoint-instance
  `NOT_ALIVE` transition — the same code path for graceful exit, SIGKILL purge, and
  single-endpoint deletion. D12's "graceful dispose now / purge pending" split and D16's
  app-level fan-out are deleted from the design.
- The **D16 fan-out is the named fallback**, reinstated only if the smoke disproves
  per-endpoint delivery.
- The Phase 2 smoke gains the deciding evidence line: after SIGKILL (and after graceful
  exit), count `NOT_ALIVE` transitions on the builtin endpoint readers and confirm one per
  owned endpoint.
- D16's lease values and the presence-ordering constraint are untouched.

**Docs changed.** D12/D16 (amend notes), `implementation-plan.md` (Phase 2 evidence),
`code-architecture.md` (`DiscoveryDispatcher` bullet).

---

## D29 — The expected-origin warning is a discovery-time rule inside controller matching; the per-sample seen-set and `InputOriginObserved` are deleted (2026-07-09, accepted; amends D15)

**Context.** D15's origination-visibility bullet built real machinery: a per-reader
seen-set of `publication_handle`s (hash lookup per sample on the hot path), an
`InputOriginObserved` event, and a controller-side handle→index join. But the controller
already runs endpoint→route-topic matching over every raw record (D22) and already knows
`origin_router` from the tag join (D15) — the warning is one `if` in code that exists.
Validated 7.7 alternative for actual-RxO-match fidelity (StatusCondition
`subscription_matched()` on the `AsyncWaitSet`, then diff `matched_publications()` against
a known set — `last_publication_handle` does not batch — and
`rti::sub::matched_publication_participant_data()` for the participant `USER_DATA`;
`matched_publication_data()` works only for currently-associated writers): viable, but new
machinery the POC does not need.

**Decision.**

- The **expected-origin-per-leg policy is unchanged** from D15 (LAN inputs: app-origin
  only; WAN inputs: router-origin only). Its **evaluation point moves to discovery time**:
  when the controller matches an endpoint record to a route topic, a router-origin record
  on a LAN leg or an app-origin record on a WAN leg logs a loud warning. Strictly earlier
  than first-sample detection — the loop is flagged before anything echoes — at zero
  hot-path cost.
- The per-reader seen-set, the per-sample check, and the `InputOriginObserved` event are
  **deleted**.
- The `subscription_matched` variant is recorded as the upgrade path if "candidate
  matched" vs "actually RxO-matched" ever matters; not built for the POC.

**Docs changed.** D15 (amend note), `code-architecture.md` (event-table row deleted,
event-list prose, `PublicationDiscovered` action), `implementation-plan.md` (Phase 3
evidence line).

---

## D30 — `DiscoveryDispatcher` is a translator plus a participant table; the GUID-keyed endpoint-record cache is deleted (2026-07-09, accepted; amends D12, D22)

**Context.** With D27–D29, D22's dispatcher-side endpoint cache has no consumer left: matching
and the matched sets live in the controller, which upserts from events — D13's
late-arriving type is just a second `PublicationDiscovered` for the same GUID; removal is
native per-endpoint (D28); origin resolution needs no handle→record join (D29); the
structured-log inventory (D17) logs at event time; routes are config-fixed until Phase 4+
(D10) and `ADD_ROUTE` does not exist, so there is no late-registered-route replay; D14's
future GUID→router join needs the participant table, not endpoint records. The builtin
readers are themselves KEEP_LAST(1)-per-instance current-state caches (D12) — an app-level
mirror is a second copy of a store DDS already maintains.

**Decision.**

- `DiscoveryDispatcher` collapses to a **translator**: `take()` from the three builtin readers
  on waitset dispatch, apply the D15 ignore/tag rules, post events carrying the
  builtin-data copy (D27). **No endpoint store anywhere**; if an ad-hoc query need ever
  appears, the dispatcher switches to `read()` and the builtin readers' own bounded caches
  serve it.
- The only dispatcher state is the **participant table** (participant GUID → `act.router`
  tag / name), maintained from the `DCPSParticipant` reader; consumers: the D15 same-node
  ignore decision and the D14/presence GUID→router join.
- **Participant loss is dispatcher-internal** in Phase 2: the table entry is dropped and the
  loss logged; the controller reacts only to per-endpoint `EndpointLost` (D28). No
  `ParticipantLost` controller event until a phase needs one (presence has its own
  roster).
- The Phase 1 fake dispatcher (D3/D22) is unchanged — it was already a dumb event source.

**Docs changed.** D12/D22 (amend notes), `code-architecture.md` (`DiscoveryDispatcher` bullet,
class-responsibility row), `implementation-plan.md` (Phase 2 banner + deliverables).
