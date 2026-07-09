# Presence & Health (System-Level, Not Per-Topic)

## Paradigm

Failure detection in the router mesh is **system-level presence awareness** — "is this
*router* alive?" — not per-topic liveliness. WAN **data** topics carry no liveliness-driven
behavior; a single dedicated **`RouterHealth`** topic is the one liveliness-bearing channel,
and it feeds a **membership roster** each router maintains and publishes.

Validated against Connext 7.7.0 (see the reasoning in
[isc-findings.md](isc-findings.md) for the related instance-state mechanics):

- **Participant discovery liveliness is separate from topic `LIVELINESS`.** Setting WAN data
  topics to `LIVELINESS = AUTOMATIC` with `lease_duration = INFINITE` (no per-topic liveliness
  traffic) does **not** disable crashed-router detection: the participant lease still purges a
  dead participant and all its endpoints, transitioning its instances to
  `NOT_ALIVE_NO_WRITERS` downstream. Crash-detection latency is then governed by
  **participant discovery** QoS, not the topic lease.
- This composes with the instance-state mirror: one presence-loss event for a router drives
  the existing `NO_WRITERS → unregister` path for **all** instances that router forwarded, in
  bulk; rediscovery drives ISC/durability recovery.

## `RouterHealth` topic

The single liveliness-bearing WAN topic. It lives on the **WAN participant** (mesh presence
needs cross-WAN visibility) — distinct from the LAN-local admin command/status plane.

- **Key:** `router_id`.
- **Payload (compact summary — deliberately WAN-frugal):** `router_id`, `node_name`, `role`,
  `heartbeat_seq`, `send_timestamp`, `state_revision` (matches the router's own `RouterStatus`
  revision), and a rollup: `n_routes`, `n_degraded`, `n_error`, `overall_state`
  (`ROUTER_OK` / `ROUTER_DEGRADED` / `ROUTER_ERROR`). It carries **summary** status, **not** the
  full route table — full per-route detail stays on the LAN plane. This resolves the old "echo
  status in the health payload?" open choice: **yes, but summary-only**, so the sample stays
  small and scales safely with mesh size across a lossy/constrained WAN.
- **QoS:** `RELIABLE`, `KEEP_LAST(1)`, `TRANSIENT_LOCAL` (late joiners get last state),
  `DEADLINE ≈ 1.5–2×` heartbeat period, `LIVELINESS = AUTOMATIC` with a finite lease
  (≈ 2–3× period). Periodic heartbeat write (e.g. 1 s).
- **QoS is deliberately untouched by link-metrics capture (D14).** `RouterHealth` doubles as
  the *passive* stats bellwether — the `LinkStatsCollector` polls this pair's protocol
  statistics like any WAN pair (known offered rate; signal even when data routes are idle) —
  but the RTT probe's special QoS (app-ack, per-sample piggyback HB, zero ACK delay) lives on
  the dedicated `RouterLinkProbe` topic, never here. See [Link Metrics](link-health.md).
- **Multi-network (D18):** WAN participants are never multi-homed — one WAN participant per
  unique network (`allow_interfaces_list`). Each network participant carries its own
  `RouterHealth` pair, so presence becomes per-path (`router_id, network`) when a second
  network exists; today's single-network rig is the `N = 1` case.

Two signals, deliberately kept separate:

| Signal | Mechanism | Meaning |
|---|---|---|
| **Dead** | `LIVELINESS` lost / instance `NOT_ALIVE_NO_WRITERS` (and participant purge) | router/writer is gone |
| **Stale** | `DEADLINE` missed / heartbeat-delta over threshold | router still present in discovery but not publishing — **policy decision, not teardown** |

## WAN data topics

- `LIVELINESS = AUTOMATIC`, `lease_duration = INFINITE` — no topic-level liveliness.
- Crash detection comes from participant presence; per-topic staleness (where it matters) from
  `DEADLINE`.
- Instance-state routes keep their `RELIABLE + RECOVER` QoS; recovery is driven by participant
  loss + rediscovery, not topic-liveliness timeouts.

## Participant discovery tuning

Crash-detection latency for the whole mesh is set here, not per topic. Starting point is RTI's
`BuiltinQosSnippetLib::Optimization.Discovery.Common` (participant lease 10 s, assert 3 s,
~6 s detection); tighten for the demo if faster router-loss detection is wanted.

