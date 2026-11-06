# Accurate EMANE accounting and compact router health

Date: 2026-11-04

Status: **Implemented and verified in a matched one-C2/one-platform EMANE lab.
Fleet rollout and broader RF/scale acceptance remain separate gates.**

## Implementation and measured result

The approved accounting, view toggle, compact heartbeat, and debug diagnostics are
implemented together. The original characterization and proposed gates below are
retained as the historical baseline, not a description of the current code.

| Fresh normal-mode measurement | C2 | Platform |
|---|---:|---:|
| RouterHealth UDP datagram bytes, including UDP header | 684 | 892 |
| IPv4 fragments per heartbeat | 1 | 1 |
| MAC TX packets / captured Ethernet TX frames | 103 / 103 | 115 / 115 |
| Corrected monitor / independently reconstructed RTPS TX bytes | 21,628 / 21,628 | 29,252 / 29,252 |
| MAC accounted TX rate | 18.55 kb/s | 24.08 kb/s |
| RTPS physical Ethernet TX rate | 14.25 kb/s | 19.27 kb/s |

These are approximately 12.14-second normal-mode windows, not application-payload
byte rates. Both captures reported zero drops. MAC bytes again exceeded Ethernet
bytes by exactly 63 bytes/frame **in this measured stack**; this is evidence, not
a universal conversion or a correction applied to counters.

Retained measurements: [C2](../../debug/test_reports/optimized-Control_20.json),
[platform](../../debug/test_reports/optimized-Platform_30.json), and
[captures](../../debug/pcap/router/optimized-telemetry-validation/).
Replaying the corrected production parser on the original fragmented captures also
matched the independent totals exactly: 125,414 C2 bytes and 166,000 platform bytes.

Verified debug entry, exit, and re-entry: full platform snapshots reached C2,
publication stopped outside debug, and a fresh sequence arrived on re-entry.
Compact route state, connected peers, RouterLinkStats, and normal application
status remained available. Full diagnostics retain errors, endpoint/QoS detail,
configuration identity, revision, independent sequence, and source timestamp.
The bridge marks historical, stale, wrong-configuration/revision, and pre-entry
snapshots as noncurrent.

Browser checks verified the unchecked default, node MAC/RTPS overlays hidden until
enabled, normal C2 link statistics retained, visible-series-only peaks/scaling,
three-second averaging, cache-busted scripts, and coupled panel/graph geometry.
Native serialization also preserves wide counters and a 70-peer roster; large
rosters are not promised to remain below one MTU.

Validation passed: all four C++ test groups, 90 targeted presence/startup/mission
regressions, 17 accounting/diagnostic contracts, and the headless chart contract.
The 70-peer serialization fixture produced 3,500 CDR bytes and preserved STALE
peer observations and counters above 32 bits. This is a fixture measurement, not
a maximum-name or maximum-route wire-size bound.

[Live browser/API acceptance](../../debug/test_reports/optimized-ui-validation.json)
also verified debug exit/re-entry, invalid input returning HTTP 400, clean RF state,
and correct topic attribution after capture rotations. A final offline browser check
loaded the declared `traffic_chart.js?v=20261104-layer-accounting-v3` and verified
that long expanded diagnostics scroll above the chart rather than covering it.
The owned test mesh was torn down and no DDS shared-memory artifacts remained.

### Deployment and remaining acceptance

**RouterHealth and embedded RouterMeshStatus changed schema. Restart all matched
routers and LAN consumers together. Do not mix old and new binaries/type XML.**
Uninterrupted rolling fleet upgrades still need the versioned-topic transition
described below. Heartbeat cadence and presence deadlines/leases were not retuned.

Before a fleet release, repeat multi-platform shared-team routing, directional
impairment/reset with the merged diagnostics path, and maximum-name/route/roster
wire-size and resource tests. The one-platform lab is not proof of those gates.
Current live tshark summaries expose capture errors, invalid rows, fragment
incompleteness, freshness and rotation gaps. Kernel-drop counts are explicitly
unreported (`null`), not asserted zero; independent dumpcap verification reported
zero drops for the measurements above.

## Objectives and operator contract

1. Make EMANE/DDS traffic statistics accurate, with explicit measurement layers,
   sampling intervals, completeness, and freshness.
