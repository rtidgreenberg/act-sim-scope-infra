# Status Resolution Control Plan (Init / Mission / Debug)

Date: 2026-07-29  
Status: Draft implementation plan  
Scope: Platform sim status routing to UI with per-platform resolution control via local orchestrator

## Goal

Add a per-platform status resolution flow where:

1. Platform simulator publishes high-level status (position and other core fields).
2. Data flows through `platform_primary_status`, downsampled to 1 Hz, and appears in the UI data panel when a platform is clicked.
3. Operator can right-click a platform in the UI and set status resolution: `init`, `mission`, `debug`.
4. A per-platform orchestrator process receives that intent and applies local router commands to enable/disable the matching routes.
5. UI automatically shows additional data sections as detail/debug topics become active.

## Correlation with Existing Implementation

This plan intentionally reuses shipped mechanisms:

1. Primary route already exists and already uses 1 Hz route-side downsampling.
2. Detail route already exists and is disabled by default.
3. UI already has right-click write flow (TeamAssignment).
4. Bridge already writes DDS control samples from UI.
5. Per-platform local control process pattern already exists (`platform_mesh_control.py`).
6. Local orchestrator model is already documented: policy local to platform, router commanded on LAN.

References:

- `router/config/control-platform.yaml`
- `harness_v2/qos/lan_qos_lib.xml`
- `gui/mesh_dashboard/static/mesh_graph.js`
- `gui/mesh_dashboard/server/mesh_bridge.py`
- `harness_v2/scripts/platform_mesh_control.py`
- `docs/cpp_router/orchestrator-design.md`
- `docs/cpp_router/command-status.md`
- `docs/cpp_router/team-control-topic-plan.md`

## Desired Behavior

### Resolution modes

- `init`
1. `platform_primary_status`: enabled
2. `platform_detail_status`: disabled
3. `platform_debug_status`: disabled
4. Payload intent: high-level operator view (position, overall system state).

- `mission`
1. `platform_primary_status`: enabled
2. `platform_detail_status`: enabled
3. `platform_debug_status`: disabled
4. Payload intent: mission-active operational data (mission state, waypoints, mission
	progression fields) intended to be on during mission execution.

- `debug`
1. `platform_primary_status`: enabled
2. `platform_detail_status`: enabled
3. `platform_debug_status`: enabled
4. Payload intent: internal engineering diagnostics (for example thruster status, rudder,
	temperatures, currents, voltages, and similar subsystem telemetry).

### Data semantics by resolution level

Primary data class:

- Position and navigation summary fields.
- Coarse overall system state suitable for continuous operator awareness.

Detail data class:

- Mission state and mission-phase indicators.
- Waypoints and mission-plan execution fields.
- Additional operational fields needed during active mission control.

Debug data class:

- Internal actuator/sensor diagnostics (for example thruster, rudder, motor controllers).
- Power and thermal telemetry (for example currents, voltages, temperatures).
- Additional engineering-only streams that are not required for normal mission operations.

### Operator experience

1. Click platform node -> always see primary data section (1 Hz).
2. Right-click platform node -> set mode (`init` / `mission` / `debug`).
3. UI reflects richer data fields as routes/topics become active.
4. UI shows resolution badges (primary/detail/debug) in the platform data panel.

## Architecture

## 1) Data-plane status routes

Keep `platform_primary_status` as the always-on baseline path.

- Source: platform LAN
- WAN relay: existing status route
- Destination: control LAN / dashboard bridge
- Downsampling: retain existing route-side 1 Hz behavior

Keep `platform_detail_status` as optional (already present, default disabled).

Add `platform_debug_status` as optional (new route, default disabled).

Platform simulator requirement:

- `platform_sim` must publish distinct status topic sets by resolution level:
	- init-level topics (always on baseline),
	- mission-level topics (enabled when mode is `mission` or `debug`),
	- debug-level topics (enabled when mode is `debug`).
- Debug-level topics may include additional internal telemetry topics intended only for
	debug exposure in the UI.

## 2) Control intent topic (UI -> platform)

Add a declarative status-mode control topic, carried through a router route with
target filtering at the destination-side WAN reader.

- C2/UI writes one sample keyed by platform node.
- Route carries it control -> WAN -> platform LAN.
- Router-level CFT targets `${node.name}` so only the intended platform route leg
  receives the sample.

Suggested filter shape (destination-side WAN reader):

- expression: `platform_node = %0`
- parameters: `["${node.name}"]`

## 3) Platform orchestrator process (local policy)

Extend/replace `platform_mesh_control.py` into a per-platform orchestrator process that:

1. Subscribes to status-mode intent topic on platform LAN.
2. Publishes local `ActRouterCommand` (`ENABLE_ROUTE` / `DISABLE_ROUTE`) on platform LAN targeting local router.
3. Enforces mode->route mapping idempotently.

This preserves the current command boundary: router state changes still occur only through router command topic handling.

## DDS/Data Model Changes

## 1) Add status-mode control type

Add a new IDL struct to `harness_v2/datamodel/ActTypes.idl`, keyed by platform.

Suggested fields:

- `platform_node` (key)
- `resolution_mode` (`INIT` / `MISSION` / `DEBUG`)
- optional metadata: `request_id`, `issued_by`, `issued_at`

## 2) Optional debug status payload type

If debug telemetry is separate from detail status, add a dedicated payload struct and topic.

## 3) Platform simulator topic generation

