# RouterHealth wire-size review and optimization recommendations

Date: 2026-11-03

Status: Review only. No application, IDL, QoS, or live-mesh changes implemented.

Decision recorded after review: detailed remote router diagnostics should use the
existing **debug-mode status path**, not a new always-on WAN diagnostics stream.
Connected-peer/link-state information remains **always-on**, including outside debug.

## Executive recommendation

Separate the fast presence signal from detailed router diagnostics before investing
in bitpacking.

The current heartbeat repeats full route configuration and diagnostic strings every
second. In the measured mesh, route entries occupy **90% of control's serialized
heartbeat and 95% of a platform's**. Compact route summaries would reduce the
serialized message from approximately 5-6 KB to **708-852 bytes**, while retaining
the existing string identity, rollup, sequence, timestamp, teams, and peer roster.

Recommended order:

1. Correct fragment-aware traffic accounting so optimization is measured accurately.
2. Introduce a compact WAN heartbeat and put detailed diagnostics on the existing
   debug-mode status path.
3. Stop periodically retransmitting unchanged configuration and text.
4. Optimize peer representation and small state fields where measurements justify it.
5. Consider representation/compression changes only after the above.

The proposed layouts below are **estimates from decoded captured CDR**, not deployed
schemas or production-serializer benchmarks. Any new layout requires regeneration,
builds, compatibility checks, and actual packet-size verification.

## Evidence and measurement method

Two read-only captures were taken on control's `emane0` in the existing mesh:

- A 20-second capture established the MAC/DDS accounting discrepancy.
- A subsequent 3-second capture provided complete reassembled heartbeat payloads
  for field-level analysis.

The mesh had control plus five platforms. Observed heartbeat traffic was one control
copy per platform per second, and one platform copy to control per second. This is
the measured routing/partition state, not an assertion that every topology uses the
same recipient set.

The second capture was decoded according to
[ActTypes.idl](../../harness_v2/datamodel/ActTypes.idl). The payload had little-endian
CDR encapsulation `0x0001`. An independent encoder reproduced the consumed serialized
length, including CDR field alignment and the four-byte encapsulation header; up to
three bytes of final RTPS padding were excluded. Candidate sizes use the same
observed values and encoding rules, with selected fields omitted.

| Measurement | Control | Platform_30 |
|---|---:|---:|
| UDP datagram, including UDP header | 5,236 B | 5,828 B |
| Serialized RouterHealth, including CDR encapsulation | 5,145 B | 5,737 B |
| Full `routes` sequence | 4,645 B | 5,477 B |
| `routes` share of serialized message | 90.3% | 95.5% |
| Peer roster | 324 B / 5 peers | 60 B / 1 peer |
| Route / topic counts | 8 / 12 | 10 / 14 |

Sizes differ from the first capture's 5,284/5,588-byte UDP datagrams because the
message contains changing route diagnostics. These are snapshots, not fixed or
maximum sizes.

### Major repeated costs

The following field costs include alignment attributed to that field. Nested rows
are subsets of their parent, so they must not be added to the full route total.

| Repeated field group | Control | Platform_30 | Purpose |
|---|---:|---:|---|
| Full `RouterRouteSpec desired` | 1,839 B | 2,417 B | Configuration, endpoint names, partitions, QoS aliases, topic lists |
| Topic reader/writer QoS summary strings | 1,136 B | 1,069 B | Human-readable endpoint policy summaries |
| Per-topic status sequence, including those summaries | 2,199 B | 2,285 B | Topic state, counters, matching, warnings, errors |
| Route names | 206 B | 262 B | Human-readable identity |
| Topic names in topic status | 290 B | 330 B | Repeated names also present in route configuration |
| Command-cause strings | 40 B | 50 B | Last command attribution |

The population path is
[RouterController::apply_presence_tick](../../router/src/core/RouterController.cxx),
which copies every entry from `build_route_statuses()` into `RouterHealth.routes`.
That helper is also used for full LAN `RouterStatus` snapshots. Reusing that full
status type is the main source of unnecessary WAN repetition.

## What consumers actually need

The [message definition](../../harness_v2/datamodel/ActTypes.idl),
[presence monitor](../../router/src/core/PresenceMonitor.cxx), and
[dashboard](../../gui/mesh_dashboard/static/mesh_graph.js) establish these constraints:

| Data | Current consumer / required behavior |
|---|---|
| String router key | Roster identity, duplicate-identity detection, discovery-name correspondence, graph node identity |
| `heartbeat_seq`, `send_timestamp` | Heartbeat inspection and sequence-matched delivery audit |
| Role and rollup | Node classification and overall health display |
| Configuration hash | Configuration-drift detection and dashboard inspection |
| State revision | Detecting changes to a peer's reported controller state |
| Team partitions | Team rings, membership, team-scoped graph behavior |
| Peer presence and age | Directed topology, transitive edges, freshness, STALE/DEAD reporting |
| Route name/state | Resolution badges and route-state table |
| Route forwarded count | Route table's forwarded-message display |
| Route/topic diagnostics | Discovery state, errors, unresolved types, match counts, QoS warnings, match reason |

The dashboard does **not** currently read `health.routes[].desired`,
`caused_by_command_id`, per-route revision, lifecycle counters, or reader/writer QoS
summary strings. Their removal from the WAN representation is a good candidate,
but a repository-wide consumer check remains necessary before implementation.

Do not simply delete all route information: that would remove authoritative
resolution badges and remote route diagnostics. LAN `ActRouterStatus` already
contains full detail, but a control-side dashboard cannot assume it can access a
remote platform's LAN. Preserve remote visibility through an explicit WAN detail
path and merge it into the LAN/dashboard view.

## Option comparison

| Candidate | Control serialized | Platform serialized | Consequence |
|---|---:|---:|---|
| Current | 5,145 B | 5,737 B | Four Ethernet frames per observed heartbeat |
| Remove desired config, command IDs, and unused QoS summary strings | 2,137 B | 2,177 B | About 58-62% smaller, but still fragmented |
| Keep compact routes: name, operational state, discovery state, forwarded count | 852 B | 708 B | About 83-88% smaller; fits one frame in this rig |
| No routes in heartbeat; keep identity, rollup, teams, peers | 500 B | 260 B | About 90-95% smaller; unsuitable for always-on route badges unless another compact path exists |

The compact-routes option is the recommended first target: badges, per-route state
and forwarding counts remain available in every mode. The no-routes option is not
recommended when detailed diagnostics are debug-only, because basic operational
visibility must not require enabling debug.

For the observed recipient set, the estimated heartbeat-only Ethernet load is:

| Candidate | Whole mesh, 1 Hz |
|---|---:|
| Current | 453.4 kb/s |
| Remove repeated config/text | 185.3 kb/s |
| Compact route summaries | 72.4 kb/s |
| No routes | 40.4 kb/s |

These estimates include UDP/IP/Ethernet framing using the observed 1,500-byte MTU
and approximately 91 bytes of observed UDP/RTPS overhead beyond serialized health.
They exclude EMANE headers, acknowledgments, repairs, discovery, and the proposed
separate diagnostics traffic. They are not total RF-capacity or airtime estimates.

## Recommended message split

### Fast heartbeat: periodic, small, self-contained

Keep the presence and audit facts available without a successful diagnostics fetch:

- Existing name key initially; retain identity semantics.
- Heartbeat sequence and send timestamp.
- Configuration identity and controller revision.
- Overall state and route counts.
- Compact route state/forwarding summaries if needed for continuously fresh badges.
- Team/topology state, initially as current fields or references to separately
  versioned snapshots once that mechanism exists.

### Always-on connected peers and link state

Knowing which peers each router currently hears is a required operational feature,
not detailed debug telemetry. Keep `peers_seen` in the initial compact heartbeat,
with peer identity, ALIVE/STALE/DEAD state, and age since the last received heartbeat.
The measured 708-852-byte compact-route estimates already include this roster;
they do not achieve their savings by removing connected-peer information.

Preserve the direction of every observation: A reporting B means A has heard B.
It does not prove B hears A, nor that every application route between them works.
The graph needs both observations to distinguish bidirectional connectivity from
an asymmetric link. Preserve roster information used for second-hand graph edges.

Required behavior in `init`, `mission`, and `debug`:

- Report newly heard peers and transitions to STALE/DEAD without enabling debug.
- Retain explicit disconnected states for the defined retention interval; do not
  publish only an ALIVE list whose omissions cannot distinguish loss from incomplete
  data.
- Age cached peer observations forward, including transitive observations.
- Show topology/presence separately from transport quality. A connected peer can
  still have a degraded link; retain the existing link-statistics view rather than
  treating a peer-list entry as proof of healthy delivery.
- Mark stale/missing observer information explicitly instead of presenting an old
  roster as current link state.

