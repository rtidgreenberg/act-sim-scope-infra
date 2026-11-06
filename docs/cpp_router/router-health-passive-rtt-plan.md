# Replacing RouterLinkProbe with passive RTT in compact RouterHealth

Date: 2026-11-06

Status: Implemented and validated (2026-11-06). The sections below retain the
design and acceptance requirements; validation results are recorded at the end.

## Goal and scope

Remove the dedicated WAN `RouterLinkProbe` writer/reader and its application-ack
traffic. Measure per-peer RTT by piggybacking acknowledgment metadata on the existing
compact `RouterHealth` samples, without changing either router's publication schedule.

Keep:

- Compact route summaries and always-on peer/link visibility.
- Detailed remote diagnostics on the existing debug-mode path.
- Health deadline/liveliness behavior and independent PresenceTick, MeshTick, and
  LinkStatsTick cadences.
- WAN protocol-counter collection and LAN `ActRouterLinkStats` publication.
- RouterHealth sequence/timestamp delivery auditing.

This step removes a periodic application probe stream, not all periodic discovery
traffic. Putting health metadata onto regular SPDP2 assertions is the separate,
verify-first product request in [LP-7](../product-gaps.md). It is not a prerequisite.

## 1. Wire contract

Author types in [ActTypes.idl](../../harness_v2/datamodel/ActTypes.idl), then regenerate
the committed XML and build-generated C++ types.

Proposed additions:

| Location | Field | Purpose |
|---|---|---|
| `RouterHealth` | `uint64 session_id` | Nonzero incarnation token generated once per health-writer lifetime; changes on process restart or writer recreation |
| `RouterPeerRef` | `uint64 ack_session_id` | Session of the peer heartbeat being acknowledged; zero means no acknowledgment |
| `RouterPeerRef` | `uint64 ack_heartbeat_seq` | Exact peer heartbeat sequence acknowledged |
| `RouterPeerRef` | `uint64 ack_age_at_send_us` | Local monotonic elapsed duration from receiving that heartbeat to publishing this health sample |

Reuse `RouterPeerRef.router` inside `RouterHealth.peers_seen` as the acknowledged
peer identity. Do not add a second peer-name list, absolute RTT timestamps, or a
per-peer acknowledgment topic.

Keep the existing `last_seen_delta_ms` for presence/topology. It is not the RTT
field: it has different precision and can be aged when constructing LAN mesh views.
The acknowledgment age is immutable evidence from the original health publication;
never age or recompute it when embedding a cached health sample into MeshStatus.
Only direct WAN RouterHealth receipts contribute RTT, not transitive mesh summaries.

Preserve `send_timestamp` for existing wall-clock audit consumers. RTT uses only
monotonic local durations and does not depend on that field.

Use the repository's existing session-token utility if one exists; otherwise use
a small nonzero random-token generator with collision/error handling. Do not use
router name, controller state revision, or a restart-reset counter as the token.

### Sequence example

1. A publishes heartbeat `(A-session, 42)` and retains its monotonic send time.
2. B receives it and stores the tuple plus its local monotonic receipt time.
3. B does not send a response.
4. On B's next scheduled health publication, its peer entry for A contains
   `(A-session, 42, age_at_send_us)`.
5. A receives B's health sample and records its local monotonic receipt time.
6. A finds its retained send time for 42 and calculates:

   ```text
   RTT = (A receipt time - A send time for 42) - B reported age at send
   ```

7. A's next scheduled health sample can acknowledge B's heartbeat independently,
   allowing B to measure the reverse round trip.

For 725 ms total elapsed and 680 ms receiver wait, RTT is 45 ms. There is no clock
synchronization and no subtraction between timestamps from different machines.

## 2. Timing ownership and correctness

Implement the measurement state in
[PresenceMonitor](../../router/src/core/PresenceMonitor.hpp), beside its existing
direct peer roster and health writer/reader. Keep any extracted timing helper
DDS-free and limited to what deterministic unit tests require.

- Capture receipt time while extracting each valid direct health sample, before
  roster processing, audit logging, or LAN mesh publication.
