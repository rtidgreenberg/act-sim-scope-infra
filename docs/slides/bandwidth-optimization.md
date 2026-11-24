---
marp: true
theme: rti
paginate: true
size: portrait
title: "Bandwidth Optimization"
description: "Repository-tested WAN strategies and options, with data filtering, discovery, typed unions and compression"
footer: "RTI Connext | Bandwidth Optimization | Evidence: 2026-11-24"
---

<!-- _class: lead -->

# Bandwidth Optimization

## Send less. Send selectively. Preserve required data.

**Data filtering, discovery, delivery policy and typed-union compression**

Current implementation, isolated tests, rejected approaches and next options.

<!--
Evidence snapshot: 2026-11-24, Connext 7.7.0. Repository-wide synthesis,
not a new full-suite run, deployment or fleet-bandwidth benchmark.
The requested mesh was stopped before this deck was created.
The current tested native processor uses six physical priority/family topics,
retaining the three logical Event / State / Periodic delivery classes.
The 2026-11-17 deployed reference used three physical pairs; the new compression
comparison is private source qualification, not deployment.
Older documents' Event/Status and Event/State/Telemetry names describe earlier
architectures, not the current native processor's physical WAN names.
Speaker notes preserve evidence, measurement conditions and qualification gaps.
The PowerPoint export contains rendered slides and these notes; edit Markdown.
-->

---

<!-- _class: compact -->

# Five levers, with different benefits

| Lever | What it can reduce | What it does not prove |
|---|---|---|
| Generate / admit less | Offered samples and stale updates | Safe loss of commands or deletions |
| Filter before transmission | Unwanted topics, audiences and receiver copies | Savings from receiver drops alone |
| Consolidate and isolate discovery | WAN participants, endpoints and leaked announcements | Lower application reporting rates |
| Encode / compress efficiently | Type metadata or serialized sample bytes | A universal compression ratio |
| Pace and prioritize | Bursts, queue pressure and latency interference | More radio capacity |

> Count actual transmitted bytes. Do not add savings from different experiments.

<!--
This is an accounting framework, not a measured ranking or multiplicative
capacity model. Workload, receiver fanout, retransmissions and packetization
interact. A writer API call, a physical DATA push, a UDP datagram and an Ethernet
frame are different quantities.
Sources: ../../router/routing_service/README.md:245-251,299-326,460-464;
../../spikes/wan_dp_route_tradeoffs/README.md:112-115;
../../spikes/full_union_compression/README.md;
../../spikes/mac_rtps_accounting/README.md:50-70.
-->

---

<!-- _class: compact -->

# What is current, and what is not?

| Evidence level | Included strategies |
|---|---|
| Implemented; prior native/live qualification | Three WAN families; audience/mission receive interests; shared pacing; LAN isolation |
| Qualified source; not deployed | Startup suppression/spreading; full six-topic union + LZ4/zlib with async/shared controller |
| Isolated proof; not ACT integration | Native SEDP/LBED filtering characterization |
| Proposal / next qualification | Adaptive control topic; fleet-wide allocation; representative radio/compression comparison |

**Current tested setup:** six WAN pairs per gateway; three logical delivery classes.

> Functional qualification is not a measured percentage of fleet bandwidth saved.

<!--
Current architecture/deployment: ../two-topic-wan-migration-plan.md:2786-2795
(checkpoint 3.54); diagnostics follow-up at 2857-2871 is source only.
Current mission interests: ../../router/routing_service/README.md:15-18.
Current shared pacing: ../../harness_v2/qos/act_qos_profiles.xml:935-972,1025-1067.
LAN isolation: ../../harness_v2/qos/act_qos_profiles.xml:114-136.
Startup source qualification: ../startup-optimization.md, Fresh qualification.
Current full-processor comparison: ../../spikes/full_union_compression/README.md.
Its monitoring repairs and compression settings are not deployed.
Adaptive proposal: adaptive-runtime-tuning.md.
No mesh was launched or changed for this documentation task.
-->

---

# Data filtering: save bytes before the radio

```text
LAN samples -> gateway WAN writer -> selected transmissions -> WAN
                        ^
           Interests advertised by WAN readers
           Topic + source + destination / audience
```

**Content-filtered topics (CFTs)** select whole samples for each reader.

- A reader-configured filter can execute at the **remote writer**.
- A filter that only drops after reception does **not** save WAN bytes.
- Prove the difference with writer counters or a WAN capture.

