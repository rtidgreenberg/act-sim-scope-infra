# Instance churn across a short WAN outage

Experimental native Connext 7.7.0 test. Production code/QoS and the live mesh are
not modified. See [PLAN](PLAN.md) and the [detailed plan](../../docs/instance-churn-reconnect-test-plan.md).

## Run

```bash
python3 -m unittest discover -s spikes/instance_churn_reconnect -p 'test_workload.py'
python3 spikes/instance_churn_reconnect/run.py
python3 spikes/instance_churn_reconnect/run_routed.py
```

For ContactReport/Event only, assuming transient-local durability:

```bash
python3 spikes/instance_churn_reconnect/run.py --family Event --event-durability transient_local
python3 spikes/instance_churn_reconnect/run_routed.py --family Event --event-durability transient_local
```

`--event-durability production|transient_local` defaults to production policies.
The experimental XML changes only Event writer/reader durability; history,
resource limits, writer depth, and infinite liveliness remain inherited.
The routed runner applies those XML-authored durability kinds to `wan_event`
only in its disposable snapshot, preserving the required production alias.
Canonical production XML and the live mesh are unchanged.

Requires Docker access, `connext:7.7.0`, and the usual readable license directory.
The runner freezes its source/XML inputs and uses `--network none`, private IPC,
and container-only `NET_ADMIN`. UDP drops are restricted to the test WAN domain,
not the host network. All fixed QoS comes from XML; the spike reader profiles add
the production adapter's infinite instance retention.

Default workload: 160 live instances, 20 creations + 60 updates + 20 disposals
per second, updates every 2 seconds, 8-second lifetimes. Each family runs a
healthy control followed by a 2-second blackout with 20 seconds before/after.
Command/ACK and health probes are additional 1/sec streams.
Current Event runs also seed one stationary contact outside the measured
100/sec workload. It is never updated or disposed, distinguishing communication
loss from an actual lifecycle operation.

Options: `--family Event|Status|both`, `--pattern steady|short|revival`,
`--baseline SECONDS`, `--recovery SECONDS`, `--offset SECONDS`, `--control-only`,
`--outage-seconds SECONDS` (default 2), and `--unfiltered`. `short` uses 50 creations + 50 disposals/sec and 1-second
lifetimes; `revival` reuses a disposed physical key after an 8-second gap.

Unique reports are retained under `debug/test_reports/instance_churn_reconnect_<id>/`:
manifest/input hashes, per-key source/destination truth, native observations,
100-ms protocol/cache snapshots, command RTTs, health delivery ages, and JUnit.
A recovery failure returns nonzero even if it is an expected best-effort gap.
Writer cache occupancy and reliable unacknowledged counts are reported separately.
Byte counters are DDS protocol-status facts, not total RF/RTPS wire-byte counts.
The routed runner builds a fresh private router, temporarily installs its missing
pytest dependency, and uses the existing controller fixture without editing it.
Build/dependency preparation has bridge networking; DDS execution has no external
network. `--yaml-cpp-source PATH` can reuse an existing yaml-cpp 0.8.0 source cache.
Neither build writes to the persistent router binary. Router log tails are retained
for diagnosis, including failed setup cases.

Both runners resolve and record the system policy, forwarding `WAN_TIMEOUT_SEC`,
`WAN_HB_PERIOD_SEC`, and the derived `WAN_HB_RETRIES` into their private containers.
The updated defaults are 300 seconds, 3 seconds, and 100 retries. A custom example:

```bash
WAN_TIMEOUT_SEC=600 WAN_HB_PERIOD_SEC=3 python3 spikes/instance_churn_reconnect/run.py --family Event --outage-seconds 120 --recovery 40
```

The historical unchanged-policy failure below used the former 10-retry default.
For outages shorter than the final timeout, the routed test now requires STALE
to remain visible without DEAD or roster removal, followed by ALIVE recovery.
The production shared Status profile supplies that final timeout; assertions
do not infer it from a fixed 30-second countdown.
The transition-only trace carries the last preceding state into the checked
outage interval, so early DEAD/removal cannot pass by producing no later
transitions. STALE must be observed within the blackout; recovery at its end
is not counted as an in-blackout failure.
These review corrections have host regression coverage; the historical routed
measurements below were not rerun with this strengthened gate. No live mesh was
changed for the correction.

## Scope

The native stage uses production Event/Status types and policies with native DDS
endpoints. The routed stage adds full ContactReport LAN-WAN-LAN forwarding and
multi-source Status WAN-to-LAN receive-adapter coverage. Its original-type LAN
producer first primes dynamic route discovery outside measurement.
Status identities explicitly represent multiple logical sources; they do not
prove fleet-scale behavior or multi-source LAN ingress through a single router.
Native command probes use a small test command and a test receiver that emits
ACKs: they measure shared-endpoint delivery, not mission execution.
Native health observations measure delivery ages/gaps, not the production presence
state machine. No ISC reconciliation, restart persistence, application repair
snapshot, EMANE radio cap, or production flow-controller change is enabled.

## Two-minute outage with unchanged production QoS (2026-11-11)

```bash
python3 spikes/instance_churn_reconnect/run.py --family Event --event-durability production --outage-seconds 120 --recovery 40
python3 spikes/instance_churn_reconnect/run_routed.py --family Event --event-durability production --outage-seconds 120 --recovery 40
```

**Both healthy controls pass; both outage cases fail.** The actual-router
failure is **160 disposed contacts still ALIVE on the destination LAN**,
matching the native result. Neither stage has missing or stale surviving
contacts at its final comparison. The native case additionally loses **121
command ACKs** for commands written during the blackout; fresh post-restoration
commands succeed. These are retained failures, not skips or passing gates.

