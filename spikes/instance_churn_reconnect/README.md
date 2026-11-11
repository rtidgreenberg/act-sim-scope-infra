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

No retry, discovery, health, durability, production binary, or live mesh setting
was changed. This is isolated loopback evidence, not a radio-constrained gate.
The [RTI heartbeat/retry documentation](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/Controlling_Heartbeats_and_Retries.htm)
describes the independent reliable-reader inactivity mechanism.

Retained local evidence:

- Native: `debug/test_reports/instance_churn_reconnect_20261111T173233-569631/`.
- Routed: `debug/test_reports/instance_churn_routed_20261111T173233-314940/`.

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

## Flow control and prioritization assessment

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
