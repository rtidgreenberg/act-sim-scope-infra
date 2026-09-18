# CommEffect Mesh Verification Plan

## Objective

Verify the live six-platform EMANE mesh and its operator dashboard as one system:

- CommEffect commands are applied to the intended directional link.
- Application delivery, router presence, link statistics, EMANE counters, and WAN RTPS counters change consistently with the impairment.
- The Experiment, Delivery, graph, and traffic views display the commanded state and observed behavior correctly.
- Reset restores nominal communication and does not leave stale directional profiles.

This plan deliberately excludes RF Pipe pathloss. It uses the active topology:
Control_20 plus Platform_30 through Platform_35, with the shared WAN on domain 200.

## Evidence Rules

Every scenario gets a timestamped record from four evidence classes:

1. **Command/UI:** Experiment tab fields, direction, current commanded paths, apply/reset response, and browser console errors.
2. **Application/audit:** Delivery tab rows and raw `events.jsonl` sequence matches for all available application topics.
3. **Protocol/router:** `RouterHealth`, `ActRouterMeshStatus`, `ActRouterLinkStats`, router journals, and domain-200 discovery/user-data counters.
4. **EMANE:** event 103 reception, MAC RX/TX/drop counters, and CommEffect drop tables for affected NEMs.

Evidence is correlated by wall-clock timestamps. A packet counter is not treated as an application delivery result, and a graph edge is not treated as proof of healthy application traffic.

## Scenario Matrix

### S0: Nominal baseline

Observe the untouched mesh for at least 30 seconds.

Expected:

- Six platforms and Control_20 are present and healthy in the graph.
- Experiment source/destination lists come from `/api/mesh_status`.
- Delivery rows for all configured application topics show sends and receives with no unexplained loss after their grace windows.
- `RouterHealth` and mesh-status heartbeat sequences advance.
- EMANE event tables are readable and no unexpected CommEffect drops are present.
- Browser has no page errors or failed dashboard API requests.

### S1: Apply a small bidirectional impairment

Apply CommEffect between Control_20 and Platform_30 in both directions with modest latency, jitter, loss, and duplicate values.

Expected:

- Both directional commands are shown in current commanded paths.
- Both affected NEMs receive event 103.
- Delivery may degrade or arrive later for the selected route, while unrelated Platform_31 through Platform_35 routes remain nominal.
- Protocol counters and EMANE counters change in the selected direction without implying a total topology outage.
- Graph topology remains connected; health may move to degraded only if RouterHealth misses its configured deadline.

### S2: One-way loss isolation

Apply high CommEffect loss from Control_20 to Platform_31 only, leaving the reverse path nominal.

Expected:

- Only the forward directional profile is recorded and visible.
- Control-originated messages to Platform_31 lose delivery after the topic grace period.
- Platform_31-originated traffic and unrelated links continue, subject to DDS repair behavior.
- A reset of the forward direction does not silently leave the reverse direction impaired.

### S3: Reliability and presence under severe bidirectional loss

Apply near-total loss in both directions between Control_20 and Platform_32.

Expected:

- CommEffect event reception increments on both receivers.
- EMANE drop counters rise for the selected path.
- Reliable command/ack and RouterHealth delivery loss appears only after their documented 15-second grace period.
- Platform_32 presence transitions through the configured degraded/stale/dead states if the impairment lasts long enough; unrelated peers remain visible.
- Graph edge health, link stats freshness, and delivery rows agree on the affected path.

### S4: Direction control and reset correctness

Exercise forward-only, reverse-only, and bidirectional modes on separate pairs, then use reset-all.

Expected:

- The GUI direction selector maps to the correct transmitter/receiver NEMs.
- Bidirectional apply creates two directional records.
- Reset-all removes all tracked commanded paths and writes zero-effect profiles in both directions.
- New event 103/reset evidence is visible, drop counters stop increasing, and delivery/presence recover after the appropriate windows.
- No stale profile remains in EMANE tables for the tested pairs.

### S5: Profile seeding and untouched-peer protection

Apply impairment to Control_20 and Platform_33 while all six platforms are active, then inspect every receiver's CommEffect profile/drop state.

Expected:

- The bridge seeds zero-effect profiles for known transmitters so omitted transmitters do not become `No Profile` drops.
- Only the selected directional pair has nonzero impairment.
- Unselected platform-to-platform and control-to-platform traffic remains explainable and nominal.

### S6: Combined impairment and recovery

While S3 is active, apply a second impairment to another pair, then reset only one pair followed by reset-all.

Expected:

- The dashboard tracks both active pairs independently.
- Resetting one pair does not erase or reset the other pair.
- Reset-all clears both pairs and restores the full mesh.
- Any mismatch between in-memory controller state, EMANE tables, and GUI labels is recorded as a finding.

### S7: Dashboard/API consistency and resilience

During nominal, impaired, and recovered states, compare browser-rendered values with `/api/emane_controller`, `/api/emane_stats`, `/api/mesh_status`, `/api/link_stats`, `/api/delivery_stats`, and `/api/traffic_stats`.

Expected:

- Labels, source/destination options, direction, impairment units, timestamps, and status text match API state.
- No stale JavaScript revision is loaded.
- WebSocket updates and REST refreshes converge without duplicate or contradictory rows.
- The browser remains usable while the mesh is impaired.

## Execution Order

Run S0, then S1, S2, S3, S4, S5, S6, and S7. Combine waits where the grace windows permit, but do not declare a recovery pass until the relevant 2-second periodic or 15-second reliable-topic window has elapsed. Capture raw evidence before clearing artifacts.

## Finding Format

Each finding records:

- ID and priority: P0 blocks the experiment, P1 invalidates a key conclusion, P2 misleads operators, P3 polish.
- Scenario and timestamp.
- Commanded source, destination, direction, and values.
- Expected versus observed UI, application, protocol, and EMANE evidence.
- Reproduction command/API payload and artifact paths.
- Scope, likely owner, and recommended next investigation.

## Known Measurement Boundaries

- `/api/emane_stats` is aggregate and cannot alone attribute loss to one NEM.
- Delivery loss is grace-windowed and sequence-matched; it is not an instantaneous packet counter.
- Graph topology and edge health are derived from different streams and must be correlated, not conflated.
- Reset-all replays zero profiles tracked by the bridge; independent or stale EMANE profiles require table-level verification.
- The current baseline manifest is not a full six-platform, all-topic acceptance test.

## Results

Results and findings are appended below during execution. No issue is considered resolved by a plausible-looking GUI value without corroborating raw evidence.

### Execution Record: 2026-09-18

#### Completed scenarios

