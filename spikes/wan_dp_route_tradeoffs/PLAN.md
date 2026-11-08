# Spike: WAN DP consolidation versus partition and on-demand route control

Date: 2026-11-06

Status: Stage 1 implemented and measured on 2026-11-06; see
[results and reproduction](README.md). Stages 2/3 remain pending. No live
configuration changes were made by this spike.

## Questions

1. Does replacing `platform_wan` plus `location_wan` with one WAN participant per
   platform reduce discovery peaks, average traffic, and bytes per assertion cycle?
2. Does broader participant connectivity increase endpoint metadata exposure,
   discovery state, resource usage, or repair traffic enough to offset that saving?
3. Can sparse, on-demand route endpoints reduce that exposure and overhead while
   preserving explicit destination selection?
4. What are the startup, route-open, membership-change, and recovery costs?

The motivating observation is a network-wide discovery-TX peak of approximately
209 kb/s across seven live nodes on 2026-11-06. It is a discovery-category
measurement, not yet proof of assertion-only traffic or responses from both WAN
participants. The spike must identify those packets before making that claim.

This is separate from the
[RouterLinkProbe removal plan](../../docs/cpp_router/router-health-passive-rtt-plan.md).
Keep health/probe behavior identical across comparison runs; do not combine two
optimizations and attribute their combined saving to removing a DP.

## Comparison matrix

| Variant | WAN DPs per platform | Participant connectivity | Application audience | Endpoint lifetime |
|---|---:|---|---|---|
| A: baseline | 2 | Current team/control DP plus navigation DP | Current route/partition rules | Enabled routes |
| B: consolidated | 1 | Shared discovery scope needed for cross-team navigation | Explicit pub/sub partitions | Same enabled endpoint inventory as A |
| C: consolidated, on-demand | 1 | Same as B | Explicit pub/sub partitions, including pair-specific route audiences | Create on enable; destroy on disable |
| D: baseline, on-demand control | 2 | Same as A | Same required recipients as C | Same on-demand schedule as C |

Use A versus B to isolate consolidation with the same logical endpoints/workload.
Use B versus C to measure endpoint-lifetime benefits under the same route demand.
Use C versus D to isolate DP count with identical sparse route schedules.

For B versus C, B keeps all potential route endpoints present, but prevents inactive
routes from forwarding application traffic. Report that endpoint state explicitly;
do not label forwarding-paused endpoints as destroyed.

All variants implement the same application recipient matrix. Opening a route
must not merely create a writer that all platforms can match. The audience gate
must select the requested destination(s), and be tested on both sending and
receiving sides. Application partitions are routing controls, not DDS Security.

Do not assume partition syntax, wildcard behavior, or endpoint metadata suppression.
Verify the installed Connext implementation using actual matched/discovered endpoint
sets, received samples, and packet evidence.

## Minimal topology and recipient contract

Start with one control node and three platforms:

```text
Control
Platform A -- TEAM_RED
Platform B -- TEAM_RED
Platform C -- TEAM_BLUE
```

| Flow | Expected recipients |
|---|---|
| Targeted control command to A | A only |
| Navigation published by a platform | Other platforms across both teams |
| Team data from A | B, not C |
| Explicit A-to-C route enabled | C only for that route |
| Explicit A-to-C route disabled | No new route data delivered after teardown/drain boundary |
| Router health | Same configured observers in every variant |

Verify whether control also needs navigation and include it identically in every
variant. Assign unique logical flow/source/sequence IDs to payloads. Sample audit
keys include receiver, so fanout and unintended deliveries can be checked exactly.

For closed routes, distinguish writes issued before closure from new writes after
teardown completes. Report late in-flight/reliable deliveries separately rather
than declaring every arrival after the command timestamp a leak.

## Stage 1: isolated DDS mechanism spike

Use subprocess workers per logical node, with one or two WAN participants as
specified above. LAN simulation stays constant and out of the WAN capture.

Implement fixed discovery, UDPv4-only transport, reliability, history, and resource
limits in external XML profiles. Load them through the installed QosProvider.
Only runtime identity, membership, and requested endpoint audience/lifecycle are
data-dependent. Start with a small test type, then repeat with representative
application types so TypeObject/configuration costs are not hidden.

Reuse the capture/GUID-attribution approach in
[bandwidth_compare.py](../partition_retarget/bandwidth_compare.py), not its legacy
domain selections, in-code fixed QoS, or startup/steady-state window assumptions.
Do not treat historical MCP explanations as established wire behavior.

### Scenarios

1. **Cold startup:** capture before workers start; wait for required matches and
   successful delivery, then record startup discovery bytes and completion times.
2. **Infrastructure-only steady state:** pause route application publishers; retain
   identical health/probe behavior. Measure participant/discovery overhead.