The test ran 18,000 measured contact operations (20 seconds before, 120 seconds
down, 40 seconds after). The native 120.012-second blackout covered 12,005
operations: 2,401 creates, 7,203 updates, and 2,401 disposals. The routed outage
lasted 120.008 seconds and covered the same counts. Scheduler lag stayed below
2 ms in both outage cases.

Effective production Event QoS was reliable/volatile/KEEP_ALL, asynchronous,
with infinite writer liveliness, **10 heartbeat retries at 3-second periods**,
and a separate **300-second participant discovery lease**.

Native instrumentation exposes the failure:

- At **30.50 seconds into the outage**, matched readers become inactive and the
  writer cache/unacknowledged count falls to zero. Discovery match count remains
  unchanged. Peak unacknowledged samples before abandonment: **3,318**.
- After reconnection, readers reactivate around 0.8-0.9 seconds after restoration
  and fresh traffic resumes, but the removed volatile history is not repaired.
- Final zero unacknowledged samples therefore does **not** establish successful
  delivery: the state oracle still finds 160 ghosts.

The stationary contact remains ALIVE without another write in **both** stages.
No NO_WRITERS contact observations occur. The dropout did not fabricate
disposes: source-generated disposes were lost after reliable-reader inactivity.

Actual-router application health is independent: the remote peers become STALE
about **1.92 seconds** into the outage, DEAD about **31.97 seconds** in, leave
the application health roster around **60 seconds**, and return ALIVE about
**0.92 seconds after restoration**. Roster pruning is not DDS participant
discovery expiry, and recovered health does not prove contact convergence.

The relevant retention knob is the writer's **reliable-reader inactivity retry
budget**, not its already-infinite liveliness lease. For a supported two-minute
disconnection, test a retry window longer than 120 seconds plus recovery margin;
100 retries at the existing 3-second period is a candidate for alignment with
the 300-second discovery window, **not a qualified fix**. Longer retention also
requires queue/memory budgets for roughly 12,000 contact operations plus other
Event traffic and recovery time. Do not extend health STALE/DEAD timers merely
to retain reliable history.

During that baseline experiment, no retry, discovery, health, durability,
production binary, or live mesh setting was changed. This is isolated loopback
evidence, not a radio-constrained gate.
The [RTI heartbeat/retry documentation](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/Controlling_Heartbeats_and_Retries.htm)
describes the independent reliable-reader inactivity mechanism.

Retained local evidence:

- Native: `debug/test_reports/instance_churn_reconnect_20261111T173233-569631/`.
- Routed: `debug/test_reports/instance_churn_routed_20261111T173233-314940/`.

## Two-minute outage with the 300-second policy (2026-11-11)

Source now derives 100 retries at 3-second heartbeat periods from
`WAN_TIMEOUT_SEC=300`. Static XML resolves a 300-second discovery lease and
two-topic final presence timeout since the last Status. Event is still
reliable/volatile/KEEP_ALL, asynchronous, with infinite liveliness. No resource
limits, durability, flow controllers, or priorities were retuned.

The actual-router healthy/outage tests **both passed** (251.38 seconds). The
120.012-second outage finished with zero ghosts, missing, stale, or unexpected
live contacts on the destination LAN. The stationary contact remained ALIVE.
Both peers became STALE about 2.03 seconds into the outage, remained in the
roster without becoming DEAD, and recovered ALIVE about 0.99 seconds after
restoration. This routed gate checks final convergence, not a command-latency
or 10-second recovery guarantee.

Native tests recovered all contacts and command ACKs, with no inactive readers
and a final reliable backlog of zero, but **the complete native performance
gate is not green**:

| Native outage run | First current-state convergence | Maximum fresh command RTT | Maximum workload scheduling lag | Result |
|---|---:|---:|---:|---|
| Initial, concurrent with other isolated validation | 11.89 s | 10.77 s | 95.95 ms | FAIL |
| Repeat without concurrent router builds/tests | 9.98 s | 8.49 s | 75.34 ms | FAIL |

Both unchanged healthy controls passed. The serial outage produced all 18,000
measured operations; 12,005 fell in the blackout. Its peak unacknowledged count
was **12,721**, peak process RSS **149,216 KiB**, with zero final discrepancies,
zero missing command ACKs, and no contact NO_WRITERS transitions. The stationary
contact remained ALIVE. These measurements do not establish an approved memory
or radio budget.

The existing proposed thresholds remain unchanged: 10-second convergence,
2-second fresh-command RTT, and one 50-ms workload tick of scheduling slack.
The repeat still fails command RTT and workload pacing; the initial run also
failed convergence time. Longer retention addresses the observed abandonment,
not head-of-line delay during repair. Recovery pacing, prioritization and
bounded admission/load remain next work, not a reason to increase timeouts again.

Native XML checks passed for default and non-divisible custom settings
(301-second timeout / 7-second heartbeat -> 43 retries). The real router health
regression passed with an explicitly shortened 32-second final timeout, covering
STALE/DEAD, repeated misses, any-Status recovery, disposal and crash/restart;
production defaults were not shortened. C++ timer tests cover the exact
299/300-second boundary and recovery resetting the reference timestamp.

Retained local evidence:

- Initial native: `debug/test_reports/instance_churn_reconnect_20261111T214811-319017/`.
- Serial native: `debug/test_reports/instance_churn_reconnect_20261111T215308-292992/`.
- Actual routers: `debug/test_reports/instance_churn_routed_20261111T214811-859210/`.
- Policy/configuration/health checks: `debug/test_reports/wan_policy_20261111T2148/`.

The retained EMANE mesh and persistent router binary were not restarted or
overwritten. Rebuild and deploy the router and XML together through the owned
lifecycle before claiming the running fleet uses this policy.

## ContactReport/Event with transient-local durability (2026-11-11)

