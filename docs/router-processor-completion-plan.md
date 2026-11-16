# Router processor completion and acceptance plan

Date: 2026-11-16.

Status: **implementation started with user authorization on 2026-11-16.
Live replacement remains separately gated; the retained mesh is unchanged.**

### Confirmed implementation decisions (2026-11-16)

- Reuse native shared WAN ownership if it meets the contract. If it cannot,
  use the minimal plugin-owned shared WAN manager proven by the spike; no
  additional architecture approval is required for that fallback.
- Complete the existing regression and five-mission acceptance gates first.
  Agree new numeric load/recovery limits separately. Preserve existing
  scheduling tests, safety checks and resource limits, but do not claim new
  performance/recovery guarantees or full load qualification from this release.
- Authorization to replace the retained live mesh remains a separate decision.

## 1. Outcome

Finish the Routing Service processor using existing Connext features first.
Run the existing retained-scope tests, including all five real browser mission
workflows, against the final implementation. Preserve the existing GUI/Python
monitoring interfaces rather than introduce a new statistics format.

The deliverable is a reproducible XML deployment bundle, a qualified processor,
owned start/stop integration, rollback assets and current test evidence.
Passing isolated spikes is not completion.

## 2. Current baseline and what changes

The retained mesh already runs the fixed-ACT, XML-only processor on Control_20
and Platform_30-35, Event/State/Periodic, dashboard port 8080, owned workdir
`debug/mesh_runs/emane`. The five live mission tests previously passed on
[checkpoint 3.52](two-topic-wan-migration-plan.md#352-platform-mission-cfts-deployed-and-qualified-2026-11-16).
The [3.53 review fixes](two-topic-wan-migration-plan.md#353-processor-review-fixes-qualified-not-deployed-2026-11-16)
are qualified source changes, not the retained deployment. Inspect actual
worktree, build hashes and lifecycle ownership before resuming; these are dated
records, not proof of present runtime state.

Already implemented: ACT union forwarding, channel priorities, shared WAN
endpoints, mission/team filtering, health/presence, RTT, status, mesh summaries,
link-stat collection, diagnostics and applied receive-policy history.
Do not rebuild these systems from scratch.

The [union + native auto-QoS spike](../spikes/union_auto_qos/README.md)
proves a different arrangement: independent native LAN routes, shared
plugin-owned WAN endpoints, schema preparation and whole-payload forwarding.
It does not include production monitoring/recovery parity. Its priority checks
verify configuration and assigned priorities, not congestion guarantees; its
typed filtering checks are not a native writer push-count gate.

## 3. Contract to preserve

- Typed unions carry the **whole original payload**. Both ends preload the
  same frozen type/union XML; no generated application classes are required.
- Exactly three physical WAN topics, **Event, State, Periodic**, and one shared
  writer/reader pair per family per gateway. More WAN endpoints are not an
  approved shortcut; an intentional change needs a separate decision.
- Independent one-way application bindings: a missing producer/consumer leaves
  only its binding pending. Available topics must forward; health and control
  must not wait for every application.
- Native LAN auto-QoS with discovery-triggered creation. Retain explicit
  policies for unsupported requirements and local resource budgets. No automatic
  inheritance of application policies onto WAN endpoints.
- XML-authored family policies, shared asynchronous flow controller, nominal
  2 Mbps starting budget, two High/Normal bands per family, six channel priorities
  600/500/400/300/200/100 as specified in the
  [channel mapping](routing-service-xml-mapping-plan.md#user-facing-channels).
- Preserve Mission/Team/PlatformStatus naming, addressed/team/global recipients,
  authoritative source restoration, feedback suppression and supported identity
  bounds. Do not redesign command payloads.
- Local mission state controls Init/ActiveMission receive interests; whole-platform
  restart starts Init. Outgoing reports stay always on. Optional Periodic LAN input
  filtering remains per-topic, off by default; no Event/State input throttling.
- Team commands update local membership, subsequent outgoing metadata and receive
  filters consistently. Report applied state/revision/errors, not just requested
  state. Preserve ACKs and rollback of rejected changes.
- Keep `ActRouterLinkStats` and existing status/mesh/health/diagnostic contracts.
  Native protocol metrics belong to physical endpoints; never fabricate per-branch
  retransmissions, delivery or remote readiness.
- Journals, retired route-management/report-mode commands, persistence and a
  general command redesign remain outside the retained scope.

## 4. Implementation phases

### Implementation record — Phase 0 (2026-11-16)

The starting source was clean at `b6d4c8d`. Read-only inspection confirmed seven
running containers in Compose project `act-emane-container-baseline`, the
checkpoint 3.52 XML-only deployment and dashboard port 8080. All six platform
peers reported `PRESENCE_ALIVE` / `ROUTER_OK`; the operator controller was
available with no impairments. This is a startup snapshot, not a new mission gate.

A local ignored rollback bundle is retained under
`debug/test_reports/processor_completion_baseline_20261116/`. Its manifest records
container image identities, lifecycle command/mounts, source commit, health and
SHA-256 hashes. The archive contains 30 matching deployment files: generated
XML/types/YAML and Compose/run identity, processor/config helper, common type/QoS
XML and lifecycle/renderer scripts. Every archived member was rehashed without
extracting over the checkout. The SDK/image and license remain external
prerequisites; no license or container environment values are archived.

- Image: `sha256:cbabafb1dab80c66483f092e1770249a6196d974b01592fe3a52979613f5f7a0`.
- On-disk plugin SHA-256:
  `75afb441e0d9b770bcfd76fb0ef8dc7d50bcf908e5d46f87bf2aec11c1de5233`.
- Archive SHA-256:
  `bfdd98bf5d742ef1ae2db7319f88d5b0e7cc33dffd2fcba96c7392e1b17bf3da`.

**Ownership decision:** use the approved minimal plugin-owned shared WAN manager
for independent LAN routes. The bounded native investigation does not establish
that native sharing is impossible; it establishes that no qualifying native
cross-route shared endpoint mechanism has been found on installed 7.7.0.0.
The installed Routing Service XSD defines ports within a route, and describes
AutoRoute as creating routes for discovered streams, not sharing their endpoints.
Existing isolated characterization retained in `rs_autoqos_summary.json` covers
52 scenarios: with an absent input, another producer matched but forwarding and
processor startup remained blocked, with auto-QoS both off and on. Conversely,
the [union/auto-QoS spike](../spikes/union_auto_qos/README.md) qualified six cases
with independent LAN routes, one pair per WAN family, and unchanged WAN
endpoint identities after activating the missing input. These are existing
measured results, not fresh production-integration results.

The manager is therefore the bounded missing ownership surface in Phase 2,
not permission to add a discovery engine, scheduler or intermediate DDS bus.
Native LAN discovery/auto-QoS and XML-authored WAN policies remain the design.
Production auto-QoS/independent-startup qualification is still open.

**Required test inventory:** actual collection in a private-network,
private-IPC `connext:7.7.0` container found **373 router tests** at this starting
checkpoint. Pytest 9.2.0 and pytest-subtests were supplied offline after the
image reported missing pytest; Python Connext and CMake 3.22.1 were checked in
that same environment. Collection did not run any tests. Retained listing:
`debug/test_reports/processor_completion_collection.txt`.

| Collected baseline group | Items | Acceptance mapping |
|---|---:|---|
| Native processor, three-topic processor and mesh/policy modules | 56 | Required against freshly built final processor; set `ACT_RS_BUILD_DIR`, otherwise the fixtures skip |
| Live mission workflows | 5 | Required unchanged, zero skips after authorized deployment; dashboard/backend/contract variables and working Chromium required |
| Mission contract/map/navigation, EMANE config, traffic accounting/dashboard and wire-delivery modules | 220 | Shared application/UI/config regressions; browser prerequisites must be installed for browser checks |
| Remaining controller, adapter, helper and discovery modules | 92 | Keep legacy-specific route-admin/journal/partition contracts on their original backend; retained forwarding/team/health/lifecycle behavior must also pass the processor modules |

This is a collection map, not a claim that the 56 processor items replace every
legacy assertion. Phase 5 must check equivalent retained behavior and report
execution/skips explicitly; new binding tests will increase these counts.
Rollback restoration itself has not been exercised: it requires the owned
coordinated stop/restore/relaunch and separate deployment authorization.

The shared 220-item offline group was also executed in that isolated environment:
**197 passed, 2 failed, 21 skipped, 272 passing subtests** (4.27 s). Both failures
are existing mission-contract fixture initialization gaps: the fixture bypasses
`DdsBridge.__init__` and lacks `audit_policy_history` required by the committed
receive-policy audit consumer. The new binding implementation does not modify
that fixture or dashboard code. All 21 skips report missing Playwright. These
are explicit remaining acceptance blockers, not a green offline gate.
Evidence: `debug/test_reports/processor_completion_offline.{txt,xml}`.

### Implementation record — Phase 1 first slice (2026-11-16)

**Qualified source, not deployed; Phase 1 is not complete.** The production
codec now accepts a frozen full-type binding registry. Grouped XML properties
carry custom bindings through the model, configuration helper, receive filters,
metadata/channel selection and processor. Whole structures are copied; original
branch/type equality, string identity keys, envelope fields, selected union
labels/member IDs and duplicates are validated before forwarding. Built-in ACT
definitions and ContactReport's authoritative source/composite-key exception
remain protected. Production IDL, generated XML and fixed QoS are unchanged.

The prepared-schema fixture adds a non-ACT command and nested status structure.
Custom bindings currently require **static receive policy and monitoring off**;
unsupported addressed extraction, multi-key custom payloads and unsupported
union-label shapes reject explicitly. Custom audit fields are not guessed.
See the [processor guide](../router/routing_service/README.md) for the property
contract and the [native acceptance test](../router/routing_service/test_full_type_bindings.py).

Fresh builds in private-network/private-IPC `connext:7.7.0` containers passed:

- Legacy `router_main`, processor, configuration helper and selected test targets.
- **3/3 C++ tests**: existing ACT codec and interests plus the new binding test.
- **34/34 Python unittest cases, zero skips**: 30 existing configuration cases,
  prepared-bundle validation, malformed-schema rejection subcases and real
  native processor round trips for custom command/nested status and existing
  ACT command/power status. The ACT command contains an 8192-byte payload.

Commands and durations are retained in
`debug/test_reports/processor_phase1_schema_slice.txt`. Private builds/runtime
state were removed; existing build outputs and the seven live containers were
not changed. No commits or pushes were made.

**Review fixes qualified (2026-11-16), not deployed:** explicit binding IDs now
survive configuration/bundle round trips independently of literal topic names,
including dots. Envelope string/sequence capacities and payload-key capacity
are validated before forwarding against the existing 255-character names,
512-character instance ID and sixteen-name audience contract. Regression tests
first failed on the reviewed code, then passed after the fixes.

A fresh full private-network/private-IPC build passed **10/10 C++ tests** and
**49/49 Python unittest cases, zero skips** (0.70 s and 27.263 s). Python coverage
includes configuration, launcher and real native processor ACT/custom/dotted
topic round trips; schema checks cover thirteen insufficient-capacity cases,
and C++ round trips exercise exact-boundary keys, identities, topics and all
sixteen audience slots. Logs are retained as
`debug/test_reports/processor_review_fixes_{cpp,python}.txt`; red-run logs use
the `processor_review_fixes_red_` prefix. Existing build outputs, wire schemas
and live containers remain unchanged; full fleet/mission acceptance is still
open.

**Next slice:** integrate the minimal shared WAN owner and independent native
LAN routes/auto-QoS while preserving the existing ACT monitoring and application
policy paths. The current renderer still uses the original all-in-one route;
this code does not yet solve missing-topic startup blocking. New-type monitoring,
independent activation, full native regression and unchanged mission acceptance
remain open. The baseline offline blockers above also remain open.

### Phase 0 — Freeze baseline and resolve native ownership

1. Capture active ownership, image/build/config identities, effective XML and a
   rollback bundle without changing the live mesh. Preserve unrelated work.
2. Inventory existing tests and map each to retained behavior, a deliberate
   retired behavior, or a separately scoped legacy backend. Record required
   environment variables and expected collection; a missing backend fixture is
   not an acceptable reason to skip a required gate.
3. Perform a bounded native-feature check: can Routing Service own the three WAN
   pairs while independently activating application ports, without an extra DDS
   intermediate bus or a plugin endpoint manager? AutoTopicRoute is a factory,
   not proof of shared endpoints or many-to-one conversion.
4. Inspect the installed version. Auto-QoS predates 7.7; support for
   `ON_DOMAIN_AND_ROUTE_MATCH` is assigned to 7.7.0.1, not the tested 7.7.0.0.
   Do not rely on an uninstalled patch or proposed policies.

**Gate:** record tested route creation, actual endpoint counts and missing-topic
behavior. Choose native ownership if proven. If it cannot meet the contract,
explicitly record the minimal plugin-owned shared owner as the required delta
before production integration. Do not silently adopt more WAN endpoints.
See [LP-10](product-gaps.md) and the
[auto-QoS feature reference](routing-service-qos-auto-configuration-routing-1309.md).

### Phase 1 — Prepared schema/bindings and LAN auto-QoS

1. Refactor current fixed-catalog assumptions into a validated full-type binding
   registry, reusing the existing codec and XML property conventions. Preserve
   built-in ACT behavior; add a non-ACT command and nested status acceptance case.
2. Bind union branch/type, original topic, one existing string instance-ID field,
   channel and explicit audience/identity sources. Unsupported keys or policies
   fail visibly; do not guess from field names.
3. Keep runtime configuration XML-only. Validate complete definitions, duplicate
   bindings, branch labels/member IDs, type equality and node identity before
   enabling forwarding. Contract assignability alone is not proof of lossless
   whole-payload restoration.
4. Enable native auto-QoS only on intended LAN ports with qualified creation
   modes. Test endpoints appearing before/after startup, differing endpoints,
   explicit-profile interaction and required finite deadlines. Keep WAN and
   monitoring policies explicit.

**Gate:** original LAN -> union -> original LAN equality for every ACT case and
the new types; malformed/unknown/conflicting types reject explicitly. Native
observations and false/true controls demonstrate actual QoS inference and
delivery, not merely accepted XML. Missing topics never block active ones.

### Phase 2 — Shared endpoint lifecycle and application policy

1. Integrate the Phase 0 ownership choice with the current processor/model.
   Reuse native entity lifecycle as far as the selected arrangement permits.
2. If a shared plugin owner is necessary, implement only the missing ownership
   surface: bounded registrations, generation guards, detach/shutdown barriers
   and idempotent cleanup. No custom retry queue, discovery engine or scheduler.
3. Share node membership and receive-policy state across active routes. Preserve
   transactional validation/rollback, local mission authority, team commands,
   internal RouterConfig, addressing and source suppression.
4. Bound behavior when a destination application is absent: qualify native history
   and late-join behavior per family, distinguish submission from delivery, and
   report overflow/timeouts explicitly. Do not silently lose required events
   or introduce an unbounded plugin backlog.

**Gate:** one WAN pair per family before/after late topic activation, route detach,
source restart and shutdown; no duplicates/feedback or stale callbacks. Existing
mission policy and team tests pass through the final data path.

### Phase 3 — Monitoring and GUI/Python parity

1. Reuse [GatewayMonitor](../router/routing_service/GatewayMonitor.cxx),
   [LinkStatsCollector](../router/src/core/LinkStatsCollector.cxx) and presence/RTT
   components. Adapt endpoint access to the chosen owner instead of replacing
   their publication contracts.
2. Preserve logical per-topic forwarding, lifecycle, error and write-timeout
   accounting. Collect native Event/State/Periodic protocol statistics and
   per-peer observations at the real shared readers/writers.
3. Inventory the baseline's actual `ActRouterLinkStats` rows, keys, units,
   direction, rate/delta/reset semantics, cadence and unavailable conventions.
   Map each to a truthful final source. Identify any missing required per-topic
   metric as an explicit gap, not an invented division of shared protocol totals.
4. Preserve health deadline/countdown/recovery, any-valid-Status semantics,
   session/restart tracking, passive RTT and protocol counters. Health must
   continue with no application ports active.
5. Consume the same publications with the existing bridge/GUI and a Python reader.
   Check team/filter applied revisions, physical family/priority fields, freshness,
   zero traffic and reset/restart behavior.

**Gate:** existing monitoring consumers work without a new format; native counter
changes agree with induced traffic/impairments, stale data is visible, and per-topic
and per-family facts are distinguishable. Status does not claim independent
branch-level WAN matches. No new WAN diagnostics topic or dedicated health pair.

### Phase 4 — Safety, lifecycle and congestion qualification

Exercise dispose/unregister/key-only recovery, multiple producers and aliases,
wrong-type input, unknown keys, producer/router/platform restart, rapid team and
mission changes, startup with partial applications, teardown during traffic,
outages and queued final-state recovery.

Reuse the six-priority backlog tests and add mixed-family saturation checks for
command/ACK and health responsiveness, latest-state behavior, memory/instance
limits and explicit write timeouts. Agree numeric load/admission, latency,
convergence and memory acceptance limits before running this gate. Do not claim
exactly-once execution, persistence or all-intermediate-state delivery.

**Gate:** existing regression guarantees and safety checks pass. New numerical
load/recovery qualification is a separate follow-up under the confirmed scope,
not a blocker to the regression/mission completion gate. Expected best-effort
loss and known recovery limitations are recorded, not relabeled as successes.

### Phase 5 — Existing suites and unchanged mission acceptance

Run the ladder below against freshly built final sources and the generated
deployment artifacts. Diagnose failures before expanding scope. Change tests only
to select the backend/owned fixture or an already approved behavior; preserve
their application-level assertions. Do not weaken assertions to make a run green.

**Gate:** all required retained-scope tests execute and pass, including five
live browser workflows with zero skips. Record exact counts, subtests, durations,
versions and configuration/build identifiers; never copy historic counts.

## 5. Test ladder and evidence

| Order | Existing coverage to run | Required evidence |
|---|---|---|
| 1 | Fresh processor/config-helper build; [C++ router tests](../router/run_tests.sh); configuration/launcher checks in [routing_service](../router/routing_service/) | Correct mounted build paths, nonzero test collection; XML loaded by real Connext/Routing Service |
| 2 | [Native service acceptance](../router/test_e2e/test_routing_service.py), [three-topic tests](../router/test_e2e/test_three_topic_routing_service.py), [mesh/monitor/policy tests](../router/test_e2e/test_routing_service_mesh.py) | All ACT cases, lifecycle, filtering, six priorities, stats, health, teams, Init/ActiveMission and restart |
| 3 | New union/auto-QoS/independent-startup tests plus relevant codec/interest qualification | Final production code, not just standalone spikes; native endpoint inventories, full equality and writer-side filter counters |
| 4 | Existing offline dashboard, mission, simulator, delivery-audit and configuration regressions | Existing GUI/API/data contracts and application assertions preserved |
| 5 | Full existing router suite, with explicit backend coverage mapping | No silent backend skips; legacy-only contracts remain tested on the legacy backend; retained processor behaviors have equivalent real coverage |
| 6 | Existing WAN/adapter qualification affected by schema, codec or endpoint changes; existing scheduling/recovery regressions | No regression in shared types/policies; new numerical load/recovery limits are a separately agreed follow-up |
| 7 | Owned EMANE deployment and [five live mission workflows](../router/test_e2e/test_mission_workflow.py) | Exactly five pass, zero skip, real Chromium, real DDS/EMANE, final prepared XML and plugin |

Use the existing test interfaces, for example inside an owned private-network
Connext container with a freshly built processor at `/tmp/rs-build`:

```bash
ACT_RS_BUILD_DIR=/tmp/rs-build/routing_service \
python3 -m pytest -q -p no:cacheprovider \
  router/test_e2e/test_routing_service.py \
  router/test_e2e/test_three_topic_routing_service.py \
  router/test_e2e/test_routing_service_mesh.py
```

Verify pytest, its required plugins, `rti.connextdds`, CMake and actual browser
launch in the environment used for the corresponding gate. Use the existing
test environment rather than install dependencies speculatively. Keep disposable
state in owned local temporary storage. Reserve suite domains and split runs
when necessary so every domain remains <=232. No DDS e2e run may join the live mesh.

## 6. Mission gate and deployment safety

Before any changed live deployment, obtain explicit authorization. Use a fresh
isolated one-platform reproduction for new startup/configuration failures; only
scale after understanding and cleaning it up. Preserve the retained mesh until
the coordinated owned replacement is approved.

Deploy using the existing `run_mesh.sh --emane` lifecycle and Routing Service
backend, not individual router restarts. Stop the owned mesh before rebuilding
or replacing its binaries, type XML or generated configuration. The license-only
mount uses `CONNEXT_SHARED_DIR=/home/dgreenberg/rti_connext_dds-7.7.0`.
Keep a complete matching old bundle and verified rollback procedure.

Start Control_20 plus six platforms with dashboard and final XML. Verify actual
plugin/config versions, exactly three WAN pairs per gateway, expected peers,
current stats, controller availability and empty impairments before tests.

```bash
MISSION_DASHBOARD_URL=http://127.0.0.1:8080 \
MISSION_ROUTER_BACKEND=routing_service \
MISSION_WAN_CONTRACT=three_topic_union \
python3 -m pytest -q -p no:cacheprovider \
  router/test_e2e/test_mission_workflow.py
```

Run from the existing supported browser-test environment with access to the owned
dashboard. The five unchanged workflows cover:

1. WAN blackout and Init/terminal recovery.
2. Automatic reporting and independent target controls.
3. Team drag/drop, validation, mission execution and cross-tab DDS state.
4. Multiple teams/boats/missions at demonstration speed.
5. Live team goal inheritance and reassignment.

Reset impairments explicitly before/after scenarios and verify the controller's
empty state. Retain JUnit, concise reports and useful browser screenshots under
`debug/test_reports`. Check expected ALIVE/health states, restored delivery,
cancelled missions, baseline teams/clocks/targets and zero speeds afterward.
Check logs for new errors; do not claim clean logs while known warnings remain.
Leave or stop the mesh according to the user's instruction, using its owner.

If a deployment gate fails, retain bounded evidence and restore the old matching
bundle through the same lifecycle. Never repair one router in place.

## 7. Scope of the Python setup/debug tool

The [Quickstart](quickstart.md) defines one Python workflow for schema capture,
channel selection, peers, QoS evidence and deployment generation. Reuse Connext
APIs; SPY is an optional diagnostic/reference, not a mandatory manual workflow.
Complete arbitrary-schema export must be qualified; the spike's bounded export
collector is not a general exporter.

Do not block core runtime parity on a terminal UI. A prepared, validated XML
bundle is sufficient for its engineering and mission gates. The setup tool
automates production of that same bundle, not a second runtime configuration.

A co-deployed Python debug reader can load the same type XML to dissect WAN
unions. Preloading alone does not suppress WAN type metadata: explicitly qualify
WAN type-advertisement/TypeLookup settings with bounded wire captures, while
retaining local discovery. Do not claim this bandwidth property until measured.

## 8. Definition of done

- Ownership decision is documented; available topics forward independently;
  three WAN endpoint pairs and native LAN auto-QoS are verified in final code.
- Existing application, mission/team, monitoring, link-stat, health and GUI/Python
  contracts pass through that implementation, including review fixes.
- Required existing suites and all five live missions pass with explicit
  collection/skip accounting and reproducible artifacts.
- Existing lifecycle/recovery regressions pass; residual unsupported behavior
  and deferred numerical load/recovery qualification are explicit. No fake
  per-topic protocol statistics or proxy-only acceptance.
- Deployment, mismatch diagnosis, teardown and rollback are exercised; current
  handoff/evidence distinguishes tested source from deployed binaries.
- Full general schema-export tooling and the setup UI have their own acceptance
  record; they are not falsely reported complete by passing router tests.
