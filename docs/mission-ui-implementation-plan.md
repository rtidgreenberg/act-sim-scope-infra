# Mission UI implementation plan

> **Status:** Implemented; validation evidence recorded in [mission-ui-review.md](mission-ui-review.md).
>
> **Architecture:** [mission-ui.md](mission-ui.md)
>
> **Goal:** Exercise the existing DDS router, platform simulator, dashboard bridge,
> and EMANE framework through a live five-boat San Diego mission scenario.

## Scope and constraints

The autonomous-navigation follow-up makes goals team-owned and inherited on
confirmed reassignment. C2 sends goal state, while sims water-plan and maintain
91.44 m buffers from cross-team DDS location reports. A dedicated `NAVIGATION`
partition preserves team-application isolation. Missing/stale fixes hold motion;
restart intentionally resets the scenario. See the architecture's onboard
separation section for assumptions and limits.

The deliverable is a Mission workspace integrated with the existing Mesh, Delivery,
and Experiment workspaces. It must use the existing `ControlCommand`,
`PlatformCommandAck`, `TeamAssignment`, and platform status topics; it must not
introduce a browser-only mission-control path.

The initial topology is one control node and five simulated boats. The Mission map
uses an offline dark Southern California/San Diego visual, with no external map-tile
dependency.

## Phase 0 - Contracts and repeatable scenario data

### Implementation

1. Define the versioned JSON payload carried in `control_command.payload` for:
   `route`, `replace_route`, `cancel`, `return_to_base`, and `set_time_scale`.
   The implemented bounds are 64 waypoints and 8192 payload bytes; regenerate
   `ActTypes.xml` and rebuild/restart all DDS endpoints when changing the IDL bound.
2. Define validation limits for commands: target list, waypoint count, coordinate
   ranges, command ID, and payload size.
3. Give every simulated boat a stable display name, launch coordinate, and
   return-to-base coordinate near San Diego Harbor.
4. Define the mission lifecycle: `IDLE`, `TRANSIT`, `HOLDING`, `COMPLETE`,
   `CANCELLED`, `RTB`, and explicit continuous `ORBIT`.
5. Record Alpha, Bravo, and Reserve as the initial squad choices. Runtime squad
   state and missions reset when the mesh restarts.

### Tests and exit criteria

- Unit-test valid and invalid mission-command payload parsing.
- Unit-test coordinate, waypoint-count, and command-type rejection paths.
- Verify every configured boat has a unique launch position and valid home position.
- Verify no new DDS topic/type is needed and the payload fits the enlarged bounded
  `control_command.payload` sequence. Test 64-waypoint encode/decode, the exact
  8192-byte DDS sequence bound, and rejection of 65 waypoints/8193 payload bytes.

**Exit:** A route command contract is documented, validated, and independently
parsable by the bridge and simulator.

## Phase 1 - Simulator mission execution

### Implementation

1. Extend `platform_sim.py` to consume the mission payload from `ControlCommand`.
2. Send a `PlatformCommandAck` for accepted and rejected commands with a useful
   reason on rejection.
3. Replace random mission/waypoint samples with mission-state-driven samples when a
   route is active. The UI must consume those live DDS reports, not mock mission data.
4. Advance simulated positions at 10 knots along acknowledged route legs.
5. Implement cancel, hold-at-final-waypoint, and return-to-individual-launch-point
   behavior.

### Tests and exit criteria

- Focused simulator tests for route acceptance, replacement, cancellation, RTB, and
  final-waypoint completion.
- Route a command through a control/platform router pair and assert one
  acknowledgement per addressed platform.
- Assert `PlatformInitStatus`, `PlatformMissionStatus`, and
  `PlatformWaypointStatus` report coherent position, phase, and waypoint index.
- Assert an unaddressed platform does not change mission state.

**Exit:** A live simulated platform acknowledges commands and reports the resulting
route progress over existing DDS topics.

## Phase 2 - Dashboard bridge mission APIs

### Implementation

1. Add a validated mission-command writer to `mesh_bridge.py`.
2. Add read APIs for the live mission inventory, current squad membership, mission
   status, waypoint status, and recent command acknowledgements.
3. Broadcast mission-state updates through the existing WebSocket connection.
4. Derive the inventory from current platform/mesh status; do not retain a
   browser-owned roster.
5. Keep `TeamAssignment` as the persistence/control path for squad changes.

### Tests and exit criteria

- API tests reject malformed requests with explicit `400` responses.
- API tests verify one DDS write per selected platform and unique command IDs.
- Browser/API test verifies inventory changes when a platform appears or disappears.
- Browser/API test assigns a platform to a squad and observes the corresponding live
  team state.
