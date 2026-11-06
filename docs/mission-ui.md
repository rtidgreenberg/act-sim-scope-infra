# Mission UI scenario architecture

## Purpose

The Mission UI is an operator scenario that exercises the existing ACT DDS router,
platform simulator, dashboard bridge, and EMANE framework. It is not a separate
mission-control product or a parallel control plane.

The initial scenario operates one control node (`Control_20`) and five simulated
platform boats (`Platform_30` through `Platform_34`) near San Diego Harbor. Boats
start at staggered offshore launch positions west of the harbor to avoid land at
the bundled map's resolution.

## Operator workspaces

The dashboard presents coordinated views of the same live scenario:

| Workspace | Operator purpose |
|---|---|
| Mission | Plan routes, organize squads, command boats, and observe acknowledgements and progress. |
| Mesh | Inspect the live router topology and network health for the mission fleet. |
| Delivery | Verify command, acknowledgement, health, and status delivery for the selected scenario. |
| Experiment | Apply controlled EMANE impairments and observe their mission effects. |

No workspace owns independent state. A mission command issued from Mission must be
observable in the other views through the normal DDS status and delivery streams.

## Mission workspace

### State-driven WAN reporting and target visibility

Platforms start in Init reporting. The local mesh-control helper observes the
platform's own `PlatformMissionStatus` on its LAN: accepted TRANSIT, ORBIT, RTB,
or HOLDING phases enable Mission reporting at the platform's outbound router.
C2 does not enable telemetry merely because it sends a command. Completion or
cancellation keeps Mission reporting enabled for three seconds after the first
local terminal report, allowing final updates to cross the WAN, then returns to
Init. This drain window is not a delivery guarantee under radio loss.
Always-on `PlatformInitStatus` also carries the current/last `mission_id` and
`mission_phase`. C2 reconciles a newer compact summary with detailed reports, so
completion/cancellation can recover after a dropout that covered the drain window.
A new mission ID never inherits the previous mission's detailed progress.