**Native: 2/2 passed. Actual routers: 2/2 passed, 91.48 seconds.**
Both stages ran a healthy control and a 2-second bidirectional packet outage,
using the 100-operations/sec steady workload and schema version 3. Status churn
was excluded. Actual Event publications and subscriptions discovered in the
routed test reported transient-local durability.

The outage case executed 4,200 measured contact operations. Its measured
2.015-second native blackout covered 41 creates, 123 updates, and 41 disposals.
Native and full LAN-WAN-LAN destinations finished with **zero ghosts, missing,
stale, or unexpected live instances**. Native first observed convergence about
**1.48 seconds after restoration**; peak unacknowledged samples were **636**,
draining to **zero**. The largest fresh post-restoration native command RTT
was **0.36 seconds**, with no missing command ACKs. These timings are one
unconstrained-loopback measurement, not a radio latency guarantee.

After production stopped, the native test also created a new transient-local
ContactReport reader while the original writer remained alive:

| Native case | Late-reader convergence | Valid historical samples | Disposed keys observed |
|---|---:|---:|---:|
| Healthy | 3.58 seconds | 3,360 | 800 / 800 |
| Outage | 3.52 seconds | 3,520 | 840 / 840 |

Both late readers converged to the latest live state without ghosts. Their first
application observations included historical valid data already marked disposed
and invalid dispose notifications. This is observed application delivery, **not
a guarantee that all disposes globally precede data or commands**. The routed
tests did not restart a router or add a late-joining router.

The effective Event writer used reliable, asynchronous, KEEP_ALL,
transient-local policies with infinite liveliness; the API reported writer depth
as `0`. No writer-depth or resource-limit override was introduced. At the final
pre-late-join snapshot, the healthy/outage writer caches still held **4,200 /
4,402 samples**, respectively, despite zero unacknowledged samples. Retained
history is not an undrained reliable queue. Its long-duration memory/resource
budget and replay pacing remain unqualified.

Retained local evidence:

- Native: `debug/test_reports/instance_churn_reconnect_20261111T172114-367381/`.
- Routed: `debug/test_reports/instance_churn_routed_20261111T172114-454242/`.

This verifies reconnect and native late-join behavior with the original writer
alive, **not persistence across writer/router restart**. The earlier Status
lost-dispose finding below is not a ContactReport/Event failure. No flow
controller or prioritization experiment has been applied yet.

## Initial measurements (2026-11-11)

These are individual measurements, not fleet capacity or a statistical latency
guarantee. Full offset/repetition, ten-cycle, and radio-constrained gates remain.
The main native and routed results below use the current schema version 3.
Short-lived/revival exploratory results used version-2 headers with the same DDS
wire types; they establish native DDS behavior, not schema-3 router acceptance.

| Native test | Result |
|---|---|
| Steady healthy Event/Status controls | Both converge |
| Steady Event, 2-second outage | Converges about 1.98 seconds after restoration; peak 616 unacknowledged samples, draining to zero |
| Steady Status, 2-second outage | **Fails convergence: 41 ALIVE ghosts**, no missing or stale surviving values at the final check |
| Short-lived Event | Converges about 2.98 seconds after restoration; queue drains to zero |
| Short-lived Status | **Fails convergence: 50 ALIVE ghosts** |
| Same-key revival | Event converges; Status leaves one terminal ghost in the measured window |

The measured impairment lasts roughly 2.01-2.02 seconds including rule-removal
overhead. Scheduler/rule timing placed 205 operations in the main blackout
(41 creates, 123 updates, 41 disposes), rather than exactly 200. No receiver
samples were taken during the measured blackout in the final native main run.
This is not 205 unique instances.

For the latest main Event run, the largest fresh post-restoration command RTT was
about 0.30 seconds (an earlier main run measured 0.95 seconds). An earlier
short-lived run observed **2.35 seconds**, exceeding the
proposed 2-second budget; a repeat measured about 1.18 seconds. Keep both pieces
of evidence: a good repeat does not establish a guaranteed latency bound.

The latest main recovery burst reached about 804,000 DDS protocol-counter bytes/sec in
a 100-ms measurement interval. That is **not measured radio throughput** and
includes the counter's endpoint accounting. It shows why an unconstrained
loopback recovery must not be used to select a 2 Mbps radio admission limit.

Retained local evidence:

- Main native, schema 3: `debug/test_reports/instance_churn_reconnect_20261111T155826-894668/`.
- Actual adapter cases, schema 3: `debug/test_reports/instance_churn_routed_20261111T155826-631482/`.
- Earlier native main: `debug/test_reports/instance_churn_reconnect_20261111T154647-97475/`.
- Short-lived repeat: `debug/test_reports/instance_churn_reconnect_20261111T155242-939147/`.
- Earlier short-lived latency failure: `debug/test_reports/instance_churn_reconnect_20261111T154955-902268/`.
- Revival: `debug/test_reports/instance_churn_reconnect_20261111T154955-998795/`.

The first two routed runs passed both Event cases; both Status cases failed
during setup because injected envelopes used schema version 2 while the current
router requires version 3. Router tails in the second report confirmed the
explicit codec rejection. The spike now pins version 3. Those failed reports
are preserved under `debug/test_reports/instance_churn_routed_20261111T154647-747284/`
and `debug/test_reports/instance_churn_routed_20261111T155242-551304/`;
they are not evidence of Status outage behavior. The native measurements above
used the same wire types with version-2 headers and establish DDS behavior only;
the version-3 rerun is recorded above.

**Final routed result: 3 passed, 1 failed, 192.86 seconds.** Event healthy and
outage cases converge through both production adapters. The multi-source Status
receive-adapter healthy control passes; its outage case leaves the same **41
ALIVE ghosts on the destination LAN**. This confirms a real application
deletion-convergence gap, not just a native reader artifact. Neither Event case
has missing, stale, unexpected, or ghost instances at the final comparison.
The final native run also has 3 passing cases and one failing Status outage case.
Both failing gates remain ordinary failures in JUnit and return nonzero.