- **S0 nominal/recovery:** After reset and a 16-second recovery wait, the dashboard showed `6/6 direct alive`, `6 relayed`, `7 nodes / 12 edges`, `ROUTER_OK`, and `PRESENCE_ALIVE`. Delivery showed zero lost samples for the visible `ContactReport`, `PlatformInitStatus`, `PlatformCommandAck`, `ControlCommand`, and `RouterHealth` rows across platforms 30-35.
- **S1 combined bidirectional impairment:** Applied through the Experiment tab between Control_20 and Platform_30: `100 ms` latency, `20 ms` jitter, `10%` loss, and `2%` duplicate. The UI recorded both directional paths and aggregate EMANE counters changed.
- **S2 one-way total loss:** Applied `100%` loss from Control_20 to Platform_31 only. The UI recorded only `Control_20 -> Platform_31`; the reverse path was not added. The graph moved to `5/6 direct alive` and `11 edges` while the impairment was active.
- **S3 severe bidirectional loss:** Applied `100%` loss in both directions between Control_20 and Platform_32. The UI recorded both directions, the graph moved to `5/6 direct alive`, and Delivery showed loss on the affected Platform_32 route while unrelated platform routes remained at zero loss in the sampled window.
- **S4 reset/recovery:** Reset-all removed all commanded paths from the Experiment tab. After the reliable-topic grace period, the graph returned to `6/6 direct alive` and Delivery returned to zero loss in the visible baseline rows.
- **S6 combined controls:** While S2 was active, applied Platform_33 -> Control_20 with `50 ms` latency, `10 ms` jitter, `1000 bps` unicast, and `500 bps` broadcast. The UI retained both independent commanded paths, demonstrating multi-profile state tracking.
- **S7 dashboard transport:** The local bridge returned `HTTP/1.1 101 Switching Protocols`; the forwarded page initially returned WebSocket `502/504`. REST fallback was added, then the tunnel recovered and the live badge reported `WebSocket live`. The page loaded the cache-busted fallback/badge assets and mesh revisions continued advancing.
- **F4 regression:** After a confirmed clean reset, applied `100%` one-way loss from Platform_35 to Control_20. The browser displayed all active Platform_35 status-topic rows and the API reported `0%` delivery / `100%` loss for settled affected flows once their thresholds were met. Before readiness, the corrected API returned `null` percentage fields rather than optimistic `100%` / `0%` values.
- **F5 combined moderate impairment:** After a clean reset, applied bidirectional Control_20 ↔ Platform_30 CommEffect with `250 ms` latency, `50 ms` jitter, `2,000 bps` unicast, `1,000 bps` broadcast, `25%` loss, and `5%` duplication. Experiment rendered both directional profiles; the graph showed `5/6 direct alive`; Platform_30 command/health delivery degraded while untouched Platform_31 remained at zero loss. Reset returned the graph to `6/6 direct alive`.
- **F6 reverse direction isolation:** Applied `100%` loss only from Platform_31 to Control_20. Experiment recorded one directional profile; Delivery showed Platform_31-to-control status/health loss while the reverse Control_20-to-Platform_31 command path remained delivered and unrelated Platform_32 remained healthy. Reset returned the graph to `6/6 direct alive`.
- **F7 multi-profile state:** Applied 50% bidirectional loss to Control_20 ↔ Platform_30 and 100% forward loss to Control_20 → Platform_32, then cleared the Platform_32 profile selectively. The controller preserved independent profiles. The browser initially failed to display the second profile because concurrent apply responses arrived out of order; request sequencing was added, and a rerun displayed both profiles correctly. Reset returned to six peers and zero active impairments.
- **F8 all-topic impairment coverage:** With all platforms assigned to `TEAM_A` and Debug mode, applied Platform_35 -> Control_20 CommEffect (`150 ms` latency, `30 ms` jitter, `3,000 bps` unicast, `1,000 bps` broadcast, `25%` loss, `3%` duplicate). All nine Platform_35-to-control topics showed loss in Delivery, while untouched Platform_34 showed `30/30/0` across the same topic set. Reset returned to `6/6 direct alive` and zero impairments.
- **F9 presence awareness:** Applied 100% bidirectional loss on Control_20 ↔ Platform_32 for more than 20 seconds. The graph degraded to `5/6 direct alive`; both RouterHealth directions reached `0%` delivery / `100%` loss while unrelated PlatformData flows remained healthy. The raw mesh payload reported the authoritative sibling field `peer.presence=PRESENCE_DEAD` and `last_seen_delta_ms=32498`; the graph displayed the degraded topology correctly. Reset returned to `6/6 direct alive`.
- **F10 delay/bandwidth without configured loss:** Applied one-way Platform_34 -> Control_20 CommEffect with `500 ms` latency, `100 ms` jitter, `1,000 bps` unicast, `500 bps` broadcast, `0%` loss, and `0%` duplicate. Delivery degraded on the constrained route despite the loss field being zero, consistent with queue/throughput pressure; unaffected routes remained at zero loss. Reset returned to `6/6 direct alive`. The browser also logged transient tunnel `504` responses during the run while the transport badge remained live; this remains forwarding-layer evidence, not a local bridge failure.
- **F11 TEAM_A peer-link degradation:** Assigned Platform_30 and Platform_31 to `TEAM_A`, confirmed nominal `peers_seen` showed each other as `PRESENCE_ALIVE`, then applied 100% bidirectional loss only between that pair. Control_20 continued to see both direct peers alive, while each platform's nested `peers_seen` dropped the other platform. Event 103 was received on Control NEM 1 and team NEMs 2/3 with no `No Profile` evidence. The initial run falsely appeared to broaden PlatformData loss because the Delivery resolver expected every platform as a recipient; after making recipient resolution team-aware and reloading the bridge, only the selected 30↔31 flows appeared and both reported 100% loss. Team assignments and impairments were cleared afterward.
- **F12 link-stat/presence correlation:** Applied 100% bidirectional loss on Control_20 ↔ Platform_30 and verified the graph degraded to `5/6 direct alive`, the peer presence became `PRESENCE_DEAD`, and the Control_20 link-stat row showed zero received samples and no heartbeat/NACK activity. After reset, the rendered Link Activity inspector and API converged to `1/1 directions receiving`, fresh age, RTT data, and zero NACK/repair counters. This confirms link stats and current presence corroborate the graph; `overall_state` remains historical health, not reachability.
- **F13 presence recovery timing:** Repeated 100% bidirectional loss on Control_20 ↔ Platform_30. After roughly 32 seconds without heartbeats, the peer reported `PRESENCE_DEAD` with a 31.7-second last-seen age. After reset, the graph returned to `6/6 direct alive` and the browser observed recovery in about 2 seconds; the API then reported `PRESENCE_ALIVE`, `last_seen_delta_ms=803`, and zero active impairments.
- **F14 sustained partial loss threshold:** Applied 50% bidirectional loss on Control_20 ↔ Platform_31. The graph initially degraded to `5/6 direct alive`; after sustained impairment Platform_31 reached `PRESENCE_DEAD` while Platform_30 remained healthy. Delivery was topic/QoS dependent: Platform_31 `ContactReport` and `RouterHealth` reached 0%, while `PlatformInitStatus` retained 35.7% delivery. This confirms configured CommEffect loss is not an application delivery percentage and that partial loss can still cross the presence deadline. Reset restored `6/6 direct alive`.
- **F15 reliability comparison display:** Added Delivery-tab summary cards that aggregate settled rows into reliable command/health topics (15 s grace) versus periodic status/report topics (2 s grace). Under a controlled total-loss run, the live cards showed `156/186` reliable samples received with `30` lost and `436/522` periodic/report samples received with `86` lost. The transport badge remained WebSocket live. Reset restored zero impairments.
- **F16 duplicate handling:** Applied one-way Platform_32 -> Control_20 CommEffect with `50%` duplicate and zero loss. Link stats reported `duplicates_received=1`, while sequence-matched Delivery remained `100%` for ContactReport and PlatformInitStatus with no duplicate inflation; presence stayed alive. Reset restored six peers and zero impairments.
- **F17 mixed loss/duplicate degradation:** Applied one-way Platform_33 -> Control_20 with `100 ms` latency, `25 ms` jitter, `40%` loss, and `30%` duplicate. Platform_33 reached `PRESENCE_STALE`; ContactReport delivered `10.7%`, PlatformInitStatus `64.3%`, and RouterHealth `0%`. The Delivery comparison cards showed reliable `172/186` settled received and periodic/report `612/662`; reset restored `6/6 direct alive` and zero impairments.

