# Thesis & Tenets (Read First)

This is the anchor for the C++ Dynamic DDS Router design. Where any other document in this
set conflicts with this one, this document wins — the others predate the decisions below and
are being brought into line. All Connext claims here were validated against RTI Connext
Professional **7.7.0** docs (via Connext AI) and/or the runnable spike in
[`spikes/isc_recovery/`](../../spikes/isc_recovery/).

## Thesis

A terminating DDS relay is **structurally unavoidable** for ACT: the endpoints live on
segmented domains/transports (LAN domains ↔ WAN domain) that do not communicate, and traffic
must be **altered** in transit (re-QoS, re-partition, downsample, filter, lifecycle) — not
merely tunneled. Bridging segmented domains + altering traffic ⇒ an intermediary that
terminates DDS on both legs. Routing Service is exactly such an intermediary and already
proves the pattern.

So the decision is **not** "relay vs. no relay" — it is **custom relay vs. Routing Service**,
and the justification is **observability and control**, *not* semantic preservation:

1. **No black box** — we own and can reason about every forwarding decision, vs. RS's opaque
   XML engine.
2. **YAML config convenience** — readable, live-mutable, demo-explainable.
3. **Network capture** — the Modern C++ API exposes `rti::util::network_capture` (pcap of DDS
   traffic incl. shared memory); the Python binding does not.
4. **Presence awareness** — liveliness callbacks on a health topic to know which *routers* are
   up (see [presence-and-health.md](presence-and-health.md)).
5. **(Future) per-writer/reader protocol statistics** to assess DDS **link health** —
   retransmits, NACKs, sample loss, latency per endpoint pair. The relay is the natural
   measurement point; neither RS nor the network layer gives you this. Investigated and
   scoped capture-first in [link-health.md](link-health.md) (D14): metrics are captured and
   rolled up per peer now; health *inference* waits for a link-degradation correlation
   experiment.

**Instance State Consistency (ISC) is explicitly NOT a justification and is out of scope** —
see Tenet 2 and [isc-findings.md](isc-findings.md).

## Tenets

### 1. Necessity is topological — bridge only what must cross and be altered
If a route needs neither domain crossing nor alteration, don't relay it (direct DDS / bypass).
The relay exists for the LAN↔WAN gateway, not as a universal hop.

### 2. The relay is not DDS-transparent — reconstruct what apps depend on, drop the rest deliberately
Each leg is its own DDS relationship; end-to-end DDS semantics do **not** flow through. The
relay:
- **forwards** valid data (`write`);
- **mirrors meta samples** — application `dispose`/`unregister` propagate through (via
  `key_value()` recovery), independent of liveliness — **kept regardless**;
- does **NOT** attempt ISC. Native `RECOVER_INSTANCE_STATE_CONSISTENCY` recovers only the
  same-physical-writer reconnect case, is unusable across an intermediary (CORE-13337), and is
  moot here anyway because WAN topics carry no liveliness (Tenet 4). The spike proved this;
  not relying on ISC is an evidence-based decision, not a compromise. Consequently the
  read-retain / re-assert-on-ALIVE machinery is **not needed**.

### 3. Impairment is the network's job, not the relay's
Latency/loss/jitter for the degraded-link exercise come from the emulated RF network
(EMANE/netem), below DDS. The relay stays a faithful bridge; it does not inject impairment.
"Alter messaging dynamics" for the relay means *transformation* (QoS/partition/rate/lifecycle),
not *degradation*.

### 4. System-level presence, not per-topic liveliness — and never across the link
- WAN **data** topics carry **no liveliness** (`AUTOMATIC` + effectively infinite lease). Per-
  topic liveliness on a lossy WAN produces false-positive `NO_WRITERS` churn.
- One **`RouterHealth`** topic (liveliness-enabled) is the single cross-link presence signal.
  Its granularity is **router / link health**, *not* process- or instance-level.
- Presence detects a router or the WAN link going down. It does **not** detect an individual
  producer going silent — that (if ever needed) is application-level freshness
  (payload timestamps / a consumer-side `DEADLINE`), not the relay's job.
- **Local** (LAN-side) liveliness may be used for **log-only** presence of local endpoints, but
  it never crosses the WAN and never drives cross-link state (see Tenet 6 for the QoS caveat).

