# Router processor completion and acceptance plan

Date: 2026-11-16. Acceptance updated: 2026-11-17.

Status: **core processor completed, deployed and accepted on 2026-11-17.
All five live mission workflows passed on the final qualified build.**
Generic setup/export tooling and new numerical load/recovery guarantees remain
separate; the baseline-reproduced BEST_EFFORT disposal limitation is recorded below.

### Confirmed implementation decisions (2026-11-16)

- Reuse native shared WAN ownership if it meets the contract. If it cannot,
  use the minimal plugin-owned shared WAN manager proven by the spike; no
  additional architecture approval is required for that fallback.
- Complete the existing regression and five-mission acceptance gates first.
  Agree new numeric load/recovery limits separately. Preserve existing
  scheduling tests, safety checks and resource limits, but do not claim new
  performance/recovery guarantees or full load qualification from this release.
- Authorization to replace the retained live mesh was obtained separately on
  2026-11-17, including the five live mission tests.

## 1. Outcome

Finish the Routing Service processor using existing Connext features first.
Run the existing retained-scope tests, including all five real browser mission
workflows, against the final implementation. Preserve the existing GUI/Python
monitoring interfaces rather than introduce a new statistics format.

The deliverable is a reproducible XML deployment bundle, a qualified processor,
owned start/stop integration, rollback assets and current test evidence.
Passing isolated spikes is not completion.

## 2. Starting baseline (2026-11-16)