> Filter placement and filter execution location are not the same thing.

<!--
With SEDP endpoint discovery, the tested Connext native filters suppress
nonmatching samples at the source writer. Do not imply every predicate,
discovery configuration or time-based filter has this behavior.
Current ACT receiver-configured interests and isolated native filtering
are separate qualification surfaces.
Sources: ../../spikes/routing_service_lbed/README.md:110-150;
../../router/routing_service/README.md:913-947.
CFTs do not strip selected fields out of a sample or provide authorization.
-->

---

<!-- _class: compact -->

# Select by audience and operational need

| Selection | Current intent |
|---|---|
| Addressed / exact recipient | Deliver commands or data to selected node/team identities |
| Control / global / teams | Distinguish C2 visibility, fleet-wide data and team-scoped data |
| Platform Init | Receive Mission / Team from C2 plus internal traffic |
| Platform ActiveMission | Also receive fleet-global PlatformStatus and same-team mission/contact data |

Team and mission changes update local WAN reader interests.
**Outgoing reports remain on**; whole-platform restart returns to Init.

> Interests reduce unnecessary subscriptions; they are not security permissions.

<!--
Sources:
../../router/routing_service/GatewayModel.cxx:64-95,655-680;
../../router/src/core/WanInterests.cxx:18-78;
../../router/routing_service/README.md:15-18,492-501;
../../docs/router-processor-completion-plan.md:76-80;
../../docs/platform-mission-cft-plan.md:48-61,120-131;
../../router/test_e2e/test_routing_service_mesh.py:723-850.
Init/ActiveMission is local receive policy, not producer mode or outgoing gating.
Audience names are exact and bounded; do not imply wildcard authorization.
Membership transitions are not a fleet-wide atomic barrier. Old queued metadata
and in-flight deliveries are separate from newly applied local interests.
The native mission/team tests verify filters and receive/exclusion behavior,
not an aggregate WAN-byte reduction under fleet load.
-->

---

<!-- _class: compact -->

# Rate filtering: where it runs matters

| Approach | Repository finding | Current status |
|---|---|---|
| Reduce producer reporting | Avoid work before serialization and routing | Candidate control path; preserve freshness |
| Space Periodic samples at LAN input | Filter before the gateway publishes on WAN | Opt-in per-topic profile; default off; WAN savings unmeasured |
| Time-filter at shared WAN reader | Fewer accepted samples, not fewer transmissions | Historical test: **644 pushes, 16 accepts** |

Minimum spacing is **per keyed instance**, not a guaranteed periodic snapshot.
Current LAN input time filtering rejects Event and State.

> Do not throttle discrete commands or deletion/revival semantics as telemetry.

<!--
Sources: ../../router/routing_service/README.md:299-326,913-947;
../../router/routing_service/test_config.py:518-568.
lan_status_1s is the supported example: configuration/native QoS checks verify
explicit 1-second separation versus default zero. No measured WAN push/byte
reduction is claimed for that source-side profile.
The separate WAN-reader test offered 161 writes for each of four keys: all
644 were pushed while four samples per key were accepted. The unfiltered
control accepted 64/64 writes for each key.
A shared reader has one separation setting, not independent periods per key.
Different readers could have different periods but would change endpoint count.
Producer control is an option, not an implemented adaptive reporting policy.
Changing Deadline alone does not set the producer's publication frequency.
-->

---

<!-- _class: compact -->

# Filtering evidence: transmissions, not just deliveries

| Test surface | What was actually proven |
|---|---|
| Native Routing Service + SEDP | Offer 3 each phase; select 1 / 2 / 3 / 1; WAN transmits **1 / 2 / 3 / 1** |
| Current ACT mission/team workflows | Correct applied interests and receive exclusions; full-workload WAN savings not quantified |

> The mechanism is proven. Its current ACT workload benefit still needs measurement.

<!--
Sources:
../../spikes/routing_service_lbed/README.md:110-150;
../../router/test_e2e/test_routing_service_mesh.py:723-850.
The SEDP characterization uses actual RS-owned endpoints and native administration;
it is not the completed ACT processor's entire route policy.
Older adapter qualification separately checks zero pushed_sample_count growth
for excluded teams and old-team updates after filter convergence:
../../router/qualification/test_two_topic_qualification.py:168-169,330-390.
Do not silently re-label that older qualification as current native RS coverage.
These counts come from distinct experiments and must not be added as one gate.
-->