Nine deterministic workload/state-oracle/XML/duration tests pass. Python syntax checks pass.
The routed runner rebuilt only its private `router_main` target; no full C++,
mission, browser, fleet, or radio qualification is claimed. All owned containers
and transient workspaces were removed; the running mesh was left untouched.

## Flow control and prioritization assessment (before priority experiment)

**First separate correctness from scheduling.** A lost best-effort dispose needs
an explicit state/deletion reconciliation contract. A FlowController cannot
recover an operation that is no longer pending for delivery.

Recommended order:

1. Define the allowed stale/deleted-instance lifetime and reliable reconciliation
   semantics. Options include a source snapshot/generation protocol or retained
   terminal-state records. Any reliable lifecycle mechanism on Event would need
   an approved contract/schema change; current Status lifecycle cannot simply
   be copied into an arbitrary existing Event union branch. Do not silently add
   a third topic, replay stale values as fresh, or invent an expiry policy.
2. Measure the same load under a separate owned, radio-constrained 2 Mbps test.
   Include discovery, health, ACK/NACK, repairs, and realistic command sizes.
   Choose an aggregate recovery budget below measured usable capacity, not
   merely the nominal MAC rate.
3. Experiment with XML-authored asynchronous FlowController token budgets to
   pace Event repair/recovery and bound queues. Reserve capacity for health and
   fresh control traffic; measure starvation and blocking explicitly.
   Status is currently best effort and its publish mode must be checked before
   assuming it participates in an asynchronous controller.
4. Evaluate priority only with a deliberately pending contact/dispose backlog
   and a newly written addressed command. Verify actual destination delivery
   order and command ACK time, not just which packet is transmitted first.

Connext 7.7 supports per-sample publication priority through advanced write and
dispose parameters; the installed Python binding exposes `WriteParams.priority`.
But **a higher priority on one sample is not proof it can bypass older samples
in the same shared Event writer**. Priority inheritance can raise the priority
of a whole pending writer/channel queue while preserving its existing order.
The manual describes multi-channel writers for differing priorities within a
writer. Qualify unicast, sender-side CFTs, lifecycle routing, ordering and endpoint
counts before proposing that mechanism for this design.

Likewise, transport priority/network marking is not a branch-aware command
queue. Adding separate command/contact writers would change the agreed single
Event-writer ownership contract, even if the topic name stayed Event.

Do not coalesce or drop required Event operations to reduce backlog. Latest-state
replacement is a separate, explicitly approved policy for suitable Status data;
terminal state and command audit identity must remain correct.

Documentation cross-checks, not measured priority experiments:
[prioritized samples](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/Prioritized_DDS_Samples.htm),
[scheduling and in-order delivery](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/Flow_Controller_Scheduling_Policies.htm),
[multi-channel policy](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/MULTI_CHANNEL_Qos.htm).

## Asynchronous topic-priority experiment (2026-11-12)

This is an isolated native experiment, **not a production router change**.
It uses Connext's asynchronous publishing thread; adding a Python thread that
merely calls the same FIFO writer would not establish priority scheduling.
There is still one shared Event writer at each test endpoint. No new topic,
application replay/coalescing, durability change, or live deployment is added.

The map uses the envelope's `original_topic`, not the physical topic `Event`:

| Original topic | Default sample priority |
|---|---:|
| ControlCommand | 100 |
| PlatformCommandAck | 100 |
| ContactReport | 10 |

`WriteParams.priority` applies to data writes, ACKs and key-handle disposals.
Higher numbers mean higher priority. Override supported names with repeated
`--topic-priority NAME=INTEGER`; invalid names/values fail before launch.
Overrides are accepted by both entry points for `sample_priority`,
`priority_channels`, and all `priority_bands` variants, including
`priority_bands_repair` and `priority_bands_last1`. The supported modes share
one catalog in [priorities.py](priorities.py); `production` and `paced_fifo`
still reject overrides. CLI regressions check acceptance, rejection and worker
propagation. Fresh isolated healthy runs with `ControlCommand=200` in the repair
variant and `ContactReport=100` in the KEEP_LAST variant both passed. These are
CLI smoke checks, not new outage/recovery qualifications.
These three topics are the entire experimental channel catalog, **not** a
drop-in profile for every production Event branch.

```bash
# Freeze inputs and assert actual reader delivery order with a queued backlog.
python3 spikes/instance_churn_reconnect/run.py --ordering-only

# Healthy control plus the unchanged two-minute recovery workload.
python3 spikes/instance_churn_reconnect/run.py --family Event \
  --priority-mode priority_channels --outage-seconds 120 --recovery 40 \
  --topic-priority ControlCommand=100 --topic-priority PlatformCommandAck=100 \
  --topic-priority ContactReport=10
```

Modes:

- `production`: unchanged production asynchronous Event policy.
- `paced_fifo`: HPF controller but **all samples have equal priority 10**,
  controlling for pacing rather than priority.
- `sample_priority`: the same controller and topic priorities, one FIFO channel.
- `priority_channels`: the same controller and priorities, with one SQL-filtered
  channel per original topic inside the same writer, so each topic's priority
  can be configured independently.

Experimental fixed QoS lives in [qos.xml](qos.xml). The controller uses highest
priority first, with one 2,500-byte token replenished every 10 ms and at most one
token accumulated. That is a nominal **2 Mbps DDS token budget per participant**,
not an aggregate fleet/radio limit or RF/RTPS wire-byte measurement. Status keeps
its existing policy. Writer/channel priority `-1` inherits sample priority.
Effective controller settings and per-topic write/dispose counts are retained
in the native result. Production QoS XML is not changed by this experiment.

### Deterministic unsent-queue check

