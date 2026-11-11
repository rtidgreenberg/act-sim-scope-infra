# ACT comms characterization and workload-fidelity plan

**Date:** 2026-11-06

**Status:** Draft implementation and experiment plan; no workload changes or experiments performed.

**Evidence:** [Customer workload characterization](comms-workload-characterization.md).

**Discovery reference (2026-11-11):**
[RTI SPDP/SPDP2 guidance, ACT applicability, and open questions](cpp_router/participant-discovery-performance-guidance.md).
Treat its vendor benchmarks as reference workloads, not ACT results; resolve
their percentage/metric discrepancies before citing savings. The review also
distinguishes current single-WAN-participant/two-topic work from the historical
topology inventory below.

**Handling:** Internal, customer-derived technical information; review before external distribution.

## 1. Objective and approach

Characterize ACT's current DDS footprint, then reproduce the supported customer workload
as closely as available schemas and evidence permit. Fidelity means matching **topic and
type diversity, schema complexity, serialized sample sizes, keyed instances, publication
patterns, route scope, and discovery behavior**; matching aggregate kbps alone is insufficient.

Keep three results distinct:

1. **ACT as-is:** the actual system, including navigation and router observability.
2. **Customer-shaped operational profiles:** direct-type and aggregated alternatives behind
   a representative local DDS population.
3. **Historical stress profiles:** simulator contamination, locator amplification, and leaked
   discovery. These are not normal mission requirements.

First establish an accurate inventory and measurement path. Then calibrate two platforms
plus control before scaling or introducing link impairment. Do not adjust arbitrary traffic
until the total happens to equal a historical capture.

## 2. Current setup and concrete fidelity gaps

Repository sources:

- [ActTypes.idl](../harness_v2/datamodel/ActTypes.idl) and its committed
  [generated XML](../harness_v2/datamodel/gen/ActTypes.xml).
- [Shared router configuration](../router/config/control-platform.yaml).
- [Platform simulator](../harness_v2/sims/platform_sim.py) and
  [control simulator](../harness_v2/sims/control_sim.py).
- [QoS profiles](../harness_v2/qos/act_qos_profiles.xml).
- [Frame accounting](../debug/scripts/traffic_accounting.py),
  [traffic monitor](../debug/scripts/domain_traffic_monitor.py), and
  [dashboard measurement definitions](../gui/mesh_dashboard/README.md).

Read-only `/api/mesh_status` observation on this date showed `Control_20` observing six
`PRESENCE_ALIVE` platforms, `Platform_30` through `Platform_35`. Each advertised only its
identity partition; detail/debug routes were disabled. This is one control-side snapshot,
not a complete endpoint inventory or proof of packet-level isolation.

| Dimension | Current evidence | Customer-shaped requirement / action |
|---|---|---|
| Topic/type population | Small fixed application model; types also include admin/infrastructure | Measure exact active inventories first; add 14 distinct operational types and a separate local-only discovery population |
| Sample shape | Flat application structs, bounded strings/sequences, embedded audit fields | Measure actual serialization; do not equate field bounds or JSON length with sample bytes |
| Basic status | `PlatformInitStatus` combines navigation/status; other status loops are generally periodic | Direct profile needs three distinct pose/velocity/speed topics, approximately 1 Hz each |
| Mission updates | Existing mission/waypoint status loops are periodic | Separate sparse collection changes from periodic vehicle telemetry; preserve existing UI behavior |
| Contacts | `contact_report` is keyed by `source`; changing `contact_id` does not create another DDS instance | New contact-element workload must have a real per-contact key and add/update/dispose behavior |
| Commands | Control sim sends a status request approximately every 5 seconds | Disable that background loop only in customer profiles; use explicit sparse, addressed mission events |
| Contact generation | Both platform and control sims publish contact reports approximately every second | Customer profile specifies measured platform report patterns; do not add control-origin contacts implicitly |
| WAN participants | Config defines `platform_wan` **and** `location_wan` per platform, plus `control_wan` | Source-config expectation is `1 + 2N` WAN participants, not `1 + N`; verify live GUIDs |
| Peer flows | `PlatformData` is team-scoped; navigation uses separate `NAVIGATION` routes | Attribute both separately; customer peer contacts are optional and their measured peer rate is unverified |
| WAN type handling | XML config requests types and uses SPDP2/SEDP; router supports discovered DynamicData routes | Measure current behavior; type-preloading/suppression must be an explicit, validated A/B variant |
| Wire/accounting | Existing frame classes, endpoint/topic association, WAN wire delivery and application audit | Extend reporting for type/schema inventory and serialization distributions, retaining unknown/mixed classes |