#### Findings

**F-001 — P1 — Forwarded WebSocket failure could invalidate live GUI evidence.**

The Dev Tunnel intermittently rejected `/ws` with `502/504` even though the local bridge accepted the upgrade with `101`. Before mitigation, the graph had no REST refresh fallback and Delivery only fetched on tab entry, so live evidence could freeze silently. The dashboard now polls mesh/link state every 500 ms and Delivery every 1 second while WebSocket is disconnected, and shows a transport badge (`WebSocket live`, `REST polling`, or `REST unavailable`). The tunnel later recovered and the badge was verified as `WebSocket live`. Root cause of the original 502/504 remains in the tunnel forwarding layer, not the local bridge.

**F-002 — P2 — Shell-side API and EMANE table corroboration is not currently reachable from the host session.**

The browser-visible forwarded dashboard and UI evidence were healthy, but the execution shell's `127.0.0.1:8080` API probe returned connection refused, and short EMANE container names did not resolve to the compose-prefixed containers. This limits automated raw-counter corroboration until the harness exposes the dashboard endpoint to the host or the evidence runner resolves the exact container names and executes `emanesh` inside them. It does not invalidate the browser observations, but it prevents a full protocol/EMANE acceptance verdict.

**F-003 — P2 — Current baseline acceptance was initially not all-topic complete (closed for baseline coverage; broader repetition remains).**

The initial Delivery view exposed only the expected command, acknowledgement, contact, init, and health rows. This was partly a mode/team test-state issue: mission/debug topics require the corresponding resolution mode, and PlatformData requires team assignment. After setting all platforms to `TEAM_A` and exercising mission/debug modes, the API and browser exposed all required topics, including PlatformData. F8 verified all nine Platform_35-to-control topics under impairment against an untouched Platform_34 baseline. Broader statistical repetition under every impairment level remains test-suite hardening, not an untested topic path.

**F-004 — P2 — Unready percentage fields were misleading (fixed, needs regression coverage).**

During total loss, Delivery correctly reported nonzero `lost` counts while the API reported `percentage=100` and `loss_percentage=0` with `percentage_ready=false`. The bridge now returns `null` for both percentage fields until the minimum settled sample threshold is met. A fresh controlled total-loss run after restart verified the corrected behavior: once ready, affected flows reported `0%` delivery and `100%` loss.

**F-005 — Closed false alarm — The earlier restart was not a true container recreation.**

An initial post-restart test found stale 20% Platform_30 bidirectional and 100% Platform_35 forward impairments still active in the controller state. Verification later showed the container start time had not changed because the first restart attempt was a no-op/permission failure. A confirmed owned `bash ... run_container_baseline.sh down` followed by `up` recreated all seven containers; the new run reported an empty impairment list. The test lesson remains: verify container recreation and an empty controller list before accepting restart evidence.

**F-006 — Closed false alarm — No impairment persistence across a true restart was found.**

