---
marp: true
theme: rti
paginate: true
size: 16:9
title: "DDIL recovery: contact deletion and retained state"
description: "Measured recovery behavior and limits of the Connext Routing Service processor"
footer: "RTI Connext | Measured 2026-11-19 | Summary 2026-11-20"
---

<!-- _class: lead -->

# DDIL recovery

## Contact deletions across delayed, disrupted, and intermittent links (DDIL)

**Reliable delivery recovers missed deletions as long as the receiver stays active.**

If the receiver times out, restarts, or lets unread messages expire, deletions can be lost.

<!--
Companion: ../instance-state-through-ddil.md.
DDIL refers here to delayed, disrupted and intermittent WAN conditions.
These are historical empirical results, not a fresh run or a general guarantee.
The user-requested debug cleanup on 2026-11-20 removed the local reports,
archives and captures. This presentation, companion document and probe sources
preserve the recorded findings. The live mesh was stopped for that cleanup.
No new lifecycle policy, binary or reconciliation design was deployed.
-->

---

# A cleanup timer does not discard unacknowledged deletions

The sending WAN writer can erase a deleted contact from memory **only when both are true**:

1. Its cleanup delay (10 seconds in our tests) has passed.
2. Every active reliable reader has acknowledged receiving the deletion.

- **Link down, reader still active:** the writer holds the deletion and resends it when the link returns.
- **Reader becomes inactive:** the writer stops waiting for it and can clean up the deletion.

> An acknowledgment at the C2 gateway only proves the gateway received the deletion—not that the final C2 application processed it.

<!--
Scope: the tested VOLATILE reliable writer and configured timestamp basis.
The ten-second writer delay is a minimum retention interval, not an instruction
to discard an active reader's unacknowledged disposal at ten seconds.
Each routing hop has independent queues and acknowledgments. Application-level
acknowledgment modes were not qualified. Native instance removal does not mean
every internal allocation or application object is removed.
-->

---

<!-- _class: compact -->

# Instance State Consistency (ISC): syncing state on reconnect

**ISC** helps a reader resync whether an item is **alive** or **deleted** after a brief disconnect, without the sender resending the full data.

| Mechanism | Question it answers |
|---|---|
| Normal reliable delivery | Did this active reader receive the deletion message? |
| Instance State Consistency (ISC) | After reconnecting to the same writer, is this item currently alive or deleted? |

ISC is not a replay of every missed message—it only syncs the current alive/deleted state.

> ISC only works if the writer still has that item's state in memory.

<!--
Verified against installed Connext 7.7 InstanceState.htm and
RELIABILITY_QosPolicy.htm, and the earlier native ISC spike recorded in
../cpp_router/isc-findings.md. That older investigation demonstrated same-physical-
writer liveliness loss/regain and data-less ALIVE recovery.
ISC is not the same trigger as the writer declaring a reliable reader inactive.
An inactive reliable reader with still-matched endpoints is not, by itself,
proof that the lost/regained-writer-liveliness ISC exchange occurred.
RECOVER_INSTANCE_STATE_CONSISTENCY and the required reader recovery settings
must be configured appropriately; the recent ContactReport tests did not
enable/qualify ISC as a distinct recovery mechanism.
-->

---

# Should we rely on ISC in Routing Service?

**Helpful for brief reconnects to the same writer; not a fix for process restarts.**

- **Cleaned-up state is gone:** once a writer erases a deleted contact, ISC cannot recover it.
- **Restarts are not covered:** in Connext 7.7, if a writer or reader restarts with a new identity, ISC does not automatically replay old deletions.
- **Forwarding gap in earlier relay:** an earlier test of our custom C++ relay could not forward an empty "alive again" sync signal without re-sending real data.

> Keep normal reliable repair alive through expected outages; test ISC end to end in Routing Service before counting on it.

<!--
Evidence: ../cpp_router/isc-findings.md, particularly native T2/T5 and relay R3/R2.
This historical finding is not a fresh test of the completed Routing Service
processor. The relay cannot synthesize a data-less ALIVE through a normal
application writer operation; the old spike repaired propagation by re-writing
the retained real payload. Do not claim this is implemented in the current
processor or that every ISC transition is blocked.
Same physical writer reconnect differs from a restarted writer with a new
physical GUID, even with virtual-GUID continuity. A new VOLATILE reader does
not gain historical replay simply by enabling ISC.
Longer writer instance retention is a prerequisite candidate, not an observed
extra recovery guarantee in the recent ContactReport matrix.
The current processor needs focused end-to-end ISC qualification before adoption.
No ISC or production lifecycle configuration changed for this presentation.
-->