- Track the newest accepted heartbeat sequence within each peer session. Duplicate
  or older samples must not replace that heartbeat's original receipt timestamp.
  Preserve intended presence handling separately from RTT ordering.
- Snapshot acknowledgment records and calculate their ages as close as possible
  to `health_writer_.write()`. Do not hold the roster/timing mutex across DDS calls.
- Record the local send boundary immediately before the write, using the same
  boundary used to finalize acknowledgment ages.
- Reserve send-history state before writing so a concurrent receive callback can
  correlate it. Finalize on write success; cancel on failure. If acknowledgment
  processing races a pending write, defer it until the write outcome is known.
- Process each `(responding peer session, acknowledged local session, sequence)`
  at most once. Repeated advertisements of the same acknowledgment do not create
  fresh measurements.
- Reject self samples, duplicate-name foreign writers, session mismatches, missing
  send history, malformed acknowledgments, excessive age, negative results, and
  values outside the output type's range. Use structured, rate-limited diagnostics
  or rejection counters; never clamp an invalid RTT into a valid zero.
- Clear peer correlation/deduplication state on incarnation changes and peer
  removal. Prevent delayed samples from retired sessions from reverting RTT state;
  bind acceptance to observed writer identity/lifecycle, with bounded retired-state
  bookkeeping.
- Bound send history by both count and elapsed age; bound peer/deduplication state
  and any pending-measurement queue. Proposed initial send-history limits are
  64 entries and 32 seconds, subject to loss/cadence tests.
- Omit acknowledgments that exceed the accepted age horizon. Reuse the existing
  bounded roster, but ensure live peers are not starved by retained dead entries;
  use deterministic rotating coverage if eligible peers exceed the wire cap.
  Expose truncation/coverage diagnostics.

This is **heartbeat-based application-observed RTT**, including receive dispatch
and send queueing outside the measured remote wait. The forward and return legs
can occur under different network conditions. The implementation must not claim
actual wire-send timestamps unless a supported, empirically verified timing hook
is used. Do not change health QoS just to make RTT look better.

## 3. Feed the existing link-stats path

Extend the DDS-free [LinkStatsSink](../../router/src/core/LinkStatsSink.hpp) with a
per-peer RTT contribution. PresenceMonitor accumulates completed measurements;
its existing `collect_wan_stats()` drains interval measurements into the sink
alongside its health protocol-counter deltas.

[LinkStatsCollector](../../router/src/core/LinkStatsCollector.cxx) remains the owner
of LAN aggregation/publication:

- Preserve `rtt_count`, `rtt_min_us`, `rtt_mean_us`, and `rtt_max_us`, now derived
  from accepted heartbeat measurements rather than probe application ACKs.
- Preserve existing protocol-counter attribution through the discovery database.
  RTT identity comes from the validated direct health peer, not that lookup.
- Retain the behavior that RTT-only peers can receive a link-stats record.
- Add an explicit per-peer RTT validity/freshness contract: proposed
  `rtt_last_sample_age_ms` plus `rtt_last_ack_age_us`, with a validity boolean.
  The first is elapsed time since the last accepted local measurement; the second
  records the separation between its forward and return legs. An interval with
  `rtt_count == 0` has no interval min/mean/max measurement, even if a previous
  measurement remains available.
- Keep all this telemetry-only: no controller state-revision bumps or automatic
  presence/health-policy changes from RTT.

## 4. Remove the probe completely

After passive RTT passes deterministic and DDS tests:

1. Delete `ProbeAckListener`, `ProbeAckAccumulator`, probe send-history/RTPS-sequence
   mirroring, probe take conditions, and probe writes from the collector.
2. Simplify its constructor and shutdown to the remaining LAN writer and source
   registry. Preserve source lifetime and shutdown ordering.
3. Remove probe QoS loading and WAN/AsyncWaitSet constructor wiring from
   [router_main.cxx](../../router/src/router_main.cxx).
4. Delete the `RouterLinkProbe` IDL type and regenerate
   [ActTypes.xml](../../harness_v2/datamodel/gen/ActTypes.xml).
5. Remove `router_link_probe` from the external
   [QoS profiles](../../harness_v2/qos/act_qos_profiles.xml) and the dashboard
   [delivery profile mapping](../../gui/mesh_dashboard/server/delivery_config.py).