---

<!-- _class: compact -->

# Keep three logical delivery contracts

| Delivery class | Current policy | Bandwidth purpose |
|---|---|---|
| Event | Reliable; KEEP_ALL | Preserve required occurrences, including commands |
| State | Reliable; writer KEEP_LAST(1), reader KEEP_ALL | Replace obsolete queued values per instance |
| Periodic | Best effort; KEEP_LAST(1) | Favor current observations over old telemetry |

All three are currently **volatile**: do not promise retained replay to a new reader.
Keys must keep different sources, topics and instances independent.

> Coalescing State is not permission to overwrite required Events.

<!--
Sources: ../../router/routing_service/README.md:245-251;
../../harness_v2/qos/act_qos_profiles.xml:645-652,665-765,817-821,1025-1067.
Current RS aliases compose the delivery classes with the shared RS flow
controller and priority bands. Do not import the isolated union_auto_qos
prototype's transient-local policies into this current baseline.
KEEP_LAST(1) is not a promise that an already-transmitted or acknowledged sample
can be recalled. Reliable delivery remains bounded by configured lifecycle,
resources and reader-inactivity behavior.
Lifecycle/restart caveats and newer actual-processor DDIL evidence:
../instance-state-through-ddil.md; ddil-recovery.md.
Required-event retention and current-state convergence have different oracles.
-->

---

# Pacing and priority protect the shared link

- Event, State and Periodic share **one asynchronous controller per gateway**.
- Current six-topic setup separates priority tiers into **six physical pairs**.
- Tested allowance: **5,000 bytes per 10 ms**, nominally **4 Mbps**.
- Priority alone did not reorder a single FIFO stream in the isolated test.
- Fleet load must include receiver copies, discovery, health and repair traffic.

> A 4 Mbps budget at every gateway is not a 4 Mbps fleet-wide allocation.

<!--
Current source: ../../harness_v2/qos/act_qos_profiles.xml:935-972,1025-1067;
../../router/routing_service/README.md:460-464,782-784.
All six current RS writers use dds.flow_controller.token_bucket.act_rs, each
with two priority bands. The prior three-pair source used a nominal 2 Mbps budget.
Current measured effective policies and 4 Mbps configuration:
../../spikes/full_union_compression/README.md;
../../harness_v2/qos/act_qos_profiles.xml (six-topic profiles).
Token allowance is not a demonstrated usable RF rate, fairness guarantee,
per-class reservation or a cap on all discovery/protocol traffic.
Historical ordering proof:
../../spikes/instance_churn_reconnect/README.md:422-456.
It shows why sample priority needs a qualified queue/channel/controller setup,
not guaranteed priority during reliable-history repair.
Historical RF Pipe checkpoint was 2 Mbps, with virtual transport bitrate=0:
../two-topic-wan-migration-plan.md:1449-1452,1491-1501.
The extra combined RX+TX transport cap was removed, not added as an optimization.
-->

---

<!-- _class: compact -->

# Recovery experiments: avoid attractive shortcuts

| Historical isolated option | Measured lesson |
|---|---|
| Separate State replacement from Event retention | Repair bytes about **7,052 -> 414 KiB**; the comparison still missed 10 s recovery |
| KEEP_LAST on the shared command writer | Only **60/180 command ACKs**; empty queues hid overwritten required data |
| Native batching in the tested outage setup | Final state recovered, but fresh-command RTT reached **15.846 s** |
| Larger repair response: 128 -> 512 KiB | Did not improve the tested recovery; worse RTT in that paired run |

> Historical mechanism evidence, not current processor recovery guarantees.

<!--
State replacement comparison:
../wan-topic-tradeoffs-2026-11-12.md:65-90. KEEP_ALL two/three-topic
median recovery was 61.925/61.975s, versus 19.119s with independent Contacts
KEEP_LAST(1). Adding a topic alone did not create the recovery gain.
Repair-byte figures are source DDS protocol bytes, not RF airtime or fleet totals.
Shared-writer KEEP_LAST:
../two-topic-wan-migration-plan.md:1832-1847; 4,489/14,561 valid
contact writes were seen as well as only 60/180 command ACKs.
Batching: ../../spikes/instance_churn_reconnect/README.md:614-620,640-654.
Larger repair burst: same README:753-780; worst fresh RTT 13.645ms versus
2,315ms, one run per setting. The comparison does not establish causation.
These are rejected tested configurations, not claims that all possible batching
or repair profiles are unsafe. Any future option needs its own full-event oracle.
Current actual-processor recovery evidence remains ../instance-state-through-ddil.md.
-->