---

<!-- _class: compact -->

# How we tested contact deletions over a bad link

```text
Platform app -> Platform router --(WAN: 3 s delay + blackout)--> C2 router -> C2 app
```

- Used the real deployed **Connext / Routing Service 7.7.0** binaries in isolated containers.
- Added **3 seconds of delay each way** (~6 s round-trip) on the WAN link only, plus **2 s or 30 s blackouts**.
- Sampled the WAN writer's internal cache every **50 ms** to see when deletions were erased.
- Checked all 18 `ContactReport` fields and deletion keys `(source, contact_id)` at the C2 app.
- Ran separate direct-reader tests to see what happens when reader memory fills up or the app reads slowly.

> Isolated test containers—not experiments on the running six-platform mesh.

<!--
LAN domains 211/213, WAN 212; UDPv4 with private network and IPC namespaces.
Linux tc netem applied to WAN-domain ports 60400-60649 in both directions,
including acknowledgments, discovery and repair; LAN traffic was not delayed.
Independent UDP calibration measured 3.000054-3.000089 seconds in the WAN queue.
The payload has eighteen checked fields. Receive-side source becomes Platform_30;
original source_node/audit fields remain intact. Invalid disposal keys are checked.
The cache observer creates no extra DDS endpoint.
Source external QoS included the undeployed pre-emptive request suppression
setting, plus isolated experiment overrides; this is not byte-identical live QoS.
Native cache-pressure controls are not routed fleet/load qualification.
-->

---

<!-- _class: compact -->

# Test summary: ContactReport over the WAN `State` topic

`ContactReport (Platform LAN) -> State (WAN) -> ContactReport (C2 LAN)`

| Scenario | Blackout | Result at C2 app |
|---|---|---|
| Reader stays active | 30 s | **Deletion delivered** at 40.785 s |
| Reader times out (inactive) | 30 s | **Deletion lost** — old contact still alive at 75 s |
| C2 router + app restart | 30 s | **Deletion lost** — no data or deletion by 75 s |
| Delete, then recreate same contact | 2 s | **Recreated contact delivered** at 13.912 s |
| Delete, recreate, then delete again | 2 s | **Final deletion delivered** at 13.332 s |

> Real Routing Service forwarding tests, not a test of ISC.

<!--
Source: ../instance-state-through-ddil.md and
../../spikes/contact_disposal_timing/{ddil.py,ddil_probe.cxx}.
These tests originate on a platform application writer and terminate on a C2
application reader. State is the shared WAN family, not a new ContactReport WAN
topic. Both routing legs use the deployed persistent processor binaries.
All eighteen original contact fields are checked; only authoritative source
is restored to Platform_30. Original source_node/audit fields are preserved.
The disposal key is (source, contact_id).
Every case uses three-second one-way WAN delay. Writer disposed-instance delay
is ten seconds; final reader instance/sample retention is INFINITE.
WAN timeout setting: 300 seconds for active-reader and revival controls,
twelve seconds for inactivity/restart boundary tests.
There are three PASS final-state cases and two LIMITATION_REPRODUCED cases.
The target is not republished after disposal except in the explicit revival
sequences. A distinct-key canary proves restored forwarding.
No State-topic ISC recovery result is claimed. Historical raw reports were
deleted during the user-requested debug cleanup, not regenerated for this deck.
-->

---

# 30-second outage (reader stays active): deletion survived

Writer cleanup delay was **10 s**, and the reader-inactivity timeout was **300 s**.

| Time since deletion | What happened |
|---|---|
| 0 s | Platform app deletes the contact during the WAN blackout |
| 10 s | Writer's 10 s cleanup delay finishes, but it keeps the deletion for repair |
| 30 s | WAN link is restored |
| 40.785 s | C2 app receives the deletion |
| 44.073 s | WAN writer cleans up the acknowledged deletion from memory |

> The 10-second cleanup timer did not throw away an active reader's pending deletion.

<!--
The WAN timeout setting was 300 seconds; native active/matched reader counts
stayed at least one throughout the thirty-second outage.
All times are from the initial source dispose, not from link restoration.
These are observed timings, not guaranteed maximum recovery latency.
Earlier eight delay/short-outage trials compared ZERO and ten-second writer
retention, with two repetitions per condition. All delivered the disposal.
-->

