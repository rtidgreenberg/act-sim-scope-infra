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

**Superseded (matching only) by D64** — the builtin readers stay (type acquisition,
loop-safety, presence, diagnosis), but per-topic *matching* moves to DDS
(`matched_publications()`), not controller topic-name re-derivation.

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

**Superseded by D64** — controller-side matching and the per-topic matched-endpoint sets are
replaced by DDS's own `matched_publications()`/`matched_subscriptions()` on the created route
entities (create-and-observe). The controller keeps lifecycle/state, not matching. (Phase 1's
fake-driven tests remain valid for the state machine; the matching they exercised moves to a
DDS-backed spike per D64's readiness prerequisite.)

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

---

## D31 — Phase 3 builds the thin real runtime spine before forwarding; generated-type readiness is a construction fast path; waitset teardown is single-owner (2026-07-09, accepted)

**Context.** A review of the next phase found that Phase 3's forwarding evidence can become
misleading if it jumps directly from synthetic controller events to `EntityFactory` work.
The committed Phase 2 smoke proves builtin discovery mechanics, but the runtime pieces that
feed the controller and publish status still need a minimal real path. Validated against
Connext 7.7 Modern C++ with `ask_connext_question` (2026-07-09): disabled-participant
builtin-reader lookup before enable, LAN `request_types_filter`, generated-type name fast
path, `dds::pub::ignore(participant, writer.instance_handle())`, and single-owner
`AsyncWaitSet` detach/close discipline are consistent with 7.7 behavior, with the caveats
below.

**Decision.** Phase 3's implementation order is:

1. Build the **thin real runtime spine** needed for one route: config-created participants,
   builtin participant/publication/subscription readers attached before participant enable,
   discovery translation into controller events, and a real LAN `StatusPublisher`.
2. Add a generated-type `TypeResolver` fast path for explicit-QoS routes: a discovered
   `topic_name` plus registered `type_name` matching local generated support is sufficient
   **construction readiness**, not proof of full remote schema equivalence. DynamicType /
   TypeLookup equivalence remains a later stronger path.
3. Keep TypeLookup semantics asynchronous: LAN `request_types_filter` initiates early type
   requests for matching topic patterns, but route creation still waits on
   controller-observed readiness.
4. `EntityFactory` creates the output `DataWriter`, immediately calls
   `dds::pub::ignore(participant, writer.instance_handle())` (`<dds/pub/discovery.hpp>`) on
   route output writers, and only then exposes the writer to forwarding or attaches input
   `ReadCondition`s. Failure to ignore is a `RouteEntityError`, not a warning.
5. `AsyncWaitSetDispatcher` is the only owner of route-condition attach/detach. Teardown
   detaches or quiesces the condition before closing DDS entities; callbacks already in
   flight are tolerated by generation-stamped completion events, which the controller
   discards when stale.

**Caveats from Connext validation.** Builtin readers do not report local entities from the
same participant, so same-participant self-discovery is already suppressed; the ignore step
is retained for multi-participant/internal-loop safety and as a deterministic guard before
forwarding. Builtin reader caches are current-state `KEEP_LAST(1)` stores, not event logs,
so the controller must reconcile current facts and must not require every intermediate
discovery edge to arrive.

**Docs changed.** `implementation-plan.md` (Phase 3 deliverables/evidence).

---

## D32 — Route teardown barrier is the blocking `detach_condition`, not generation staleness; pinned close order; `unlock_condition` is never called (2026-07-09, accepted; sharpens D31.5)

**Context.** D31.5 said the `AsyncWaitSetDispatcher` detaches/quiesces a route condition before
closing entities and that "callbacks already in flight are tolerated by generation-stamped
completion events." That framing left the *primary* safety mechanism ambiguous — it read as if
generation staleness were what protects against a forwarding handler touching a closed reader or
writer. It is not: generation stamps protect the **controller state machine** from stale
*completion events*, but they do nothing to stop an in-flight `take()`/`write()` on the AWS
worker thread from racing a `close()`. Before building the real forwarding path this needed a
hard, documented barrier. Validated against Connext 7.7 Modern C++ via `ask_connext_question`
(2026-07-09).

**Decision.** The teardown barrier is the **blocking `AsyncWaitSet::detach_condition()`** call:

- `detach_condition(cond)` **blocks until the detach completes**; on successful return it is
  guaranteed the AWS will no longer dispatch that condition, so any handler that was mid-flight
  has returned. This is the synchronization point — not generation staleness.
- Per-condition dispatch is **serialized by default**: the AWS locks a condition while a worker
  dispatches it, so the same route's forwarding handler can never run on two threads at once. We
  therefore **never call `unlock_condition()`** on route conditions (it exists only to opt into
  concurrent same-condition dispatch, which we do not want).
- Pinned close order, all issued from the controller strand:
  `detach_condition(cond)` → `cond.close()` → `input_reader.close()` → `output_writer.close()`.
  `ReadCondition::close()` requires the condition already be detached from every waitset
  (else `PreconditionNotMetError`), which the order above guarantees.
- Generation-stamped completion events (D21/D23) remain, but their job is narrowed to what they
  actually do: discard stale `TopicTeardownComplete` / `TopicEntitiesReady` / `RouteEntityError`
  events at the controller. They are belt-and-suspenders for the state machine, not the
  use-after-close defense.

**Confidence.** This resolves the sole "concurrency-sensitive" caveat on Phase 3 (the
attach/detach investigation row) to **high** — the fallback in that row ("serialize all
attach/detach/close on the controller strand") is now confirmed to be the design, backed by a
documented blocking guarantee. Self-loop is separately a non-issue: every configured route
places input and output legs on **different participants** (`control_lan`→`control_wan`,
`platform_lan`→`platform_wan`, distinct domains), so intra-participant self-match cannot occur;
cross-participant router-to-router loops are caught by the D15/D29 same-node origin rule (built
and tested in the Phase 2.5 spine); D31.5's `dds::pub::ignore(output writer handle)` stays a
cheap deterministic guard, not the primary defense.

**Docs changed.** `implementation-plan.md` (Phase 3 attach/detach investigation row → resolved;
teardown-order deliverable made explicit).

---

## D33 — Endpoint-loss GUID recovery uses a captured instance-handle→GUID map, not `key_value()` (2026-07-09, accepted; implements D28/D30)

**Context.** Building the Phase 3 forwarding path (`test_route_forward`) exercised, for the first
time against real DDS, the endpoint-loss path in `DiscoveryDispatcher`. The existing code read
the lost endpoint's GUID from `sample.data().key()` on the NOT_ALIVE builtin sample. On an
invalid builtin sample `data()` is unreadable — Connext throws `Precondition not met: Invalid
data` — so the GUID recovery silently failed (the throw was swallowed), no `EndpointLost` event
was posted, and a route never tore down when its source went away. The Phase 2.5 spine never lost
an endpoint, so the bug was latent until now.

**Decision.** `DiscoveryDispatcher` captures a minimal `instance_handle → endpoint-GUID` map from
**valid** publication/subscription samples (application endpoints only — ignored same-node router
publications are never tracked). On a NOT_ALIVE sample it looks the GUID up by the sample's
`instance_handle()`, posts `EndpointLost(guid)`, and erases the entry. `DataReader::key_value()`
is **not** used for this: validated against 7.7, it requires an existing known instance and is
not guaranteed once the instance is no longer alive.

This map is **identity translation, not the endpoint-record cache D30 deleted** — it stores no
topic/type/QoS, only what is needed to turn a native per-endpoint NOT_ALIVE (which carries only
an instance handle) into the GUID the controller keys its matched sets on. It is the concrete
mechanism behind D28's "removal arrives as native per-endpoint NOT_ALIVE transitions."

**Docs changed.** None beyond this log; `code-architecture.md` DiscoveryDispatcher note can pick
this up on its next pass.

---

## D34 — Phase 3 forwarding path shipped and test-verified (2026-07-09, accepted; closes Phase 3)

**Decision.** The Phase 3 runtime is implemented and green: `TypeResolver` (generated-type
construction-readiness registry), `QosResolver` (explicit-QoS minimum path — RELIABLE +
TRANSIENT_LOCAL + KEEP_LAST; real XML-alias resolution deferred to Phase 4/5), `EntityFactory<T>`
(D31.4 ordering: create output writer → `dds::pub::ignore` its handle → create input reader +
forwarding condition → attach → post `TopicEntitiesReady`; failures post `RouteEntityError`),
`RouteTopicRuntime<T>` (forwards valid samples reader→writer on an AWS worker thread; meta-sample
mirroring deferred to Phase 10), and `AsyncWaitSetDispatcher` (sole owner of route condition
attach/detach; D32 blocking-detach teardown barrier). `test_route_forward` proves end-to-end
forwarding across two participants/domains from real discovery, `ROUTE_ENABLED` on the status
stream, and D32 teardown when the source is closed — stable over repeated runs, no leaked
`/dev/shm` segments.

**Deferred (as planned, not gaps):** per-sample `samples_forwarded` is not surfaced into
`RouterStatus` yet (counters never bump revision, D26; the runtime tracks its own count);
multi-type routing (a type-dispatching factory selecting a typed sub-factory by discovered
type_name) — Phase 3 binds one generated type; `dynamic_data` default mode (reframe banner) —
the generated-type fast path ships first by design.

**Docs changed.** `implementation-plan.md` (Phase 3 marked done in the phase table + slice).

---

## D35 — Forwarded ACT payloads use DynamicData (runtime XML-loaded types); generated types stay the admin/fast-path lane (2026-07-09, accepted; resolves the Phase 4 type fork, aligns reframe banner)

**Context.** Phase 3 forwarded a compile-time *generated* type (`RouterStatus`, already in the
build). Phase 4's `control_command` route forwards an ACT type defined only in
`harness/act/node_sim/datamodel/act_types.xml` (`control_command { base_type msg }`, with
`msg.destination : string`). Nothing generates ACT types into the router build, so Phase 4 forced
a fork the plan never resolved: codegen the ACT types vs forward DynamicData. Both validated
supported in 7.7 Modern C++ (`ask_connext_question`, 2026-07-09).

**Decision.** Forwarded application payloads use **DynamicData**, loaded from XML at runtime — the
generic route engine the reframe banner already names as the default (`dynamic_data` default;
`serialized_cdr` a later opt-in). Concretely:

- The router loads the config's `types.xml` via a `dds::core::QosProvider` and resolves a topic's
  `DynamicType` **by registered type name** (`qos_provider.extensions().type("control_command")`).
  This becomes `TypeResolver`'s real job (Phase 3's name-registry seam generalizes to
  `get_dynamic_type(name)` + `is_constructible`).
- A **DynamicData route runtime/factory** sits beside the Phase 3 typed one: `Topic<DynamicData>`
  takes the resolved `DynamicType`; `DataReader<DynamicData>` → `DataWriter<DynamicData>`;
  `take()`/`write(sample.data())` forward generically. The D31.4 create-order, D32 teardown
  barrier, and `RouteView`/completion-event contract are unchanged — only the payload type
  differs, so Phase 3's proven lifecycle/threading carries over.
- **Generated types stay a lane, not the lane:** the router's own admin `RouterStatus` (and any
  future keyed lifecycle route wanting typed `key_value`) uses the generated path; forwarded app
  payloads use DynamicData. This is the eligibility-gated fast path Phase 9 frames — it is not
  removed, just no longer the default.

**Data model is reference-only (clarified 2026-07-09).** `act_types.xml` is an *example*
datamodel; the router is a generic relay and the deliverable/tests may use **any** data model.
This reinforces DynamicData: the router is coupled to no compiled application type. It also
removes the earlier "verify `QosProvider` loads `act_types.xml`" caveat — Phase 4 authors its own
clean DDS-type XML (rooted `<dds><types>`, `rti_dds_profiles.xsd`/`rti_dds_topic_types.xsd`) for
the example type it forwards, so the load shape is controlled by us, not inherited from the
Routing-Service-schema `act_types.xml`. The only compiled types in the router remain its own admin
types (`RouterStatus` etc.).

**Docs changed.** `implementation-plan.md` (Phase 4 deliverables/evidence; forwarding-mode note);
`thesis-and-tenets.md` dynamic-data-default line is now the active path (no edit needed — this
realizes it).

---

## D36 — Route/participant/QoS config parsed with vendored yaml-cpp (FetchContent), not a hand-rolled nested parser (2026-07-09, accepted)

**Context.** Phase 0's `RouterIdentity` reader is a dependency-free *flat* line parser (it only
reads `node.*`/`router.*`). Phase 4's `routes:` are deeply nested (route → `source_side`/
`destination_side` → `input`/`output` → `filter` → `parameters` list). No system `yaml-cpp` is
installed (only low-level C `libyaml`).

**Decision.** Add **yaml-cpp** via CMake `FetchContent` for the `RouteConfigParser`
(routes/participants/QoS sections). Rationale: nested-YAML hand-parsing is a well-known
bug factory (indentation, lists, quoting, anchors); the config is the router's contract with the
harness and must parse correctly. The build stays local per vboxsf rules (FetchContent builds into
the local `build/` tree, never the share). The Phase 0 identity reader may stay as-is or migrate to
yaml-cpp later — not required by this decision.

**Docs changed.** `implementation-plan.md` (Phase 4 `RouteConfigParser` deliverable notes yaml-cpp);
`configuration.md` can note the dependency on its next pass.

---

## D37 — Phase 4 confidence: high, with a pinned internal build order (2026-07-09, accepted)

**Decision.** With D35/D36 pinned and the content-filter surface validated (nested `msg.destination
= %0`, string param quoted as `"'Platform_30'"`, `ContentFilteredTopic<DynamicData>` supported),
Phase 4 is **high confidence**. To keep it high, build it in this order so the hardest Connext
piece is proven before the config plumbing:

1. **DynamicData forwarding smoke** — reuse the Phase 3 harness shape but forward a router-authored
   example type (a struct with a nested `destination` string) as DynamicData loaded from a clean
   DDS-type XML, across two participants/domains. Proves type-load + DynamicData runtime + D32
   teardown for the new payload path. (Data model is reference-only — D35 — so we control the XML.)
2. **ContentFilteredTopic on the input reader** — add the `<field>.destination = %0` filter with a
   runtime-substituted node-name parameter; prove Platform_30 receives only its own commands and
   Platform_31's are filtered.
3. **`RouteConfigParser` (yaml-cpp)** — parse `routes:`/`participants:`/QoS sections into
   `RouterRouteSpec`, with `source_side`/`destination_side` selected by local `node.role`; load the
   same config on both control and platform instances and assert each selects the right legs
   (`control_lan→control_wan` vs `platform_wan→platform_lan`).

Steps 1–2 are the Connext risk and get focused tests; step 3 is mechanical once yaml-cpp is in.

**Docs changed.** `implementation-plan.md` (Phase 4 confidence + build-order note).

---

## D38 — Phase 4 shipped and test-verified (2026-07-09, accepted; closes Phase 4)

**Decision.** The Phase 4 runtime is implemented and green, following the D37 order:

- `TypeResolver` gained the DynamicData lane: `load_types(xml)` (QosProvider) +
  `get_dynamic_type(name)`; the generated-type registry (Phase 3) is untouched (D35).
- `DynamicRouteFactory` (`IEntityFactory`) forwards `DynamicData` for one XML-loaded type,
  reusing `RouteTopicRuntime<DynamicData>` — same D31.4 create-order (`ignore` the output
  writer first) and D32 teardown barrier as the Phase 3 typed factory, only the payload
  type differs. When the input endpoint carries a filter it builds a
  `ContentFilteredTopic<DynamicData>`.
- `RouteConfigParser` (yaml-cpp, D36) parses `routes:`/`participants:`, selects
  `source_side`/`destination_side` by local `node.role`, and substitutes+quotes
  `${node.name}` into the SQL filter param.
- `config/example_types.xml` is the router-authored reference data model (D35).

Evidence (all passing, stable over repeated runs, no `/dev/shm` leaks): `dynamic_forward`
proves DynamicData forwarding across two participants/domains from real discovery, the
`msg.destination = %0` content filter (Platform_30's sample forwarded, Platform_31's
dropped), and D32 teardown on source loss. `route_config` proves both role selections from
the real `control-platform.yaml` (`control_lan→control_wan` on the control node,
`platform_wan→platform_lan` on the platform node) plus filter-param substitution to
`'Platform_30'`. 8/8 targets green.