---

# Discovery: shrink the WAN surface, not the LAN

- Keep application discovery local; expose only prepared gateway bindings.
- Consolidate WAN participants and endpoints: current tested gateways use **six pairs**.
- Keep LAN discovery off `emane0`; disable unnecessary RTPX tool announcements.
- Older one-WAN-participant test: discovery TX **13.86 -> 7.67 kbps**.
- After historical LAN-isolation fixes: **zero** other-domain RTPS/RTPX in 45 s.

> Reducing endpoint count is useful; measure actual discovery bytes separately.

<!--
Earlier three-pair deployment: ../two-topic-wan-migration-plan.md:2786-2795.
Current six-pair tested source: ../../spikes/full_union_compression/README.md.
Current LAN isolation/RTPX policy:
../../harness_v2/qos/act_qos_profiles.xml:114-136.
Measured participant-consolidation result:
../../spikes/wan_dp_route_tradeoffs/README.md:112-115, an isolated historical
44.7% discovery-TX reduction, not a current ACT fleet forecast.
Its on-demand pair-route change reduced endpoints 44 to 32 without a discovery
reduction: route count alone is not a byte measurement.
Independent isolation capture:
../../spikes/mac_rtps_accounting/README.md:153-170.
That run verified absence of other-domain RTPS and RTPX, not all interval-rate
accuracy gates; later capture-time checks separately passed 264 comparisons.
RTPX tool announcements are not normal WAN SPDP2 participant discovery.
SPDP2 does not remove SEDP endpoint/type traffic or all periodic bootstrap.
Corrected discovery-rig findings:
../../spikes/spdp2_delayed_enable/README.md:4-18.
-->

---

<!-- _class: compact -->

# Discovery options still need link qualification

| Option | Potential benefit | Required comparison |
|---|---|---|
| Configured unicast peers | Current explicit WAN peer setup | Roster/probe cost as the fleet grows |
| Multicast discovery | Avoid separate announcements to every address | Actual radio support, loss and airtime |
| C2 directory / Cloud Discovery Service | Supply peer addresses through a directory | Reachability, restart and compatibility |
| Preloaded matching type XML | Prepare schema availability before operation | Actual WAN type traffic; preloading alone proves no byte saving |

Directories introduce peers; they are **not user-data relays**.

> Preserve dynamic filter updates while changing discovery.

<!--
Options, not a measured ranking or new deployment decision.
Sources: dds-discovery-strategies.md, Choose multicast or unicast discovery and
If unicast, choose how peers get addresses; ../wan-schema-propagation-options.md,
Options explored and Proposed Python setup and deployment workflow.
Current six-topic test uses explicit unicast UDPv6; prior configurations used
UDPv4. WAN_RECEIVE_MULTICAST=0; loopback tests are not radio qualification.
Discovery multicast does not require user-data multicast; neither is assumed
cheaper on a multi-hop radio. A directory introduces its own availability and
address-maintenance requirements and does not automatically relay user traffic.
Knowing one ordinary DDS peer does not automatically reveal all its peers.
The C2 application directory and CDS are distinct designs; compatibility with
the selected native discovery plugins and Routing Service needs qualification.
Prepared XML/type knowledge is not evidence that Connext omits all type metadata.
The earlier discovery deck's reduced-type-propagation suggestion is explicitly
pre-7.7; do not import it as a tested Connext7.7 knob.
The current typed unions include their declared application branch structures;
preloading those schemas is not proof that their WAN type traffic disappears.
-->

---

<!-- _class: compact -->

# Startup: fewer requests, less synchronized traffic

| Qualified source change | Result / boundary |
|---|---|
| Disable application preemptive startup NACKs | Actual processor: **9 -> 0** startup requests; **8/8 commands** per variant, including repair |
| Participant preemptive setting | Did **not** suppress tested SEDP startup requests |
| Spread seven gateway startup targets over 1 s | Native participant creation span **0.128 -> 0.995 s** |
| Discovery heartbeat-response window: 0-1 s | Observed gaps occupied **10/10** 100 ms bins versus 1/10 |

> Source-only qualification. Less burstiness is not a fleet-wide byte-saving claim.