The XML-authored on-demand controller holds 400 ContactReport samples followed
by one command. No valid data may arrive before the first explicit trigger.
All 401 samples must arrive exactly once, with contact order preserved:

| Mode | Contact priority | Command priority | Command receive position |
|---|---:|---:|---:|
| Single channel | 10 | 1 | 401st |
| Single channel | 10 | 100 | 401st |
| Per-topic channels | 10 | 1 | 401st |
| Per-topic channels | 10 | 100 | **1st** |

All four native assertions pass. This proves an unsent command can overtake
contact traffic **with channels**, and that setting sample priority alone does
not reorder this writer's FIFO. It uses one local participant/writer/reader;
it does not establish priority during remote reliable-history repair.
Frozen-source evidence:
`debug/test_reports/instance_churn_reconnect_20261112T154301-305949/`.
The 24-second same-key dispose/revival control with independent priorities
200/150/5 for commands/ACKs/contacts also passes:
`debug/test_reports/instance_churn_reconnect_20261112T154527-311706/`.

### Two-minute outage comparison

Four serial runs use the same 300/3/100 timeout policy, Event reliability,
volatile durability, KEEP_ALL history, 100 contact operations/sec, 20-second
baseline, 120-second packet blackout and 40-second post-restoration workload.
The existing final drain allowance is unchanged. Every healthy control passes;
**every outage case fails the existing performance/final-state gate**.

| Mode | Worst fresh command RTT | Missing command ACKs at end | Contact convergence | Final unacknowledged |
|---|---:|---:|---|---:|
| Production, unpaced | 9.16 s | 0 | 10.99 s | 0 |
| Paced, equal priorities | No fresh ACK received | 50 | Not reached | 5,408 |
| Paced, sample priority only | No fresh ACK received | 48 | Not reached | 6,152 |
| Paced, per-topic channels | **2.39 s** | **0** | Not reached | 7,296 |

All 180 commands are acknowledged with channels, including all 39 strictly
post-restoration probes. Pacing without independent channels leaves commands
behind contacts; reporting only the RTTs of received commands would conceal
the missing replies. Channels improve responsiveness, but their 2.39-second
maximum still violates the unchanged 2-second target.

Production recovers every final contact value/state but misses the proposed
10-second convergence target. Each paced run ends with 159 ghost and 160
missing contact identities and a nonempty reliable queue. These failures are
retained, **not classified as successfully recovered**. No reader becomes
inactive in any run; the experiment stops with pending work, not demonstrated
history abandonment. Peak unacknowledged counts are 12,439 / 12,439 / 12,536 /
12,721 respectively. Producer scheduling lag is 39.45 / 2.17 / 2.82 / 2.10 ms,
all below the existing 50-ms limit.

The nominal token allowance is an upper bound, not guaranteed usable throughput:
tokens are consumed in whole units, and packet size/coalescing, repair traffic
and multiple destinations affect efficiency. The current bucket is therefore
**not a qualified 2 Mbps recovery budget**. Lower command delay does not prove
adequate contact capacity or freedom from starvation.

Retained frozen-source results, protocol/cache samples, truth/oracles and JUnit:

- Production: `debug/test_reports/instance_churn_reconnect_20261112T153056-107033/`
- Equal priority: `debug/test_reports/instance_churn_reconnect_20261112T153501-706656/`
- Sample priority: `debug/test_reports/instance_churn_reconnect_20261112T153917-683620/`
- Per-topic channels: `debug/test_reports/instance_churn_reconnect_20261112T154333-357406/`

Eleven host workload/map/XML tests and four native ordering assertions pass.
The unchanged production Status healthy control also passes. Native XML loading,
effective HPF/automatic priority, three channel filters and write/dispose/ACK
priority counts are verified with Connext 7.7.0. No new C++ implementation,
actual-router priority integration, EMANE/fleet workload or mission qualification
is claimed. Further work must qualify actual usable bandwidth, recovery time,
resource budgets, all Event branches, receiver filters and command/health
interference before production adoption. The persistent router and live mesh
remain untouched.

## Simplified priority bands (2026-11-12)

`--priority-mode priority_bands` replaces the per-topic XML channels with two
generic channels. The topic map remains in [priorities.py](priorities.py)
(`DEFAULT_PRIORITIES`), with runtime configuration supplied by repeated
`--topic-priority NAME=INTEGER` arguments. [NativeEndpoints.write](spike.py)
looks up `original_topic` and stamps `WriteParams.priority` on each write or
dispose. DDS, not application code, then maps that number to a channel:

```text
Topic-to-priority configuration -> WriteParams.priority -> DDS priority filter
ControlCommand=100                                      -> high
PlatformCommandAck=100                                  -> high
ContactReport=10                                        -> normal
```

The static writer QoS contains no topic names:

```xml
<multi_channel>
  <filter_name>DDSPRIFLTR</filter_name>
  <channels>
    <element>
      <filter_expression>@priority &gt;= 100</filter_expression>
      <priority>-1</priority>
    </element>
    <element>
      <filter_expression>@priority &lt; 100</filter_expression>
      <priority>-1</priority>
    </element>
  </channels>
</multi_channel>
```

The profile inherits the same asynchronous HPF controller and nominal token
budget as the per-topic experiment. `DDSPRIFLTR` was verified directly from
the installed 7.7.0 `DDS_PRIFILTER_NAME` symbol and with actual writer creation;
the MCP's suggested literal `PRIFILTER` was wrong and is recorded in the shared
correction log.

**This is two-band scheduling, not arbitrary sorting within each band.**
A priority-100 command overtakes 400 priority-10 contacts. A priority-99 command
does not: it shares their normal-band FIFO. Similarly a priority-101 command
does not overtake priority-100 contacts in the high-band FIFO.
Direct native priority 0 is accepted by the normal channel; application CLI
map values remain validated positive integers.