6. Update tick comments, active configuration references, feature inventory, and
   link-health/presence documentation. Keep historical probe spikes as historical
   evidence, clearly labeled, rather than rewriting their results.
7. Update [traffic accounting](../../debug/scripts/traffic_accounting.py) and its
   tests: remove active probe assumptions; retain legacy capture classification
   if historical reports need it.

Do not remove LinkStatsTick or `link_stats_period_ms`: they still control protocol
polling and LAN telemetry, independently of heartbeat timing.

## 5. Dashboard and compatibility

- Keep edge RTT displays, relabel them as heartbeat RTT, and explain that the
  peer wait is subtracted.
- Render absent interval measurements as unavailable, not zero latency. Show
  freshness and acknowledged-heartbeat age; do not silently retain stale values
  as current.
- If changing static JavaScript, bump its script query version and verify rendered
  labels, freshness states, and the loaded asset revision in a browser.
- Treat changes to RouterHealth and nested RouterPeerRef as a schema rollout.
  Verify actual generated-type assignability before promising mixed-version
  interoperability; plan a coordinated owned-lifecycle restart by default.
- Regenerate/update every active type consumer and fixture, including mesh JSON,
  Python probes, dashboard schemas, and delivery auditing. Preserve the compact
  route/detail split and audit key `(run_id, topic, source_node, sequence)`.
- Extend LP-7 references and record the new decision without presenting the
  SPDP2 product request as implemented.

## 6. Validation and acceptance gates

### Deterministic unit tests

Use an injected monotonic clock; no DDS entities:

- 20 ms forward + 680 ms peer wait + 25 ms return produces exactly 45 ms RTT.
- Different publication phases and long arbitrary waits do not inflate RTT.
- Newer received heartbeats replace the acknowledgment tuple; duplicates do not
  reset its receipt time or count again.
- Independent measurements for multiple peers and each direction.
- Restart/writer recreation, retired-session packets, reordering, failed writes,
  and concurrent receive/write-finalization paths.
- History expiry/caps, roster overflow/coverage, malformed ages, negative results,
  conversion overflow, and freshness/no-measurement reporting.
- Wall-clock jumps do not affect RTT. Presence and mesh revisions retain their
  existing behavior.

### Targeted DDS integration tests

Update [test_link_stats.py](../../router/test_e2e/test_link_stats.py) and add direct
health metadata checks:

- Both routers obtain RTT from health samples with no probe endpoints.
- RTT remains available with no application route traffic.
- Existing protocol counters, LAN-only stats, non-default stats cadence, presence,
  mesh/team behavior, and RouterHealth delivery auditing remain correct.
- Loss/recovery and one-router restart cannot produce false correlation.

Start with targeted tests, then run the router regression gate after rebuilding
in a verified Connext-capable environment. Keep domains within the harness's
reserved range and at or below 232. Never run DDS e2e tests against a live mesh.

### Wire and size verification

Replace the current probe-presence wire assertion with:

- Positive evidence of mutual health delivery and accepted RTT samples.
- No RouterLinkProbe SEDP endpoints or DATA traffic.
- No extra periodic response topic, triggered heartbeat, or application-ACK
  channel introduced by the replacement.
- ActRouterLinkStats/admin telemetry still LAN-only.
- Equal-duration, equivalent-topology before/after captures with separate startup
  and steady-state windows, attributed by router GUID prefix.
- Actual serialized health size and fragment/frame counts for the representative
  control-plus-five-platform topology and configured peer limit. The representative
  compact heartbeat must remain unfragmented on the measured path; larger-peer
  limits require an explicit documented size/coverage budget.
- Total steady-state WAN bytes/s must decrease in the representative topology
  after accounting for added per-peer acknowledgment metadata. If metadata growth
  erases the saving, revise packing/coverage before accepting the bandwidth claim.

Use bounded captures and owned test lifecycle cleanup. A startup-inclusive byte
total divided by a nominal steady-state interval is not a valid savings measurement.

## Completion