<!--
Source: ../startup-optimization.md, Proven suppression, Current WAN QoS,
Gateway launches and Fresh qualification sections.
Native seven-participant trials had mutual discovery and zero capture drops.
Observed heartbeat-to-ACKNACK gaps do not prove a uniform timer distribution or
causal attribution for every packet. Trials used native Python participants,
not a seven-gateway EMANE throughput workload.
The actual persistent processor application-suppression rig separately retained
ordinary reliable repair after deliberately losing a sequence.
Three discovery reader response windows are configured: publication,
subscription and SPDP2 participant-configuration readers.
Source files include ../../docker/connext-7.7/node-entrypoint.sh,
../../harness_v2/scripts/run_container_baseline.sh and shared QoS XML.
One-second slots act after readiness; they do not synchronize clocks or pace
all packets. No persistent binary replacement or deployment was performed.
-->

---

<!-- _class: compact -->

# Full processor compression: measured before / after

**Six-topic typed unions + full monitoring + async publishing + shared 4 Mbps controller**

Random bytes are inside the command branch; compression applies to the serialized union.

| Command/ACK workload | LZ4 DATA saving | zlib DATA saving |
|---|---:|---:|
| Small commands | ~0% | 33.4% |
| Structured 8 KiB commands | 86.8% | 90.9% |
| Random 8 KiB commands | ~0% | 3.3% |
| Burst: 64 structured 8 KiB commands | 87.8% | 92.0% |

Burst worst round-trip time: **2,599 ms off -> 181 ms LZ4 / 130 ms zlib**.

> Savings include command/ACK DATA frames and headers, not RF airtime.

<!--
Source: ../../spikes/full_union_compression/README.md.
Primary local report:
../../debug/test_reports/full_union_compression_20261124T174940Z/report.json.
Connext 7.7.0; three fresh native gateways; isolated UDPv6 loopback, no EMANE.
Six physical topics: P1_Event, P1_Periodic, P2_Event, P2_State, P2_Periodic,
P3_Periodic. All use the shared act_rs controller and two priority bands.
Two repetitions: none/LZ4/zlib, then zlib/LZ4/none; BEST_SPEED, threshold zero.
Table compares mean captured P2_Event DATA-frame bytes for a fixed 64 commands
and 64 ACKs per workload. Include native channel writers, DATA_SESSION,
DATA_FRAG_SESSION, network headers and retransmitted frames.
Structured command bytes: off1,219,702 / LZ4 160,703 / zlib110,807.
Random command bytes: off1,219,840 / LZ4 1,219,702 / zlib1,180,076.
Random octets themselves do not meaningfully compress; the small zlib benefit
includes typed-union metadata, ACKs and framing. Header/packetization differences
also explain the tiny LZ4 differences rounded to approximately zero.
The burst also carries contacts and periodic traffic; maximum RTT is the worst
observed across both repetitions, not a promised latency bound.
Normalized generated XML differs only in writer compression representation.
Source-only qualification, not deployment or radio qualification.
-->

---

<!-- _class: compact -->

# What the compression test proved

**Private three-gateway test, not a live mesh or radio run.**

### Passed with compression off, LZ4 and zlib

- **Commands:** all **1,536 command/acknowledgement exchanges** completed with exact payloads.
- **Data:** **6,110 received payloads** matched their originals, including final periodic values. Contact removal/reappearance also passed.
- **Monitoring:** both platform peers stayed reachable; all six WAN topics were captured. No native ERROR/FATAL lines or capture drops.

Older periodic updates may intentionally be replaced by newer values. The data count is **not a claim that every periodic update arrived**.

### CPU result: less total gateway work

Burst CPU time, summed across three gateways: **1,150 ms off -> 550 ms LZ4 / 540 ms zlib**.
Sending fewer bytes shortened the run; this does **not** measure compression's CPU cost alone.

> Next: changing filters/teams, restart and removal cases, sustained load, packet loss/repair, and real radio tests. Not deployed.

