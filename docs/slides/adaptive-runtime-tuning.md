---
marp: true
theme: rti
paginate: true
size: 16:9
title: "Adaptive runtime tuning"
description: "RTT and protocol-pressure feedback driving bounded WAN tuning policies"
footer: "RTI Connext | Adaptive runtime tuning | 2026-11-20"
---

<!-- _class: lead -->

# Adaptive runtime tuning

## Measure the link. Adjust the load. Protect commands.

**Use round-trip time (RTT) and backlog signals to adjust WAN load within safe limits**

Design proposal for Connext / Routing Service 7.7. **Not implemented.**

<!--
RTT means round-trip time. Backpressure means outstanding work is growing
or transmission/application progress is constrained.
This deck proposes a feedback controller, not an already qualified capability.
No application, IDL, QoS, gateway binary or runtime deployment is changed.
Sources and exact API boundaries are retained in speaker notes.
-->

---

# Yes: a topic can carry the tuning policy

```text
Measurements (RTT + queue backlog)
             |
   Threshold logic (picks a mode)
             |
  TuningPolicy topic (requests a change)
             |
  Local applier (checks & applies live settings)
             |
  TuningStatus topic (reports what actually changed)
```

> Publishing a policy request does not change QoS by itself—a local applier must validate and apply it.

<!--
An actuator is code that validates a command and invokes supported APIs.
Telemetry collection, policy decisions and applying changes are separate roles;
they need not be separate processes or require new DDS participants.
TuningPolicy and TuningStatus are proposed logical messages, not existing types.
The actuator must have supported access to Routing Service-owned resources.
Status closes the loop with actual readback and explicit failure reporting.
-->

---

<!-- _class: compact -->

# Use more than RTT to diagnose pressure

| Signal | What it tells us | Important caveat |
|---|---|---|
| Average RTT + reading age | Link delay and whether the reading is current | High radio delay does not always mean congestion |
| Unacknowledged messages | Reliable backlog is piling up | One slow receiver can hold shared history |
| Resent (repair) traffic | How much bandwidth recovery is using | Compare recent deltas, not lifetime totals |
| Oldest waiting message | Whether delivery is catching up or stuck | Track per peer or topic class where possible |
| Write timeouts / rejected writes | Queues are full or delivery is failing | Report as an error—never hide it with tuning |

> No fresh RTT during an outage means **unknown**, not a fast link.

<!--
Use available native DataWriter/DataReader protocol/cache statuses and timestamps.
Metric availability and exact per-reader/per-writer scope must be qualified for
the current Routing Service-owned endpoints. Do not invent a native pending-age
counter: derive age only if sequence/timestamp evidence supports it.
Application command/ACK RTT, passive router RTT and NACK-to-repair RTT measure
different paths; retain provenance and do not substitute one for another.
Separate producer overload, stalled LAN consumption and radio/link loss.
Counters must handle process restart/reset; do not interpret resets as recovery.
-->

---

<!-- _class: compact -->

# Which settings can actually change live?

| Setting | What Connext supports | What we still need to prove |
|---|---|---|
| Reliable send window | Grows or shrinks within min/max limits | Check configured limits; it is not a bandwidth cap |
| Bandwidth cap (`FlowController`) | Rate and burst can be updated live | Verify our Routing Service code can access it |
| Minimum update spacing | Time-based filter can throttle `State` / `Periodic` | Prove it saves WAN bytes and preserves deletions |
| Receive-filter parameters | Content-filter values (e.g., team) change live | Keep existing route and filter rules working |
| App reporting rate | Controlled by the sending app, not DDS QoS | Needs a separate app or route control message |

> Just because Connext supports changing a setting live does not mean it adjusts itself—or that our router exposes it yet.

<!--
Documented:
https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/Getting_Setting_Properties_for_a_Specifi.htm
https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/api/connext_dds/api_c/group__mutable__qos.html
https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/DATA_WRITER_PROTOCOL_Qos.htm
Send-window adaptation bounds and algorithm parameters are configured before
enablement. Do not claim that the current profiles enable a useful adaptive range
without reading back their effective min/max configuration.
FlowController.set_property changes token_bucket, not scheduling_policy.
Finite-to-infinite period changes are not part of this proposal.
TimeBasedFilter.minimum_separation is mutable, but WAN savings depend on actual
writer-side suppression. Periodic spacing must respect its freshness/deadline
contract; dispose/unregister/revival behavior must be tested for State.
Routing Service UPDATE supports documented mutable endpoint QoS and content-filter
parameters. Native mutability alone does not establish our actuator's support.
-->

---

# Keep structural changes outside the live loop

- **Locked once started:** reliability, durability, history depth, and memory/resource limits cannot change without recreating endpoints.
- **Also locked:** writer heartbeat timers and maximum repair-packet size.
- **Cannot swap live:** which bandwidth controller (`FlowController`) or scheduler a writer uses.
- **Separate projects:** static discovery, compression, and transport changes belong in offline testing, not this feedback loop.

> Keep `Event` reliable with full history (`KEEP_ALL`). Never drop required events to relieve pressure.