Debug is an explicit operator override that survives mission transitions.
The Mesh menu's Auto option releases it and follows the current mission state;
legacy Init/Mission status-mode requests both resume automatic reporting.
Position sharing, command acknowledgements, contacts, and router health remain
available in Init. Delivery measures unique RTPS samples crossing WAN interfaces,
not local LAN sends or end-to-end application receipt. Its Reliable/Best Effort/
Unknown QoS groups come from writer discovery. Mode filtering is manual; returning
to Init does not hide historical Mission traffic. See the dashboard's
[measurement definitions](../gui/mesh_dashboard/README.md#wan-wire-delivery).

Both scenario targets start hidden after a mesh restart. Their buttons independently
toggle On/Off, publishing a local C2 `ContactReport` (`lost=true` for Off).
The map changes after DDS reception; older reports cannot undo a newer toggle.
Hiding a target prevents new plans to it but does not cancel an existing mission.

### Map and visual language

- Use an offline-styled Southern California coast map centered on San Diego Harbor.
  The first version must not depend on external map tiles.
- Bundle clipped public-domain Natural Earth 1:10m land polygons for the San Diego
  coastal area (32.50-32.95 N, 117.65-117.05 W). Use dark land/water styling,
  team-colored boat markers, and compact labels; do not display red restriction boxes.
- Explicit validation inserts water-only detour waypoints around land between drawn
  destinations. Different boats may need different approach legs to the same
  destinations. This coarse map and grid planner are demo aids, not marine navigation
  or a clearance/depth guarantee. The bridge also water-plans return-to-base.
- Render every platform as a triangular boat icon at its actual reported position.
  Icons stay a consistent screen size while zooming; they do not represent physical
  hull dimensions. Crowded boats become a clickable boat-count marker anchored to
  a member's reported position. Clicking zooms to the members. There are no displaced
  boat icons, position tethers, or automatic camera movement during travel.
  **Zoom to boats** fits the selected boats; the map scale updates with zoom.
- Show all mission paths with dotted lines: drafts, acknowledged travel,
  approaches, return-to-base legs and continuous loiter circuits. Acknowledgement
  does not turn a path solid. Show numbered waypoint markers for editable drafts.
- Share the Mesh palette: Alpha blue, Bravo gold, Reserve purple, Unassigned green.
  Show upright P30/P31/etc. labels inside the icons when zoomed in. P20 is a fixed
  demo shore-base marker, not a reported vessel position.
- Reserve amber for degraded/warning states, red for failed or disconnected states,
  and green for confirmed connectivity or acknowledgement.

### Fleet and squads

The fleet inventory is sourced from the live platform roster and shows:

- platform name;
- availability;
- connection health;
- battery;
- current squad; and
- current mission.

A boat belongs to exactly one squad. Reassigning it removes its previous assignment.
Squad membership is persisted through the existing DDS `TeamAssignment` flow, rather
than stored only in browser state.

The fixed squad choices are Alpha (`TEAM_A`), Bravo (`TEAM_B`), Reserve (`TEAM_C`),
and Unassigned (empty partition). Membership shown in the UI comes from the
mesh-confirmed `RouterHealth.team_partition`, excluding identity partitions;
HTTP publication alone does not confirm assignment. The scenario resets all squad
assignments and active missions on mesh restart so a demo begins from a known state.

Alpha, Bravo and Reserve each own one shared goal. Setting a mission from even one
selected team member expands to the entire confirmed team. The bridge commits the
shared goal after all addressed boats acknowledge it. Confirmed membership changes
then send that goal only to the joining boat; existing members keep their mission
IDs and progress. Joining an idle team or Unassigned stops the previous mission.
Individual cancel/RTB does not erase the remaining team's goal. Whole-team cancel
clears it; a completed whole-team waypoint/RTB mission also clears it. Partial
dispatch failures and inheritance failures are reported in the inventory, not
silently treated as success. The three team goals are independent of bounded
command-history retention and are deliberately reset on restart.
In-flight goals are tracked before acknowledgements arrive, so reassignment can
stop the old goal even before the next mission-status sample. Late acknowledgements
do not overwrite a newer reassignment. A boat joining a team whose new goal is
still pending stops its previous team's goal while waiting for that dispatch.

### Route workflow

1. Left-drag pans by default. Click a boat-count marker to zoom if needed.
   Click a boat to select it; Shift-click adds or removes boats. There is no
   drag-box selection mode. Inventory row clicks, Enter, or Space toggle
   highlighted selection, without separate Select buttons.
2. Right-click the selected boats and assign Alpha, Bravo, Reserve, or Unassigned.
3. Select the team in the left column, then right-click **Set mission**.
4. Choose **Draw waypoints**, or **Select target** and a revealed target.
   For waypoints, click ordered destinations and drag waypoint markers to edit them.
5. Click **Validate mission** to preview per-boat water detours. This publishes no
   DDS commands and does not move boats.
6. Click **Go** to publish the validated route through `ControlCommand`.

The operator selects the same destinations for every boat. Automatic water detours
can produce different waypoint sequences for each boat's approach.
Validation previews those detours, but C2 sends the authored goal, not steering or
a precomputed navigation circuit. Each sim water-plans from its own current fix.
For a shared final waypoint, boats compute stable, separated holding stations
around that destination rather than converging on one identical coordinate.
Commands support up to 64 waypoints per boat, including inserted detours, within an
8192-byte `ControlCommand.payload`.

Before sending, operators can drag waypoints, right-click to remove one, remove the
final waypoint, or clear the route. Edits and selection/membership changes invalidate
validation. Plans expire after five minutes; Go rechecks current positions,
connectivity and confirmed squad membership. Land destinations and out-of-map
coordinates are rejected explicitly rather than silently moved.

Scroll or use the map buttons to zoom; left-drag pans and **Fit coast** restores the
view. Backspace removes the final waypoint while drawing; Escape discards the draft.
Panning can reveal map margins; waypoint destinations remain restricted to the
offline map coverage.
Periodic status updates preserve SVG and inventory elements during interaction.

The Mission UI supports sending a route, replacing the active route (Set mission,
Validate, Go again), cancelling it,
and returning a boat to base. Per-boat command acknowledgement is visible before a
route is treated as active. Return-to-base sends each boat to its own recorded launch
position. When a boat reaches the final waypoint, it holds position and reports
`Complete`.

### Two-target story

The top Scenario controller's **Target 1: On/Off** / **Target 2: On/Off** controls
display real DDS `ContactReport`s; toggling does not change a mission draft.
Demo time is also in the Scenario bar. Destination and loiter distance belong to
mission setup below the platform list, not to global scenario controls.
Select Alpha, create a mission, choose a loiter distance, Validate, then Go.
Either target can be enabled independently; for example, enable Target 2 and dispatch Bravo.
The fixed target locations are offshore and use the same real-coastline planner.

Loiter distance defaults to **1 km**, with **500 m, 1 km, 2 km, and 3 km** choices.
Changing the distance invalidates the draft's validation. Each boat approaches
the near side of the target and receives a continuous water-planned circuit.
Circuit vertices are slightly outside the selected distance so straight circuit
legs do not cut inside it. This is a demo planning distance, not a navigational
safety or obstacle-clearance guarantee. The accepted command's distance appears
in the live inventory. Different teams can use different distances.

The actual simulator reports `ORBIT` and repeats the circuit until cancelled,
returned to base, or assigned a replacement route. A free-waypoint route still
ends in `COMPLETE`; continuous orbit is explicit, not applied to every route.
Target reveal state, assignments and missions reset on the owned mesh restart.
The live two-target browser gate requires a fresh scenario.

Large, highlighted, state-aware hints and contextual controls guide selection, team assignment,
drafting, validation and Go. Go appears only for a validated draft, and waypoint
editing controls appear only for an unvalidated waypoint draft. The right-click
menu and buttons below the inventory share the same action-state rules:
no command controls without connected selected boats; Set mission and applicable
team changes before drafting; Validate and editing while drafting; Go after
validation; Cancel/RTB only for applicable live missions outside draft editing.
Already-confirmed team assignments are omitted from the menu, and only revealed
targets are offered. Right-clicking the same selected team preserves validation.

## Live DDS integration

Mission commands travel through the existing `ControlCommand` topic and receive
`PlatformCommandAck` replies. The UI consumes these live DDS commands, acknowledgements,
and reported `PlatformInitStatus`, `PlatformMissionStatus`, and
`PlatformWaypointStatus` samples; it must not use browser-side mission mock data.
Platform simulators update those reports after accepting a command.

The dashboard bridge owns command publication and converts live platform status into
the Mission inventory and map state. It must not synthesize acknowledgement or
completion state.

Commands use the active run ID and canonical delivery-audit sequence. Pending
records are registered before publication; acknowledgements must match command,
source boat and run. The bridge exposes pending, accepted, rejected, timed-out and
failed states, with explicit late-acknowledgement and per-target partial-failure
information. Acknowledgement timeout is 15 seconds. Command, acknowledgement and
plan histories are bounded to 200 records each.
Current mission metadata and dotted route previews are retained separately per
boat, and target dispatch history is retained separately for the scenario, so
history eviction does not erase an active mission. Route drawing
uses the boat's reported mission ID, not its most recent attempted command; a
failed replacement leaves the actual running route visible.

The browser periodically reconciles command history even while live inventory
pushes are arriving, with faster retries while acknowledgements are pending.
Newer command pushes received during a REST request are preserved without rolling
back the live inventory. An authoritative empty history clears old browser
commands after a fresh scenario starts.

Simulated boats navigate toward acknowledged goals at up to 10 knots. Their
reported speed decreases or becomes zero when buffering or holding; heading
reflects actual movement. The status publisher remains authoritative: the map follows reported
position and mission/waypoint state, never client-only interpolation as a substitute
for DDS state.

The fleet clock offers **1× real time** and **20× demo time**, applied through
acknowledged DDS commands. It scales simulator position progression and mission
elapsed time, not nominal speed, audit timestamps, acknowledgement
timeouts, or network-delivery grace periods. The UI claims a clock setting only
after all boats confirm it.

### Onboard separation

Each sim reads other boats' real `PlatformInitStatus` fixes through DDS, including
boats on other teams, and maintains a **300 ft (91.44 m) minimum buffer** while
following its goal. Navigation runs independently of the 1 Hz status publisher,
checks movement against the coastline, uses persistent local passing detours, and
preserves target standoff while loitering. Waypoint holding stations use 250 m
spacing to allow for status cadence and accelerated demo time.

A separate `location_wan` participant and protected `NAVIGATION` partition carry
only shared position routes. This avoids opening the team-scoped `platform_wan`
participant or leaking team-only `PlatformData`. Same-node router-publication
ignore prevents LAN-returned fixes being retransmitted. Existing external QoS
profiles are reused. The simulator audits peer-location receipts, and Delivery
expects each position report at control and every other boat, regardless of team.
New baseline manifests declare `shared_platform_locations: true`; immutable audit
snapshots exclude the sending boat from the location recipients. Legacy manifests
without that flag retain their original control-only expectations.
RouterHealth retains its existing presence participant and delivery expectations.

Goals include the expected fleet roster. Missing fixes hold navigation; fixes
older than 2.5 seconds also hold it. Conservative uncertainty expands with fix age,
the next movement interval and the maximum 20x speed, rather than assuming peers
remain at their last reported position. Status exposes `navigation_state`,
`minimum_separation_m`, and the actual `time_scale`; the UI explains buffer and
missing/stale-location holds.
Return-to-base validates and plans before changing the current navigator. A
rejected command preserves the current route, peer requirements, passing detour,
and loiter standoff.

This is a controlled simulation, not certified maritime collision avoidance.
It requires separated initial positions, bounded simulator speeds and valid
shared fixes; it cannot guarantee separation from unreported/uncontrolled traffic
or arbitrary communication failure. Physical buffering does not eliminate icon
clusters at coast-wide zoom: zoom in to inspect actual reported positions.

Automated tests may use deterministic simulator fixtures to make timing and route
progress repeatable, but those fixtures still publish and consume the same DDS mission
messages as the live scenario.

## Mesh synchronization

The Mesh view continues to represent network topology, not geographic position.
Mission state augments graph nodes with:

- squad color ring;
- mission phase; and
- waypoint/route-progress badge.

Topology, edge style, and health continue to derive from `ActRouterMeshStatus`,
`ActRouterLinkStats`, and `RouterHealth`. Mission status must not overwrite those
network facts.

The existing dashboard WebSocket distributes mission inventory and command updates;
REST is the fallback. Selected team/boat context adds a Mission filter to Delivery
and prefills Experiment's source. Operators can still choose unrelated Delivery
filters and Experiment endpoints. Experiment options retain the full mesh roster
and annotate confirmed squads.

## Validation

An end-to-end mission scenario is complete only when:

1. the live inventory lists all discovered platforms;
2. a squad assignment updates the existing DDS team-assignment path;
3. an issued route reaches every selected platform and produces a distinct
   acknowledgement;
4. platform status shows the acknowledged route and waypoint progress;
5. Mission map icons, Mesh node badges, Delivery rows, and Experiment selectors all
   reflect the same platform roster and mission state; and
6. the dashboard remains usable under a targeted EMANE impairment and reports the
   resulting delivery/network degradation without rewriting mission state.
