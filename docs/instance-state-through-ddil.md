# Instance State Through DDIL

Empirical findings, 2026-11-19. This document distinguishes delivery of a
contact's disposed state, cleanup of a writer's retained state, and removal
from a receiving application's cache. They are different events.

**Record consolidated 2026-11-20.** The measurements below are the recorded
results of those experiments, not a new test run. The user-requested debug
cleanup on 2026-11-20 deleted the local JSON reports, archived logs and packet
captures referenced here. Their historical paths are retained for
reproducibility; those files are no longer available. Probe sources and the
findings in this document were preserved. The mesh was stopped for cleanup.

Presentation: [DDIL recovery slides](slides/ddil-recovery.md),
[PDF](slides/ddil-recovery.pdf), [PowerPoint](slides/ddil-recovery.pptx).

## Practical conclusion

A writer cleanup delay is a **minimum retention interval**, not a deadline
that discards an unacknowledged disposal. With the same reliable remote reader
still active, the tested WAN writer retained disposal state while waiting for
acknowledgment, including beyond a ten-second cleanup interval.

That protection is conditional on the reader's continued membership/activity.
It is not a persistent deletion journal or an end-to-end application
acknowledgment. Reader retirement, restart, pressure-driven replacement, and
expiry of unread reader samples require separate tests and policies.

**Two loss boundaries were reproduced:** a disposal was not recovered after
reliable-reader inactivity/C2 restart, and a ten-second reader sample-expiry
policy discarded an unread disposal under pressure.

No lifecycle policy or binary was deployed by these experiments. The retained
six-platform mesh was not used as a test environment.

## What actually runs

The forwarding experiments use this complete path:

```text
Platform application ContactReport writer, LAN domain 211
  -> deployed Routing Service processor
  -> shared native State writer, WAN domain 212
  -> delayed/interrupted UDP link
  -> C2 Routing Service processor
  -> C2 LAN ContactReport writer, domain 213
  -> native application reader and an application-state record
```

These are real Routing Service processes, not a Python forwarding model.
Application data and invalid DDS disposed notifications pass through the
current processor. The `ContactReport` payload has eighteen checked fields;
the receive-side authoritative `source` becomes `Platform_30`, while the
original audit/source-node fields are preserved. Disposal keys are checked.

The tests load the deployed persistent binaries without rebuilding them:

| Component | SHA-256 |
|---|---|
| Processor | `022941caaef2604dd41657da494b9144f17e5bbb7fd1a6c7b816e4dfd007f94b` |
| Gateway helper | `406e734e0f713443fd4dd2a16bcbd6e4665d9eae583660211abb9fd1820ab6bf` |

Connext and Routing Service are 7.7.0 in the supported container image.
External production-source QoS is combined with isolated experimental
overrides. This includes the source-only pre-emptive ACKNACK suppression
setting; these runs are not a claim that the live mesh has reloaded that QoS.
Containers have private network and IPC namespaces, UDPv4 transport, and
disposable working files under container `/tmp`. They cannot discover the
live mesh. Separate resource-pressure controls use native DDS endpoints
directly; those controls are **not** routed fleet/load qualification.

### Link and measurement controls

- Linux `tc netem` delays only domain 212 UDP destination ports 60400-60649.
  The LAN legs are not delayed.
- Three seconds of delay applies in **each direction**, including discovery,
  acknowledgments and repair traffic. A round trip therefore includes about
  six seconds of queueing.
- Outages drop 100% of WAN packets. Source disposal must occur inside the
  recorded monotonic-clock loss window.
- An independent UDP probe measured 3.000054-3.000089 seconds in the WAN queue;
  the outside-WAN control measured less than 0.028 milliseconds.
- A test-only library samples the actual existing WAN State writer's native
  cache counters every 50 milliseconds. It creates no DDS participant or
  endpoint. It also reads back effective writer cleanup/durability QoS.
- Writer cleanup means the observed disposed instance disappears from that
  writer's counters. It does not mean every internal allocation, gateway cache
  or application object has been freed.
- Notification timing is source `dispose_instance()` to C2's native disposed
  observation. The final receiving reader's own instance/sample retention is
  independently controlled.