```bash
python3 spikes/instance_churn_reconnect/run.py --ordering-only
python3 spikes/instance_churn_reconnect/run.py --family Event \
  --priority-mode priority_bands --outage-seconds 120 --recovery 40
# Change channel selection through configuration, without changing XML:
python3 spikes/instance_churn_reconnect/run.py --family Event \
  --priority-mode priority_bands --pattern revival --control-only \
  --baseline 12 --recovery 12 --topic-priority ContactReport=100 \
  --topic-priority ControlCommand=10 --topic-priority PlatformCommandAck=10
```

Results:

- **12 host tests pass.**
- **12 native checks pass:** four previous scheduling controls, six band
  boundary/order checks (0/10/99/100/101), and two remote CFT
  write/dispose/revive checks at priorities 0 and 100. All ordering checks
  receive exactly 401 distinct samples and preserve contact order.
  Evidence: `debug/test_reports/instance_churn_reconnect_20261112T161716-754514/`.
- The 24-second revival control with contacts mapped to high and commands/ACKs
  mapped to normal passes without XML changes:
  `debug/test_reports/instance_churn_reconnect_20261112T161545-741669/`.
- The healthy control passes; the **120.007-second outage gate still fails**.
  All 180 commands are acknowledged, including 39 fresh probes, with worst
  fresh RTT **2.17 seconds** (target 2 seconds). Contact state does not converge;
  final discrepancies are 159 ghosts and 160 missing contacts, with **7,296**
  unacknowledged samples. Peak unacknowledged is 12,733; reader inactivity
  remains zero and producer lag is 3.21 ms. No budgets/thresholds were relaxed.
  Evidence: `debug/test_reports/instance_churn_reconnect_20261112T161524-332160/`.

The generic channel selection works; recovery capacity and latency remain
unqualified. The 2.17 versus prior 2.39 seconds is one run each, not a proven
performance advantage of bands. Moving a live instance between bands while
older operations are pending requires separate ordering/lifecycle qualification.
The map is startup configuration in this spike, not a production Config-tab
editor. Only the three workload topics are exercised, even though the generic
XML can classify other topics' priorities.

### Recovery pacing, disposal packing and batching (2026-11-12)

These historical measurements predate the corrected two-sided native
self-ignore below. Do not treat their local-reader overhead as a demonstrated
MultiChannel filtering defect or as the corrected production baseline.

The next isolated measurements allow up to 180 seconds of final drain to find
the actual convergence time. The original 10-second recovery, 2-second fresh
command RTT and 50-ms producer-lag acceptance budgets are unchanged.
All runs retain the same 20-second baseline, 120-second blackout and 40 seconds
of continued production after restoration. These are native DDS tests, not
EMANE/fleet qualification.

| Event mode | Current contact state converged | All ACKs/queue drained | Worst fresh command RTT | Valid contact writes observed |
|---|---:|---:|---:|---:|
| Coarse: 1 x 2500-byte token / 10 ms | 72.664 s | 72.819 s | 2.023 s | 14,561 / 14,561 |
| Fine: 5 x 1024-byte tokens / 20.48 ms | 71.828 s | 72.033 s | 2.565 s | 14,561 / 14,561 |
| Native batch, coarse controller | 64.627 s | 64.982 s | 15.846 s | 2,161 / 14,561 |

Both unbatched controllers have the same nominal 2 Mbps token budget.
Finer token granularity does not materially improve recovery in these runs.
All healthy controls pass and all three outage performance gates fail.
All 180 commands, including 39 fresh probes, are acknowledged. A larger,
50-token burst allowance has only a healthy control; no outage benefit is
established.

**Disposals are not logically aggregated or discarded by this application.**
The unbatched captures usually contain one dispose per DATA-bearing packet;
occasional repair packets pack two. Packet packing is not native Batch QoS
and does not replace several instance disposals with one operation.
Connext AI suggested token granularity, burst allowance, packet packing,
batching and repair-response limits as investigation paths. Native 7.7 tests
accept 1024-byte tokens and reject the tested smaller sizes; the measured
repair limit is 131072 bytes per NACK response, with unlimited configured
send-window limits. Those values do not establish a repair-window bottleneck.
See the [7.7 batch documentation](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/BATCH_Qos.htm)
for lifecycle metadata in batches; support does not establish correct complete
history for this particular endpoint/filter combination.

**Do not adopt the batch candidate.** It uses at most four samples/2048 data
bytes and a 10-ms flush, with source timestamp resolution zero. Native
scheduling and write/dispose/revive controls pass, but the long outage has
12,400 valid source writes not observed by the receiving application. Correct
final state alone previously concealed the gap because an already-disposed key
may legitimately be absent from that state. Its exact cause is not yet isolated.
The Event oracle now requires every valid source write sequence; invalid-data
lifecycle notifications and duplicate observations cannot satisfy this check.
Reliable terminal drain waits for this coverage as well as final state/ACKs.
Status remains best-effort and does not inherit this history requirement.

A separate 12-second outage confirms the strengthened check: unbatched delivery
observes all 1,441 valid writes; batching observes 745 and explicitly fails
for 696 missing writes after the final drain allowance. Both outage runs still
fail the original fresh-command RTT budget. Healthy controls pass.

**Investigate MultiChannel sender-side filtering before adoption.** Bounded
captures show source ContactReport DATA sent to both the intended receiver
and the local participant's ACK-only reader, where it is filtered out.
Both topic-SQL and priority-band MultiChannel show this extra ordinary-data
traffic; the ordinary non-MultiChannel writer mostly avoids it. Disposal
copies still occur with the ordinary writer. This is measured in this topology,
not a universal claim that MultiChannel disables filtering. Writer-side filter
resource limits are unlimited, not zero. A local `ignore_datawriter` attempt
does not reduce the two matched readers and is not a fix.

