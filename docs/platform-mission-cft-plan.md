# Platform mission-state receive filters

Date: 2026-11-14.

Status: **implemented and reference-mesh-qualified, 2026-11-16**.
See [migration checkpoint 3.52](two-topic-wan-migration-plan.md#352-platform-mission-cfts-deployed-and-qualified-2026-11-16)
for executed gates, runtime ownership and remaining stress/fault limitations.
This is a focused follow-on to the
[Routing Service processor plan](routing-service-three-topic-plan.md).
The consolidated [processor architecture and development plan](routing-service-processor-architecture-and-plan.md)
is the entry point; this document details its platform receive-policy increment.

## 1. Confirmed behavior

The platform's mission state determines its incoming ContentFilteredTopic
(CFT) filters. "Requesting data" means changing those filters, not sending a
new application-level request.

- A **whole platform/system restart starts in Init** and receives only the
  minimum required C2 data.
- Previous mission-state filter selections are not restored after that restart.
- Subsequent local mission-state changes change the platform's receive filters.
- Routes, bindings, Event/State/Periodic endpoint pairs, channels and QoS remain
  static.
- C2 does not switch routes or choose a platform's receive profile.
- There is no RequestData/ReleaseData protocol, lease, requester registry or
  request replay mechanism.

This concerns **platform receive interests**. It does not undo the approved
always-on outgoing platform reports or reintroduce Init/Mission/Debug
report-route switching. Existing C2 receive behavior remains unchanged.

**Init is the platform's startup state, not an incoming topic name.**
PlatformStatus (formerly PlatformInitStatus) is an outgoing
platform-to-C2 report; it is not part of the
platform's default WAN receive selection. Observing local state on the platform
LAN is separate from subscribing to another platform's WAN reports.

The C2-to-platform application names are **Mission** and **Team**.
The confirmed migration is ControlCommand to Mission and ActTeamAssignment to
Team. Those are the startup application selections; required internal traffic
remains separate. Active source/configuration/test topic names have been migrated;
the payload type identifiers remain unchanged.

## 2. Confirmed policy

The native `act.receive_policy=mission` model maps authoritative local mission
states to Init and ActiveMission. Every selected application topic has a
static binding/type. Profile membership does not introduce another QoS authority.
Explicit `act.receive_policy=static` retains catalog/binding acceptance coverage;
it is not the production platform mission policy.

| Situation | Receive behavior | Decision status |
| --- | --- | --- |
| Whole platform startup/restart | Init: Mission and Team, plus required internal traffic | Confirmed |
| TRANSIT, ORBIT, RTB or HOLDING | Keep Mission/Team; add fleet-wide PlatformStatus positions, plus PlatformMissionStatus and ContactReport from other platforms in the same team | Confirmed: one active-Mission profile |
| IDLE, COMPLETE or CANCELLED | Return to Init interests | Confirmed |
| No valid local mission-state sample yet | Remain at startup Init interests | Confirmed startup rule |
| Unknown state or invalid update | Diagnose explicitly; do not broaden the last successfully applied filters | Confirmed runtime rule; invalid deployment policy fails validation |

The minimum set must allow the control/coordination needed to leave Init.
Health and internal configuration traffic must retain their required behavior;
an empty application selection must not accidentally disable the health branch.

The extra mission data comes directly from same-team platforms, not C2.
Map the user's "mission status" and "contact reports" to the existing
PlatformMissionStatus and ContactReport topics; no additional rename is agreed.
Preserve existing report delivery to C2 while adding the peer team audience and
static platform receive bindings as needed. Do not create a C2 relay or change
command octet payloads. An unassigned active platform receives no team-scoped
mission/contact data, but still receives fleet navigation positions.

Live qualification exposed an existing collision-avoidance dependency:
the simulator stops with `WAITING_PEERS` without fresh fleet positions. The user
approved adding existing PlatformStatus from fleet peers to ActiveMission,
across teams, rather than weakening that safety check or changing payloads.
Init still excludes PlatformStatus. There is no new navigation topic.

## 3. Implementation approach

### A. Extend the existing deployment model

Validated receive-profile membership and state mappings are implemented in the
native XML property model in [GatewayModel](../router/routing_service/GatewayModel.cxx).
Keep the existing full-type bindings, identity rules and manual port QoS.

Apply Init interests when creating the platform's WAN readers, **before they
can receive application traffic**. Do not start with broad interests and narrow
them later. Control-role configuration must retain its existing behavior.

### B. Observe local mission state

Use existing local PlatformMissionStatus rather than a new WAN request topic.
The platform helper already observes PlatformMissionStatus:
[platform_mesh_control.py](../harness_v2/scripts/platform_mesh_control.py).
The processor also consumes that LAN topic for forwarding:
[GatewayProcessor](../router/routing_service/GatewayProcessor.cxx).

Preferred approach: observe the authoritative local state within the processor
and apply policy on its existing serialized processing path. Validate local
origin and reject stale/invalid state updates. Received peer mission reports must
not become the local platform's state authority. Do not use C2's cached mission
view as the platform's state authority.

### C. Update the existing CFTs

Compose profile selection with the existing audience, address, team,
source-suppression and internal-traffic predicates. A profile must narrow
allowed data, not bypass those existing rules.

Reuse the existing CFT-update path used for team changes in
[GatewayMonitor](../router/routing_service/GatewayMonitor.cxx), rather than
creating readers/writers or toggling routes. Coordinate team and mission-state
changes so one update cannot overwrite the other's interests.

Validate a complete update before applying it. Report failures and retain the
previous coherent state where rollback succeeds. A rollback failure must be
visible; do not advertise a successful state/profile change.

CFT changes are not a fleet-wide atomic barrier. Define and test treatment of
already-accepted/queued samples. Do not promise instantaneous queue removal or
an initial historical snapshot merely because the filter changed.

### D. Expose the applied state

RouterHealth exposes `receive_profile`, `receive_state` and
`receive_filter_error`, separately from its outgoing `reporting_policy`.
The dashboard displays those values and the selected application classes.
The existing ALL report badge means outgoing reporting, not "receiving all C2 data."

Application delivery audits retain the successfully applied Init/ActiveMission
and team selections as node-owned `receive_policy` events. Recipient expectations
use that history at the sample send timestamp, not the final mission state.
Missing policy history is reported as incomplete evidence; it must not fabricate
loss for deliberately excluded peers or excuse missing deliveries to selected peers.
The dashboard's primary WAN wire Delivery metrics remain independent of this
application audit.

## 4. Restart and teardown scope

The required restart case is the **whole platform system**, including its local
mission-state producer/controller, not merely restarting Routing Service while
the application continues a mission.

After restart:

1. The local system starts in Init.
2. The WAN readers start with Init filters.
3. Discovery and health recover.
4. A new valid local state change can select another profile.

Do not persist/replay the old mission filter selection. A router-only restart
while the local controller remains active is a separate case whose behavior
must be agreed and tested; it must not be claimed as whole-system Init coverage.

Keep owned lifecycle cleanup and callback/loan safety. Do not add ad hoc
individual-router restarts to the retained live mesh.

## 5. Qualification gates

1. **Model/configuration:** valid profile mappings, missing/unknown topics,
   invalid states, control-role invariance and correct Init startup predicates.
2. **Actual DDS filtering:** publish included and excluded C2 data concurrently;
   verify platform delivery changes with local state, while unrelated platforms
   and outgoing reports remain unaffected.
3. **Endpoint stability:** verify the same three shared WAN pairs throughout
   state/team changes; check native writer filtering and protocol counters,
   not only application-side drops.
4. **Transition behavior:** repeated states, stale samples, rapid changes,
   simultaneous team changes, update failure/rollback and queued samples.
5. **Whole-platform restart:** in an owned isolated fixture, keep C2 and another
   platform running; tear down/restart the complete target platform stack.
   Verify Init filters, no restored mission selection, health recovery and
   successful subsequent state transitions.
6. **Regression:** rerun the existing processor checks and all five live mission
   workflows. Preserve always-on report assertions; add explicit receive-profile
   and restart checks.

Run isolated tests before a coordinated live deployment. Earlier mesh results
in [migration checkpoint 3.51](two-topic-wan-migration-plan.md#351-xml-only-processor-deployed-and-mesh-qualified-2026-11-14)
are the preceding baseline, not evidence for this increment. Current evidence:
9 C++ targets; 70 native/configuration tests plus 55 subtests, including complete
platform-stack restart; and all five live browser workflows passed, zero skips,
375.89 s. The browser also verifies applied Init/ActiveMission state separately
from always-on reports and the current asset revision.

## 6. Remaining qualification

The implemented policy preserves the three WAN pairs and C2 report reception.
Native tests cover state/team composition, invalid and stale local updates,
peer-vs-local authority, lifecycle and full simulator/controller/router restart.
A production-path model-replacement regression executes 24 transitions with
immediate forwarding and continued health. The old binary failed this check;
the rebuilt processor passes without sample/callback errors.

Queue-draining semantics and sustained rapid-transition stress remain unqualified.
Rollback-failure tests inject callbacks through production transaction logic;
they are not native DDS fault-injection evidence. Do not claim atomic queue
removal, snapshot replay, fleet-scale load budgets or general multi-producer
recovery from this gate.