2. Make the graph's normal view the **C2 operator view**. Add an
   **Infrastructure stats** checkbox, unchecked on a fresh page.
3. Reduce always-on router overhead without losing connected-peer/link state,
   route-health summaries, or normal C2 functionality.
4. Put full remote router diagnostics on the debug-mode path rather than repeating
   them in every WAN heartbeat.

**RouterLinkStats is normal C2-visible information.** Keep `ActRouterLinkStats`,
link activity/detail, RTT/repair indicators, peer topology, presence, route badges,
and mission/delivery information accessible without the infrastructure toggle.
Do not hide every `Router*`/`ActRouter*` topic by name.

The checkbox is a **presentation filter**, not a runtime control. It must not
disable heartbeats, probes, statistics collection, routes, or debug mode, and
must not change network traffic.

## Preimplementation characterization

### Preimplementation heartbeat baseline

[RouterHealth](../../harness_v2/datamodel/ActTypes.idl) originally contained
`sequence<RouterRouteStatus> routes`. The controller's
[heartbeat builder](../../router/src/core/RouterController.cxx) copies full route
statuses, including repeated desired configuration and topic diagnostics.

The earlier compact implementation exists only in an isolated experimental
worktree. Its native serialization and runtime results demonstrate feasibility;
they have not been merged into the production schema or routing path.

The source-side [platform debug route](../../router/config/control-platform.yaml)
already gates platform debug status, while the control destination relay stays
enabled. It does **not yet carry a router-owned full-diagnostics topic**.

### Fresh EMANE characterization

A temporary one-C2/one-platform mesh was launched through the canonical
`run_mesh.sh up --emane` lifecycle, with simulators and the dashboard. No other
mesh/DDS test was active. This used the normal lab domains 20/30/200, not the
201/202/203 domains from the earlier isolated optimization experiment.

Preconditions: both peers discovered, no recorded impairment profiles after
explicit reset. Each node had a bounded 28-second `emane0` capture. Independently
read cumulative MAC counters bracketed approximately 20.15 seconds within those
captures; query midpoint timestamps were used for alignment. Captures had zero
drops. Both nodes reported zero nominal MAC TX/RX drops.

| TX measurement | C2 | Platform |
|---|---:|---:|
| MAC packets | 234 | 273 |
| Captured Ethernet frames | 234 | 273 |
| MAC-accounted bytes | 140,226 | 183,269 |
| Captured Ethernet bytes | 125,484 | 166,070 |
| Difference | 14,742 | 17,199 |
| Difference per frame | **63 B** | **63 B** |

RX reconciled exactly too: each node's RX packet/byte totals matched the opposite
node's TX totals, with the same 63 B/frame difference from its receive capture.

For this installed EMANE 1.5.3 RF Pipe + CommEffect stack:

```text
MAC-accounted bytes = captured Ethernet bytes + 63 * frame_count
```

**The raw MAC TX/RX counters are consistent with the captures in this nominal
test.** The extra bytes are accounted stack overhead, not evidence of phantom
application traffic. The exact internal header composition was not independently
dissected, and 63 B is an observed property of this configuration, not a universal
EMANE constant. Do not subtract a hardcoded 63 B from production counters.

### The DDS capture is missing most of the traffic

The full captures were replayed through the monitor's **actual BPF**, compiled
with libpcap, then through the same RTPS dissection. This is not a substitute
UDP display filter applied after reassembly.

| TX measurement, kb/s | C2 | Platform |
|---|---:|---:|
| MAC-accounted rate | 55.68 | 72.77 |
| Complete RTPS, summing physical Ethernet frames | 49.79 | 65.91 |
| RTPS surviving the current capture BPF | 8.79 | 12.12 |
| RTPS bytes missing from capture accounting | **82.35%** | **81.61%** |

The last line is an accounting defect, not packet loss. It measures all surviving
RTPS TX bytes, not just the current chart's blue "user data" series.

Each node transmitted 20 fragmented RTPS datagrams in the measurement window.
Their physical bytes exactly explain the missing RTPS totals: 103,280 B for C2
and 135,480 B for the platform. Another 70 Ethernet bytes per node were non-RTPS.

The complete explanation for the measured MAC-versus-old-capture gap is:

1. Missing fragmented RTPS datagrams.
2. MAC-accounted overhead above the captured Ethernet layer.
3. The small amount of non-RTPS traffic.

### Other confirmed accounting issues

- **Protocol/application classification:** the monitor identifies user-defined
  writer kinds, not application topics or payload-bearing submessages.
  `RouterHealth` and probes are consequently not separated from operational data.
  The installed dissector identifies `APP_ACK`/`APP_ACK_CONF` as submessages
  `0x1c`/`0x1d`; these are absent from the current reliability set. Both nodes sent
  40 ACK-only frames, totaling 5,440 physical bytes per node, that the current
  classifier calls `data`.
- **Aggregation intervals:** the bridge sums node deltas and divides by one
  interval; traffic aggregation uses an initial node interval and MAC aggregation
  uses the maximum. The correct rate is the sum of individually normalized node
  rates. In 27 reconstructed live MAC aggregates, the present MAC formula
  underestimated that reference by 0 to 0.164%. This was small in the nominal rig;
  larger interval differences, slow HTTP posts, or failed polls are not covered
  by that result.
- **MAC sample timing:** capture-loop timestamps are reused for a later MAC
  query. MAC reads need their own timestamped baseline and elapsed interval,
  independent of HTTP delays and packet-capture drains.
- **Freshness:** aggregation waits for every configured observer to advance.
  A stopped monitor can prevent publication while an old aggregate remains
  cached. Missing contributors and stale data must be visible, not healthy zeros
  or indefinitely frozen "live" rates.
- **Topic identity after startup:** the fresh steady-state captures contained no
  SEDP topic-name announcements. A passive writer-to-topic classifier that starts
  late cannot assume it knows every writer. Unknown traffic must remain explicit.
- **Layer labeling:** MAC bytes are not Ethernet bytes, application payload bytes,
  RF airtime, or delivery percentage. The present "Mesh RF aggregate" and
  percentage-of-1-Mb/s wording imply more than these counters establish.

Evidence: [compact characterization report](../../debug/test_reports/emane-stats-20261104T201828Z.json).
Raw captures, exact-BPF replays, capture-drop logs, and API observations are retained
under [the characterization capture directory](../../debug/pcap/router/emane-stats-20261104T201828Z/).
The temporary mesh was torn down through its owned lifecycle afterward.

## Proposed implementation sequence

### Phase 1 — Correct accounting before changing the heartbeat

Primary surfaces:
[domain_traffic_monitor.py](../../debug/scripts/domain_traffic_monitor.py),
[mesh_bridge.py](../../gui/mesh_dashboard/server/mesh_bridge.py), and metric tests.

1. Capture the selected IPv4 DDS domains' initial **and subsequent** UDP fragments.
   Associate fragments with their datagram/domain and classify the reconstructed
   RTPS message. Sum every captured physical frame exactly once.
2. Do not count only the final fragment's `frame.len`. Distinguish:
   IPv4 fragments, RTPS `DATA_FRAG` messages, and application samples.
3. Bound fragment/reassembly state and expire incomplete groups. Report incomplete,
   orphaned, ambiguous, or dropped capture data explicitly; never infer user
   payload bytes from a missing fragment.
4. Account for all applicable installed RTPS DATA/control variants, including
   batch/session traffic and application ACKs. Verify against installed dissector
   output and real captures rather than guessing submessage IDs.
5. Keep mutually exclusive physical-frame classes and separate TX/RX. Sum each
   sender's TX once for network load; do not sum TX plus every receiver's copy as
   though they were distinct transmissions.
6. Preserve raw per-node MAC deltas. Timestamp actual MAC reads, use their actual
   elapsed interval, and rebaseline explicitly after a restart/reset or failed
   read. Do not clamp a reset into a plausible-looking healthy interval.
7. Publish authoritative rate fields:

   ```text
   node_MAC_TX_Bps = node_MAC_TX_delta_bytes / node_MAC_elapsed_seconds
   network_MAC_TX_Bps = sum(fresh node_MAC_TX_Bps)
   ```

   Apply the same rule to DDS and packet/drop rates. Raw sums may remain as
   diagnostics, with source intervals attached; they are not one aligned interval.
8. Add contributor coverage, age, capture health, and stale/partial status to
   REST and WebSocket outputs. Cover `/api/traffic_stats`, `/api/emane_stats`, and
   the Experiment tab's `observed_network`; do not leave that second display stale.