The next useful targets are sender-filter/fan-out qualification, usable recovery
capacity and a deliberate recovery/command/health budget. Heartbeat/repair tuning
needs separate measurement. More link capacity or a larger recovery allowance
is a policy choice, not a free scheduling optimization. Latest-state
reconciliation/tombstones would change Event retention semantics and require
explicit approval; do not silently apply KEEP_LAST or drop historical writes.

```bash
python3 spikes/instance_churn_reconnect/run.py --family Event \
  --priority-mode priority_bands --flow-profile fine --outage-seconds 120 \
  --recovery 40 --drain-seconds 180 --capture-wire
```

Evidence directories under `debug/test_reports/`:

- Coarse: `instance_churn_reconnect_20261112T163803-540316`.
- Fine: `instance_churn_reconnect_20261112T164244-961906`.
- Batch: `instance_churn_reconnect_20261112T164755-397862`.
- History-gate controls: `instance_churn_reconnect_20261112T165602-288076`
  (batch) and `instance_churn_reconnect_20261112T165602-985099` (unbatched).
- Sixteen native scheduling/lifecycle checks pass:
  `instance_churn_reconnect_20261112T165105-898524`.
- Retrospective coverage is saved separately, without changing frozen original
  evidence: `packing_comparison_20261112/write_history_analysis.json`.

Sixteen host unit tests pass. Raw captures/logs live separately under
`debug/pcap/instance_churn_reconnect/<timestamp-id>/`; new manifests record that
directory. Capture is bounded, private-network/domain-201 loopback only, and
counts source Event DATA-bearing traffic, not total RF or reliability traffic.
RTI DATA_SESSION and standard DATA/fragment submessages are recognized;
Status is excluded. Generic capture analysis rejects the unqualified batch
format instead of producing misleading counts. Earlier captures were moved
out of their report directories; the comparison directory retains a relocation
map. No production/router/live mesh changes are deployed.

### Correct native self-ignore on both sides (2026-11-12)

The earlier native spike omitted publication/subscription ignore. Production
already ignores its own WAN writers; the follow-up adds the complementary
WAN subscription ignore in normal reader creation and deferred Status startup.
The spike now applies both operations before sending and requires exactly one
remote reader per writer:

```cpp
dds::pub::ignore(wan, local_writer.instance_handle());
dds::sub::ignore(wan, local_reader.instance_handle());
```

These are participant-scoped local endpoint exclusions, not a Publisher QoS
or a filter on payload source fields. Remote participants remain connected.
Ignore calls require an enabled participant; startup-created Status endpoints
receive both ignores after participant enablement, before normal traffic.

Publication ignore alone stops local application delivery but leaves the local
reader a writer destination. It leaves 565 unacknowledged samples in the
four-second healthy test. Subscription ignore also removes that local
destination. Source Event DATA-bearing UDP bytes fall from **331656 to 166292**
in comparable healthy controls, with one remote match and zero unacknowledged
samples. This explains the previously reported extra local traffic; it is not
evidence of a general MultiChannel CFT failure.

The corrected, otherwise unchanged two-minute outage receives all **14,561**
valid contact writes and all **180** command ACKs. Worst RTT among the 39 fresh
post-restoration commands is **13.645 ms**, compared with 2.023 seconds in the
earlier coarse run. Contacts converge in **60.342 seconds**, and the final
queue drains at **60.498 seconds** after restoration. The original 10-second
contact recovery gate still fails; do not call performance qualification complete.
These are isolated native measurements, not live mesh results.

Batching remains rejected: a corrected 12-second outage still observes only
721 of 1,441 valid writes, failing explicitly for 720 missing writes and
fresh-command RTT above 2 seconds. Correcting self-ignore does not resolve
that history problem.

Validation: 16 host tests, 19 native scheduling/lifecycle/self-ignore checks,
9 freshly built C++ tests, 11 production adapter DDS cases and one production
controller all-application-case test pass. The latter exercises deferred
startup; this is not a full-suite health timing or fleet gate. Production
binaries were compiled into disposable storage, never over the running router.
Live mesh, production QoS and deployment remain unchanged.

Evidence under `debug/test_reports/`:

- Publication-only control: `instance_churn_reconnect_20261112T174221-404869`.
- Two-sided control: `instance_churn_reconnect_20261112T174413-843863`.
- Corrected two-minute outage: `instance_churn_reconnect_20261112T174616-11423`.
- Nineteen native checks: `instance_churn_reconnect_20261112T174617-718271`.
- Corrected batch rejection: `instance_churn_reconnect_20261112T175129-305918`.
- Production builds/C++/DDS evidence: `wan_self_ignore_20261112`.

### Larger repair-response limit (2026-11-12)

`priority_bands_repair` inherits the corrected priority-band configuration and
changes only the XML writer protocol `max_bytes_per_nack_response` from the
native default 131072 bytes (128 KiB) to 524288 bytes (512 KiB). Two-sided
self-ignore, history, heartbeat policy, priority map, nominal 2 Mbps controller
and acceptance thresholds are unchanged. This limit bounds a repair response,
not the total backlog or the link budget.

| Response limit | Contact convergence | Terminal drain | Worst fresh command RTT |
|---|---:|---:|---:|
| 128 KiB corrected baseline | 60.342 s | 60.498 s | 13.645 ms |
| 512 KiB candidate | 61.580 s | 61.580 s | 2315.419 ms |

Both observe all 14,561 valid contact writes and all 180 command ACKs,
including 39 fresh probes. Healthy controls pass. Both fail the 10-second
contact recovery gate; the candidate additionally fails the 2-second fresh RTT
gate. Do not adopt the increase based on this evidence. These are one run each,
not proof that the parameter caused the RTT difference.