Update `harness_v2/sims/platform_sim.py` to generate/write the additional topic set for each
resolution level (primary/detail/debug), including any debug-only internal telemetry topics.

Guidance for first implementation pass:

- Ensure primary stream remains low-cardinality and stable for always-on UI display.
- Put mission execution fields in detail stream(s), not primary.
- Reserve internal subsystem diagnostics for debug stream(s).

## 4) Regenerate XML

Regenerate `harness_v2/datamodel/gen/ActTypes.xml` after IDL updates.

## Router Config Changes

In `router/config/control-platform.yaml`:

1. Keep `platform_primary_status` route unchanged (already 1 Hz-gated).
2. Keep `platform_detail_status` route default `enabled: false`.
3. Add `platform_debug_status` route default `enabled: false`.
4. Add/extend a control->platform event route for the status-mode topic with
   destination-side CFT targeting `${node.name}` (router-level filtering).

## Bridge and UI Changes

## 1) Bridge (`mesh_bridge.py`)

1. Add readers/caches for primary/detail/debug status topics.
2. Add POST endpoint for status resolution requests.
3. Publish status updates over existing WebSocket stream.

## 2) UI (`mesh_graph.js`)

1. On click: render primary data section for selected platform.
2. Extend existing context menu with status-mode actions.
3. POST selected mode to bridge endpoint.
4. Show detail/debug sections when data appears.
5. Show stale/unavailable state when route/mode not active.
6. Show resolution badges in the panel and treat increased DDS-delivered resolution as the
	user-visible acknowledgment of mode change.

## Phased Implementation Plan

## Phase A: Type and route scaffolding

1. Add IDL type(s) for status-mode control (+ optional debug payload).
2. Regenerate XML.
3. Add route stubs in router config (debug route and control topic carriage).
4. Add platform_sim stubs for additional topic writers per resolution level.

Exit criteria:

- Build/runtime load of updated types/config works.
- No regression in existing mesh dashboard and team assignment path.

## Phase B: Platform orchestrator mode control

1. Implement orchestrator subscriber for status-mode topic.
2. Implement mode->command mapping and idempotent command emission.
3. Validate local router route toggling via `ActRouterStatus`.
4. Validate that platform_sim writers for detail/debug topics are active and producing data
	when corresponding routes are enabled.

Exit criteria:

- Per-platform mode changes produce expected route state transitions.
- Mode changes are isolated to targeted platform.

## Phase C: Status data ingestion and UI panel

1. Bridge reads/caches primary/detail/debug status topics.
2. UI click panel shows primary data.
3. Mode changes from UI produce additional visible data when enabled.

Exit criteria:

- Clicking a platform always shows primary data at about 1 Hz.
- Detail/debug fields appear/disappear according to mode and route state.

## Phase D: End-to-end hardening

1. Restart behavior checks (router, orchestrator, bridge, sim).
2. Error handling and user feedback in UI.
3. Document operational semantics and fallback behavior.

Exit criteria:

- Deterministic behavior across restarts.
- No silent failures in mode transitions.
- Mode resets to `init` on restart unless changed again by UI.

## Test Plan

0. Scope gate (first pass)
- Validate on a single platform first.
- Keep implementation platform-agnostic by using target platform id in the control sample
	and router CFT targeting.

1. Primary baseline
- With default mode, primary status visible in UI at about 1 Hz.

2. Detail enable
- Set mode to `detail`; verify `platform_detail_status` route enabled and detail fields appear.
- Verify detail-level platform_sim topics are flowing to UI.

3. Debug enable
- Set mode to `debug`; verify debug route enabled and debug fields appear.
- Verify debug-level platform_sim topics (including debug-only internal telemetry topics)
	are flowing to UI.

4. Mode downgrade
- From debug -> primary; verify optional routes disabled and optional fields stop updating.

5. Target isolation
- Change one platform mode; verify other platforms unchanged.

6. Restart behavior
- Restart platform router/orchestrator and verify mode resets to `primary`.

## Decisions Locked (2026-07-29)

1. Use router-side destination CFT targeting for status-mode control (`platform_node`
	keyed targeting); platform mesh control does not self-filter.
2. Mode resets to `primary` after restart (non-persistent).
3. `debug` uses separate topic(s)/route(s), allowing additional internal topics to be
	exposed only in debug mode.
4. UI data panel shows resolution badges.
5. User-visible acknowledgment is observed increased DDS-delivered resolution in the panel
	(no separate ack protocol for this phase).
6. Validate single-platform first, but keep the design platform-agnostic via target id.

## Remaining Open Items

1. Final topic/type names for status-mode control and debug payload topics.
2. Exact badge visual/state model when requested mode and observed data lag briefly.

## Recommended Defaults

1. Start with non-persistent mode state (`primary` on restart).
2. Use observed-mode rendering (based on received topic data + route status) instead of
	optimistic UI state.
3. Keep primary route always enabled and stable; treat detail/debug as optional overlays.
4. Preserve command path idempotency and local-only orchestration discipline.
5. Use reliable QoS on the status-mode control route.

## Success Criteria

1. Primary platform status is continuously visible in the UI at about 1 Hz.
2. Operator can switch per-platform status resolution with right-click.
3. Orchestrator applies route enablement locally and deterministically.
4. UI shows additional telemetry only when matching routes are active, with badges
	reflecting current observed resolution.
5. Existing team assignment and mesh awareness features continue to work unchanged.
