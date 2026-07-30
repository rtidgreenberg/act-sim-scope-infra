# Mesh Presence Monitoring: Approach Comparison

> Decision context for how routers detect each other's liveliness, measure link quality,
> and build a live mesh graph. Evaluates the "epoch flood" concept against the current
> pairwise-heartbeat design and other alternatives.

## Approach 1: Epoch Flood / Rebroadcast

An originator publishes an epoch sample containing `{originator_id, epoch_seq,
stamps: sequence<{router_id, timestamp}>}`. Every router that receives it appends its own
stamp and rebroadcasts (floods) to all peers. Each router forwards a given
`{originator, epoch_seq}` at most once (forward-once dedup). The originator eventually
receives copies back via every reachable path; each copy's stamp sequence encodes the route
it traversed. When the originator has either (a) received copies from all expected paths, or
(b) a timeout expires, it closes the epoch. Missing stamps or missing paths reveal dead
nodes/links.

### Pros

- **O(diameter) detection latency** — propagation is parallel across the mesh, not serial.
  For a well-connected 8-node mesh with diameter 2–3, epoch completion takes 2–3
  hop-times, not 8.
- **Path diversity reveals topology** — the originator sees *which paths* samples
  traversed. Stamp sequences like `[A, B, D]` vs. `[A, C, D]` prove two independent
  routes to D exist.
- **Partial failures are visible** — if node D dies, copies routed through D never arrive,
  but copies through surviving paths do. The originator can diff expected vs. received
  paths to localize the failure.
- **Jitter measured per-path** — variance in arrival time of the same epoch via different
  paths captures per-path jitter without extra instrumentation.
- **Mesh graph emerges from aggregated stamp sequences** — union of all received paths
  for an epoch = the reachable topology graph.
- **Single mechanism** for liveliness + path discovery + per-path latency — fewer moving
  parts than separate presence + probe + topology topics.

### Cons

- **O(E) traffic per epoch (forward-once)** — with dedup, each router forwards once to all
  its peers. In a fully-connected 8-node mesh (28 edges), that's 28 forwarding events per
  epoch. If every router initiates its own epoch (to get bidirectional visibility), total
  traffic is O(N×E) = 224 samples/sec at 1 Hz — compared to 8 samples/sec for pairwise
  heartbeats.
- **Without dedup: combinatorial explosion** — the number of simple paths in a fully-connected
  N-node mesh is O((N-2)!). Even with TTL bounding, an 8-node mesh without dedup generates
  hundreds of samples per epoch. Dedup is mandatory.
- **Deduplication state** — every router must maintain an `{originator, epoch_seq} → forwarded`
  map with expiry. This is application-level protocol state that DDS does not manage;
  stale-entry cleanup, epoch numbering wraparound, and late-arriving duplicates must all be
  handled.
- **Payload growth is path-dependent** — a sample traversing 5 hops carries 5 stamps; the
  longest-path copy carries up to N stamps. Bounded by mesh diameter with forward-once
  dedup, but still variable-size per sample.
- **Per-hop latency requires clock sync** — adjacent stamps come from different routers.
  Without NTP/PTP discipline, per-hop deltas are unreliable. Only the full round-trip
  (same originating clock on send and receive) gives clock-independent latency — and
  that's *path* latency, not per-link.
- **Epoch initiation problem** — if every router starts its own epoch, traffic is N× the
  single-originator cost. If only one router initiates, it's a single point of failure and
  requires leader election or a fixed designator. Rotating initiators add coordination
  complexity.
- **Failure isolation is indirect** — you learn that certain paths are broken (copies
  missing), but localizing *which link* failed requires analyzing which stamp sequences are
  absent and which are present — combinatorial reasoning that ultimately converges to
  needing per-link knowledge anyway.
- **Epoch timeout tuning** — the originator must wait long enough for the longest surviving
  path to arrive before closing the epoch. Too short = false negatives (healthy path
  declared dead). Too long = slow detection. This timeout is mesh-topology-dependent and
  must adapt as the mesh changes.

### DDS implementation considerations

- **N writers on one topic** — each router's "rebroadcast" is a new `write()` with a
  modified payload from its own writer. N writers on one topic means N independent RTPS
  sequence-number streams; a reader's NACK to writer B is unrelated to samples from
  writer A. `RELIABLE` delivery works per-writer, but the application must correlate
  samples across writers by `{originator, epoch_seq}`.
