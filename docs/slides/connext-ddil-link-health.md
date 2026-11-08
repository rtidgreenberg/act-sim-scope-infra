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

| Signal | Useful interpretation | Not proof of |
|---|---|---|
| Repair requests and retransmissions | Data needed recovery | RF loss alone; late joins and replay also matter |
| Unacknowledged backlog | Sender is waiting for progress | Network congestion alone |
| Samples waiting behind a sequence gap | In-order delivery is blocked | The source of the missing sample |
| Rejected samples / full resources | Receiver or sender pressure | A failed radio link |

NACK = request to repair missing data.

> **Takeaway:** correlate counters with packet capture, host load, and controlled tests.

---

# RTT reuses existing heartbeats

1. A sends a scheduled `RouterHealth` heartbeat.
2. B records receipt and acknowledges it in B's next scheduled heartbeat.
3. A subtracts B's reported waiting time from A's elapsed round trip.

```text
RTT = A's elapsed time - B's receipt-to-report wait
Example: 725 ms - 680 ms = 45 ms
```

- No dedicated `RouterLinkProbe` stream in the current design.
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
| Passive heartbeat RTT: current | No extra probe publication schedule | Cadence, loss, and bounded history limit sample availability |
| DDS protocol counters: current | Detailed repair and queue evidence without a new WAN measurement protocol | Local polling/state; reliable-channel evidence is not every topic's delivery audit |
| Dedicated probe: alternative | Independent test cadence | Additional endpoints and packets; not the current RTT design |

> **Takeaway:** reuse existing traffic first; add probes only for a measured requirement.

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
