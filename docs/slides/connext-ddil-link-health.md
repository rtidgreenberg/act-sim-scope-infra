---
marp: true
theme: default
paginate: true
size: 16:9
title: Connext Protocol Statistics for DDIL Link Health
description: Per-peer protocol evidence, RTT probes, and calibrated degradation inference
style: |
  section {
    background: #f5f7f4;
    color: #1b2b34;
    font-family: Avenir Next, Avenir, Trebuchet MS, sans-serif;
    font-size: 28px;
    letter-spacing: 0;
    padding: 56px 76px;
  }
  h1, h2, h3 { color: #0d5c63; font-weight: 700; letter-spacing: 0; }
  h1 { font-size: 58px; }
  h2 { font-size: 42px; margin-bottom: 0.4em; }
  h3 { font-size: 26px; }
  strong { color: #b94931; }
  code { color: #8d3f2c; background: #e7ece9; }
  blockquote {
    border-left: 8px solid #e4a33b;
    color: #31454d;
    font-size: 29px;
    margin: 0.75em 0;
    padding: 0.25em 0.8em;
  }
  section.lead { background: #103f4a; color: #f7f5ec; }
  section.lead h1, section.lead h2, section.lead strong { color: #f7f5ec; }
  section.closing { background: #e4a33b; color: #172a32; }
  section.closing h2 { color: #172a32; }
  .kicker { color: #e4a33b; font-size: 20px; font-weight: 700; text-transform: uppercase; }
  .columns { display: grid; grid-template-columns: 1fr 1fr; gap: 30px; }
  .three-columns { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }
  .panel {
    background: #ffffff;
    border-top: 7px solid #0d5c63;
    box-sizing: border-box;
    min-height: 185px;
    padding: 18px 22px;
  }
  .panel.gold { border-top-color: #e4a33b; }
  .panel.warn { border-top-color: #b94931; }
  .panel h3 { margin: 0 0 0.3em; }
  .panel p, .panel li { font-size: 22px; }
  .flow { align-items: center; display: flex; gap: 12px; justify-content: space-between; margin-top: 30px; }
  .node {
    background: #ffffff;
    border: 3px solid #0d5c63;
    box-sizing: border-box;
    min-width: 155px;
    padding: 15px;
    text-align: center;
  }
  .wan { background: #d9e8e3; border-color: #b94931; }
  .arrow { color: #b94931; font-size: 38px; }
  .small { font-size: 18px; }
  .metric { color: #0d5c63; font-weight: 700; }
  footer { color: #547177; }
---

<!-- _class: lead -->

<div class="kicker">Connext DDIL networking</div>

# Link Health From Protocol Statistics

## Per-peer measurement, calibrated thresholds, and degradation inference

---

## The DDIL link-health problem

A mesh link can be reachable while still failing to carry operational traffic reliably or within latency bounds.

<div class="three-columns">
<div class="panel"><h3>Reachability</h3><p>Peer presence determines whether a router or path is reachable.</p></div>
<div class="panel gold"><h3>Degradation</h3><p>Loss, retransmission, delay, and queue pressure often appear before a peer becomes unreachable.</p></div>
<div class="panel warn"><h3>Decision</h3><p>The router needs per-peer evidence to distinguish a degraded link from an unavailable one.</p></div>
</div>

<blockquote>
Presence answers: “is the peer reachable?” Protocol statistics answer: “how is reliable delivery behaving?”
</blockquote>

<!-- Sources: docs/cpp_router/presence-and-health.md; docs/cpp_router/link-health.md -->

---

## Measure at the Connext router

<div class="flow">
<div class="node">WAN writer<br><span class="small">outbound samples</span></div>
<div class="arrow">→</div>
<div class="node wan">DDIL link<br><span class="small">loss · delay · rate limit</span></div>
<div class="arrow">→</div>
<div class="node">WAN reader<br><span class="small">inbound samples</span></div>
<div class="arrow">→</div>
<div class="node">LinkStatsCollector<br><span class="small">per-peer rollup</span></div>
</div>

<div class="columns" style="margin-top: 38px;">
<div class="panel"><h3>In-process telemetry</h3><p>Connext exposes reliable-protocol status directly on writers and readers. No additional WAN monitoring protocol is required for these counters.</p></div>
<div class="panel gold"><h3>Stable collection</h3><p>Poll cumulative totals at a fixed interval, calculate deltas locally, and key every record by peer and network.</p></div>
</div>

<!-- Sources: docs/cpp_router/link-health.md; Connext AI response cross-checked against repository header verification -->

---

## Writer-side: repair demand and send pressure

<div class="columns">
<div>

### `DataWriterProtocolStatus`

- received NACK count and bytes
- pulled samples and fragments for repair
- pushed samples and bytes for offered load
- oldest unacknowledged sequence and send-window size

</div>
<div>

### Writer-cache and reader activity

- unacknowledged sample count and peak
- high-watermark and full-cache events
- current send-window capacity
- inactive matched readers

</div>
</div>

<blockquote>
Increasing NACKs and repair traffic identify demand for recovery. Persistent unacknowledged backlog and full-window events identify pressure on the sending side.
</blockquote>

<!-- Sources: docs/cpp_router/link-health.md; Connext 7.7 Modern C++ status APIs -->

---

## Reader-side: holes, rejection, and end-to-end loss

<div class="three-columns">
<div class="panel"><h3>Gap recovery</h3><p><span class="metric">NACKs sent</span> identify missing sequence numbers detected at the receiver.</p></div>
<div class="panel gold"><h3>In-order delivery</h3><p><span class="metric">Uncommitted samples</span> show data held behind a missing earlier sample.</p></div>
<div class="panel warn"><h3>Resource limits</h3><p><span class="metric">Out-of-range/rejected samples</span> indicate local receiver pressure, not necessarily RF loss.</p></div>
</div>

<div class="panel" style="margin-top: 25px;"><h3>Failure signal</h3><p><code>SampleLostStatus::lost_by_writer</code> means the writer removed a sample before the reader recovered it. Keep this separate from local resource-limit losses and rejections.</p></div>

<!-- Sources: docs/cpp_router/link-health.md; Connext 7.7 Modern C++ status APIs -->

---

## Attribute every signal to one peer

<div class="columns">
<div class="panel"><h3>Outbound view</h3><p>Use matched-subscription protocol status for each remote reader rather than one aggregate writer total.</p></div>
<div class="panel gold"><h3>Inbound view</h3><p>Use matched-publication protocol status for each remote writer rather than one aggregate reader total.</p></div>
</div>

<div class="panel warn" style="margin-top: 25px;"><h3>Endpoint lifecycle matters</h3><p>A rematch, restart, or rediscovery creates a new endpoint association and can reset counters or cause durable replay. Mark that interval and reset its baseline; do not classify it as link recovery or degradation.</p></div>

<!-- Sources: docs/cpp_router/link-health.md; Connext AI response, lifecycle caveats -->

---

## RTT requires an application-ack probe

<div class="flow">
<div class="node">Write probe<br><span class="small">record send time</span></div>
<div class="arrow">→</div>
<div class="node wan">Remote reader<br><span class="small">take probe</span></div>
<div class="arrow">→</div>
<div class="node">Application ack<br><span class="small">subscription handle</span></div>
<div class="arrow">→</div>
<div class="node">RTT record<br><span class="small">min · mean · max</span></div>
</div>

<div class="columns" style="margin-top: 38px;">
<div class="panel"><h3>Dedicated probe topic</h3><p>A small, reliable, volatile probe topic isolates measurement QoS and retention from the presence authority and operational data routes.</p></div>
<div class="panel gold"><h3>Interpretation</h3><p>Application-ack RTT includes scheduling, queueing, reader processing, acknowledgment timing, and the return path. It is a system round trip, not pure RF latency.</p></div>
</div>

<!-- Sources: docs/cpp_router/link-health.md; spikes/link_probe/README.md; Connext AI response, application-ack caveats -->

---

## Normalize and combine the measurements

<div class="three-columns">
<div class="panel"><h3>Loss and repair</h3><p>NACK rate and repair ratio: NACKs/pushed samples, pulled/pushed samples, fragment repair for large data.</p></div>
<div class="panel gold"><h3>Congestion and queueing</h3><p>Unacknowledged backlog, watermark/full-window events, oldest-unacknowledged lag, and RTT.</p></div>
<div class="panel"><h3>Receiver stress</h3><p>Uncommitted samples, rejection reasons, out-of-range arrivals, and lost-by-writer events.</p></div>
</div>

<blockquote>
Raw counts cannot compare a 1 Hz control route with a high-rate payload route. Report interval rates and ratios against offered load.
</blockquote>

<!-- Sources: docs/cpp_router/link-health.md; Connext AI response, normalization guidance -->

---

## From protocol evidence to link state

<div class="flow">
<div class="node">Fixed-interval<br><span class="small">per-peer deltas</span></div>
<div class="arrow">→</div>
<div class="node">Threshold rules<br><span class="small">rate · ratio · duration</span></div>
<div class="arrow">→</div>
<div class="node wan">Link state<br><span class="small">healthy · degraded · unavailable</span></div>
<div class="arrow">→</div>
<div class="node">Mesh status<br><span class="small">operator visibility</span></div>
</div>

<div class="columns" style="margin-top: 42px;">
<div class="panel"><h3>Degraded</h3><p>Protocol pressure persists above calibrated thresholds while the peer remains reachable: repair rate, RTT, backlog, or receive-window stress.</p></div>
<div class="panel warn"><h3>Unavailable</h3><p>Presence/liveliness declares the peer or path dead. This is distinct from a degraded but still communicating link.</p></div>
</div>

<!-- Sources: docs/cpp_router/link-health.md; docs/cpp_router/presence-and-health.md -->

---

## Do not mistake symptoms for root cause

<div class="columns">
<div>

### Likely network symptoms

- NACK/repair growth in both directions
- increasing RTT under offered load
- inactive readers or retransmission bursts
- fragment repair on constrained MTUs

</div>
<div>

### Possible local causes

- reader rejection or receive-window exhaustion
- writer cache full or history overwrite
- endpoint restart, rematch, or durable replay
- slow application draining or host resource pressure

</div>
</div>

<blockquote>
Protocol statistics provide evidence of reliability pressure. Packet capture, host telemetry, and controlled tests determine the cause.
</blockquote>

<!-- Sources: docs/cpp_router/link-health.md; Connext AI response, interpretation limits -->

---

## Calibrate thresholds with ground truth

1. Run two Connext routers across an emulated DDIL link.
2. Sweep loss, delay, jitter, rate limits, and blackout independently.
3. Record per-peer protocol statistics, application-ack RTT, and the imposed network schedule.
4. Determine signal lag, noise floor, and threshold duration for each impairment.
5. Promote only validated rules into the degraded-link classifier.

<div class="panel gold" style="margin-top: 30px;"><h3>Expected correlation</h3><p>Loss drives NACKs and repair; rate limits drive backlog and window pressure; delay drives RTT and oldest-unacknowledged lag; blackout progresses from inactive readers to presence <code>STALE</code>/<code>DEAD</code>.</p></div>

<!-- Sources: docs/cpp_router/link-health.md, Deferred: inference and the correlation experiment -->

---

<!-- _class: closing -->

## Link-health design summary

1. Use Connext matched-endpoint protocol status for per-peer evidence.
2. Add a dedicated application-ack probe for round-trip time.
3. Normalize counters by offered load and separate local resource symptoms.
4. Correlate measurements with controlled DDIL impairment.
5. Infer degraded links only from calibrated, sustained threshold breaches.

The router becomes the measurement point for a DDIL mesh: it relays presence, measures reliable-delivery behavior, and reports a link state that operators can act on.

---

## References

- `docs/cpp_router/link-health.md`
- `docs/cpp_router/presence-and-health.md`
- `docs/cpp_router/design-decisions.md` (D14)
- `spikes/link_probe/README.md`
- Connext 7.7 Modern C++ protocol-status APIs

Connext AI was used for additional API context. API claims in this deck were cross-checked against the repository's header-verified link-health design and `docs/connext-ai-issues/`.