9. Expose capture subprocess errors, capture drops, and capture/restart gaps.
   Bound buffering and disk use. Avoid an unexplained gap at every current
   60-second tshark restart, including loss of reassembly/topic metadata.

This phase is primarily a monitor/HTTP accounting change; it need not change
DDS types, QoS, or application routing.

### Phase 2 — Define honest operator/infrastructure categories

Normal view is defined by the C2 operational contract, **not an entity-kind byte
or a broad topic-name prefix**.

| Information | Normal graph view | Infrastructure enabled |
|---|---|---|
| C2 commands/ACKs, platform status, mission/delivery information | Keep | Keep |
| `ActRouterLinkStats`, link activity/detail, RTT/repair metrics | Keep | Keep |
| Peer topology, directional observations, ALIVE/STALE/DEAD, route rollup | Keep | Keep |
| Additional WAN heartbeat/probe byte breakdown | Hide | Show |
| Discovery, standalone RTPS control/application ACK traffic | Hide byte breakdown | Show |
| MAC-accounted totals, capture-layer reconciliation, non-RTPS residual | Hide detailed accounting | Show |
| Mixed/unknown attribution and classification completeness | Show warning if it affects the user series | Show full breakdown |

Important details:

- `ActRouterLinkStats` is published on the admin LAN. Its metrics remain visible
  normally, but its LAN publication bytes must not be added to a domain-200 WAN
  byte chart.
- Keep operational repair/NACK indicators in normal link health even when
  standalone protocol **byte accounting** is hidden.
- Build an explicit reviewed topic-role registry from the current C2 interfaces
  and application routes. Treat team/platform application traffic intentionally;
  do not accidentally call it infrastructure just because C2 is not its recipient.
- Obtain writer GUID-to-topic attribution from discovery and an authoritative
  warm-start mechanism. First check whether existing local endpoint/journal
  registrations provide the **actual local WAN writer GUIDs**, not upstream
  application GUIDs. If insufficient, add a small LAN-local endpoint catalog.
  Validate the required SDK API before committing to that implementation.
- Capture discovery before DDS endpoints start where practical, but do not rely
  on startup order alone: monitor restarts must be supported. No new WAN
  dictionary or DDS participant should be introduced just to measure traffic.
- Unknown mapping must remain unknown. Do not silently classify unknown data as
  user traffic or claim complete application accounting when it is incomplete.
- When one frame contains multiple topic roles, count its physical bytes in a
  mixed category. Do not charge the entire frame to multiple categories.
  If a payload-only estimate is added, label it separately from frame bytes.

### Phase 3 — Add the default-off graph toggle

Surfaces:
[index.html](../../gui/mesh_dashboard/static/index.html),
[traffic_chart.js](../../gui/mesh_dashboard/static/traffic_chart.js), and only
directly affected graph/Experiment consumers.

- Add a clearly labeled `Infrastructure stats` checkbox to graph controls.
  Start unchecked on a fresh page. For the first implementation, do not persist
  an enabled value that surprises the next operator.
- Unchecked: normal C2 graph and operational metrics, with a truthfully labeled
  application/operational series. Preserve topology and existing link inspection.
- Checked: add infrastructure/discovery/control/mixed/unknown series, the
  MAC-accounted TX/RX totals, contributor status, and layer definitions.
- Label the MAC total **all traffic**. Never suggest the toggle removes
  infrastructure from the actual radio load.
- Keep metric histories; switching views must not clear counters, issue
  impairment requests, change status mode, or restart data collection.
- Preserve the current 3-second average. Rescale peaks/axes consistently with
  visible series; do not let hidden infrastructure peaks flatten the user chart.
- Mark stale or incomplete data as such. A quiet valid interval is zero; a stopped
  monitor is not zero.
- Synchronize traffic-panel height and graph bottom spacing when toggling and on
  resize/tab changes. Bump affected static-asset version queries and browser-check
  the loaded revision, labels, DOM geometry, toggle behavior, and console errors.

**Do not ship the toggle by simply renaming the current blue line.** It contains
infrastructure/protocol traffic and is incomplete for fragmented datagrams.

### Phase 4 — Compact the always-on WAN heartbeat