---

<!-- _class: compact -->

# Reader times out (inactive): the deletion was lost

For this boundary test, we shortened the WAN inactivity timeout to **12 s**.

| Time since deletion | Observation |
|---|---|
| 0 s | Platform app deletes the contact during the 30 s blackout |
| 10.609 s | Writer marks the reader inactive and erases the deletion |
| 30 s | WAN link is restored |
| 41.065 s | A brand-new test contact arrives, proving the route works again |
| 75 s | Original contact still appears alive at C2; deletion never arrived |

> Still being "matched" in discovery does not protect repair once a reader is marked inactive.

<!--
Qualification: LIMITATION_REPRODUCED, not successful deletion recovery.
The test accelerated WAN timeout to twelve seconds and participant assertion
period to three seconds. No actual participant/endpoint removal was observed:
the minimum matched count stayed one. Do not equate reader inactivity with
participant deletion, or treat twelve seconds as a live production threshold.
The distinct-key recovery canary was repeated until received; the target was
never republished. Source disposed state was gone before restoration.
Zero unacknowledged samples after inactivity is not proof of reader receipt.
-->

---

# C2 restarts during outage: missed deletions are not replayed

C2 router **and C2 app** restarted during the 30 s blackout (12 s inactivity timeout).

| Event | Time since deletion |
|---|---|
| C2 stopped / fresh C2 launched | ~6.6 s / 16.6 s |
| Platform WAN writer erased the deletion | 14.617 s |
| A brand-new test contact reached the restarted C2 | 59.107 s |
| Old contact data or deletion by 75 s | None |

> Reconnecting a restarted node restores live traffic, but does not replay past deletions.

<!--
Same thirty-second blackout, three-second one-way delay, ten-second writer
retention and accelerated twelve-second WAN timeout. C2 stopped cleanly.
Qualification: LIMITATION_REPRODUCED. Native reliable-reader activity fell
to zero; minimum matched count remained one.
The application reader was recreated and its old in-memory contents cleared,
so NO_DATA is not a recovered deletion. Persisted application caches were not
modeled. The distinct-key canary proves restored forwarding without republishing
the target. A source/platform restart was not tested.
-->

---

# Rapid delete-and-recreate on the same contact: final state wins

Changes happened 250 ms apart inside a **2-second blackout** (3 s each-way WAN delay).

| Sequence at platform | Final state at C2 | First observed |
|---|---|---|
| Delete, then recreate | Alive (recreated contact) | 13.912 s |
| Delete, recreate, then delete | Deleted | 13.332 s |

Because the WAN `State` topic keeps only the **latest state per contact** (`KEEP_LAST(1)`), intermediate transitions were merged before crossing the link.

> `State` is designed to deliver the latest state of a contact, not every step along the way.

<!--
Revival was 250 ms after disposal; the second disposal was another 250 ms later.
Times are since the first source disposal. Thirty-two-second total observations;
the terminal-dispose variant checks its final fifteen-second window.
C2 did not observe the first disposal in the revival case, or the intermediate
revival in the terminal-dispose case. Those omissions are intentional State
KEEP_LAST(1) coalescing, not command/event-history delivery qualification.
Required Event/command history has separate KEEP_ALL and loss requirements.
-->

---

<!-- _class: compact -->

# Reader memory limits: expired unread deletions get dropped

Direct DDS reader test with **room for only 2 contacts**: 1 slot holds an active contact, 1 holds a deleted contact, then a new contact arrives.

| Keep deleted samples for | Wait before new contact | Deletion still readable? | New contact admitted? |
|---|---|---|---|
| Forever (`INFINITE`) | 11 s | Yes | No (cache full) |
| 10 s | 8.5 s | Yes | No (cache full) |
| 10 s | 11 s | **No (overwritten)** | Yes |

Even when the app had already read the contact's data and only an unread "deleted" notice remained, the 10 s expiry let the new contact overwrite it.

> A finite sample-expiry timer does not erase at 10 s—it lets new traffic overwrite unread deletions once memory is full.

<!--
These are native DDS controls, NOT routed WAN measurements.
With data plus invalid disposal pending, finite expiry dropped two samples;
with the original valid data already taken, it dropped the one pending invalid
disposal. Before pressure, expiry counters were zero even after eleven seconds.
All controls used INFINITE reader disposed-instance delay and FULLY_PROCESSED
replacement for disposed/no-writers instances. Thus the finite sample delay,
not finite instance autopurge, permits the unread notification loss.
Installed 7.7 reader disposed-instance delay accepts ZERO or INFINITE, not
an arbitrary finite two/ten-second interval.
The UMAA profile is a useful policy comparison, not adopted wholesale.
-->