3. **Loaded steady state:** identical topic types, rates, payload sizes, reliability,
   recipients, and active flows in every variant.
4. **Sparse route demand:** keep navigation/health active; cycle selected pair routes
   through absent, opening, active, and closed. Compare eager versus on-demand
   endpoint inventory with identical offered application traffic.
5. **Membership change:** move B from RED to BLUE; audit old/new team recipients,
   convergence, metadata changes, and event-window bytes.
6. **Direct route change:** enable then disable A-to-C; measure endpoint announcements,
   first successful delivery, teardown completion, and residual in-flight traffic.
7. **Restart:** stop/restart one owned worker; record discovery/repair burst and
   recovery, with all unrelated required flows checked throughout.

Capture steady-state for at least five actual configured assertion periods
(150 seconds if the period is 30 seconds). Start only after required delivery and
discovery state stabilize; use a bounded timeout and report failure if they do not.
Do not use a guessed fixed handshake delay as proof of settled discovery.

Run at least three repetitions per variant/scenario with balanced ordering.
Report per-run results and spread, not just the best run.

## Stage 2: real-router integration

Only after Stage 1 establishes audience correctness:

- Render A/D from the current two-WAN-DP config.
- Render B/C with the navigation and platform WAN legs assigned to one participant,
  plus explicit endpoint audience rules.
- Exercise route creation/teardown through existing router commands, not manual
  generated-config edits or arbitrary restarts.
- Verify disabled routes have no route endpoints; enabled routes create only after
  their type is available. Record type-discovery wait separately from endpoint
  creation/matching and first delivery.
- Confirm available admin commands/configuration support the required endpoint
  audience changes. If they do not, document that gap before implementing a narrowly
  scoped spike adapter; never claim a middleware-only test proves router feasibility.
- Preserve control reachability, compact health, diagnostics, mesh/team visibility,
  protocol stats, and delivery audit behavior.

Inspect the supported route QoS/partition configuration and existing dynamic-route
helpers before adding an adapter. Compare logical endpoint inventories explicitly;
merging DPs must not accidentally duplicate or remove a navigation flow.

## Stage 3: scale and loss

The workload characterization refines the population sweep to 2, 7, and 8
platforms, plus a 12-platform extrapolation. Use fourteen distinct synthetic
mission schemas, not UMAA, with similar serialized sample sizes, squad-wide pose,
and sparse team contacts. Do not inflate schemas to match TypeObject sizes.
The isolated middleware sweep compares A/B/C; the original D remains an
endpoint-lifetime control from Stage 1. This does not skip Stage 2's real-router
gate or the remaining scale/recovery/RF scenarios.

Repeat with sparse teams and a common-team case. Report
DP count, discovered peer edges, endpoints per DP, and recipient fanout at each size.

Then validate representative winners under isolated EMANE loss:

- Fixed, identical one-way and bidirectional impairment profiles.
- Reset experiment state before and after each scenario.
- Compare discovery/control repair amplification, intended app delivery, membership
  changes, and restart recovery.
- Use the owned container lifecycle and verify all required peers and routes recover.

No RF experiments or production mesh restart are needed to write this plan.

## Measurement contract

Record a run manifest: Connext version, source/config revision, topology, GUID-to-node/
DP mapping, domain, assertion period, QoS profiles, endpoint inventory, offered load,
recipient matrix, lifecycle events, and explicit capture-window boundaries.

Measure:

- Per-node and per-DP TX bytes/s, packets/s, one-second peaks, cycle bytes, and event
  burst bytes. Primary bandwidth accounting counts each transmission once.
- Discovery versus application versus reliability/control versus mixed/unknown
  frames; retain unclassified traffic rather than silently discarding it.
- Assertion/bootstrap/configuration/endpoint-discovery/response breakdown only
  where verified packet decoding supports that attribution. Correlate responses
  to preceding packets; temporal coincidence alone is insufficient.
- Whole-frame bytes without double-counting packets containing multiple submessages.
  Separate submessage-level attribution from whole-frame totals.
- Actual discovery visibility: peer participants, topic/type/QoS metadata, matched
  endpoints, and out-of-team metadata exposure.
- Endpoint creation/destruction, worker CPU/RSS, and resource-limit/rejection errors.
- First delivery, route-open/close and team-change convergence, intended delivery
  percentage, unintended recipients, duplicate samples, and restart recovery.
- Frame/fragment counts and per-recipient copies, not just application payload size.

Use one shared monotonic event origin for same-host transition measurements. Keep
wall-clock timestamps for capture correlation. Align one-second windows and record
cycle totals so staggered assertions do not masquerade as lower total overhead.
Do not sum aggregate TX and RX and call it outbound bandwidth.

## Safety and implementation prerequisites

- Leave the running mesh immutable. Stage 1 uses a verified free, isolated domain
  outside live domains 20, 30-99, and 200; every domain must be at or below 232.
