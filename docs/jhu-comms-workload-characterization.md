# Comms workload characterization for ACT

**Date:** 2026-11-06

**Project:** ACT - Autonomous Collaborative Teaming, an internal project

**Purpose:** Reproduce the discovery, data sizes, mission traffic, and platform-to-platform behavior of the original comms use case in the ACT simulation.

**Evidence handling:** Local analysis only. Customer captures, recorder databases, and configurations were not uploaded or changed. This document contains customer-derived technical information; review before external distribution.

## 1. Executive conclusion

Do not model this customer as "hundreds of topics, each sending large samples over the radio." The evidence supports a layered workload:

1. **A much larger local DDS environment behind each gateway.** One delivered UMAA catalog contains **401 topic/type registrations**. The historical recorder discovered **154 nonempty topic names and 153 type names** across its observation, not all concurrently active or all forwarded.
2. **A small off-platform working set.** The delivered direct-UMAA routing filters select **14 distinct topic suffixes**. Later captures also show aggregation into **`/umaaOutgoingStatus` and `/umaaOutgoingCommand`**, with additional wire-message and platform-navigation channels in some versions.
3. **Mostly small operational samples.** Supplied direct-UMAA sizes range from **60 to 4,420 bytes**; the three basic status types have maxima of **104, 256, and 332 bytes**. Captured command/status wrappers are generally hundreds, not megabytes, of bytes.
4. **Approximately 1 Hz basic mission status, with aperiodic mission updates.** During the documented real mission interval, pose, velocity, and speed each average **0.987 Hz**. Contact-set elements average **0.779 updates/s in aggregate**; mission-plan and waypoint changes are sparse and bursty.
5. **The best short cross-network calibration is 376.04 kbps as captured**, for two platforms plus CCS. It includes alternate-locator sends and discovery leaking from other domains. The subset addressed strictly between the three cross-network IPs is **198.64 kbps of user DATA-bearing frames**, not a proven deduplicated RF requirement.
6. **Discovery must be measured independently of steady data.** The March cross-network window contains participant announcements but no endpoint announcements. That is a mid-run observation, not evidence that startup discovery costs nothing.
7. **The seven-boat capture is a different, pathological simulator workload.** Its readable prefix averages **21.22 Mbps**, with **94.63% of recorded frame bytes in user DATA-bearing packets**. Simulator geospatial updates and platform-navigation traffic dominate; this is not principally an endpoint-discovery bandwidth explosion.
8. **Run separate historical and clean-architecture profiles.** Preserve the multi-interface sends, unwanted participant announcements, and simulator fan-out in a historical-reproduction test. Exclude them from the isolated operational baseline, then compare.

**Recommended starting point:** 1 CCS plus 2 platforms, a 14-type direct-UMAA profile and a separate aggregated profile, 1 Hz primary status, sparse reliable mission commands, 100-500 byte normal samples, occasional 1.5-4.4 KB mission/contact samples, and explicit startup/rejoin tests. Scale to 7-8 platforms after calibrating the two-platform case.

## 2. Evidence and confidence

Source locations and identifying names are omitted. Evidence IDs below refer to the original locally held materials.

| ID | Evidence | What it establishes |
|---|---|---|
| E1 | Type-size workbook | Supplied min/max sample sizes and type-object sizes for 14 selected types. These are workbook values, not newly measured packet percentiles. |
| E2 | Delivered v10 routing configuration, QoS, and readme | Architecture, topic selection, partitions, type preloading, and intended QoS. Not proof that every later capture ran this exact configuration. |
| E3 | March capture set and network notes | Simultaneous CCS LAN, platform LAN, and cross-network observations; explicit router IP-to-platform mapping. |
| E4 | Two-platform mission capture | Startup/discovery-heavy mixed-network run and aggregated command/status samples. Packet timestamps are December 2024 despite the directory name. |
| E5 | Seven-boat capture | Simulator traffic fan-out and discovery leakage, but only for its readable prefix. |
| E6 | November captures and timeline | Reduced-message-set bench test, startup, a command event, and shutdown. Two full interface captures overlap and must not be added together. |
| E7 | Radio-node capture | Additional wire-status channel and substantial non-DDS background traffic. |
| E8 | Mission notes and DDS recorder directory | Actual 2024 mission context, recorded application sample rates, and historical discovery scope. Recorder JSON lengths are not serialized DDS or network sizes. |
| E9 | WireMessage IDL and PlatformIns IDL | Wrapper shape and navigation schema. WireMessage has two long keys and an **unbounded octet sequence**; there is no finite maximum in this IDL. |
| E10 | Original comms problem presentation and optimization history | Original squad/control architecture and later optimization history; contextual notes are not substituted for packet measurements. |

**Confidence:** High for packet counts, durations, recorded-byte totals, the March cross-network split/directions, workbook entries, and recorder counts. Conditional for translating captures to actual RF airtime, inferring uncaptured discovery, or assigning names to March writer streams. Proposed ACT settings below are recommendations, not measurements.