<!--
7.7 immutable reference:
https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/api/connext_dds/api_c/group__immutable__qos.html
The reference lists heartbeat_period, fast_heartbeat_period and
late_joiner_heartbeat_period as immutable, contrary to the earlier Connext AI answer.
max_bytes_per_nack_response and reliable protocol window/retry sizing are not
proposed live actuators.
Changing XML on disk does not update enabled entities. Changes requiring recreation
must use the owned coordinated lifecycle, with explicit delivery/recovery impact.
Deadline and Lifespan are mutable, but changing Deadline does not set a reporting
rate and reducing Lifespan can discard required work. Neither is the first actuator.
LBED and compression are deployment/compatibility qualification work, not threshold
knobs in the first feedback loop. CFT parameters are not arbitrary expression edits.
-->

---

<!-- _class: compact -->

# Send bounded requests, not raw QoS XML

| Proposed `TuningPolicy` field | Purpose |
|---|---|
| Target gateway + traffic class | Apply only to the intended node and class |
| Sender epoch + sequence number | Reject old or replayed policy requests |
| Mode: Normal / Constrained / Recovery | Select a pre-approved profile instead of raw QoS |
| Rate budget + update spacing | Adjust limits only within approved min/max bounds |
| Expiration + reason | Expire stale requests and explain the decision |

**`TuningStatus` reports back:** requested revision, what was actually applied, pass/fail, and any error.

> Sending a request is not the same as applying it—require an acknowledgment and read back the live setting.

<!--
Illustrative contract only; no IDL is introduced by this deck.
Resource identifies the actuator's actual control scope: the existing shared
controller has one aggregate budget, not independent per-topic reservations.
A profile maps to allowlisted XML-authored settings. Unsupported settings are
rejected explicitly; do not silently clamp a request and report success.
Requested and effective values must remain distinguishable.
Authority epochs require a defined failover/fencing rule; sequence alone is not
enough across controller restart. Use a single authorized writer initially.
Validity must define clock assumptions. With no bounded clock skew, use local
monotonic lease tracking plus session/revision fencing; restart must not replay
stale retained intent as fresh. Qualify the freshness/expiry design before coding.
Partial apply: validate all settings before application where possible, report
partial failure, and restore the last known safe profile with observable readback.
-->

---

<!-- _class: compact -->

# Thresholds pick a mode, not a moving target

| Mode | When we enter it | Safe response |
|---|---|---|
| **Normal** | Fresh readings; flat backlog | Run normal reporting within the node's budget |
| **Constrained** | Backlog growing; high RTT or heavy repairs | Slow `Periodic` updates first; keep `Event` commands flowing |
| **Recovery** | Link returns with old reliable messages waiting | Hold reporting low so repair traffic can drain |
| **Unknown / outage** | Readings go stale or delivery stops | Drop to a safe baseline—never ramp up rates blindly |

**Avoid flip-flopping (hysteresis):** require a *higher* threshold to enter a slower mode than to leave it.

**Wait and step slowly:** require steady readings before recovering, and raise rates in small steps.

<!--
Thresholds are symbolic until isolated measurements establish safe values.
Example: enter Constrained after backlog growth exceeds B_high for K valid
windows with supporting RTT/repair evidence. Enter severe pressure independently
on a cache-capacity alarm even when no fresh RTT can be measured.
Exit only after backlog is below B_low, RTT is below R_low and progress is stable
for an independently chosen dwell interval; B_low < B_high and R_low < R_high.
This avoids oscillation when noisy measurements sit near a boundary.
Recovery means existing readers/routes recover, not a promise of VOLATILE replay
to a restarted/new reader. Reader inactivity beyond system timeout remains a
separate reliability limitation.
For outages, apply existing producer admission/backpressure behavior for required
Events; do not replace/drop them or let memory grow without limits.
-->

---

# The control loop must survive pressure

- Allow **only one authorized policy sender** and accept only pre-approved settings.
- Rate-limit policy updates and let newer requests replace older pending ones.
- Keep a **local safe fallback** if telemetry or policy requests expire.
- Switch to the safe baseline locally when the WAN cannot deliver instructions.
- Share the **whole radio link budget** across all gateways—including other nodes and repair traffic.

> Giving every gateway a 2 Mbps budget does not keep total fleet traffic under 2 Mbps.

<!--
Authenticate/authorize policy writers and targets using the supported deployment
mechanism; do not imply DDS Security is currently enabled.
Local baseline is explicit, logged fail-safe behavior, not a silent fallback.
Backpressure must not cause unbounded policy-topic backlog or a command storm.
Do not assume priority alone allows a policy to overtake earlier required data
in the same reliable stream. Local fail-safe decisions cannot depend on WAN repair.
The controller needs fleet/link arbitration or conservative per-gateway allocations;
independent controllers increasing rates together can destabilize a shared radio.
Highest-priority-first service is not a guaranteed per-class reservation.
Any guaranteed minimum requires a qualified allocation/admission design.
-->

---