**Deferred (not gaps):** end-to-end wiring of parsed config → live controller/factory in one
process is the command-loop/harness phases (6/11); Phase 4 proves the parser and the DDS
forwarding halves with focused tests per the D37 order. Multi-type dispatch (factory picks
the DynamicType by discovered type_name) and LAN `auto` QoS (Phase 5) remain deferred as
planned.

**Docs changed.** `implementation-plan.md` (Phase 4 marked done in the phase table + slice).

---

## D39 — Phase 5 QoS is asymmetric and static: weakest-request input readers, strong-offer output writers with in-place deadline derivation; residual RxO mismatches warn, never adapt (2026-07-10, accepted; bounds refined by D19/D27)

> **Amended by D42:** the output writer additionally derives **liveliness** (kind + lease)
> from the matched readers at creation, and the fixed-TL offer is recognized as already
> *being* the durability auto-match. The residual warning set shrinks accordingly.

**Decision.** Reader-side `auto` QoS derivation is **deleted, not implemented**. The RxO
asymmetry does the matching work (Tenet 9):

- **Route input readers use one fixed weakest-request profile**: `BEST_EFFORT` +
  `VOLATILE` + default deadline/latency_budget/liveliness/destination_order/presentation,
  `DataRepresentation` as the union of accepted representations. By RxO construction this
  matches **every** discovered writer, present or future — so reader-side QoS immutability
  (the "snapshot at resolve time" risk) stops existing on the input side. LAN reliability
  is not needed generally (user), and the `VOLATILE` input means only samples published
  before the route reader existed are lost at the input hop; downstream late-joiners are
  still served by the route **writer's** `TRANSIENT_LOCAL` cache, so the loss window is
  app-writer-start → route-creation (discovery-driven, short). Stated as contract, not bug.
- **Route output writers offer a fixed strong baseline** — `RELIABLE` + `TRANSIENT_LOCAL`
  (a strong offer matches all weaker readers; best-effort readers cost nothing since they
  don't ack) — and derive exactly **one** policy from discovered local readers:
  **deadline** (offer = min requested period across matched readers). Deadline is
  **mutable after enable**, so a later reader with a tighter deadline is accommodated
  in place via `set_qos` — no entity recreation, no D32 teardown cycle.
- **Residual immutable mismatches warn, never auto-adapt**: Ownership (RxO is
  **equality** — a SHARED-default reader/writer never matches an EXCLUSIVE peer),
  durability requested above `TRANSIENT_LOCAL`, liveliness kind above the offer,
  coherent/ordered presentation. First-resolved-wins + loud warning (D20 precedent);
  recreate-on-stronger-request is a documented non-goal until someone needs it.
- **Detection is DDS-native**: `REQUESTED_INCOMPATIBLE_QOS` (route reader) and
  `OFFERED_INCOMPATIBLE_QOS` (route writer) enabled on the entities' `StatusCondition`s,
  attached to the existing `AsyncWaitSet` beside the ReadConditions; the status carries
  `last_policy_id`, so the warning/route-status reason names the failing policy. This
  closes code-review finding **F5** (forced RELIABLE+TL → silent no-match).
- History/resource_limits stay alias/default-supplied (D19 — reconfirmed absent from
  builtin discovery data in 7.7).

**Validated against Connext 7.7** (`ask_connext_question`, 2026-07-10):
- Immutable after enable: Reliability, Durability, Liveliness, Ownership,
  DestinationOrder, Presentation, DataRepresentation. Mutable: **Deadline**,
  **LatencyBudget**.
- RxO reduction directions (recorded for the writer side and any future derivation):
  min kind for Reliability/Durability, **max** period for Deadline/LatencyBudget,
  weakest kind for Liveliness/DestinationOrder, **union** for DataRepresentation,
  **equality** for Ownership (no ordering).
- `PublicationBuiltinTopicData`/`SubscriptionBuiltinTopicData` do **not** propagate
  History or ResourceLimits (D19 stands; an initial contrary MCP aside was retracted on
  targeted follow-up).
- Incompatible-QoS statuses are enableable on a `StatusCondition` attached to an
  `AsyncWaitSet` alongside ReadConditions, with `last_policy_id` + per-policy counts.

**Rejected.** Per-policy reader derivation from discovered writers (the plan's original
shape): every derived immutable policy re-creates the snapshot problem a weakest-request
reader dissolves for free, and it needs the full reduction table live in the controller.
Recreate-on-late-weaker-endpoint: a D32 teardown/rebuild cycle per QoS surprise, for cases
the rig doesn't have.

**Docs changed.** `implementation-plan.md` (Phase 5 slice rewritten to the asymmetric
contract; phase-table row + confidence notes; Phase 5 investigation row resolved).

---

## D40 — Phase 5 confidence: high, gated on the factory unification and rebuild-leak fixes landing first (2026-07-10, accepted)

**Decision.** With D39 pinned, the Connext-hard parts of Phase 5 are all validated —
what remains is controller policy plus known entity plumbing, so Phase 5 is **high
confidence**, with sequencing against the open Phase 3–4 code review
(`phase3-4-code-review.md`):

1. **F10 → F6 first**: unify the two drifting `IEntityFactory` bodies into one skeleton
   parameterized by a type-lane strategy, and pick the single canonical QoS-alias location
   — so Phase 5's QoS logic lands **once**, not twice, and the alias split stops being
   masked the moment alias lookup exists.
2. **F1, F4 (and F3) before or with the phase**: readiness gating means the writer is
   created/destroyed on discovery edges, so create/teardown cycles multiply — the CFT and
   Publisher/Subscriber rebuild leaks go from latent to hot, and the abort-path stale-error
   guard (F3) sits on the wait-then-activate seam Phase 5 exercises.
3. **F5 closes as part of the phase** (D39's StatusCondition detection is the fix).

Output readiness itself reuses existing machinery: the D20/D22 per-topic matched-endpoint
sets already know whether a compatible local reader exists; the phase adds the gate and the
deadline derivation, nothing structurally new.

**Docs changed.** `implementation-plan.md` (Phase 5 confidence High in phase table +
banner; confidence notes).

---

## D41 — Phase 3–4 code review resolved: shared factory skeleton, endpoint-level QoS alias, per-build entity ownership, participant-loss endpoint purge (2026-07-10, accepted; executes D40 items 1–2, resolves phase3-4-code-review.md F1–F10)

**Decision.** All ten findings of `phase3-4-code-review.md` are fixed; the choices pinned:

- **One factory skeleton (F10)**: `RouteEntityFactory<T>` owns the D31.4 create-order,
  the content-filter branch, teardown/abort (D32), and completion reporting (D21/D23)
  once; `EntityFactory<T>` (generated lane) and `DynamicRouteFactory` (DynamicData lane)
  are thin bindings supplying only `ensure_type_available()` + `make_topic()`. The
  generated lane thereby gains the CFT branch it silently lacked.
- **Canonical QoS-alias location is the endpoint spec (F6)**: `input.reader_qos` /
  `output.writer_qos`, matching the YAML schema (D36). `RouterRouteTopicSpec` lost its
  unused alias slots, and auto-QoS detection is now `route_uses_auto_qos(spec)` at route
  scope (was per-topic — a granularity nothing could configure).
- **The runtime owns the whole build (F1/F4)**: `RouteTopicRuntime` holds and closes the
  per-build Publisher, Subscriber, and ContentFilteredTopic (close order: condition,
  reader, writer, CFT, subscriber, publisher — validated against 7.7: closing the reader
  does not delete the CFT; explicit `cft.close()` frees the fixed `"<topic>_cft"` name
  for the next build). Rebuild proven end-to-end by the extended `dynamic_forward` leg
  (new source after teardown → route re-enables → filtered forwarding resumes).
- **Stale-error guard is exact-stamp (F3)**: `apply_entity_error` discards on any
  generation mismatch — the former `entity_generation != 0 &&` escape hatch let an
  aborted build's late error force a topic that had legitimately returned to IDLE into
  sticky ERROR. Deliberately NOT gated on `TOPIC_CREATING`: a runtime error on a
  FORWARDING build carries the live stamp and must still land (per-topic containment,
  D11/D21, as `test_per_topic_activation_two_topics` requires).
- **Participant loss is handle-translated and purges its endpoints (F2/F7/F9)**:
  `on_participant` no longer reads `data()` on invalid samples — the D33 pattern now
  covers all three builtin readers via a `part_handle_guid_` map. Endpoint handle-map
  entries carry the owning participant GUID, so a participant purge erases its endpoints
  and synthesizes the per-endpoint losses D28 says may never arrive; a publication lost
  while still parked pending its participant is dropped instead of being replayed later
  as a phantom discovery.
- **Unresolvable QoS alias fails loudly (F5, interim)**: `QosResolver` throws (→
  `RouteEntityError`, visible sticky topic error) on any alias other than `""`/`"default"`
  until Phase 5 alias lookup plus D39's incompatible-QoS StatusCondition detection close
  the silent no-match structurally.
- **Filter-param quoting is a documented heuristic (F8)**: numeric literals and
  pre-quoted values pass through verbatim; everything else is treated as a string and
  single-quoted. Typed parameter resolution stays with the layer that knows the member
  type (Phase 5+).

Cleanups from the same review: `pump()` reuses a cached `Selector` (safe because the D32
detach barrier stops dispatch before close — validated 7.7); `TypeResolver` no longer
double-looks-up nor derefs a null provider. Deferred, still open (noted in the review
doc): shared test scaffolding extraction; QoS-triple duplication in test helpers.

Evidence: 8/8 targets green, including the new `test_stale_error_after_abort_discarded`
controller case and the `dynamic_forward` rebuild leg (route entities created twice, CFT
recreated by name, samples forwarded after rebuild).

**Docs changed.** `phase3-4-code-review.md` (all findings marked fixed, with notes).

---

## D42 — Output writer also derives liveliness at creation; durability auto-match is the fixed TRANSIENT_LOCAL offer itself (2026-07-10, accepted; amends D39)

**Context.** User: durability and liveliness are the most common QoS conflicts — can they
auto-match on the writer side? The two policies turn out to need opposite treatments.

**Decision.**

- **Durability: no derivation — the fixed offer already IS the auto-match.** RxO direction
  means the `TRANSIENT_LOCAL` offer matches every servable reader (`VOLATILE` and
  `TRANSIENT_LOCAL` requests alike); the common conflict — TL reader vs weaker writer —
  cannot occur against a TL offer. Deriving durability *downward* from matched readers is
  **rejected**: it would re-introduce the snapshot problem D39 dissolved (first matched
  reader VOLATILE → writer created VOLATILE → later TL reader mismatches an immutable
  policy). Requests above TL (`TRANSIENT`/`PERSISTENT`) stay in the warning bucket — the
  router cannot honor them anyway without Persistence Service.
- **Liveliness: derive kind + lease at writer creation.** The D39 baseline (default
  `AUTOMATIC` + infinite lease) fails any finite-lease reader — the common real conflict —
  and cannot be fixed-strong (the "strongest" offer is lease→0, which nothing can honor).
  Output readiness (D39/D40) means the matched readers are known at creation, so derive
  there: **kind = max requested kind, lease = min requested lease** across matched local
  readers. Honoring, by kind:
  - `AUTOMATIC` + finite lease: middleware asserts automatically, zero router machinery
    (`assertions_per_lease_duration`, default 3) — covers the common case.
  - `MANUAL_BY_PARTICIPANT` / `MANUAL_BY_TOPIC`: each forwarded `write()` asserts; for
    quiet topics the route propagates upstream liveliness — `LIVELINESS_CHANGED` enabled
    on the input reader's `StatusCondition` (already attached for D39's incompatible-QoS
    detection) → `writer.assert_liveliness()` while upstream is alive. Downstream thereby
    tracks *source* liveliness through the relay, not merely router-process liveliness.
- Liveliness is immutable after enable, so a **later** reader requesting stronger than the
  created offer falls into the existing D39 first-resolved-wins + warning bucket —
  derivation-at-creation makes that a rare edge rather than the common case.
- Residual warning set after this amendment: Ownership (equality RxO), durability above
  `TRANSIENT_LOCAL`, presentation, and late-joiner liveliness stronger than created.

**Validated against Connext 7.7** (`ask_connext_question`, 2026-07-10): liveliness RxO is
offered kind ≥ requested kind (`AUTOMATIC < MANUAL_BY_PARTICIPANT < MANUAL_BY_TOPIC`) with
offered lease ≤ requested lease; `AUTOMATIC` + finite lease is middleware-asserted with no
app action; `MANUAL_BY_TOPIC` asserts via `write()` and Modern C++
`DataWriter::assert_liveliness()`; upstream transitions are observable via
`LIVELINESS_CHANGED` on the reader's `StatusCondition` on a waitset (notified within one
lease of the change).

**Docs changed.** `implementation-plan.md` (Phase 5 banner/deliverables/evidence: writer
derives deadline **and liveliness**; durability sentence; residual list). D39 gained the
amend note.

---

## D43 — Filter-param quoting decided by YAML author intent (quoted-vs-plain scalar), not value shape (2026-07-10, accepted; supersedes D41's F8 heuristic)

**Decision.** `RouteConfigParser::resolve_filter_param` no longer guesses a filter
parameter's SQL quoting from whether the substituted *value* looks numeric (D41's
`is_numeric_literal` heuristic, which misquotes a numeric-looking string like a node named
`"101"` compared against a `string` member). Instead it reads how the author *wrote* the
YAML scalar: `YAML::Node::Tag()` returns `"!"` for any explicitly quoted scalar
(single- or double-quoted, regardless of content) and `"?"` for a plain/bare one —
confirmed against the vendored yaml-cpp 0.8.0 with a standalone probe. An explicitly
quoted parameter (the shipped configs already write `"${node.name}"` this way) is always
treated as a string; only a plain/bare scalar falls back to the numeric-shape check, which
is now only ever exercised by an actual bare YAML number.

**Why not full DDS-type introspection instead.** The original F8 fix direction ("quoting
belongs where the member type is known") is real but heavier: it needs the parser to defer
quoting to `RouteEntityFactory` (the only place with a resolved type — `DynamicType` via
`TypeResolver`, or `rti::topic::dynamic_type<T>::get()` for the generated lane, both
confirmed reachable via `ask_connext_question`), plus a small filter-expression parser
mapping each `%N` to the member path it's compared against. That's a real design (new
factory hook, new expression tokenizer, `filter_parameters` changing from pre-quoted to
raw) worth its own decision if a config ever needs it. The author-intent signal solves the
actual reported failure (a numeric-looking identifier) with a ~10-line, YAML-only change
and no cross-layer plumbing — the router's one real use case (`msg.destination` filtered
by a node identifier like `PLATFORM232`) never needed type introspection to begin with.

**Residual gap.** A plain/bare `${node.name}` (author omits quotes) substituting to a
purely numeric node name is still misquoted — two independent author choices working
against each other, materially narrower than D41's original gap, and avoided entirely by
the config convention already in use (quote the templated parameter).