## 3. Architecture to reproduce

```text
local DDS applications                           local CCS applications
       |                                                 |
 platform-local domain                            CCS-local domain
       |                                                 |
 platform gateway ---- shared comms/WAN domain ---- CCS gateway
       |
       +----------- optional squad/platform-peer routes
```

The gateway is the architectural boundary: discover and serve the full local application environment, but export only selected data and WAN endpoints.

The delivered configuration separates platform, comms, CCS, simulator, and administration roles. Its platform-to-CCS route publishes in the `PLATFORM` partition, the reverse route uses `CCS`, and optional peer routing uses `SQUAD`. Peer routing defaults to disabled and selects `*ContactReportType` in that version. A separate command route selects `*MissionPlanObjectiveAddCommandType`; route naming alone does not establish that destination filtering was active in a particular capture.

**Domain numbers changed between versions.** March announcements identify platform domain 0, comms 1, CCS 2, simulator 11, and administration/monitoring 100. Delivered v10 defaults use platform 1, comms 2, and CCS 3. ACT's current documented topology uses CCS LAN 20, platform LANs starting at 30, and WAN 200. Preserve the roles, not old numeric IDs.

The early problem presentation also describes multiple ashore/afloat control stations, squad membership, and selectively shared data. These are valid ACT extension scenarios, but the measured calibration case here has one CCS and two platforms.

## 4. How many topics and types?

These are different quantities and should not be collapsed into one "topic count":

| Scope | Evidence-backed count | Interpretation |
|---|---:|---|
| Delivered UMAA catalog | 401 topic definitions and 401 distinct type registrations | Available schema/catalog, not 401 active WAN streams. |
| Historical recorder discovery | 154 nonempty topic names; 153 nonempty type names | Union of publication/subscription observations over time. Includes local application traffic and restarts. |
| Historical recorder tables | 208 topic/domain tables: 133 on domain 0, 75 on domain 32 | Tables can be empty; domain-specific duplicates are not new type definitions. |
| Delivered direct-UMAA route selection | 14 distinct suffixes | 13 platform-to-CCS selectors, plus one distinct command selector; reverse-route selectors mostly reuse these names. |
| November full platform-side capture | 7 topic names, 7 type names, 18 endpoint GUIDs | Wireshark discovery-associated fields with IP reassembly. Only two named application topics carry the measured user DATA below. |
| December mixed-network capture | 553 topic names, 510 type names, 3,825 endpoint GUIDs | Cumulative decoded fields across a mixed-domain run, not the desired WAN working set or simultaneous endpoint count. |
| Seven-boat readable prefix | 14 topic names, 14 type names, 277 endpoint GUIDs | Includes simulator and RTI administration/monitoring channels. Not 14 operational application channels. |
| March cross-network capture | 10 active physical user writers; 18 participant source GUID prefixes | Endpoint announcements are absent in this short window, so the exact topic/type count cannot be reconstructed from discovery in this capture alone. |

The historical discovery database also contains **1,645 distinct participant keys over its recording**. Do not instantiate 1,645 participants per platform: that is cumulative history, not a simultaneous topology.

### Direct-UMAA type sizes

Values below are **bytes, as supplied in E1**. Namespace prefixes are shortened for readability; the workbook contains the complete type names.

| Type suffix | Min sample | Max sample | Type object |
|---|---:|---:|---:|
| MissionPlanObjectiveAddCommandType | 236 | 4,420 | 4,632 |
| TaskPlanTypeObjectivesSetElement | 148 | 4,332 | 4,364 |
| ContactReportTypeContactsSetElement | 156 | 4,212 | 9,584 |
| MissionPlanReportTypeMissionPlanSetElement | 144 | 2,716 | 3,992 |
| RouteObjectiveTypeWaypointsListElement | 117 | 1,521 | 18,928 |
| ControlSystemTransferRequest | 72 | 648 | 1,688 |
| ControlSystemControlReport | 68 | 456 | 2,020 |
| ControlSystemTransferReport | 60 | 384 | 1,824 |
| ControlSystemControlRequest | 56 | 380 | 1,400 |
| GlobalPoseReportType | 144 | 332 | 6,596 |
| VelocityReportType | 120 | 256 | 4,652 |
| MissionPlanReportType | 144 | 180 | 2,140 |
| ContactReportType | 100 | 120 | 2,044 |
| SpeedReportType | 60 | 104 | 2,512 |

The type objects sum to **66,376 bytes**, before endpoint announcement envelopes, QoS, names, copies, fragmentation, and repair. This is a schema-size inventory, not a measured discovery transfer.

In particular, the waypoint-element type object is **18,928 bytes** while its maximum sample is **1,521 bytes**. A synthetic `byte[payload_size]` type can match data bandwidth while radically underrepresenting discovery.

**Implication for ACT:** use real types where locally available and authorized, or deliberately model both sample size and schema complexity. Distinct topic names sharing one tiny type are not equivalent to 14 distinct complex type definitions.

## 5. Actual mission behavior from the recorder