<!--
Source: ../../spikes/full_union_compression/README.md;
../open-wan-investigations.md (WAN-01 remains partially qualified).
All algorithms use the same fresh repaired build. The uncompressed baseline
first exposed two six-topic monitoring bugs: RouterConfig/health-cache filter
sequences were bounded to three, and health dispatch omitted P1_Periodic.
The user authorized source repairs and type regeneration before comparison.
These repairs and experimental compression are not deployed. Rebuild consumers
together before deployment of the changed six-record type bounds.
Six cases, 24 stages; 647 offered originals per stage. Required Event coverage,
distinct contacts and explicit paced final periodic markers passed. KEEP_LAST
and existing one-second LAN filters can omit periodic intermediates; 6,110
delivered originals is not all offered samples and not a no-loss Event proxy.
Mission policy intentionally excludes ActPlatformStatusMode and peer
PlatformData; negative LAN checks preserve that behavior. This is not a new
arbitrary dynamic-filter transition or sender-exclusion byte-measurement gate.
Mean burst wall time: off2.578 / LZ4 1.249 / zlib1.254 seconds, including a
fixed 1.2-second final-marker settling interval. CPU sums the three native
gateway processes; it is not isolated compressor cost or fleet radio capacity.
Native warnings are not claimed absent. Two native type/binding targets and
21 affected offline wire-parser tests passed; not a new full router test suite.
Same-key State churn, unregister, incompatible readers, sustained overload,
loss/repair, exact token charging and EMANE/fleet remain separate.
-->

---

<!-- _class: compact -->

# Rejected: static discovery that sends everything

**Limited Bandwidth Endpoint Discovery (LBED)** removed SEDP in the tested rig.
Dynamic reader selection remained correct, but sender suppression disappeared.

| Discovery path | Selected deliveries | Actual WAN transmissions |
|---|---|---|
| SPDP2 + SEDP | 1 / 2 / 3 / 1 | **1 / 2 / 3 / 1** |
| SPDP2 + LBED; initial CFT in catalog | 1 / 2 / 3 / 1 | **3 / 3 / 3 / 3** |
| SPDP2 + LBED; no catalog CFT | 1 / 2 / 3 / 1 | **3 / 3 / 3 / 3** |

> Do not adopt LBED for this WAN: dynamic sender-side audience filtering is required.

<!--
Source: ../../spikes/routing_service_lbed/README.md:110-188.
Two real Routing Service processes, stable endpoints, actual native admin
UPDATE/GET and single-parameter CFT changes. All nine updates and twelve GETs
were correlated and successful; no capture drops/native ERROR/FATAL lines.
Independent WAN destination-port captures deduplicate writer sequences.
The forwarding-only LBED matrix succeeds; that does not establish WAN filtering.
This is not a universal claim about every possible static-discovery system.
Retaining SEDP or adding a separately qualified sender-policy mechanism would
be necessary for this requirement.
The characterization intentionally exits 1 with
DYNAMIC_WRITER_FILTERING_NOT_OBSERVED rather than producing a misleading PASS.
Related deck: dds-discovery-strategies.md.
-->

---

# Runtime adaptation: an option, not an installed loop

```text
RTT + backlog growth + repair traffic
                |
       Bounded policy decision
                |
      TuningPolicy -> local actuator -> applied-state readback
```

- Reduce Periodic load first; preserve required Event service.
- Use sustained thresholds, different enter/exit limits and slow recovery.
- Qualify live actuator access before promising dynamic controller changes.

> A topic carries tuning intent. It does not apply QoS by itself.

<!--
Source: adaptive-runtime-tuning.md, especially Which options can actually
change live, Thresholds choose a mode, and Fit the existing three-topic WAN.
This is a proposal, not new IDL, an implemented controller or a native RS feature
automatically exposed through a policy topic.
Candidate actuators: supported interest-parameter updates, producer reporting,
qualified minimum spacing and native token-bucket changes if accessible.
Native mutability and current RS integration are different questions.
Do not assume writer flow-controller assignment, history, reliability, heartbeat
periods or maximum repair-response size can be changed live.
Use explicit bounds, one authorized owner, policy revision/expiry, failure
reporting, acknowledgment/readback and local safe behavior when WAN is stale.
RTT can rise from configured delay, not just congestion; stale RTT is unknown.
Allocation must account for the whole shared link, not independent node optimism.
-->

---

<!-- _class: compact -->

# Measure at the right layer

| Measurement | Use it for | Do not call it |
|---|---|---|
| Application sequence / ACK audit | Required-event delivery and useful results | WAN byte usage |
| Writer protocol counters | Pushes, repair and queue progress | Complete radio traffic |
| WAN capture | DATA, discovery, reliability traffic and packet copies | RF airtime |
| MAC / RF counters | Medium/model traffic and overheard frames | Delivered DDS application bytes |

Compare steady state, startup, interest changes, outage and recovery separately.