The current source topology would have **13 WAN participants at six platforms**, versus
**5 at two platforms**. These are expectations to test, not measured counts. A separate
one-WAN-participant-per-gateway profile would target three at two platforms, but must not
silently remove navigation needed by the normal ACT mission UI.

## 3. Deliverable: versioned workload and evidence manifest

Create one effective manifest per scenario, reused by generation, capture analysis, and
delivery expectations. Extend existing harness manifests rather than creating a parallel
experiment framework. Include:

- Run/test ID, revision and dirty-worktree status, configuration/schema hashes, Connext and
  analyzer versions, random seed, selected profile, topology, and actual startup/event times.
- Node/domain/interface/IP mapping; participant roles and expected counts.
- Every topic's distinct type identity, key fields, bounds, extensibility/representation,
  nested structures, active writers/readers, partitions, direction, and expected receivers.
- Sample-size convention, field-value cases, rate/event schedule, contact concurrency and churn.
- Effective XML QoS profiles and resolved environment substitutions; configured versus observed
  endpoint settings must be labeled separately.
- Provenance per target: customer measurement, workbook bound, ACT measurement, or proposed value.
- Capture point/link type, snaplen, timestamp alignment, missing evidence, capture drops, and
  retention locations.

Keep catalog definitions, instantiated topic entities, active endpoints, and transmitting
streams as separate counts. A catalog entry alone produces no representative endpoint
discovery workload.

## 4. Phase 1: characterize ACT as-is

### Inventory and size measurements

1. Extract the complete schema catalog from the IDL/generated XML. Record application versus
   infrastructure types separately, plus distinct types used by instantiated topics.
2. Inventory actual publications/subscriptions on control and selected platform LANs using
   bounded diagnostics and router journal evidence. Record a timestamped endpoint/GUID map.
   Diagnostic participants themselves must be labeled and excluded from target population counts.
3. Obtain WAN endpoint evidence from captures started before discovery, not an arbitrary DDS
   probe on WAN 200. Include both platform WAN participants and navigation endpoints.
4. Exercise each current application type with minimum, representative, and maximum legal
   field values in an isolated one-platform fixture. Identify representation and measure the
   serialized region including its four-byte encapsulation header and alignment padding.
5. Reassemble fragmented samples and keep sample-length distributions separate from DATA
   submessage length, UDP/IP length, and captured frame length. Capture measurement is the
   arbiter; a serialization API, if used, must be validated on the installed binding.
6. Measure type-definition traffic independently: definition identity/size where recoverable,
   requests/replies, endpoint announcements, fragmentation, and cumulative discovery cost.
   Workbook TypeObject bytes are reference inventory values, not exact expected wire bytes.

### Baseline runs

After coordinating teardown of the existing mesh, use the owned EMANE lifecycle for fresh
one-platform validation followed by two platforms plus control:

- Capture before participant creation; hold application publication behind a scenario gate.
- Cold start and discovery-only interval, then 10 minutes of current nominal operation.
- Fixed primary mode, identity-only partitions; repeat with detail/debug and then a shared team.
- Record navigation fan-out, team data, contacts, commands/acks, health/probes, and discovery
  independently. Keep wall-clock message rates unchanged by mission-navigation time scaling.
- Repeat key baselines three times to quantify discovery and scheduling variability.

**Exit:** Exact current counts, per-topic size distributions, per-direction traffic, and a
reconciled phase-level byte ledger. Unsupported or missing attribution is explicit.

## 5. Phase 2: build the fourteen-type operational fixture

