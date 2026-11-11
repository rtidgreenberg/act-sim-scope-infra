# Instance churn and short-outage recovery test plan

Date: 2026-11-11. Status: initial isolated spike implemented and exercised;
complete qualification and acceptance-budget approval remain open.

Implementation, measured results, scope limits, and flow-control assessment:
[instance churn reconnect spike](../spikes/instance_churn_reconnect/README.md).

## 1. Objective and scope

Measure what reaches the destination when producers continue creating, updating,
and disposing keyed instances at **100 total operations/second**, the WAN is
unavailable for **2 seconds**, and then connectivity returns.

Answer:

- Do surviving instances converge to their latest value?
- Do previously received, now-disposed instances disappear, or remain as ghosts?
- Does recovery produce a disposal/data burst, and does it delay new commands?
- What differs between reliable Event and best-effort Status?

The baseline is a packet-outage test with the same processes and writers, not a
restart, late-joiner, or persistence test. Native ISC is not enabled.
Do not change production QoS or navigation safety behavior to make it pass.

The subsequent ContactReport/Event-only extension assumes transient-local
durability in experimental XML/private snapshots, without changing production
QoS. Healthy and outage cases pass natively and through actual routers; native
late-reader checks also pass while the original writer remains alive. These
are not writer/router restart tests. See the spike's
[transient-local measurements](../spikes/instance_churn_reconnect/README.md#contactreportevent-with-transient-local-durability-2026-11-11).

A subsequent **120-second outage with unchanged production Event QoS** fails
both native and actual-router convergence, leaving 160 disposed-contact ghosts.
Reliable-reader inactivity at about 30.5 seconds removes volatile history even
though participant discovery has a 300-second lease. A never-disposed stationary
contact remains ALIVE and application health recovers separately. No production
timeouts were changed. See the
[two-minute measurements and timeout distinctions](../spikes/instance_churn_reconnect/README.md#two-minute-outage-with-unchanged-production-qos-2026-11-11).

Related context: [LP-8](product-gaps.md#-lp-8--finite-liveliness-isc-recovery-conflicts-with-full-writer-side-content-filtering),
[lifecycle implementation](two-topic-native-lifecycle-plan.md), and
[migration evidence](two-topic-wan-migration-plan.md).

## 2. Deterministic workload

Use a monotonic-clock scheduler and unique run/key identifiers. A creation is the
first valid write, not a registration followed by a separately counted update.
Count all attempted operations, successful operations, blocking, and errors.
Do not resend application operations until observed: that would hide recovery
failures and exceed the requested rate.

### Main steady-state case

Use 160 active instances with an 8-second lifetime, staggered births at 20/sec:

| Operation | Schedule | Operations/sec |
|---|---|---:|
| Create | Initial write at birth | 20 |
| Update | At ages 2, 4, and 6 seconds | 60 |
| Dispose | At age 8 seconds | 20 |
| **Total** | | **100** |

After warmup, each 50-ms tick has one creation, three updates, and one disposal.
Each active instance is refreshed every 2 seconds until disposal. A 2-second
blackout spans 40 ticks: **40 creations, 120 updates, and 40 disposals**, not
200 distinct instances.

Prepopulate staggered cohorts and run at least 8 seconds before measurement.
Record actual operation timestamps and blackout boundaries; scheduling stalls
must be reported as workload failures, not compensated by an artificial burst.
Use a fixed payload and record its actual serialized size.

### Separate edge cases

- **Short-lived instances:** 50 creations/sec and 50 disposals/sec, lifetime
  1 second. Instances do not reach their first 2-second update. This preserves
  the 100/sec budget and exercises keys born and disposed entirely in the outage.
- **Same-key revival:** explicitly script write, dispose, and later write of the
  same key, alongside paced background work within the same 100/sec budget.
  Distinguish intentional revival from delayed old data incorrectly reviving a
  terminally disposed key.

Do not require a dispose callback for a key the destination never materialized.
For that key, the final assertion is absence of an ALIVE ghost.

## 3. Topology and existing harness reuse

### Stage A: native DDS behavior

Use the isolated-container pattern in
[run_checks.sh](../router/qualification/run_checks.sh) and the existing
[pending-backlog test](../router/qualification/test_two_topic_qualification.py).
Compare production `wan_event` and `wan_union_status` XML profiles without
retuning policies. Verify effective QoS, writer identities, matching, filtering,
resource limits, and inactivity/retry settings in the report.

Use valid production Event/Status envelopes and catalog-consistent keys.
ContactReport supports many contact IDs under one source for Event. For the
Status comparison, declare simulated source identities explicitly and keep the
payload source, envelope source, and catalog-selected instance ID consistent.
This models many logical sources, not a measured fleet of that many routers.

Run an unfiltered diagnostic control, then the production audience filter.
Do not add an unfiltered observer to the measured filtered writer: it can change
writer-side suppression and acknowledgement/retention behavior.

### Stage B: actual routed lifecycle

Reuse [the lifecycle route cases and mesh fixture](../router/test_e2e/test_two_topic_controller.py)
and [DDS probes](../router/test_e2e/util/dds_probe.py).

For Event, route ContactReport through:

`source LAN -> source router -> WAN -> destination router -> destination LAN`.

For Status, run a declared multi-source WAN injection into the destination
adapter at the full workload, plus a separate full-route check using genuine
configured node identities. Do not invent 160 producer source aliases behind
one router and claim 160 distinct routed Status instances: source normalization
can collapse those keys. Report the cardinality/rate of the smaller full-route
Status check separately; it is not equivalent to the full-workload comparison.

Use a dedicated new test module, with small shared helpers only where needed.
Proposed module: `router/test_e2e/test_instance_churn_reconnect.py`.
Do not modify the concurrently edited controller/filter tests until their owner
has finished and the source/build snapshot is coherent.

## 4. Outage, observations, and run sequence

1. Build current source into an owned private `/tmp` tree. Use an owned container
   network namespace, private IPC, UDPv4, and fixture-reserved domains <= 232.
   Never use host networking, the live router binary, or the user's live mesh.
2. Verify LAN/WAN route readiness and expected readers before warming up.
   Start observation before publishing. Warmup can retry setup samples;
   the measured workload cannot.
3. Measure 20 seconds of healthy steady-state operation.
4. Drop **both directions of WAN traffic only** for 2 seconds. Preserve LAN
   delivery and local test control. Continue producers and collectors.
5. Remove the impairment and measure at least 20 seconds of recovery while
   producers continue at 100/sec. Track backlog and time to convergence.
6. Stop producing at a recorded operation boundary, retain source truth, and
   observe a bounded drain period. Do not publish a repair snapshot or rewrite
   all survivors, which would mask missing recovery.
7. Compare destination state against source truth, finish reports, and clean up.

The existing qualification test applies netem to all loopback traffic. Reuse
its isolation guard and guaranteed cleanup, **not** that broad impairment for a
routed test. Add a WAN-domain-scoped drop rule in the private namespace, including
discovery and repair traffic for that domain. Verify the rule's packet counters,
unchanged LAN delivery, and zero new destination WAN reception during blackout.
Derive ports from the reserved domain and actual configured participant-port
range; do not assume one UDP port covers DDS.

Keep all processes alive and record matching/discovery changes. If a reader is
removed or marked inactive, report that explicitly rather than assuming ordinary
pending-reader repair still applies. Always remove owned rules in `finally`.

Maintain a test-only source ledger and destination state map keyed by canonical
identity. Correlate valid data using existing run/audit fields. Correlate invalid
notifications using native instance handles and recovered keys, not blank payloads.
Record source-LAN, source-WAN, destination-WAN, and destination-LAN observations
with local monotonic times. Same-host private tests avoid cross-host clock claims.

Measure:

- Actual operation rates/mix, API duration, errors, and scheduler lag.
- Source/destination instance states and latest audit sequence per key.
- Ghosts, missing survivors, stale values, duplicates, and unintended revival.
- Writer pending samples, reader rejection/loss, memory, retained instance
  counts, and whether queues drain or grow over repeated cycles.
- Recovery DATA/repair bytes and samples in 100-ms bins; valid-data versus
  lifecycle observations. Do not assume one callback equals one source operation.
- Last stale delivery and time to latest-state/deletion convergence.
- Fresh addressed-command/ACK latency through the same shared Event endpoints
  during catch-up, plus health freshness/presence on shared Status.

Run command probes at a recorded 1/sec, explicitly **in addition to** the 100/sec
instance workload. Report health/discovery overhead separately as well.

## 5. Matrix and acceptance

Start with one no-outage control and one 2-second-outage main case per policy.
Then run blackout offsets of 0, 0.5, 1, and 1.5 seconds relative to the update
cycle, three repetitions each. Add the separate short-lived and revival cases,
and a 10-cycle outage/recovery run to expose accumulating ghosts/resources.

Correctness requirements:

- Healthy controls deliver the intended state without unexplained errors.
- Reliable Event reaches correct terminal state and latest surviving values
  within the observation deadline, with no unintended revival. Record any
  inactivity, resource, or filtering boundary that prevents that result as a
  failure/limitation, not a skip or a silently weakened assertion.
- Best-effort Status reports exact missed-deletion/stale-state counts. A ghost
  is an application convergence failure even if expected from best-effort
  semantics; a test proving that limitation is not a passing recovery gate.
- All processes survive, LAN stays functional during the WAN outage, and owned
  impairments/resources are removed after success or failure.

**Proposed measurement budgets, requiring approval before qualification:** 10
seconds after restoration for Event terminal-state convergence and backlog drain;
5 seconds for a surviving periodic Status instance to deliver a post-restoration
value; 2 seconds for a fresh command/ACK round trip after restoration. Commands
sent during the blackout are measured separately from restoration. Set explicit
memory/instance-count and recovery-bandwidth ceilings after a healthy baseline;
do not invent fleet capacity from this small test.

These are proposed thresholds, not existing guarantees. A timeout is a recorded
failure with partial evidence, not permission to extend waits until green.

## 6. Deliverables and follow-up

Retain unique-run JUnit and compact JSON summaries under
`debug/test_reports/instance_churn_reconnect_<run_id>.*`, including source commit,
dirty-source manifest, private binary hash, effective XML QoS, workload counts,
impairment timestamps/counters, state discrepancies, and latency/backlog series.
Keep raw packet captures and scratch state in the owned `/tmp` workspace and
remove them after retaining the relevant compact evidence.

First implement and run the focused isolated cases; do not launch a live mesh
or browser suite for this protocol test. If isolated behavior is understood,
propose a separately authorized owned EMANE run at the current 2 Mbps MAC rate,
1 MHz channel, and disabled virtual-transport cap. It must not interrupt the
user's current mesh.

Restart/rediscovery, durable recovery, multiple-producer ownership, team changes,
and long outages are follow-up gates, not covered by a passing short-outage run.
If lost best-effort disposes leave ghosts, present the measured gap and options
for explicit reconciliation; do not silently add replay, TTLs, or change QoS.