The notes identify a return transit/mission evolution on **2024-03-10, 12:50-15:31 PDT**, or **19:50-22:31 UTC**. The query window is half-open and exactly **9,660 seconds**.

Counts below use valid samples received in that interval from domain-32 recorder tables. Reception time measures what the recorder saw, not necessarily every original publication.

| Recorded topic family | Valid samples | Mean samples/s | Distinct recorded instance handles |
|---|---:|---:|---:|
| GlobalPoseStatus / GlobalPoseReport | 9,538 | 0.98737 | 1 |
| VelocityStatus / VelocityReport | 9,538 | 0.98737 | 1 |
| SpeedStatus / SpeedReport | 9,538 | 0.98737 | 1 |
| ContactReport parent | 3,978 | 0.41180 | 1 |
| ContactReportContactsSetElement | 7,522 | 0.77867 | 373 |
| MissionPlanReport parent | 24 | 0.00248 | 2 |
| MissionPlanReportMissionPlanSetElement | 22 | 0.00228 | 11 |
| MissionPlanTaskPlansSetElement | 20 | 0.00207 | 11 |
| RouteObjectiveWaypointsListElement | 34 | 0.00352 | 26 |

**Interpretation:**

- **1 Hz basic status** is a well-supported starting point.
- Contact updates need multiple keyed instances and churn. **373 distinct handles over the mission does not mean 373 simultaneous contacts.** The 0.779 Hz rate is aggregate, not per contact.
- Mission plans, objectives, and waypoints are event-driven collections. Replaying a continuous 1 Hz stream on every mission topic would substantially overstate normal data traffic.
- The notes describe station keeping, route changes, obstacle avoidance, and eight discrete avoidance interactions. They do not provide a synchronized packet-level event log for the later simulation captures.
- This recorder is an older schema/deployment than the 2025 captures. Use it for traffic patterns and rates; do not assume its topic names or binary sizes are identical to the later UMAA catalog.

For scale, the workbook maxima for pose + velocity + speed total **692 bytes per 1 Hz update cycle**, or **5.536 kbps of sample bytes per platform**, before RTPS/IP/link overhead. At their workbook minima, the same three types total **324 bytes/s**, or **2.592 kbps**. Neither figure includes contacts, commands, discovery, peer fan-out, or repairs.

## 6. Capture-wide bandwidth measurements

**Units:** decimal kbps/Mbps. Bytes are the pcap's recorded original frame lengths, not pcap file size. Ethernet captures include recorded Ethernet headers but normally omit preamble, inter-frame gap, and FCS; the radio-node capture uses Linux cooked v2 pseudoheaders. These figures are **not RF airtime**.

Mean = `8 * sum(frame.len) / (last_timestamp - first_timestamp)`. Percentiles and maxima use complete, UTC-aligned one-second bins, exclude partial edge bins, and include zero-traffic bins. Maxima therefore depend on bin alignment.

| Capture / observation point | Packets analyzed | Duration (s) | Mean kbps | Median 1-s kbps | P95 1-s kbps | Max 1-s kbps |
|---|---:|---:|---:|---:|---:|---:|
| March CCS LAN | 10,605 | 86.943 | 241.52 | 236.56 | 355.23 | 406.77 |
| March cross network | 9,172 | 84.809 | 376.04 | 375.49 | 426.38 | 549.31 |
| March platform-11 LAN | 114,182 | 82.357 | 3,128.70 | 3,106.30 | 3,231.82 | 3,555.17 |
| December two-platform mixed-network run | 109,568 | 544.377 | 709.14 | 204.96 | 2,614.88 | 29,053.54 |
| November full platform-side / m126 | 9,030 | 342.649 | 41.13 | 31.46 | 113.42 | 568.96 |
| November full CCS-side / m140 | 9,095 | 350.136 | 41.08 | 29.52 | 114.26 | 544.13 |
| February radio-node / all captured protocols | 1,023,158 | 282.046 | 4,966.40 | 5,607.18 | 7,036.32 | 8,135.50 |
| Seven-boat simulator / readable prefix only | 785,291 | 104.359 | 21,224.17 | 10,625.86 | 68,308.37 | 71,432.66 |

Do not add rows together: capture points can overlap, windows differ, and versions/workloads differ. The March LAN-to-cross-network contrast demonstrates why the local DDS load and exported WAN load need separate generators.

### Capture timing and integrity

- March cross network: **2025-03-27 17:04:31.295222-17:05:56.104140 UTC**.
- December mission file: **2024-12-13 04:03:44.301891-04:12:48.679374 UTC**, despite being stored under a January-2025 directory.
- Radio-node file: **2025-02-26 23:28:21.132735-23:33:03.178378 UTC**, despite the February-27 directory.
- Seven-boat readable prefix: **2025-02-05 11:25:13.724294-11:26:58.083386 UTC**. Folder dates and later review dates are not the recording date.
- The seven-boat file is **2,549,638,118 bytes**. At byte **289,431,560**, the next pcap record claims a **415,182,923-byte captured packet**, exceeding its 262,144-byte snaplen. Independent `capinfos` and the local parser agree on **785,291 readable packets**. Only approximately **11.35% of the file bytes** precede the invalid record. Nothing in this report characterizes the unread remainder.
- The two November files without `full` in their names are snap-truncated to about 120 bytes and were excluded from payload-size characterization.