The change is complete when passive RTT is correct under the edge cases above,
all active probe surfaces are removed, compact-health and existing dashboard/
presence/audit behavior are preserved, and wire captures demonstrate the promised
stream removal and representative bandwidth saving. SPDP2 remains a later product
integration step.

## Implementation and validation results (2026-11-06)

- Removed the probe type/profile/endpoints/listener and collector WAN/AWS wiring.
  Passive monotonic timing lives in the DDS-free `HeartbeatRtt` helper owned by
  `PresenceMonitor`; its measurements flow through `LinkStatsSink`.
- Added session/ack metadata and validity/freshness fields; regenerated committed XML.
  Updated active configuration, dashboard labels/versions/JSON contracts, and
  documentation. Historical probe captures/spikes remain explicitly historical.
- Rebuilt in the Connext 7.7 container: **5/5 C++ unit tests passed**.
  Python regression: **234 passed, 23 skipped, 155 subtests passed**; optional
  environment-dependent tests remain skipped, not claimed as passing.
  Targeted link tests passed, including idle bidirectional RTT, 250 ms stats cadence,
  writer restart/incarnation metadata, and startup SEDP absence of probe/WAN admin
  endpoints. Deterministic tests cover wait subtraction, duplicates/reordering,
  independent directions/peers, write-finalization races/failure, retired sessions,
  history/peer/pending caps, expiry, malformed durations, and rotating live coverage.
- Used the owned EMANE lifecycle for a coordinated restart; restored the original
  **control plus six platforms** (a larger representative topology than control plus
  five). Every expected control-side peer is ALIVE/OK with fresh passive RTT.
- Equal 12-second steady-state captures on the control RF interface, excluding startup:
  total WAN traffic **27,028.7 -> 20,313.8 bytes/s** (24.8% lower).
  The retired probe's DATA/protocol/app-ACK traffic cost 8,280 bytes/s; health metadata
  added 1,104 bytes/s. Application traffic varied slightly between the two nominal
  runs, so these totals are observations, not a universal savings percentage.
  Health publication/frame counts stayed 144 per capture; observed health frame
  sizes grew from 926/1,038 to 958/1,190 bytes, remaining unfragmented.
  Application ACK/confirmation frames fell from 288 to zero. Larger peer limits
  are bounded but are **not** promised to remain unfragmented.
- Headless local-browser validation: current script revisions, seven nodes/twelve
  edges, heartbeat RTT/age/ack-age labels, remote-wait explanation, no JS errors.
  The shared Dev Tunnel required GitHub authentication; validation used the actual
  local dashboard instead, without weakening tunnel access controls.
- GUI-applied one-way CommEffect **200 ms latency + 10% loss**:
  selected RTT median **271.0 ms**, unrelated median **41.4 ms**; reset restored
  selected median **69.3 ms**. All expected peers stayed ALIVE; the controller
  finished with an empty impairment list.
- Resolved the follow-up review's recovery/freshness findings. Publication liveliness,
  not discovery matching, gates incarnation replacement. Roster pruning retains
  bounded dormant writer/session high-water marks without permanently retiring a
  returning physical writer; superseded sessions remain rejected. No fixed QoS
  or presence timing was changed.
- Link-stat freshness is monotonic end to end: router sample age, bridge-cache age,
  and browser `performance.now()` elapsed time. Opaque bridge sample tokens prevent
  repeated cached snapshots from rejuvenating measurements. Wall-clock capture/send
  timestamps remain audit/display fields only.
- Follow-up validation: **5/5 C++ tests**, **5 browser/bridge tests**, and **5 real-DDS
  link tests** passed. Real DDS covers graceful restart, crash recovery while the old
  publication remains matched but no longer alive, and unchanged-session recovery
  after the >60 s outage actually triggers roster pruning. Browser tests cover wall
  clock jumps, stale caches, repeated snapshots, and open-inspector expiry.
  Restored the seven-node EMANE mesh again: all six control peers ALIVE/OK with valid
  RTT, empty impairments, current v2 script loaded, no browser errors, and live RTT
  still available under browser wall-clock offsets of +24 h and -24 h.

Coordinated restart is the rollout contract. Mixed-version type assignability and
SPDP2 health/discovery consolidation are not claimed.
