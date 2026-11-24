---
marp: true
theme: rti
paginate: true
size: 16:9
title: "Bandwidth Optimization"
description: "Repository-tested WAN strategies and options, with data filtering, discovery, binary payloads and compression"
footer: "RTI Connext | Bandwidth Optimization | Evidence: 2026-11-20"
---

<!-- _class: lead -->

# Bandwidth Optimization

## Send less. Send selectively. Preserve required data.

**Data filtering, discovery, delivery policy and binary compression**

Current implementation, isolated tests, rejected approaches and next options.

<!--
Evidence snapshot: 2026-11-20, Connext 7.7.0. Repository-wide synthesis,
not a new full-suite run, deployment or fleet-bandwidth benchmark.
The requested mesh was stopped before this deck was created.
The completed native Routing Service processor uses Event / State / Periodic.
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
../../spikes/binary_wan_compression/README.md:16-43;
../../spikes/mac_rtps_accounting/README.md:50-70.
-->

---

<!-- _class: compact -->

# What is current, and what is not?

| Evidence level | Included strategies |
|---|---|
| Implemented; prior native/live qualification | Three WAN families; audience/mission receive interests; shared pacing; LAN isolation |
| Qualified source; not deployed | Application startup-request suppression; one-second startup/response spreading |
| Isolated proof; not ACT integration | Fixed binary envelopes; native LZ4/zlib; measured dynamic sender filtering |
| Proposal / next qualification | Adaptive control topic; fleet-wide allocation; representative radio/compression comparison |

**Current processor:** Routing Service owns three WAN writer/reader pairs per gateway.

> Functional qualification is not a measured percentage of fleet bandwidth saved.

<!--
Current architecture/deployment: ../two-topic-wan-migration-plan.md:2786-2795
(checkpoint 3.54); diagnostics follow-up at 2857-2871 is source only.
Current mission interests: ../../router/routing_service/README.md:15-18.
Current shared pacing: ../../harness_v2/qos/act_qos_profiles.xml:935-972,1025-1067.
LAN isolation: ../../harness_v2/qos/act_qos_profiles.xml:114-136.
Startup source qualification: ../startup-optimization.md, Fresh qualification.
Binary proof: ../../spikes/serialized_wan_envelope/README.md:21-40.
Compression proof: ../../spikes/binary_wan_compression/README.md:74-99.
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
Current ACT receiver-configured interests and native binary-envelope filtering
are separate qualification surfaces.
Sources: ../../spikes/serialized_wan_envelope/README.md:21-50;
../../spikes/routing_service_lbed/README.md:110-150;
../../router/routing_service/README.md:913-947.
CFTs do not strip selected fields out of a sample or provide authorization.
Opaque payload CFTs can inspect the exposed metadata, not arbitrary inner bytes.
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
| Binary-envelope gateway spike | **45/45** wrong-source/topic/destination offers suppressed at writer |
| Native Routing Service + SEDP | Offer 3 each phase; select 1 / 2 / 3 / 1; WAN transmits **1 / 2 / 3 / 1** |
| Native compressed Event + dynamic interests | B -> C -> B; offer both destinations; **one** compressed transmission per phase |
| Current ACT mission/team workflows | Correct applied interests and receive exclusions; full-workload WAN savings not quantified |

> The mechanism is proven. Its current ACT workload benefit still needs measurement.

<!--
Sources:
../../spikes/serialized_wan_envelope/README.md:21-40;
../../spikes/routing_service_lbed/README.md:110-150;
../../spikes/binary_wan_compression/README.md:74-99;
../../router/test_e2e/test_routing_service_mesh.py:723-850.
The first rig is custom C++ gateways, not native RS integration.
The second and third use actual RS-owned endpoints, fixed envelopes and native
administration; they are not the completed ACT processor's entire route policy.
The B/C/B check uses a single expression parameter and stable endpoints.
Older adapter qualification separately checks zero pushed_sample_count growth
for excluded teams and old-team updates after filter convergence:
../../router/qualification/test_two_topic_qualification.py:168-169,330-390.
Do not silently re-label that older qualification as current native RS coverage.
These counts come from distinct experiments and must not be added as one gate.
-->

---

<!-- _class: compact -->

# Keep three different delivery contracts