## 7. Best inter-platform calibration: March cross network

E3 maps the routers as follows:

| Node | Cross-network IP | Local-side IP |
|---|---|---|
| Platform 11 | 172.27.0.2 | 172.28.0.15 |
| Platform 12 | 172.27.0.3 | 172.29.0.21 |
| CCS | 172.27.0.4 | 192.168.100.14 |

### Exactly measured traffic split

This capture has no IP-fragmented or snap-truncated packets, and is the strongest classification result:

| Exclusive packet class | Packets | Recorded frame bytes | Mean kbps | Share of total bytes |
|---|---:|---:|---:|---:|
| Application user DATA-bearing | 9,026 | 3,885,756 | 366.54 | 97.47% |
| Participant discovery DATA | 102 | 98,820 | 9.32 | 2.48% |
| ARP | 44 | 1,848 | 0.17 | 0.05% |
| **Total** | **9,172** | **3,986,424** | **376.04** | **100%** |

There are no captured endpoint-discovery announcements or standalone user ACKNACK/HEARTBEAT exchanges in this window. This does not establish their absence at startup or under loss.

### Directional data between the cross-network IPs

Each row counts complete DATA-bearing frames over the whole 84.809-second capture:

| Direction | Frame bytes | Mean kbps |
|---|---:|---:|
| Platform 11 -> CCS | 438,000 | 41.32 |
| Platform 12 -> CCS | 938,158 | 88.50 |
| CCS -> Platform 11 | 162,932 | 15.37 |
| CCS -> Platform 12 | 162,932 | 15.37 |
| Platform 11 -> Platform 12 | 200,376 | 18.90 |
| Platform 12 -> Platform 11 | 203,412 | 19.19 |
| **Cross-IP unicast subset** | **2,105,810** | **198.64** |

**Observed platform-to-platform traffic exists**, at approximately 19 kbps in each direction in this run. However, endpoint metadata is absent, so it cannot be identified confidently as operational ContactReport traffic rather than navigation/simulator traffic. Do not infer the exact v10 peer-route configuration from these packets.

### Alternate-interface amplification

Additional DATA-bearing flows use the same senders but local-side destination addresses:

- Platform 11 -> CCS local IP: another **438,000 bytes**.
- Platform 12 -> CCS local IP: another **938,158 bytes**.
- Platform 11 -> Platform 12 local IP: another **200,376 bytes**.
- Platform 12 -> Platform 11 local IP: another **203,412 bytes**.

These total **1,779,946 additional bytes**, or **167.90 kbps**. Packets include repeated physical-writer sequence numbers sent to different destination locators. The capture contains **9,026 user DATA transmissions but 3,715 distinct physical-writer/sequence pairs**, approximately **2.43 transmissions per pair**.

These are not automatically "retransmissions due to loss," and they must not be silently deduplicated in an as-captured bandwidth result. Some sends represent fan-out to different readers/nodes; some use alternate interface addresses. The 198.64 kbps subset is useful for calibration, but is **not proof** that a real radio would carry exactly that amount after interface cleanup.

### Observed serialized sample sizes

Here, measured size is the DATA serialized region **including the four-byte encapsulation header and any serialized alignment padding**, excluding RTPS DATA/inline-QoS and network headers.

| Physical writer-stream grouping | Number of writers | Min bytes | Median bytes | P95 bytes | Max bytes |
|---|---:|---:|---:|---:|---:|
| Fixed-size navigation-shaped streams | 6 | 356 | 356 | 356 | 356 |
| Variable stream A | 1 | 112 | 156 | 268 | 268 |
| Variable stream B | 1 | 76 | 276 | 472 | 472 |
| Variable stream C | 1 | 112 | 224 | 268 | 268 |
| Variable stream D | 1 | 84 | 256 | 396 | 396 |

The six 356-byte streams resemble the named PlatformIns data in other captures, but **topic identity is not proven in this window**. A physical writer count is not a distinct-topic count.

Recorded RTPS frame sizes have median **418 bytes** and P95 **506 bytes**. No need for megabyte-scale payloads is evident here.

## 8. Discovery, reliability, and version-specific behavior

### Discovery leakage is directly visible

The March cross network sees **18 distinct participant source GUID prefixes**, six from each router:

| Announced domain | Participant prefixes | Role identified in participant names |
|---|---:|---|
| 0 | 3 | Platform-local participants |
| 1 | 3 | Comms participants |
| 2 | 3 | CCS-local participants |
| 11 | 3 | Simulator participants |
| 100 | 6 | Routing Service administration/monitoring-side participants |