## Writer cleanup: what starts it and what allows it

For the tested VOLATILE reliable writer, cleanup requires both:

1. The disposed-instance retention interval has elapsed, using the configured
   writer lifecycle timestamp basis.
2. The relevant matching, active reliable readers no longer require the
   retained disposition for repair.

Actual removal can occur later because of protocol and cleanup scheduling.
Acknowledgment at this WAN hop means the **C2 gateway reader received the
state**, not that the final application has processed it or deleted its object.
Every forwarding hop has its own writer and reader.

The ten-second interval is not a guaranteed minimum outage tolerance after a
peer has been removed. Likewise, setting it to zero does not authorize removal
of an outstanding disposal that an active reliable reader still needs.

## Earlier delay and short-outage comparison

All eight routed trials passed: two repetitions for each row below. Times are
seconds since the original platform application disposal, not since link
restoration.

| WAN writer cleanup | Link | C2 disposed observation | WAN writer cleanup |
|---|---|---|---|
| ZERO | 3 s delay | 3.003 / 3.012 | 7.506 / 8.057 |
| ZERO | 3 s delay + 2 s outage | 11.469 / 13.564 | 14.519 / 16.874 |
| 10 s | 3 s delay | 3.005 / 3.009 | 10.312 / 10.412 |
| 10 s | 3 s delay + 2 s outage | 11.361 / 14.125 | 14.770 / 17.274 |

Every cleanup observation had zero unacknowledged samples. In the ten-second
outage cases, cleanup occurred **after ten seconds**, rather than dropping the
unacknowledged disposal when the timer expired. The measured loss intervals
were 2.002-2.003 seconds. These timings are observations, not latency guarantees.

Historical evidence, deleted during the 2026-11-20 cleanup:
`debug/test_reports/contact_disposal_delayed_20261119/report.json`
and the accompanying `inputs-and-logs.tar.gz`.

## Extended routed WAN matrix: executed results

All five scenarios completed with native QoS readback confirming a **ten-second
WAN writer disposed-instance delay and VOLATILE durability**. Three established
the expected final-state behavior. Two reproduced recovery limitations; they
are explicitly labeled `LIMITATION_REPRODUCED`, not successful deletion recovery.

Every case retains three seconds of one-way WAN delay. Times below are seconds
since the **first source disposal**, including blackout and repair time.

| Scenario | Blackout | Isolated WAN timeout setting | C2 result | WAN disposed-state cleanup | Qualification |
|---|---:|---:|---|---:|---|
| Same reliable reader remains active | 30 s | 300 s | DISPOSED at 40.785 s | 44.073 s | PASS |
| Reliable reader becomes inactive | 30 s | 12 s | **Original ALIVE contact remains** through 75 s | 10.609 s, during outage | Recovery limitation |
| C2 gateway and application reader restart during blackout | 30 s | 12 s | **NO_DATA; no target disposal replayed** through 75 s | 14.617 s, during outage | Recovery limitation |
| Dispose, then revive same key | 2 s | 300 s | ALIVE_REVIVED at 13.912 s | Not a cleanup measurement | PASS |
| Dispose, revive, then dispose same key | 2 s | 300 s | DISPOSED at 13.332 s | Not a cleanup measurement | PASS |

### Outage longer than the cleanup interval

The thirty-second outage exceeds the writer's ten-second retention interval.
The existing reliable reader stayed active and matched throughout that outage.
The writer did not remove the disposed state while the link was down. C2
observed it after restoration, and writer cleanup followed later at 44.073 s.
This directly tests a delayed disposal surviving beyond the retention timer.

### Reliable-reader inactivity, not endpoint removal

The accelerated boundary test sets the WAN timeout to twelve seconds and
participant assertion period to three seconds, only inside the isolated
configuration. The default thirty-second assertion period is inconsistent
with a twelve-second participant lease, so it cannot be left unchanged.

Native writer status showed **active reliable readers falling to zero while
matched readers remained one**. Actual endpoint/participant removal was not
observed and is not claimed. Inactivity was sufficient for the writer to
release the pending disposed state at 10.609 s.

