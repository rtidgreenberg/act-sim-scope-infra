---
marp: true
theme: rti
paginate: true
size: 16:9
title: "System Presence: Is the Platform Still There?"
description: "Plain-language roles for writer liveliness, SPDP2 discovery, RouterHealth, and wire-traffic validation"
footer: "RTI Connext | System presence | 2026-11-11"
---

<!-- _class: lead -->

# Is the platform still there?

## Monitor presence at the system level,<br>not separately for every message stream.

**Discovery keeps track of participants.<br>RouterHealth tells the application what we are hearing.**

<!--
The operational question is whether the platform is alive or has gone down.
A receiver can establish reachability by its policy, not prove physical death:
a crashed platform, failed radio path, or severe congestion can look alike.
Router health is a system-presence signal, not proof every onboard task works.
Scope: Connext DDS Professional 7.7.0. No new runtime qualification is claimed.
-->

---

# Three choices, three different jobs

| Choice | Plain-language meaning | Fit for our goal |
|---|---|---|
| Reader / writer liveliness | "This writer is still there." | Too narrow for whole-platform presence; finite writer leases disable writer-side filtering in Connext 7.7 |
| Participant liveliness / SPDP2 | "Keep this participant in the discovery database." | Maintains discovery records and removes expired participants |
| Periodic RouterHealth + deadline | "We are hearing from this platform, with fresh metadata." | System-presence signal with application callbacks |

> **Use each for its own job. Do not make every data writer a platform heartbeat.**

<!--
Writer LIVELINESS is asserted by a DataWriter and observed by matched readers;
there is no symmetric DataReader LIVELINESS contract.
The finite-writer-lease filtering restriction is documented for 7.7 and does
not mean reader-side filtering stops working. Unwanted data may cross the WAN.
AUTOMATIC writer liveliness may continue while application work is stalled.
MANUAL_BY_PARTICIPANT writer liveliness is not the participant discovery lease.
Infinite application-writer leases remove this particular filtering blocker,
but other prerequisites for writer-side filtering still apply.
Source: 7.7 Users Manual, LIVELINESS QosPolicy:
https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/LIVELINESS_QosPolicy.htm
-->

---

# SPDP2 keeps the discovery database current

**Discovery is the middleware's list of known participants.**

- SPDP2 sends periodic assertions: "This participant is still here."
- Receiving assertions keeps the participant's discovery record alive.
- If its discovery lease expires, the middleware removes the participant.
- This supports discovery and cleanup; it is not our application-health report.

> **SPDP2 answers:** "Should this participant remain in discovery?"<br>
> **Our application asks:** "Is the platform still reachable, and what is its state?"

<!--
SPDP2 means Simple Participant Discovery Protocol 2.0.
Its small periodic liveliness messages are separate from full discovery
announcements. The assertion period and discovery lease are distinct settings.
Participant expiry also affects associated endpoint discovery.
Discovery can expose status notifications, but it does not carry RouterHealth
application metadata or prove progress of the platform's business tasks.
This slide explains SPDP2's role; it does not claim it is enabled in the live mesh.
Sources: 7.7 Users Manual, Simple Participant Discovery 2.0 and DISCOVERY_CONFIG:
https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/Simple_Participant_Discovery_2.htm
https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/DISCOVERY_CONFIG_Qos.htm
-->

---

# RouterHealth tells the application what changed

```text
Platform sends periodic RouterHealth metadata
                 |
                 v
Receiver gets an update, or a deadline callback reports a miss
                 |
                 v
Presence policy updates the platform state and notifies the application
```

- **Updates arrive:** refresh platform identity, health summary, and presence.
- **Updates stop:** mark stale; mark down after the agreed grace period.
- **Updates resume:** report recovery using current platform / session evidence.

> **"Down" means unreachable from this observer, not proven physically dead.**

<!--
RouterHealth is the logical application health stream. In the two-topic design
it is a case inside the shared Status topic, not a third permanent WAN topic.
DDS DEADLINE provides a missed-update callback for a known instance; the
application policy decides how this becomes a presence notification.
An incoming-data callback/condition handles new metadata; DEADLINE does not
itself carry the metadata or publish a dashboard update.
Reader DEADLINE begins after first receipt. Expected platforms never seen need
a separate startup/expected-roster check. Allow for scheduling and network jitter.
Current two-topic source uses a 2 s deadline and 30 s grace after STALE;
only health-instance deadline misses drive presence. Any valid received Status
can restore ALIVE, so current ALIVE denotes source reachability, not necessarily
resumption of the health stream. This slide is the plain-language flow, not
a replacement specification for those details.
Sources:
https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/DEADLINE_QosPolicy.htm
Repository: harness_v2/qos/act_qos_profiles.xml,
router/src/core/PresenceMonitor.cxx, router/src/core/WanAdapterFactory.cxx.
-->

---

<!-- _class: compact -->

# Validate all wire traffic by role and purpose

**For every traffic type: who sends it, who needs it, and why is it on the wire?**

| Traffic | Its job | What to validate |
|---|---|---|
| Discovery announcements / SPDP2 assertions | Find participants; maintain and clean up discovery records | Intended peers, cadence, expiry, and byte cost |
| Writer liveliness | Track an individual writer | Where it is needed; effect on filtering |
| RouterHealth | Periodic platform metadata and presence notifications | Intended monitors receive it; missed updates notify |
| Commands, reports, and status | Carry application work | Correct control/platform roles, teams, destinations, and sender-side filtering |
| Acknowledgments and repairs | Support reliable delivery | Expected return paths and traffic during loss / recovery |

> **Validate with wire evidence, not just correct application output.**

<!--
"Role" covers both the message's function and the sender/receiver system role:
control versus platform, addressed recipients, and team membership where relevant.
This is a traffic-validation requirement, not a claim that DDS filters provide
authentication, authorization, or complete security enforcement.
Every observed traffic type must be accounted for, including middleware control
traffic, retries, and startup/restart bursts, not only user-topic payloads.
Check appropriate control-to-platform and platform-to-control health flows;
do not assume all platform-to-platform health visibility without a requirement.
Writer-side suppression requires sender counters or scoped packet capture:
receiving only desired values also happens with filtering at the receiver.
This deck defines what to validate; it does not claim those captures were run.
-->

---

# Keep discovery and system presence separate

1. **SPDP2:** maintain the discovery database and clean up expired participants.
2. **RouterHealth:** provide periodic metadata and callbacks for system presence.
3. **Application traffic:** keep writer-side filtering for the intended recipients.
4. **Wire validation:** account for every traffic type by purpose and system role.

> **The operational question is simple:**<br>
> "Are we still hearing from the platform, or should we report it down?"

<p style="font-size:18px;margin-top:22px">Connext 7.7 references:
<a href="https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/LIVELINESS_QosPolicy.htm">writer liveliness</a> |
<a href="https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/Simple_Participant_Discovery_2.htm">SPDP2</a> |
<a href="https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/DEADLINE_QosPolicy.htm">deadline callbacks</a></p>

<!--
Do not silently enable finite writer liveliness on shared application endpoints.
This is a communication/design deck; no QoS, application, or live-mesh behavior
was changed. System presence and detailed task health remain different claims.
The same absence can result from platform failure, a broken path, or congestion.
Validate timeout behavior, recovery, restarts, never-seen expected platforms,
audience filtering, and wire traffic before claiming system acceptance.
-->