Prefer approved original schemas if they can be provided locally. Otherwise create sanitized,
structurally representative distinct types: preserve keying, nested aggregates, sequence/string
bounds, optional/union structure where known, and type-name/member-name costs. Do not copy
unavailable schema structure by guessing; report the fidelity tier and remaining unknowns.

Use the repository's single IDL source-of-truth/code-generation workflow for added benchmark
types. Do not mutate existing application types just to make their byte counts match.

Targets below are **customer workbook values**, not newly measured serialization:

| Distinct type suffix | Sample min-max bytes | TypeObject bytes |
|---|---:|---:|
| MissionPlanObjectiveAddCommandType | 236-4,420 | 4,632 |
| TaskPlanTypeObjectivesSetElement | 148-4,332 | 4,364 |
| ContactReportTypeContactsSetElement | 156-4,212 | 9,584 |
| MissionPlanReportTypeMissionPlanSetElement | 144-2,716 | 3,992 |
| RouteObjectiveTypeWaypointsListElement | 117-1,521 | 18,928 |
| ControlSystemTransferRequest | 72-648 | 1,688 |
| ControlSystemControlReport | 68-456 | 2,020 |
| ControlSystemTransferReport | 60-384 | 1,824 |
| ControlSystemControlRequest | 56-380 | 1,400 |
| GlobalPoseReportType | 144-332 | 6,596 |
| VelocityReportType | 120-256 | 4,652 |
| MissionPlanReportType | 144-180 | 2,140 |
| ContactReportType | 100-120 | 2,044 |
| SpeedReportType | 60-104 | 2,512 |

The combined TypeObject reference is **66,376 bytes**. The source report's executive size
summary starts at 60 bytes, but its detailed table includes 56-byte control requests; use the
detailed table. Establish the workbook's encoding/encapsulation convention before exact
comparison with captured serialization.

Fidelity tiers:

- **Exact schema:** original approved schema and matching representation; verify samples and
  discovery empirically.
- **Structural surrogate:** 14 distinct types with matched structural and size properties;
  document per-type TypeObject differences and remaining semantic gaps.
- **Payload-only surrogate:** suitable only for preliminary data-throughput tests, not a
  discovery-fidelity claim.

**Exit:** Each type instantiated and routed, all size cases measured, distinct type identities
verified, and keyed contact lifecycle tested. Proposed surrogate tolerance is +/-10% per
TypeObject after establishing comparable encoding; if unmet, label discovery fidelity partial
rather than adding meaningless padding to force a match.

## 6. Phase 3: representative local discovery population

Add a local-only population generator through the harness, independent of WAN traffic:

- Start at **100-150 instantiated topic names and 40 local application participants per
  platform**; sweep to 80 participants. These are proposed brackets, not customer concurrency facts.
- Include explicit writers and readers, with deterministic endpoint distribution across
  participants. Record unique versus reused types and whether low-activity writers send an
  initial sample; do not invent 150 equally busy mission streams.
- Use measured/sanitized local schemas where possible; otherwise declare the synthetic
  complexity mix. Scale diversity separately from endpoint multiplicity.
- Test 401 catalog registrations separately from 401 instantiated topic/endpoints. The 154
  historical discovered names are a union over time, not a required simultaneous population.
- Keep the same local population during direct/aggregate and type-propagation A/B comparisons.
- Assert that local-only topics and domains do not appear on WAN. Measure the gateway's local
  discovery, type-resolution time, CPU/memory and route-ready time, not only WAN bytes.

**Exit:** A repeatable discovery load with known participant/endpoint/type counts and verified
export selection. No claim of exact full-local-catalog fidelity without original structure.

## 7. Phase 4: operational publication and routing profiles

### A. Direct fourteen-type mission

- Three distinct primary status streams at 1 Hz per platform.
- Contact parent approximately 0.412 updates/s and contact elements approximately 0.779
  updates/s aggregate per platform as a proposed transfer of the recorder pattern.
- Start with 10 active contacts; sweep 1/10/50, with deterministic keyed churn. The recorded
  373 handles are cumulative, not simultaneous contacts.
- Sparse plan/task/waypoint events: use a deterministic scenario schedule, with measured
  counts available for a 161-minute endurance pattern. Short runs include named bursts rather
  than fabricating smoothly periodic mission changes.