Peer IDs and state bitpacking are possible later optimizations, but peer identity
mapping and freshness must be available outside debug. If a large deployment needs
a separate compact topology topic, that topic must also be always-on and recoverable,
with a bounded refresh interval. A topology revision alone, or a debug-only full
snapshot, is not enough to establish current connected peers.

Preserve the heartbeat cadence and presence semantics in this size optimization.
Do not simultaneously change reliability, deadline, liveliness, or recipient scope:
that would make the performance result harder to attribute.

### Detailed diagnostics: debug-mode path, versioned updates

Detailed platform-router diagnostics should share the existing
`platform_debug_status` route's **source-side debug gate**. Do not add an independent
always-enabled WAN detail publisher, and do not require control to toggle its
destination-side relay per platform.

The established route is defined in
[control-platform.yaml](../../router/config/control-platform.yaml):

- Source: platform LAN, forwarded through platform WAN on partition `PLATFORM`.
- Destination: control WAN to control LAN.
- Source-side route is disabled in `init` and `mission`, enabled in `debug`.
- Control's destination-side relay remains enabled/ready.
- [platform_mesh_control.py](../../harness_v2/scripts/platform_mesh_control.py)
  already enables/disables this route for `ActPlatformStatusMode`.

Proposed implementation, not yet applied:

1. Publish a distinct router-owned LAN diagnostics topic, provisionally
   `ActRouterDiagnostics`, from the router's detailed snapshot. Do not make the
   platform simulator synthesize router internals.
2. Add that topic to the `platform_debug_status` route alongside
   `PlatformDebugStatus`, `PlatformThrusterStatus`, and `PlatformPowerStatus`.
   Preserve the dedicated topic/type boundary rather than embedding router state
   inside actuator/power telemetry.
3. Reuse the detailed LAN snapshot builder where appropriate; use a clearly named
   topic and source key so relayed remote diagnostics cannot be confused with
   control's own local `ActRouterStatus`.
4. On entering debug, provide a current full snapshot even if no configuration
   revision changed. Validate startup/route-enable replay or explicitly trigger
   refresh after the route is ready; do not rely on an old cached snapshot.
5. While debug is active, send meaningful diagnostic changes and a bounded counter/
   refresh cadence. Keep full configuration descriptions out of periodic heartbeats.
6. On leaving debug, stop detailed WAN forwarding and clearly mark the dashboard's
   retained detail as historical/debug-off.

This is a routing gate, not a simulator log-verbosity setting.

| Mode | Compact RouterHealth | Connected peers / directed link state | Basic route state/counts | Full remote router diagnostics |
|---|---|---|---|---|
| `init` | Always on | Available, including presence and age | Available | Not forwarded |
| `mission` | Always on | Available, including presence and age | Available | Not forwarded |
| `debug` | Always on | Available, including presence and age | Available | Forwarded through `platform_debug_status` |

Full configuration, per-topic match/error/QoS details and command attribution belong
in debug diagnostics. Rollups and compact per-route operational states must remain
always-on so an operator can identify trouble and decide to enable debug. Metadata
required to decode the fast heartbeat must also remain available outside debug;
the first version should retain readable route names rather than depend on a
debug-only dictionary.

Control's own router diagnostics are already available locally on its LAN. This
recommendation does not add a control-to-platform diagnostics flow or a new
control debug-mode contract.

Implementation requirements:

- Use a new WAN-specific summary/detail type rather than slimming shared
  `RouterRouteStatus`, which is also the LAN admin/status contract.
- Define cache identity and ordering across writer restart and configuration change.
- Carry a diagnostics generation/version separate from controller `state_revision`.
  Forwarded counters change without incrementing that revision; revision-only
  publishing would freeze the current counter display.
- Keep heartbeat freshness independent of detail freshness.
- Show missing/stale/debug-off detail explicitly; cached detail is not current
  reachability. The UI may offer the existing debug-mode action to obtain detail,
  but must not silently enable debug merely because a node was clicked.
- Preserve route removal, team leave, DEAD peers, and reset-to-healthy transitions.
- A delta-only protocol needs a known baseline, gap detection and resynchronization.
  Prefer full compact snapshots first; add deltas only if their benefit warrants
  the additional recovery machinery.
- Author all fixed QoS in
  [act_qos_profiles.xml](../../harness_v2/qos/act_qos_profiles.xml).
- Preserve the existing debug route's endpoint-QoS pattern initially. Validate that
  it actually delivers full diagnostics snapshots under loss before choosing any
  separate XML endpoint alias; do not assume sharing the route guarantees the
  desired replay/reliability semantics.