- Verify bridge audit polling remains restricted to the active run and does not
  regress into replaying retained logs.

**Exit:** The dashboard exposes an authoritative mission inventory and can issue
validated commands through the live DDS path.

## Phase 3 - Mission workspace

### Implementation

1. Add the Mission tab and dark offline San Diego map canvas.
2. Render team-colored triangle boats at live reported coordinates. Keep icons
   screen-sized, use clickable count clusters instead of overlapping/displacing
   boats, and offer Zoom to boats. Show P-number labels inside zoomed icons.
3. Render inventory, Alpha/Bravo/Reserve squad assembly, health, battery, and
   current mission.
4. Implement default left-drag panning and click/Shift-click boat selection,
   fixed-team assignment, team selection, right-click Set mission with Draw
   waypoints / Select target choices, waypoint drag/removal, clear,
   explicit Validate mission, Go, replacement, cancel, and water-planned RTB.
   Validation does not publish commands; edits invalidate the plan.
5. Render planned routes as dashed blue paths and acknowledged routes as solid blue
   paths with numbered waypoint markers.
6. Show per-boat acknowledgement and rejection feedback.

### Tests and exit criteria

- JavaScript/unit tests cover coordinate conversion, route editing, and payload
  generation.
- Playwright verifies the Mission tab loads without console errors and without
  external tile requests.
- Playwright draws, drags, and removes waypoints, then confirms the generated
  command targets the selected boats.
- Playwright verifies acknowledgement changes route styling from planned to active.
- Screenshot review verifies dark theme, blue boat triangles, route lines, waypoint
  markers, and degraded/disconnected color states.

**Exit:** An operator can create and command a route for one or more live simulated
boats entirely from the Mission workspace.

## Phase 4 - Cross-workspace synchronization

### Implementation

1. Add squad-color rings, mission phase, and route-progress badges to Mesh nodes.
2. Preserve Mesh topology, edge health, and presence semantics as network facts.
3. Make Delivery highlight the selected mission command and its per-boat
   acknowledgement flow.
4. Populate Experiment source/destination selectors from the same live platform
   inventory.
5. Share live mission context through the existing dashboard push socket with REST
   fallback; do not create a browser-only mission or acknowledgement model.

### Tests and exit criteria

- Integration test sends a route and verifies Mission, Mesh, Delivery, and
  Experiment expose the same live platform set.
- Playwright verifies mission phase and waypoint progress update on the Mesh node
  without altering its topology position.
- Delivery test verifies each selected boat has an independently tracked
  `ControlCommand`/`PlatformCommandAck` flow.
- Apply a targeted CommEffect impairment and verify only affected mission delivery
  and network-health displays degrade.

**Exit:** All dashboard workspaces present consistent live state for one scenario.

## Phase 5 - Full five-boat demonstration and resilience

### Implementation

1. Launch the canonical five-platform EMANE mesh.
2. Create at least two squads, issue independent routes, then reassign a boat.
3. Demonstrate route replacement, cancellation, completion, and individual RTB.
4. Add bounded retention for mission UI state and verify restart resets to the
   initial scenario.

### Tests and exit criteria

- Canonical lifecycle launches one control plus five platforms and the dashboard.
- E2E scenario verifies five boat inventory entries, squad assignment, route
  acknowledgement, animated progress, and completion.
- E2E scenario reassigns one boat and verifies its old and new squad state across
  Mission and Mesh.
- E2E scenario applies and resets a one-link impairment, then confirms all
  unaffected boats and routes remain healthy.
- Restart the mesh and verify squads, missions, and route overlays reset while the
  platform roster repopulates.

**Exit:** The five-boat scenario can be run repeatedly as a live end-to-end
demonstration with verifiable command, routing, and degradation behavior.

## Validation order

Run the smallest phase-specific checks first. Do not start a live mesh solely for
work that can be validated in-process. For the final scenario, use the canonical
containerized EMANE lifecycle and validate both REST/WebSocket state and the rendered
browser UI.

## Existing test-suite integration

Mission work extends the repository's existing test layers; it must not create a
second runner or standalone test topology.

