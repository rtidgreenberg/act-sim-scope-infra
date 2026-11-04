# Mission UI implementation review

Reviewed 2026-11-03 against the [architecture](mission-ui.md) and
[implementation plan](mission-ui-implementation-plan.md).

## Autonomous team-goal follow-up (2026-11-04)

- C2 publishes authored team goals; sims compute their own water navigation and
  local passing maneuvers. Whole-team validation includes every confirmed member.
- Shared goals become active after DDS acknowledgements. Late joins inherit only
  on the joining boat; active-to-active reassignment preserves other mission IDs;
  joining an idle team or Unassigned stops the old mission.
- `PlatformInitStatus` crosses teams through `platform_locations_to_wan` and
  `wan_locations_to_platform`, using the separate `location_wan` participant on
  domain 200 and protected `NAVIGATION` partition. Team-only data stays isolated.
- Location delivery receipts are audited. New immutable manifests explicitly
  declare shared locations, with source-excluding recipients; legacy expectations
  remain readable.
- Sims enforce a 91.44 m minimum with bounded movement, age-expanded peer-location
  uncertainty, persistent passing detours, coastline checks, and loiter standoff.
  Missing/stale required peer fixes hold navigation, with a visible status reason.
- Clock confirmation uses actual simulator telemetry; speed and heading reflect
  movement/holds. All 70 supported platform IDs have distinct launch positions.
- Scenario activation is display-only, Demo time is in the Scenario bar, mission
  destination/radius are draft-local, and highlighted walkthrough hints follow
  actual state. Mission creation expands a selected member to its entire team.

### Follow-up validation

- Regenerated DDS XML and rebuilt the router successfully.
- C++ in-process gate: **4/4 passed**.
- Focused Python gate: **84 passed** (72 mission contracts, 8 navigator cases,
  4 immutable delivery-report cases).
- Navigation cases verify head-on, crossing and overtaking traffic, continuous
  swept separation, five-boat convergence without collapse, stable final stations,
  missing/stale/invalid fixes, unique launches, and multiple loiter circuits without
  violating target standoff.
- Real headless Chromium and five-platform EMANE check passed: whole-team Go from
  one selected boat, Alpha/Bravo goals, real 20x telemetry, late join, active-team
  reassignment, idle-team stop, unchanged other-member mission IDs, and all four
  active boats reaching ORBIT.
- Across **367 live inventory samples**, the smallest observed reported separation
  was **196.68 m**, above the required 91.44 m. No page/console errors occurred.
  This complements the continuous swept-motion unit checks; sampled live fixes
  alone are not a proof of continuous separation.
- A final clean-run immutable delivery audit passed **479/479 expected deliveries**
  with shared-location recipients enabled. That check exposed redundant 1 Hz
  downsampling on the simulator's routed-location reader; using the existing
  unfiltered `lan_status` reader profile eliminated the dropped fixes. WAN source
  downsampling remains unchanged.
- The operator requested a clean restart, not restoration of the previous missions.
  Compact live evidence is retained in `debug/test_reports/autonomy-live.json` and
  `debug/test_reports/autonomy-live.png`.