A clean one-WAN-participant-per-gateway design would expose **3 WAN participants**, not all 18. The intended baseline should pin the WAN transport to the WAN interface and keep local/simulator/admin domains off that interface. A separate historical profile should reproduce the leakage to quantify its cost.

### Startup cannot be calibrated from March alone

The December run has extensive discovery and **5,690 IP fragment packets**. Wireshark with IP reassembly decodes the broader topic/type inventory shown earlier. Its all-traffic median is only **204.96 kbps**, while its P95 is **2.615 Mbps** and its largest one-second bin is **29.054 Mbps**.

The local non-reassembling classifier attributes at least **35.21 MB of 48.25 MB** to participant/endpoint-discovery-bearing frames, approximately **73%**, with **3.44 MB** in identified user DATA-bearing frames. These are **partial attribution figures**, not a complete reassembled discovery/application byte split: first/continuation IP fragments and other RTPS control frames require care. Use the complete all-traffic envelope for burst calibration, not these lower bounds as an exact accounting partition.

November provides a command-event timeline: ground-station startup around 11:58, platform startup around 11:59, command around 12:00, platform shutdown around 12:02, CCS shutdown around 12:03 local time. Reproduce these phases rather than labeling the first N seconds of every file "startup."

### QoS facts from delivered files, not assumptions about all runs

The active v10 XML specifies:

- Compact participant-discovery base profile and UDPv4 transport for comms.
- **3 initial participant announcements**.
- Participant assert period **6,000 s**, lease duration **12,000 s**, loss-detection period **3,000 s**. These are unusually long; they are not the shorter historical values mentioned elsewhere.
- Discovery publication/subscription writer heartbeat periods **3 s**.
- Reliable aperiodic user writer heartbeat periods **3 s**, with `max_heartbeat_retries=1000`.
- TypeCode and TypeObject serialized-length limits **0 on the comms participant**; types are preloaded. Local platform/CCS profiles allow larger type objects.
- Report-type matching to the periodic profile, with a **1 s minimum-separation reader filter**; additional local overrides exist for pose and speed.
- Optional peer sessions disabled by default.

**Do not blindly apply those long discovery leases to ACT:** test stale-presence and rejoin behavior separately. Likewise, the 3-second heartbeat setting is not a guarantee of application delivery within 3 seconds.

Two important configuration inconsistencies:

1. The v10 readme mentions BZIP2 compression, but the inspected QoS XML has **no active compression setting**. Do not claim a compression ratio or attribute the measurements to compression without a capture/runtime check.
2. The v10 UDP `message_size_max=1450` property is **commented out**. Historical notes cite an MTU of **1268**, so even a 1450-byte transport setting would need re-evaluation for that path.

The February edited QoS also has its periodic downsampling block commented out. Therefore **1 Hz is a supported mission-pattern baseline, not a statement that every captured wrapper was rate-limited to 1 Hz**.

## 9. Aggregated operational traffic versus simulator traffic

### Captured command/status wrappers

| Capture / topic | DATA transmissions | Distinct physical-writer/sequence pairs | Min / median / P95 / max serialized bytes |
|---|---:|---:|---|
| November platform-side `/umaaOutgoingStatus` | 501 | 501 | 96 / 160 / 212 / 212 |
| November platform-side `/umaaOutgoingCommand` | 26 | 25 | 140 / 176 / 352 / 352 |
| December `/umaaOutgoingStatus` | 7,786 | 1,956 | 96 / 160 / 212 / 212 |
| December `/umaaOutgoingCommand` | 496 | 91 | 140 / 180 / 308 / 352 |
| Radio-node `/umaaOutgoingStatus` | 2,314 | 1,137 | 100 / 156 / 184 / 276 |
| Radio-node `/wire_status` | 1,076 | 538 | 72 / 76 / 604 / 604 |
| Radio-node `/umaaOutgoingCommand` | 4 | 2 | 100 / 100 / 100 / 100 |

Repeated writer/sequence pairs can result from multiple destinations or repeated delivery, not only reliability repair. Distinct physical sequences also do not deduplicate the same original logical sample forwarded by different gateways.

The radio-node capture's all-protocol rate is **4.966 Mbps**, but Wireshark identifies only **6,928 RTPS-associated frames totaling 1,874,996 bytes**, approximately **53.18 kbps** including those frames' headers. The directly identified application DATA portion is about **34.62 kbps**. Most captured traffic is other protocols/encapsulation/background, not operational DDS payload. Do not size the ACT application generator to 5 Mbps on the basis of that file.

### Seven-boat simulator: a stress profile, not the operational default

For the readable prefix:

- Total recorded frames: **276.87 MB in 104.359 s**, or **21.22 Mbps**.
- User DATA-bearing frames: **261.99 MB**, or **20.08 Mbps**, **94.63%** of total bytes.
- Identified user DATA transmissions: **696,977**, with **76,634 distinct physical-writer/sequence pairs**, approximately **9.10 transmissions per pair**.
- Named `/sim/bcast/geo_spatial_data_update` alone accounts for **517,395 DATA transmissions**, **55,809 distinct physical-writer/sequence pairs**, fixed **212-byte serialized samples**, and **187.30 MB of containing frames**.
- Named `/ops/bcast/platform_ins` accounts for **67,020 DATA transmissions**, **7,417 distinct physical-writer/sequence pairs**, fixed **356-byte serialized samples**, and **33.91 MB of containing frames**.
- Other active streams remain unmapped in the lightweight parser; their names should not be guessed from size alone.
- The capture contains **2,744 IP fragment packets**, and the damaged tail prevents any full-run conclusion.