- Do not run the real-router e2e suite against a live mesh. Stage 2/3 require a
  separately isolated environment or an explicitly scheduled owned teardown.
- Check the actual execution environment for Connext bindings, capture permissions,
  and required build/test tools before starting.
- Use `/tmp` for worker state, captures, logs, and caches; runner owns exact PIDs,
  cleanup, timeouts, and shutdown. Capture filters cover only the spike's DDS domain.
- Use UDPv4-only workers and bounded captures. Check owned processes and shared-memory
  remnants before/after runs without killing unrelated processes.
- Before relying on Connext MCP claims, sync/check the known-issues submodule and
  verify against bindings, QosProvider/builds, or actual runs.
- Validate new QoS XML with installed Connext; avoid dependencies/tools beyond
  what is required after a demonstrated missing-tool failure.

## Deliverables and acceptance

### Optimized goal model v2 (2026-11-08)

**Cancelled by user on 2026-11-08.** Small native validation completed before
cancellation; the full matrix was never started. Remove optimized run reports
and input archives, retain the model implementation and earlier v1 results,
and do not resume without explicit authorization.

The current correction combines pose/velocity/speed into NavigationState,
mission/task progress into MissionState, contacts/observations into
SituationalAwareness, and rendezvous/intent into CoordinationState. Minimize
compatible C2 traffic into ControlCommand and a tagged reliable ControlFeedback
envelope; keep direction and incompatible QoS separate. RouterHealth remains
its own synthetic topic/type. Six application topics plus health replace the
expanded fourteen-application-topic model. All logical fields/tags must survive
serialization and be checked against decoded intended receivers.

Create only seven schemas/topics, 12 endpoints/platform and 8 at C2 (`12N+8`),
with exactly four operational types learned at C2. Preserve v1 and historical
reporting, and reject mixed versions. Default runner model is v2, team count 3.
The N6/T2/A,B/SPDP,SPDP2 small native validation matrix finished for this
correction; the full 16-case optimized bandwidth matrix is cancelled by request.
Earlier v1 results stay unchanged and must not be presented as v2 savings.

### Expanded isolated goal-model upgrade v1 (2026-11-07)

Implement a separate goal model v1 / assignment model v3 campaign while
preserving all historical assignment model v2 reporting (18N+16 endpoints).
No HTML/slide upgrade or production integration is part of this iteration.

Required full matrix: N32; team sizes 4/8; selected team-topic counts k3/k6;
protocols SPDP/SPDP2; eager A/B; `scale_health30`; 150 s steady; two repetitions:
**32 cases**. Each includes startup/activation, stable discovery, swap/change
and settled hold, complete reassignment/change and settled hold, mission burst
and operational windows. Default settled hold is 10 s. Smoke topology requires
at least three complete teams (N6/T2); a smoke does not establish steady savings.
Teams must have at least two members so reassignment can mix prior peer groups
while changing every node's team. Expanded-model invocation (not current defaults):

```bash
python3 spikes/wan_dp_route_tradeoffs/run_scale.py \
  --goal-model-version 1 --populations 32 --team-sizes 4,8 --team-topic-counts 3,6 \
  --protocols SPDP,SPDP2 --variants AB --health-profile scale_health30 \
  --seconds 150 --repetitions 2 --parallel 2 --change-hold-seconds 10
```

For an eight-case native smoke, use the same flags with `--populations 6
--team-sizes 2 --seconds 3 --repetitions 1`. Legacy reproduction explicitly
requires `--team-topic-counts 1 --protocols SPDP2` and the historical population/
team/design options, rather than the new goal defaults.
Native effective SDK discovery masks must verify SPDP|SEDP=3 and SPDP2|SEDP=18.

Keep fourteen application types: squad-wide Pose, Velocity and Speed;
TaskProgress, MissionState and ContactReport shared with team peers plus C2 in
k3; k6 also shares SharedObservation, RendezvousState and CoordinationIntent.
Those optional three remain C2-only in k3. MissionAdd, TransferRequest,
ControlReport, TransferReport and ControlRequest remain C2-only in both.

This is a proposed **compact synthetic coordination-state** model. Use proper
state/progress/location/intent primitives, not full-plan field labels or
multi-KiB mission-plan averages for compact peer state. Target sample ranges:
TaskProgress80–160, MissionState96–256, RendezvousState96–160,
CoordinationIntent96–332, SharedObservation156–4212 (typical284), and
ContactReport100–120 bytes. Selected team rates are respectively 0.2/0.2/0.2/
0.2/0.8/0.4 Hz; squad-wide Pose, Velocity and Speed are modeled at 1 Hz.
Only unchanged squad/C2 types inherit historical size ranges/layouts. Do not
claim vendor/source-schema fidelity, calibrated TypeObject sizes or RF forecasts.