The apparent cross-session persistence was the same stale process from the no-op restart, not persisted controller state. A real container recreation cleared the 20% profile. Test orchestration should still call reset-all and verify an empty impairment list before every scenario, because this catches failed teardown/restart attempts early.

**F-007 — P2 — Concurrent Experiment responses could overwrite newer state (fixed, regression-verified).**

Two rapid applies were both accepted by the controller, but the panel could render only the older response when requests completed out of order. `operator_tabs.js` now sequences Experiment requests and ignores stale responses. A rerun displayed both independent profiles, and reset returned to nominal state.

**F-008 — Closed false alarm — Presence state was misread by the diagnostic check.**

During a 20-second 100% bidirectional impairment, the initial diagnostic incorrectly searched for `presence` inside the nested `health` object. The contract places `presence` beside `health` on each mesh peer. Correct parsing produced `PRESENCE_DEAD` with a 32.5-second last-seen delta, while `health.overall_state=ROUTER_OK` correctly represented the peer's last published health summary. No product defect was established.

**F-009 — Fixed and regression-verified — TEAM_A PlatformData recipients were not team-scoped.**

With only Platform_30 and Platform_31 assigned to TEAM_A, the Delivery resolver incorrectly expected PlatformData recipients on every other platform. The bridge now derives expected recipients from shared non-identity `team_partition` values. A clean no-impairment baseline produced only 30→31 and 31→30 rows; the corrected 100% pair impairment produced only those two rows at 100% loss. `peers_seen` behavior remained correct. Control-observer link stats still do not expose direct platform-pair rows, which is a visibility limitation rather than an isolation defect.

#### Current disposition

The live dashboard transport blocker was mitigated before continuing. The mesh is currently at a clean checkpoint with zero active impairments and six peers visible. F-002 and F-003 are closed for the tested baseline; broader statistical repetition remains. F-004, F-007, and F-009 are fixed and regression-verified. F-005/F-006 are closed as false alarms caused by an unverified/no-op restart. F-008 is closed as a diagnostic false alarm. The remaining limitation is that Control-observer link stats do not expose direct platform-pair counters.

## Session Handoff

### Current runtime checkpoint

- Six-platform EMANE mesh is running: Control_20 plus Platform_30 through Platform_35.
- Dashboard is reachable through the mapped control container on host port `8080`.
- `/api/emane_controller` reports an empty `impairments` list.
- `/api/mesh_status` reports six visible peers.
- Browser transport badge is expected to read `WebSocket live`; REST fallback remains available if the Dev Tunnel drops WebSocket upgrades.
- Team assignments used for PlatformData testing were cleared after the last scenario.

### What is verified

- CommEffect apply/reset, bidirectional and one-way directionality, latency, jitter, bandwidth, loss, duplication, mixed degradation, recovery, and presence timing.
- Link Activity and `/api/link_stats` corroborate graph degradation and recovery for Control_20-observed WAN links.
- `peers_seen` correctly removes an impaired team peer while the platform can remain directly alive to Control_20.
- Delivery comparison cards distinguish reliable command/health topics from periodic/status topics using their 15 s and 2 s grace windows.
- PlatformData accounting is team-scoped; only shared non-identity team members are expected recipients.
- Raw EMANE event 103 reception was observed on exact Compose containers/NEMs, with no `No Profile` evidence in the TEAM_A peer test.

### Changes in this worktree

- `gui/mesh_dashboard/server/mesh_bridge.py`: team-aware PlatformData recipients and unready percentage semantics.
- `gui/mesh_dashboard/static/operator_tabs.js`: REST Delivery refresh, explicit CommEffect reset semantics, response ordering, and reliability comparison cards.
- `gui/mesh_dashboard/static/mesh_graph.js`: REST mesh/link fallback and transport-state tracking.
- `gui/mesh_dashboard/static/index.html`: transport badge, cache-busted asset revisions, and Delivery comparison layout.
- `.github/copilot-instructions.md`: live CommEffect, team-routing, presence-field, EMANE evidence, and tunnel lessons.

### Resume next

1. Re-check `git diff`, run focused Python/JavaScript diagnostics available in the environment, and review the changed files together.
2. Add or run automated regression coverage for team-scoped PlatformData recipients, unready percentage fields, and out-of-order Experiment responses.
3. Investigate whether direct platform-to-platform link counters should be exposed; the current Control_20 observer does not provide those rows.
4. Run the full all-topic matrix across multiple loss/bandwidth levels only after the clean precondition check and exact container/run-id verification.

Do not claim a clean restart unless the owned down/up lifecycle recreates the containers and the new run reports an empty controller impairment list.