With debug disabled, the 72.4 kb/s compact-heartbeat estimate above has no extra
remote full-diagnostics traffic. With debug enabled, detailed snapshots legitimately
increase WAN/MAC usage; chart and measure those bytes as debug diagnostics, not as
unexplained overhead. Rate-limit large snapshot refreshes independently of the
1 Hz presence heartbeat.

## Bitpacking and field-level recommendations

Bitpacking is useful after removing the repeated text/configuration. It cannot
recover kilobytes when most bytes are strings.

### 1. Explicit packed state bytes, not C++ bitfields

A compact topic-state word could use:

| Bits | Meaning |
|---|---|
| 0-2 | Topic state: five current values |
| 3-4 | Discovery state: three current values |
| 5 | Type resolved |
| 6 | Has error |
| 7 | Has QoS warning |

Use an explicitly sized IDL `octet` or `uint16` container, documented masks, named
encode/decode helpers, and reserved-bit rules. The current topic state/discovery/
type-resolved fields occupy two four-byte enums plus a boolean, with surrounding
alignment. An explicit byte can reduce this, but the actual total depends on field
order and the following aligned fields.

Route operational/discovery state similarly needs only five meaningful bits today.
Avoid packing unrelated quantities into a dense opaque blob: typed fields remain
easier to inspect and evolve. Decoders must handle reserved/unknown values explicitly.

**Connext verification:** Connext AI recommended explicit scalar flags rather than
assuming native IDL bitmask/bitset support or C++ bitfield layout determines wire
size. A disposable test with the installed `rtiddsgen 4.7.0` successfully converted
`octet`, `unsigned short`, and `uint8` fields to XML and generated C++11
`uint8_t`/`uint16_t` members. Native `bitmask`, `bitset`, and `@bit_bound` were not
empirically validated in this review and should not be prerequisites for the design.

### 2. Replace stable descriptive strings with metadata references

- Route/topic numeric IDs can replace repeated names after a reliably available
  name dictionary exists.
- IDs must be stable across both sides' metadata version and must support runtime
  added/removed routes. Do not use array position as an implicit identity.
- Unknown IDs require an explicit unknown state and metadata refresh, not a guessed
  label.
- Encode role as a small scalar if its vocabulary is intentionally closed.
- Send the configuration digest as 32 binary bytes instead of a 64-character hex
  string. This saves roughly 37-40 bytes including string framing/alignment while
  preserving the full digest; render hex in the bridge. Do not truncate the hash
  merely to save a few bytes.

Keep the name key for the first optimization. A numeric router key is a separate
identity-contract migration, not a harmless packing change.

### 3. Compact peer roster

At the measured five-peer control roster, peer names/state/age cost 324 bytes.
After route slimming, this becomes an important fraction of the remaining message.

Options:

- Dictionary-backed peer ID, small state, and bounded age.
- For a fixed indexed topology, separate ALIVE/STALE/DEAD bitsets plus ages.
- Move topology to a versioned, change-driven snapshot and reference it from the
  heartbeat.

The third option avoids a heartbeat that grows linearly with every possible peer,
but its topology updates must remain always-on, not debug-gated.
Numeric IDs/bitsets require stable mappings, completeness semantics and recovery.
An absent entry must not silently mean DEAD. Preserve explicit transitions and
transitive-edge aging in `PresenceMonitor::build_mesh_locked()`.

For age quantization, document units, maximum age, rounding, and an explicit overflow/
unknown value. For example, a 16-bit age at 100 ms resolution covers about 109 minutes,
but that is a behavioral change, not a lossless substitution for `int64` milliseconds.

### 4. Counters and field ordering

- Keep cumulative forwarded counters at 64 bits unless mission duration and maximum
  rate justify a smaller range. At 1,000 increments/s a 32-bit counter wraps in
  about 49.7 days.
- A 32-bit heartbeat sequence at 1 Hz lasts about 136 years, so narrowing it is
  plausible, but changes the IDL/audit contract for only four bytes of saving.
- Keep the send timestamp semantics used by delivery matching; changing units or
  dropping it would require an audit redesign.
- Smaller match counts/count fields need documented ranges and explicit overflow.
- Group aligned wide fields, then smaller scalar fields; measure the resulting
  CDR size instead of using `sizeof(C++)`.
- Empty strings still have length/NUL/alignment cost. Omit healthy diagnostic text
  from the fast message rather than repeatedly sending empty placeholders.

### 5. Compression, XCDR2, and traffic fan-out

- Do not make an opaque compressed heartbeat the first solution. It preserves the
  oversized semantic contract and makes inspection, evolution and error handling
  harder. Consider compression for separately transferred large snapshots only.