| Phase | Existing suite integration |
|---|---|
| 0 | Add payload and state-transition unit coverage beside the existing C++ controller tests in `router/test/` when the behavior is router-owned; keep simulator-only parsing tests in the existing Python test layout. |
| 1 | Extend the real-router Python e2e suite in `router/test_e2e/`, following `test_control_command_route.py`, `test_platform_events.py`, and `test_control_platform_full.py`. |
| 2 | `test_mission_contract.py` covers bridge validation, publication, acknowledgement correlation, history bounds, timeout, stale inputs, partial HTTP results, and canonical delivery-audit matching; use pytest temporary directories. |
| 3 | `test_mission_map.py` exercises the bundled real geography in Chromium. `test_mission_workflow.py` drives the real canonical five-boat mesh using `MISSION_DASHBOARD_URL`; it does not launch a second topology or fake responses. |
| 4 | `test_mission_workflow.py` checks shared team context, unchanged six-node Mesh roster, per-boat command/ack Delivery rows, Experiment squad labels, and GUI CommEffect apply/reset. Existing router link/presence tests remain unchanged. |
| 5 | Run `bash router/run_tests.sh`, then the focused mission e2e selectors, and finally `python3 -m pytest -q -p no:cacheprovider router/test_e2e` in the validated Connext environment. |

The dashboard tests must use the bridge's real REST and WebSocket APIs. Browser-only
mocking is insufficient because the mission scenario exists to exercise command routing,
acknowledgements, status publication, and delivery accounting end to end.

Deterministic simulator fixtures are permitted only within automated tests to make route
timing reproducible. They must use the same `ControlCommand`, acknowledgement, and
platform status DDS path as the live scenario; remove or replace tests that assert
browser-only synthetic mission data.

### Phase-specific gates

| Phase | Tests |
|---|---|
| 0 | DDS 8192-byte capacity, 64-waypoint round trip, malformed targets, payload/waypoint limits. |
| 1 | Motion-clock reset, idempotent redelivery/cancel/rejection, real DDS acknowledgements, active progress, completion and RTB. |
| 2 | Freshness/presence, fixed confirmed squads, plan expiry and Go rechecks, pre-write registration, source/run correlation, timeout/late ack, partial errors, bounded histories, audited Delivery matching. |
| 3 | Four real-coastline browser geometry tests plus live click/Shift-click selection, default panning, fixed-team and mission-choice menus, waypoint dragging across updates, validation without publication, Go, and visible controls. |
| 4 | Live Mission/Mesh/Delivery/Experiment consistency; actual audited command/ack flows; GUI CommEffect apply/reset and alive peers. |
| 5 | Live replacement, cancel, individual water-only RTB, reassignment, and canonical restart reset. |

Run offline geometry with a host Chromium installation and contracts in the
validated Connext environment:

```bash
python3 -m pytest -q -p no:cacheprovider router/test_e2e/test_mission_map.py
python3 -m pytest -q -p no:cacheprovider router/test_e2e/test_mission_contract.py
```

The live browser gate is opt-in because it publishes real commands and temporarily
changes teams and EMANE profiles. Launch a fresh owned five-platform demo first;
the two-target story requires unrevealed targets:

```bash
MISSION_DASHBOARD_URL=http://localhost:8080 MISSION_PLATFORM_COUNT=6 \
  python3 -m pytest -q -p no:cacheprovider router/test_e2e/test_mission_workflow.py
```

The live gate supports five or six platforms; set `MISSION_PLATFORM_COUNT` to match
the launched mesh (default five). Mission commands accept up to six recipients.
The multi-team scenario includes every platform in the fleet.
The live gates also cover late joins inheriting an active team goal,
active-to-active reassignment without restarting other members, and leaving a
team stopping the joining boat's old mission. The loiter gate samples water-only
positions through a full 2 km-radius circuit; continuous separation is checked
separately by the navigator contracts.
It restores the affected boats' original teams and demo clocks, cancels its missions, and resets
EMANE in teardown. It does not clear the target reveals; restart the owned mesh
before repeating the complete story gate. Do not point it at a shared operational deployment. A default
router pytest run skips this gate; run it separately on the live demo. Run the
isolated real-router suite only with the live mesh stopped.

## Guided two-target demo extension

- Reveal actual ContactReports through the Scenario controller; require an accepted
  Target 1 dispatch before revealing Target 2.
- Dispatch Alpha and Bravo independently with selectable 500 m / 1 km / 2 km /
  3 km loiter distances, defaulting to 1 km. Carry the distance through validated
  plans and command metadata; encode the real circuit as DDS route waypoints.
- Support repeated simulator circuits with compact payload `l` (loop start).
  Cancel/RTB/replacement must remain isolated to the addressed boats.
- Apply 1x/20x time through actual DDS commands; retain real audit/ack clocks and
  reported 10-knot speed.
- Keep row selection highlighted, simplify next-step hints and hide irrelevant
  controls. Keep markers at actual coordinates without tethers or camera chasing.
- Extend the two live browser workflows to verify five boats, both team colors,
  different loiter distances, actual accelerated motion, another full circuit,
  selection/cluster zoom, invariant icon size, true-position transforms, and
  isolated cancellation/RTB/replacement. Retain stage screenshots with
  `MISSION_SCREENSHOT_DIR`.
