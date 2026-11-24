---
marp: true
theme: rti
paginate: true
size: 16:9
title: "Startup optimization"
description: "Proven application-reader suppression and one-second discovery spreading"
footer: "RTI Connext | Startup optimization | 2026-11-20"
---

<!-- _class: lead -->

# Startup optimization

## Quiet unnecessary requests. Spread the work that remains.

**Application-reader suppression + one-second startup spreading**

Implemented and isolated-tested in source. **Not deployed.**

<!--
Companion: ../startup-optimization.md.
Connext / Routing Service 7.7.0, x64Linux4gcc8.5.0.
The mesh remains stopped after the requested debug cleanup.
No persistent binary rebuild/replacement or live restart was performed.
-->

---

<!-- _class: compact -->

# Application endpoints are not endpoint discovery

| Traffic | ACKNACK behavior | Our finding |
|---|---|---|
| Application reliable readers | Startup requests and normal repair | Startup suppressed; repair retained |
| SEDP endpoint discovery | Reliable builtin readers | Startup requests remain |
| Classic SPDP | Best-effort participant announcements | No ACKNACK exchange |
| SPDP2 bootstrap | Best-effort discovery | No ACKNACK exchange |
| SPDP2 configuration | Reliable builtin reader | Startup requests remain |

> ACKNACKs come from reliable readers, including discovery readers.

<!--
Installed 7.7 Simple_Participant_Discovery.htm,
Simple_Participant_Discovery_2.htm and Simple_Endpoint_Discovery.htm,
plus actual scoped packet captures. Startup suppression is not the same
as disabling all reliability acknowledgments.
-->

---

# What we proved

| Reader setting | Participant setting | Application startup requests | SEDP startup requests |
|---|---|---:|---:|
| Off | Off | 9 | 4 |
| On | Off | 0 | 4 |
| Off | On | 9–11 | 4 |
| On | On | 0 | 4 |

- Two repetitions; **64/64 commands arrived**, including deliberate loss repair.
- Classic SPDP did not make the participant setting suppress SEDP either.

> Keep the working reader setting. Do not claim discovery suppression.

<!--
Reader property: dds.data_reader.protocol.disable_preemptive_nack=true.
Participant control:
dds.participant.inter_participant_data_reader.protocol.disable_preemptive_nack.
Four-way matrix has eight routed trials. SEDP counts combine publication
and subscription categories, two startup requests each.
Connext AI's conflicting scope recommendations were not accepted as proof.
The compact participant snippet already inherits participant flags;
no new participant-suppression property is added.
-->

---

# Current WAN QoS: a 0–1 second response window

**Retain application-reader startup suppression.**

Add a zero minimum and one-second maximum heartbeat-response delay to:

- SEDP **publication reader**
- SEDP **subscription reader**
- SPDP2 **participant-configuration reader**

> Spread discovery responses—not a fixed one-second pause.

<!--
Authored in harness_v2/qos/act_qos_profiles.xml, common WAN participant base.
min_heartbeat_response_delay = 0 seconds; max = 1 second.
Applies through legacy/two-topic/three-topic and EMANE profile inheritance.
Application response timing, heartbeat/retry budgets, system timeout,
WAN pacing and delivery contracts are unchanged.
This is not suppression of initial requests or every discovery packet.
-->

---

<!-- _class: compact -->

# Gateway launches span one full second

| Gateway | Assigned delay |
|---|---:|
| Control_20 | 0.000 s |
| Platform_30 | 0.167 s |
| Platform_31 | 0.333 s |
| Platform_32 | 0.500 s |
| Platform_33 | 0.667 s |
| Platform_34 | 0.833 s |
| Platform_35 | 1.000 s |

**After RF readiness, before gateway startup.** First gateway starts without a delay.

<!--
Owned container lifecycle generates WAN_STARTUP_DELAY_SEC.
Slots are topology order / number of platforms, not hardcoded node IDs.
The node entrypoint validates 0–1 second input; both gateway backends use it.
Native startup, RF-ready polling and container scheduling add variation.
This is not fleet clock synchronization or an exact packet scheduler.
Standalone entrypoints default to zero; host-process lifecycle unchanged.
-->

---

<!-- _class: compact -->

# Measured: work spreads through the second

| Seven native WAN participants | Immediate control | Optimized |
|---|---:|---:|
| First-to-last creation | 0.128 s | **0.995 s** |
| Smallest heartbeat-to-ACKNACK gap | 0.005 ms | 5.589 ms |
| Largest heartbeat-to-ACKNACK gap | 0.632 ms | **996.331 ms** |
| Occupied 100 ms gap bins | 1 of 10 | **10 of 10** |
| Mutual discovery | All seven | All seven |
| Capture drops | 0 | 0 |

> Observed spreading—not a guarantee that every packet is evenly spaced.

<!--
Evidence: debug/test_reports/startup_optimization_20261120/report.json.
Two Python-only private network/IPC trials; no application endpoints.
Control overrides only the three reader response intervals to zero and
starts seven participants without slots. Optimized uses actual current
WAN profile plus launch targets across a second.
Gaps use the latest preceding matching heartbeat, not guaranteed causal
attribution for each ACKNACK; closing traffic is excluded.
No uniform distribution or radio-throughput saving is claimed.
-->

---

# Correctness checks still pass

- **10 targeted checks:** native QoS, slot generation and actual entrypoint timing.
- **5 decoder checks:** standard and vendor-specific discovery traffic.
- Actual Routing Service: **9 → 0** application startup requests.
- **8/8 commands in each variant**, including the lost-and-repaired command.
- Expected invalid configurations still rejected.

> Discovery jitter adds latency. It does not weaken application reliability.

<!--
The processor uses unchanged persistent binaries in isolated containers.
Jitter exposed an asynchronous observer-fixture race; qualification now
waits for all three writers AND readers before recording the first gateway.
No assertions were removed to hide missing endpoints.
This is a focused gate, not a new full-suite or live EMANE mission gate.
-->

---

# Key takeaways

1. **Suppress proven application startup requests.**
2. **Keep discovery reliability**; its suppression remains unresolved.
3. **Launch gateways over one second**, not all at once.
4. **Spread discovery replies over 0–1 second**, accepting added latency.
5. **Do not promise packet-perfect pacing or bandwidth savings.**
6. **Source is ready; deployment is separate.** The mesh remains stopped.

<!--
Document: ../startup-optimization.md.
Reproduction:
bash spikes/preemptive_acknack/run_startup.sh --optimized
bash spikes/preemptive_acknack/run.sh
Deploy only through owned coordinated lifecycle with regenerated native XML.
Existing generated deployments do not pick up changed QoS source automatically.
-->