Reassembled discovery-associated fields identify these 14 topic names:

```text
/ops/bcast/platform_ins
/sim/bcast/create_target
/sim/bcast/destroy_target
/sim/bcast/geo_spatial_data_creation
/sim/bcast/geo_spatial_data_drop
/sim/bcast/geo_spatial_data_update
/sim/bcast/register_rcs
/umaaOutgoingCommand
/umaaOutgoingStatus
rti/service/admin/command_reply
rti/service/admin/command_request
rti/service/monitoring/config
rti/service/monitoring/event
rti/service/monitoring/periodic
```

This supports a strong diagnosis of **simulator-data fan-out and interface/domain isolation problems** in the captured prefix. It does **not** establish that operational mission traffic requires 21 Mbps or that discovery alone caused the historical scaling failure above 7-8 platforms. CPU, buffering, neighbor tables, application load, and the corrupt/unread recording remainder remain outside that conclusion.

## 10. Recommended ACT replication profiles

These are proposed simulation inputs. They have not been implemented or executed by this characterization session.

### Shared topology and scale

| Parameter | Initial recommendation | Basis / qualification |
|---|---|---|
| Control stations | 1 CCS | Matches best mapped capture set. |
| Platforms | 2 baseline; 7 and 8 scale points; 16 exploratory | Two-platform and seven-boat evidence exists; 16 is an extrapolation. |
| WAN participants | 1 per gateway: 3 at baseline, 8 with 7 platforms | Clean architectural target, not the leaked historical count. |
| Local topic names | 100-150 per platform for a representative discovery load; optional 401-catalog case | Historical recorder union and delivered schema catalog; not all continuously publishing. |
| Local participants | Start at 40 per platform; sweep to 80 | Proposed load bracket. March platform LAN observed 72 domain-0 participant source prefixes during its window. |
| Direct WAN topics/types | 14 names / 14 distinct types | Delivered route selection and workbook. |
| Aggregate WAN channels | 2 UMAA wrapper topics initially; add wire command/status and navigation as separate variants | Do not simultaneously double-publish the same logical traffic on both direct and aggregate profiles unless testing that explicitly. |
| Contact instances | Start at 10 active; sweep 1/10/50 with churn | Proposed active-set sizes; 373 historical handles do not reveal concurrency. |
| Mission duration | 10 min quick calibration; 161 min endurance replay pattern | Ten minutes is proposed; 161 minutes matches the documented real mission interval. |

Keep local discovery load constant while comparing direct-topic versus aggregate WAN export. Otherwise a bandwidth reduction could simply reflect running fewer local applications.

### Profile A: direct-UMAA operational mission

| Traffic class | Suggested cadence | Serialized-size target | Delivery / scope |
|---|---|---|---|
| Pose, velocity, speed | 1 Hz each per platform | Respective E1 ranges: 144-332, 120-256, 60-104 bytes | Best-effort periodic/latest-state baseline; platform -> CCS. |
| Contact parent | About 0.4 Hz aggregate per platform | 100-120 bytes | Latest contact-set metadata; platform -> CCS. |
| Contact-set elements | About 0.8 updates/s aggregate per platform, across keys | Mostly small samples in the E1 range; explicit 4,212-byte worst-case tests | Event/update stream; include keyed add/update/dispose lifecycle as a proposed test. |
| Mission-plan/objective/waypoint changes | Bursts on command or plan change, not 1 Hz on every topic | Use E1 min/max; include 1,521 / 2,716 / 4,332 / 4,420-byte cases | Reliable where required; CCS -> addressed platform plus corresponding reports. |
| Control/ownership transfer | On transfer events | 56-648 bytes depending on type | Reliable event traffic; no background high-rate loop. |
| Optional squad/peer contacts | Off for default v10-like profile; enable 0.1 Hz and 1 Hz variants | Contact parent 100-120 bytes, plus elements only if selected | Platform-peer route isolated to a team; these rates are proposed, not measured peer ContactReport rates. |

Use the full fourteen-type inventory but leave infrequent topics idle between events. Test small, typical, and maximum-sized samples separately; do not send every type at its maximum continuously.

### Profile B: aggregated operational export