- **`KEEP_LAST(1)` vs. `KEEP_ALL`** — with `KEEP_LAST(1)` keyed by originator, intermediate
  forwarding samples can be overwritten before the originator reads them. `KEEP_ALL` with
  sufficient history depth is needed, adding resource-limit configuration.
- **`BEST_EFFORT` may be more natural** — the epoch is inherently tolerant of loss (a
  missing copy just means one path wasn't observed this epoch). `RELIABLE` adds repair
  traffic that compounds the already-elevated sample count.
- **Content-filter complexity** — each router must avoid re-processing samples it already
  forwarded. Either filter in the reader (subscribe with a content-filter excluding own
  stamp), or filter in application logic post-read. Neither composes cleanly with
  Connext's built-in filtering when the filter predicate involves a variable-length
  sequence field.
- **Epoch lifecycle and "closed" state** must be managed entirely in application logic —
  DDS provides no built-in epoch/barrier/consensus primitive. `DEADLINE` and `LIVELINESS`
  QoS cannot substitute for the timeout-based epoch-close logic.

---

## Approach 2: Pairwise Heartbeat + Roster (Current Design)

Each router publishes a periodic `RouterHealth` sample (1 Hz). Every other router maintains
a per-peer "last-seen" roster. Presence = "received your heartbeat within `DEADLINE`."
Latency = separate `RouterLinkProbe` topic with app-ack RTT. Mesh graph = each router's
`peers_seen` edge-list in its `RouterHealth` payload (D77).

### Pros

- **O(1) detection latency** — bounded by `DEADLINE` (1.5–2× heartbeat period),
  independent of mesh size. One dead router doesn't delay detection of any other.
- **Per-link fault isolation** — each pair is independently monitored; a failure on link
  A→B doesn't mask the health of link C→D.
- **Leverages DDS QoS natively** — `DEADLINE`, `LIVELINESS`, `TRANSIENT_LOCAL` give
  presence detection, stale detection, and late-joiner catchup with zero application-level
  protocol logic.
- **Mesh graph without serial dependency** — each router publishes who *it* sees; any
  observer (C2, dashboard) unions edge-lists into the full graph in one read, without
  waiting for a token to circulate.
- **Scalable** — O(N) total WAN samples/sec (each of N routers publishes 1 sample/period);
  readers are N-1 per router. No quadratic traffic.
- **Compact, fixed-size payload** — `RouterHealth` carries summary status + a bounded
  `peers_seen` list; size doesn't grow with epoch depth.
- **Clean separation of concerns** — presence (heartbeat), quality (probe RTT), capacity
  (protocol-stats harvest) are independent mechanisms that can evolve separately.

### Cons

- **No single-sample full-path latency** — you get per-link RTT (from the probe) but not
  a single end-to-end measurement across the full mesh path. (Mitigated: path latency =
  sum of per-hop RTTs when the graph is known.)
- **Two topics for presence + latency** — `RouterHealth` for liveliness, `RouterLinkProbe`
  for RTT. Slightly more topic surface than a single-mechanism approach.
- **Mesh graph is eventually-consistent** — the union of `peers_seen` lists converges
  within one heartbeat period, not instantaneously. (Acceptable: sub-second convergence at
  1 Hz.)

---

## Approach 3: Gossip / SWIM-like Protocol

Each router periodically picks a random peer to "ping." If no ACK within timeout, it asks
K other peers to indirectly probe the suspect. Only after K indirect failures is the node
declared dead. Dissemination of membership changes is piggybacked on protocol messages.

### Pros

- **O(log N) detection** with bounded false-positive rate — very robust to transient
  packet loss and partial network partitions.
- **Constant per-node bandwidth** — each node sends a fixed number of probes/period
  regardless of mesh size.
- **Handles asymmetric failures** — indirect probing distinguishes "I can't reach X" from
  "X is dead."

### Cons

- **Overkill for small meshes** (< ~50 nodes) — the protocol complexity buys little when
  N < 20.
- **Complex to implement correctly atop DDS** — DDS's participant liveliness and
  `DEADLINE` already provide the protocol-level equivalent; reimplementing at the
  application layer duplicates infra.
- **No latency measurement** — SWIM detects presence, not quality; still needs a separate
  RTT mechanism.
- **Non-deterministic detection time** — probabilistic guarantees are harder to reason
  about for real-time/tactical SLA than the deterministic `DEADLINE`-based bound.

---

## Approach 4: All-to-All Active Probe Matrix

Every router sends a dedicated ping to *every* peer; each peer echoes it back. Gives
per-link RTT and loss rate directly from each probe round.

### Pros

- **Per-link RTT without clock sync** — same-origin round-trip; no NTP/PTP required.
- **Complete loss matrix** — every directed link is independently measured every period.
- **Simple logic** — send, wait, measure. No protocol state machine.

### Cons

- **O(N²) traffic** — 8 nodes = 56 probe pairs/period; 20 nodes = 380. Doesn't scale
  past ~20-30 nodes on constrained WAN without rate limiting.
- **Redundant with DDS reliability** — the RTPS protocol already sends HEARTBEATs and
  NACKs between matched endpoints; harvesting those counters (Approach 5) gives loss/RTT
  signals for free.

---

## Approach 5: Passive Protocol-Stats Harvesting

No extra probes. Harvest RTPS reliability counters (`nack_count`, `uncommitted_sample_count`,
heartbeat timing, send-window size) from existing WAN pairs — including the always-flowing
`RouterHealth` writer/reader pair. Infer link quality from protocol behavior.

### Pros

- **Zero additional WAN traffic** — pure in-process counter reads.
- **Works even on idle links** — `RouterHealth` heartbeat is always flowing, so
  reliability stats always accrue.
- **Rich signal surface** — loss (`nack_count`), congestion (`send_window_size`),
  backpressure (`unacknowledged_sample_count`), reordering (`duplicate_sample_count`).

### Cons

- **No clean RTT without an active probe** — protocol-level heartbeat/ACK timing gives a
  coarse RTT estimate but not a precise one (bundled with processing delay).
- **Inference requires calibration** — raw counters don't directly map to "good/bad" link
  without a controlled degradation experiment to establish thresholds.
- **Coupled to traffic patterns** — signal quality degrades on truly idle links (though
  `RouterHealth` prevents full silence).

---

## Summary Matrix

| Criterion | Epoch Flood | Pairwise HB + Probe | SWIM Gossip | All-to-All Probe | Passive Stats |
|-----------|:----------:|:-------------------:|:-----------:|:----------------:|:-------------:|
| Detection latency | O(diameter) | **O(1)** | O(log N) | O(1) | O(1)† |
| Per-link fault isolation | Indirect | **✓** | ✓ | ✓ | ✓ |
| Per-link RTT | ~‡ | **✓** (probe) | ✗ | ✓ | ~‡ |
| Full-path latency | ✓ (per-path) | sum-of-hops | ✗ | sum-of-hops | ✗ |
| WAN traffic cost | O(N×E)§ | **O(N)** | O(N) | O(N²) | **O(0)** |
| Mesh graph | ✓ (from paths) | **✓** (from roster) | ✓ (membership) | ✓ (reachability) | ✗ |
| Clock-sync required | Yes (per-hop) | **No** | No | No | No |
| DDS-native QoS leverage | Low | **High** | Low | Medium | **High** |
| Implementation complexity | High | **Low** | High | Low | Medium |
| Failure mode | Timeout-dependent | **Independent** | Probabilistic | Independent | Dependent on traffic |

† Via `DEADLINE` on the harvested topic.  
‡ Coarse estimate from protocol timing, not precise RTT.  
§ O(E) per epoch with forward-once dedup; O(N×E) if all N routers initiate.

---

## Recommendation

The pairwise heartbeat + dedicated probe design (Approach 2, already specified in D14/D77)
is the strongest fit for a WAN-constrained tactical mesh because:

1. **Detection is O(1) and per-link** — bounded by `DEADLINE`, independent of mesh size.
   The epoch flood's detection latency is O(diameter) at best, and timeout-dependent when
   paths are broken.
2. **Leverages DDS primitives** — `DEADLINE` + `LIVELINESS` + `TRANSIENT_LOCAL` give
   presence detection for free; no application-level flood/dedup/epoch protocol to maintain.
3. **Mesh graph without flood overhead** — `peers_seen` edge-lists converge in one
   heartbeat period (O(N) traffic) without the O(N×E) cost of flooding epochs from every
   router.
4. **Separation of concerns** — presence, latency, and capacity are independent
   mechanisms that can be tuned, disabled, or extended without coupling.
5. **Traffic cost** — 8 routers at 1 Hz = 8 samples/sec total. Epoch flood with all
   routers initiating on a fully-connected 8-node mesh = 224 samples/sec (28× more).

The epoch flood is a strong concept for **topology/path discovery and characterization** in
a stable mesh — it reveals multi-path diversity that pairwise heartbeats cannot. It could
serve as a **supplemental, low-rate diagnostic** (e.g., every 10–30 s, single designated
initiator) atop the primary pairwise presence system. Use cases:

- Verifying that redundant paths exist between critical nodes.
- Measuring per-path latency variance to inform routing decisions.
- Detecting asymmetric reachability (A→B works, B→A doesn't) before it causes data loss.

But it should not *be* the presence system, because its failure detection depends on a
timeout (how long to wait for paths that may never arrive?), and its traffic cost scales
with edge count rather than node count.

If path-level end-to-end latency becomes a primary requirement, consider a **source-routed
diagnostic probe** (initiated by C2 or a designated root, forwarded along a *specified*
path, returned to origin) as a controlled, on-demand alternative — it gives the same
per-path measurement without the flood's combinatorial traffic.

---

## Mesh Protocol Lineage: Why OLSR HELLO + ETX Is the Right Model

The recommended design (Approach 2) is not ad-hoc — it maps directly to the best-studied
algorithms in the MANET (Mobile Ad-hoc Network) mesh routing literature. This section
documents the lineage and the one enhancement it motivates.

### Protocol comparison

| Mesh protocol | Core mechanism | What it optimizes for | Applicability here |
|---------------|---------------|----------------------|-------------------|
| **OLSR** (RFC 3626 / OLSRv2 RFC 7181) | Periodic HELLO (1-hop) + TC flooding (multi-hop) + ETX link metric | Proactive link-state for dense mobile meshes | **Direct analog** — HELLO = `RouterHealth`, TC = `peers_seen` edge-list, ETX from heartbeat loss |
| **BATMAN** (batman-adv) | OGM flooding with TQ (Transmit Quality) decay per hop | Best next-hop selection in multi-hop forwarding meshes | Solves hop-by-hop routing — not applicable (all routers have direct WAN links, no forwarding) |
| **802.11s** (IEEE 802.11s) | Peer-link keepalive + airtime link metric + HWMP path selection | Layer-2 mesh forwarding | Lower-layer concern; presence model similar to OLSR HELLO |
| **SWIM** (Scalable Weakly-consistent Infection-style Membership) | Random probe + indirect probe via K peers | Membership in large-scale (1000+) clusters | Overkill below ~50 nodes; probabilistic detection harder to reason about for real-time SLA |

### OLSR mapping to the DDS router design

| OLSR concept | DDS equivalent | Implementation |
|--------------|---------------|----------------|
| **HELLO message** (periodic, 1-hop broadcast) | `RouterHealth` topic at 1 Hz | Already designed (D77) |
| **Neighbor list in HELLO** | `peers_seen` edge-list field in `RouterHealth` | Already designed (D77) |
| **HELLO sequence number** | `heartbeat_seq` field in `RouterHealth` | Already designed |
| **ETX from HELLO loss counting** | Delivery ratio computed from `heartbeat_seq` gaps | **Enhancement below** |
| **TC (Topology Control) flooding** for multi-hop | C2/dashboard unions `peers_seen` from all routers | Already designed — no flooding needed (all routers are 1-hop from each other on WAN) |
| **Link hysteresis** (OLSR §14) | `ALIVE → STALE → DEAD` state machine with threshold | Already designed |
| **Jitter** (inter-arrival variance) | Stddev of `RouterHealth` inter-arrival per peer | Free from existing heartbeat — no new mechanism |

### Why BATMAN's OGM flooding doesn't apply

BATMAN floods "Originator Messages" so that each node can select the **best next-hop** for
multi-hop forwarding. The TQ (Transmit Quality) decays at each relay, and the receiving
node picks the neighbor that delivered the highest TQ as its gateway to the originator.

This solves **hop-by-hop routing** in a mesh where not all nodes can reach each other
directly. In the ACT router mesh, **every router has a direct WAN link to every other
router** — there is no multi-hop forwarding decision to make. The epoch flood is the DDS
analog of BATMAN OGMs, and it inherits the same irrelevance: it solves a routing problem
that doesn't exist when all nodes are 1-hop peers.

The one scenario where path diversity matters (redundant multi-path verification) is
better served by an on-demand diagnostic than a continuous flood.

### Enhancement evaluated: ETX-style delivery ratio from `heartbeat_seq`

OLSR's ETX (Expected Transmission Count) metric is computed passively from HELLO loss:

$$\text{ETX}(A \to B) = \frac{1}{d_f \times d_r}$$

Where $d_f$ = forward delivery ratio (fraction of A's HELLOs received by B), and
$d_r$ = reverse delivery ratio (fraction of B's HELLOs received by A).

**Mapped to `RouterHealth`:**

Each router already publishes a monotonically-incrementing `heartbeat_seq`. The receiving
router could compute the forward delivery ratio with zero additional traffic:

```
// On receiving RouterHealth from peer P:
uint32_t expected = P.heartbeat_seq - last_recorded_seq_from[P];
uint32_t received = samples_received_this_window_from[P];
float forward_delivery_ratio = (float)received / (float)expected;
```

**Verdict: subsumed by `LinkStatsCollector` — not implemented.**

The `LinkStatsCollector` (Phase 9, D14/D81) already captures strictly richer per-peer,
per-interval link-quality signals from RTPS protocol counters:

| ETX would give | `LinkStatsCollector` already gives | Source |
|----------------|-----------------------------------|--------|
| Loss rate (`1 - delivery_ratio`) | NACK rate (writer + reader side), `lost_by_writer` count | `WriterLinkDeltas`, `ReaderLinkDeltas` |
| Jitter (inter-arrival variance) | Implicit from heartbeat timing; also `uncommitted_samples` gauge for transient loss | `ReaderLinkDeltas` |
| Asymmetry (`ratio(A→B) ≠ ratio(B→A)`) | Per-peer directional NACK/loss/repair counts | Both sides via matched-endpoint status |
| Congestion signal | Send-window size, backpressure counters | `WriterLinkDeltas` |
| Per-link RTT | App-ack on `RouterLinkProbe` | Probe writer/reader |

Additionally, the `PresenceMonitor`'s `RouterHealth` writer/reader pair is registered as an
`IWanStatsSource` (the "idle-mesh bellwether"), so protocol-stats capture already covers
the heartbeat channel even when all data routes are idle — the one scenario where
heartbeat-only ETX would have an advantage.

`RouterPeerRef` stays identity + presence only (no `delivery_ratio` field). Link quality
lives on the LAN in `ActRouterLinkStats`, not on the WAN in `peers_seen`.

### Comparison: what each approach provides vs. traffic cost (8-node mesh, 1 Hz)

| Metric needed | Epoch Flood | Pairwise HB + Protocol Stats | All-to-All Probe |
|---------------|:-----------:|:----------------------------:|:----------------:|
| Presence | ✓ (timeout) | **✓** (DEADLINE) | ✓ |
| Per-link loss rate | ✗ | **✓** (NACK rate + `lost_by_writer`) | ✓ |
| Per-link RTT | ~ (clock-sync) | **✓** (app-ack probe) | ✓ |
| Per-link jitter | ~ (clock-sync) | **✓** (inter-arrival + uncommitted gauge) | ✓ |
| Asymmetric failure | ✓ (path diff) | **✓** (directional counters) | ✓ |
| Congestion / backpressure | ✗ | **✓** (send-window, unacked count) | ✗ |
| Multi-path diversity | **✓** | ✗ | ✗ |
| **Samples/sec (WAN)** | **224** | **8** (heartbeat) + **8** (probe) = **16** | **56** |

The pairwise heartbeat + protocol-stats harvest provides liveliness, loss, RTT, jitter,
congestion, and asymmetry detection at 16 samples/sec — 14× less WAN traffic than epoch
flood, with strictly better per-link fault isolation, O(1) detection latency, and richer
signal decomposition.