After restoration, the reader became active again and a fresh, distinct-key
canary reached C2 at 41.065 s. The target was never republished. Its disposal
did not return; the application still held its original alive contact at
the end of the seventy-five-second observation.

This is the important failure mode: **a matched endpoint count is not proof
that a reliable reader still protects a pending deletion**. Zero outstanding
samples after reader inactivity is also not proof of receipt by that reader.

### C2 restart

The isolated C2 Routing Service stopped cleanly about 6.6 s into the blackout;
its replacement launched about 16.6 s into it. The application reader was also
recreated. The source WAN writer's disposed state disappeared at 14.617 s,
before the thirty-second blackout ended.

A fresh canary reached the restarted C2 at 59.107 s, proving the new path
worked. No target data or disposal was reconstructed before observation ended
at 75 s. `NO_DATA` is not a recovered deletion: the reader's old in-memory
contents were cleared by restart. Persisted application state was not modeled.

### Same-key ordering and State coalescing

Revival occurs 250 ms after disposal. The terminal-dispose variant disposes
again another 250 ms later, all while the two-second blackout is active.

The revival case delivered the complete updated payload and finished alive;
the terminal-dispose case finished disposed. No late transition regressed
the required terminal state during the checked observation window
(32 seconds total, with a final fifteen-second window for the terminal-dispose
variant).

Intermediate states were coalesced: C2 did not observe the first disposal in
the revival case, nor the intermediate revival in the terminal-dispose case.
This is correct latest-state behavior, **not delivery of every lifecycle event**.

### Evidence and acceptance

- Historical `debug/test_reports/instance_state_ddil_20261119/report.json`:
  top-level `COMPLETE_WITH_LIMITATIONS`; three PASS final-state cases and two
  reproduced recovery limitations.
- Actual merged XML, cache timelines and native logs were archived in that
  same directory as
  `inputs-and-logs-long_outage_peer_loss_restart_revive_revive_dispose.tar.gz`.
- Historical `debug/test_reports/instance_state_ddil_20261119/live_postflight.json`:
  six healthy platforms/peers, clean impairment controller.

All owned gateway/probe processes exited cleanly, observer shutdown was
confirmed before DDS teardown, private DDS shared-memory artifacts were absent,
and deployed binary hashes were unchanged. The retained mesh was not restarted
or reconfigured by the experiments. These local evidence files were subsequently
deleted and the mesh stopped during the 2026-11-20 cleanup.

## Reader cleanup is a separate policy

The original routed controls establish:

- Default reader disposed-instance and disposed-sample delays are INFINITE.
  The disposed instance remained present throughout the 5.5-second
  post-loan-return observation window.
- With ZERO reader disposed-instance delay, removal waits until the invalid
  notification is taken and its loan returned. With minimum-state retention
  enabled, a detached state entry remains; disabling that option removes it.
- The installed 7.7 runtime rejects a two-second
  `autopurge_disposed_instances_delay`: this setting accepts ZERO or INFINITE.
  It cannot provide an arbitrary ten-second reader-instance deletion timer.

These controls do not establish that an application automatically erases its
own dictionary entry when DDS changes state. The application must consume and
handle that state.

Historical evidence, subsequently deleted:
`debug/test_reports/contact_disposal_20261119/report.json`.

## How the UMAA alternative differs