> Use matched time windows, zero-drop captures and explicit unknowns.

<!--
Independent evidence: ../../spikes/mac_rtps_accounting/README.md:50-70,92-145,
172-195. A historical Platform_30 window showed MAC input388.14kbps versus
domain-200 Ethernet RTPS35.52kbps; overheard destination-filtered traffic and
measurement layers explain why these must not be an efficiency ratio.
Later capture-time validation passed264 exact frame/byte comparisons in33
valid windows per node on two nodes, excluding explicit capture gaps.
Those are accounting controls, not radio capacity qualification.
Current traffic-accounting-design.md is a future semantic-bucket design;
do not claim every proposed semantic category is already in the monitor.
Method references: ../delivery-audit-framework.md;
../traffic-accounting-design.md; ../../spikes/write_drop_accounting/.
Do not sum TX and destination RX as independent generated traffic; do count
actual transmitted receiver copies and repairs as WAN cost.
No customer-derived workload statistics are reproduced in this deck.
-->

---

<!-- _class: compact -->

# Next qualification: spend effort where bytes disappear

| Priority | Compare | Required gate |
|---|---|---|
| 1. Selection | Current ACT interests versus deliberately broad interests | Native writer / wire reduction; correct mission/team transitions |
| 2. Offered load | Default Periodic input versus approved spacing/report rates | WAN savings; per-instance freshness; unaffected commands |
| 3. Compression | Union baseline versus LZ4/zlib on representative payloads | Lifecycle, restart, latency and CPU correctness |
| 4. Shared capacity | Fixed budgets versus bounded adaptive policy | Fleet headroom; bounded queues; full Event coverage and recovery |

> Hold topology, payloads, fanout and link conditions constant between comparisons.

<!--
This is a proposed sequence, not a performance result or implementation plan
approved by this deck. The bounded six-topic union/async/controller compression comparison now passes.
It does not qualify the broader lifecycle/load/recovery or radio workload.
Representative radio tests should include small commands, actual status sizes
and values, mission/team interest changes, multiple readers, packet loss,
blackout/rejoin, dispose/unregister/revival, slow consumers and admission limits.
Include discovery, RouterHealth/configuration and repair bytes in total cost.
Do not relax event-loss, freshness or latency thresholds to make tuning pass.
Use the owned coordinated mesh lifecycle if deployment is separately requested.
Sources: ../../router/routing_service/README.md:299-326,1047-1050;
../../spikes/full_union_compression/README.md, Remaining limits;
../instance-state-through-ddil.md; adaptive-runtime-tuning.md.
-->

---

# Key takeaways

1. **Data filtering is a first-class bandwidth control:** do not transmit data nobody needs.
2. **Prove sender suppression:** fewer receiver deliveries are not enough.
3. **Separate delivery contracts:** preserve Events; replace only appropriate State.
4. **Compress typed unions using measured payloads:** random command bytes offer little saving.
5. **Keep the evidence boundaries:** no measured fleet saving or adaptive controller yet.

> Select less, then compress what remains, within a measured shared-link budget.

<!--
Repository evidence map / reusable reading list:
- Current RS implementation and qualification:
  ../../router/routing_service/README.md
  ../router-processor-completion-plan.md
  ../two-topic-wan-migration-plan.md (current checkpoints3.54/3.55)
- Filtering: ../platform-mission-cft-plan.md;
  ../../router/qualification/test_two_topic_qualification.py;
  ../../router/test_e2e/test_routing_service_mesh.py
- Runtime typed schema alternative: ../../spikes/discovered_union_catalog/README.md
- Union compression: ../../spikes/full_union_compression/README.md
- LBED rejection: ../../spikes/routing_service_lbed/README.md
- Discovery/participants: ../../spikes/wan_dp_route_tradeoffs/README.md
- Startup: ../startup-optimization.md; ../../spikes/preemptive_acknack/README.md
- Historical pacing/recovery: ../wan-topic-tradeoffs-2026-11-12.md;
  ../../spikes/instance_churn_reconnect/README.md; ../../spikes/three_topic_wan/README.md
- Current DDIL limits: ../instance-state-through-ddil.md; ddil-recovery.md
- Accounting: ../../spikes/mac_rtps_accounting/README.md
- Adaptive option: adaptive-runtime-tuning.md
No production code, QoS, persistent binary or running mesh was changed.
No aggregate savings percentage is inferred by combining these separate studies.
-->