- Addressed reliable commands and ownership changes, with explicit report/ack expectations.
  Test the large 1.5-4.4 KB cases separately, not continuously at 1 Hz.
- Default peer-contact export off; enable isolated team-scoped variants at proposed 0.1/1 Hz.
  Do not equate those streams with the unnamed March peer traffic.

### B. Aggregated operational export

- A separate pair of status/command wrapper topics and types; do not simultaneously export
  the same logical data on the direct profile.
- Target captured status serialization **96-276 bytes**, typical median **156-160 bytes**;
  command serialization **140-352 bytes**, typical median **176-180 bytes** for the selected
  November/December cases.
- Derive wrapper frequency from the logical schedule and batching rules. An aggregate topic
  is not necessarily a single update or packet per second.
- Add wire-status **72-604 bytes** and navigation **356 bytes** only as named variants.
- Use approved wrapper IDL where available; otherwise declare bounds and the limits of the
  surrogate. Small observed samples do not imply a small bounded TypeObject.

For both profiles, add explicit configuration selection to the existing lifecycle and simulator
entry points. Customer profiles must not also run the normal simulator's background publishers.
Reuse audit/reporting components, but avoid putting ACT audit strings into exact customer
payloads: use existing schema identity fields where possible and a sidecar original-message
ledger otherwise. If extra audit fields are unavoidable, report their byte/discovery cost.

**Exit:** Topic/type, size, schedule, recipient, and lifecycle fidelity demonstrated separately.
Peer navigation and router overhead remain visible; normal ACT/UI behavior remains unchanged.

## 8. Phase 5: discovery and scale experiments

Run a staged matrix rather than the full Cartesian product:

1. Two-platform nominal direct profile, then aggregate profile with the same local load.
2. Simultaneous versus staggered cold start; one late join; gateway restart.
3. Same-process WAN reconnect versus restarted participant with a new GUID.
4. Current type-discovery path versus validated preloaded-type/suppressed-propagation variant.
   Confirm route creation still works before treating this as an optimization.
5. Current extra-navigation-participant topology versus an opt-in customer-shaped topology
   without that export. Count actual participants in each; do not impose the three-participant
   gate on the unchanged ACT topology.
6. Scale **1/2/7/8 platforms**, starting with fixed per-platform workload and local discovery.
   Record per-node cost and total source egress; recipient fan-out may grow faster than linearly.
7. Only after nominal calibration, selected **50/100/250/500/1,000 kbps** CommEffect rate trials,
   **100/200/1,500 ms** delay and **0/1/5/10%** loss trials. Specify direction and shared versus
   per-link capacity. These are proposed points, not measured radio distributions.
8. Normal-MTU and **1268-byte IP-MTU** variants, verified using captured IP/DDS fragments.

Historical leakage/alternate-locator behavior and the seven-platform simulator profile are
separate, later experiments. Match the latter's 212-byte geospatial and 356-byte navigation
samples only when recreating that pathology; the 21.22 Mbps damaged-prefix result is not a
normal operational acceptance target.

## 9. Measurement and acceptance gates

Capture bounded full-snaplen PCAPs on selected LANs and every node's `emane0` WAN path.
Use passive capture on WAN; retain startup and event timelines. Verify both RTPS/RTPX
handling, SPDP/SPDP2, SEDP, type exchanges, IP fragmentation and DATA_FRAG with known fixtures.
Do not assume current tools support every vendor encoding.

Extend existing tools only for missing capabilities:

- Per-topic/type sample min/median/P95/max, first sends versus repairs/fan-out.
- Timestamped participant and endpoint inventories with type-definition evidence.
- Frame-level discovery/data/reliability/mixed/unknown ledger, plus submessage/sample totals.
  Keep mixed frames explicit; never assign their entire bytes to multiple exclusive buckets.
- Source WAN TX versus destination WAN RX, separately from DDS/application consumption.
- Offered/delivered sample bytes, CPU/memory, convergence/rejoin times and backlog.
- Decimal rate percentiles over complete UTC-aligned one-second bins, including empty bins;
  maintain the historical report's byte and window conventions.