The simulation assumptions and communication-failure limits are documented in
[Onboard separation](mission-ui.md#onboard-separation). Earlier gates below are
historical, not a claim that the unrelated full router e2e suite is green.

## Scope

This review covers the Mission UI, mission-command bridge, platform simulator, DDS
command payload, existing Mesh/Delivery/Experiment integration points, and mission
test coverage. It is not an audit of the entire router repository.

## Changes completed during review

- Replaced the invented coast/streets with bundled public-domain Natural Earth
  land polygons covering a larger San Diego coastal region.
- Preserved the reference image's dark tactical appearance, blue/teal glowing
  triangle boats, and compact labels; removed red restriction overlays.
- Added automatic water-only detours with grid search and line-of-sight
  simplification. Offshore launches prevent initial boats being plotted on land.
- Fixed map clicks to use the actual SVG transform, including letterbox margins.
- Fixed Cancel/Return-to-Base requests to omit the forbidden waypoint field.
- Added a visible Replace route control and per-boat command state.
- Replaced misleading missing battery values of 0% with "Battery unknown."
- Expanded the existing DDS payload from 100 to 8192 bytes and both validators
  from three to 64 waypoints; regenerated XML and rebuilt the router.
- Fixed malformed target arrays that previously could throw an unhandled
  unhashable-type error.
- Mission commands now request mission-level status for init-mode platforms,
  preserving debug mode when already selected.
- Moved the transport badge so it no longer overlaps the Experiment tab.

## Review findings resolved

| Original priority | Resolution | Regression evidence |
|---|---|---|
| High: missing mission audit | Active run identity and canonical sent events use the command's actual sequence; Delivery includes mission sends and simulator acknowledgements. | Contract audit matching and live per-boat ControlCommand/PlatformCommandAck rows. |
| High: missing squad/graph integration | Rectangle/Shift selection, fixed Alpha/Bravo/Reserve/Unassigned menus, confirmed teams, team-based drafts, and graph squad/phase/progress overlays. Delivery and Experiment share selection context. | Live GUI assignment, unchanged six-node roster, Mission filtering and Experiment squad labels. |
| Medium: cached availability | Current presence plus mesh/position freshness determine availability; UI shows position age; stale/missing targets block validation/publication. | Presence/freshness contract cases. |
| Medium: frontend-only planning/direct RTB | Backend plans real coastline detours for Validate, Go, legacy requests and individual RTB; rejects land endpoints and out-of-bounds coordinates. | Real-geometry detour, rounded-coordinate checking, live validation and RTB. |
| Medium: unbounded histories | Command/ack/plan retention is bounded to 200 each; unrelated acknowledgements are excluded. | History bound and source/run correlation cases. |
| Medium: missing timeout/outcome | Pending, accepted, rejected, timed-out and failed states; 15-second acknowledgement timeout, late-ack labels and rejection explanations. | Timeout/late-rejection contracts; live accepted commands. |
| Medium: registration race/partial writes | Serialize IDs, register before write, retain per-target failures, return 207 for partial results; expected write/audit exceptions are logged and unexpected programming errors propagate. | Immediate-ack, parallel-ID, partial HTTP and error-logging cases. |
| Medium: motion clock | Reset clock on new commands and preserve state during reliable redelivery; explicit RTB home endpoint validation. | Motion reset and idempotency cases. |
| Medium: missing scenario regression | Added opt-in Playwright workflow to the existing router suite, using the owned mesh and actual APIs/DDS reports, not intercepted mission/status data. | Live team/detour/Go/replacement/cancel/RTB/reassignment/cross-tab/CommEffect workflow. |
| Low: interaction/redraw | Stable inventory/SVG elements, draggable/removable waypoints, zoom/pan/Fit coast, Backspace/Escape editing and persistent team selection. | Drag survives a live refresh; edits disable Go until revalidation. |

## Demo map limitations

Natural Earth 1:10m data is real geography but deliberately generalized, particularly
around small harbor features. The grid and sampled segment tests avoid this map's
land polygons, not submerged hazards, depth constraints, dynamic obstacles, or a
specified coastal clearance. The UI labels the map as demo-only.

Data provenance: [Natural Earth public-domain terms](https://www.naturalearthdata.com/about/terms-of-use/)
and [source land GeoJSON](https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_10m_land.geojson).
The committed regional GeoJSON includes the source URL and clipping bounds and is
served locally; there are no runtime external tile requests.

## Verified gates

- Router rebuild succeeded after regenerating the DDS type XML.
- Existing C++ suite: 4/4 passed using `router/run_tests.sh`.
- Focused mission contracts: 64 passed, including 64-waypoint round trip through
  the actual installed DDS DynamicData type, validation/API/audit/timeout/error
  regressions, and simulator motion/idempotency.
- Added real-clock/20x behavior, continuous circuit execution, target ordering and
  DDS reception, all four loiter distances, distance-preserving circuit legs,
  metadata/payload consistency, and invalid-distance rejection.
- Existing delivery-audit tests: 3 passed.
- Offline Chromium geometry tests: 4 passed, including a real coastline-crossing
  detour and offshore launch checks.
- Live five-platform EMANE mesh: all six containers relaunched.
- The live workflow gate passed against actual five-platform EMANE containers:
  drag-selection, confirmed Alpha assignment, team Create mission, backend coast
  detours, waypoint drag invalidation, two distinct DDS acknowledgements, active
  routes, cross-tab context, audited Delivery, GUI CommEffect apply/reset, replacement,
  cancellation, individual RTB/completion and Bravo reassignment.
- A 64-waypoint command sent through the live DDS route was also acknowledged by
  Platform_31 during the earlier payload expansion check.

## Guided demo and zoom refinements

- Shared Mesh/Mission fills identify Alpha, Bravo, Reserve and Unassigned.
  Zoomed icons show P-number identities, with P20 explicitly a fixed shore base.
- The Scenario controller publishes two actual DDS contacts. Each team receives
  its own acknowledged, continuous loiter mission around an unknown demo target.
- Loiter distance defaults to 1 km; 500 m, 1 km, 2 km and 3 km are selectable before
  validation. The circuit's straight legs preserve the chosen distance. Coast
  avoidance and distances remain demo-level, not marine navigation guarantees.
- Actual simulator time supports 1x/20x, while reported speed and audit/ack clocks
  retain their original semantics.
- Clicking/keyboard-toggling a boat row highlights selection without a Select
  button. Short step hints and contextual buttons make the next action explicit.
- Removed the interim displaced-marker/tether approach. Screen-sized true-position
  icons resolve with zoom; crowded groups show a clickable count anchored to a
  member's actual position. Zoom to boats is explicit, so motion does not drag
  the camera or make individual icons jump.
- Both live browser workflows passed, along with all four geometry tests.
  The extended workflow verified different 1 km/2 km team circuits, five boats
  in actual ORBIT, continued motion beyond another complete circuit, matching
  Mesh colors, screen-size invariance within 0.01 pixels, count-marker zoom,
  true-position transforms without tethers, isolated cancellation/RTB and
  Bravo replacement without changing the remaining Alpha missions.
- Screenshot review exposed overly thick route/grid/coast strokes at close zoom.
  These now use non-scaling strokes, and in-icon P-number labels have stronger
  contrast. Final screenshots are retained under
  `debug/test_reports/mission-scenario-ui/`.
- The final browser smoke loaded `true-position-clusters-v2`, verified
  non-scaling route strokes, stable icon size, no position lines, true-coordinate
  transforms, readable separated icons, and no page errors. The retained demo
  has Alpha P32/P33/P34 orbiting Target 1 at 1 km and Bravo P30/P31 orbiting
  Target 2 at 2 km, with actual 20x simulator time confirmed on all five boats.

## Latest interaction refinements

- All mission paths are dotted, including accepted travel, RTB and loiter circuits.
- Left-drag pans by default, including from a boat icon; click selects one boat and
  Shift-click adds/removes boats. Removed box-selection mode.
- Right-click Set mission offers Draw waypoints or Select target; only revealed
  targets are offered. Team assignment omits already-confirmed identical membership.
- Menus and buttons share availability rules. An editable waypoint draft offers
  editing/Validate, a target draft offers Validate, and a validated draft shows only
  Go below the inventory. Cancel/RTB are hidden during drafting and shown only for
  applicable live state. Open menus close when their availability becomes stale.
- A live, non-publishing browser check verified default pan, Shift-click add/remove,
  both mission-choice branches, correct menu/button contents, validation, retained
  validation when right-clicking the same team, and invalidation after changing
  loiter distance. The check left DDS command history unchanged and had no page errors.

## Wider router suite: not green

The full isolated Python router run reported **61 passed, 3 failed, 5 skipped**.
The five browser skips in the Connext container were covered separately on the
host (four geography tests and the opt-in live workflow).

The three non-mission tests also failed when rerun separately:

- `test_auto_qos_create_and_observe_summaries_warnings_and_tightening`: writer
  deadline remained infinite instead of tightening to 500 ms.
- `test_link_stats_wire_frugal`: packet parsing observed no WAN topics.
- `test_qos_alias_route_resolves_and_forwards`: no route status was observed.

These failures remain outside this mission implementation; the full router gate
is not claimed green. Investigate them separately before treating the repository
as fully regression-clean.

## Independent review corrections (2026-11-04)

The follow-up review reproduced and corrected five defects:

| Priority | Defect | Correction |
|---|---|---|
| High | Reassignment before mission telemetry could leave a boat executing its previous team's goal. | Register in-flight ownership at dispatch, cancel departing goals even with old IDLE telemetry, and never overwrite a newer follower when late acknowledgements commit a batch. Preserve original recipients if membership changes during publication. |
| Medium | A missed acknowledgement push could leave controls pending indefinitely while inventories continued arriving. | Periodically reconcile command history, preserve newer pushes received during REST requests, and clear obsolete commands on an authoritative fresh-run snapshot. |
| Medium | Command-history eviction erased active target/radius metadata, route previews, and Target 2 eligibility. | Retain current mission records per boat and scenario dispatch facts independently of the 200-record history. |
| Medium | Rejected RTB commands modified the active navigator's peer and loiter guards. | Finish RTB validation and planning before changing navigation state. |
| Medium | A failed replacement hid the still-running dotted route. | Render the command matching the reported mission ID, with retained active-command metadata as the source. |

Regression cases cover delayed acknowledgements and telemetry, departure and
return during pending dispatch, joining a pending team, membership changes during
publication, history eviction, rejected RTB planning and endpoint validation, missed
browser pushes, stale REST responses, scenario reset, and route continuity.
Browser regressions use an isolated origin and the actual versioned mission
JavaScript and bundled coastline; they do not publish commands to a live mesh.

Follow-up validation passed: **94** mission-contract/navigation/delivery tests
(82 mission contracts, 8 navigator cases, 4 delivery-report cases) and **11**
isolated Chromium cases (4 geometry, 7 mission-state cases), **105 total**.
The browser cases also syntax-check all four mission/integration scripts and
verify that the current versioned mission asset is loaded.

At the operator's request the owned five-platform EMANE mesh was torn down for
these corrections and was not relaunched. Earlier live-run evidence above is
historical, not a live verification of these follow-up changes.