**Ordering constraint (pinned — [design-decisions.md](design-decisions.md) D16):** the WAN
participant lease must stay **longer** than the `RouterHealth` liveliness window
(≈ 2–3× heartbeat period), so the presence topic is always the first and authoritative DEAD
signal and the DDS participant purge trails it as backstop/bulk cleanup — it can never race
ahead of the roster. This constraint must survive any retune of either knob. (LAN
participants are tuned separately and shorter — local crash detection for route
degradation, see D16.)

## Membership roster (each router)

Each router **publishes** its own `RouterHealth` heartbeat and **subscribes** to the topic to
build a mesh view:

```
peer[router_id] = { node, role, last_seq, last_heartbeat_ts, state,
                    state_revision, n_routes, n_degraded, n_error, overall_state }
state ∈ { ALIVE, STALE, DEAD }
  ALIVE  heartbeats fresh, liveliness ok
  STALE  liveliness ok but heartbeat delta > staleness threshold  (deadline missed)
  DEAD   liveliness lost / participant purged / NOT_ALIVE_NO_WRITERS
```

Each peer entry keeps the last **compact summary** heard on `RouterHealth` (identity + presence
+ the rollup) — not the peer's full route table.

- `on_liveliness_changed` / instance `NOT_ALIVE_NO_WRITERS` → mark `DEAD`.
- `on_requested_deadline_missed` / `now - last_heartbeat_ts > threshold` → mark `STALE`.

## Aggregate mesh view, published over the LAN