- Start with `/umaaOutgoingStatus` and `/umaaOutgoingCommand`, preserving distinct topic/type identities rather than one generic test topic.
- Calibrate status samples to the captured **96-276 byte** envelope, with approximately **156-160 byte median** depending on the selected historical run.
- Calibrate ordinary command samples to **140-352 bytes**, with **176-180 byte median** for the November/December profile; retain a separate larger-fragmented mission-command test.
- Use 1 Hz logical updates for the basic mission statuses before aggregation. Measure resulting wrapper frequency rather than assuming "one aggregate topic means one packet per second."
- Add `/wire_status` as a separate variant using its **72-604 byte** observed range. WireMessage's unbounded IDL sequence is not an unlimited operational bandwidth budget.
- Include navigation export only in a named variant. A **356-byte** sample is a useful captured target, but use discovered type identity to verify which stream is being generated.
- Keep multi-recipient fan-out, alternate-locator sends, and loss-repair repetitions as separately controlled factors.

**Calibration targets:** reproduce either the approximately **41 kbps November all-traffic envelope**, or the **376 kbps March as-captured cross-network envelope**, with matching topology, phase, channel set, and capture point. They are alternative historical scenarios, not interchangeable universal budgets.

### Profile C: simulator contamination / historical failure reproduction

Keep separate from Profiles A/B:

- Seven platforms plus CCS, simulator geospatial update streams, PlatformIns, and simulator lifecycle topics.
- Match **212-byte geospatial-update** and **356-byte PlatformIns** serialized sizes.
- Introduce historical discovery leakage and alternate-locator/fan-out behavior explicitly.
- Target the readable prefix's **21.22 Mbps mean**, **68.31 Mbps P95 one-second bin**, and **71.43 Mbps maximum one-second bin**, only when recreating that historical pathology.
- Compare against an otherwise identical run with simulator/admin traffic kept off the WAN.

Do not claim a full seven-boat trace replay: the source is damaged after 104.359 readable seconds.

### Profile D: discovery and DDIL experiment matrix

Run each workload under these proposed independent factors:

1. Cold start, simultaneous versus staggered gateway/app startup.
2. One late platform join, then one gateway restart.
3. WAN outage/reconnect with the same process, versus restart with a new participant GUID.
4. Type propagation enabled versus preloaded types with propagation suppressed on WAN.
5. Direct 14-type export versus aggregation; retain the same local catalog.
6. Clean WAN-only interface binding versus historical multi-interface announcements/sends.
7. Admin/monitoring and simulator domains isolated versus leaked.
8. 1/2/7/8-platform sweeps; collect bytes by phase and by gateway, not just whole-run averages.

For transport impairment, use **50, 100, 250, 500, and 1,000 kbps** link-rate trials, **100/200/1,500 ms** latency trials, and **0/1/5/10%** loss trials. These are **proposed test points**, informed by the historical latency/MTU notes and ACT's limited-bandwidth goals, not measured distributions. State whether delay is one-way and whether the rate limit is per direction, per link, or shared mesh capacity.

Run a **1268-byte IP-MTU variant** to reflect the historical constraint, plus the environment's normal-MTU control. Verify actual IP and DDS fragmentation. A transport payload limit must leave room for UDP/IP headers; do not treat an Ethernet frame size, IP MTU, and DDS transport message-size limit as interchangeable.

**A 50 kbps link cannot carry the March historical offered load without selection, downsampling, loss, or backlog.** Demonstrating graceful prioritization is a different claim from demonstrating identical throughput.

## 11. Measurement and acceptance plan

### Measure the right boundary

Capture separately at:

- A platform's local application domain.
- The platform's WAN egress/ingress interface.
- CCS WAN ingress/egress.
- At least one peer receiver for the squad scenario.

For EMANE, use a capture point that observes the traffic being submitted to or delivered from the emulated link. The OTA/control bridge is not automatically equivalent to user RF traffic.

Report:

- Offered sample bytes, UDP/IP bytes, recorded L2 bytes, and available RF airtime metrics separately.
- Participant discovery, endpoint discovery, builtin/control traffic, application DATA, fragmentation, and non-DDS background separately.
- Endpoint GUIDs, participant GUIDs, distinct names/types, active writers/readers, and keyed instances as different counts.
- Unique application message IDs or original writer identity where available; physical writer/sequence uniqueness is only a proxy.
- Per-direction bytes and per-destination fan-out. Do not count mirrored captures twice or call every repeated sequence a retransmission.
- Cold-start completion time, bytes until first useful command/status delivery, and rejoin cost.
- One-second median/P95/max and cumulative phase bytes; also retain the event timeline.
- Delivered command count, duplicate commands, command latency, lost latest-state updates, and queue/backlog growth.

### Proposed calibration gates

These tolerances are proposed engineering checks, not customer acceptance requirements:

