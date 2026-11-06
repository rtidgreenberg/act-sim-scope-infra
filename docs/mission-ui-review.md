# Mission UI implementation review

Reviewed 2026-11-03 against the [architecture](mission-ui.md) and
[implementation plan](mission-ui-implementation-plan.md).

## Commit-readiness fixes (2026-11-06)

- Accept discovery `PID_SENTINEL` regardless of its length, as required by
  DDSI-RTPS 2.3 section 9.4.2.11. Both byte orders and ignored trailing bytes
  have regressions; missing terminators and truncated policies still fail.
- Preserve cumulative parser-error diagnostics, but use the error-counter delta
  to assess the current capture interval. A counter reset restarts coverage.
  Regressions keep the lifetime total nonzero and verify that loss and recovery
  become available only after a complete clean rolling window. Traffic capture
  health also recovers without clearing historical diagnostics.
- Fresh isolated SDK build: **4/4 native suites passed**, and committed type XML
  exactly matched regeneration from IDL.
- Binding-enabled wire/accounting gate: **57 tests passed**. In-process
  mission/reporting gate: **108 passed, 1 real-router test deselected**.
- Host parser/accounting/browser gate: **61 tests, OK with 15 skips reported**.
  Diff whitespace and modified launcher shell syntax checks passed.
- These checks did not restart or change the running six-platform mesh. The full
  real-router e2e suite was not rerun against that live mesh; the capture fixes
  require a subsequent owned restart to take effect in the running backend.

## Best Effort RouterHealth (2026-11-05)

- Changed both `router_health` endpoints to Best Effort in external XML.
  Kept 1 Hz publication, KEEP_LAST(1), durability, and presence timing unchanged.
  Commands, acknowledgements, and contact reports remain Reliable.
- Health probe tests now load the production XML profiles instead of hard-coding
  Reliable endpoints. Added a profile regression covering health and event QoS.
- Installed Connext validation, **4/4 native tests**, and **3 real-router presence
  tests** passed, including stale detection, process death, rejoin, and config drift.
- Coordinated six-platform restart and live Chromium verification confirmed all
  **12 RouterHealth WAN flows** under Best Effort with the **2 s** Delivery grace.
  Restored the operator's 10% loss in both Control_20/Platform_30 directions;
  all six peers were ALIVE/ROUTER_OK after verification.

Retained results: [presence tests](../debug/test_reports/router-health-best-effort-20261105.xml)
and [live QoS/GUI verification](../debug/test_reports/router-health-best-effort-live-20261105.json).

## Reliable WAN recovery timing (2026-11-05)

- Added **Avg recovery** to topic rows: original WAN send to complete WAN receipt
  for samples received after a confirmed retry. Confirmation requires a captured
  ACKNACK/NACK_FRAG request and matching repeat transmission to the same destination
  socket/reader. First-attempt deliveries and still-missing samples are excluded.
- Recovered samples contribute once within their original rolling send window.
  Parentheses show sample size; no observations shows a dash instead of zero.
  Incomplete capture coverage shows Unknown, not a misleading average.
- Added regressions for bitmap endianness, reader identity, initial fanout versus
  repair, multiple retries, fragmented receipt completion, averaging/exclusions,
  and desktop/mobile rendering. Existing traffic/accounting browser tests pass.
- Live six-platform verification at the restored **10% bidirectional loss**
  independently reconciled **68 flow counts and 8 Reliable averages** at each of
  twelve snapshots over 60 seconds. **296 rendered-table/card checks** passed
  without browser errors or invalid observations. For example, one ContactReport
  window showed **4.22 s across 2 recovered samples**, matching the WAN JSONL.
  All six peers remained ALIVE/ROUTER_OK after verification.

Retained [live recovery evidence](../debug/test_reports/wan-recovery-20261105.json)
contains per-sample send, request, retry, and receipt timestamps.

## Delivery filters and configured QoS sidebar (2026-11-05)

- Added independent Source and Destination filters plus a multi-topic checkbox
  menu. Table rows and comparison cards use the same filter intersection.
  Existing Node/Mode controls and mission selection remain available.
- Added a configuration-only sidebar resolved at dashboard startup from the
  active router YAML and its XML libraries. It shows WAN writer/reader profile,
  reliability, history/depth, durability, deadline, and liveliness lease.
  Identical settings are combined; different reader/writer settings remain
  separate. Configuration never changes observed QoS grouping or delivery counts.
- Canonical host/container launchers supply the active YAML. Configuration is
  refreshed in the browser on tab entry and WebSocket reconnect, without treating
  undeployed source-file edits as runtime QoS changes.
- Targeted host gate: **49 tests, 44 passed and 5 binding-dependent skips**.
  SDK gate: **29 passed**, including all binding-dependent contracts.
- Live browser verified 68 unfiltered rows, 5 selected-direction rows, and 2
  selected-topic rows; RouterLinkProbe still appears as observed Unknown while its
  sidebar correctly shows configured Reliable. Verified writer depth 1 versus
  reader depth 50, current script revision, and widths 1440/800/375 without
  horizontal overflow or browser errors. Both 10% loss directions were restored
  and all six peers were ALIVE/ROUTER_OK.

Retained [live filter evidence](../debug/test_reports/delivery-filters-20261105.json).

## WAN wire delivery and terminal-state recovery (2026-11-05)

- Replaced the Delivery tab's LAN/application audit correlation with passive
  source-WAN-TX to destination-WAN-RX RTPS observations. Fragment reconstruction,
  writer/sequence deduplication, and actual unicast destination nodes define the
  counts; DDS reader acceptance and application consumption are outside scope.
