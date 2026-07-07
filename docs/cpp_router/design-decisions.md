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

---

## D3 — Phase 1 includes the `ControllerEvent` queue; DDS is faked behind interfaces (2026-07-07, accepted)

**Context.** Phase 1 has no DDS, but the architecture's core rule
([code-architecture.md](code-architecture.md) Controller Event Model) is that everything
reaches the controller as typed events on one strand. Unclear whether Phase 1 builds that or
drives the state machine with direct method calls.

**Decision.** The event queue **is the Phase 1 deliverable's spine**: Phase 1 builds the
`ControllerEvent` types and the single-strand drain loop, with `DiscoveryIndex`,
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

---

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
- **No** bump for: counter/metric deltas (counters advance inside a revision), `DESCRIBE`,
  duplicate commands, status republish.

**Docs changed.** `command-status.md` (IDL types).

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

---

## D7 — Phase 1 seams: concrete routes, three commands, read-only participants, no DDS status writer (2026-07-07, accepted)

**Context.** Phase 1 borrows shapes from later phases (role selection → Phase 4, DDS
command/status loop → Phase 6, partitions → Phase 8). The exact seams were unstated.

**Decision.**

- **Routes**: the controller is constructed with a list of concrete active-side
  `RouterRouteSpec`s (from the Phase 0 config parser or test fixtures). Role-aware
  `source_side`/`destination_side` selection stays in Phase 4; Phase 1 never sees it.
- **Commands**: `ENABLE_ROUTE`, `DISABLE_ROUTE`, `DESCRIBE` + duplicate handling, injected as
  `CommandReceived` events (no DDS reader). `UPDATE_ROUTE` and `SET_PARTICIPANT_PARTITION`
  are parsed-and-rejected per D4.
- **Participants**: `MutableRouterState.participants` is populated read-only from config for
  status completeness; no mutation path until Phase 8.
- **Status**: `StatusPublisher` is an interface; Phase 1's implementation captures
  `RouterStatusView` snapshots for assertions (and/or logs them). The DDS `RouterStatus`
  writer arrives with the admin plumbing (Phase 2 shows discovery in status; Phase 6 completes
  the command/status loop).

**Docs changed.** `implementation-plan.md` (Phase 1 section).