Each router **aggregates** every peer's compact `RouterHealth` summary into the roster above and
**republishes the whole connected-router list over its LAN** on a dedicated topic,
**`ActRouterMeshStatus`** — the "who's connected" view for local consumers/dashboards. This is
distinct from the per-router `ActRouterStatus` (this router's own detailed route table):

| Topic | Plane | Contents | Why here |
|---|---|---|---|
| `RouterHealth` | WAN | one compact summary per router (this router publishes its own) | single cross-link presence + summary signal; small on the constrained WAN |
| `ActRouterStatus` | LAN | this router's own full route/participant detail | LAN-local control plane, WAN-independent |
| `ActRouterMeshStatus` | LAN | aggregated list of **all** connected routers' summaries + presence | local mesh observability; LAN is unconstrained so the full peer list is cheap here |
| `RouterLinkProbe` | WAN | tiny per-router probe samples (app-ack RTT) | isolates the probe's special QoS (`VOLATILE`, no liveliness, zero ACK delay) from the presence authority — D14/D18, [link-health.md](link-health.md) |
| `ActRouterLinkStats` | LAN | per-peer raw link metrics each poll interval | capture-only telemetry (D14); nothing new crosses the WAN |

- The mesh-status sample carries, per connected router: identity, `presence` (`ALIVE`/`STALE`/
  `DEAD`), `last_seen_delta`, and the compact rollup (`state_revision`, `n_routes`, `n_degraded`,
  `n_error`, `overall_state`). Full per-route detail for a *specific* peer is obtained by asking
  that peer's router directly (its own LAN `ActRouterStatus`) or over a future WAN remote-admin
  channel — it is **not** fanned out over the WAN.
- The aggregate is republished on roster change (peer appears/disappears, presence transition,
  or a peer's `state_revision` advances), so LAN consumers see a coherent mesh snapshot per write.

## Interaction with instance state

- **Router DEAD → bulk instance-state cleanup.** Participant purge takes that router's writers
  down; the local data readers go `NOT_ALIVE_NO_WRITERS`; the route mirror unregisters the
  affected instances downstream — one presence event, all instances handled.
- **Router returns → recovery.** On rediscovery, `RECOVER_INSTANCE_STATE_CONSISTENCY` +
  `TRANSIENT_LOCAL` replay restore instance state (subject to the Scenario-A/B limits in
  [isc-findings.md](isc-findings.md)).
- **Router STALE → no teardown.** A silent-but-present router is *not* torn down (it may
  resume); it's flagged in the roster and left to operator/policy.

## Assume-present, reset only on definite death

Because WAN data topics carry no topic-level liveliness, **nothing resets instance state
automatically from a liveliness timeout** — and that is deliberate. On a lossy/constrained WAN,
liveliness timeouts would be **false positives**: instances flapping to `NO_WRITERS` during
transient loss, churning ISC/state needlessly. So the router **assumes writers are present**
and resets state **only** when the `RouterHealth` topic declares a peer **DEAD** (not STALE).

### Programmatic reset mechanisms (Connext 7.7, validated)

| Mechanism | Effect | Reversible |
|---|---|---|
| output `unregister_instance(handle)` | downstream instance → `NOT_ALIVE_NO_WRITERS` ("source gone") | **yes** (peer returns → re-write → `ALIVE`) |
| output `dispose_instance(handle)` | downstream → `NOT_ALIVE_DISPOSED` ("instance deleted") | semantically wrong for a dead source |
| `dds::domain::ignore(participant, peer_handle)` (free fn) | local removal of peer "as if it never existed"; drops its writers **without** waiting for the discovery lease | **NO — irreversible for the life of the local participant** |
| `dds::pub::ignore(...)` / `dds::sub::ignore(...)` | same, per remote writer/reader | irreversible |
| `ReaderDataLifecycle.autopurge_*_instances_delay`, `keep_minimum_state_for_instances(false)` | reclaim NOT_ALIVE instance *state* on the reader | n/a |
| per-instance reader purge | **does not exist**; only `take()` samples or delete+recreate the reader | n/a |

### Chosen reset path

1. **Primary — output-side `unregister_instance`, presence-gated.** On peer **DEAD**, the mirror
   iterates the instances that peer was sourcing and `unregister_instance()`s them on the output
   writer → downstream `NOT_ALIVE_NO_WRITERS`. Reuses the mirror's existing `NO_WRITERS` path,
   is semantically correct (source gone ≠ instance deleted, so **unregister, not dispose**), and
   is **reversible** — a returning peer re-writes and instances recover via ISC/durability.
   Requires bookkeeping: `peer_id → { instance keys }` in the mirror.
2. **`ignore()` — heavy, rarely used.** Forces immediate input-side drop of a dead peer ahead of
   the discovery lease, but is **irreversible** (no un-ignore). Safe only because a restarted
   peer usually reappears under a *new* participant GUID. Use **only from DEAD, never STALE**,
   and only when input-side cleanup must beat the discovery lease.
3. **Automatic fallback — participant discovery purge.** A truly dead participant is purged
   after `participant_liveliness_lease_duration`, auto-driving `NO_WRITERS` on the input reader
   → mirror unregisters downstream with no manual action. The health-topic reset just does this
   *sooner and deliberately*; tune the discovery lease and the manual reset becomes near-optional.

### Tension to honor

Do **not** use aggressive reader instance-purge (`keep_minimum_state_for_instances(false)`,
near-zero `autopurge_*_instances_delay`) on **state routes** — the ISC / key-recovery path
requires `keep_minimum_state_for_instances = true`. Reset at the **writer** (unregister), not by
purging reader state.

## Discovery-database hygiene under a long lease (long-mission churn)

The lease serves a second purpose beyond presence: it is the built-in cleanup timer for the
discovery database. Design intent here is a **very long `participant_liveliness_lease_duration`**
so brief partitions don't purge a peer and trigger rediscovery churn (assume-present) — the
`RouterHealth` topic, not the lease, is the presence authority. The cost: dead participant GUIDs
(one new GUID per restart) linger in the local DB until that long lease expires. Validated
mechanics (Connext 7.7):

- **A normal lease auto-prunes** — a stale participant *and all its entities* are removed when
  the lease expires. A very long lease deliberately defers this.
- **`dds::domain::ignore(handle)` prunes the discovery-DB entry** (reclaims matching resources),
  but moves the GUID into a separate **ignore table** bounded by `ignored_entity_allocation`
  with an eviction policy `ignored_entity_replacement_kind`.
- **Resource limits reject, they don't evict** — `remote_participant_allocation` /
  `remote_writer_allocation` / `remote_reader_allocation` (defaults 16/64/64 initial, max
  unlimited). Hitting a max **rejects new discovery**; nothing is auto-evicted. So under churn,
  explicit pruning is **mandatory** with a long lease.

### Chosen approach

1. **Long lease** → tolerate transient absence, no rediscovery churn.
2. **Correlate `router_id → current participant GUID`** from `RouterHealth` (stable key vs.
   churning GUID). When a `router_id`'s GUID changes (restart observed), the **old GUID is
   dead-and-superseded** → `dds::domain::ignore(old_handle)` prunes it now. `ignore()`'s
   irreversibility is a non-issue — that GUID never returns.
3. **Bound the ignore table** — size `ignored_entity_allocation` and set
   `ignored_entity_replacement_kind` so pruned GUIDs self-evict; this keeps the system bounded
   over arbitrarily long missions.
4. **Size `remote_*_allocation`** as a backstop against runaway growth / silent discovery
   rejection.

### Tradeoff (a knob, not a fork)

If extreme transient-absence tolerance isn't required, a **moderate lease** does all of this
automatically (built-in stale eviction, zero app logic), with the health topic still providing
fast authoritative presence + roster. The long-lease + app-driven `ignore()` path is worth the
extra machinery only when brief partitions are frequent enough to want rediscovery-churn
suppression across them.

## Granularity: link health, not process/instance

The router's presence role is deliberately scoped to **router / WAN-link health** — "is this
router/link up?" It is **not** process- or instance-level liveness. Presence catches a router
crash or a WAN partition (heartbeats stop, since the health topic rides the same link). It does
**not** catch an individual producer app going silent while its router and link are fine — that
(if ever needed) is application-level freshness (payload timestamps / a consumer-side
`DEADLINE`), not the relay's job. This is not a regression: Routing Service carries no
end-to-end liveliness across the WAN either; presence *adds* router-level awareness that isn't
available today.

## Local endpoint logging (optional, log-only)

Liveliness across the WAN is prohibited (Tenet 4), but **local** (LAN-side) endpoint presence
may be logged for diagnostics — never crossing the WAN, never driving cross-link state:

- **QoS caveat (Tenet 6):** the relay's LAN reader QoS is dictated by the discovered app
  writers (RxO). It cannot request a *finer* liveliness lease than the apps offer — a
  finite-lease request against a default-infinite app writer is incompatible and **breaks the
  match**. So liveliness-lost logging is available **only if the app writers offer a finite
  lease**.
- **QoS-independent alternative:** log local endpoint appearance/disappearance from **discovery
  events** (the `DiscoveryDispatcher` already watches the built-in publication topic). This catches
  crash/disconnect regardless of app QoS, but not the hung-but-connected case (only liveliness
  or deadline catches that).

Either way it is a local diagnostic only; cross-link presence remains the `RouterHealth` topic.

## Health/mesh IDL sketch

Generated alongside `RouterAdminTypes.idl` (see [command-status.md](command-status.md)); wired
in during the presence phase, after the P0–P3 core exists.

```idl
enum RouterOverallState { ROUTER_OK, ROUTER_DEGRADED, ROUTER_ERROR };
enum RouterPresenceState { PRESENCE_ALIVE, PRESENCE_STALE, PRESENCE_DEAD };

// WAN: one compact, liveliness-bearing summary per router. Small on purpose.
struct RouterHealth {
    @key
    uint32 router_id;
    string node_name;
    string role;
    uint64 heartbeat_seq;
    int64  send_timestamp;      // ns since epoch
    string state_revision;      // matches this router's RouterStatus revision
    uint32 n_routes;
    uint32 n_degraded;
    uint32 n_error;
    RouterOverallState overall_state;
};

// LAN: this router's aggregated view of every connected peer (the "connected router list").
struct RouterMeshPeer {
    RouterHealth        health;              // last compact summary heard from the peer
    RouterPresenceState presence;
    int64               last_seen_delta_ms;  // now - last_heartbeat_ts
};

struct RouterMeshStatus {
    @key
    string observer_node;       // the router publishing this aggregate
    @key
    string observer_router;
    string state_revision;      // bumps when the roster changes
    sequence<RouterMeshPeer> peers;
};
```

## Open choices

- Heartbeat period and lease/deadline/participant-discovery numbers (demo vs. production).
- Whether STALE should ever escalate to a forced teardown after a longer timeout.
- ~~Whether the roster is echoed in the `RouterHealth` payload in addition to `RouterStatus`.~~
  **Resolved:** `RouterHealth` carries a **compact summary** (not full status); each router
  aggregates peers' summaries and republishes the connected-router list on the LAN
  `ActRouterMeshStatus` topic. Residual: whether a router's *aggregate mesh view* should also be
  echoed over the WAN (so peers see each other's views), or stay LAN-local — currently LAN-local.

## Where it fits the build

New health/mesh types (`RouterHealth`, `RouterMeshStatus`) generated alongside
`RouterAdminTypes.idl`. The `PresenceMonitor` module (see
[code-architecture.md](code-architecture.md)) publishes this router's compact `RouterHealth`
over the WAN, subscribes to peers', maintains the roster, and publishes the aggregated
`ActRouterMeshStatus` over the LAN; the roster also feeds presence-driven reset (Tenet 5).
Recommend proving it in a spike (N `RouterHealth` publishers on the WAN participant; kill one;
confirm peers mark it DEAD with the right delta, that the LAN `ActRouterMeshStatus` aggregate
updates, and that participant purge drives `NO_WRITERS` on a co-tested data topic) before wiring
into the router — after the P0–P3 core exists. Per project convention, that spike's driver and
kill/verify tooling are Python.