Use the [wire-size review](router-health-wire-size-review.md) and prior isolated
prototype as the starting point, not an already-approved wire contract.

Retain in every mode:

- Router identity/role and configuration identity.
- Heartbeat sequence, send timestamp, and controller revision.
- Overall/route-health rollup.
- Teams and connected-peer identity, presence, age, and direction.
- Compact per-route name, operational state, discovery state, and uint64 forwarded
  count.

Replace only the WAN heartbeat's full route element with a dedicated compact
type. Preserve full LAN `RouterRouteStatus`/`RouterStatus` semantics.

The prior real-router prototype reduced C2 UDP heartbeats from 4,020 to 692 B
and platform heartbeats from 4,964 to 804 B, eliminating fragmentation in that
rig. Native scalar packing/binary-digest changes provided only an additional
10-16% over the representative compact payloads. Therefore do structural
separation first; packing is optional follow-up.

Target **<=1,200 B measured UDP**, not just serialized size. The string roster
does not meet that universally: the prior 20-peer fixtures were already
1,812/1,940 B serialized with compact routes. Before claiming the supported
deployment is fragmentation-free, validate its largest permitted identities,
team memberships, routes, and peer count. If necessary, design compact peer IDs
or bounded roster updates with always-on identity/generation metadata.
**Do not remove connected peers to meet the budget.**

Keep heartbeat cadence, presence policies, route behavior, and team semantics
unchanged in this phase. Do not bundle heartbeat-rate/QoS tuning with a schema
change.

### Phase 5 — Full router diagnostics on the debug path

- Publish a distinct router-owned LAN diagnostics snapshot; do not fabricate
  router diagnostics in the platform simulator.
- Route it through the existing `platform_debug_status` source-side gate.
  Keep the control destination relay ready.
- Full desired configuration, topic errors/matching/type/QoS detail, and
  command-cause strings belong in that snapshot, not every heartbeat.
- Publish a fresh snapshot on debug entry. Make generation/cadence explicit:
  `state_revision` alone does not track counter-only refreshes.
- Key/join snapshots by router identity and configuration/generation. Reject
  mismatched-generation detail.
- On debug exit or stale delivery, label detail **debug off / historical /
  unavailable**, while always-on health still updates. Do not silently keep old
  errors as current or show missing detail as a healthy empty list.
- Merge diagnostics in the bridge/dashboard without making mission tasking,
  discovery, connected-peer identity, or essential link health depend on debug.

### Phase 6 — Compatibility, controlled rollout, rollback

`RouterHealth` is also embedded in LAN `RouterMeshStatus`. Changing the route
element type affects generated C++, generated XML, bridge decoders, tests,
inspection tools, and other subscribers—not just a single WAN publisher.

Recommended first deployment: a **coordinated lab maintenance restart** of a
matched artifact set. Do not assume old/new types interoperate.

1. Inventory all producers/consumers and choose a type/version strategy before
   implementation.
2. Generate from the single IDL source, regenerate committed XML, rebuild, and
   validate the exact matched schema/config/binary bundle.
3. Validate accounting/UI changes first, separately from wire-format changes.
4. Schedule a controlled whole-mesh restart for the compact schema, preserving
   operator team/mode/mission state according to the existing lifecycle.
5. Verify peers, routes, command/ACKs, status delivery, and C2 mission availability;
   process uptime alone is not acceptance.
6. Roll back the complete matched bundle and owned mesh lifecycle if any gate
   fails. Do not manually repair one router in a mixed-schema mesh.

If uninterrupted fleet-wide rolling deployment is required, use a separately
versioned health topic with a compatible receiving transition. A dual-publish
period temporarily increases traffic and needs explicit retirement criteria.
That is additional scope, not something to assume this in-place prototype solves.

## Operational impacts and safeguards

