# Participant discovery: RTI guidance and ACT questions

Reviewed: 2026-11-11. Documentation review only; no runtime or QoS changes.

## Sources and scope

- RTI Knowledge Base: [Participant Discovery Performance Characterization and
  Guidance: SPDP vs. SPDP2](https://community.rti.com/kb/participant-discovery-performance-characterization-and-guidance-spdp-vs-spdp2).
  The supplied URL ending in `spdp2-` returned the knowledge-base listing during
  this review; the link above returned the article.
- Linked [Connext 7.7 discovery benchmarks](https://community.rti.com/static/documentation/performance/benchmarks/srcDoc/pro/7.7/core/discovery/discovery_performance.html).
  These explicitly warn that results depend on hardware, architecture, QoS,
  types, and network infrastructure.

The findings below are attributed to RTI, not new ACT measurements. The article
describes tests at 10, 30, 60, 90, 150, and 210 participants, one topic and ten
endpoints per participant, one UDPv4 interface, default QoS with Observability
Framework disabled, and no Security Plugins. Its five-minute steady-state
comparison uses ten participants and one topic. Do not extrapolate its rates or
percentages to our radio mesh.

## What the guidance adds

| Area | RTI guidance | Consequence for ACT |
|---|---|---|
| Protocol structure | SPDP repeats full participant declarations; SPDP2 separates bootstrap DATA(Pb), reliable configuration DATA(Pc), and lightweight liveliness DATA(m). | Count these flows separately; a small liveliness message is not the whole discovery cost. |
| Example message sizes | Approximately 800 bytes for SPDP DATA(p), 212-240 for DATA(Pb), 729-764 for DATA(Pc), and 50 for DATA(m), under the article's configuration. | Not packet-size budgets. Verify serialized/submessage, UDP/IP, captured-frame, and radio-airtime boundaries before comparing sizes. |
| Startup | Default discovery completion times are comparable; the article calls differences below two seconds insignificant against its test variance. SPDP2 adds a configuration handshake. | Do not promise faster cold start. Measure participant discovery, endpoint readiness, and application delivery separately. |
| Steady state | SPDP2 generally reduces traffic by avoiding repeated full declarations to discovered peers. | Supports SPDP2 for bandwidth-limited WANs, but peer probing, SEDP, types, repairs, and application traffic remain separate costs. |
| Unicast versus multicast | The recommendation favors SPDP2 for no-multicast networks; multicast startup can favor SPDP in bytes sent. | Our unicast WAN is a relevant use case, not proof of a universal SPDP2 win or of optimal radio transport. |
| Configuration changes | SPDP2 reliably repairs participant configuration updates rather than waiting for the next periodic SPDP announcement. | Not an immediate-delivery guarantee during loss, reverse-path failure, or congestion. Team CFT and endpoint updates need their own measurements. |
| Compatibility | RTI describes SPDP2 as RTI-specific and not backward-compatible with versions lacking support; SPDP remains the multi-vendor choice. | Include tools and services in the compatibility inventory, not just routers. |
| Small MTU | RTI recommends SPDP2 when participant metadata would fragment and optimizations are unavailable. | Verify maximum configuration/type sizes and actual fragmentation; protocol choice alone does not establish a fragmentation-free WAN. |

## Relationship to our current design

- [D94](design-decisions.md)
  re-adopted SPDP2 after the corrected delayed-enable investigation. The old
  D87 failure and intermediate D92 failure rates are not current blockers.
  The [spike's correction notice](../../spikes/spdp2_delayed_enable/README.md)
  records 66/66 successful controlled cross-process repetitions and periodic
  announcements to unmatched peers. "Stops bootstrap once discovered" does not
  mean "never retries an unmatched configured peer."
- The current [QoS XML](../../harness_v2/qos/act_qos_profiles.xml) selects
  `SPDP2 | SEDP` in `wan_router_participant_base`, inherits the participant
  compact-discovery snippet, requests types, sets three initial announcements,
  and specifies a 30 s participant liveliness assertion period. These are
  source settings, not freshly introspected live effective QoS.
- [One WAN participant per node](single-wan-participant.md) and
  [two shared WAN topics](../two-topic-wan-migration-plan.md) reduce participant
  and endpoint population independently of discovery protocol. Neither removes
  all SEDP/type traffic. The single-participant comparison already used the same
  broad initial-peer probing in both variants; its savings are not an SPDP/SPDP2
  comparison.
- Team membership now uses endpoint partitions in legacy mode and envelope
  metadata/receive content filters in two-topic mode, not participant-partition
  retargeting. Historical fast SPDP2 participant-retarget results do not measure
  current team convergence.
- [C2-seeded bootstrap](c2-peer-bootstrap-report.md) remains a separately
  qualified address-distribution option, not transitive discovery or a relay.
  Configuring a locator is not proof of discovery or communication.
- DDS participant liveliness is distinct from application-level RouterHealth
  presence and passive RTT. The article does not establish support for putting
  our health/RTT metadata into DATA(m), nor does it justify removing the health
  branch or changing its timing policy.

## Questions to resolve with RTI

1. **Which numbers and denominators should we cite?** The summary says SPDP
   uses 60%/80% more steady-state bandwidth, while the detailed section says
   SPDP2 reduces it by about 59%/over 78%. These are not equivalent percentages.
   Multicast startup is summarized as 5-10% more for SPDP2, but the bytes-sent
   section reports 65% by default and about 40% after tuning. Are these different
   metrics/configurations or editorial inconsistencies? Request per-case raw
   sent/received bytes, measurement layers, and percentage formulas.
2. **What exact environment and XML produced each result?** Request release and
   patch versions, benchmark revision, peer descriptors/participant-ID ranges,
   effective discovery/configuration-channel QoS, and all optimization settings.
   How do those compare with Connext 7.7.0 and our compact-discovery profile?
   The linked benchmark page describes other workloads, including 50 topics and
   type-evolution tests; where are the exact SPDP/SPDP2 datasets behind this KB?
3. **What happens to unicast cost with unreachable peers?** How does SPDP2
   bootstrap traffic scale with locator count, participant-ID scanning, absent
   peers, and a peer returning after a long outage? Compare explicit descriptors,
   our broad probing baseline, and a C2 directory. Count destination copies.
4. **What bounds recovery under real loss and contention?** Which configuration
   channel heartbeat/NACK/retry/resource settings govern recovery? Request
   evidence under delay, loss, reverse-path interruption, simultaneous fleet
   startup, restart, and competing reliable Event traffic. "Reliable" is not a
   bounded recovery time, and participant repair is not endpoint/CFT convergence.
5. **How strong is the small-MTU claim?** Does avoiding IP fragmentation require
   configuration-channel transport limits or RTPS fragmentation settings? Request
   maximum-size metadata/security/type cases, actual MTU, fragment counts, and
   captures rather than inferring safety from the default example sizes.
6. **What is supported for health consolidation?** Is there a supported 7.7 API
   for dynamic metadata and receive timestamps on the periodic discovery/
   liveliness path without extra periodic traffic? Keep this separate product
   question from the bandwidth comparison; it is not answered by the article.

## Local measurement gate

Use the [comms characterization plan](../comms-characterization-plan.md) and
owned isolated harness lifecycle, not experiments against the retained live mesh.
Preserve production health policy and workload while varying discovery protocol
and peer configuration explicitly in external XML.

Capture before startup; record effective QoS, image/source hashes, actual
participant/endpoint counts, reachable and absent peers, and capture coverage.
Separate startup, five-minute steady state, join, restart, and blackout/recovery.
Report participant bootstrap/configuration/liveliness, SEDP/type traffic,
repair/control traffic, and application bytes separately, plus total radio cost
where measured. Retain both sent and received totals and unknown/mixed traffic.
Require expected identities/GUIDs, endpoint readiness, sequence-matched delivery,
and unchanged health behavior alongside bandwidth and recovery-time results.

No tuning, live cutover, or performance claim is authorized by this source review.