Source-egress sums measure submitted mesh traffic. Adding destination ingress measures another
boundary and double-counts transmission cost. Neither is RF airtime; report EMANE telemetry
as a separate layer.

| Gate | Proposed requirement |
|---|---|
| Accounting correctness | Known-fixture packet/frame-byte totals reconcile exactly; capture drops/truncation/incomplete reassembly exposed; malformed evidence fails or is inconclusive |
| Type fidelity | 14 distinct export types; every per-type size case checked; schema-complexity evidence and surrogate deviations reported |
| Size calibration | Exact schema/encoding cases match expected serialization; surrogate measured min/median/P95 within proposed +/-10% where comparable customer targets exist |
| Rate fidelity | Three primary streams 1 Hz within +/-5% over nominal steady windows; declared deterministic sparse/contact schedule reproduced without per-contact rate multiplication |
| Local load fidelity | Configured and observed population counts reconcile; unchanged local population across A/B tests |
| Isolation | No local/admin/simulator-domain WAN leakage in clean profiles; expected WAN participant count is topology-specific |
| Delivery | All explicitly expected reliable commands delivered once at the application under nominal conditions; duplicates and unexpected deliveries exposed; latest-state gaps and grace windows separately reported |
| Semantic confidence | Proposed <=1% unresolved user-frame bytes in a clean nominal full-startup capture; otherwise topic attribution is partial even if total bytes reconcile |
| Historical March match | Only for the matching historical topology/channel profile: 376.04 kbps mean +/-15%, 426.38 kbps P95 +/-25%, with directional explanations |
| Clean-profile bandwidth | Report measured load and overhead; do not force 376 kbps or treat the 198.64 kbps cross-IP subset as an exact deduplicated RF budget |

The November approximately 41 kbps envelope is an alternative reduced-channel historical
scenario, not an interchangeable target for the fourteen-type mission.

## 10. Implementation sequence, safety, and outputs

Suggested implementation batches, each with its own targeted tests:

1. Inventory/serialization and capture-fixture report tooling; ACT as-is baseline.
2. Fourteen-type fixture and opt-in deterministic workload; generated XML and routing tests.
3. Local discovery population; catalog-versus-endpoint and isolation tests.
4. Aggregate variant and sidecar correlation; delivery expectations for keys/teams/events.
5. Owned lifecycle capture-before-start and report integration; discovery/scale A/B runs.
6. Selected impairment and historical-stress scenarios after the earlier gates pass.

Do not change or restart the running mesh during diagnosis. Coordinate a test window and use
[run_mesh.sh](../harness_v2/scripts/run_mesh.sh) `up --emane` / `down --emane`, with the
documented license mount. The container runner currently constrains its lifecycle workdir
to the repository debug tree; do not pretend it accepts an arbitrary `/tmp` workdir.
Use `/tmp` for disposable probes, parsing databases and lock-sensitive state.

Before each DDS run and after teardown, check owned processes and RTI/DDS shared-memory
artifacts. Never run concurrent e2e tests against the live mesh; use reserved test domains
and keep every domain <=232. Clear raw artifacts only after their owning processes stop.

Author all fixed QoS in external XML, preserve defaults for normal ACT, and validate changed
profiles with the installed runtime/schema tooling. Check Connext-specific proposed APIs
against available guidance and known-issue records, then verify by build/capture rather than
relying on documentation alone. Rebuild router binaries and regenerate XML when required.

Retained outputs:

- This plan and versioned workload configuration/schema definitions.
- A baseline-versus-customer fidelity table covering every topic/type and observed size.
- Compact per-test JSON/HTML summaries under `debug/test_reports/<test_id>.*`, including
  configuration hashes, phase metrics, measurement confidence and unresolved gaps.
- Current-run node captures/logs under owned debug roots; selected evidence exports only
  when explicitly retained before the next lifecycle cleanup.

**First milestone:** a trustworthy ACT inventory and a nominal two-platform fourteen-type
profile, with real keyed contact instances, measured serialization and TypeObject evidence,
representative local discovery, and separate navigation/router overhead accounting.
Exact customer schemas, wrapper batching rules and peer-topic identification remain evidence
dependencies, not reasons to stop the measurable baseline work.