- A different CDR representation may reduce padding, but cannot remove the main
  duplicated configuration/text cost. Verify supported representation and
  extensibility behavior on the actual generated types before budgeting savings.
- Do not switch to best-effort or multicast simply to lower the measured byte
  count. Those changes affect delivery, reverse-path repair and peer visibility,
  and should be separate experiments.
- Recipient fan-out matters: compact messages still cost one unicast copy per
  matched destination in the observed path. Measure actual matching under common
  teams as well as the current control/platform pattern.

## Size budget and scaling

Target **UDP datagrams <= 1,200 bytes** for the frequent heartbeat, leaving margin
under the observed 1,500-byte MTU for RTPS variation and evolution.

The 708-852-byte estimates meet this target only for the measured roster/routes.
Current string and sequence fields are not a worst-case wire-size guarantee.
Seventy platforms, long names, added routes, many teams or diagnostic text can exceed
the target again.

Before declaring the design bounded:

1. Define maximum names/counts and the supported deployment size.
2. Keep full error/QoS/configuration text out of the frequent message.
3. Move or compact the growing roster and route lists.
4. Budget metadata, detail snapshots and resynchronization traffic too.
5. Make any truncation visible and distinguish complete from partial snapshots.

## Compatibility and verification before implementation

1. Decide the new heartbeat/detail contract and mixed-version migration strategy.
   Do not assume old and new readers remain compatible after removing/retyping fields.
2. Change the single IDL source of truth and regenerate committed XML plus C++
   types; keep LAN admin behavior intact.
3. Update presence caching/mesh aggregation and bridge decoding.
4. Preserve dashboard labels, remote diagnostics, team membership and directed
   topology. If static JavaScript changes, bump its asset revision.
5. Test restart, duplicate identity, late join, stale/missing metadata, out-of-order
   snapshots, route deletion and recovery after impairment.
6. Verify the mode matrix through the real status-mode control path: no detailed
   WAN diagnostics in `init`/`mission`, immediate fresh detail after debug enable,
   source-side forwarding stops after debug disable, unrelated platforms remain
   unchanged, and compact presence/badges plus connected-peer/link-state updates
   continue in every mode. Exercise a one-way link loss and recovery with debug
   disabled; verify both directional observations and their freshness. Verify local
   diagnostics remain available even when their WAN route is disabled.
7. Test exact maximum UDP size, fragment count and output shape, including the
   largest supported roster/routes/names.
8. Replay a known fragmented capture against the fixed accounting path; include
   every fragment exactly once. Reassembly displayed on the last frame must not
   result in counting only that last frame's `frame.len`.
9. Measure per-source/per-recipient heartbeat, details, discovery, repair and MAC
   traffic independently. Validate nominal and impaired runs through the owned
   lifecycle; do not mutate a running mesh piecemeal.

Existing
[presence tests](../../router/test_e2e/test_presence_roster.py) and
[mesh/team tests](../../router/test_e2e/test_mesh_team_partition.py) are relevant
regression surfaces. No full router gate was run for this documentation-only review.

## Documentation discrepancies to resolve during implementation

[Presence & Health](presence-and-health.md) describes a summary-only WAN heartbeat
without a full route table. The implementation now sends that full table. The new
contract should reconcile this discrepancy explicitly.

There is also an inspected timing mismatch: `router_health` in the checked-out XML
has a 2-second deadline/3-second lease, while
[PresenceMonitor.hpp](../../router/src/core/PresenceMonitor.hpp) declares 8/12 seconds.
This review did not change either or establish which settings the running writers
actually loaded. Resolve effective timing separately before relying on a presence
regression gate; do not mix a timing retune into the size comparison.

## Sources and validation limits

- Repository sources and actual WAN capture are primary evidence.
- Connext AI product guidance was consulted, then the supported scalar-field
  code-generation path was checked against the installed generator.
- The known-issues submodule was fetched and both the pinned document and freshly
  fetched `origin/main` were searched for matching serialization/bitpacking
  corrections. Its checkout is detached, so `git pull --ff-only` could not merge;
  the parent repository's submodule pin was intentionally left unchanged.
- Official background:
  [RTI data representation guidance](https://community.rti.com/static/documentation/connext-dds/7.7.0.1/doc/manuals/connext_dds_professional/extensible_types_guide/extensible_types/Data_Representation.htm).
  Representation-dependent savings and native bitmask support were not established
  by a runnable serialization benchmark here.