### 5. Assume present; reset only on definite death
Because there's no data-topic liveliness, nothing resets instance state on silence — by design.
State is reset **only** when `RouterHealth` declares a peer **DEAD** (not STALE): the relay
`unregister_instance()`s that peer's instances on the output side (→ `NOT_ALIVE_NO_WRITERS`),
which is **reversible** (peer returns → re-writes → `ALIVE`) and semantically correct (source
gone ≠ instance deleted, so unregister, not dispose). This reuses the meta-mirror path; it just
triggers on presence. Participant discovery purge is the automatic fallback; `ignore()` is a
heavy, irreversible option used only for pruning known-dead/superseded participant GUIDs.
Detail: [presence-and-health.md](presence-and-health.md).

### 6. The relay adapts on the LAN side and imposes on the WAN side
- **LAN endpoints** are peers to the ACT apps, so their QoS is **dictated by the discovered app
  writers** (RxO compatibility; in `auto` mode derived from them). We adapt, we do not impose.
  *Caveat:* the relay cannot request finer liveliness than the apps offer — a finite-lease
  request against a default-infinite app writer is incompatible and breaks matching. So
  local liveliness logging is available only if apps offer finite liveliness; otherwise use
  discovery events for local presence logging.
- **WAN endpoints** are routers we control, so we **impose** explicit QoS (named aliases,
  liveliness-free). The relay is the translation boundary: app-dictated LAN policies (including
  liveliness) **terminate at the relay** and are re-shaped, not propagated, across the link.

### 7. Minimize impact per-route, baselined against Routing Service
- **Default forwarding is `dynamic_data`.** `serialized_cdr` (skip-deserialization) is an
  **opt-in, eligibility-gated** optimization, allowed only when the route has no reader-side
  content filter and no instance-state/lifecycle needs (both require field access). Bulk
  pass-through routes optimize throughput; filtered/state routes optimize fidelity.
- The relay hop is **not new** — RS was already that hop. "Minimize impact" means *no worse
  than the RS hop it replaces*, better where we can.

### 8. Define and honor the per-topic application contract
For each ACT topic, know which DDS guarantees the apps actually depend on — reliability,
ordering, explicit lifecycle (dispose), keying — and reconstruct exactly those. This set is the
spec for "faithful enough." (ACT payload types are currently unkeyed *for demo only*; real
state topics will be keyed — see [[act-state-topics-will-be-keyed]] in project memory.)

### 9. Simplicity first — prefer DDS-native mechanisms over app-level machinery
This is a clean-sheet design; the review criterion applied at every phase is: before adding
router logic or a new representation, check whether DDS (or the generated types) already
provides the behavior — durability for late-joiner state, liveliness for aliveness, CFTs
for filtering, partitions for scoping, `ignore` for loop safety, RxO for compatibility.
App-level machinery must earn its place by doing something DDS cannot (e.g. command
idempotency, the route state machine). Applied in D25/D26: the status snapshot is the
generated `RouterStatus` itself; `TRANSIENT_LOCAL` status durability replaces the
request/reply status command; no periodic status republish. Applied to Phase 2 in D27–D30: the
endpoint record is the builtin topic data itself; endpoint removal rides native builtin
instance transitions; the origin warning is a discovery-time matching rule, not per-sample
machinery; the discovery dispatcher is a translator, not a second cache.

## Consequences for the build

- Instance-state handling reduces to: **forward data + mirror meta samples (dispose/unregister)
  + presence-driven reset**. No ISC, no re-assert-on-ALIVE, no value cache — only a lightweight
  `peer → {instance keys}` set for the reset.
- The two medium-confidence items in the old plan (serialized-CDR fast path, ISC/keyed-lifecycle
  mirroring) drop off the critical path. `serialized_cdr` becomes a late optimization; ISC is
  removed.
- Admin/control plane is **LAN-local** (see [command-status.md](command-status.md)); the
  **one** liveliness-bearing WAN topic is `RouterHealth`.
- Structured logging is one stream with the Connext logger bridged into it (see
  [code-architecture.md](code-architecture.md)).

## Evidence

- [isc-findings.md](isc-findings.md) — the ISC investigation that grounds Tenet 2, with the
  `spikes/isc_recovery/` results (native ISC scope, CORE-13337, the intermediary gap).
- [presence-and-health.md](presence-and-health.md) — the presence/health design (Tenets 4–5).
- Connext 7.7 QoS/API specifics were validated via the `connext` MCP throughout.