| Change | Expected operational impact | Safeguard |
|---|---|---|
| Correct fragmented-DDS accounting | Reported DDS load rises sharply although actual traffic is unchanged | Explain metric-version boundary; do not call it a traffic regression |
| Correct MAC normalization/freshness | Rates may shift; partial/stale monitors become visible | Preserve raw counters and contributor/source-interval metadata |
| Graph infrastructure toggle | Display only; normal C2 link stats and health remain | No command, routing, mode, or collection side effects |
| Capture/topic attribution | More capture/reassembly work and local metadata | Measure CPU/memory/loss; bound state and artifacts; no new WAN stream |
| Compact heartbeat | Much less always-on traffic; expected lower fragmentation | Preserve identity/roster/health and validate under loss |
| Debug-only full diagnostics | Detailed remote text is unavailable or historical outside debug | Explicit UI state; essential operational rollups remain always-on |
| Wire-schema rollout | Restarts can interrupt tasking/status/forwarding; mismatched types can remove presence visibility | Maintenance window or explicit versioned migration; whole-bundle rollback |
| Debug enabled across many nodes | Detailed status traffic intentionally rises | Measure that mode separately; do not promise debug is as cheap as mission |

Mixed-version failure can affect operations even if some application routes still
forward: missing health can make the mission UI regard a platform as unavailable.
Do not treat this as a cosmetic dashboard-only schema change.

Keep two pre-existing issues explicit, but separate from the optimization:

- Current health XML specifies a 2 s deadline / 3 s lease while presence constants
  describe 8 s / 12 s. Resolve the intended operational policy in a separate,
  tested QoS change; do not silently retune it here.
- LAN mesh aggregation filters non-ALIVE transitive `peers_seen` entries. The
  prior one-way-loss test confirmed raw WAN health retains STALE state but the
  remote LAN transitive edge can be omitted. Define unknown/omitted versus
  explicit disconnected state before claiming full directional failure fidelity.

## Acceptance gates

### Accounting and EMANE accuracy

- Replay fragmented/unfragmented, out-of-order, duplicate, incomplete, RTPS
  `DATA_FRAG`, bundled, discovery/control, application-ACK, and unknown-writer
  fixtures. Every captured physical byte is counted once or explicitly
  unattributed; application samples and IP fragments are not conflated.
- Require exclusive category sums to equal their applicable TX/RX totals.
- Sum network rates from each node's own interval. Test deliberately unequal
  intervals and missing/delayed samples, not only synchronized 1-second monitors.
- Test counter reset, failed MAC read, capture failure/drop, monitor restart, late
  startup without SEDP, contributor loss, and dashboard reconnect/REST fallback.
- In the nominal live lab, reconcile paired MAC counter deltas to bounded packet
  captures with documented timing/layer differences. The current zero-residual
  63-B/frame relationship is a reference for this configuration, not a universal
  production correction or RF-airtime formula.
- Under controlled loss/latency/bandwidth changes, verify MAC/shim counters and
  audit-based delivery separately. MAC drops, RTPS repair, and end-to-end lost
  application samples must never be treated as interchangeable.

### Normal operations and heartbeat conversion

- Check init, mission, debug, and debug-exit behavior for both toggle states.
- Normal view retains C2 `RouterLinkStats` and peer/link health. Toggling causes
  no wire-traffic, mode, route, impairment, or collection change.
- Command/ACK completion and required application delivery remain at their
  nominal targets; run team reassignment, mission command, and status regression
  tests.
- Use at least two platforms sharing a team for platform-to-platform coverage;
  the one-platform characterization did not exercise that route.
- Repeat the prior directional loss/reset test with the new schema and merged
  diagnostics: correct primary presence, reverse observations, recovery, no stale
  detail mistaken for live data.
- Measure actual UDP size/fragment count at the supported maximum roster and
  route/name bounds, then measure all-node MAC TX with and without debug.
- Browser-check the expected asset revision, unchecked default, labels,
  contributor/stale indicators, graph/panel geometry, tab/resize behavior, and no
  console errors.
- Rebuild and run focused C++/Python gates first, then the required broader
  router/mission suite in a verified Connext environment. Do not claim prebuilt
  binaries or zero-test CTest output establish compatibility.

## Original proposed approval/order

1. Approve accounting definitions and the normal C2/infrastructure view boundary.
2. Implement and validate Phases 1-3 without changing the heartbeat.
3. Approve the debug diagnostic contract and coordinated-versus-versioned rollout.
4. Implement Phases 4-5 together so compact health does not silently remove
   necessary diagnostic functionality.
5. Pass operational/size gates, then execute Phase 6.

The original approval/order is retained above for traceability. Implementation and
local verification are complete as described at the top; fleet rollout remains gated.
