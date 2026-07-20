# Link Metrics: Capture First, Infer Later

> Investigation and capture-architecture proposal for Tenet 5's "(Future) per-writer/reader
> protocol statistics" ([thesis-and-tenets.md](thesis-and-tenets.md)). Status: **proposed,
> under discussion** (D14 in [design-decisions.md](design-decisions.md)).
>
> **Stance:** this phase **captures and rolls up** reliable-protocol metrics per WAN link. It
> deliberately does **not** classify link health from them. Thresholds/classification come
> only after a controlled link-degradation experiment correlates each metric against known
> impairment (see [Deferred: inference](#deferred-inference-and-the-correlation-experiment)).
> Until then the metrics are published raw; their *meaning* is assigned later, with data.

All API facts below validated against **Connext 7.7.0, Modern C++** via
`ask_connext_question` (2026-07-08), per the repo rule "validate Connext specifics — don't
guess." **Header-verified 2026-07-17 (D81, stronger than the MCP sourcing):** the
matched-endpoint status getters (`DataWriterImpl.hpp:331-395`), probe QoS knobs
(`heartbeats_per_max_samples` `CorePolicy.hpp:4658`, `acknowledgment_kind`
`CorePolicy.hpp:880`, `min/max_heartbeat_response_delay` `PolicySettings.hpp:657-669`),
the app-ack listener + `AcknowledgmentInfo`, and
`matched_subscription/publication_participant_data` (`discoveryImpl.hpp`) all exist as
described; `docs/connext-ai-issues/` has no contradicting entry.

## What a reliable router-to-router pair exposes

Every reliable WAN writer/reader pair the router owns is already an instrumented channel.
The RTPS reliability machinery (HEARTBEAT → ACKNACK → repair) runs constantly, and Connext
exposes its counters through per-entity status getters — no extra QoS, no monitoring
library, in-process reads.

### Writer side (this router's WAN writers)

`writer.extensions().datawriter_protocol_status()` →
`rti::core::status::DataWriterProtocolStatus`:

| Field | Captures |
|---|---|
| `received_nack_count()` / `received_nack_bytes()` (+ `_fragment_` variants) | peer readers detecting gaps — the sending-side loss signal |
| `pushed_sample_count()` / `pushed_sample_bytes()` | first-transmission traffic |
| `pulled_sample_count()` / `pulled_sample_bytes()` | repair + late-joiner (`TRANSIENT_LOCAL` replay) traffic |
| `sent_heartbeat_count()` | HB traffic; rate rises with `fast_heartbeat_period` under backlog |
| `send_window_size()` | **current** window capacity — the adaptive window shrinks under congestion, so this is itself a signal |
| `first_unacknowledged_sample_sequence_number()` vs `last_available_sample_sequence_number()` | oldest-unacked lag (backlog depth in sequence numbers) |
| `first_unacknowledged_sample_subscription_handle()` | *which* peer is holding progress back |

`writer.extensions().reliable_writer_cache_changed_status()` →
`ReliableWriterCacheChangedStatus` — the backpressure numbers live **here**, not in the
protocol status:

| Field | Captures |
|---|---|
| `unacknowledged_sample_count()` / `unacknowledged_sample_count_peak()` | current / worst in-flight backlog |
| `full_reliable_writer_cache()` | occupancy hit the send-window ceiling (writes now block) |
| `high_watermark_reliable_writer_cache()` / `low_watermark_reliable_writer_cache()` | threshold band crossings (also flip fast/normal heartbeat) |

`writer.extensions().reliable_reader_activity_changed_status()` →
`ReliableReaderActivityChangedStatus`: `active_count()` / `inactive_count()`. A matched
reader goes **inactive** after `max_heartbeat_retries` heartbeats without ACK progress —
independent of liveliness and of `publication_matched` (the reader stays matched; it flips
back to active when ACKs resume). While inactive, the writer stops waiting on it for
send-window progress.

Blocked writes: with the window full, `write()` blocks up to `reliability.max_blocking_time`
and throws `dds::core::TimeoutError` — reliably observable only under `KEEP_ALL`; under
`KEEP_LAST` the write may instead succeed by replacing an unacked sample, which surfaces on
the far side as `lost_by_writer` (below).

### Reader side (this router's WAN readers)

`reader.extensions().datareader_protocol_status()` →
`rti::core::status::DataReaderProtocolStatus`:

| Field | Captures |
|---|---|
| `sent_nack_count()` / `sent_nack_bytes()` | gaps detected inbound (mirror of the writer view) |
| `uncommitted_sample_count()` | samples buffered out-of-order awaiting hole repair — the clearest reader-side transient-loss gauge |
| `duplicate_sample_count()` | redundant / reordered delivery |
| `out_of_range_rejected_sample_count()` | receive window full + out-of-order (mixed network/local-window stress) |
| `rejected_sample_count()` | local resource pressure (NACKed and re-sent, not lost) |
| `received_heartbeat_count()`, `received_gap_count()` | control-traffic context |
| `last_available_sample_sequence_number()` vs `last_committed_sample_sequence_number()` | current hole size |

Loss vs. local limits — the reasons matter:

- `SampleLostStatus::last_reason() == SampleLostState::lost_by_writer()` — the writer
  removed the sample before the reader recovered it: **reliability actually failed
  end-to-end**. Highest-severity counter we capture.
- `lost_by_*_limit()` / `SampleRejectedState::rejected_by_*_limit()` — **local resource**
  symptoms, not link problems. Captured separately; never conflated with loss.

### Per-peer attribution (the aggregation key)

The plain per-writer status **sums across all matched subscriptions** — in a mesh, an
aggregate NACK spike can't say which link is sick. 7.7 provides matched-endpoint variants:

- `writer.extensions().matched_subscription_datawriter_protocol_status(handle | rti::core::Locator)`
- `reader.extensions().matched_publication_datareader_protocol_status(handle)`

**Attribution is one middleware call (D81, header-verified):**
`rti::pub::matched_subscription_participant_data(writer, handle)` /
`rti::sub::matched_publication_participant_data(reader, handle)` resolve a matched-endpoint
handle straight to the peer's `ParticipantBuiltinTopicData`, whose `participant_name()` is
the router identity (D74/D79: `name = "<node>/<router>"`, `role_name = "act.router"`; per
D79 the name IS the only identity — `router_id` is retired). The discovery DB is the
association authority; no roster join, and no pre-heartbeat gap by construction (an
endpoint match implies the participant was already discovered — SEDP rides SPDP). The
`PresenceMonitor` roster plays **no role** in stats attribution. The collector may cache
name-per-handle for the life of a match (optimization only). **Rollup key: peer name**,
summed across this router's WAN endpoints.

### Multi-network peers (decided — D18)

Peers may be reachable over **multiple physical networks** (e.g. mesh radio + SATCOM),
typically separate NICs. In DDS these surface as **multiple unicast locators** per peer
endpoint, and per-peer rollup would conflate physically distinct links. Validated 7.7
behavior (`ask_connext_question`, 2026-07-08):

- **A multi-homed peer receives redundant traffic — user DATA included, not just protocol
  messages.** Remote writers send to *all* announced locators (duplicates discarded at the
  receiver); locator-reachability logic prunes currently unreachable locators; the OS
  routing table picks the egress NIC per destination. On constrained WAN links this
  near-duplication of traffic is itself a system-level cost, independent of stats.
- **Writer side supports per-path stats:** `get_matched_subscription_locators()` +
  `matched_subscription_datawriter_protocol_status(Locator)` yield separate views per
  locator of the same matched reader. (Exact per-field attribution semantics are not fully
  documented — verify empirically in the correlation experiment.)
- **Reader side does not:** `DataReaderProtocolStatus` is per matched publication only — no
  per-source-locator breakdown for inbound traffic.
- **The RTT probe cannot attribute per path** on a multi-homed participant: the app-ack
  callback identifies the reader, not the locator the ack traveled.
- **RTI's recommended pattern for clean per-path characterization/segregation is separate
  DomainParticipants per NIC** (`allow_interfaces_list`, optionally `max_interface_count` /
  link priority). Each network then becomes its own participant → per-path presence, probe,
  and both-side stats fall out naturally, and routes could pin topics to paths — but this is
  a system-architecture change (participants × networks, discovery cost, config), not a
  collector tweak.

**Decided (D18, 2026-07-08): one WAN participant per unique network**, pinned via
`allow_interfaces_list` — a WAN participant is never multi-homed. Each network participant
carries its own `RouterHealth` and `RouterLinkProbe` pair, so per-path presence, RTT, and
both-side stats fall out by construction; the rollup key generalizes to
`(peer name, network)` (D79) with no per-locator machinery — "network" is simply which
local WAN participant observed the peer. The locator-aware-rollup
alternative was rejected (redundant-DATA cost stands, writer-only attribution, wasted once
participant-per-network arrives). Today's single-network rig is the degenerate `N = 1` case;
config/registry changes activate when a second network reaches the rig.

### Read/reset semantics (capture constraint)

Base counters are **cumulative totals**; the `_change` fields are deltas **that reset on
every status read**, and reading also clears the "changed" flag listeners/conditions key on.
Two consequences for the design:

1. **Exactly one component reads these statuses** (the collector). No ad-hoc reads elsewhere,
   no mixing polled reads with listener consumption on the same status.
2. The collector **polls totals and computes its own interval deltas** — never trusts
   `_change` fields, so a stray read elsewhere degrades nothing. A **negative delta** means
   the peer's matched-endpoint status restarted (rematch): re-baseline, count from zero,
   and stamp that interval `rediscovery_in_interval` (D81 — the flag marks "baselines
   untrustworthy this interval", not only `TRANSIENT_LOCAL` replay).

## RTT probe (the one thing counters can't give)

No protocol status carries timing — counters and sequence numbers only. Two validated ways
to measure a middleware-level roundtrip; both require probe-side QoS because the defaults
defeat timing:

- **Piggyback HB per sample:** `rtps_reliable_writer.heartbeats_per_max_samples ==
  send_window_size`, fixed window (`min == max`) — RTI's low-latency builtin snippet pattern
  (40/40/40, "send a piggyback heartbeat per sample"). Default is 8 per window: not
  per-sample.
- **Zero ACK delay on the probe reader:** `rtps_reliable_reader.max_heartbeat_response_delay`
  defaults to **0.5 s** (min 0) — deliberate jitter that would swamp the measurement. Set
  min/max to 0 (probe reader **only** — on data-plane readers that jitter prevents ACK
  implosion under fanout); consider `heartbeat_suppression_duration` (62.5 ms default) too.

| Instrument | Extra wire traffic | Per-peer attribution | Measures |
|---|---|---|---|
| `AcknowledgmentKind::APPLICATION_AUTO` + `on_application_acknowledgment` | **AppAck + AppAckConf** per sample per peer (~2 small msgs; AppAck re-sends every `app_ack_period` [5 s default] until confirmed) | **yes** — callback carries the acknowledging reader's subscription handle | app-level RTT: wire + peer middleware + peer take/return-loan |
| `write()` → `wait_for_acknowledgments(timeout)` stopwatch | none (ACKNACK already flows) | no — returns on the **slowest** matched peer | protocol-level RTT upper bound; needs one-sample-at-a-time, sync publish, no batching |

Chosen direction (D14): **app-ack on a dedicated `RouterLinkProbe` topic.** At probe cadence
(~1 Hz) the app-ack overhead is ~2 small messages/s/link. App-ack is **never** enabled on
data routes: there the per-sample handshake scales with traffic, and writer retention
extends to "fully ACKed" (protocol **+** app ack), adding queue/memory pressure and
interacting with `KEEP_LAST` replacement. AppAck retransmissions under loss are themselves
recorded (they correlate with impairment).

**Why not reuse `RouterHealth` as the probe carrier** (discussed and decided 2026-07-08):

- **Blast radius.** Presence is the single health authority and drives the destructive
  reset path (peer DEAD → unregister). App-ack changes writer retention to fully-ACKed,
  which interacts with `KEEP_LAST(1)` replacement — an unvalidated semantic on the topic
  the mesh least affords to get wrong.
- **Durability mismatch.** `RouterHealth` must be `TRANSIENT_LOCAL`; a durable writer
  replays history to (re)matched readers, whose app-acks would fire for samples written
  long ago — garbage RTT exactly at rejoin-after-outage, needing filtering machinery. The
  probe is naturally `VOLATILE` and the problem never exists.
- **Knob entanglement.** Probe cadence/QoS will be tuned during the correlation experiment;
  `RouterHealth` cadence is load-bearing for DEADLINE/STALE/lease calibration — and presence
  transitions are among the signals being correlated *against*.
- **Rollout.** A router built without the probe simply doesn't match the probe topic;
  presence matching is never at risk from probe QoS evolution.

**Accepted cost:** one more WAN writer/reader pair per router — more endpoint discovery
announced to all peers, re-announced (new GUID) per restart, lingering in peers' discovery
DBs under the long-lease hygiene regime and counting against `remote_*_allocation` budgets.
Judged worth it for isolation from the presence authority; the probe endpoints are included
when sizing those allocations ([presence-and-health.md](presence-and-health.md)).

`RouterLinkProbe` sketch: WAN participant, keyed by the router name (D79), payload
`{router, probe_seq, send_timestamp}` — tiny. `RELIABLE`, `VOLATILE`, `KEEP_LAST(1)`,
**no liveliness** (presence stays `RouterHealth`'s job), fixed send window with
`heartbeats_per_max_samples == send_window_size`, probe readers set zero
`heartbeat_response_delay`, `APPLICATION_AUTO` acknowledgment, ~1 Hz.

`RouterHealth` remains the passive bellwether it already is: the collector polls its
writer/reader pair's protocol statistics like any other WAN pair (known offered rate,
active when data routes are idle — idle routes produce *no* protocol stats). Its QoS is
untouched.

## Capture architecture

New module in the `RouterInstance` composition
([code-architecture.md](code-architecture.md)), beside `PresenceMonitor`:

```text
LinkStatsCollector     # polls WAN endpoint protocol statuses; per-peer rollup;
                       # publishes ActRouterLinkStats on the LAN (D14)
```

- **Poll loop, one owner.** A periodic tick (**config-fixed** in YAML —
  `link_stats_period`, default 1 s, aligned with the `RouterHealth` heartbeat; constant per
  run so experiment sweeps stay comparable — no runtime command, no adaptive cadence)
  posted through the controller's event machinery like everything else. Each tick, for each
  **registered** WAN writer/reader × matched peer endpoint: read the matched-endpoint
  statuses, compute deltas from the previous totals snapshot, fold into the per-peer
  accumulator. Status getters are in-process reads — cheap at router endpoint counts.
- **Endpoints reach the collector by registration, not discovery (D81).** The protocol
  statuses are typed-only getters (`AnyDataWriter` exposes none of them), so polling lives
  where the payload type is known: a `collect_wan_stats` virtual on
  `RouteTopicRuntimeBase` (the `forwarded()`/`set_partitions()` pattern), implemented in
  `RouteTopicRuntime<T>`; runtimes with a WAN-side leg register at build and unregister at
  close on the controller strand (no locks; delta baselines dropped with the endpoint).
  `PresenceMonitor` registers its `RouterHealth` pair — mandatory, it is the idle-mesh
  bellwether. The collector owns its own probe pair. The collector is **active only when
  presence is active** (no `presence_participant` → no designated WAN participant, no
  bellwether, no collection). Participant-level `find()` enumeration was rejected:
  un-pollable type-erased handles, no ownership context, teardown races.
- **Gauges vs. deltas.** Deltas for event counters (NACKs, pushed/pulled, full-window
  events, lost-by-writer…); point-in-time gauges for occupancy (`unacknowledged_sample_count`,
  `send_window_size`, `uncommitted_sample_count`, inactive-reader count).
- **Discovery gating.** A `pulled_*` burst right after a peer (re)match is `TRANSIENT_LOCAL`
  replay, not link loss. The collector stamps each interval with match/rediscovery events
  from `DiscoveryDispatcher` (a `rediscovery_in_interval` flag) so analysis can exclude or
  down-weight those intervals — raw counters still published unmodified.
- **RTT probe.** `on_application_acknowledgment` timestamps per peer feed min/mean/max/count
  into the same per-peer record each interval. **App-ack delivery is listener-only**
  (verified: users manual §34.6.1 + headers — a `datawriter_application_acknowledgment()`
  StatusMask bit exists but there is no getter/condition path to `AcknowledgmentInfo`, so a
  waitset wake would carry no payload). D81 contains the codebase's first listener to the
  probe writer alone: exactly that mask, callback does clock-read + push
  `(subscription_handle, sample_identity, recv_time)` into a mutex-guarded accumulator the
  collector drains on its tick (no `ControllerEvent`s — telemetry, not state); listener
  reset before writer close (D31/D32 discipline). Per-peer attribution via the same
  `matched_subscription_participant_data(subscription_handle)` call. ~~Gated on
  `spikes/link_probe/` (the `KEEP_LAST(1)` × `APPLICATION_AUTO` retention interaction is
  unvalidated — the same concern that rejected `RouterHealth` reuse applies to the probe
  itself); pinned fallback if disproven: a probe/echo topic pair measured entirely with
  `ReadCondition`s (waitset-pure; costs a responder per router and a second topic).~~
  **Gate cleared (2026-07-17): `spikes/link_probe/` PASSED 3/3** — retention is benign
  (`KEEP_LAST(1)` replacement proceeds, `write()` never blocks behind a non-taking peer;
  a replaced-never-taken sample produces NO app-ack, so an ack always means the peer
  consumed the sample), attribution and ~1 Hz cadence proven, RTTs ms-scale on `lo`.
  Join send-times by the ack's 1-based RTPS `sequence_number`, not payload `probe_seq`.
  The echo fallback is retired unused.
- **Publication: LAN only.** Per-peer `ActRouterLinkStats` samples on the LAN admin plane —
  WAN-frugal (nothing new crosses the constrained link), consistent with the
  `ActRouterMeshStatus` pattern. Additionally one structured log line per poll interval
  (`source=router`, `event=link_stats`) so the correlation experiment can run from logs
  alone, before any dashboard exists.
- **No `state_revision` interaction.** Counter/metric deltas explicitly do not bump revision
  (D5); link stats are a telemetry stream, not router state.
- **Alternative rejected:** RTI Monitoring Library 2.0 publishes similar telemetry but adds
  a participant + its own topics and doesn't know our peer-router rollup key. The router is
  the measurement point; in-process getters are strictly cheaper and stay off the WAN.

### `ActRouterLinkStats` IDL sketch

Raw counters by design — derived ratios (repair ratio, window utilization, NACK rate) are
computed at analysis time, so their definitions can change without touching the wire type.

```idl
// LAN: per-interval reliable-protocol metrics for one WAN link (observer → peer).
// Keys are the D74/D79 name-only identity (router_id is retired); types land in
// router/admin/RouterAdminTypes.idl (D75/D81 single codegen path).
struct RouterLinkStats {
    @key string observer_router;     // "<node>/<router>" (D79)
    @key string peer_router;         // "<node>/<router>" (D79)
    string network;                  // local WAN participant name (D18: (peer, network) when N>1)
    uint64 capture_seq;
    int64  capture_timestamp;        // ns since epoch
    uint32 interval_ms;              // actual elapsed poll interval
    boolean rediscovery_in_interval; // TRANSIENT_LOCAL replay may inflate pulled_*

    // writer-side rollup: this router's WAN writers → peer's readers (interval deltas)
    uint64 pushed_samples;   uint64 pushed_bytes;
    uint64 pulled_samples;   uint64 pulled_bytes;
    uint64 nacks_received;   uint64 nack_frags_received;
    uint64 heartbeats_sent;
    uint64 send_window_full_events;
    uint64 high_watermark_events;
    // writer-side gauges (worst across this peer's matched endpoints)
    uint32 unacked_samples;  uint32 unacked_samples_peak;
    uint32 send_window_size_min;     // adaptive window: min current capacity seen
    uint32 inactive_reader_count;
    uint64 oldest_unacked_lag;       // last_available_sn - first_unacked_sn, max

    // reader-side rollup: peer's writers → this router's WAN readers (interval deltas)
    uint64 samples_received; uint64 duplicates_received;
    uint64 nacks_sent;
    uint64 out_of_range_rejected;
    uint64 samples_rejected_local;   // resource-limit rejections (not link loss)
    uint64 samples_lost_by_writer;   // reliability failed end-to-end
    // reader-side gauges
    uint32 uncommitted_samples;      // max across matched endpoints

    // probe RTT (app-ack roundtrips observed this interval)
    uint32 rtt_count;
    uint32 rtt_min_us;  uint32 rtt_mean_us;  uint32 rtt_max_us;
};
```

## Deferred: inference and the correlation experiment

Nothing in this phase maps metrics to `ROUTER_OK/DEGRADED/ERROR`, feeds
`n_degraded`/`overall_state`, or alerts. Presence (`RouterHealth` DEAD/STALE) remains the
**only** health authority. Rationale: every threshold we could write today would be a guess —
e.g. NACKs at some steady rate are the reliability protocol *working*, not failing; where
"working" ends and "degraded" begins is an empirical question.

The gate to inference is a **link-degradation correlation experiment**:

1. Two routers over an impaired link — netem/EMANE (impairment is the emulated network's
   job per Tenet 3; here it is the experiment's ground-truth generator, not a relay feature).
2. Sweep impairments one axis at a time (delay, jitter, loss %, rate limit, blackout), with
   representative traffic (steady state topic + bursts) and idle periods.
3. Record `ActRouterLinkStats` intervals + the ground-truth netem schedule.
4. Analyze which metrics track which impairment, with what lag and what noise floor
   (expected, to verify: loss → `nacks_*`/repair ratio; rate limit → window shrinkage,
   `unacked_samples`, watermark/full events; delay → `rtt_*`, `oldest_unacked_lag`;
   blackout → inactive readers, then presence STALE/DEAD).
5. Only then pin thresholds/classification as a follow-up decision (per-link
   OK/DEGRADED/ERROR feeding the `RouterHealth` rollup and `ActRouterMeshStatus`).

## Open choices

All resolved 2026-07-08 (discussion pinned in D14):

- ~~Probe carrier: `RouterHealth` vs. dedicated topic.~~ **Dedicated `RouterLinkProbe`
  topic** — discovery cost accepted for isolation from the presence authority; rationale
  above.
- ~~Poll period control.~~ **Config-fixed** (YAML, default 1 s, constant per run);
  `interval_ms` in the sample leaves command-adjustability open later with no wire change.
  Adaptive cadence rejected: measurement cadence must not depend on the measured signal.
- ~~Per-topic detail.~~ **Per-peer rollup only** in the published sample; per-topic remains
  observable at the source (matched-endpoint statuses are per topic-endpoint × peer) and can
  be added later if the experiment shows per-topic divergence matters.
- ~~Topic vs. log-only.~~ **Both from the start** — the LAN topic makes the experiment
  recordable with stock DDS tooling; the log line keeps it runnable offline.
- ~~Build phasing.~~ **Own thin phase immediately after the Presence/Health phase**
  (reuses the roster/D15-tag GUID→router join and the WAN entities). The correlation
  experiment is its own `spikes/` entry (PLAN.md + Python netem driver, per repo
  convention) anchored to this phase.
