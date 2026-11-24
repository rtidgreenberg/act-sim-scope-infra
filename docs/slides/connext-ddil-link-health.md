---
marp: true
theme: rti
paginate: true
size: 16:9
title: Measuring Connext Link Health
description: Peer presence, passive heartbeat RTT, reliable-delivery counters, and interpretation limits
footer: "RTI Connext | Link health | 2026-11-07"
---

<!-- _class: lead -->

# Measure first.<br>Diagnose with evidence.

## Link health on a disrupted or constrained network

Peer presence + round-trip time + reliable-delivery counters

**Use existing traffic. Keep measurement separate from diagnosis.**

---

# Three questions need three measurements

| Question | Measurement | What it tells us |
|---|---|---|
| Can I reach this peer? | `RouterHealth` presence | Current reachability state |
| How long is the round trip? | Passive heartbeat RTT | Delay, after subtracting the peer's reporting wait |
| Is reliable delivery under pressure? | Per-peer DDS protocol counters | Repairs, backlog, and receiver pressure |

RTT = round-trip time. DDS = Data Distribution Service.

> **Takeaway:** reachability is not delivery quality, and one counter is not a root cause.

---

<!-- _class: compact -->

# Two levels of presence awareness

| Level | Mechanism | Plain-language role |
|---|---|---|
| **Application level** | `RouterHealth` topic + deadline / grace policy | Tells the application and dashboard whether we are still hearing from a peer (`ALIVE`, `STALE`, or `DEAD`), along with its health metadata |
| **DDS middleware level** | `SPDP2` participant assertions + discovery lease | Keeps the peer in DDS's internal discovery database; if assertions stop and the lease expires, DDS prunes the participant and its endpoints |

- **Why keep them separate?** `SPDP2` manages middleware discovery and cleanup; `RouterHealth` drives application-visible reachability and status.

> **Takeaway:** `SPDP2` prunes the DDS discovery database; `RouterHealth` informs the application.

---

<!-- _class: compact -->

# What happens when a platform restarts?

| Level | What stays the same | What changes | How cleanup works |
|---|---|---|---|
| **Application** (`RouterHealth`) | Same node instance key (e.g., `Platform_30`) | New `session_id` and new writer GUID | Receiver detects the new `session_id`, knows the peer restarted, and resets RTT / counter baselines |
| **DDS middleware** (`SPDP2`) | Same network address / port (locator) | New participant GUID | **Clean exit:** removed immediately. **Crash:** old GUID stays until its `SPDP2` discovery lease expires (`WAN_TIMEOUT_SEC` = 300 s) |

- DDS does **not** overwrite an old participant just because the new one has the same IP/port.
- Until the discovery lease expires, both the old (dead) participant and the new one exist in the database.

> **Takeaway:** the app detects restarts immediately via `session_id`; DDS prunes crashed participants when the `SPDP2` lease expires.

---

# Measure each peer, not just the router total

| Local endpoint | Remote peer | Useful evidence |
|---|---|---|
| Writer | Matched reader | Requested repairs and unacknowledged data |
| Reader | Matched writer | Missing sequences, duplicates, and rejections |

- Poll cumulative counters and calculate interval differences locally.
- Keep separate baselines for each endpoint/peer association.
- Restart, rematch, or counter reset starts a new baseline.

> **Takeaway:** do not report replay or a new endpoint association as sudden link degradation.

---

<!-- _class: compact -->

# Separate network symptoms from local pressure

| Signal | Useful interpretation | Does NOT by itself prove |
|---|---|---|
| Repair requests (`NACK`s) and resends | Data needed recovery | Radio loss alone (late joiners and replay also cause resends) |
| Unacknowledged backlog | Sender is waiting for confirmation | Network congestion alone |
| Samples waiting behind a gap | In-order delivery is waiting for an earlier missing packet | Why that packet went missing |
| Rejected samples / full buffers | Receiver or sender memory pressure | A failed radio link |

`NACK` = a reader's request to resend missing data.

> **Takeaway:** compare counters with packet captures, host CPU/memory load, and controlled tests.

---

# RTT reuses existing heartbeats

1. A sends a scheduled `RouterHealth` heartbeat.
2. B records receipt and acknowledges it in B's next scheduled heartbeat.
3. A subtracts B's reported waiting time from A's elapsed round trip.

```text
RTT = A's elapsed time - B's receipt-to-report wait
Example: 725 ms - 680 ms = 45 ms
```

- Reuses existing heartbeat traffic.
- No subtraction of wall-clock timestamps across machines.
- Dispatch, queueing, and the return path still contribute.

> **Takeaway:** this is a system round trip, not pure propagation delay.

---

# Missing or stale evidence is not zero latency

| Displayed fact | Meaning |
|---|---|
| Presence ALIVE / STALE / DEAD | Reachability according to the presence policy |
| Valid, recent RTT | A usable round-trip measurement exists |
| No interval samples | No measurement in that interval, not 0 ms |
| Old RTT | Historical evidence; do not display it as current |

Accept only direct heartbeat evidence from the correct writer/session.
Reject duplicate, superseded, or invalid acknowledgments.

> **Takeaway:** show freshness alongside the value.

---

<!-- _class: compact -->

# Measurement choices have different costs

| Approach | Benefit | Trade-off |
|---|---|---|
| Passive heartbeat RTT (current) | No extra test-probe traffic on the link | Sample rate depends on heartbeat schedule, loss, and history size |
| DDS protocol counters (current) | Detailed repair and queue evidence without a new WAN protocol | Local polling only; reliable-channel counters do not cover best-effort topics |
| Dedicated probe (alternative) | Independent, controllable test cadence | Adds extra endpoints and packets (not our current RTT design) |

> **Takeaway:** reuse existing traffic first; add dedicated probes only for a measured need.

---

# Capture now. Classify only after calibration.

```text
Per-peer measurements
        -> controlled loss / delay / rate-limit tests
        -> validated thresholds
        -> degradation diagnosis
```

- Separate loss, delay, rate limits, blackout, and host-resource pressure.
- Measure signal delay, noise, and how long a threshold must persist.
- Keep presence-based reachability separate from any future degradation classifier.

> **Takeaway:** protocol counters support diagnosis; they are not a calibrated health score by themselves.

---

# Link-health decision

1. Keep presence, RTT, and delivery pressure as separate facts.
2. Use current heartbeat RTT and per-peer counter baselines.
3. Show freshness and lifecycle changes explicitly.
4. Validate any degradation rule against controlled impairment.

**Do not add probe traffic or imply a diagnosis without evidence.**

---

<!-- _class: sources -->

# Evidence and further detail

- [Current link-metrics design](../cpp_router/link-health.md): passive RTT and protocol counters.
- [Passive RTT implementation and validation](../cpp_router/router-health-passive-rtt-plan.md): timing, session identity, freshness, and acceptance gates.
- [Presence and health](../cpp_router/presence-and-health.md): reachability policy and peer roster.
- [Design decisions](../cpp_router/design-decisions.md): capture first; inference requires calibration.

The older dedicated application-ack probe investigation is historical.
This deck describes the current heartbeat-based RTT design.