---

<!-- _class: compact -->

# Fixed-size reader caches: memory safety vs. dropping new contacts

Two direct DDS reader tests each processed **2,000 contact write/delete pairs** with no unexpected loss.

| Remember placeholder after delete | Max active | Max placeholders | Max total |
|---|---|---|---|
| Enabled | 4 | 4 | 8 |
| Disabled | 4 | 0 | 4 |

- **App must finish reading:** if the app holds a sample buffer without returning it, that slot cannot be freed.
- **Full-cache tradeoff:** when all slots were held by *live* contacts, a new contact was dropped (`SAMPLE_LOST`) instead of evicting a live contact.

> Fixed-size caches protect memory, but require an explicit policy for when every slot is full of live contacts.

<!--
Nine native controls completed with their explicit expectations. Intentional
negative controls report SAMPLE_LOST rather than SAMPLE_REJECTED.
The 4,000 aggregate write/dispose pairs are two independent runs with 2,000
distinct keys each, not 4,000 simultaneously attached instances.
The writer allowance was 4,096 instances; this is not low-capacity writer
admission qualification. Snapshots followed sample draining and do not measure
peak queue occupancy or allocated bytes. The full UMAA 1,000-instance operating
scale, fleet load and multi-producer ownership remain unqualified.
-->

---

<!-- _class: compact -->

# Wire experiment: removing extra startup repair requests

Separate Routing Service test on the WAN `Event` topic using reader property `dds.data_reader.protocol.disable_preemptive_nack=true`:

| Observation | Default | Suppressed |
|---|---|---|
| Extra startup repair requests (`ACKNACK`s) | 9 | 0 |
| Intentionally dropped message #2 repaired? | Yes | Yes |
| Complete command payloads received | 8 / 8 | 8 / 8 |

> Cuts startup request noise on connect while keeping normal packet-loss repair intact; does not solve restart replay.

<!--
Preserved source: ../../spikes/preemptive_acknack/README.md.
Other successful baseline runs saw eleven startup requests; suppressed cases
saw zero. This does not disable all ACKNACKs or discovery acknowledgments.
One original Event DATA_SESSION sequence 2 was intentionally dropped; packet
ordering showed the ordinary request followed by its repair and complete payloads.
Controlled startup XML also disabled the writer preemptive-heartbeat optimization
and used two-second heartbeat periods in both variants. Not an unmodified live
traffic benchmark, RF airtime saving claim or improved recovery-time guarantee.
The working property was added to external WAN reader QoS source, with
inheritance and positive-ACK checks. No live deployment was performed.
The local packet captures and reports were deleted in the requested cleanup.
-->

---

<!-- _class: compact -->

# Key takeaways

- **Active reader = safe:** unacknowledged deletions stay available, even with a 0 s cleanup delay.
- **Timeouts are the real boundary:** default WAN timeout is 300 s; our passing outage test was 30 s, not 5 minutes.
- **Latest state converges:** rapid delete/recreate sequences over `State` settled on the true final state.
- **Inactivity or restart loses deletions:** once a reader times out or restarts, missed deletions are not replayed.
- **Reader memory is a tradeoff:** expiring unread deletions frees space for new contacts; keeping them forever can block new contacts.
- **ISC needs separate testing:** it can resync a reconnecting writer, but is not a drop-in restart fix.

> Size timeouts for expected outages, and design separately for restarts and reader cache limits.

<!--
Companion: ../instance-state-through-ddil.md; sources:
../../spikes/contact_disposal_timing/README.md,
../../spikes/instance_state_resources/README.md,
../../spikes/preemptive_acknack/README.md.
Extended routed acceptance: five completed scenarios, three PASS final-state
cases and two LIMITATION_REPRODUCED recovery cases. Nine native resource controls
include expected admission loss and unread-disposal loss. Earlier eight routed
delay trials and nine reader-retention trials plus an expected finite-reader-delay
rejection are separate groups.
No 120/300-second outage, source restart, persisted application cache, fleet load,
multi-writer ownership or general restart reconciliation was qualified.
No production lifecycle policy changed. Reconciliation is a design need, not
an implementation claim. The reports/pcaps were removed at the user's request
on 2026-11-20; no fresh tests were run for this presentation.
-->