| WAN family | Current policy | Bandwidth purpose |
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
- High/Normal channels provide scheduling choices within three physical pairs.
- Default allowance: **2,500 bytes per 10 ms**, nominally **2 Mbps**.
- Priority alone did not reorder a single FIFO stream in the isolated test.
- Fleet load must include receiver copies, discovery, health and repair traffic.

> A 2 Mbps budget at every gateway is not a 2 Mbps fleet-wide allocation.

<!--
Current source: ../../harness_v2/qos/act_qos_profiles.xml:935-972,1025-1067;
../../router/routing_service/README.md:460-464,782-784.
All three current RS writers use dds.flow_controller.token_bucket.act_rs.
The high-priority-first multichannel split thresholds are Event 500, State 300,
Periodic 400; do not confuse thresholds with every configured sample priority.
Token allowance is not a demonstrated usable RF rate, fairness guarantee,
per-class reservation or a cap on all discovery/protocol traffic.
Historical ordering proof:
../../spikes/instance_churn_reconnect/README.md:422-456.
It shows why sample priority needs a qualified queue/channel/controller setup,
not guaranteed priority during reliable-history repair.
Current RF Pipe is 2 Mbps, with virtual transport bitrate=0:
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
- Consolidate WAN participants and endpoints: current gateways use **three pairs**.
- Keep LAN discovery off `emane0`; disable unnecessary RTPX tool announcements.
- Older one-WAN-participant test: discovery TX **13.86 -> 7.67 kbps**.
- After historical LAN-isolation fixes: **zero** other-domain RTPS/RTPX in 45 s.

> Reducing endpoint count is useful; measure actual discovery bytes separately.

<!--
Current architecture: ../two-topic-wan-migration-plan.md:2786-2795.
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
Current WAN is explicit unicast UDPv4 with WAN_RECEIVE_MULTICAST=0.
Discovery multicast does not require user-data multicast; neither is assumed
cheaper on a multi-hop radio. A directory introduces its own availability and
address-maintenance requirements and does not automatically relay user traffic.
Knowing one ordinary DDS peer does not automatically reveal all its peers.
The C2 application directory and CDS are distinct designs; compatibility with
the selected native discovery plugins and Routing Service needs qualification.
Prepared XML/type knowledge is not evidence that Connext omits all type metadata.
The earlier discovery deck's reduced-type-propagation suggestion is explicitly
pre-7.7; do not import it as a tested Connext7.7 knob.
The binary-envelope spike is the separate proof of avoiding application type
graphs over the WAN, with local schema/decoder and tooling costs.
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

# Binary payloads: a schema strategy, not free bytes

| Typed WAN union | Fixed envelope + original binary CDR |
|---|---|
| WAN DDS tools see nested application fields | WAN tools see metadata and opaque payload bytes |
| Union contains all declared branch types | WAN type graph excludes application structures |
| New branch needs coordinated contract preparation | Tested new local payload type without replacing WAN endpoints |
| Payload-field CFTs are possible | CFTs select exposed metadata; local schema enables decoding |

Binary spike: **15 exact originals restored; 45 metadata exclusions**.
Its sample buffers grew **2,010 -> 4,554 bytes** after envelope wrapping.

> No application type graph over WAN does not mean no schema or automatic size savings.

<!--
Primary evidence: ../../spikes/serialized_wan_envelope/README.md:21-40,43-80,
82-140. CDR means Common Data Representation, the DDS serialization format.
The 2,010 and 4,554 totals are serialized original/envelope sample buffers,
excluding discovery, RTPS, repair and network headers; not a typed-union comparison.
The gateways discover compatible complete types on their respective local LANs.
This is DynamicData serialize/copy/deserialize, not zero-copy original-wire relay.
WRITE only, one top-level string key, same-version schema fingerprint.
Dispose/unregister, generic keys, ACT codec/auto-QoS/lifecycle/restart integration
remain separate gates. A fixed outer contract does not infer a type from bytes.
Other tested architecture:
../../spikes/discovered_union_catalog/README.md:53-124,150-174.
Runtime discovered unions remove manual authoring, not deployment-specific schemas.
Context: ../wan-schema-propagation-options.md predates the later binary spike;
use the spike's completed results, not that older document's untested status.
-->

---

<!-- _class: compact -->

# Compression: binary data can still be redundant