# Fit the existing three-topic WAN

**Start locally on each gateway's management LAN.**

- Local stats feed the controller, and local policies go to the applier.
- Route remote policies over the WAN **`Event` topic** only if remote control is needed.
- Register any new WAN message through the approved schema/catalog workflow.
- Do not add a fourth WAN topic or a hidden DDS participant by accident.

> First prove the Routing Service applier can change one live setting and read it back.

<!--
The proposed local policy/status messages do not automatically exist in the
current schemas or prepared bindings. Remote policy over Event requires an
approved payload branch/binding and compatible deployment; do not assume an
arbitrary new LAN topic can be forwarded without contract preparation.
Local LAN policy is a proposed control-plane scope, not a deployed participant.
Routing Service owns the DDS entities. Confirm a supported extension/API boundary
for a FlowController token_bucket update before designing around it; do not merely
update the participant's startup property strings and assume a live effect.
Remote administration reference:
https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/services/routing_service/remote_admin.html
If native FlowController access is unavailable, choose a supported producer-rate
or mutable reader-filter actuator; report that limitation rather than recreating
entities invisibly under a purported live change.
-->

---

<!-- _class: compact -->

# Routing back to C2: voting on link strength

**Use published link health to pick the strongest path back to C2.**

| Step | What happens | How it fits our WAN |
|---|---|---|
| **1. Publish link health** | Each node shares presence, RTT, and backlog pressure | Sent over `Telemetry` (e.g., `RouterHealth` / link stats) |
| **2. Vote on path strength** | Nodes score direct vs. peer-relay paths from recent health | Fresh, low-backlog links get higher votes; stale links drop out |
| **3. Forward via winner** | Route or relay policy sends data over the top-voted link | Uses hysteresis + dwell timers so routes do not ping-pong |

- **Why "vote"?** One noisy metric can mislead; combining presence, RTT, and backlog across peers picks a genuinely healthy relay when a direct link degrades.
- **Safety:** require a clear margin before switching paths, and fall back to direct C2 if relay votes expire.

> **Proposed extension:** link-health voting can guide relay selection back to C2 without adding a new WAN topic.

<!--
Design proposal only; multi-hop relay routing and link-strength voting are not
implemented in the current Routing Service processor.
Link health is already carried on Telemetry (RouterHealth / link stats), so
publishing scores back to C2 does not require a fourth WAN topic.
A voting/scoring rule must handle asymmetric links (good outbound, bad return),
avoid forwarding loops, prevent route flapping with hysteresis and dwell times,
and account for extra hop latency and shared-radio airtime when relaying.
Any route-switching actuator must preserve Event reliability, State lifecycle
keys, and duplicate suppression across direct and relayed paths.
-->

---

<!-- _class: compact -->

# Testing: compare fixed vs. adaptive runs

| Test scenario | What must happen |
|---|---|
| RTT rises, no backlog | Reporting does not collapse unnecessarily |
| Sustained overload / one slow peer | Queues stay bounded; modes do not flip-flop |
| Loss, blackout, and link return | Required `Event`s arrive; `State` converges to final values |
| Delete / recreate contact while throttled | Final `ContactReport` state is still accurate |
| Old, duplicate, or invalid policy | Explicitly rejected or safely expired |
| Controller restart or apply failure | No stale replay; applied status is reported accurately |

Compare **command delay, recovery time, WAN bytes, queue pressure, and mode stability** against fixed profiles.

<!--
Use isolated private network/IPC environments and paired fixed-profile controls;
do not test against a retained operator mesh or overwrite persistent binaries.
Predeclare numerical acceptance gates from requirements before qualification:
Event occurrence coverage and no duplicates where required, command p95/p99/worst
latency, State convergence after restoration, per-link bytes, bounded memory and
maximum policy transition frequency. No new numeric guarantees are established here.
Include simultaneous gateways, asymmetric loss and reverse-path ACK impairments.
Prove live application through QoS/property readback AND actual wire/behavior change,
without endpoint GUID churn, not merely a successful policy publication or API return.
Separate restarted-reader VOLATILE limitations from same-reader outage repair.
-->

---

# Key takeaways

1. **Yes:** RTT and backlog signals can drive a tuning policy topic.
2. **A topic only asks; an applier acts:** a local applier must validate and change supported live settings.
3. **Start small:** tune reporting rates and approved bandwidth caps first.
4. **Protect core rules:** keep `Event` reliable, `Telemetry` fresh, and `State` deletions intact.
5. **Build in safety:** use hysteresis, small steps, expiration timers, and readback confirmation.
6. **Prove it first:** verify Routing Service API access and closed-loop gains in isolated tests before deploying.

<!--
Proposal only. No adaptive controller, policy type, runtime actuator or new
QoS behavior has been implemented or deployed by creating these slides.
The 7.7 product documentation supports native FlowController token-bucket
mutability and endpoint TimeBasedFilter/Deadline/Lifespan mutability, not a claim
that our existing Routing Service integration already exposes all those controls.
Known Connext AI corrections were checked before presenting this design.
-->