- Grouped flows and comparison cards by discovered writer reliability: Reliable,
  Best Effort, and explicit Unknown QoS. Removed automatic current-mode filtering
  so Init/Mission transitions cannot reinterpret old samples or hide wire history.
- Added capture coverage/status warnings. Missing enabled source/destination
  capture evidence produces Unknown loss rather than a fabricated loss percentage.
  Kernel capture drops remain unreported; even complete observed coverage does
  not prove missing samples were lost in the RF channel.
- Added compact mission ID/phase to always-on Init status. C2 reconciles newer
  Init summaries without carrying detailed progress across mission IDs.
- Rebuilt generated types and routers: **4/4 native unit tests passed**.
  Binding-enabled in-process gates: **135 passed**, **16 subtests**, one
  real-router subprocess test deliberately excluded while the mesh was live.
  Host parser/accounting/Chromium gates: **47 passed**, **16 subtests**;
  three binding-dependent cases skipped on the host passed in the SDK gate.
- All four existing real six-platform mission browser workflows passed in
  **382.56 seconds**. A new fifth live test passed in **27.85 seconds**:
  applied 100% one-way CommEffect loss through the Experiment GUI, observed
  missing Reliable and Best Effort WAN samples while an unrelated platform
  remained unaffected, and cancelled a mission during the blackout. After reset,
  detailed mission status was still TRANSIT, but fresh Init status recovered
  CANCELLED at C2, proving recovery did not depend on a detailed terminal report.
- Final coordinated restart left six ALIVE/ROUTER_OK platforms in Init/IDLE,
  Unassigned, clocks 1x, targets Off, and no active impairments. Chromium loaded
  `operator_tabs.js?v=20261105-wan-wire-delivery-v3`, rendered all QoS groups,
  and reported no page errors. All seven WAN captures were fresh, with zero
  invalid observations, unsupported destinations, or incomplete RTPS samples.
  Both known-QoS summaries showed 100% settled receipt; 12 RouterLinkProbe flows
  remained explicitly Unknown QoS rather than receiving guessed reliability.

Retained results: [mission workflows](../debug/test_reports/wan-wire-mission-20261105.xml)
and [wire blackout/recovery](../debug/test_reports/wan-wire-blackout-20261105.xml).

## State-driven reporting and target toggles (2026-11-05)

- Removed C2's pre-command switch to Mission reporting. The platform-local helper
  now follows actual mission status, drains terminal reports for three seconds,
  and returns to Init. Explicit Debug remains enabled until Auto is selected.
  Fast missions first observed as complete also receive a terminal-report window.
- Replaced manual Init/Mission menu entries with Auto, and derive Delivery's mode
  from actual reported route state. Kept final mission status cached at C2.
- Both scenario targets start hidden and have independent On/Off buttons.
  Off publishes a lost ContactReport; sequence ordering rejects an older reveal
  arriving after a hide. Existing missions survive hiding their target.
- In-process gates: **132 passed**, plus **16 subtests**; the real-router helper
  subprocess test was excluded because the live mesh was running. Offline
  Chromium contracts: **13 passed**.
- Full six-platform Chromium/DDS workflows: **4 passed, 0 skipped**, in
  **337.29 seconds**, including a new state-transition/target-toggle scenario
  and all three existing mission acceptance scenarios.
- After cleanup, verified six ALIVE/ROUTER_OK peers, Init reporting on every boat,
  both targets Off, teams Unassigned, clocks 1x, cancelled test missions, and no
  active impairments. Mission/debug forwarded counters remained unchanged over
  four seconds on all six boats, confirming idle telemetry is gated before WAN.
  Current static asset revisions loaded without browser errors.

Retained result: [live JUnit report](../debug/test_reports/mission-state-full-20261105.xml).

## Six-platform mission acceptance (2026-11-05)

- Ran the canonical containerized EMANE mesh with one C2 and six platforms.
  Expanded mission commands from five to six recipients after the live gate
  exposed the old limit rejecting fleet-wide Demo time requests. Added boundary
  contracts for six accepted recipients and seven rejected recipients.
- Updated the opt-in browser workflows to use `MISSION_PLATFORM_COUNT=6`, include
  every platform in the two-team scenario, load current declared assets, and wait
  for confirmed startup state. Icon selection uses the boat group, not a polygon
  obscured by its label.
- Added a third live scenario for late-join inheritance, active-team reassignment,
  unchanged other-member command IDs, and stopping the old goal on Unassigned.
- Final combined Chromium/DDS run: **3 passed, 0 skipped**, in **343.20 seconds**.
  Covered editing/dragging, Validate/Go, DDS command acknowledgements, audited
  Delivery rows, cross-tab state, GUI CommEffect apply/reset, route replacement,
  cancel/RTB, both targets, 1 km/2 km loiter, all six boats reaching ORBIT, long
  circuit persistence, water-only sampled positions, and team-goal inheritance.
- Movement checks use cumulative geographic travel (at least 100 m per boat in
  60 seconds), fresh positions, confirmed 20x telemetry, and bounded reported
  speed. They allow legitimate onboard separation holds instead of requiring
  exact simultaneous 10-knot motion or a fixed endpoint displacement. Continuous
  separation remains covered by the deterministic navigator contracts.
- Supporting gates: **94 mission/navigation contracts passed** and **11 offline
  Chromium geometry/UI tests passed**. This is mission-focused acceptance, not a
  claim that the entire router e2e suite was run.
- Final cleanup verified all six peers ALIVE/ROUTER_OK, teams restored to
  Unassigned, clocks restored to 1x, test missions cancelled, and no active EMANE
  impairments. The mesh remains running. Target reveals remain visible until the
  next owned lifecycle restart.

Retained result: [live JUnit report](../debug/test_reports/mission-six-live-20261105.xml).

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