| Experiment | Suggested check |
|---|---|
| March as-captured bridge profile | Match topology and 84.8-second steady observation; total mean 376.04 kbps within +/-15%, P95 426.38 kbps within +/-25%, and explain each directional flow. |
| Clean March-like topology | Exactly 3 WAN participant GUIDs for 1 CCS + 2 platforms; zero local/admin/simulator-domain announcements on WAN. Do not require the historical 376 kbps after removing historical traffic. |
| Direct-type fidelity | Instantiate the selected 14 distinct types; verify sample min/max cases and type-object inventory, not only payload padding length. |
| Operational mission | Pose/speed/velocity each approximately 1 Hz; sparse plan/waypoint changes; keyed contact updates and lifecycle behavior recorded. |
| Discovery A/B | Compare cumulative discovery bytes until convergence at the same population; a lower steady-state average alone is insufficient. |
| Fragmentation | Verify IP fragment and DDS DATA_FRAG counts under normal and 1268-byte-MTU paths, including maximum sample/type cases. |
| Seven-platform contamination | Show simulator channels and repeated-destination amplification explicitly; keep historical-prefix matching separate from operational pass/fail. |

No ACT runtime configurations were changed and no new mesh was launched for this report. These are workload specifications for a subsequent implementation/calibration session, not a claim that the harness already reproduces them.

## 12. Reproducibility and limitations

Analysis used **Wireshark/TShark 4.6.4**, `capinfos`, local pcap parsing, XML/workbook parsing, and read-only SQLite queries.

- Both RTPS header forms **`RTPS` and RTI `RTPX`** must be recognized. Ignoring RTPX undercounts the March participant-discovery traffic by 54 packets.
- The lightweight parser classifies frame bytes using RTPS writer entity IDs and measures DATA serialized regions. It does not reassemble IPv4 fragments or all vendor batching encodings. For fragmented captures its application/discovery splits and name attribution are incomplete; **capture-wide byte totals remain valid**.
- Wireshark's default IP reassembly was used independently for the reported discovery-associated topic/type/endpoint inventories. These are unions of decoded fields over each observation, not guarantees of concurrent live entities.
- The March cross-network result was independently checked with TShark: **9,128 RTPS frames / 3,984,576 bytes**, including **102 participant-discovery frames / 98,820 bytes** and **9,026 user DATA-bearing frames / 3,885,756 bytes**.
- A named topic's "containing frame bytes" include protocol overhead. They are not sample-byte totals and can overlap if several topics share a frame; the highlighted March/seven-boat DATA streams are counted as observed.
- No PCAP contains a verified complete mission/application event timeline aligned to every packet. Phase-specific causal claims beyond the provided November timeline remain uncertain.
- No measured RF bitrate, link retransmission/airtime accounting, or loss distribution was recovered. Historical bandwidth notes use inconsistent units; those notes were not used as measured link capacity.
- Original schemas and captures remain customer evidence. A public benchmark should use approved/sanitized schemas and payloads while retaining representative sizes and discovery complexity.

### Example local verification commands

Set these variables to the locally held source root and March capture directory (E3). Identifying paths are intentionally omitted:

```bash
: "${ROOT:?Set ROOT to the local source root}"
: "${MARCH_CAPTURE_DIR:?Set MARCH_CAPTURE_DIR to the local March capture directory}"

capinfos -M -c -s -u -a -e \
  "$MARCH_CAPTURE_DIR/cross_net.pcap"

# Pipe through stdin if tshark cannot open the shared-folder path directly.
cat "$MARCH_CAPTURE_DIR/cross_net.pcap" |
  tshark -r - -q -z \
  'io,stat,0,rtps,rtps.sm.wrEntityId==0x000100c2,rtps.sm.id==0x15 && rtps.sm.wrEntityId!=0x000100c2'

# Read only the validated prefix; do not alter or "repair" the source file.
head -c 289431560 \
  "$ROOT/Optimization/sim_pcap_2_6_2025/7-boats-pcap-tcpdump.pcap" |
  tshark -r - -q -z io,phs
```

Recorder query pattern, with the appropriate table name:

```sql
SELECT COUNT(*), COUNT(DISTINCT SampleInfo_instance_handle)
FROM "UMAA::SA::GlobalPoseStatus::GlobalPoseReport@32"
WHERE SampleInfo_valid_data = 1
  AND SampleInfo_reception_timestamp >= 1710100200000000000
  AND SampleInfo_reception_timestamp < 1710109860000000000;
```

Open the recorder database in read-only mode. These timestamps are nanoseconds since the Unix epoch and select the documented mission interval.

## 13. Outstanding evidence that would improve fidelity

1. An undamaged seven-platform recording, with a scenario timeline and exact runtime configuration revision.
2. A startup-through-mission capture on the actual WAN interface, rather than only Docker bridges or mirrored mixed-domain interfaces.
3. Endpoint discovery from the same March runtime to name all ten active physical writers confidently.
4. Exact compression/limited-bandwidth-plugin settings and runtime QoS dumps for each capture.
5. Platform-peer packet metadata tied to operational contacts or squad tasks, distinct from simulator geospatial/navigation synchronization.
6. Current intended mission topic selection, command chunking/reassembly rules, contact concurrency, and acceptable delivery/latency budgets.
7. Measured radio MTU, per-direction/shared capacity, loss, latency, multicast handling, and link-layer repair overhead.

Until those are available, the most defensible ACT workload is **a small operational WAN working set behind a large local DDS environment, plus separately labeled discovery, peer-coordination, and simulator-contamination tests**.