The [pinned UMAA profile](https://github.com/rticommunity/rticonnextdds-usecases-umaa/blob/ecb923646e7f3fe569b773c3946444e793d8a8be/qos/umaa_qos_lib.xml#L143)
combines several independent choices:

| Choice | Purpose and limitation |
|---|---|
| Writer disposed-instance delay ZERO | Reclaim once reliable-reader obligations allow it; not unconditional immediate deletion. |
| Reader disposed-instance delay INFINITE | Do not automatically delete the instance merely because a timer elapsed. |
| Reader disposed-sample delay 10 s | Permit lazy sample expiry under pressure; not a precise ten-second instance timer. |
| Reader maximum 1,000 instances | Bound attached instance capacity. |
| FULLY_PROCESSED replacement for disposed/no-writers | Reuse eligible old slots when a new instance needs capacity. |
| NO_INSTANCE replacement for alive instances | Do not evict still-alive contacts simply to admit a different key. |

For normal reliable protocol acknowledgments, the installed manual defines
FULLY_PROCESSED as **read or taken, with the loan returned**. Taking is not the
only qualifying operation. Outstanding loans and samples not yet processed
are separate barriers. Explicit application acknowledgment mode adds further
requirements and is not qualified here.

Pressure-driven replacement and detached minimum-state retention must not be
mistaken for an application deletion event. A finite disposed-sample delay also
allows unread data to expire: it is unsuitable as an unconditional guarantee
that a slow application will receive every disposal.

The profile is therefore a useful starting point for bounded consumer caches,
not a drop-in persistent deletion/restart solution. The tests below use smaller
limits to force the same replacement decisions; they do not qualify the
entire UMAA library or its original 1,000-instance operating scale.

## Bounded caches and slow consumers: executed results

All **nine native controls completed and met their explicit expectations**.
They use direct reliable DDS endpoints, not the Routing Service path.
The controls deliberately include admission loss and unread-disposal loss;
this is not a claim of lossless behavior.

### Two thousand contacts through four attached slots

Each of two profiles processed 2,000 distinct contact write/dispose pairs,
plus a protected alive sentinel. Every full payload, invalid disposed
notification and both key fields were checked. Neither run reported sample
loss or rejection.

| Reader minimum state | Maximum attached observed | Maximum detached observed | Maximum combined observed |
|---|---:|---:|---:|
| Enabled, total limit 8 | 4 | 4 | 8 |
| Disabled | 4 | 0 | 4 |

This demonstrates repeated slot reuse with bounded native instance counts.
It is not a measurement of total process memory or peak queued samples:
these cache snapshots are taken after notifications are consumed. The
companion writer has its own 4,096-instance allowance; low-capacity writer
admission is not tested by this experiment.

### Unread disposed state versus new-instance pressure

The slow-reader controls have only two attached slots: one protected alive
sentinel and the target. A third, new key forces a replacement decision.
The table describes the first pressure attempt, **before taking any pending
target disposal**.

| Reader sample retention | Target pending | Hold before pressure | Target disposal still available? | New key admitted? | Expired samples |
|---|---|---:|---|---|---:|
| INFINITE | Valid data + invalid dispose | 11 s | Yes | No | 0 |
| 10 s | Valid data + invalid dispose | 8.5 s | Yes | No | 0 |
| 10 s | Valid data + invalid dispose | 11 s | **No** | Yes | 2 |
| INFINITE | Invalid dispose only | 11 s | Yes | No | 0 |
| 10 s | Invalid dispose only | 11 s | **No** | Yes | 1 |

In the finite-delay cases, the expired-sample count was still zero after the
eleven-second hold, before pressure. It increased only when the new key
arrived. The old attached-key lookup then failed and the new key was
available. This directly demonstrates **lazy, pressure-triggered expiry**,
not an autonomous deletion exactly ten seconds after disposal.

Most importantly, the invalid-only case had already consumed the original
valid data. The **unread disposal itself expired**. This is not merely loss
of an old position while the deletion remained available.

For INFINITE retention, the application could still consume the disposal
after eleven seconds, but the competing new sample was reported lost.
Keeping deletions indefinitely with a finite cache therefore needs an
admission/backpressure strategy; reliability alone did not admit that new key.
These failures incremented native `SAMPLE_LOST`, not `SAMPLE_REJECTED`.

### Read, take, and outstanding loans

Two additional INFINITE-retention controls verified FULLY_PROCESSED behavior:

- Reading the disposed target **without taking it**, then returning the
  read loan, allowed replacement under pressure. No sample-expiry count
  increment was involved.
- Holding that read loan blocked replacement. Returning it allowed a
  subsequent pressure write to reuse the slot.

Every slow-reader control subsequently filled both slots with alive contacts,
attempted another new key, and verified that both existing keys survived.
The failed admission produced exactly one additional sample-lost increment.
The sentinel survived all nine controls.

Historical evidence, subsequently deleted:
`debug/test_reports/instance_state_resources_20261119/report.json`.
The [runner details](../spikes/instance_state_resources/README.md) remain available.

## Where Instance State Consistency fits

Instance State Consistency (ISC) complements ordinary reliable delivery:

| Mechanism | Purpose |
|---|---|
| Ordinary reliability | Retain/repair an outstanding disposal while an active reliable reader still needs it. |
| ISC | Correct an existing reader's remembered instance state after losing and regaining liveliness with the same physical writer, even without another application write. |

ISC can report the writer's current ALIVE or DISPOSED state; it is not replay of
every intermediate update. It needs the writer to still hold the relevant
instance state. Once a disposed instance has been purged, ISC cannot reconstruct
that deletion. Longer instance retention can therefore matter for a prospective
ISC recovery path, even though it added no demonstrated delivery guarantee in
the recent ordinary-reliability comparison.

Reader inactivity at the writer is not automatically an ISC recovery trigger.
The recent inactivity case retained one matched endpoint and ended with the old
contact still alive. It does not demonstrate that a writer-liveliness
loss/regain recovery exchange occurred.

Our [earlier ISC investigation](cpp_router/isc-findings.md) demonstrated native
same-physical-writer recovery in Connext 7.7 and a relay gap forwarding data-less
recovery back to ALIVE. Its re-write workaround is historical spike evidence,
not a claim that the current processor implements or qualifies that behavior.
A new physical writer after restart, or a new VOLATILE reader, does not acquire
a general historical-state replay guarantee from ISC alone.

**The five ContactReport-over-State cases above qualify ordinary routed
lifecycle/reliability behavior, not ISC on the current processor.** ISC remains
a separate end-to-end qualification candidate. Keep the system's normal
reliable-repair/discovery budgets aligned with expected outages; do not adopt
ISC as an untested replacement for that protection or for restart reconciliation.
No ISC configuration was changed for this record or presentation.

These distinctions were cross-checked against the installed 7.7
`InstanceState.htm` and `RELIABILITY_QosPolicy.htm` manual pages as well as the
preserved historical spike findings.

## Related protocol finding: pre-emptive ACKNACK suppression

A separate actual Routing Service wire experiment suppressed the tested
base-zero startup/pre-emptive ACKNACK form without disabling ordinary repair:

| Observation | Baseline | Suppressed |
|---|---:|---:|
| Startup/base-zero requests across tested WAN writers | 9 | 0 |
| Missing Event sequence 2 requested and retransmitted | Yes | Yes |
| Complete original command payloads received | 8/8 | 8/8 |

Other successful baseline runs observed eleven startup requests; suppression
consistently produced zero in those runs. These are controlled startup counts,
not a bandwidth or outage-recovery performance guarantee.

The working DataReader property is
`dds.data_reader.protocol.disable_preemptive_nack=true`. It was added to the
external WAN reader QoS source, with inheritance checks; no live deployment was
performed. It does not fix disposal replay after reader inactivity or restart,
and does not disable normal positive acknowledgments or repair requests.

The [preserved spike record](../spikes/preemptive_acknack/README.md) explains
the packet-level controls, exact scope and effective configuration. Its local
reports and captures were also deleted during the requested debug cleanup.

### Participant-level follow-up (2026-11-20)

The exact participant-scoped property
`dds.participant.inter_participant_data_reader.protocol.disable_preemptive_nack`
was subsequently tested with false/true controls. It was accepted, but **did not
suppress the observed discovery startup requests** with this installation's
SPDP2 + SEDP configuration. In each of two routed before/after comparisons,
SPDP2 configuration, service request, SEDP publication and SEDP subscription
writers each retained two base-zero ACKNACKs: eight total in both variants.
All four trials delivered 8/8 commands, including the intentionally lost command
after ordinary repair. Application-reader startup suppression remained enabled
in both variants.

A separate native DDS control read the false/true setting back from created
participants and reproduced the unchanged builtin counts without Routing
Service. This is a negative qualification result, not a claim that the property
can never work under other discovery conditions.

The [spike follow-up](../spikes/preemptive_acknack/README.md#participant-scoped-follow-up-2026-11-20)
records effective inline-QoS verification, vendor-specific builtin decoding and
current retained evidence. Its six new captures were created after the debug
cleanup. The original application-reader experiment was also rerun successfully
(9 startup requests to zero, with repair retained), regenerating its evidence.
No participant property was deployed; the mesh remains stopped.

A subsequent explicit four-way matrix repeated reader off/on crossed with
participant off/on. Application startup ACKNACKs were 9/9 with both off,
0/0 with reader-only, 11/9 with participant-only, and 0/0 with both on
(the two values are the two repetitions). **SEDP publications and subscriptions
each remained at two startup requests in all eight trials**, including both
settings enabled. All 64 commands arrived, with deliberate loss repaired in
every trial. The [matrix record](../spikes/preemptive_acknack/README.md#independent-readerparticipant-matrix-2026-11-20)
and current evidence distinguish this negative SEDP result from the working
application-reader suppression.

A minimal Python-only follow-up compared classic SPDP + SEDP against
SPDP2 + SEDP, with the participant property false and true, twice each.
SEDP publication and subscription startup counts each stayed **2 to 2** under
both discovery configurations. All eight mutual-discovery checks passed, and
native QoS readback plus wire identities confirmed the selected protocol.
There were no application endpoints or Routing Service processes.
This rules out SPDP2 alone as the explanation for the observed negative result
in these controls; the property's effective scope remains unresolved.
See the [minimal startup comparison](../spikes/preemptive_acknack/README.md#minimal-python-discovery-comparison-2026-11-20).

## Policy implications and remaining limits

1. **Keep propagation and cleanup separate.** A correct application handles
   native disposed state; it must not wait for the instance's DDS cache entry
   to disappear before deleting its own contact.
2. **Do not size retention from one-way latency alone.** Repair includes
   reverse-path acknowledgment and protocol scheduling. Cleanup eligibility
   also changes when a reliable reader becomes inactive, even if an endpoint
   still appears matched.
3. **Restart and very long outages need reconciliation.** A persistent
   tombstone/state journal or a current-state snapshot/reconciliation protocol
   is a separate design decision. A larger finite cleanup interval alone
   does not establish recovery across all reader or writer restarts.
4. **Choose a slow-consumer loss policy explicitly.** INFINITE disposed-sample
   retention preserves unread deletion state in these controls but can block
   admission of new keys. Ten-second expiry permits bounded-cache progress
   by sacrificing unread data, including deletion notifications.
5. **Do not transfer State conclusions to commands.** State uses latest-value
   coalescing; dispose/revive sequences need the correct final contact state,
   not delivery of every intermediate transition. Required command/event
   history has different KEEP_ALL and loss requirements.

This study is not qualification of arbitrary link distributions, a 120-second
or 300-second outage, source/platform restart, persisted application caches,
late join across every lifecycle state, fleet-scale resource pressure,
multiple writers sharing a key, or a full UMAA configuration. The short-policy
test deliberately accelerates reader inactivity; it does not retune the live
300-second policy or prove a particular long-outage guarantee for it.

## Reproduction and evidence

From the repository root, with the supported image and readable license:

```bash
bash spikes/contact_disposal_timing/run.sh
bash spikes/contact_disposal_timing/run_delayed.sh
bash spikes/contact_disposal_timing/run_ddil.sh
bash spikes/instance_state_resources/run.sh
```

The extended WAN runner also accepts a comma-separated subset, for example:

```bash
DDIL_SCENARIOS=peer_loss,restart bash spikes/contact_disposal_timing/run_ddil.sh
```

Runners build only disposable probes in private container `/tmp`, mount source
read-only, and retain compact reports plus archived inputs/logs under
`debug/test_reports`. They do not overwrite running binaries, reconfigure the
mesh, or change live QoS.

### Reference behavior checked against installed documentation

The relevant installed Connext 7.7 manual pages are:

- `WRITER_DATA_LIFECYCLE_QoS_Policy.htm`
- `READER_DATA_LIFECYCLE_QoS_Policy.htm`
- `DATA_READER_RESOURCE_LIMITS_Qos.htm`

They are under the SDK's
`doc/manuals/connext_dds_professional/users_manual/users_manual/` directory.
The experimental observations, effective native QoS, cache records and actual
forwarded application state take precedence over an unverified API description.