| Synthetic fixture envelope | LZ4 wire saving | zlib wire saving |
|---|---:|---:|
| Small nested status: 248 B | 0% | 14.0% |
| Structured numeric: 4,272 B | 78.8% | 83.2% |
| Structured numeric: 16,568 B | 93.9% | 94.2% |
| High-entropy binary: 32,940 B | 0% | 0.17% |
| Already-compressed content: 32,972 B | 0% | 0.18% |

Savings cover complete user-DATA frames in each status + command/echo workload.
**Actual fleet-wide savings remain unknown.**

> Representative payloads decide the benefit, not the word "binary."

<!--
Source: ../../spikes/binary_wan_compression/README.md:16-43.
Two repetitions per cell; algorithms BEST_SPEED, writer threshold zero.
Each repetition mixes 32 State writes, 32 Periodic writes and 32 bidirectional
command/echo exchanges. Fixture envelope size is the telemetry size, not the
size of every sample in the measured mixed workload.
Wire percentages include captured user-DATA headers, not discovery or
ACK-only packets. Structured numeric fixtures are deliberately redundant;
these are not representative production-payload compression ratios.
The tiny zlib entropy/already-compressed savings include envelope/command bytes.
LZ4 with an 8KiB threshold misses the useful 4KiB fixture completely.
No RF emulation, link-rate cap, loss or current ACT workload in this test.
-->

---

# Compression: qualified mechanism, not yet deployment

- Actual Routing Service endpoints automatically compress/decompress envelopes.
- Matrix: **6,403 exact deliveries**, **203 sender exclusions**, zero capture drops.
- Dynamic compressed filtering works with **SPDP2 + SEDP**.
- LZ4 is the next radio-test candidate; compare zlib using real payloads.
- **Reject tested bzip2:** 256 native errors despite correct deliveries.

> Choose algorithm and threshold by wire savings, CPU cost and command latency.

<!--
Source: ../../spikes/binary_wan_compression/README.md:45-140.
Counts include the complete matrix, including the explicitly rejected bzip2
variant; they must not be described as a clean all-variants PASS.
Report: COMPLETED_WITH_REJECTED_VARIANTS; runner intentionally exits 1.
Uncompressed/LZ4/zlib/threshold/compatibility/dynamic variants had zero native
ERROR/FATAL lines. LZ4-writer/compression-disabled-reader control did not deliver.
Large structured workload CPU (both RS processes): none40ms, LZ4 40ms,
zlib60ms, bzip2 370ms; command RTT p95 2.18/1.89/2.01/5.91ms.
CPU resolution was 10ms; do not call LZ4 free or small RTT differences significant.
RTT includes Python echo/polling and native forwarding, not RF or codec-only work.
Unique outer instance IDs avoid State replacement; no lifecycle/recovery gate.
Compression is native on the outer envelope, not hand-compressed inner payloads.
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
| 3. Representation | Typed baseline versus integrated binary + LZ4/zlib | Real payloads; lifecycle, schema identity and restart correctness |
| 4. Shared capacity | Fixed budgets versus bounded adaptive policy | Fleet headroom; bounded queues; full Event coverage and recovery |

> Hold topology, payloads, fanout and link conditions constant between comparisons.

<!--
This is a proposed sequence, not a performance result or implementation plan
approved by this deck. Existing isolated experiments must not be mistaken for
the current completed ACT processor's combined workload.
Representative radio tests should include small commands, actual status sizes
and values, mission/team interest changes, multiple readers, packet loss,
blackout/rejoin, dispose/unregister/revival, slow consumers and admission limits.
Include discovery, RouterHealth/configuration and repair bytes in total cost.
Do not relax event-loss, freshness or latency thresholds to make tuning pass.
Use the owned coordinated mesh lifecycle if deployment is separately requested.
Sources: ../../router/routing_service/README.md:299-326,1047-1050;
../../spikes/binary_wan_compression/README.md, Limits and next gate;
../instance-state-through-ddil.md; adaptive-runtime-tuning.md.
-->

---

# Key takeaways

1. **Data filtering is a first-class bandwidth control:** do not transmit data nobody needs.
2. **Prove sender suppression:** fewer receiver deliveries are not enough.
3. **Separate delivery contracts:** preserve Events; replace only appropriate State.
4. **Binary and compression solve different problems:** schema cost versus sample cost.
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
- Native binary envelope: ../../spikes/serialized_wan_envelope/README.md
- Runtime typed schema alternative: ../../spikes/discovered_union_catalog/README.md
- Compression: ../../spikes/binary_wan_compression/README.md
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