**Evidence.** `test_route_config` gained a case: node name `"101"` substituted into the
quoted `"${node.name}"` parameter is asserted to come out `'101'`, not `101` (would have
failed under D41's heuristic — `is_numeric_literal("101")` is true).

**Docs changed.** `phase3-4-code-review.md` (F8 marked fixed under the new mechanism).

---

## D44 — Second-pass code-review cleanup: route-qualified CFT names, config-time QoS-alias validation, handle-map/lookup dedup, single dynamic-type lookup (2026-07-10, accepted)

**Decision.** A follow-up review of the D41 diff surfaced six further items (four
correctness/robustness, two reuse) — all fixed:

- **CFT name is route-qualified.** `RouteEntityFactory` named a filtered input's
  `ContentFilteredTopic` only `"<topic>_cft"`. Two different, concurrently-enabled routes
  reading the same topic through the same input participant (a legal config shape — e.g.
  two destinations fanning out from one WAN participant with different filters) would
  collide on that name; the second construction throws `PRECONDITION_NOT_MET` (validated
  7.7), sticking that route in `TOPIC_ERROR` though its own definition is valid. Not hit
  by either shipped config today, but reachable by the schema. Fixed by naming it
  `"<route>_<topic>_cft"` — still stable across same-route rebuilds (the property D41's
  fix depended on), now also unique across routes.
- **QoS-alias resolvability is one shared predicate, checked at config-load time too.**
  `QosResolver::ensure_resolvable` (D41) and a new `RouteConfigParser::validate_qos_aliases`
  both call `is_resolvable_qos_alias` (new `QosAliasPolicy.hpp`, dependency-free) instead of
  each carrying its own copy of the `""`/`"default"` rule. `validate_qos_aliases` is **not**
  called by `parse_route_config` itself (parsing stays purely syntactic) — it's there for
  whatever eventually wires a `RouteConfig` into the real route-building pipeline to call
  first, so an unresolvable alias (both shipped configs use `wan_event`/`wan_status`/
  `lan_status_1hz`, which `QosResolver` cannot yet honor) fails once with one clear message
  naming the route and alias, instead of as N per-topic sticky errors discovered piecemeal
  at runtime. `test_route_config` gained a case proving it rejects `control-platform.yaml`
  today, so the gap is an explicit, tested fact rather than a silent one.
- **`find_topic_spec` unified.** `RouteEntityFactory` and `RouterController` each
  linear-scanned a `RouterRouteSpec`'s topics by name independently — same underlying
  vector, reached through different wrapper types (`RouteView`/`RouteState`) that both
  happen to just be a `RouterRouteSpec`. Replaced with one free function in
  `RouterState.hpp/.cxx`; both call sites now pass their `RouterRouteSpec` directly
  (`view.spec`, `route.desired`).
- **`take_lost_guid` unified.** The two overloads existed only because `part_handle_guid_`
  was typed `map<string,string>` while `pub_handle_guid_`/`sub_handle_guid_` were
  `map<string,EndpointIdentity>`. Participants now use `EndpointIdentity` too
  (`participant_guid` simply left unset — a participant has no "owner"), collapsing to the
  one overload.
- **Lock-hygiene in `DiscoveryDispatcher`.** String formatting (`handle_str`/`format_key`)
  is now computed before acquiring `table_mutex_` rather than inside the critical section
  (`on_participant`'s valid branch, `on_subscription`), and `handle_publication_sample`
  takes the caller's already-computed `participant_guid` as a parameter instead of
  recomputing `format_key(data.participant_key())` under the lock. The two full-map-scan
  functions (`purge_participant_endpoints_locked`, `drop_pending_publication`) were left
  as-is — indexing endpoints by owning participant would remove the scan but adds real
  structural complexity, not justified without an actual endpoint-count scale requirement.
- **Dynamic lane's double type lookup reduced to one call.** `DynamicRouteFactory::
  ensure_type_available` now calls `TypeResolver::get_dynamic_type` directly instead of
  `has_dynamic_type` (which itself wraps the identical lookup in a try/catch just to return
  a bool), letting its own exception propagate. `make_topic`'s separate lookup is
  unavoidable (it needs the `DynamicType&` to construct the `Topic`) but only runs on a
  topic's first build, not every rebuild (`find_or_create_topic` skips it once the topic
  exists) — confirmed low-cost/low-frequency, so not worth caching at construction (which
  would require `load_types` to run before the factory is constructed, changing today's
  graceful "not yet loaded → per-topic `RouteEntityError`" behavior into a constructor
  throw).

**Evidence.** 8/8 test targets still green; `test_route_config` gained the QoS-alias
validation case.

**Docs changed.** None beyond this entry — no findings doc existed for this second-pass
review (informal follow-up, not a separate high-effort review run).

---

## D45 — Phase 5 shipped and test-verified: output-side gate, derived writer QoS, DDS-native warnings; XML alias lookup re-pinned to Phase 7 (2026-07-10, accepted; closes Phase 5, refines D39/D41/D42)

**Decision.** Phase 5 is implemented per D39/D42 with three refinements surfaced by the
pre-implementation review:

- **The readiness gate is OUTPUT-side only.** `route_uses_auto_qos` (both endpoints
  alias-less) became `output_uses_auto_qos` (`spec.output.writer_qos.empty()`): under the
  asymmetric contract only an auto output writer depends on discovery (it derives
  deadline/liveliness from the matched local readers at creation); the auto input
  reader's weakest-request profile matches every writer by RxO construction and never
  gates. The old both-sides predicate would have skipped the gate for the shipped
  configs' destination sides (named `reader_qos` + auto output).
- **XML QoS-alias lookup (QosProvider profiles) is deferred to Phase 7, not Phase 5.**
  Three code comments had promised it for Phase 5, but no Phase 5 deliverable or
  evidence item exercises a named alias — the first consumers (`wan_event`/`wan_status`
  routes) ship in Phase 7. Resolvable aliases stay `""`/`"default"`
  (QosAliasPolicy.hpp); `validate_qos_aliases` still rejects the shipped configs as a
  tested, explicit fact (D44). Comments updated to say Phase 7.
- **Quiet-MANUAL liveliness residual (documents a D42 bound).** Propagation is
  assert-on-forwarded-write (middleware-automatic for MANUAL kinds) plus
  `assert_liveliness()` on upstream ALIVE transitions via `LIVELINESS_CHANGED`. An alive
  upstream's periodic manual asserts do NOT re-fire `LIVELINESS_CHANGED` (no transition),
  so a quiet MANUAL-kind route writer past its derived lease shows not-alive downstream
  until traffic or a transition resumes — downstream liveliness therefore means
  "upstream alive AND recently forwarding". True periodic re-assertion would need a
  router-owned timer; deliberately out of scope until a real topic needs it. The common
  case (AUTOMATIC + finite lease, D42) has zero router machinery and is test-verified.

**Mechanics shipped** (all validated against 7.7 via MCP before implementation):

- `QosResolver`: auto reader = BEST_EFFORT + VOLATILE + DataRepresentation union
  {XCDR, XCDR2} + KEEP_LAST(16) (the router-default history depth, alias-supplied per
  D19); auto writer = RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(16) + derived deadline and
  liveliness. Plus `summarize()` for the status summaries and a runtime
  QosPolicyId→name map (`qos_policy_name` — 7.7 only ships compile-time policy traits,
  so the reverse map is spelled out).
- Derivation is a pure function (`derive_writer_qos`: min deadline, max liveliness
  kind, min lease over `matched_readers`) computed at build-issue time; the offer is
  remembered (`offered_deadline_nanos`) so `apply_subscription` /
  `apply_entities_ready` can tighten a FORWARDING build's deadline **in place** via
  `IEntityFactory::update_writer_deadline` → dispatcher → `writer.qos(q)` — never
  relax, never rebuild. `EndpointRecord`/`MatchedEndpoint` carry the requested
  deadline/liveliness subset (POD nanos; infinite = INT64_MAX) captured in
  `on_subscription`.
- `RouteTopicRuntime` owns the two entity StatusConditions (reader:
  REQUESTED_INCOMPATIBLE_QOS + LIVELINESS_CHANGED-when-MANUAL; writer:
  OFFERED_INCOMPATIBLE_QOS), dispatched on the same AsyncWaitSet;
  `conditions()` replaced the single-condition accessor and the dispatcher
  attaches/detaches them all (each detach is a D32 barrier). Warnings post
  `TopicQosWarning` events (stamp-gated like errors; a `writer:DEADLINE` warning
  already resolved by tightening is dropped — the status can race set_qos).
- Status: `RouterRouteTopicStatus` gained `reader_qos_summary`, `writer_qos_summary`,
  `qos_warning`; all three are in the route fingerprint (revision bumps/publishes) and
  are cleared with the build they describe (teardown/abort/error/re-arm).

**Evidence.** 9/9 targets green. New `test_auto_qos` (real DDS, DynamicData lane) proves
all five phase evidence items in one flow: waits at DISCOVERY_PARTIAL with a source but
no local reader → activates on reader appearance; BE+VOLATILE source forwards;
AUTOMATIC finite-lease reader observes liveliness with no router asserts and the exact
derived summaries ride the status; TRANSIENT reader → `writer:DURABILITY` and EXCLUSIVE
source writer → `reader:OWNERSHIP` warnings with the route still ENABLED; a later 500ms
deadline reader tightens the offer in place (summary flips to `deadline=500ms`, route
never leaves ENABLED, data flows to the new reader). `test_controller_phase1` gained
`test_gate_is_output_side_only`, `test_writer_qos_derivation`, and
`test_deadline_tightening_and_warning`.

**Docs changed.** `implementation-plan.md` (Phase 5 banner → shipped);
`QosResolver.hpp`/`QosAliasPolicy.hpp` comments (alias lookup → Phase 7).

---

## D46 — Controller journal enum trimmed to today's real event set; `JOURNAL_TOPIC_QOS_WARNING` added (2026-07-13, accepted; Phase 6 review)

**Context.** Reading `RouterEvents.hpp` against the `ControllerJournalEventKind` enum in
`RouterAdminTypes.idl`/`command-status.md` before starting the Phase 6 review surfaced drift:
`JOURNAL_ROUTE_DATA_READY` and `JOURNAL_SHUTDOWN_REQUESTED` have no matching
`ControllerEventKind` today, and `TopicQosWarning` (added by D39/D45, after the journal enum
was written) has no `JOURNAL_*` counterpart at all. Neither type is referenced anywhere in
`router/src` or `router/test` yet, so the enum was safe to correct directly rather than carry
the drift into Phase 6.

**Decision.** `JOURNAL_ROUTE_DATA_READY` and `JOURNAL_SHUTDOWN_REQUESTED` are **dropped**:
no `RouteDataReady`/`ShutdownRequested` controller event exists, per-sample journaling would
turn the debug journal into a firehose (contradicts "one sample per processed controller
event"), and shutdown is handled procedurally today (`Concurrency Rules`' ordered
stop-intake/detach/close sequence), not through the event queue. `JOURNAL_TOPIC_QOS_WARNING`
is **added** so every `ControllerEventKind` the controller actually processes has a journal
counterpart. The enum is now exactly the journaled event set, no forward-looking
placeholders.

**Docs changed.** `command-status.md` (`ControllerJournalEventKind` in the Admin IDL Sketch —
the authoritative copy); `RouterAdminTypes.idl` (transcribed copy, kept in sync per its own
header comment).

---

## D47 — Command target filtering is a ContentFilteredTopic on `target_node`/`target_router`, not an app-level check (2026-07-13, accepted; Phase 6 review, refines code-architecture.md's CommandHandler wording)

**Context.** `code-architecture.md`'s `CommandHandler` bullet says it "performs cheap
target/idempotency checks" — read literally that's an app-level field compare, not a DDS
filter. But Tenet 9 (simplicity first — prefer DDS-native mechanisms) lists "CFTs for
filtering" as a first-class example, and Phase 4 already validated exactly this shape (D37:
`msg.destination = %0`, single-quoted string parameter) for the platform-destination route
filter.

**Decision.** The command reader uses a **ContentFilteredTopic** on `RouterCommand`
(`target_node = %0 AND target_router = %1`), reusing the D37/D43 quoting approach. Unlike
the D37 CFT, the two parameter values here are this router's own identity strings
(`RouterIdentityInfo::node_name`/`router_name`), known at construction time as plain
`std::string` — not YAML scalars — so there is no D43 quote-vs-plain ambiguity to resolve;
the parameters are simply wrapped in single quotes directly. `command_id` idempotency stays
app-level in the controller (D4/D8) — DDS cannot do that part, per Tenet 9's own carve-out.
This keeps the reader thin: a non-matching command never reaches the callback at all, rather
than being received and discarded by an `if`.

**Docs changed.** `code-architecture.md` (`CommandHandler` bullet: CFT-based target
filtering, idempotency stays app-level); `command-status.md` (command reader construction
note).

---

## D48 — `RouterCommand`/`RouterCommandAck` QoS: `RELIABLE` + `VOLATILE` + `KEEP_LAST(16)` on both (2026-07-13, accepted; Phase 6 review)

**Context.** Neither topic's QoS was pinned anywhere before Phase 6. Commands are
control-plane traffic that must not be silently dropped (favors `RELIABLE`), but durability
was an open question — does a late-joining sender/observer need history replay?

**Decision.** Both the command reader and the ack writer are `RELIABLE` + `VOLATILE` +
`KEEP_LAST(16)`. No durability is needed on either side: duplicate `command_id` replay
already happens at the app layer via the controller's bounded ack cache (D4) — a sender that
resends the same `command_id` gets the cached ack republished regardless of whether the
original ack sample is still available from DDS history. Depth 16 absorbs a burst of
commands (e.g., several `ENABLE_ROUTE`s at startup) without needing `KEEP_ALL`.

**Docs changed.** `command-status.md` (new QoS paragraph for the command/ack topics,
alongside the existing D26 status-writer QoS paragraph).

---

## D49 — Controller journal writer: `RELIABLE` + `KEEP_LAST(256)` with the reliable send window `LENGTH_UNLIMITED`; backlog observed via `RELIABLE_WRITER_CACHE_CHANGED_STATUS` (2026-07-13, accepted; Phase 6 review)

**Context.** `command-status.md` required the journal writer to never block the controller
thread on backpressure, with an explicit drop/backlog policy — left unresolved pending
validation.

**Validated against Connext 7.7** (`ask_connext_question`, 2026-07-13): a `RELIABLE` writer's
`write()` can block for two independent reasons — a full reliable send window
(`rtps_reliable_writer.{min,max}_send_window_size`), or (with `KEEP_ALL`) exhausted
`resource_limits` with no fully-ACKed sample to evict. With `KEEP_LAST`, history fullness
alone never blocks — the writer evicts the oldest sample in the instance/cache and returns
success regardless of ACK state — so the **only** remaining blocking path is the send
window. Setting `rtps_reliable_writer.max_send_window_size` /
`min_send_window_size` to `LENGTH_UNLIMITED` (the documented `BuiltinQosLib::
Generic.KeepLastReliable` pattern) removes that path too: `write()` never blocks, at the cost
of old unacknowledged samples being overwritten under sustained backpressure — the right
tradeoff for a debug journal attached to the controller's strand. `PUBLICATION_MATCHED_STATUS`
only reports attach/detach of a debug reader, not whether it's keeping up;
`RELIABLE_WRITER_CACHE_CHANGED_STATUS` (high/low watermark on unacknowledged sample count) is
the correct backlog signal.

**Decision.** Journal writer QoS: `RELIABLE`, `KEEP_LAST(256)`, reliable send window
`LENGTH_UNLIMITED`. A `StatusCondition` on `RELIABLE_WRITER_CACHE_CHANGED_STATUS` is wired
from Phase 6 (not deferred): crossing the high watermark logs
`Log::warn("journal_falling_behind", ...)`. This is observability-only — it never feeds back
into route control (code-architecture.md's existing constraint).

**Docs changed.** `command-status.md` (journal writer QoS paragraph, replacing the
"must be explicit" open item with the resolved shape); `code-architecture.md`
(`ControllerJournalPublisher` bullet: QoS + backlog-monitoring note).

---

## D50 — `router_main` wired to run real config-driven routes; QoS-alias/multi-type dispatch stay deferred; first Python e2e coverage uses dedicated single-type fixtures (2026-07-13, accepted; closes the router_main wiring gap ahead of the Python e2e suite)

**Context.** Phases 0-5 (D1-D45) were proven only inside ad-hoc `main()` functions of the
C++ test binaries (`test_route_forward.cxx`, `test_dynamic_forward.cxx`, etc.) — the actual
shipped `router_main.cxx` remained the Phase-0 skeleton: it validated config, printed one
log line, and exited, creating zero DDS entities. That made it impossible to write real
end-to-end tests (Python or otherwise) against the actual router process, since there was
no real, standalone router to subprocess-launch.

Two pre-existing, deliberately deferred library boundaries constrain what a wired
`router_main` can run today, independent of this decision:
- `QosAliasPolicy`/`QosResolver` only resolve `""`/`"default"` — named aliases (`wan_event`,
  `wan_status`, `control_wan_udpv4_qos`, ...) are unresolvable until Phase 7's QoS-library
  lookup (D45).
- `DynamicRouteFactory` binds **one** DynamicData type for its entire process lifetime
  (D34/D35) — multi-type dispatch by discovered type name is not implemented. A route set
  spanning more than one type cannot run in a single `router_main` process yet.

Both `control-platform.yaml` and `platform-team.yaml` as committed hit at least one of
these (named QoS aliases throughout; `control-platform.yaml`'s `platform_events` route is
also multi-type). `platform-team.yaml`'s flat `input`/`output` route shape (no
`source`/`destination`/`*_side` keys) is additionally unparsed by `RouteConfigParser` today
— consistent with Phase 8 (team partitions) being unstarted, not a regression.

**Decision.**
1. `router_main.cxx` now assembles the real object graph already proven in the Phase 3-5
   test mains — `RouteConfigParser` → `TypeResolver` → `ParticipantRegistry` →
   `DdsStatusPublisher` → `AsyncWaitSet`/`AsyncWaitSetDispatcher` → `QosResolver` →
   `DynamicRouteFactory` → `RouterController` → `DiscoveryDispatcher` — runs until
   `SIGINT`/`SIGTERM` (mirroring the existing `relay/cpp/isc_relay.cxx` signal idiom), then
   shuts down in the proven order (`route_disp.shutdown()` → `discovery.shutdown()` →
   `drain.stop()` → `aws.stop()`). `--role`/`--node-name` CLI overrides let one config file
   be launched twice, once per node role, matching how `control-platform.yaml` is meant to
   be loaded (once on the control node, once on each platform node).
2. `RouteConfig` gains `types_xml_path`/`qos_library_paths` (parsed, not yet applied beyond
   a fail-fast existence check) and `router.type_name` — the one DynamicData type name this
   process's `DynamicRouteFactory` binds to. If a config has active routes but no
   `type_name`, `router_main` refuses to start rather than silently building the wrong type
   (the D34/D35 guard).
3. Path resolution: `types.xml`/`qos_libraries` paths in the YAML are repo-root-relative (as
   literally authored), so `router_main` requires `cwd` == repo root and fails fast naming
   the resolved path if a file is missing, rather than inventing a path-rewriting rule the
   YAML wasn't authored for. `router/README.md`'s run examples are corrected accordingly.
4. Two real, in-scope bugs fixed: `control-platform.yaml`'s `platform_primary_status` route
   named its topic `PlatformStatus`; the real ACT system (`harness/act/node_sim/python/*`,
   `act_types.xml`) uses `PlatformPrimaryStatus` — corrected. The stale "Phase 0" status
   blurb in `router/README.md` is updated to Phases 0-5 shipped / Phase 6 next.
5. Since neither committed production YAML can run as a single process today (item above),
   two dedicated fixtures are added for the Python e2e suite:
   `router/config/e2e_control_command.yaml` and `router/config/e2e_platform_status.yaml` —
   each mirrors `control-platform.yaml`'s topology for exactly one route/type, with
   `qos: ""` (exercising the real Phase 5 auto-QoS, not a fixed alias) instead of named
   aliases. This is the same escape hatch the project's own confidence notes already
   endorse ("use `dynamic_data` for the first working route... don't block earlier slices
   on deferred items") — applied to test fixtures rather than route implementation.
6. `DrainThread` (duplicated verbatim in both C++ test mains) is promoted to
   `router/src/core/DrainThread.hpp` and reused by `router_main.cxx` and both tests — pure
   refactor, no behavior change.

**Docs changed.** `router/README.md` (build/run instructions, status section).
`docs/cpp_router/implementation-plan.md` (Phase 6 contract note corrected: `router_main` is
wired, just without a command/status admin channel yet).

**Not done here (tracked, not silently skipped).** Phase 7 QoS-alias/XML lookup; D34/D35
multi-type dispatch; `platform-team.yaml`'s flat-route-shape parsing (Phase 8);
`participant_partition` application in `ParticipantRegistry`. Running the real
`control-platform.yaml`/`platform-team.yaml` end-to-end through `router_main` needs those
items first.

---

## D51 — Auto-QoS output-writer readiness gate deadlocks two independent `router_main` processes on a WAN hop; e2e fixtures route around it with `writer_qos: default` (2026-07-13, accepted; D50 code-review follow-up)

**Context.** Bringing up the two `router_e2e` fixtures end-to-end (D50) hung indefinitely:
neither router process's route ever left `DISCOVERY_PARTIAL`. Root cause is in
`RouterState.cxx::derive_topic_discovery` (Phase 5, D39 — not touched by D50, pre-existing):
an auto-QoS (`""`) output writer's `qos_resolved` requires `!topic.matched_readers.empty()`
before the topic can build at all, so it can derive the writer's deadline/liveliness from an
already-discovered reader. That holds trivially in the Phase 3-5 C++ test mains (an external
sink reader always pre-exists, independent of the router under test), but breaks when **two
separate `router_main` processes** each own one side of a WAN hop with `""` on the
WAN-crossing leg: each side's output writer needs a matched reader to build, and the only
possible match is the *other* side's not-yet-built reader — neither side can ever go first.

`RouterController::maybe_tighten_deadline` already implements the right shape of fix (build
now with a safe default, correct in place once a real reader appears) but only runs once a
topic has already reached `TOPIC_FORWARDING`/`TOPIC_CREATING` — it is never invoked to
unblock the initial `TOPIC_IDLE` → `TOPIC_CREATING` transition `derive_topic_discovery`
gates. Extending that pattern to the initial transition is the deeper fix, but it changes
already-shipped, already-tested Phase 5 semantics (`test_auto_qos.cxx`) and needs its own
design pass — out of scope for closing out D50's e2e baseline.

**Decision.** Route around the gate in config, not in the library: `writer_qos: default` on
the WAN-crossing output leg (`source_side.output`) in both `router/config/
e2e_control_command.yaml` and `e2e_platform_status.yaml`, instead of leaving it empty/auto.
`output_uses_auto_qos()` is a plain `.empty()` check on the alias string, so a non-empty
`"default"` bypasses the gate entirely — no core library change. LAN-local legs (matched by
a real, always-present external reader/writer, exactly like the C++ test rigs) keep `""`.

**Verified not live in production shape.** `control-platform.yaml`'s WAN-crossing legs
already use non-empty named aliases (`wan_event`, `wan_status`), so `output_uses_auto_qos()`
already returns `false` there today, independent of Phase 7 — the production config was
authored to sidestep this gate by construction, same pattern as this fix. The gate remains a
real, general landmine for any *future* route/config that puts `""` on a WAN-crossing leg
between two independent `router_main` processes — not fixed, only avoided for the two
fixtures.

**Also fixed this pass (code-review follow-up, same session as D50):**
- `router_main.cxx` force-includes the admin participant regardless of its own `role:` tag
  (needed so command/status always has a home participant); this now also validates that
  tag against `cfg.node_role` when non-empty (`admin_participant_role_mismatch`), so a
  role-inconsistent admin participant fails fast instead of silently binding the wrong LAN.
- `RouterController` is now constructed with the same role-filtered participant list used to
  build `ParticipantRegistry`, not the full unfiltered `cfg.participants` — the published
  `RouterStatus` no longer lists participants this process never actually created.
- `has_participant()` (previously anonymous-namespace-scoped in `RouteConfigParser.cxx`) is
  exposed via `RouteConfigParser.hpp` and reused by `router_main.cxx`'s admin-participant and
  route-participant checks instead of three independent linear scans.
- `router/test_e2e/conftest.py`'s `RouterProcess.returncode` now calls `poll()` (Python's
  `Popen.returncode` never self-updates); `write_until_seen()` gained an optional
  `check_alive` callback so a crashed router process fails a test immediately with a clear
  message instead of waiting out the full timeout. Both e2e tests pass
  `check_alive=lambda: control_proc.is_alive() and platform_proc.is_alive()`.

**Second flake found while confirming the fix (repeated `pytest` runs, ~50% fail rate):**
even with the deadlock fixed, `test_status_reaches_control` and
`test_command_reaches_only_addressed_platform` failed intermittently, always the same
signature: `publication_pending_participant` followed by `pending_publication_dropped`
~30-40s later in one side's log — that side's `DiscoveryDispatcher` saw the other
router's SEDP endpoint data but never received its SPDP participant announcement.
Validated against Connext 7.7 docs (`ask_connext_question`): default
`participant_liveliness_assert_period` (the periodic SPDP re-announcement, once the
5-message initial burst is exhausted) is **30s**. Two `router_main` processes launched
back-to-back via `subprocess.Popen` can plausibly have one miss the other's brief initial
burst (ordinary process-spawn/exec/dynamic-link jitter), leaving it waiting up to ~30s for
the next periodic announcement — enough to burn through `write_until_seen`'s own timeout
and surface as a confusing "sample never arrived" test failure rather than a discovery-
timing issue. Fixed by adding `conftest.py`'s `wait_for_mutual_discovery()`: `router_pair`
now blocks (up to 35s, polling both router logs for `participant_router_tagged`) until both
processes have actually discovered each other's participant *before* the timed test body
starts, so SPDP variance no longer competes with the test's own timeout for what it's
actually meant to measure (routing, not discovery). `write_until_seen`'s default timeout
was lowered back to 15s accordingly, now that it only needs to cover SEDP/route-build, not
SPDP.

**Repeated `pytest` runs after this mitigation still failed intermittently** (`wait_for_
mutual_discovery` itself timing out at 35s — one side's log showed zero `participant_
router_tagged` lines for the whole run), which prompted checking whether this is really an
environmental/VM multicast reliability issue (as the "30s SPDP retry" theory assumed) or a
bug in the router's own discovery code. **It's the latter.** Two standalone Python probe
scripts (`discovered_participants()`, zero router/`DiscoveryDispatcher`/`AsyncWaitSet`
code) were run as separate OS processes on the same domains `router_main` uses:
- One participant per process (matching nothing router-specific): 5/5 runs, mutual
  discovery in 0.05-1.0s.
- **Two** participants per process, one per domain, exactly matching `router_main`'s real
  LAN+WAN topology (still zero router code): 6/6 runs, mutual discovery in 0.05-1.1s.

11/11 successes, no VM/multicast unreliability at all — ruling out the environment. The
flakiness is real and lives specifically in `ParticipantRegistry`/`DiscoveryDispatcher`/how
`router_main` wires the `AsyncWaitSet` (a likely candidate: a timing/ordering gap between
participant creation, `AsyncWaitSet` condition attachment, and `aws.start()`, though this
is not yet confirmed). **Left as a known, tracked, unresolved issue** — `wait_for_mutual_
discovery()` remains in place as a mitigation (it turns the flake into a slow-but-honest
fixture-setup failure instead of a misleading "sample never arrived" test failure), but the
root cause in the library itself is not fixed. Next step if picked up: instrument
`DiscoveryDispatcher::attach_participant`/`on_participant` to log exactly when each
`ReadCondition` is attached relative to `aws.start()` and participant enablement, and
compare against the timeline of a reproduced failure.

> **Resolved by D52.** The "likely candidate" above was confirmed and fixed: participants
> were enabled at construction, before the builtin-reader conditions were attached and the
> `AsyncWaitSet` was started. See D52.

**Docs changed.** `router/config/e2e_control_command.yaml` and `e2e_platform_status.yaml`
(header comments correctly point here instead of D50); `router/test_e2e/README.md`.

---

## D52 — Disabled startup: create participants disabled, attach conditions + start the AsyncWaitSet, then enable — fixes the D51 flaky-discovery race (2026-07-13, accepted; resolves D51's tracked unresolved issue)

**Context.** D51 left the flaky mutual-discovery failure as a confirmed-but-unfixed
library bug: two back-to-back `router_main` processes intermittently (~50%+ over repeated
runs) failed to discover each other's participant — one side logging
`publication_pending_participant` (it saw the peer's SEDP endpoint) but *zero*
`participant_router_tagged` for the whole run (it never processed the peer's SPDP
announcement). D51's standalone Python probes proved the DDS layer delivers discovery
reliably (11/11), so the fault was in how the router consumes it.

**Root cause (D51's "likely candidate", now confirmed).** `router_main` created its
`DomainParticipant`s **enabled** (`ParticipantRegistry` ctor, then default factory
auto-enable), which starts SPDP/SEDP discovery immediately — a substantial construction
window *before* `DiscoveryDispatcher` attaches the builtin-reader `ReadCondition`s and
`aws.start()` runs. A peer's SPDP announcement arriving in that enable→`start()` window
lands in the participant builtin-reader cache and sets the participant `ReadCondition`'s
`trigger_value` true **before** the `AsyncWaitSet` is dispatching. The AWS's handler
dispatch is effectively **edge-triggered** (it must be, to avoid busy-looping on a
persistently-true `DataState::any()` condition nothing has drained), so a condition
already true at `start()` that never re-transitions false→true is **never dispatched** —
`on_participant` is never called for that peer, its participant stays unknown forever, and
its later-arriving SEDP publication (which *does* produce a genuine post-start transition
on the *publication* condition) is parked pending a participant that will never resolve.
The Connext 7.7 docs describe the `WaitSet`/`ReadCondition` model as level-triggered and
say already-true conditions *should* be serviced (`ask_connext_question`), but that is
documented as inference with no explicit guarantee, and the observed behavior (condition
true for the full 35s yet never dispatched while the AWS was demonstrably running) is
inconsistent with reliable level-triggering. Either way the fix is the same.

**Why the C++ Phase 3-5 test mains never hit it.** `test_route_forward.cxx` /
`test_dynamic_forward.cxx` / `test_auto_qos.cxx` create every discoverable peer (source
writer, sink reader, status probe) **after** `aws.start()`, so no discovery sample can
arrive during the enable→`start()` window — every sample yields a post-start transition
and is dispatched. `test_discovery_smoke.cxx` uses a synchronous `WaitSet` in an explicit
polling loop (genuinely level-triggered, re-checked each iteration) and is likewise
immune. Only two independent `router_main` processes coming up concurrently expose the
race.

**Decision.** Adopt the deferred D12 "disabled startup" ordering, which is also RTI's
recommended pattern (`ask_connext_question`):
1. Create participants **disabled** — `ParticipantRegistry(configs, autoenable=false)`
   flips the process-global `DomainParticipantFactory` `EntityFactory` to
   `ManuallyEnable()` for the construction loop, then restores it (the factory QoS is a
   singleton; restore even on exception so later creation elsewhere is unaffected).
2. Attach the builtin-reader `ReadCondition`s (`DiscoveryDispatcher` ctor) and
   `aws.start()` — both valid while the participant is disabled.
3. `registry.enable_all()` — enable participants **last**. `enable()` recursively enables
   the builtin subscriber/readers and any child entities created while disabled (verified
   for 7.7: recursion holds because the participant's own `EntityFactory` autoenable is
   true, per Users Manual §18.2.1).
Discovery traffic now begins only after the AWS is dispatching, so every sample is a
genuine post-start transition — nothing is stranded.

**`autoenable` defaults to true.** Only `router_main` opts into disabled startup; the C++
test mains keep the enabled default (safe — peers come after `start()`), so this is a
zero-blast-radius change to already-shipped Phase 3-5 tests. As those integration tests
migrate to Python they can be dropped rather than reworked.

**Startup-snapshot follow-on.** `RouterController`'s constructor publishes its revision-0
status snapshot, which now runs while the admin participant is still disabled — a no-op on
a disabled writer. `DdsStatusPublisher::publish` swallows the expected
`dds::core::NotEnabledError` (debug log, not warn), and `router_main` calls the new
`RouterController::republish_status()` after `enable_all()` (before the `DrainThread`
starts, so it cannot race controller state) to put the snapshot on the now-live writer.

**Verified.** New Python regression test `router/test_e2e/test_discovery_startup.py`
asserts *prompt* mutual discovery (both sides log `participant_router_tagged` within 10s —
above SEDP/route-build, far below the 30s SPDP retry the flake hid behind), parametrized
over 6 iterations, deliberately NOT using `router_pair`'s `wait_for_mutual_discovery` mask.
Fixed binary: 6/6 pass, discovery in 0.2-1.4s. Reverting only the `autoenable` flag to
`true` and rebuilding: 2/6 fail with the exact D51 signature (one side stranded,
`control saw platform: False` / `platform saw control: True`), confirming causality. Full
C++ suite (9 tests) and Python e2e suite (8 tests) green; no stray `/dev/shm` segments.
`wait_for_mutual_discovery()` stays as belt-and-suspenders, no longer load-bearing.

**Files changed.** `router/src/core/ParticipantRegistry.{hpp,cxx}` (disabled creation +
`enable_all()`); `router/src/router_main.cxx` (opt into disabled startup; `enable_all()` +
`republish_status()` after `aws.start()`); `router/src/core/RouterController.{hpp,cxx}`
(`republish_status()`); `router/src/core/DdsStatusPublisher.{hpp,cxx}` (`NotEnabledError`
handling + comment); `router/test_e2e/test_discovery_startup.py` (new).

---

## D53 — Action item (proposed, not yet implemented): replace the D15 `user_data` router tag with `participant_name` (ENTITY_NAME) as the router identifier (2026-07-13, proposed — scoping only)

**Context.** D15 gives every router participant `user_data = act.router=<node>/<router>`,
which is programmatically load-bearing, not just descriptive: `DiscoveryDispatcher`
(`extract_router_tag`/`is_same_node`) parses it to (a) ignore same-node sibling
publications (loop safety) and (b) join discovered-participant GUID → `origin_router`, which
feeds the presence roster and the D14 link-stats rollup key. Nothing today sets
`participant_name` (`rti::core::policy::EntityName`), so router participants show up in
RTI Admin Console unnamed (GUID only) — Admin Console reads discovery metadata but does not
decode the `user_data` tag format. Validated against 7.7 (`ask_connext_question`,
2026-07-13): `participant_name` is a distinct QoS from `user_data` (structured
`{name, role_name}`, ≤255 chars each, no uniqueness requirement), is native Admin Console
and RTI-tooling display data, is set-once-before-enable (same effective constraint as the
tag today), and is readable off the same builtin-topic sample `DiscoveryDispatcher` already
reads (`dds::topic::ParticipantBuiltinTopicData::participant_name()`) — no new builtin-topic
type or read path required.

**Requested scope: full replacement**, i.e. `participant_name` becomes the sole router
identifier and `user_data` on router participants goes away, not an additive
display-only field alongside the tag.

**Surfaces this touches (scoped, not yet changed):**

- `router/src/core/ParticipantRegistry.{hpp,cxx}` — `Config::user_data_tag` and
  `make_participant_qos`'s `UserData` QoS insertion become an `EntityName` QoS insertion.
- `router/src/core/DiscoveryDispatcher.{hpp,cxx}` — `extract_router_tag(UserData)`,
  `is_same_node`, and the `own_router_tag_` member all currently parse the composite
  `act.router=<node>/<router>` string; they would read `participant_name()` (returning
  `rti::core::optional_value<std::string>` for `name()`/`role_name()`) instead of
  `user_data()`.
- `router/src/router_main.cxx` — where the composite tag string is assembled today.
- Docs: this entry supersedes D15's tag mechanism (D15's ignore/loop-safety *decision*
  stands; only the identifier field changes) — `code-architecture.md`
  (`ParticipantRegistry`/`DiscoveryDispatcher` bullets), `implementation-plan.md` (Phase 2
  deliverable bullet: "`origin_router` comes from the participant `user_data` tag join"),
  `link-health.md` (D14 rollup-key wording, GUID→router join).

**Open sub-questions to resolve before implementing (not decided here):**

1. **Field mapping.** `EntityName` is two structured fields, not one string. Candidates:
   `name = <router>`, `role_name = <node>` (cleanest — removes the `/`-splitting
   `node_of()` helper in `is_same_node` entirely); or keep a single composite string in
   `name` for minimal-diff parity with today's format. Leans toward the structured mapping
   given it removes string-parsing, but not decided.
2. **Router-participant sentinel.** Today's `act.router=` prefix on `user_data` also acts
   as a namespace guard: `user_data` is a private byte buffer the router owns exclusively,
   so any value starting with that prefix is unambiguously a router tag. `participant_name`
   is not private — any application participant may set its own `EntityName` for its own
   debugging purposes, and nothing here has verified whether ACT platform/control apps
   already do. Without a reserved sentinel (e.g. requiring `role_name == "act.router"`
   rather than free node text), an app that happens to set a `participant_name` could be
   misread as a router by `is_same_node`/the `origin_router` join. **Needs verification
   against the ACT app side before implementation**, not just the router repo.
3. **Re-validation.** D15's ignore/loop-safety mechanics were validated against 7.7 via
   `ask_connext_question` for the `user_data` path specifically; the accessor swap to
   `participant_name()` is mechanically confirmed in this session but the end-to-end
   same-node-ignore and D14-rollup-join behavior off the new field has not been
   test-verified the way D15/D52 were (no standalone spike run yet).

**Fallback if this stalls or the sentinel/collision question can't be resolved cleanly:**
keep `user_data` as the sole *mechanism* identifier (D15 unchanged) and add
`participant_name` **additively**, populated from the same `router_name`/`node_name` already
in `RouterController`'s identity struct, purely for Admin Console display. That is
zero-risk to D15/D14 but does not remove `user_data` as requested here.

**Status.** Scoping only — no code changed by this entry. Not scheduled to a specific
phase; likely bundled with a future discovery/presence-touching phase rather than run as its
own phase, given the small diff surface once sub-questions 1-2 are resolved.

**Docs changed.** This entry only. Implementation (if this proceeds past the open
sub-questions) will also touch `code-architecture.md`, `implementation-plan.md`,
`link-health.md`, and record a follow-up decision here noting D15's mechanism superseded.

---

## D54 — Phase 6 split into 6a (command/status/ack control loop) and 6b (controller journal) (2026-07-14, accepted; Phase 6 implementation-readiness pass)

**Context.** Readiness pass before implementing Phase 6. The command/ack/status loop is
real-DDS wiring around the **already-tested** Phase 1 state machine: a live command source
posts `CommandReceived` into the existing `RouterController::post`, the existing
`handle_command` runs, and the existing `IStatusPublisher::publish_ack` seam (today a
labeled no-op in `DdsStatusPublisher`) gets a real writer. The controller **journal** is the
only part of Phase 6 that needs a *new seam through the already-green Phase 1 controller* —
there is no journal hook today. Bundling the two makes one large diff that mixes low-risk
wiring with a change to tested code.

**Decision.** Phase 6 lands as two slices.

- **6a — control loop.** `CommandReader` (D47 CFT on `target_node`/`target_router`, D48 QoS)
  posting `CommandReceived`; real `DdsStatusPublisher::publish_ack` (ack writer, D48 QoS)
  replacing the no-op; aggregate `RouterStatus` after accepted commands (already the
  controller's behavior — 6a just gives it a live command source and a live ack sink). Covers
  evidence bullets E1–E4 + the new E-CFT item (D56). New Python test
  `router/test_e2e/test_router_admin_commands.py` + config `router/config/e2e_admin_commands.yaml`
  (one route `enabled: false` so the startup-disabled evidence has something to observe).
- **6b — journal.** `ControllerJournalPublisher` + the D55 `IControllerJournal` seam + D46
  enum + D49 QoS + backlog `StatusCondition`. Covers evidence bullet E5 (D56). New Python
  test `router/test_e2e/test_controller_journal.py`.

**Rationale.** Small, independently reviewable diffs; the tested controller is untouched
until 6b, where the change is isolated and additive (D55). Matches the plan's own framing of
the debug recorder as a separable concern (the recorder reader only exists in debug mode).

**Docs changed.** `implementation-plan.md` (Phase 6 banner + Deliver/Evidence split into
6a/6b with named tests); `README` (`router/test_e2e` Phase-6 line → two-slice plan); this
entry.

---

## D55 — Controller journal seam is a nullable `IControllerJournal*` on `RouterController` (2026-07-14, accepted; Phase 6 review)

**Context.** Evidence bullet E5 requires one journal record per processed controller event
carrying the input event, controller decision/outcome, pre/post `state_revision`, affected
route/topic delta, and requested side effects. `RouterController` has no journal hook today.
Reconstructing records externally from the `RouterStatus` + `RouterCommandAck` streams cannot
capture decision/reason/action or the true pre-state, and collapses multi-event bursts — it
does not satisfy the bullet (the rejected alternative).

**Decision.** `RouterController` gains a nullable `IControllerJournal*` (symmetric with
`IStatusPublisher*`), invoked **once per processed event from `process()`** with the full
pre/post context the controller already computes for the D5 change predicate. `nullptr` = no
journaling: **all Phase 1 tests pass `nullptr`**, so the existing 18-case
`test_controller_phase1.cxx` suite stays green unchanged — the seam is additive with zero
behavior change. The real DDS-backed `ControllerJournalPublisher` implements the seam (D46
enum, D49 QoS). The record's `pre_state_revision`/`post_state_revision`/`state_changed` reuse
the before/after fingerprint the controller already takes per event (D5/D23);
`decision`/`reason`/`action` are the handler's own outcome strings.

**Validated.** No Connext API question — this is an internal C++ seam. The record type
(`ControllerJournalRecord`) and enum (`ControllerJournalEventKind`, D46) already exist in
`RouterAdminTypes.idl`.

**Docs changed.** `code-architecture.md` (`ControllerJournalPublisher` bullet + Interfaces
list: names the `IControllerJournal` seam and the nullptr-in-tests rule);
`command-status.md` (journal section: seam note); this entry.

---

## D56 — Phase 6 evidence→test mapping; the D47 CFT target-filter is an explicit evidence item; D49 backlog signal is wired-not-forced (2026-07-14, accepted; Phase 6 review)

**Context.** The plan's Phase 6 evidence bullets were prose, not mapped to named tests the
way Phase 5's were (D45 → `test_auto_qos.py`). Two test-scope calls were open: whether the
D47 CFT target filter gets its own assertion, and whether the D49 backlog signal
(`journal_falling_behind`) is force-tested.

**Decision.** Python e2e, real `router_main`; the probe reads acks + status + journal as
DynamicData via the existing `admin_types_xml` fixture; **"debug mode" = a matched Python
journal reader** (no separate recorder process — the reader's existence is the toggle).

Evidence→test mapping:

- **E1** disabled route in startup status with no entities → `test_router_admin_commands.py`:
  `RouterStatus` at startup shows the `enabled: false` route as `ROUTE_DISABLED`, empty
  `topic_status` / no entities.
- **E2** `ENABLE_ROUTE` → route reaches `ROUTE_WAITING_FOR_DISCOVERY` (no app writer present)
  then `ROUTE_ENABLED` (app writer present); `ack.accepted`; `state_revision` bumps.
- **E3** `DISABLE_ROUTE` → `ROUTE_DISABLED`, topics `TOPIC_IDLE`, forwarding stops;
  `ack.accepted`.
- **E4** duplicate `command_id` → second ack byte-identical to the first; `state_revision`
  unchanged (the D4 cached-ack replay path).
- **E-CFT** (new explicit item): a command whose `target_node`/`target_router` is **not** this
  router never changes route state and draws no ack — the D47 CFT drops it before the
  callback. Without this assertion the CFT is untested.
- **E5** journal (6b, `test_controller_journal.py`): with the Python journal reader matched,
  driving a command + a discovery event produces `ControllerJournalRecord` samples with the
  expected `event_kind`, pre/post revision, route/topic, and decision/action; route
  behavior/status is identical to the 6a run whether or not the journal reader is attached.

D49 backlog signal: the `RELIABLE_WRITER_CACHE_CHANGED_STATUS` `StatusCondition` +
`journal_falling_behind` log line are **wired** in 6b, but backlog is **not force-produced** —
deterministically stalling a `KEEP_LAST(256)` / unlimited-send-window writer is impractical in
a functional test. 6b asserts `journal_falling_behind` is **absent** under normal load (no
false positive). Real backpressure verification is deferred to a future stress/soak phase
(recorded as a confidence-increasing investigation, not a Phase 6 gate).

**Docs changed.** `implementation-plan.md` (Phase 6 Evidence rewritten as the mapped list,
split 6a/6b); this entry.

---

## D57 — Phase 6 slice 6a (command/ack/status control loop) IMPLEMENTED (2026-07-14, accepted; closes 6a per D54/D56)

**What shipped.** The D54 slice 6a landed against the D56 test plan:

- `CommandReader` (`router/src/core/CommandReader.{hpp,cxx}`): a `RouterCommand` reader on
  the admin participant through a `ContentFilteredTopic`
  (`target_node = %0 AND target_router = %1`, this router's identity single-quoted, D47) +
  `RELIABLE + VOLATILE + KEEP_LAST(16)` (D48); its `ReadCondition` is attached to the shared
  `AsyncWaitSet` and its handler `take()`s and posts `CommandReceived` (D24) to the existing
  controller state machine. CFT-on-a-generated-type + `ReadCondition` + `AsyncWaitSet` was
  validated via `ask_connext_question` before coding (no `@top_level` requirement; SQL
  single-quote rule is type-agnostic).
- Real ack writer: `DdsStatusPublisher::publish_ack` (was a labeled no-op) now writes
  `RouterCommandAck` on `ActRouterCommandAck` with `RELIABLE + VOLATILE + KEEP_LAST(16)`
  (D48), same `NotEnabledError` guard as `publish()` for D52 disabled startup.
- `router_main` wiring: `CommandReader` is constructed after `DiscoveryDispatcher` and
  **before** `aws.start()`/`registry.enable_all()` (same D52 edge-trigger reason — a command
  arriving in the gap would otherwise strand), and `shutdown()` on the stop path before
  `aws.stop()`.
- Fixture + test: `router/config/e2e_admin_commands.yaml` (one router, route `admin_r1`
  `enabled: false`, output leg `writer_qos: default` so ENABLE reaches `ROUTE_ENABLED` on
  input-writer discovery alone — no auto-QoS output-reader gate, keeping the test about the
  command loop) and `router/test_e2e/test_router_admin_commands.py` (E1–E4 + E-CFT).

**Two small clarifications surfaced while testing** (neither changes a prior decision):

- "Disabled route with no entities" (E1) is **not** zero `topic_status` rows — a configured
  topic always carries one row; when no entities exist it is `TOPIC_IDLE` with empty
  reader/writer QoS summaries. The test asserts `TOPIC_IDLE` + empty summaries (the probe's
  `read_route_facts` gained a `topic_state` field), which is the accurate "no entities"
  signal and the same signal E3 uses after `DISABLE_ROUTE` tears the build down.
- Ack topic name is `ActRouterCommandAck` (already named in command-status.md's D48
  paragraph; made concrete as the `DdsStatusPublisher` default).

**Evidence.** C++ ctest 4/4 (unchanged — no C++ integration test added, per D52 policy);
Python e2e 12/12 (new `test_router_admin_commands.py` among them), the new test stable 5/5
reruns, no `/dev/shm` leaks, no stray `router_main`. Uncommitted in the working tree at write
time.

**Docs changed.** `implementation-plan.md` (Phase 6 banner: 6a marked implemented);
`README` (`router/test_e2e`); this entry. Next on this thread: slice 6b (controller journal,
D55).

---

## D58 — Phase 6 slice 6b (controller journal) IMPLEMENTED; Phase 6 COMPLETE (2026-07-14, accepted; closes 6b per D55/D56)

**What shipped.** The D55 seam and its DDS implementation:

- `IControllerJournal` seam (`Interfaces.hpp`) + a nullable `IControllerJournal*` on
  `RouterController` (defaulted `nullptr`, D55). `drain()`/`wait_and_drain()` now share a
  `process_one()` helper that, after `publish_if_changed`, builds one
  `ControllerJournalRecord` per processed event and calls `journal_->record()` — skipped
  entirely when the pointer is null. **Phase 1 tests pass nothing and the 18-case
  `test_controller_phase1` suite stays green unchanged** (verified) — additive, zero behavior
  change as promised.
- Record contents (`build_journal_record`): event kind mapped 1:1 to
  `ControllerJournalEventKind` (D46); `pre_state_revision`/`post_state_revision`/
  `state_changed` from the revision before/after the event; `decision`/`reason` from the
  event's outcome — for a command, the cached ack (`accepted`/`rejected` + message, D4), so
  even a duplicate-replay journals the original decision with `state_changed=false`;
  `action` = `status_published` iff state changed. `event_sequence` is a monotonic
  per-controller counter. `payload_json` is left empty (reserved for future enrichment).
- `ControllerJournalPublisher` (`ControllerJournalPublisher.{hpp,cxx}`): writer on
  `ActRouterControllerJournal` with D49 QoS — `RELIABLE` + `KEEP_LAST(256)` + reliable send
  window `min/max_send_window_size(-1)` (unlimited) so `write()` never blocks the strand.
  Backlog watched via a `StatusCondition` on `RELIABLE_WRITER_CACHE_CHANGED_STATUS` attached
  to the shared `AsyncWaitSet`; `on_backlog()` logs `journal_falling_behind` on the rising
  edge past a `128` unacked threshold (half the depth), `journal_caught_up` on the falling
  edge. Wired into `router_main` before `aws.start()`/`enable_all()` (D52 ordering), declared
  before `RouterController` so it can hold the seam; `shutdown()` after `drain.stop()`.

**Connext API note (contradicts the MCP validator).** `validate_modern_cpp_code` got the
send-window QoS (literal `-1`, since it didn't accept the `dds::core::LENGTH_UNLIMITED`
symbol) and the extension status accessor
(`writer.extensions().reliable_writer_cache_changed_status()`) right, but it asserted the
condition handler is installed via `sc.handler(...)` — that does **not** compile against this
7.7 install. The correct call is `sc->handler(...)` (arrow — the handler lives on the
condition delegate), matching the existing Phase 5 `RouteRuntime` StatusConditions. Validator
output is a strong hint, not ground truth; the build is the arbiter.

**Test.** `router/test_e2e/test_controller_journal.py` (reuses `e2e_admin_commands.yaml`): a
Python `ControllerJournalRecord` reader IS "debug mode" (D56) — asserts a COMMAND_RECEIVED
record (decision `accepted`, affected route, real pre<post bump), discovery/entities-ready
records while the route builds, strictly-increasing `event_sequence`, the route still
reaching `ROUTE_ENABLED` with the journal attached (behavior unchanged by observation), and
`journal_falling_behind` ABSENT under normal load (D49 backlog signal wired-not-forced; real
backpressure deferred to a stress phase). Probe gained a `JournalCollector` (buffers records
across queries — `ControllerJournalRecord` is keyless, so a bare `take()` loop would discard
records for other queries, same rationale as `AckCollector`). The journal is VOLATILE, so the
test waits for the reader↔writer match before driving events.

**Evidence.** C++ ctest 4/4 (Phase 1 unchanged); Python e2e 13/13; journal test stable 5/5
reruns; no `/dev/shm` leaks, no stray `router_main`. **Phase 6 is complete** (6a = D57, 6b =
this). Uncommitted in the working tree at write time.

**Docs changed.** `implementation-plan.md` (Phase 6 banner: 6b implemented, phase complete);
`code-architecture.md` (`ControllerJournalPublisher`/`IControllerJournal` now implemented);
`README` (`router/test_e2e`); this entry.

---

## D59 — Phase 7 sliced into 7a (QoS-alias XML) / 7b (partitions) / 7c (multi-type) / 7d (full config e2e); evidence→test map (2026-07-14, accepted; Phase 7 implementation-readiness pass)

**Context.** Readiness pass before implementing Phase 7 (same treatment Phase 6 got in
D54–D56). Unlike Phases 1–6, Phase 7 in `implementation-plan.md` was still the original
6-line stub — no pinned contract, no D-numbers, no evidence→test map. Reading the stub's
three "Deliver" bullets against the tree surfaced that Phase 7 silently embeds **two
deferred library features and two never-wired mechanisms**, none previously validated
against 7.7:

- **QoS-alias XML resolution** is deferred *to* Phase 7 (D45/D50): `is_resolvable_qos_alias`
  honors only `""`/`"default"` (`QosAliasPolicy.hpp`), but production `control-platform.yaml`
  uses `wan_event`/`wan_status`/`lan_status_1hz` and participant `control_wan_udpv4_qos`/
  `platform_wan_udpv4_qos`. The `qos_profiles:` alias→`LIB::profile` indirection map is not
  even parsed by `RouteConfigParser` today.
- **Multi-type dispatch** is deferred (D34/D35): `DynamicRouteFactory` binds one type for the
  whole process and `router.type_name` is a single scalar, but `platform_events` carries two
  types (`PlatformCommandAck`, `ContactReport`) and the full config spans four.
- **Partition application**: `publisher_partition`/`subscriber_partition` are parsed onto the
  endpoint spec but never applied — `RouteEntityFactory` builds `Publisher`/`Subscriber` with
  default QoS.
- **Sample counters**: `RouteTopicRuntime::forwarded()` counts locally but
  `TopicRouteState.samples_forwarded` is read into status and never written from it, so status
  always reports 0; and D5 forbids counter deltas from bumping `state_revision`, so even once
  wired, "counters advancing" is not observable without a refresh path.

**Decision.** Phase 7 lands as four slices, each independently reviewable, riskiest-primitive
first per Tenet order. All four are in Phase 7 scope (delivers the real production
`control-platform.yaml`, Milestone 2).

- **7a — QoS-alias XML resolution** (D60). Parse `qos_profiles:`; load `qos_libraries` into a
  `QosProvider`; resolve named aliases; apply participant `qos:`. First cross-cutting slice.
- **7b — Publisher/Subscriber partition application** (D61). Apply endpoint partitions;
  smallest, isolated.
- **7c — Per-topic (multi-)type resolution** (D62). Retire process-global `router.type_name`;
  resolve each topic's DynamicType from its discovery-resolved type name. Critical-path risk.
- **7d — Full `control-platform.yaml` end-to-end** (D63 for the counter path). Control-node +
  platform-node `router_main` pair on the real production config; all three enabled routes
  forward; `platform_detail_status` toggled via the Phase 6 `ENABLE_ROUTE` loop; counters
  advance. After 7d, D50's blocker list collapses to only the Phase 8 items
  (`platform-team.yaml` flat-route parsing, `participant_partition` application).

**Evidence→test map** (Python e2e, real `router_main`, probe reads via DynamicData —
same harness as D56):

- **E1** (7a) a route using `wan_status`/`wan_event` aliases forwards; **E2** the participant
  `*_wan_udpv4_qos` profile is applied → `router/test_e2e/test_qos_alias_route.py` +
  `router/config/e2e_qos_alias.yaml`.
- **E3** (7b) a PLATFORM-partitioned route matches only a PLATFORM-partitioned reader;
  a `""`/mismatched-partition reader never matches (route stays `DISCOVERY_PARTIAL`, no
  incompatible-QoS event — D61) → `router/test_e2e/test_wan_partition.py` +
  `router/config/e2e_partition.yaml`.
- **E4** (7c) `platform_events` forwards both `PlatformCommandAck` and `ContactReport` from a
  single `router_main` process → `router/test_e2e/test_platform_events.py`.
- **E5** (7d) full `control-platform.yaml` control+platform pair: `control_command`,
  `platform_primary_status`, and `platform_events` all cross the WAN → 
  `router/test_e2e/test_control_platform_full.py`.
- **E6** (7d/D63) `RouterStatus` shows per-route `samples_forwarded` advancing for an active
  route (asserted within E5).
- **E7** (7d) `ENABLE_ROUTE platform_detail_status` starts detail-status flow from the target
  only → `router/test_e2e/test_detail_status_toggle.py` (exercises the Phase 6 command loop).

**Connext validations run before pinning** (`ask_connext_question`, 2026-07-14): (1)
`QosProvider` from multiple XML files via `rti::core::QosProviderParams::url_profile` +
`provider.datareader_qos("LIB::profile")`/`datawriter_qos(...)`; (2) Partition + CFT are
orthogonal, both apply, and partition mismatch is **not** an incompatible-QoS event; (3)
`PublicationBuiltinTopicData::type_name()` reports the struct name (e.g.
`platform_primary_status`), feeding `QosProvider::extensions().type(type_name)` directly.
Details in D60/D61/D62.

**Rationale.** Small diffs, hardest primitive (multi-type) isolated in 7c; the three
Connext-hard assumptions are validated up front, so each slice is high confidence going in —
matching the phase's "High" rating in the plan table, which the stub did not earn on its own.

**Docs changed.** `implementation-plan.md` (Phase 7 banner + Deliver/Evidence rewritten as
the four-slice contract with named tests); this entry (umbrella), D60–D63 (per-slice
contracts).

---

## D60 — Phase 7a: QoS-alias XML resolution via a `QosProvider` over `qos_libraries`; named alias short-circuits auto-QoS derivation and the D51 gate (2026-07-14, accepted; Phase 7 review)

**Context.** The deferred D45 work. `control-platform.yaml` names aliases on both endpoints
(`writer_qos: wan_event`, `reader_qos: wan_status`, `reader_qos: lan_status_1hz`) and on
participants (`qos: control_wan_udpv4_qos`). Three parsing/resolution gaps: `qos_profiles:`
(the `wan_event → WAN_QOS_LIB::event_qos` indirection) is unparsed; `is_resolvable_qos_alias`
rejects everything but `""`/`"default"`; participant `qos:` is not applied.

**Validated against 7.7** (`ask_connext_question`, 2026-07-14): construct one
`dds::core::QosProvider` over several files with
`rti::core::QosProviderParams params; params.url_profile(urls); dds::core::QosProvider(params);`
then fetch named QoS with `provider.datareader_qos("WAN_QOS_LIB::status_qos")` /
`provider.datawriter_qos("WAN_QOS_LIB::status_qos")`. Profile-name format is exactly
`"Library::Profile"`.

**Decision.**

- `RouteConfigParser` parses `qos_profiles:` into `RouteConfig` as an alias→`LIB::profile`
  map. The endpoint `reader_qos`/`writer_qos` and participant `qos:` values are **alias
  keys** into that map; the map value is the `QosProvider` profile path.
- `QosResolver` gains a `QosProvider` built (via `QosProviderParams::url_profile`) from
  `qos_library_paths`, plus the `qos_profiles` map. `reader_qos(alias)`/`writer_qos(alias)`
  resolve a named alias to `provider.datareader_qos(profiles[alias])` /
  `datawriter_qos(...)`; `""` and `"default"` keep their D39 meanings unchanged.
- `is_resolvable_qos_alias` (`QosAliasPolicy.hpp`) widens: an alias is resolvable iff it is
  `""`, `"default"`, or a key in the loaded `qos_profiles` map. Because the predicate is now
  config-dependent, it takes the `qos_profiles` set (the config-load check
  `validate_qos_aliases` and the runtime `QosResolver` both consult the same map — the D44
  no-drift rule holds by construction).
- **A named alias fully specifies the endpoint QoS**: it short-circuits the D39/D42
  auto-derivation (deadline/liveliness are the profile's, not derived) and the D51 readiness
  gate. `output_uses_auto_qos()` stays a plain `.empty()` check — a named alias is non-empty,
  so the gate is bypassed exactly as `control-platform.yaml`'s WAN legs already rely on
  (D51). This is now stated contract, not incidental behavior.
- Participant `qos:` is applied in `ParticipantRegistry` from the same provider
  (`provider.participant_qos(profiles[alias])`), preserving disabled-startup ordering (D52).
- `QosResolver::summarize()` is extended to report the resolved reliability/durability/
  deadline/liveliness of an arbitrary alias profile for status (D45 summaries), not just the
  auto profiles.

**Docs changed.** `implementation-plan.md` (Phase 7a); `QosResolver.hpp`/`QosAliasPolicy.hpp`
header comments (alias lookup now implemented); this entry.

**Reshaped by D64** — the alias contract stands, but the `QosProvider` API specifics here
(`QosProviderParams::url_profile`, `provider.datareader_qos`/`datawriter_qos`) are
**MCP-sourced and not build-verified**; confirm against the build before use (connext-ai-issues
guardrail).

**Spike-validated (`spikes/qos_alias/`, 2026-07-14, 3/3).** The mechanism works against the
real production QoS libs (load + resolve + apply + match + participant-from-profile), but the
spike surfaced three concrete additions 7a must absorb — the reason it was not high-confidence
on paper:
1. **The production QoS libs are templated with 14 env vars** (`*_LAN_PEER*`, `*_WAN_PEER*`,
   `WAN_HB_PERIOD_SEC`, `WAN_TTL`, `WAN_TIMEOUT_SEC`, …) and do **not parse** unless they are
   defined. `router_main` must supply/propagate them and fail fast naming a missing one.
2. **The WAN participant profile has an env-var constraint:** `participant_liveliness_assert_period`
   is hardcoded 30 s and `lease_duration = $(WAN_TIMEOUT_SEC)`; Connext requires assert < lease,
   so **`WAN_TIMEOUT_SEC` must be > 30** (XML default 100) or the participant fails to create
   with "Inconsistent QoS".
3. **`control-platform.yaml` named a non-existent profile (now fixed):** `lan_status_1hz →
   LAN_QOS_LIB::status_1hz_qos` (the lib has `status_1sec_qos`). Fixed to `status_1sec_qos`.
   `validate_qos_aliases` must still check profile **existence in the loaded provider**, not
   just the `is_resolvable_qos_alias` string rule, so this class of error is caught at load.

**API confirmed (Python):** multi-file load is `QosProvider(";".join(paths))`; resolve via
`datareader_qos_from_profile`/`datawriter_qos_from_profile`/`participant_qos_from_profile` — not
D60's `datareader_qos(...)`. The C++ `QosProvider` surface is still a compile-check at
implementation time.

---

## D61 — Phase 7b: apply endpoint Publisher/Subscriber PARTITION QoS; a partition mismatch is a non-match, not an incompatible-QoS event (2026-07-14, accepted; Phase 7 review)

**Context.** `RouterRouteEndpointSpec.publisher_partition`/`subscriber_partition` are parsed
(`RouteConfigParser.cxx`) but `RouteEntityFactory` builds `dds::pub::Publisher(out_dp)` /
`dds::sub::Subscriber(in_dp)` with default (empty `""`) partition — so
`control-platform.yaml`'s `CONTROL`/`PLATFORM` WAN partitions have no effect. Phase 7's
"explicit WAN PLATFORM partition handling" deliverable.

**Validated against 7.7** (`ask_connext_question`, 2026-07-14): Partition and
ContentFilteredTopic are orthogonal — partition gates association first, then the CFT filters
samples on matched pairs; set via `PublisherQos << dds::core::policy::Partition({"PLATFORM"})`
/ `SubscriberQos << Partition({...})`. **Load-bearing caveat: a partition mismatch is NOT
reported as an incompatible-QoS status** — mismatched endpoints simply never match, so the
D39/D45 `REQUESTED/OFFERED_INCOMPATIBLE_QOS` warnings will not explain a partition problem.

**Decision.**

- `RouteEntityFactory` sets `Partition` on the per-build `Publisher` from
  `view.spec.output.publisher_partition` and on the `Subscriber` from
  `view.spec.input.subscriber_partition`; empty ⇒ default `""` partition (no change from
  today). One partition string per endpoint for the POC (the field is scalar); wildcard/multi
  deferred until a route needs it.
- Because a partition mismatch is invisible to the incompatible-QoS path, the route surfaces
  it as it already surfaces "no writer discovered": the topic stays at `DISCOVERY_PARTIAL`
  with no matched endpoint. To keep it diagnosable, the discovery-time structured log (D17
  endpoint inventory) records each discovered endpoint's partition, so an operator can see
  "app writer on partition X, route input expects Y" in the log even though status shows only
  `DISCOVERY_PARTIAL`. No new status field.
- Partition is part of the D19 captured discovery subset already; controller matching is
  unchanged (DDS does the partition gating — the router does not re-implement it), consistent
  with D15's "enforcement is DDS-level, not route-matching logic."

**Docs changed.** `implementation-plan.md` (Phase 7b); `code-architecture.md`
(`RouteEntityFactory` partition-application note); this entry.

**Refined by D64** — under create-and-observe, a partition mismatch shows as the created
entity's **matched-count = 0** (surfaced as a status reason), not a controller-side
`DISCOVERY_PARTIAL` guess; the false-green is dissolved, not just diagnosed.

---

## D62 — Phase 7c: per-topic DynamicType resolution from discovery `type_name()`; retire process-global `router.type_name` (2026-07-14, accepted; amends D34/D35, D50)

**Context.** `DynamicRouteFactory` binds one type name for the whole process
(`DynamicRouteFactory.hpp`), and `router_main` requires a single `router.type_name` (D50 Gap-C
guard). `platform_events` carries two types and the full config four, so neither can run in
one process. `TypeResolver::get_dynamic_type(name)` already resolves *any* type by name — the
limitation is purely that the factory is bound once and the route spec has no per-topic type
source (topic name ≠ type name: topic `PlatformPrimaryStatus`, type `platform_primary_status`).

**Validated against 7.7** (`ask_connext_question`, 2026-07-14):
`PublicationBuiltinTopicData::type_name()` reports the registered **struct** name
(`platform_primary_status`), distinct from `topic_name()` (`PlatformPrimaryStatus`), and can
be passed directly to `QosProvider::extensions().type(type_name)` provided the loaded XML
registers that exact name. Pitfall recorded: it is the type-name string, never the topic name.

**Decision.**

- The type name for a route topic comes from the controller's already-resolved
  `resolved_type_name` (D20 first-resolved-wins, sourced from discovery `type_name()`), not
  from a process-global config scalar. `router.type_name` is **retired** — `router_main` no
  longer needs it and the D50 Gap-C guard is removed.
- `IEntityFactory::create_topic_entities` gains the resolved type name (the controller has it
  at build-issue time); `DynamicRouteFactory::make_topic`/`ensure_type_available` resolve the
  DynamicType per topic from that name instead of a single `type_name_` member. One
  `DynamicRouteFactory` instance now serves all DynamicData topics regardless of type.
- The generated-type lane (Phase 3, admin types) is unaffected — it keeps its
  compile-time-registered names.
- Consequence for D50: item 2 of its "not done" list (multi-type dispatch) is closed here;
  running the full `control-platform.yaml` in one process per node becomes possible (7d),
  leaving only the Phase 8 items (`platform-team.yaml` flat-route parsing,
  `participant_partition`).

**Docs changed.** `implementation-plan.md` (Phase 7c); `DynamicRouteFactory.hpp`,
`code-architecture.md`, `RouteConfigParser.hpp` (`type_name` retirement); D50 (amend note:
Gap C closed); this entry.

**Reshaped by D64** — `router.type_name` is still retired, but the per-topic type is the
**type object read from discovery** (`data.type`, rti_view model), not a discovery
`type_name()`→catalog resolution (there is no local catalog) and no per-topic `type:` config
key. Spike-proven (`spikes/type_discovery`).

---

## D63 — Phase 7d: wire `forwarded()` → `samples_forwarded` in status via a periodic refresh tick that does not bump `state_revision` (2026-07-14, accepted; Phase 7 review, refines D5)

**Context.** Phase 7's second evidence bullet ("status snapshots show sample counters
advancing per route") is unsupported today: `RouteTopicRuntime::forwarded()` counts on the
AsyncWaitSet worker thread, but `TopicRouteState.samples_forwarded` is only ever read into
status, never written from the runtime, so status reports 0. Worse, D5 deliberately excludes
counter deltas from the `state_revision` increment predicate ("counters advance inside a
revision"), and status publishes on revision change — so even after wiring the counter,
counters would only appear in status when some *other* change republishes it, i.e. never in
steady state.

**Decision.**

- A periodic **status-refresh tick** (config-fixed cadence, default 1 s — reusing the D14
  `LinkStatsCollector` tick precedent and its "measurement cadence is config-fixed, not
  adaptive" reasoning) runs on the controller strand: it pulls each active topic's
  `forwarded()`/lifecycle count into `TopicRouteState`, and if any counter changed,
  **republishes `RouterStatus` without bumping `state_revision`**. This is the one sanctioned
  exception to "status publishes only on revision change": D5 stays authoritative for what
  *changes revision*; the refresh tick republishes the same revision with fresh counters.
  Observers reading `state_revision` for change detection are unaffected (it does not move);
  observers watching counters see them advance at the tick cadence.
- The counter pull is strand-confined (no cross-thread read of the runtime): the tick posts a
  `RefreshCounters` controller event, and the handler reads the runtimes it owns — consistent
  with the single-strand rule (D3/D12). The runtime's `forwarded()` is a plain relaxed
  counter; exact-at-tick sampling is sufficient for the "advancing" evidence.
- Scope note: this is the counter-observability path only. The D14 link-stats tick remains
  separate (LAN-only `ActRouterLinkStats`, its own phase); this tick is the controller's own
  status republish. If both ticks coexist later they can share one timer, not designed here.

**Docs changed.** `implementation-plan.md` (Phase 7d evidence); `code-architecture.md`
(`RouterController` refresh-tick + the D5 republish-without-revision-bump exception);
`command-status.md` (status cadence note: revision-change OR counter-refresh tick); this
entry.

---

## D64 — Create-and-observe: types learned inline from discovery, DDS is the matching authority; supersedes the discovery-gated controller matching (2026-07-14, accepted direction; supersedes the matching half of D12/D20/D22 and the D39/D51 readiness gate; reshapes D62; amends D13/D35)

**Context.** The Phase 7 design discussion adopted a sharper premise than the earlier docs
assumed: **the router has topic names from config but NO local type objects** for the
forwarded application payloads (D35's router-authored XML is illustrative/reference only, not
a runtime dependency), and a LAN app may not even be a matching Connext version. Two threads
converged:

1. **Type acquisition.** To build a `DynamicData` reader/writer the router needs the
   `DynamicType`; with no local XML it must learn it from discovery. The `spikes/type_discovery`
   spike proved (Connext 7.7, stable 3/3, Part C decisive with the TypeLookup channel
   *disabled*) that a no-type participant reads the **COMPLETE** type object straight off the
   builtin discovery data (`data.type` on `publication_reader`/`subscription_reader` — the
   `rti_view`/`rtiddsspy` model), **inline in SEDP**, for a small type, from both a discovered
   **writer and reader**, with no `request_types_filter` and no matching local endpoint — and a
   `ContentFilteredTopic` built from that wire-learned type filters correctly.
2. **Matching.** The controller today re-derives matching from builtin discovery by topic name
   (`RouterController::apply_publication`), which is a second, incomplete implementation of what
   DDS already computes — the source of the D61 partition false-green (a cross-partition writer
   counts as matched → route reaches `ENABLED` → DDS never associates → zero samples, and the
   incompatible-QoS path is silent because partition mismatch is not a QoS event).

**Decision (accepted direction; the matching-authority refactor needs its own readiness pass
before coding — see "Not yet proven").**

- **Type acquisition = read the type object directly from discovery.** The router learns each
  route topic's `DynamicType` from `data.type` on the builtin readers (the rti_view model), not
  from a name→catalog lookup (there is no local catalog). This **supersedes D13's
  `request_types_filter`/TypeLookup framing** for the ACT (small-type) case; `request_types_filter`
  remains a documented **fallback** only for a type whose TypeObject exceeds the inline size
  threshold (C++-only on this install — see the connext-ai-issues submodule).
- **Learn-from-LAN rule.** Each router learns a route topic's type from its **local LAN app
  endpoint** — the app *writer* on the source side, the app *reader* on the destination side —
  then builds **both legs** (LAN + WAN) with that one type object. Symmetric; the WAN never
  learns, so **D13's LAN-only posture is preserved** even with no local XML.
- **Creation gate = "type resolved," not "match predicted."** A route topic's entities are
  created as soon as its type is known from the local LAN endpoint — not when the controller
  predicts a topic-name match.
- **DDS is the matching authority (create-and-observe).** Once entities exist, route
  connectivity and per-topic discovery state are driven by the entities' own
  `matched_publications()` / `matched_subscriptions()` (`SUBSCRIPTION_MATCHED` /
  `PUBLICATION_MATCHED`), **not** by the controller re-deriving matching by topic name. This
  **supersedes the matching half of D12/D20/D22** (controller topic-name matching + the
  per-topic matched-endpoint sets) and **retires the D39/D51 auto-QoS output-readiness gate**
  (create with a safe default, adjust in place). Builtin discovery is demoted to: type
  acquisition, loop-safety (D15, unchanged), presence/link-stats (D14), and **near-miss
  diagnosis** (e.g. "a writer exists on this topic but on the wrong partition"). The D61
  partition false-green **dissolves**: a cross-partition writer is simply absent from
  `matched_publications()`; no re-derivation, no partition-regex to replicate.

**Reconciliation of prior Phase 7 decisions.**

- **D62 (multi-type)** is reshaped: `router.type_name` is still retired, but the per-topic type
  is the **type object from `data.type`**, not a discovery-`type_name()`→catalog resolution
  (there is no catalog). No per-topic `type:` config key is added (the earlier "config `type:`"
  idea is dropped — the type is learned from the wire).
- **D61 (partitions)** is refined: the honest "built-but-not-connected" signal is the created
  entity's **matched-count = 0**, not a controller-side `DISCOVERY_PARTIAL` guess. Surface it as
  a status reason, not a new operational state (do not overload `DEGRADED`; keep the D2/D11
  contract).
- **D60 (QoS aliases)** is unaffected in intent (QoS aliases are orthogonal to type/matching),
  but its specific `QosProvider` API calls (`QosProviderParams::url_profile`,
  `provider.datareader_qos/datawriter_qos`) are **MCP-sourced and not build-verified** — confirm
  against the build before use (connext-ai-issues guardrail).
- **D59 slicing** is reframed: 7b (partitions) becomes nearly free under create-and-observe, and
  7c changes from "per-topic type resolution from discovery `type_name()`" to "type object from
  discovery + create-and-observe."

**Evidence.** `spikes/type_discovery/` (Parts A/B/C, 3/3) and `spikes/matched_endpoints/`
(Parts A/B/C, 4/4), stable, `/dev/shm` clean, Connext 7.7.0. Three MCP claims disproved along
the way are recorded in the `docs/connext-ai-issues` submodule (build is the arbiter).

**Spike-proven (2026-07-14).** Both halves are now validated against Connext 7.7.0:
- Type acquisition — `spikes/type_discovery/` (COMPLETE type read inline from discovery, from
  a writer and a reader; Part C decisive with TypeLookup disabled).
- Matching authority — `spikes/matched_endpoints/` (4/4 stable): DDS's own
  `matched_publications()`/`matched_subscriptions()` are the connectivity truth; a
  cross-partition writer never enters a CFT route reader's `matched_publications` (the D61
  **false-green is dissolved, not merely diagnosed**), a same-partition writer matches, and
  created-but-unmatched is an observable zero.

**Still to do before implementing (the readiness pass, NOT a spike).** Retiring the controller
topic-name matching + the matched-endpoint sets (D12/D20/D22) and the D39/D51 gate, in favor of
entity-`matched_*`-driven discovery state, is a change to shipped, tested Phase 1–5 code. It
needs its own D54/D59-style slicing: choose the poll-vs-`SUBSCRIPTION_MATCHED`-StatusCondition
mechanism (reuse the Phase 5 StatusCondition/AsyncWaitSet pattern), wire the
created-but-unmatched **status reason** (not a new operational state — keep the D2/D11
contract), and confirm the C++ `matched_publications`/StatusCondition call surface by compile
(the MCP is not trusted). The behavior/API is proven; the code refactor is the remaining work.

**Docs changed.** This entry; amend pointers on D12/D20/D22 (matching superseded), D39/D51
(gate retired), D13/D35 (type learned from wire), D60/D61/D62 (reconciled above);
`implementation-plan.md` (Phase 7 banner: create-and-observe pivot + the readiness-pass
prerequisite). `code-architecture.md` reconciliation deferred to the matching-authority
readiness pass.

---

## D65 — Phase 7a implemented: QosResolver/ParticipantRegistry gain real XML-alias resolution; router_main owns the QosProvider (2026-07-14, accepted; implements D60)

**Context.** D60 designed 7a; the `spikes/qos_alias/` spike then validated the mechanism against
the real production QoS libraries. This entry records the actual implementation in the shipped
router (`router/src/...`, not the spike) and the one correction it required to D60's own text.

**C++ `QosProvider` construction path corrected (build-verified, closes D60/D64's "not
build-verified" flag).** D60's literal snippet — `dds::core::QosProvider(params)` — **does not
compile**: `dds::core::TQosProvider` (`$NDDSHOME/include/ndds/hpp/dds/core/TQosProvider.hpp`)
has no constructor taking `rti::core::QosProviderParams`, only `(uri)`/`(uri, profile)` strings.
The real multi-file-load path is
`rti::core::create_qos_provider_ex(const rti::core::QosProviderParams&)`
(`dds/core/detail/QosProvider.hpp:52`), which returns a `dds::core::QosProvider`. D60's three
named getters — `datareader_qos(profile)` / `datawriter_qos(profile)` / `participant_qos(profile)`
— **do** exist exactly as written (`TQosProvider.hpp:334,398,249`) and compile unchanged. Logged
in `docs/connext-ai-issues/connext-ai-issues.md`.

**Decision (as implemented).**

- `router_main` builds ONE `std::shared_ptr<dds::core::QosProvider>` from
  `cfg.qos_library_paths` (via `create_qos_provider_ex`) and passes it to both `QosResolver`
  and `ParticipantRegistry` — "applied... from the same provider" (D60) is literal, not two
  independently-constructed providers re-parsing the same env-var-templated XML.
- `QosAliasPolicy.hpp`/`RouteConfigParser` stay DDS-free per their existing layering rule:
  `is_resolvable_qos_alias` widens to take the `qos_profiles` map (declared-alias check only).
  `validate_qos_aliases` also now checks participant `qos:` aliases (D60 only mentioned
  endpoint aliases; extended for D44 no-drift symmetry).
- Whether a *declared* alias's profile actually **exists** in the loaded XML (the class of bug
  the historical `lan_status_1hz -> status_1hz_qos` typo was) can only be checked once a real
  `QosProvider` exists, so it is NOT inside the DDS-free config layer as D60's phrasing implied.
  Instead, `router_main` runs a startup preflight right after constructing `QosResolver` (before
  any DDS entity exists): eagerly resolve every route's declared `reader_qos`/`writer_qos` alias
  **and every participant's resolved `qos:` profile** (the same bug class on the participant leg
  — without this it would only throw inside `ParticipantRegistry`'s constructor as an unlabeled
  fatal), failing fast + loudly (naming the route/participant) on the first one that doesn't
  resolve.
- `QosResolver::reader_qos`/`writer_qos`: a named alias (not `""`/`"default"`) returns
  `provider->datareader_qos(...)`/`datawriter_qos(...)` directly — fully replacing the D39/D42
  auto-derivation path, no baseline-then-derive, exactly D60's short-circuit.
- `ParticipantRegistry::Config` gains an already-resolved `qos_provider_profile` (alias
  resolution stays in `router_main`, keeping this class alias-agnostic); `make_participant_qos`
  uses `provider->participant_qos(profile)` when set, else `default_participant_qos()` as
  before; D52 disabled-startup ordering unchanged.
- The 14 QoS-lib env vars and the `WAN_TIMEOUT_SEC > 30` liveliness constraint (spike findings
  1/2) are **not** re-implemented in `router_main` — both are inherent to Connext's own XML
  loading / `Inconsistent QoS` participant-creation checks, which already propagate to
  `router.fatal` with a message naming the problem. Hardcoding the 14 ACT-harness-specific var
  names into the generic router was deliberately rejected (leaks harness-specific knowledge into
  code meant to work with any `qos_libraries:` list).
- The `control-platform.yaml` `lan_status_1hz` fix (spike finding 3) was already committed;
  `test/test_route_config.cxx`'s pinned "known gap" assertion (expected `validate_qos_aliases`
  to fail on this file) now flips to expect success, plus direct assertions on the parsed
  `qos_profiles` map.

**Evidence.** `router/test_e2e/test_qos_alias_route.py` (`config/e2e_qos_alias.yaml`, plan's
E1/E2) — real production QoS libs, real alias names (`wan_status`/`wan_event`/
`control_wan_udpv4_qos`/`platform_wan_udpv4_qos`), one router process: route reaches
`ROUTE_ENABLED`, forwards a sample end-to-end, and the RouterStatus QoS summaries show the
named profiles' actual policies (BEST_EFFORT reader / RELIABLE writer), not the D39 baseline.
Full existing `router/test_e2e/` suite (14 tests) and `ctest` (`test_route_config` et al.)
re-ran clean — no regression to the `""`/`"default"`-only configs.

**Docs changed.** This entry; `implementation-plan.md` (Phase 7a marked delivered);
`docs/connext-ai-issues/connext-ai-issues.md` (construction-path correction).

---

## D66 — D64 implementation-readiness pass: matching-authority refactor sliced (7m before 7b/7c); StatusCondition-driven match state; zero matches is a status reason, not a teardown; one wire type per topic per process (2026-07-15, accepted; executes D64's "still to do", reshapes D59's slice order)

**Context.** D64 accepted the create-and-observe direction and explicitly required a
D54/D59-style readiness pass before touching the shipped controller: choose the observation
mechanism, wire the created-but-unmatched status surface without breaking the D2/D11
contract, state the type-versioning rule, and confirm the C++ call surface by compile (the
MCP is not trusted). This entry is that pass. The C++ surface is now **compile-verified**
(`spikes/matched_endpoints/cpp_compile_check.cxx`, `-fsyntax-only` with the router's own
flags, clean): `dds::sub::matched_publications(reader)` / `dds::pub::matched_subscriptions
(writer)` → `InstanceHandleSeq`; `subscription_matched_status()` /
`publication_matched_status()` (counts + last handle); `StatusCondition` with
`StatusMask::subscription_matched()`/`publication_matched()` attached to the
`AsyncWaitSet`; `matched_publication_data(reader, handle)`; and the 7c wire-type read
`PublicationBuiltinTopicData->type()` → `optional<DynamicType>` → `StructType` →
`Topic<DynamicData>`/reader/writer.

**Decisions.**

1. **Observation mechanism: StatusConditions, not polling.** Each route entity's
   `SUBSCRIPTION_MATCHED`/`PUBLICATION_MATCHED` status rides the entity's existing
   StatusCondition on the `AsyncWaitSet` — the same `RouteTopicRuntime` callback pattern
   the D39/D45 incompatible-QoS warnings already use. The handler posts a new controller
   event `TopicMatchChanged{route, topic, side, current_count, generation}` (thread-safe
   MPSC `post()`, stale-stamp discarded like every D21/D23 completion). Polling is
   rejected as the primary mechanism; the D63 refresh tick MAY re-sample counts as a
   consistency backstop, but nothing depends on it.
2. **Matched counts are the discovery truth for a live build.** `TopicRouteState` gains
   `input_matched_count`/`output_matched_count`, updated only by `TopicMatchChanged`.
   For a built topic, `derive_topic_discovery` maps counts onto the existing enum
   (both sides ≥ 1 → `READY`; one side → `PARTIAL`; none → `NONE`) — no IDL enum change.
   `RouterRouteTopicStatus` gains `input_matched`/`output_matched` integers plus a
   `match_reason` string (`"input_unmatched"`/`"output_unmatched"`/empty) so the
   created-but-unmatched zero is directly visible. **No new operational state** — the
   D2/D11 tables are unchanged; the reason string is status-only (D64's instruction).
3. **Zero matches is NOT a teardown.** The `TOPIC_FORWARDING → TEARING_DOWN` edge on
   discovery regression is retired for live builds: entities persist while unmatched
   (that is the point of create-and-observe — an unmatched entity is an observable zero,
   and DDS rematches without our help). Teardown remains command-driven
   (`DISABLE_ROUTE`), abort-driven (creation-time regression no longer exists — see 4),
   and error-driven. Consequence: the derived `DEGRADED` value narrows to
   teardown-in-progress; "was forwarding, peer gone" reads as
   `ENABLED`/`TOPIC_FORWARDING` + `match_reason` + counts at zero.
4. **Creation gate = "type available", and in 7m that means immediately on enable.** The
   D39/D51 output-readiness gate is retired (`output_uses_auto_qos` stops gating; the
   predicate survives only to mark which routes use writer-QoS derivation). Slice 7m
   keeps today's XML `TypeResolver`, where the type is always available, so entities are
   created directly when a route (re-)enables; 7c swaps the type source to the wire and
   reintroduces the wait honestly (`TOPIC_IDLE` until the local LAN endpoint's inline
   type object arrives — the D64 learn-from-LAN rule; `TypeResolved` becomes a real
   controller event there).
5. **Writer-QoS derivation survives as best-effort input, not a gate.** At creation the
   auto-output writer derives deadline/liveliness from the readers *currently known* via
   builtin discovery (possibly none → the D39 strong baseline with default liveliness).
   Deadline keeps its in-place tightening path (mutable, D39). Liveliness is immutable:
   a stricter reader arriving after creation surfaces through the existing
   incompatible-QoS warning (loud, names the policy), remedy = a named alias (7a) or a
   `DISABLE`/`ENABLE` re-arm (the rebuild derives from the now-known readers). The
   matched-endpoint maps (`matched_writers`/`matched_readers`) are **demoted from
   matching authority to derivation-and-diagnosis input** (deadline/liveliness
   derivation, D17 near-miss logging); they gate nothing.
6. **Type authority (the versioning rule D64 left unstated).** Per topic per process:
   **first-learned-wins from the local LAN endpoint**, D20's spirit carried from name to
   type object. Both legs of this process's route use that one object. Cross-router
   version skew (control-side learned v1, platform-side learned v2) is tolerated by DDS
   type assignability on the WAN match — each leg is its own DDS relationship (Tenet 2);
   a non-assignable pair simply never matches and reads as unmatched + a near-miss log
   naming the type. A later *local* endpoint with a different type object on an
   already-resolved topic: `type_conflict` log, ignored, no rebuild (unchanged posture).
   A discovered endpoint whose TypeObject is not inline (exceeds the SEDP threshold):
   loud `type_not_inline` log; the `request_types_filter` fallback (C++-only on this
   install) is wired only when a real type needs it.
7. **Builtin discovery's remaining jobs** (unchanged by the refactor): D15 loop-safety
   ignores, D17 endpoint inventory + near-miss diagnosis, D14 presence/link-stats join,
   QoS-derivation input (5), and — from 7c — type acquisition (`data->type()`).

**Slicing (reshapes D59's order; 7a already shipped, D65).**

- **7m — matching-authority refactor** (new slice, lands first): `TopicMatchChanged`
  event + StatusCondition wiring in `RouteTopicRuntime`; matched counts + `match_reason`
  in state/status/IDL; retire the creation gate and the regression-teardown edge;
  demote the matched-endpoint maps per (5). Phase 1 unit tests migrate with the
  contract: the fake factory posts `TopicMatchChanged`, and the D2 conformance edges
  that drove teardown-on-regression retire with the edge.
- **7b — partitions** rides 7m (its original D61 surface is one `PublisherQos`/
  `SubscriberQos` partition apply; the mismatch evidence is 7m's observable zero).
- **7c — wire-type acquisition** per (4)/(6): `DiscoveryDispatcher` reads
  `data->type()` off the local LAN builtin readers, posts `TypeResolved`; retire
  `router.type_name`; `example_types.xml` stays for test *peers* and admin types stay
  generated.
- **7d — unchanged** (full config e2e + D63 counter tick; the tick has no dependency on
  7m and may land any time).

**Evidence→test map.**

- 7m: full existing e2e suite green (matched-path behavior is unchanged); new
  `router/test_e2e/test_create_and_observe.py` — an enabled route on an empty domain
  reaches `TOPIC_FORWARDING` with both counts 0 and `match_reason` set, then matches and
  forwards when the peer appears, with counts advancing in status.
- 7b: `test_wan_partition.py` (D59 E3) — mismatched partition = held-zero counts +
  reason, never a false `READY`; matched partition forwards.
- 7c: `test_platform_events.py` (D59 E4) — two types, one process, types learned from
  the wire (no `type_name` in config).
- 7d: unchanged (D59 E5–E7).

**Docs changed.** This entry; `implementation-plan.md` (Phase 7 banner: readiness pass
complete, slice order 7m → 7b → 7c → 7d; 7b/7c bullets reshaped);
`spikes/matched_endpoints/README.md` (compile check). `code-architecture.md`
reconciliation lands with 7m itself (its controller/state sections change shape there).

---

## D67 — Slice 7m IMPLEMENTED: DDS is the matching authority in the shipped router; two findings — the dissolved false-green appeared in a real test, and journal consumers need KEEP_ALL (2026-07-15, accepted; implements D66's 7m)

**Context.** Implements D66 exactly: `TopicMatchChanged` events, matched counts as the
discovery truth, creation gate and regression edges retired, record maps demoted. This
entry records the implementation shape plus two findings the migration surfaced.

**As implemented.**

- `RouteTopicRuntime`'s existing entity StatusConditions gain
  `SUBSCRIPTION_MATCHED`/`PUBLICATION_MATCHED`; the handlers read
  `subscription_matched_status()`/`publication_matched_status()` (read clears the change
  flag) and post `ControllerEvent::topic_match_changed{route, topic, gen, input_side,
  current_count}` — same MPSC path and D23 stamp-gating as the QoS warnings.
- `TopicRouteState` gains `input_matched_count`/`output_matched_count` (entity facts,
  cleared with the build); `derive_topic_discovery` maps them onto the existing enum
  (both ≥1 READY / one PARTIAL / none NONE); new `derive_match_reason` names a live
  build's unmatched leg(s). IDL: `RouterRouteTopicStatus` gains
  `input_matched`/`output_matched`/`match_reason`; `ControllerJournalEventKind` gains
  `JOURNAL_TOPIC_MATCH_CHANGED` (D46 1:1 rule). Matched counts are in the route
  fingerprint — a count change is externally visible D5 state and bumps revision (the
  sample counters stay excluded).
- `reconcile_topic`: an enabled IDLE topic builds immediately; the CREATING
  regression-abort and FORWARDING regression-teardown edges are deleted — teardown is
  command/error-driven only; `apply_endpoint_lost` is record-map hygiene only. New
  `RouterController::activate()` builds every startup-enabled route; `router_main` calls
  it after `enable_all()` (entity ignores/instance handles need enabled participants —
  D52 ordering) and before the `DrainThread`. Writer-QoS derivation unchanged in shape
  but now best-effort: derives from the readers currently known (possibly none → the
  strong baseline `deadline=inf, liveliness=AUTOMATIC:inf`).
- Phase 1 suite migrated to the D66 contract (17 tests): the create-and-observe walk,
  count-change revision semantics, DISABLE-aborts-in-flight-create, records-never-teardown;
  the pre-D64 gate/type-upsert/matched-set-boundary tests retired with their edges.

**Finding 1 — the D61/D64 false-green existed in a shipped test and dissolved on contact.**
`test_same_node_ignore.py`'s "genuine application writer" was a default VOLATILE writer
against the route's `default`-alias (RELIABLE+TRANSIENT_LOCAL) input reader — RxO
incompatible, it NEVER actually matched; the old controller topic-name matching reported
`ROUTE_ENABLED` anyway. Under 7m the truth surfaced as `input_matched == 0` +
`route_qos_incompatible reader:DURABILITY`. The test now offers genuinely compatible
writers and asserts the matched counts, which also makes the D15 part-A zero attributable
to the ignore rather than to a QoS mismatch.

**Finding 2 — journal consumers must use KEEP_ALL history.** 7m emits journal records in
bursts (a command's COMMAND_RECEIVED and the enable's TOPIC_ENTITIES_READY land in one
event-drain, ~2 ms apart). `ControllerJournalRecord` is keyless — one instance — so a
consumer's default KEEP_LAST(1) reader cache holds exactly one sample and a polling
`take()` loses the earlier record of a burst (this is how `test_controller_journal.py`
failed post-migration: reliable delivery, but reader-side history eviction). The probe's
journal reader now uses KEEP_ALL; any future debug recorder must too. The router-side
D49 writer QoS is unchanged and correct.

**Also e2e-proven now:** the D66 liveliness residual — the baseline writer (built before
any reader existed) draws `writer:LIVELINESS` from a later finite-lease reader, warn-only
(`test_auto_qos.py` 4c); and E-M (`test_create_and_observe.py`): built-unmatched zeros +
`match_reason`, counts rise when peers appear, sample forwards.

**Evidence.** ctest 4/4 (migrated Phase 1 suite); e2e **15/15 twice** (new
`test_create_and_observe.py`; migrated `test_auto_qos.py`, `test_same_node_ignore.py`,
`test_router_admin_commands.py`, `test_controller_journal.py`; all other tests unchanged
and green); `/dev/shm` clean.

**Docs changed.** This entry; `implementation-plan.md` (7m delivered);
`code-architecture.md` (D66 reconciliation: ownership-boundary demotion, state model,
event table incl. `TopicMatchChanged`).

---

## D68 — Controller journal keyed by (target_node, target_router) (2026-07-15, accepted; user-directed, amends D67's finding-2 framing and the D46/D49 journal type)

**Context.** D67's finding 2 observed that `ControllerJournalRecord` was keyless — the
whole topic was ONE instance, so any consumer's per-instance reader cache was shared
across every router writing to `ActRouterControllerJournal`. That is wrong data modeling
for the multi-router deployment the journal exists for (a recorder watching a control
node + N platform routers): one router's record burst could evict another router's
records from a bounded reader cache.

**Decision.** `ControllerJournalRecord` gains `@key` on `target_node`/`target_router` —
the same key idiom as `RouterStatus`. Each router is one instance on the journal topic:
per-router reader caches, per-router instance lifecycle, and Admin Console groups records
by router. `build_journal_record` already stamps both fields on every record, so no
writer-side code changes. The writer's D49 QoS (RELIABLE + KEEP_LAST(256) + unlimited
send window) is per instance, and each router writes only its own instance — unchanged in
effect.

**What keying does NOT fix (D67 finding 2 stands).** Within a single router's instance
the stream is still bursty (7m: COMMAND_RECEIVED + TOPIC_ENTITIES_READY in one
event-drain), so a consumer's KEEP_LAST(1) cache still drops the first record of a burst.
Journal consumers use **KEEP_ALL** (or deep KEEP_LAST) regardless — keying bounds
cross-router eviction, KEEP_ALL bounds same-router burst loss.

**Evidence.** ctest 4/4; full e2e 15/15 (`test_controller_journal.py` unchanged in
behavior — one router, one instance).

**Docs changed.** `RouterAdminTypes.idl` + `command-status.md` IDL sketch (@key);
`dds_probe.py`/`test_controller_journal.py` comments (keyless → keyed-per-router); this
entry.

---

## D69 — Slice 7b IMPLEMENTED: endpoint Publisher/Subscriber partitions applied at build, plus runtime per-route partition change via SET_ROUTE_PARTITION (2026-07-15, accepted; implements D61 as refined by D64/D66, extends the command surface per user direction)

**Context.** D61's core (apply the parsed-but-dropped
`publisher_partition`/`subscriber_partition` to the per-build `Publisher`/`Subscriber`)
rides 7m as designed. The user additionally required **runtime per-route partition
modification** — distinct from Phase 8's participant-level `SET_PARTICIPANT_PARTITION`
(which stays parsed-and-rejected). The D15 side-finding already validated the mechanism:
pub/sub PARTITION is runtime-mutable via `set_qos` with automatic rematching, no entity
recreation.

**Decision.**

- **Build-time application (D61):** `RouteEntityFactory` sets `Partition` on the
  per-build `Publisher` from `output.publisher_partition` and on the `Subscriber` from
  `input.subscriber_partition`; empty ⇒ default partition. One scalar name per endpoint
  (wildcard/multi deferred); the `inherit_participant` sentinel in `platform-team.yaml`
  is NOT a 7b keyword — it is decided in Phase 8's readiness pass with
  `participant_partition` itself.
- **New command kind `SET_ROUTE_PARTITION`** (appended to `RouterCommandKind`; existing
  ordinals unchanged). Payload rides the command's embedded route spec:
  `route.input.subscriber_partition` / `route.output.publisher_partition` become the
  named route's desired values — both legs, every command; empty = default partition
  (callers read current values off the status desired spec). Unknown route ⇒ the D24
  cached reject; unchanged values ⇒ D8-style idempotent accept ("partition unchanged",
  no revision bump, no factory call).
- **Controller semantics:** update `desired`, **re-mint the RouteView** with a fresh D23
  stamp (future builds — re-enable/re-arm — read the new spec; live builds keep their
  generation because they are adjusted, not rebuilt), then for each live topic
  (CREATING or FORWARDING — creation is synchronous on the strand, so a CREATING
  topic's entities already exist) call the new
  `IEntityFactory::update_route_partitions` →
  `AsyncWaitSetDispatcher::set_partitions` →
  `RouteTopicRuntime::set_partitions` (Subscriber + Publisher `set_qos`). A failed
  lookup (racing teardown) is log-only — the next build applies the new view.
- **Observability:** the desired-spec partitions join the route fingerprint (a change is
  externally visible D5 state → revision bump + publish; the values ride
  `RouterRouteStatus.desired`). The rematch itself is visible for free through the
  7m matched counts — the retargeted-away reader's count drops to zero, the new
  partition's reader rises, `topic_state` never leaves `TOPIC_FORWARDING`.

**Evidence.** `router/test_e2e/test_wan_partition.py` + `config/e2e_partition.yaml`
(E3 + RT): default-partition reader held at zero with `match_reason` and **no**
incompatible-QoS event (D61's load-bearing caveat); PLATFORM reader matches and receives
forwarded samples; `SET_ROUTE_PARTITION` retargets PLATFORM→TEAM_B in place — PLATFORM
unmatches, TEAM_B matches and receives, QoS summaries byte-identical across the change
(same build, no rebuild), revision bumps, idempotent repeat acks "partition unchanged"
with no bump. Unit: `test_set_route_partition` (accept/idempotent/view-re-mint — the
rebuild after disable/enable carries the new partitions). ctest 4/4; full e2e **16/16
twice**; `/dev/shm` clean.

**Docs changed.** `RouterAdminTypes.idl` + `command-status.md` (enum + command table);
`implementation-plan.md` (7b delivered); this entry.