A: 2N+1 WAN DPs, squad types on position DP and operational types on main DP.
Private+team main-DP partitions gate discovery. Peer-topic pub/sub partitions
remain fixed `TEAM_DATA` (writer also `PLATFORM`); only participant partitions
change on membership updates. B: N+1 DPs, main stays `SHARED`, and every selected
topic changes both Publisher and Subscriber parent partitions. Both eagerly
enable the same endpoint inventory: 19+k per platform, 16 control, (19+k)N+16
total. Synthetic health is unchanged C2↔platform traffic, not a full production
RouterHealth roster. The 30 s health lease is not the production lease.

Swap must exchange two members across two teams. Reassignment must move every
platform to a different team while mixing prior peer groups and keeping teams
balanced. Before initial assignment, all selected team kinds reach only C2.
Each change retains authoritative before/after memberships, changed nodes,
preserved GUIDs/endpoints, before/after metadata records, measured convergence,
positive probes, zero missing/unintended probes, and a positive-send strict
settled audit. A records one DP partition update per changed node, zero endpoint
parent updates; B records zero DP updates and 2*k parent updates per changed node.

**Change-window byte totals include normal background traffic**, not isolated
serialized transaction bytes. Continuously transmitted transitional team samples
are captured but not strict-audited. Strict scope/delivery auditing begins after
convergence; ledger targets are fixed at send time using authoritative membership.
Goal ledger `expected_receivers` are stored at send time; per-worker `phase_ends`
exclude the final two seconds of each strict phase. Legacy audit behavior remains
unchanged. Control must learn exactly 12 native type identities (all 14 except
MissionAdd and Pose).
Document this explicitly, rather than claiming zero transitional leaks.

Goal reporting must require complete Cartesian coverage, native protocol proof,
the endpoint/DP model, both required change proofs, and existing source/capture
guards (execution provenance v1, capture integrity v1, accounting v2). Capture
acceptance requires owned graceful zero-drop shutdown, reconciled packet/byte
counts, and initial/final measurement boundary coverage. No retrospective
certification of old captures; keep protocols and k dimensions separate.
Use dumpcap `-B 64` (64 MiB capture buffer) for scale runs, retaining
`capture_buffer_mib=64` in the manifest and each case result alongside pinned
execution provenance. Larger buffering never relaxes the mandatory zero-drop
and graceful-completion gates. The first concurrent N32/SPDP campaign using the
default 2 MiB buffer rejected one completed case with 4,966 pcap drops; the
entire incomplete run was stopped and exact owned containers cleaned. Do not
merge any rejected/incomplete captures into accepted measurements. Validate
concurrent N32/SPDP capture pressure with the larger buffer before starting a
fresh full 32-case campaign, which must independently pass every existing gate.

Deliver concise Markdown and JSON with the goal datamodel/scope table,
setup/steady metrics per design/protocol/k/team, change-window total/discovery/
SEDP bytes, peaks, convergence, local update counts and metadata exposure.
Retain per-run proofs/windows and min/median/max. Derive conclusions from data,
handling increases, ties and zero baselines; compare architecture at fixed
protocol and protocols at fixed architecture, never pooling either dimension.
See [goal usage and scope semantics](README.md#goal-datamodel-campaign-protocol-and-membership-comparison).
Focused report validation is DDS-free (`python3 -m unittest test_goal_report`
with the spike on `PYTHONPATH`); native measurement remains a separate gate.

When implementation is authorized, add a runner, worker, external QoS XML, and
README beside this plan. Retain compact results under
`debug/test_reports/wan_dp_route_tradeoffs.*`; raw artifacts are disposable.

The report must include this table for each topology/workload:

| Variant | WAN DP count | Idle discovery TX avg/peak | Discovery bytes/cycle | Loaded total TX | Remote metadata/endpoints | CPU/RSS | Route-open/team-change latency | Unintended delivery |
|---|---:|---|---|---|---|---|---|---:|
| A | measured | measured | measured | measured | measured | measured | measured | 0 |
| B | measured | measured | measured | measured | measured | measured | measured | 0 |
| C | measured | measured | measured | measured | measured | measured | measured | 0 |
| D | measured | measured | measured | measured | measured | measured | measured | 0 |

Functional acceptance requires the exact recipient contract, preserved required
health/control paths, bounded resources, and explicit errors. Performance is a
measured tradeoff, not a predetermined pass: report whether consolidation reduces
cycle bytes and average discovery TX, whether peaks fall or merely move, and whether
on-demand endpoint churn offsets idle savings.

Recommend migration only when the same required deliveries are preserved and the
measured benefit is consistent across repetitions. If broader metadata exposure
is unacceptable, retain the two-DP design regardless of byte savings. Record route
activation latency and metadata exposure as explicit decision tradeoffs; do not
invent an operational latency threshold without user agreement.