The starting retained mesh ran the fixed-ACT, XML-only processor on Control_20
and Platform_30-35, Event/State/Periodic, dashboard port 8080, owned workdir
`debug/mesh_runs/emane`. The five live mission tests previously passed on
[checkpoint 3.52](two-topic-wan-migration-plan.md#352-platform-mission-cfts-deployed-and-qualified-2026-11-16).
The [3.53 review fixes](two-topic-wan-migration-plan.md#353-processor-review-fixes-qualified-not-deployed-2026-11-16)
were qualified source changes, not that retained deployment. Inspect actual
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

**Initial ownership decision (superseded by the native-hub integration below):**
use the approved minimal plugin-owned shared WAN manager
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

The manager was therefore the bounded missing ownership surface selected for Phase 2,
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

### Implementation record — independent native bindings (2026-11-16)

**Source implemented, not deployed.** Production integration found a native
ownership arrangement that meets the endpoint/startup contract: one always-on
native WAN hub route plus independent one-port LAN routes in the same session.
Processor instances share node state and native port handles; Routing Service
still owns every DDS entity. This supersedes the earlier fallback decision.
There is no extra WAN participant, intermediate DDS bus or plugin pending queue.

The hub owns the exact family reader/writer pairs and monitoring ports, using
explicit XML QoS, `IMMEDIATE` creation and disabled auto-QoS. LAN routes default
to `ON_DOMAIN_MATCH` and native auto-QoS. An absent producer/consumer leaves
its binding pending without blocking unrelated traffic or the hub.
Common XML properties are repeated and checked across processors, with
`act.runtime_mode=independent` and distinct hub/tx/rx role registrations.
Legacy combined-route XML remains supported, including its original startup
barrier; it is still the retained deployment.

Per-topic native-history selection leaves application events in DDS until their
LAN output exists. Three same-instance command events survive a late consumer
while unrelated Periodic traffic continues. Native resource limits still apply;
admission rejection and write errors/timeouts are diagnosed, without plugin
retry/replay or an end-to-end delivery guarantee. Detach/shutdown uses bounded
shared registrations rather than adding a separate forwarding engine.

The dashboard XML adapter now selects the hub, validates common properties,
route registrations and session ownership, and aggregates LAN ports across
bindings. It retains combined-route support. LAN QoS tables explicitly display
configured values and mark native auto-QoS; they do not present those values as
live inferred policies. The standalone characterization runner now selects its
application port by name rather than accidentally editing the first WAN port.

Qualification exposed two issues before completion:

- Native content queries also returned unrelated invalid-data lifecycle samples.
  The shared path now recovers and verifies the instance key before consuming
  that instance, so a health query cannot steal an application's lifecycle
  notification or the reverse. The existing health STALE/recovery checks remain.
- Native ownership negatives assumed all LAN endpoints existed at startup.
  They now deliberately activate the malformed binding, bypassing Python
  preflight as before, and require native rejection with no forwarding. Starting
  the independent hub is no longer incorrectly treated as activating every
  application binding.

Fresh full-source validation is recorded in
`debug/test_reports/processor_independent_*.txt` and matching native JUnit files.
The targeted lifecycle repair also has compact evidence under
`debug/test_reports/processor_lifecycle_followup/`.

- **10/10 C++ targets passed.**
- **67 configuration/launcher/schema/dashboard unittest cases passed; one browser
  case skipped because Playwright is unavailable.** Native XML/QoS-provider
  checks and ACT/custom full-type round trips ran, rather than skipping the SDK.
- **63/63 native pytest cases passed, zero skips:** seven new independent
  activation/auto-QoS cases and all 56 existing two-topic, three-topic and
  monitoring/mission-policy cases. Modules run separately with fresh runtime
  directories to avoid domain-allocation exhaustion and fixture-directory reuse.
  All builds, processes, logs and DDS traffic used private-network/private-IPC
  containers; retained build outputs and live configurations were not replaced.
- The standalone characterization runner also passed **2/2 false/true comparisons**
  with a late reliable producer and an absent second input.

Read-only post-checks matched both retained binary hashes and confirmed all six
platform peers `PRESENCE_ALIVE` / `ROUTER_OK`, with an available controller and
empty impairments. Compact evidence is in
`debug/test_reports/processor_independent_postcheck.json`. Disposable build and
runtime state was removed after qualification; no live deployment was performed.

**Still open:** new-type monitoring, the remaining full regression/browser/fleet
acceptance, quantitative load/recovery budgets and coordinated deployment/
rollback qualification. The five unchanged live mission workflows have not been
rerun against this source update. The baseline offline fixture/browser blockers
remain separate; prior live mission results qualify the retained binary only.

### Implementation record — independent-hub review fixes (2026-11-17)

**All three review findings resolved in source, not deployed.**

- Status/Periodic observation now uses the same codec and transport-family
  validation as forwarding before notifying presence. Unsupported schema
  versions cannot revive a stale peer, whether the LAN output is pending or
  active.
- Observation reads at most 32 unread samples, retaining native DDS history.
  Monitored Status consumption selects only READ samples, so a per-topic query
  cannot consume an unobserved instance or race ahead with a newly arrived sample.
  Both the query's instance lookup and its take apply this state selection.
  Invalid-data lifecycle notifications remain eligible for health-loss handling;
  valid health/RTT callbacks are not repeated on forwarding. Native consumption
  also has a 32-sample total per original topic per pass, rather than a separate
  allowance for every instance.
- The standalone auto-QoS qualifier defaults to `ON_DOMAIN_MATCH` and rejects
  unsupported `IMMEDIATE` at argument parsing, before creating DDS entities.
  Historical combined-route characterization remains identified as such.

New native regressions first reproduced invalid-schema presence revival in both
WAN contracts, missed presence updates in a 40-instance burst in both contracts,
and failure of the qualifier's default invocation. After the fixes:

- **10/10 C++ targets passed** after a fresh private build.
- **67 configuration/launcher/schema/dashboard unittest cases passed; one
  browser case skipped because Playwright is unavailable.**
- **70/70 native pytest cases passed, zero skips:** eight independent auto-QoS/
  qualifier cases, eighteen two-topic cases, eighteen three-topic cases and
  twenty-six monitoring/mission-policy cases. New coverage checks invalid Status
  with pending and active LAN outputs, every peer's recovery and exact source/
  sequence delivery in a 40-instance burst, supported CLI defaults and early
  rejection of `IMMEDIATE`. Existing health lifecycle/countdown and RTT tests
  also passed.

Compact red/green logs and JUnit reports are retained locally under
`debug/test_reports/processor_presence_review_fixes_20261117/`. All tests used
the current checkout read-only, private-network/private-IPC containers and
disposable builds/runtime state under `/tmp`. Retained processor/config-helper
hashes are unchanged; read-only checks confirmed all six live peers
`PRESENCE_ALIVE` / `ROUTER_OK` and an available controller with empty impairments.
No live deployment or mission rerun was performed. The wider release gates above
remain open.

### Completion acceptance record (2026-11-17)

The user authorized completion of the existing checks, coordinated deployment
and all five live mission tests. The owned mesh now deploys the native shared
WAN hub and independent discovery-triggered LAN routes with native auto-QoS,
including the review fixes above and checkpoint 3.53. Routing Service owns all
entities; there is no intermediate bus, extra WAN participant or plugin backlog.
Prepared full-type XML is sufficient; generic capture/export and the setup UI
remain separate.

Release qualification used the supported Connext 7.7.0 image with the current
checkout, fresh builds, private network/IPC and temporary state under `/tmp`.
Final retained-scope results:

| Gate | Result |
|---|---|
| Fresh C++ build/regressions | 10/10 targets passed; private and persistent binaries match |
| Router nonlive retained scope | 386 top-level cases and 284 subtests passed; no final failures, errors or skips |
| Native processor subset | 74 passed: 18 two-topic, 8 auto-QoS, 30 monitoring/policy/mesh, 18 three-topic |
| Configuration and native time filters | 56 unittest cases executed successfully, including all three opt-in time-filter cases |
| Dashboard | 30 cases plus 24 subtests passed, including real Chromium |
| WAN union qualification | 29 passed |
| Selected workload regressions | 38 cases plus 37 subtests passed |
| Whole-structure adapter spike | 9 PASS groups and 10 expected rejections |
| Final live mission gate | 5/5 passed, zero skips, 380.53 s on the final deployed build |

Groups overlap; do not sum them into a unique total. The router figure combines
the complete per-module ladder with final affected native reruns by test ID,
not one fresh monolithic full-suite invocation. Final native runs split the mesh
module across processes to respect the domain allocator's 52 triples and the
domain <=232 invariant. An earlier unsplit run exhausted that allocator at the
last case; its case passed on rerun. A mistyped auto-QoS module path produced no
tests and was corrected before the final 74-case gate.

Two stale fixtures were repaired without weakening assertions: the mission
backend fixture now initializes receive-policy audit state, and the browser
fixture expects the actual mesh-graph cache revision.

Deployment preflight and live log auditing exposed three production-path issues,
each reproduced red/green against real native DDS before correction:

1. Expected BEST_EFFORT `lost_by_writer` gaps must not permanently mark routes
   failed. They now produce rate-limited warnings.
2. The monitor's own diagnostics did not activate an application-style LAN
   input. Monitored bundles now use the existing internal diagnostics producer,
   retaining LAN publication and encoding directly into the existing shared
   WAN output with configured audiences, enable flags and priority. There is
   no extra endpoint or queue, and input matches remain truthfully zero.
3. An initial live run passed all five missions (376.49 s), but the final log
   audit found C2 marking 20 State replacements as a permanent history error
   during blackout recovery. State writer KEEP_LAST(1) intentionally supersedes
   older values. Only `lost_by_writer` with no new rejection is now a warning
   for that family; reliable Event history loss, rejections and other loss
   reasons remain errors. The native regression induces actual packet loss,
   verifies final ContactReport sequence 30 and unchanged published C2 health.

The installed Modern C++ extension uses `status->last_reason()`, verified by
headers and compilation. With explicit user approval, that correction and the
previous unpublished correction entries were published in the shared correction
log (`dd90b8b`); parent commit `381a57d` pins it. No parent implementation branch
was pushed.

**Deployment and rollback:** the complete matching old bundle was launched from
an isolated old checkout through its own lifecycle, verified with six healthy
peers and matching old hashes, then stopped. Current binaries were rebuilt only
with the retained mesh stopped. The final owned coordinated launch is
`container-20261117T181119-2947503`, Compose project
`act-emane-container-baseline`, Control_20 plus Platform_30–35, dashboard 8080,
workdir `debug/mesh_runs/emane`. All seven deployed hashes match the qualified
build:

- Processor SHA-256:
  `022941caaef2604dd41657da494b9144f17e5bbb7fd1a6c7b816e4dfd007f94b`.
- Config helper SHA-256:
  `406e734e0f713443fd4dd2a16bcbd6e4665d9eae583660211abb9fd1820ab6bf`.

Native inspection checks exactly Event/State/Periodic reader/writer pairs per
gateway and published health from all seven routers, including C2, rather than
only the six platform summaries. Existing link-stat rows retain their shape,
real protocol counters and RTT fields; no per-branch physical counters are
invented. The observer must open builtin endpoint readers before loading its
application reader, and run from `/workspace` to avoid stale image-local types.
Parity checks select the six expected platform peers; the raw snapshot also
retains a diagnostic observer row from its earlier router-role invocation.
That extra row is explicitly identified in the parity report, not hidden or
treated as a platform. The observer now uses a non-router role.

Final postflight: six platform peers ALIVE/ROUTER_OK; all seven routers publish
ROUTER_OK; missions CANCELLED with receive profile Init, teams empty, clocks 1x,
zero speeds, targets hidden and impairments empty. Actual Chromium rendering
verified a populated seven-node graph and XML configuration view with current
assets and no page/console errors. All seven router logs contain zero structured
or native ERROR lines; rate-limited BEST_EFFORT warnings and the expected C2
latest-State replacement warning remain visible. This is not a warning-free log
claim. The verified mesh is left running. Private qualification containers,
builds, caches and the stopped rollback checkout were cleaned up; the rollback
archive and compact evidence remain.

**Known separate limitation:** an additional legacy routed-churn spike passes
three cases but fails its BEST_EFFORT Status outage disposal check, retaining
41 ghosts. The same case against the archived old `router_main` reproduces the
same 41 ghosts. This is not a new processor regression or a passing disposal
guarantee; the assertion remains unchanged. Quantitative sustained load/recovery
budgets, generic monitoring/export/setup, persistence, exactly-once delivery,
reliable BEST_EFFORT disposals and atomic fleet-wide filter barriers are not
qualified by this release.

Compact local evidence is under
`debug/test_reports/processor_release_20261117/`: consolidated
`final_router_nonlive.xml`, final native reports, `acceptance_counts.json`,
red/green logs, live JUnit/screenshots, native inventory, all-node hashes,
link-stat/browser checks and exercised rollback records. Failed attempts remain
identified as such. The matching old rollback archive remains under
`debug/test_reports/processor_completion_baseline_20261116/rollback.tar.gz`.

### Final-review follow-up — diagnostics failure isolation (2026-11-17)

**Qualified in source; not deployed.** A diagnostics encoding/WAN-write failure
is now recorded in the existing route error/timeout counters and explicitly
logged as `rs.diagnostics.error`, without escaping the diagnostics publisher.
The monitoring tick can therefore advance its heartbeat timer and publish link
statistics even while diagnostics continue failing. Errors are not hidden and
the existing timeout versus non-timeout route-state distinction is preserved.

A native regression caps the Periodic/Status writer at one instance: health
occupies it and diagnostics fail persistently. Both WAN contracts reproduced
the old heartbeat flood (roughly 300 sequence increments in 3.2 seconds).
After the fix, **10/10 C++ targets and 4/4 focused native cases passed**, zero
skips. The two failure cases verify bounded heartbeat cadence, continued fresh
link-stat captures, visible route errors and explicit error logs; normal internal
diagnostics forwarding and health/protocol/team updates also pass.
Compact red/green evidence is under
`debug/test_reports/processor_diagnostics_isolation_20261117/`.

Only a private build was changed. The retained seven-node mesh and persistent
processor hash `022941ca…` are unchanged; the qualified private processor hash
is `5293552934ef180a9cf4d0f0dea93a06954697cd2453cb56ea0a0f77c7ca748e`.
No new live mission gate or deployment is claimed. Use the owned coordinated
teardown/build/prepare/relaunch lifecycle before deploying this follow-up.

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