Offline captures show the same request-bitmap distribution in frames containing
normal-channel ACKNACKs: 62 full 256-bit request bitmaps and one full 128-bit
bitmap in each run, plus empty acknowledgments. Enlarging the response limit
does not enlarge these requests. This is consistent with the original cap not
being the binding limit, but is not a direct measurement of bytes in each repair
response. Measure request-window/cadence and pacing next rather than simply
increasing the response cap again.

```bash
python3 spikes/instance_churn_reconnect/run.py --family Event \
  --priority-mode priority_bands_repair --baseline 20 --recovery 40 \
  --outage-seconds 120 --drain-seconds 180 --capture-wire
```

Seventeen host tests and 24 native controls pass. The latter include the
candidate's native XML/QoS creation, 99/100 priority boundary, remote lifecycle
and self-ignore checks; effective QoS confirms 524288 bytes. Evidence:
`debug/test_reports/instance_churn_reconnect_20261112T183400-412324/`
(healthy/outage), `instance_churn_reconnect_20261112T183400-973547/`
(native controls), and `repair_limit_comparison_20261112/comparison.json`
(derived comparison, original reports unchanged).

History and scheduling remain separate decisions: Event still uses KEEP_ALL,
not latest-state-only replay. On a writer with KEEP_LAST(1), disposal metadata
counts toward that instance's depth and can replace queued data; a subsequent
write can replace the disposal. Reader-side invalid-data notifications do not
count toward reader history depth. History is writer-wide, not a separate
per-disposal or per-channel setting. Do not change the shared command writer
to KEEP_LAST to optimize contacts.

Sources: [7.7 history semantics](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/HISTORY_QosPolicy.htm)
and [7.7 reliable writer protocol](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/api/connext_dds/api_c/structDDS__RtpsReliableWriterProtocol__t.html).
The MCP repair follow-up was unavailable; installed SDK, native effective QoS,
official documentation and packet captures are the evidence. No production
QoS or live mesh changes are deployed.

### KEEP_LAST(1) latest-state experiment (2026-11-12)

`priority_bands_last1` inherits the 512 KiB repair profile and changes only
writer history to KEEP_LAST, depth 1. This applies to both Event writers
(outgoing contacts/commands and return ACKs), not the KEEP_ALL readers.
The same priorities, nominal 2 Mbps controller, two-sided self-ignore,
20-second baseline, 120-second outage and 40-second continued production remain.
Native effective QoS records and verifies writer/ACK history kind and depth.
Production QoS and the running mesh are not changed.

The repeat with receipt-triggered convergence measurement has:

| Measurement | Result |
|---|---:|
| Current contact-state convergence after restoration | 12.929 s |
| Worst fresh command RTT, 39 probes | 13.726 ms |
| Commands acknowledged | 60 / 180 |
| Valid contact writes observed | 4,489 / 14,561 |
| Missing valid contact writes | 10,072 |
| Peak unacknowledged samples | 2,628 |
| Final unacknowledged samples | 0 |

Final contact state is correct: no ghosts, missing current contacts, stale
values or unexpected live instances. However, **120 command ACKs are missing**
(commands 21 through 140), as are overwritten intermediate contact writes.
All commands for a source share a DDS instance key in this workload, so
KEEP_LAST(1) cannot preserve every queued command. It is not safe to substitute
this profile for the shared required-event writer.

The healthy control passes; the outage still fails the original 10-second
state-recovery, complete required-write coverage and command-ACK checks.
Do not treat correct final state or an empty reliable queue as successful
required-event delivery. Compared with the earlier 512 KiB KEEP_ALL run,
recovery is much shorter, but this changes retention semantics and the timing
probe below. These are native matched-reader reconnect tests, not late-joiner,
restart or durable deletion guarantees.

The first KEEP_LAST run retained the full 180-second final observation allowance.
It still had 120 missing ACKs and 10,588 unobserved valid writes. The periodic
100-ms convergence probe reported 39.985 seconds: it sampled near producer
write ticks and missed intervening caught-up states. Convergence is now also
checked after processing newly received state, without changing its state oracle
or acceptance thresholds. Future reports label the probe
`receipt_with_periodic_fallback`.

The refined repeat uses a 10-second terminal allowance because the long run
already established irrecoverable missing operations. Its 12.929-second recovery
is measured while production continues, before either terminal allowance starts.
Keep both reports; do not silently relabel the old timing as receipt-triggered,
or attribute all differences between separate runs to the probe alone.

Native queued-operation controls verify, for a seeded instance:

- `write(1), write(2), write(3)` yields only valid value 3.
- `write(1), write(2), dispose` yields only the disposed notification.
- `dispose, write(3)` yields only valid value 3, not the intervening disposal.

Eighteen host tests and 32 native priority/lifecycle/self-ignore/replacement
checks pass. An initial control failure was residual tokens after seeding the
on-demand controller; the corrected control consumes that allowance before
queuing replacements and asserts that target data cannot leak before triggering.
This was a test-precondition issue, not a Connext history failure.

```bash
python3 spikes/instance_churn_reconnect/run.py --family Event \
  --priority-mode priority_bands_last1 --baseline 20 --recovery 40 \
  --outage-seconds 120 --drain-seconds 10 --capture-wire
```

Evidence under `debug/test_reports/`:

- Original long observation: `instance_churn_reconnect_20261112T184519-917160`.
- Receipt-timed repeat: `instance_churn_reconnect_20261112T185417-434098`.
- Thirty-two native controls: `instance_churn_reconnect_20261112T184914-832988`.
- Method-labelled derived comparison:
  `keep_last_one_comparison_20261112/comparison.json`.

**Status is unchanged and outside this Event controller.** There is no global
Event-versus-Status priority ordering: neither a claim that Status is below
both Event bands nor a health-isolation guarantee. Applying such a policy would
require an explicitly shared scheduling/capacity design that accounts for
RouterHealth. No production/router/live mesh changes are deployed.
