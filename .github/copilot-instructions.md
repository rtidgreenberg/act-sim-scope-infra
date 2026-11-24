# Repo context & guardrails for AI assistants

This repo is developed on an AWS instance. The checkout is on the instance's normal
filesystem.

## Runtime artifact safety (READ FIRST before running anything)

`debug/logs`, `debug/pcap`, and per-node directories such as `debug/platform_30_debug` are
generated-artifact roots. Use the per-node directories for current container logs and
`debug/test_reports/<test_id>.*` for compact retained summaries; clear raw artifacts before
each run and keep source/config files separate from artifacts.

Use `/tmp/...` (or `mktemp -d`) for disposable working directories, FIFOs, SQLite/DWH files,
and other lock-sensitive transient state. Do not leave processes holding runtime files open
across forced termination; use the owned harness lifecycle for cleanup.

## Communication style

- Use plain language and concise, direct explanations.
- Avoid unnecessary jargon; explain technical terms and acronyms when introducing them.
- Preserve technical accuracy and use simple examples when they help.

## Slide format

- New or updated slide decks in this repository must be portrait.
- For Marp decks, use `size: portrait` with the shared RTI theme (960 x 1280).
- Verify PDF and PowerPoint orientation and review rendered slides for clipped
  text or overlap with logos and footers. Reflow content rather than shrink it
  until it becomes hard to read.

## Active work handoff: two-topic WAN refactor (2026-11-09)

**2026-11-24 source-only Periodic QoS naming:** the base/shared/RS profiles and
configuration alias now use `wan_periodic`, `wan_union_periodic` and
`rs_wan_periodic`. Best-effort Periodic readers omit preemptive-NACK suppression;
reliable Event/State readers retain it. Wire topics and payload types are
unchanged. Fresh build: 10 C++ targets, 57 targeted Python checks and five
real-DDS forwarding/filtering checks passed; one unchanged browser check skipped
for missing Playwright. Sixteen resolved WAN profile comparisons verify that
writer QoS is unchanged and only the requested Periodic reader property differs.
Rebuild development helpers and regenerate older bundles before using renamed
profiles. Persistent binaries, generated live configs and retained evidence are
untouched. See [the native XML guide](../router/routing_service/README.md).

**2026-11-24 source-only compression follow-up:** the current six-topic IPv6
baseline exposed a three-record RouterConfig/cache bound and a health callback
that did not dispatch P1_Periodic. The user authorized repairing both and
regenerating types before private off/LZ4/zlib qualification. Matched full
monitoring/async/shared 4 Mbps union comparisons pass; see
[the bounded results](../spikes/full_union_compression/README.md).
Consumers must be rebuilt together before deploying the changed bounds.
Persistent binaries and mesh deployment are untouched; binary/load/loss/radio
qualification remains separate. Native wire parsing now recognizes
DATA_FRAG_SESSION (0x81); experimental capture attribution includes native
priority-channel writers rather than silently omitting their bytes.

Read the [baseline record](../docs/two-topic-refactor-baseline.md) and
[eight-phase migration plan](../docs/two-topic-wan-migration-plan.md) before resuming.
These documents hold the detailed evidence, code surfaces, decisions, and test gates.
Update this dated handoff as implementation progresses; do not treat it as live state
without inspecting the current worktree, commits, and runtime ownership first.

### Current runtime checkpoint (2026-11-17)

This supersedes older retained-mesh descriptions below. The owned EMANE mesh now
runs the **completed XML-only Routing Service processor with independent native
LAN auto-QoS routes and an always-on shared WAN hub**, Control_20 plus
Platform_30-35, contract `three_topic_union` (Event/State/Periodic), dashboard 8080,
workdir `debug/mesh_runs/emane`, run `container-20261117T181119-2947503`.
Routing Service owns all entities; exactly three WAN pairs per gateway are
native-verified, with no extra participant, intermediate bus or plugin backlog.
All seven routers including C2 publish ROUTER_OK; six peers are ALIVE; missions
cancelled, teams unassigned, clocks 1x, speed zero, targets hidden, impairments empty.
Application names are Mission, Team and PlatformStatus; payload type names are
unchanged. Platforms start Init (Mission/Team from C2 plus internal traffic).
ActiveMission adds fleet-global PlatformStatus positions and same-team
PlatformMissionStatus/ContactReport. Local mission state alone selects filters;
whole-platform restart resets Init. Outgoing reports remain always on.

Final gates: 10 C++ targets; 386 nonlive router cases plus 284 subtests
(complete module ladder with final affected reruns); 74 native processor cases;
56 configuration/native time-filter cases; 30 dashboard cases plus 24 subtests;
29 WAN qualification cases; 38 workload cases plus 37 subtests; 9 adapter PASS
groups / 10 expected rejections; **all 5 live mission workflows passed, zero
skips, 380.53 s**, on matching qualified/deployed binaries. Groups overlap.
Actual matching-bundle rollback was exercised. Final seven-router logs contain
zero structured/native ERROR lines, not zero warnings: expected BEST_EFFORT
gaps and State KEEP_LAST(1) supersession remain rate-limited warnings.
Reliable Event history loss, rejections and other loss reasons remain errors.
Monitored diagnostics use the existing internal producer/codec/shared WAN
writer, retaining LAN publication and configured audiences/priority, not a LAN
input waiting for its own monitor.

Checkpoint 3.53's bounded diagnostics, valid-Status presence checks, LAN identity/
domain validation, receive-policy audit and adapter catalog repairs are deployed.
LAN identities include `/participant`, with a deterministic SHA-256 form beyond
255 bytes. Prepared full-type non-ACT data-plane bindings are supported; ACT
monitoring/policy remains catalog-specific. Generic export/setup UI and new
numerical load/recovery guarantees remain separate. An additional legacy
BEST_EFFORT Status churn/disposal case still fails with 41 ghosts, identically
reproduced against the old binary; do not call every extra spike green.
See the [completion acceptance record](../docs/router-processor-completion-plan.md#completion-acceptance-record-2026-11-17),
[checkpoint 3.54](../docs/two-topic-wan-migration-plan.md#354-independent-native-processor-completed-and-deployed-2026-11-17)
and [native XML guide](../router/routing_service/README.md).
Compact local evidence is under `debug/test_reports/processor_release_20261117/`.
Disposable test state/rollback checkout were removed; rollback archive retained.
Use the owned `run_mesh.sh down --emane --workdir` lifecycle before rebuilding
live binaries/types/configuration; do not restart individual routers.

**Final-review follow-up (2026-11-17), source only:** internal diagnostics
failures now retain route error/timeout accounting and explicit error logs
without aborting heartbeat/link-stat scheduling. Persistent native writer
exhaustion reproduced the heartbeat flood in both WAN modes; 10 C++ targets and
four focused native cases pass after the fix. The retained mesh/persistent
binary remain unchanged. See [checkpoint 3.55](../docs/two-topic-wan-migration-plan.md#355-diagnostics-failure-isolation-qualified-not-deployed-2026-11-17).

### Baseline and implementation status

- **2026-11-13 exhaustive-review follow-up resolved:** cross-writer spike controls
  now use DDS reception timestamps and explicitly reject ambiguous ties; both
  churn entry points accept overrides for all supported priority-band variants.
  Fresh gate: 38/38 SDK/offline tests, 9/9 isolated native controls and two actual
  overridden-priority healthy workloads passed. Current cache-deployment guidance
  is corrected; telemetry slide PDF/PPTX are regenerated and footer clearance
  verified. Historical recovery results are unchanged. No production router,
  IDL/QoS, running binary or live mesh changes/restart. See migration-plan §3.43.

- **2026-11-13 review fixes deployed:** destination binds now stage resources
  and route bookkeeping before committing shared receive interests; failed
  State binds preserve the prior filter and release partial resources.
  Native regression covers both missing aliases and invalid filters, then
  successful retry and ContactReport delivery. The configuration summary now
  includes `Last Telemetry to DEAD`, with browser coverage and a new cache token.
  Qualification: 9/9 C++, 30/30 isolated DDS (337.71 s), 33 host display/parser/
  policy checks plus two SDK-only checks passed separately. Owned all-node
  down/build/up passed 9/9 C++ again; deployed binary matches the qualified build.
  The user approved interrupting three active ORBIT missions for deployment.
  Native discovery and live browser checks passed; the targeted live contact/
  target workflow passed 1/1 (25.97 s), not a new five-workflow gate.
  Final state: six peers ALIVE/ROUTER_OK, original teams/20x clocks restored,
  all missions CANCELLED at zero speed, no impairments. Mesh remains on 8080.
  See migration-plan §3.42 and `debug/test_reports/wan_review_fixes_*`.

- **2026-11-13 semantic WAN names deployed and live-verified:**
  `three_topic_union` now uses **Event / State / Telemetry**, replacing physical
  Contacts with State and Status with Telemetry. Event is unchanged. Legacy and
  two-topic Event/Status names are unchanged. This is a naming-only change:
  payloads/schema 3, LAN topics, keys, audience rules, QoS, priorities, pacing
  and health timing are preserved. Internal `WanEvent`/`WanStatus` types and
  `wan_contacts`/`wan_periodic` aliases are intentionally retained. No transient
  local or catalog-driven telemetry model was introduced.
  Fresh source gate: 9/9 C++, 16/16 native three-topic DDS (256.40 s), three
  targeted two-topic regressions (166.09 s), 56 launcher/display/wire checks
  plus 163 subtests; four image-only browser skips then passed on host (3.59 s).
  Owned all-node stop/build/up rebuilt the persistent router (9/9 C++ again).
  Native discovery verifies exactly one Event/State/Telemetry pair per router
  on Control_20 and Platform_30-35; all seven configuration caches and the
  live Observed WAN filters panel show the new names. Config labels/health
  recovery wording and the chart cache revision are updated.
  Final **5/5 live browser mission workflows passed, zero skips, 377.12 s**.
  Six peers ALIVE/ROUTER_OK, missions CANCELLED at zero speed, original
  TEAM_A/TEAM_B assignments and 20x clocks restored; impairments empty.
  Mesh remains on 8080 in `debug/mesh_runs/emane`; use the owned lifecycle,
  never overwrite the live binary or edit generated configs. Mixed old/new
  three-topic participants do not exchange the renamed topics.
  See migration-plan §3.41 and `debug/test_reports/semantic_wan_*`.
  Historical SEDP/log caveats and open recovery/health-priority/load gates remain.

- **2026-11-13 earlier three-topic baseline, names superseded by §3.41:**
  the user selected default heartbeat suppression and authorized main C++ router
  integration plus an owned coordinated live restart after qualification.
  `three_topic_union` separates Event KEEP_ALL, Contacts writer KEEP_LAST(1) /
  reader KEEP_ALL, and unchanged shared Status. Event/Contacts retain tested
  priority bands, shared nominal 250,000 B/s XML pacing and 512 KiB repair cap.
  Health-priority tuning remains separate. The user also approved raising the
  administrative writer-filter sequence bound from two to three; IDL/XML are
  regenerated, requiring coordinated restart. Fresh gate: 9/9 C++, 16/16 native
  three-topic DDS tests (257.85 s, zero skips), 54 launcher/display tests plus
  159 subtests; all four image-only browser skips subsequently passed on host.
  Two-topic regressions: 18 passed plus a separately passing opt-in loss case;
  three legacy regressions passed. The owned lifecycle stopped all seven
  containers, rebuilt the persistent router (9/9 C++), and relaunched Control_20
  plus Platform_30-35 in **three_topic_union**, dashboard port 8080.
  Native discovery verifies exactly three writer/reader pairs per router;
  live configuration snapshots report all three families.
  Initial live failures exposed missing DATA_SESSION wire decoding and a fixture
  assuming unassigned teams. Both are fixed without weakening routing/loss
  assertions; the monitor was reloaded by another owned down/up, retaining the
  qualified router binary. Wire regressions: 42 passed plus 107 subtests, 16
  SDK/binding-dependent host skips; actual capture: 100/100 DATA_SESSION decoded.
  Final **5/5 live browser mission workflows passed, zero skips, 355.79 s**.
  All six peers ALIVE/ROUTER_OK, missions CANCELLED at zero speed, original
  TEAM_A/TEAM_B assignments and 20x clocks restored; impairments empty.
  Workdir remains `debug/mesh_runs/emane`. Never overwrite the running binary
  or edit generated configs; use the owned lifecycle.
  Logs are not clean: startup status writes are diagnosed/skipped before enable,
  SEDP TX attribution remains incomplete, and historical native participant
  warnings are not qualified as resolved.
  See migration-plan §3.40 and the durable WAN tradeoff report. Do not claim the
  selected 19.1-20.3 s spike recovery meets the ten-second target.

- **2026-11-12 RouterConfig cache review fixes, source only:** both reported
  issues are resolved in source. Session eviction preserves the current health
  session's replay watermark; at capacity, oldest config-only identities absent
  from the roster are evicted explicitly. Bounds remain 256 identities and two
  sessions per identity; roster peers are protected. Permanent real-DDS tests
  reproduce both failures against the deployed binary, then pass against the
  private rebuilt router. Fresh gate: 9/9 C++ and 7/7 config DDS tests, zero
  skips, including 32-second packet-loss repair. Shared health/restart and legacy
  presence regressions also passed. A related test now waits for complete WAN
  matches and a current config revision before asserting revision stability;
  its startup race was reproduced against the old binary too. The persistent
  binary and seven-node live mesh remain untouched. Rebuild/restart through the
  owned lifecycle before deploying these fixes. See migration-plan §3.39 and
  local `debug/test_reports/router_config_cache_*`.
- **2026-11-12 reliable RouterConfig deployed:** user requested reliable
  filter changes, superseding optional best-effort snapshots (§3.36).
  `RouterConfig` is Event branch 6 on the existing RELIABLE/KEEP_ALL pair;
  every WAN health heartbeat omits details. First/change/30-second full config
  snapshots preserve late-reader recovery under existing VOLATILE durability.
  Session/sequence validation, early config caching, empty clears and explicit
  malformed-input warnings preserve the LAN dashboard cache/freshness shape.
  Internal Event interest survives route changes; deferred two-sided self-ignore
  covers both Event and Status. No additional topic/endpoint or QoS retuning.
  Fresh source passed 9 C++ targets, 8 isolated DDS cases (plus the final
  malformed-config/cache rerun), and 5 browser checks. Actual 100% packet loss
  for 32 seconds repairs a config published during loss after restoration.
  User-authorized owned teardown, serial persistent rebuild (9/9 C++), and
  coordinated seven-node restart are complete. All six platforms are ALIVE /
  ROUTER_OK; teams and 20x clocks restored, zero speed, impairments empty.
  Complete native wire decoding verifies every captured health omits filters:
  platform health is 1,648 CDR bytes, config 16,772 bytes. Chromium verifies
  cached Event/Status details and SQL expansion. A 40-second observation reports
  1.219 Mbps fleet data TX, including config refreshes (not RF airtime).
  Deployment also activates the previously tested 300-second timeout policy
  and two-sided native self-ignore. Mesh remains running on port 8080.
  Mixed-type, fleet/load and full live mission gates remain unqualified.
  See migration-plan §§3.37-3.38 and local `debug/test_reports/router_config_*`.
- **2026-11-12 recovery timing investigation, not deployed:** twelve private
  two-minute outage trials show repair-request stalls dominate the old
  12.929 -> 19.110 s timing difference: 5.891 s of the 6.181 s delta is additional
  time in long request gaps. Identical three-topic/no-MultiChannel repeats give
  13.869/19.331 s; keep MultiChannel available as the `bands_all` comparison,
  not assumed responsible for the slowdown. All ten three-topic trials preserve
  180 command/ACK pairs, but fail 10 s contact recovery. One unchanged paired
  control reaches 28.210 s and 2.254 s fresh RTT. Zero Contacts heartbeat
  suppression gives 12.227/11.781 s but does not remove all stalls or prove a
  complete fix. Twenty-two host tests and nine native controls pass. No default
  retuning or live-mesh changes by this investigation. See §3.35.1 and the
  [spike's controlled results](../spikes/three_topic_wan/README.md#why-129-s-became-191-s-controlled-investigation).
- **2026-11-12 three-topic spike, not deployed:** user-authorized
  [Status/Contacts/Event prototype](../spikes/three_topic_wan/README.md) separates
  reliable latest-state Contacts from reliable KEEP_ALL required Events; Status
  is unchanged. Independent writers share the nominal 2 Mbps HPF controller,
  priorities 100/10, without MultiChannel. Both nodes have one writer/reader per
  topic and remote-only matches. Twenty host, nine new native and 32 existing
  native controls pass, including negative Event-loss oracles. The two-minute
  outage preserves all 180 commands/ACKs (14.124 ms worst fresh RTT), correct
  final contacts and zero pending samples. State catch-up takes **19.110 s**:
  the 10 s gate still fails in JUnit/exit status. This reuses schema-3 WanEvent
  on Contacts only in an isolated private-network spike; production schema,
  routing and the live mesh are unchanged. Restart, late joining, tombstone
  retention, ownership and fleet/load qualification remain open. See §3.35.
- **2026-11-12 KEEP_LAST(1) experiment, not deployed:** an isolated profile
  changes only writer history on the 512 KiB repair candidate. Receipt-triggered
  current-state recovery is 12.929 s, fresh RTT 13.726 ms, with correct final
  contacts/zero pending samples. However, 120/180 command ACKs and 10,072/14,561
  valid contact writes are missing. Required-event and 10 s recovery gates fail.
  An earlier 180 s observation confirms losses; the refined 10 s terminal run
  corrects periodic convergence sampling near producer ticks by checking at
  receipt processing too. Preserve method/run distinctions. Eighteen host and
  32 native controls pass, including queued update/dispose/revive replacement.
  Do not deploy KEEP_LAST on the shared command writer. See migration-plan §3.34.
- **2026-11-12 larger repair-response experiment, not deployed:** an isolated
  XML profile raises only Event `max_bytes_per_nack_response` from 128 to
  512 KiB. Complete valid-write/ACK coverage remains, but contact convergence
  is 61.580 s versus 60.342 s corrected baseline, and worst fresh RTT is
  2.315 s versus 13.645 ms. Original contact/command gates fail; do not adopt
  the increase. One comparison does not prove the RTT difference causal.
  Captured request bitmaps remain mostly 256 bits; investigate request-window/
  cadence and pacing next. Seventeen host and 24 native controls pass.
  Event remains KEEP_ALL; flow control and priorities already operate together.
  No production QoS/live deployment changes. See migration-plan §3.33.
- **2026-11-12 native self-ignore correction, deployed in §3.38:** production already
  ignores its own WAN writers; WAN readers now also use subscription ignore,
  including deferred Status startup. The spike previously omitted native ignore;
  it now applies both and verifies one remote reader per writer. Writer ignore
  alone suppresses local delivery, not writer destinations. Two-sided ignore
  halves healthy source Event DATA UDP bytes and fixes that extra local copy.
  Corrected two-minute results: all 14,561 valid writes/all 180 ACKs, worst fresh
  RTT 13.645 ms, contact convergence 60.342 s. The 10 s contact gate still fails.
  Corrected batching still fails history/latency. Sixteen host, nineteen native,
  nine C++ and twelve targeted production DDS checks pass. Earlier pacing
  numbers below predate this correction; see migration-plan §3.32.
- **2026-11-12 recovery/packing measurements, not deployed:** equal nominal
  2 Mbps coarse/fine pacing recovers contacts in 72.664/71.828 s, observing all
  14,561 valid writes, but still fails latency budgets. Native batching reaches
  final state in 64.627 s yet has 15.846 s fresh command RTT and 12,400 valid
  writes not observed; reject it. A stronger Event-history oracle now detects
  missing intermediate writes, independently reproduced over a 12 s outage.
  Sixteen host and sixteen native scheduling/lifecycle checks pass; all long
  outage performance gates fail. Captures show mostly individual disposals
  and extra local ACK-reader contact traffic with both MultiChannel variants.
  Qualify sender-side filtering/fan-out and usable recovery capacity next.
  No logical disposal aggregation/history dropping is implemented. Raw captures
  are under `debug/pcap`, reports under `debug/test_reports`; see migration-plan
  §3.31 and the spike README. Status/live mesh remain unchanged.
- **2026-11-12 generic priority-band follow-up, not deployed:** the churn
  spike adds `priority_bands`, with static `DDSPRIFLTR` XML channels for
  `@priority >= 100` and `< 100`. Topic-to-priority map/CLI stamps writes
  and disposals; DDS selects the band. Twelve host and twelve native
  ordering/lifecycle checks pass, including priority 0 and same-band FIFO;
  configuration-only inverted priorities pass the revival control.
  The two-minute outage still fails: all command ACKs recover, fresh RTT
  2.17 s, but 7,296 samples remain pending and contact state is unconverged.
  Status is unchanged/outside this Event controller, not implicitly lower
  than Event. See migration-plan §3.30 and the spike's band results.
  The MCP's wrong `PRIFILTER` literal is recorded locally in the shared
  correction-log submodule; its publication/re-pin is not performed here.
- **2026-11-12 native priority experiment, not deployed:** the churn spike
  uses XML HPF/asynchronous publishing and configurable per-original-topic
  sample priorities (commands/ACKs 100, ContactReport writes/disposes 10).
  Four deterministic checks prove per-topic channels inside one Event writer
  let a command overtake 400 contacts; sample priority alone does not.
  Eleven host tests and same-key dispose/revival pass. Four serial two-minute
  outage runs pass healthy controls but all fail outage acceptance. Channels
  acknowledge every command and improve worst fresh RTT from 9.16 to 2.39 s,
  still over 2 s; the nominal 2 Mbps token budget leaves contact recovery
  undrained (7,296 pending). No thresholds relaxed or live settings changed.
  The three-topic experimental channel catalog is not production-ready;
  capacity, complete branch/filter/lifecycle coverage and actual-router/fleet
  qualification remain open. See migration-plan §3.29 and the spike README.
- **2026-11-11 system-timeout update, deployed in §3.38:** `WAN_TIMEOUT_SEC`
  defaults to 300 seconds and drives WAN discovery plus final two-topic death
  since last Status. `WAN_HB_PERIOD_SEC=3` derives 100 retries in C++/Python
  startup before static XML loading. Actual-router 120-second outage tests
  pass (2/2); all native contact state/ACKs recover without reader inactivity.
  The native performance gate still fails: serial command RTT 8.49 s and
  scheduling lag 75.34 ms (convergence 9.98 s); no assertions were relaxed.
  A shortened-policy real health/restart regression and configuration checks
  passed. See migration-plan §3.28 and the churn spike's policy retest.
  The coordinated 2026-11-12 rebuild/restart activates this policy. Recovery flow control,
  prioritization, resource budgets and full current live missions remain open.
- **2026-11-11 destination-list migration deployed:** Source and the retained mesh use WAN schema
  version 3 and a shared `audience.destination_ids` sequence (16 total node/team
  IDs). The user explicitly enabled team-addressed commands and approved the
  coordinated deployment. C++ 9/9, targeted DDS/application 125/125, native
  qualification 29/29 and browser 5/5 passed; the persistent rebuild passed
  C++ 9/9 again. Live checks verified all 98 observed reader filters and accepted
  TEAM_B command ACKs from 30/31/35 only. Full regression/live mission workflows
  were not rerun for schema 3. Never mix schema versions. See migration-plan §3.27.
- **2026-11-11 filter update:** Two-topic WAN CFTs now select readable
  `original_topic` names, including RouterHealth; the codec still checks topic/
  discriminator agreement. Identical topic sets share grouped audience selectors.
  Native qualification passed 29/29, fresh C++ 9/9, targeted controller DDS 14/14,
  adapter pilot DDS 11/11 and browser regressions 5/5.
  The user-approved owned teardown/rebuild/relaunch deployed this
  to Control_20 plus Platform_30-35 on port 8080. Teams were restored (32/33/34
  TEAM_A, 30/31/35 TEAM_B); all six peers ALIVE/ROUTER_OK, impairments empty.
  Only fixed SHARED participant isolation remains; team changes use CFTs/metadata,
  not DDS partition changes. See migration-plan §3.26. Full missions were not rerun.
- Baseline tag: `pre-two-topic-refactor-2026-11-08`, pointing to `008ff4d`.
  The preceding checkpoints are `4549e87` (separate docs/slides), `86576aa`
  (plan/comparisons), and `fc44d6b` (union qualification/adapter spike).
  These checkpoints and tag are local, not pushed; do not push without a request.
- **Schema/codec, shared adapters and opt-in controller/interests are implemented;
  canonical production configuration remains legacy.**
  Production IDL now declares WanEvent/WanStatus and all 15 branches; committed
  XML is regenerated. [WanCodec](../router/src/core/WanCodec.hpp) and its exact
  catalog/tests and [WanAdapterFactory](../router/src/core/WanAdapterFactory.hpp)
  are linked into router_main. `router.wan_contract: two_topic_union` selects
  [WanControllerFactory](../router/src/core/WanControllerFactory.hpp); omission
  keeps the legacy factory. Original wire types must equal the XML catalog before
  binding. Application LAN contracts are unchanged except ContactReport now keys
  on `(source, contact_id)` and receive-side decode restores the authoritative
  WAN source into the existing payload source. The health transport slice
  of Phase 6 was brought forward: presence now uses the health branch of Status,
  sharing one Status writer/reader with applications, retaining LAN summaries
  and passive RTT. The user-approved simplification uses `wan_union_periodic`
  (BE/volatile/KEEP_LAST(1), 2s deadline, infinite liveliness); a health deadline
  or health NO_WRITERS/dispose starts STALE -> 30s DEAD. Any valid received
  Status restores ALIVE; repeated misses never restart the countdown. Only the
  listener's supplied health handle is examined, with no generic sweep.
  Dispatcher ownership and detach
  barriers are hardened; targeted legacy regressions passed.
- Fresh Connext-container baseline: **5/5 C++ tests; 237 Python tests plus 155
  subtests passed; 23 browser/live-dashboard checks skipped** (18 lack Playwright,
  5 require `MISSION_DASHBOARD_URL`). Do not call this complete browser/fleet coverage.
- Earlier controller checkpoint: fresh router/pilot build and **8/8 C++ tests passed**.
  [Adapter checks](../router/qualification/run_adapter_checks.sh): **11/11 Python
  DDS tests passed** (81.70 s), four production adapter pilots, four three-node
  controller tests and three legacy regressions.
  Production codec -> DDS -> codec -> original LAN equality, endpoint sharing,
  alias deduplication, reference removal/re-enable, failure cleanup, stale
  generations, active-traffic detach, feedback suppression and physical statistics
  registration are covered. [Union qualification](../router/qualification/run_checks.sh):
  **28/28 passed**, no skips (60.68 s), matching all sixteen receive-interest
  parameters and all outgoing membership slots plus mixed audience/filter updates.
  Controller tests cover all fourteen application cases and exactly Event/Status
  WAN publication names, command/broadcast/mode routing, team changes, source alias
  gates, rejected-update rollback and wire-type error containment.
  Subsequent full regression rerun: **263 passed, 5 skipped, 156 passed subtests**
  (392.41 s), plus **8/8 C++** and **28/28 qualification** (62.08 s).
  All eighteen previously skipped offline browser checks now pass after installing
  Playwright/Chromium in an owned temporary test image. A subsequent owned
  five-platform EMANE run executed all five live mission workflows: **5/5 passed,
  zero skips**, 386.08 s. This closes the legacy live-mission regression gap,
  not live mission qualification of the new contract.
  Subsequent health-on-Status checks: **8/8 C++ and 13/13 DDS tests passed**
  (195.63 s), plus a strengthened protocol/RTT health check (18.65 s).
  Two-topic controller tests now keep presence enabled; health STALE/DEAD,
  same-name restart/new session, passive RTT, physical counters and continued
  application traffic are verified. Live two-topic missions were pending then.
  Those health results describe the prior dedicated-health implementation;
  the shared-pair simplification and current policy are in §3.12.
  Latest shared-Status gate: **8/8 C++, 16 DDS regressions plus the strengthened
  health test (114.68 s), and 20/20 production qualification cases (29.48 s) passed**.
  Health covers exact shared endpoint counts, ignored nonhealth deadlines,
  any-Status recovery, the non-restarting 30s countdown, NO_WRITERS/dispose,
  crash/restart/session recovery and RTT/protocol counters. Native XML assertions
  verified infinite liveliness; native writer filtering passed with the real
  shared Status profile. This is not a full-suite/live-two-topic mission gate.
  Subsequent user-authorized deployment stopped the legacy mesh, rebuilt the
  persistent router, and launched five platforms with the canonical EMANE
  `--wan-contract two_topic_union` flag. Native discovery before/after testing
  confirms exactly one Event and one Status writer/reader on each of six routers.
  **5/5 live browser mission workflows passed, zero skips, 388.67 s**, including
  blackout/recovery, reporting/targets, route UI/ACKs/CommEffect/RTB, concurrent
  multi-team orbits and live goal inheritance/reassignment. Physical wire-row
  assertions explicitly select the contract; LAN mission assertions remain.
  **Current mesh is the NEW two-topic design**, still running on port8080 in
  `debug/mesh_runs/emane`. All five peers ALIVE/ROUTER_OK, missions cancelled,
  original teams/clocks restored, controller available and impairments empty.
  Use the owned lifecycle to stop/reconfigure it; never patch running configs.
  Native matched-participant-data warnings remain; do not claim clean logs.
  Details are in migration-plan §§3.6-3.13.
  [Whole-structure adapter spike](../spikes/two_topic_union_adapter/README.md):
  9 PASS groups and 10 expected rejections, using real DDS participants. It proves
  whole-structure packing/unpacking, sender filtering, and seen-instance
  dispose/revive/unregister, not final production restart/load/health integration.
- Use the verified `connext:7.7.0` container environment from the baseline record,
  not stale host binaries. Temporary builds/logs were removed; compact local reports
  under `debug/test_reports` are ignored by Git and may later be cleared/overwritten.
- The standalone internal brand-guide PDF is deliberately untracked. Preserve it
  and unrelated work; do not reset the checkout to the baseline tag.

### Live mesh retained for inspection (2026-11-11)

- The user requested leaving the verified mesh running. Check current ownership
  before another run: Compose project `act-emane-container-baseline`, control
  plus Platform_30 through Platform_35, **schema version 3 two-topic WAN mode**,
  dashboard port 8080. The persistent router binary was rebuilt from current
  source before the coordinated launch; all earlier review fixes are deployed.
- Workdir: `debug/mesh_runs/emane`, run `container-20261111T152950-1705974`.
  Post-deployment checks confirmed all six peers
  `PRESENCE_ALIVE` / `ROUTER_OK`, controller available and empty impairments.
  The latest user memberships were restored: TEAM_B on 30/31/35; 32/33/34
  unassigned. Live browser checks confirmed the readable destination-ID filters.
- Historical complete gate (2026-11-09, schema version 2): **9/9 C++ targets, 283 Python tests plus 169 subtests,
  29 WAN qualification cases, and 87 spike tests plus 63 subtests passed**.
  All **five unchanged live mission workflows passed**, zero skips, 365.59 s.
  The user-approved simulator fix now reports zero speed immediately on COMPLETE
  and CANCELLED, with six red/green regression checks. See migration-plan §3.18.
- [Live mission JUnit](../debug/test_reports/contact_schema2_live_missions.xml) and
  [browser screenshots](../debug/test_reports/contact_schema2_mission_screenshots/)
  are local ignored evidence. Never overwrite a running router binary; use a
  coordinated owned teardown/rebuild/relaunch for further wire-contract changes.
- Use the owned lifecycle for teardown, not individual router restarts:
  `CONNEXT_SHARED_DIR=/home/dgreenberg/rti_connext_dds-7.7.0 harness_v2/scripts/run_mesh.sh down --emane --workdir /home/ssm-user/act-sim-scope-infra/debug/mesh_runs/emane`.
  Do not run DDS e2e tests against this live mesh.

### Agreed conversion rules

- Exactly two shared WAN topic names: **Event** and **Status**. Existing LAN topics
  remain behind adapters; RouterHealth and WAN diagnostics become Status cases.
  Reliable RouterConfig filter snapshots are Event cases; health stays compact
  on Status. The LAN mesh cache may embed those details without repeating them on WAN.
- Select a predefined branch from a validated topic/type mapping and copy the
  **whole original DynamicData structure**; decode it back to the allowed original
  LAN topic/type. Preserve original keys, identity, timestamps, and audit fields.
  **Field mapping and operation-specific command redesign are deferred**, including
  rewriting the existing command octet payload. This is not a zero-copy promise.
- Kind is the union discriminator, not a duplicate envelope field. Schema version 3
  retains the version 2 Event/Status keys, `source_id + original_topic + instance_id`; source ID is
  the configured system/node name, not the internal router process name.
  The instance ID is the raw catalog-selected payload identity, with no encoded
  branch/topic prefix. ContactReport uses its existing `contact_id`; its LAN DDS
  key is `(source, contact_id)`. On WAN-to-LAN decode, copy the envelope's
  originating `source_id` into the existing payload `source`, including key-only
  recovery. Do not trust the producer's source or substitute the receiving node's
  identity. Local producers may leave source empty; no payload field is added.
  Other payload key annotations and all payload fields remain unchanged.
  Do not key the whole union payload or
  attempt a keyed discriminator on 7.7. The user approved the same stable keys
  for Event (with required KEEP_ALL retention) and no tighter legacy name/key
  limits. This supersedes the version 1 two-key/encoded-ID contract and requires
  a coordinated participant rebuild/restart; do not mix versions on a live mesh.
  The generic instance ID remains bounded at 512 bytes. Preserve the full
  current 16-entry team set. Endpoint queue/admission budgets remain open.
- Reuse WAN endpoints by Event/Status class, not by legacy topic. Health shares
  the single Status writer/reader; no dedicated health endpoint class or finite
  liveliness lease in opt-in mode. Preserve controller generations, detach barriers, loop guards,
  source enable gates, and one intended logical send/output.
- Team/data audiences move to CFTs and envelope metadata; partitions may remain
  for coarse fixed endpoint isolation. After the existing internal team-control
  command is applied, stamp subsequent team-scoped samples from updated local
  state and update receive CFT interests. Periodic status may converge gradually:
  old queued metadata/deliveries and brief new-team gaps are possible; no fleet-wide
  atomic barrier is required. Required events retain their separate delivery policy.
- Event and Status share `audience.destination_ids`, capped at 16 total individual/
  team IDs, replacing the version 2 scalar destination and team list. Commands
  may address a platform or its current team. Global/control flags remain additive;
  original LAN payload structures are unchanged. Consumers must recognize current
  team destinations; the simulator and mesh-control helper use local RouterStatus.
  The dashboard still publishes mission commands per platform for ACK tracking.
- Preserve TeamAssignment broadcast, addressed-command rules, global navigation,
  and supported multiple audiences/teams. Membership changes do not make all traffic
  team-scoped. Do not invent receiver rejection of every already-accepted sample.

### Isolated discovered-union/catalog spike (2026-11-16)

- [Schema propagation options and proposed Python setup workflow](../docs/wan-schema-propagation-options.md)
  records runtime union discovery, optional State catalog, preloaded shared
  XML and opaque-codec alternatives, with a matching one-slide comparison.
  Exactly three WAN families remain the design; the subsequent XML plan uses
  six logical channels to derive family and priority. The development router's
  Telemetry name is distinct from the Routing Service Periodic contract.
  Preloaded XML generation/distribution and reduced WAN type traffic are
  proposals requiring tooling and packet qualification, not spike results.
- The same runner's `--discovery-only` mode removes State entirely. Topic-named
  branches use sparse labels 17/42; the platform derives bindings from union
  discovery and selects only alive local subscriptions with matching topic names
  and exactly equal discovered structures. No topic-selection message or
  per-sample `original_topic` field is needed. Five phases send 60 commands;
  Move/Mode/none/same-name-incompatible/both produce 3/3/0/0/6 native WAN pushes
  and exactly matching original LAN deliveries. State endpoint absence and
  explicit structural rejection are checked. Local discovery uses controlled
  test-phase barriers, not a production continuous-rebinding implementation.
  Original catalog mode remains available and passes its regression.
- [Startup discovery/catalog proof](../spikes/discovered_union_catalog/README.md)
  passes in separate application, gateway and platform processes on Connext 7.7.
  Gateway learns complete LAN command structures, constructs a runtime Event
  union, and publishes one retained State catalog before the platform starts.
  Platform discovers the envelope/nested types without application type XML,
  selects a catalog binding and restores whole original LAN commands.
- Each run sends 36 commands; Move/Mode/none selection restores 3/3/0 with exact
  native Event writer push deltas of 3/3/0, full original payload equality,
  duplicate/destination/family checks and two explicit rejection cases.
  Private network/IPC namespaces isolate the spike from the retained mesh.
  No production contract/configuration or live-mesh change was made.
- This removes manual union authoring, not deployment-specific wire schemas.
  Runtime branch addition, stable multi-sender contracts, large type graphs,
  command execution/authorization/ACKs and outage/load/lifecycle remain untested.

### Isolated native LAN auto-QoS qualification (2026-11-16)

- [Qualification and repeat instructions](../router/routing_service/README.md#native-lan-auto-qos-qualification)
  record a fresh Connext 7.7.0 processor build and 52 characterization scenarios,
  plus a verbose diagnostic probe, in owned private network/IPC containers.
  Native discovery shows input reliability/durability inference and output
  durability inference with `ON_DOMAIN_MATCH`; focused positive cases verify
  whole-payload forwarding through our processor, including explicit deadline
  plus inferred durability. A finite consumer deadline was not inferred.
- `IMMEDIATE` with auto-QoS enabled did not create the LAN endpoint or reach the
  processor startup marker within 10 seconds. With `ON_DOMAIN_MATCH`, one
  absent configured LAN input prevented the shared processor route from
  starting and forwarding another matched input. This occurred with auto-QoS
  both off and on. Resolve that startup barrier before adopting it by default.
  Renderer/runtime/live mesh remain unchanged; full policy coverage, endpoint
  conflicts, replacement and fleet/load behavior are not qualified.
- Compact ignored evidence: `debug/test_reports/rs_autoqos_summary.json`.
  The [Quickstart](../docs/quickstart.md) reflects these constraints. Temporary
  build and raw diagnostic output are disposable, not deployment assets.

### Next work and unresolved checks

- **2026-11-11 user-approved radio update:** canonical RF Pipe now uses **2 Mbps**.
  The extra virtual transport cap remains disabled; PHY bandwidth remains 1 MHz.
  Owned restart retained six-platform run `container-20261111T003138-4127828`
  on port 8080 in `two_topic_union` mode. All seven native radios report
  `datarate=2000000`, transport `bitrate=0`; all six peers ALIVE/ROUTER_OK with
  empty impairments. Two config tests and shell syntax passed; all 36 location
  flows stayed fresh (720 samples, maximum age 0.021 s). Read-only live browser
  rendered six platforms without errors. See migration-plan §3.25. The five
  passing mission workflows below were run at 1 Mbps, not repeated at 2 Mbps.
  This supersedes the previous retained run and current radio rate.
- **2026-11-11 mission-stale fix: live gate GREEN.** Removed only the canonical
  EMANE virtual transport's combined RX+TX cap; RF Pipe remains 1 Mbps and
  navigation retains its 2.5 s stale threshold and 300-foot collision buffer.
  All 22 targeted checks and all five unchanged live mission/browser workflows
  passed (385.37 s, zero skips or cleanup errors). Postflight measured all 36
  location flows: 720 samples, maximum age 0.456 s. See migration-plan §3.24.
  Retained six-platform run: `container-20261111T001725-4096008`, port 8080,
  `two_topic_union`, all six peers ALIVE/ROUTER_OK and empty impairments.
  The user ran a new Platform_32 mission after the passing test cleanup; preserve
  operator activity rather than forcing the test baseline. The persistent router
  binary/hash is unchanged. This supersedes earlier live-state/failure notes,
  not the remaining Phase 5 fleet-load/resource-budget qualification.
- **2026-11-10 full isolated gate and historical live failures.** Fresh isolated checks
  passed nine C++ targets, 424 Python tests plus 242 subtests (including all
  21 offline Chromium checks), 21 native purge cases and nine adapter PASS
  groups, with zero skips. After a user-authorized clean six-platform restart,
  the unchanged live suite passed three workflows but failed concurrent fleet
  ORBIT convergence and the following reassignment check, with two cancellation
  ACK cleanup errors. Independent inheritance passed its body but still errored
  during cancellation cleanup. These failures were resolved by the §3.24
  transport fix and unchanged live rerun; the isolated suite was not repeated
  for that shell-only fix. See migration-plan §3.23 for historical evidence.
- **2026-11-10 lifecycle-only finalization:** reviewed and freshly rebuilt the
  native dispose/unregister relay independently of uncommitted filter-observation
  and dashboard work. Nine C++ targets, 273 Python tests plus 171 subtests,
  29 WAN qualification cases, and 21 purge cases passed; 24 optional browser/live
  checks skipped. Four per-leg cases passed an additional evidence rerun.
  See migration-plan §3.20.1. The active seven-router EMANE mesh was untouched;
  no persistent binary rebuild, deployment, or live mission gate was performed.
  Unseen instances, resource churn, automatic purge and durable restart/replay
  remain separate gates.
- **2026-11-10 six-platform live deployment:** the integrated persistent router
  was rebuilt (9/9 C++ targets passed), then the owned EMANE lifecycle launched
  Control_20 plus Platform_30 through Platform_35 in `two_topic_union` mode,
  dashboard port 8080. All six peers are ALIVE/ROUTER_OK; Event/Status observations
  report seven named readers each on all seven routers. Browser canvas/inspectors
  passed with no page/console errors; controller available, impairments empty.
  The mesh is left running in `debug/mesh_runs/emane`, Compose project
  `act-emane-container-baseline`. All running router executable hashes match the
  rebuilt binary. See migration-plan §3.22 for evidence and owned teardown.
  Live missions and health congestion/load were not run for this deployment.
  This supersedes the earlier source checkpoint's no-deployment status below.
- **2026-11-10 writer-filter source checkpoint:** the main working tree now
  includes per-reader Event/Status filter observations on RouterHealth and their
  dashboard inspector, preserving native lifecycle and Config-tab work. The
  local health snapshot retains the exact transmitted heartbeat acknowledgments.
  Final isolated gate: nine C++ targets, 304 router/dashboard/browser tests plus
  179 subtests (five live mission skips only), 29 WAN qualification cases, 87
  spike tests plus 63 subtests, 21 purge cases and nine adapter PASS groups.
  See [migration-plan §3.21](../docs/two-topic-wan-migration-plan.md#321-writer-observed-filters-integrated-into-main-2026-11-10)
  for initial-attempt caveats and evidence. No commit, push, persistent binary
  rebuild or deployment was performed. Rebuild consumers together before using
  the changed IDL/XML; live missions and health congestion/load remain open.
- **2026-11-10 lifecycle source checkpoint:** application dispose/NO_WRITERS now
  uses the same two-topic route bindings and native output writer registry as data.
  XML-authored infinite reader-instance retention is composed only into adapter
  inputs; automatic purge is deferred. Producer aliases sharing boat/contact ID
  intentionally share lifecycle (user approved), with no separate ownership ledger.
  Nine C++ targets, 280 router/dashboard tests plus 177 subtests (25 browser/live
  skips), 29 WAN qualification cases, and an additional dispose/unregister sequence
  test passed. See [migration-plan §3.20](../docs/two-topic-wan-migration-plan.md)
  and the [implementation plan](../docs/two-topic-native-lifecycle-plan.md).
  Persistent router binaries were not overwritten or deployed. During this work
  the six earlier mesh containers were observed stopped; inspect ownership before
  a later launch. This supersedes historical "currently running" claims above.
- Controller/interest checkpoint is verified (migration-plan §3.8). Next is
  Phase 5: lifecycle/restart/multiple-producer ownership, required-event retention,
  latest-state replacement, priorities/flow control and bounded load/admission.
  Settle open acceptance/resource budgets before qualification/cutover.
- New mode currently requires the XML catalog/QoS library, one local-role WAN
  participant with an opt-in profile, exact mapped `dynamic_data` WAN routes and
  unpartitioned endpoints on the admin LAN. Team state updates CFTs and outgoing
  metadata, not WAN discovery partitions. Unsupported LAN partition/control-audience
  changes reject explicitly. WAN match counts are shared-class connectivity, not
  per-original-topic remote producer readiness.
- Exact audience names retain the 255-byte bound and sixteen-name aggregate limits
  for receivers and merged source aliases. No wildcard patterns, apostrophes or
  NULs: the user approved explicit rejection of apostrophe names, not an encoding
  scheme. Original payload keys and legacy mode are unchanged.
- Keep qualified CFT topic-first ordering: current selectors use `original_topic`,
  with grouped audience predicates (§3.26). Older discriminator/sequence-ordering
  findings are historical; do not substitute an unqualified predicate shape.
  Mixed predicates, replacement and sender suppression are tested. Start the WAN observer's built-in
  publication reader before routers; late creation loses the discovery evidence.
- Factory bind/unbind/shutdown runs on the controller strand. Metadata providers
  run on AWS workers and must use immutable snapshots without reentering the
  factory. Error callbacks must not throw or reenter; logical errors carry route
  generations, physical endpoint errors carry physical names/epochs.
  Pilot filters are deliberately broad plumbing checks, not final audience rules.
  Application lifecycle is now forwarded through existing routes (checkpoint above);
  unseen-instance, pressure/churn and durable restart/replay qualification remains open.
- The new opt-in `wan_two_topic_participant` XML profile raises only the filter
  property limit to 16384 bytes; active legacy profiles retain their limits.
  A default 256-byte property rejected even a 16-slot filter. Production team
  qualification covers empty/short/full sequences, exact matches and a health
  branch excluded by the discriminator, with remote protocol push counters.
  The `_emane` opt-in variant is also present and preserves its base transport settings.
- Continue application lifecycle/restart/load; health congestion/timing qualification;
  evidence/clients; qualification
  and rollback-tested cutover. Current aggregate role/global/addressed/team CFTs
  are qualified; future schema compatibility, multi-producer lifecycle ownership,
  fleet-scale convergence and load remain unverified.
- Resolve the known health XML/code discrepancy (DDS profile 2 s deadline/3 s lease
  versus 8/12 s code constants and the 8 s coalesced-peer sweep) without silently
  retuning behavior. Qualify restart/unseen lifecycle keys, multiple producers,
  fleet teardown, aggregate filter convergence, and command/health interference.
- Do not start a live cutover, add permanent legacy WAN topics, or mark integration
  complete based solely on the passing spike. Apply the runtime-safety rules below.

## DDS / Connext runtime hygiene

- **Prefer UDPv4-only transport** for single-host test rigs
  (`<participant_qos><transport_builtin><mask>UDPv4</mask></transport_builtin>`). Then a
  `SIGKILL`ed participant cannot leak `/dev/shm` shared-memory segments.
- After kill-based tests, clean up: `pkill -x state_reader; pkill -x state_writer` (use
  `-x` exact-name match — `pkill -f` can match your own shell), and check `/dev/shm` for
  stray `RTI*`/`dds*` segments.
- Loopback UDP works for co-located test processes; isolate concurrent tests by DDS
  **domain id**.
- **Keep every domain id <= 232.** RTPS maps a domain to a UDP port as `PB + DG*D`
  (`7400 + 250*D`). That exceeds 65535 at **D = 233** and then wraps mod 65536, landing on
  an arbitrary port — sometimes in the privileged <1024 range, where the bind fails with
  `RTIOsapiSocket_bindWithIP: OS bind() failure ... Permission denied`. Measured here:
  `232 -> 65400` (ok), `233 -> 114`, `1023 -> 1006`, and `1046` gives a WAN port range of
  `[268900, 269149]` that matches no real traffic at all.
  An earlier version of this note quoted "~5900–6000" as the ceiling; that was **one
  observed instance of the wrap landing low, not the limit**, and following it cost a full
  e2e run (2026-08-11: `E2E_DOMAIN_BASE = 1000` failed exactly this way). Above 232 the
  failure is not a threshold you can stay under — it is a wrap that can land anywhere, so
  a domain id can look fine and silently mis-bind or capture nothing.
  Live domains in this repo: `20` (control_lan), `30–99` (platform_lan, one per platform),
  `200` (WAN). The e2e suite allocates triples across `101–199`, `201–232`,
  `0–19` and `21–29` (`conftest.E2E_DOMAIN_WINDOWS`). Concurrent independent runs
  still require owned network namespaces.

## Mesh debugging safety

- Treat a running mesh as immutable during diagnosis: inspect logs, bridge JSON, and code
  read-only first. Do not patch a generated config or manually kill/relaunch one router in a
  live mesh.
- Use `harness_v2/scripts/run_mesh.sh up --emane` and `down --emane` as the default live-mesh
  lifecycle. The containerized EMANE path is the normal launch mode for dashboard and mesh
  validation; use the host-process path only when explicitly requested or when debugging the
  host harness itself. The runner owns its PIDs and cleanup; do not bypass it with ad hoc
  restarts. Set `CONNEXT_SHARED_DIR=/home/dgreenberg/rti_connext_dds-7.7.0` for the license-only
  mount before EMANE container lifecycle commands unless a different readable license directory
  is explicitly provided.
- Reproduce a configuration failure in a fresh, isolated `/tmp` workdir with **one** platform
  first. Scale to a multi-boat mesh only after that reproduction is understood and torn down.
- Before starting any new mesh, verify no prior mesh processes and no `/dev/shm/RTI*` or
  `/dev/shm/dds*` segments remain. After every teardown or VM freeze, repeat that check before
  another DDS run.
- Prefer unit tests and builds for controller/UI diagnostic changes. Do not launch a live mesh
  solely to validate code that can be exercised in-process.
- For EMANE RF Pipe discovery tests, preserve the validated pathloss-event invocation in
  `harness_v2/scripts/run_container_baseline.sh`: use the range form
  `emaneevent-pathloss ... "${source_nem}:${target_nem}" 0`. The installed utility also
  documents target/reference controls, but validate packet counters before changing the
  harness's nominal RF setup.
- In this installed EMANE utility, the valid ascending range form `low_nem:high_nem` creates
  the bidirectional receiver/transmitter matrix for that contiguous NEM range; reversed ranges
  such as `2:1` are invalid, and wide ranges such as `1:4` affect every NEM pair in the range,
  not only the selected endpoints. For GUI one-way impairment, use the validated pair-specific form
  `emaneevent-pathloss -i eth0 -g 224.1.2.8 -p 45702 <tx_nem> <db> -t <rx_nem> -r <tx_nem>`.
  The same form with `0` resets that direction. A running container with zero
  `emane_mac_rx_*` and zero Domain 200 discovery packets is an RF/event path failure, not
  evidence of a DDS QoS mismatch.
- A successful `emaneevent-pathloss` exit status is not sufficient validation: the incomplete
  target form `source db --target target` accepted an impairment command yet removed Platform
  31/32 DDS peers. Validate EMANE RX/drops, Domain 200 discovery/data counters, route delivery
  percentage, and all expected `PRESENCE_ALIVE`/`ROUTER_OK` peers after every GUI impairment
  and reset. A good live GUI check is: apply `100 dB` to one direction, observe that only the
  selected route's delivery drops, reset that same direction to `0 dB`, wait for the delivery
  window to roll forward, and confirm delivery returns to 100% with all unrelated peers alive.
- RF Pipe pathloss is not a smooth application-delivery percentage knob in the current config:
  with `txpower=0 dBm`, `bandwidth=1 MHz`, `systemnoisefigure=4 dB`, and the packaged PCR
  curve, `90 dB` still maps to about 100% probability of reception while `100 dB` maps to the
  lossy region. See [`docs/emane/pathloss-scale-and-gui-control.md`](../docs/emane/pathloss-scale-and-gui-control.md)
  before interpreting or changing dashboard dB controls.
- For predictable dashboard latency, loss, and bandwidth controls, use CommEffect, not raw
  RF Pipe pathloss. CommEffect must be present as a shim in each generated EMANE NEM, and this
  installed EMANE version accepts `defaultconnectivitymode` (not `defaultconnectivity`) plus
  `enablepromiscuousmode`. Keep `defaultconnectivitymode=on` so nominal traffic flows before
  the first CommEffectEvent; with it off, startup traffic can drop as `No Profile`. Validate
  event processing with `shim0 EventReceptionTable` event `103` on every affected NEM. Reset
  with zero values; `unicast=0` and `broadcast=0` mean no bitrate limit. After a receiver
  processes a CommEffect event, omitted transmitters can be dropped as `No Profile`, so GUI
  pair controls must seed zero-effect profiles for all known transmitter NEMs on each affected
  receiver before overlaying the selected pair's impairment. See
  [`docs/emane/pathloss-scale-and-gui-control.md`](../docs/emane/pathloss-scale-and-gui-control.md)
  for the validated command form and status checks.
- Treat CommEffect state as directional (`tx_nem -> rx_nem`), even when the GUI presents a
  bidirectional pair. A bidirectional apply must record and reset both directional profiles;
  otherwise a later one-way reset can leave the reverse path impaired while the dashboard shows
  only the last zeroed command. This especially affects reliable DDS because ACKNACK/NACK and
  heartbeat repair traffic needs the reverse WAN path to get through.
- Delivery dashboard counts are sequence-matched audit facts from `events.jsonl`, not inferred
  packet counters: sent/received keys are `(run_id, topic, source_node, sequence)` plus receiver.
  `ControlCommand`, `PlatformCommandAck`, and `RouterHealth` use a 15 s grace window before
  missing samples count as lost; periodic status/report topics use 2 s. A 100% loss row under
  sustained bidirectional impairment can be real app-level non-delivery while DDS repair traffic is
  itself impaired, not necessarily a dashboard accounting bug.
- `RouterHealth` is now emitted into the same delivery-audit stream by the C++ router presence
  path, not by the Python sims: `heartbeat_seq` is the audit `sequence`, `send_timestamp` is
  `sent_at_ns`, and `source_node` is the node prefix before `/` in the router identity. The
  dashboard should expect control-to-platform and platform-to-control health flows; do not infer
  all platform-to-platform RouterHealth deliveries unless that routing/visibility is explicitly
  added and verified.
- When an EMANE experiment disrupts mesh discovery, do not attempt incremental manual repairs
  or recreate one router. Recreating only `control-20` can leave DDS discovery empty even when
  EMANE telemetry is fresh. Use the owned `run_container_baseline.sh down` then `up` lifecycle
  to rebuild the nominal RF matrix and wait for every peer in `/api/mesh_status`; process uptime
  or the harness smoke check alone does not prove all platforms rediscovered.
- The WAN discovery design is explicit unicast UDPv4: Domain 200 uses `initial_peers`, the
  WAN transport mask is `UDPv4`, and `WAN_RECEIVE_MULTICAST=0`. When peers disappear, first
  compare EMANE TX/RX and `traffic_stats.jsonl` discovery counters, then inspect Connext
  matched-participant warnings; do not treat repeated `Failed to get discovered_participant_data`
  messages as the root cause until RF receive traffic is present.
- A fixed EMANE `subid` combined with `excludesamesubidfromfilterenable=on` is suspicious,
  but changing sub-IDs alone did not restore this mesh. Keep the sub-ID experiment separate
  from pathloss-event and RF-interface diagnostics so A/B results remain attributable.

## Mesh dashboard frontend debugging (lessons learned)

- **Treat static JS as cache-sensitive.** After changing `gui/mesh_dashboard/static/*.js`, bump
  the version query in `gui/mesh_dashboard/static/index.html` script tags (for example
  `mesh_graph.js?v=...`, `traffic_chart.js?v=...`, `operator_tabs.js?v=...`) so the browser
  does not keep executing stale code.
- If the UI still shows old labels/behavior (for example "Resolution" after a rename to
  "Mode"), verify which asset revision is actually loaded in the page before changing logic.
  In browser devtools/automation, inspect `document.querySelectorAll('script[src]')` and
  confirm the expected `?v=` token is present.
- For dashboard layout edits, treat `#graph` and `#traffic-panel` as coupled. A fixed
  `#traffic-panel` height with a mismatched `#graph` bottom offset creates blank gaps or
  overlap. Keep them synchronized.
- Prefer content-sized bottom panels for WAN charts and compute `#graph` bottom spacing from
  the rendered panel height (minus status bar height), then update that spacing on resize.
- After merging stacked WAN charts into a single canvas, remove obsolete fixed-height
  assumptions and validate there is no empty panel space at the bottom.
- When validating GUI fixes, verify both source and runtime behavior:
  1. no syntax/lint errors,
  2. expected script revision loaded,
  3. live DOM/layout metrics (`getBoundingClientRect`) match intent,
  4. interactive labels/context menus show updated wording.
- For the Experiment tab specifically, verify the live browser has loaded the current
  `operator_tabs.js?v=...`, source/destination options come from `/api/mesh_status`, the
  controller status comes from `/api/emane_controller`, and apply/reset behavior is validated
  through the GUI path, not only by manually running `emaneevent-pathloss` in a container.
- After changing Delivery tab metric semantics, update the definitions text box below the table
  in `gui/mesh_dashboard/static/index.html` in the same change and browser-verify the rendered
  labels. Current definitions: `Sent` is rolling-window sends, `Received` is rolling-window
  destination receives, `Lost` is only messages outside the topic grace period that have not been
  received, and the grace period calls out reliable command/health topics versus periodic
  status/report topics.
- When diagnosing mesh graph color, separate topology evidence from link health: solid/dashed
  edges come from `ActRouterMeshStatus`/`peers_seen`, while orange/red health should come from
  `ActRouterLinkStats` counters and freshness. Under lossy CommEffect, app/status samples can keep
  arriving while the `RouterHealth` topic misses its presence deadline; that means degraded health,
  not necessarily a complete WAN outage.
- `RouterHealth` has a known timing discrepancy as of the 2026-11-08 baseline:
  XML supplies a 2 s deadline/3 s automatic liveliness lease, while code constants
  retain 8/12 s and the coalesced-peer sweep uses 8 s. Do not assume they agree or
  infer all production STALE/DEAD behavior from native DDS timing alone. See the
  [migration plan](../docs/two-topic-wan-migration-plan.md) for the reconciliation
  gate. If changing policy, update XML and the relevant presence constants/sweep
  together, then rebuild/restart; a browser reload only picks up static changes.

### Live CommEffect verification lessons

- Treat every scenario as contaminated until `POST /api/emane_controller` with
  `{"reset_all":true}` succeeds and a follow-up `GET` reports an empty `impairments` list.
  Restarting the mesh does not by itself prove the operator experiment state is clean. Record the
  precondition and reset again after every scenario.
- The Experiment UI's `pathloss_db=0` is not a generic reset signal: CommEffect applies always use
  zero pathloss. Reset logic must use the explicit reset flag, otherwise a valid CommEffect apply
  can be rendered or submitted as a reset. Apply requests are asynchronous; sequence responses so
  an older response cannot overwrite a newer multi-profile state. Verify multiple profiles through
  both the UI and `/api/emane_controller`.
- For complete Delivery coverage, assign platforms to a common team (for example `TEAM_A`) so the
  platform-to-platform `PlatformData` route is active, and set each platform to `debug` to expose
  Detail, Mission, Waypoint, Debug, Thruster, and Power status topics. `PlatformData` is not a
  control-to-platform flow; its expected recipients are other platforms sharing a non-identity
  team partition. A Delivery resolver that expects every platform will falsely report loss for
  unassigned peers.
- In `/api/mesh_status`, each peer has `presence`, `last_seen_delta_ms`, and `health` as sibling
  fields. `health.overall_state` is the last published RouterHealth summary and can remain
  `ROUTER_OK` after communication is lost; use the sibling `presence` (`PRESENCE_STALE` or
  `PRESENCE_DEAD`) for current reachability. Do not diagnose a presence defect by searching inside
  `health`.
- For raw EMANE evidence, resolve exact Compose names such as
  `act-emane-container-baseline-control-20-1` and `...-platform-30-1`; short service names are
  not valid `docker exec` targets. Check NEM 1 for Control_20 and NEM 2 for Platform_30 with
  `emanesh localhost get table <nem> all`, especially `shim0 EventReceptionTable` event `103`.
- The control container maps the dashboard to host port `8080`. Verify `/api/*` from the host only
  after confirming that mapping. A local WebSocket `101 Switching Protocols` proves the bridge;
  Dev Tunnel `502/504` failures are forwarding-layer evidence. Use the dashboard transport badge
  and REST fallback state when deciding whether browser evidence is live.

## Debugging Workflow

Use the repository debug tree as the first place to look for runtime evidence:

- `debug/logs/mesh/`: host-process mesh application logs, including `control.log`,
  `platform<ID>.log`, simulator and mesh-control logs, `mesh_bridge.log`, and
  `traffic_monitor.log`. For the containerized EMANE mesh, prefer the live per-node logs under
  `debug/<node>_debug/logs/` (`router.log`, `mesh_dashboard.log`, `traffic_monitor.log`, etc.);
  `debug/logs/mesh/` may be stale from an older host-process run.
- `debug/logs/network_monitor/traffic_stats.jsonl`: append-only WAN traffic samples from
  the dashboard monitor. The monitor watches DDS domain `200` only and splits discovery
  and user-data packet counts and bytes.
- `debug/logs/journal/control.jsonl` and `platform<ID>.jsonl`: JSONL samples captured by
  the canonical mesh lifecycle from each node's journal/status subscriber, containing
  `ActRouterControllerJournal` route decisions and `ActRouterStatus` snapshots.
- `debug/logs/router_e2e/<test-name>/`: rendered e2e configs and subprocess logs. Test
  failures expose the exact `log_path` to inspect.
- `debug/pcap/<application>/`: raw packet captures. Use `debug/pcap/router/` for the
  planned in-process Connext network capture and application-specific subdirectories for
  other capture tools.
- `debug/scripts/`: diagnostic tools; keep generated output in the matching `debug/logs/`
  or `debug/pcap/` application directory.

Recommended triage order:

1. Check the active lifecycle's router logs for process startup, shutdown, DDS discovery, and
  router errors: `debug/<node>_debug/logs/router.log` for container EMANE runs, or
  `debug/logs/mesh/` for host-process runs.
2. Check `debug/logs/journal/router_journal.jsonl` for the controller event, decision,
   pre/post `state_revision`, and status-publish action.
3. Check `debug/logs/network_monitor/traffic_stats.jsonl` for WAN discovery versus
   user-data activity and whether dashboard posting succeeded.
4. Run `python3 debug/scripts/dds_type_probe.py --domain 20 --wait 5` when a LAN endpoint or
  inline TypeObject is missing. Use the WAN traffic monitor for domain `200`; do not infer
  WAN discovery health from a probe that is not configured for the WAN discovery protocol.
5. Use the live dashboard only after confirming the bridge log and local HTTP endpoint;
   a blank Dev Tunnel page can be a forwarding problem rather than a WebSocket failure.
6. For an EMANE mesh with no peers, inspect `debug/<node>_debug/traffic_stats.jsonl` and
  `debug/<node>_debug/logs/emane.log` before changing DDS QoS. Confirm nonzero RF RX,
  Domain 200 discovery counters, and the generated `WAN_PEER*`/`WAN_RECEIVE_MULTICAST`
  values. The router's `Failed to get discovered_participant_data` warnings are often a
  downstream symptom of missing or unstable RF discovery traffic.

The `/clear` prompt removes generated logs and captures while preserving `.gitkeep`
directory markers. Stop the associated mesh, test, or capture process before clearing.

## Connext environment (this VM)

- `NDDSHOME=/home/rti/rti_connext_dds-7.7.0`, arch **`x64Linux4gcc7.3.0`**, Connext
  **7.7.0**, `rtiddsgen` 4.7.0, license at `$NDDSHOME/rti_license.dat`.
- CMake pattern: add `${CONNEXTDDS_DIR}/resource/cmake` to `CMAKE_MODULE_PATH`,
  `find_package(RTIConnextDDS "7.7.0" REQUIRED COMPONENTS core)`, generate types with
  `connextdds_rtiddsgen_run(... LANG "C++11" ...)`, link `RTIConnextDDS::cpp2_api`.
  Reference build: `spikes/isc_recovery/relay/cpp/CMakeLists.txt`.
- Modern C++ (C++11) API. Proven entity/QoS/`key_value()` patterns live in
  `spikes/isc_recovery/relay/cpp/isc_relay.cxx`.
- **Generated types use DIRECT public data members, not accessors.** Connext 7.7 follows the
  updated OMG IDL-to-C++11 mapping, so `rtiddsgen` emits struct fields as public members with
  `{}` initializers — write `s.target_node = "x";` / `s.routes.push_back(r);` / `s.routes.at(0)`,
  **not** the old getter/setter pairs (`s.target_node("x")`). Sequences are vector-like
  `omg::types::bounded_sequence<T, N>` (unbounded IDL sequences default to cap `100`). Verified
  against the `router/admin/RouterAdminTypes.idl` codegen.

## Docker Connext setup and test workflow

- Build the supported image from `docker/connext-7.7/`:
  `docker compose -f docker/connext-7.7/compose.yaml build`. The image installs the RTI
  7.7.0 SDK from RTI's Debian repository and `rti.connext==7.7.0` from PyPI; it does not
  require a host Connext SDK.
- The fixed host directory `/home/dgreenberg/rti_connext_dds-7.7.0` is **license-only**.
  It must contain `rti_license.dat` and is mounted at `/shared:ro`; use
  `RTI_LICENSE_FILE=/shared/rti_license.dat`. Do not use this directory as a Docker build
  context or assume it contains headers, `rti_versions.xml`, or the SDK.
- After adding a user to the Docker group, start a new shell. Until then use
  `sg docker -c '...'`; do not use `sudo docker`.
- For a disposable unit-test/build container, mount the checkout writable because CTest
  writes `router/build/Testing/Temporary/LastTest.log`:
  ```bash
  sg docker -c 'docker run --rm \
    -v "$PWD":/workspace \
    -v /home/dgreenberg/rti_connext_dds-7.7.0:/shared:ro \
    -w /workspace \
    -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
    -e RTI_LICENSE_FILE=/shared/rti_license.dat \
    connext:7.7.0 sh -lc "cmake --build router/build -j2 && bash router/run_tests.sh"'
  ```
- For e2e validation, mount the checkout read-only and keep all caches, logs, pytest state,
  and temporary files under `/tmp`. Set `PYTHONPYCACHEPREFIX=/tmp/pycache`, `TMPDIR=/tmp`,
  and run pytest with `-p no:cacheprovider` so `.pytest_cache` is not written to the share.
  Install pytest only inside the disposable container if the image does not already provide it.
- Before and after either test mode, check for `pytest`, `router_main`, and mesh processes and
  for `/dev/shm/RTI*` and `/dev/shm/dds*`. Use host networking for DDS e2e tests, reserve
  domains through `router/test_e2e/conftest.py`, and never run e2e tests against a live mesh.
- Rebuild `router/build` after C++ changes; stale binaries can report behavior from older
  source. The [2026-11-08 baseline](../docs/two-topic-refactor-baseline.md) records
  `bash router/run_tests.sh` with 5/5 passing and
  `python3 -m pytest -q -p no:cacheprovider router/test_e2e` with 237 passing,
  155 passing subtests, and 23 browser/live-dashboard skips. Record actual current
  results and skips rather than treating historical counts as a current gate.

## DDS QoS authoring rule

- **All fixed DDS QoS must be authored in external XML profiles.** Do not construct or tune
  reliability, durability, history, deadline, liveliness, publish mode, RTPS protocol, data
  representation, transport, discovery, resource-limit, or application-ack behavior in C++
  or Python code. Add or update profiles in `harness_v2/qos/act_qos_profiles.xml`, then load
  them through `dds::core::QosProvider` / `QosResolver`.
- Route endpoint aliases such as `wan_event`, `wan_periodic`, and internal profiles such as
  `router_health`, `router_link_probe`, `router_link_stats`, `router_mesh_status`,
  `router_admin_status`, `router_admin_command`, `router_admin_command_ack`,
  `router_controller_journal`, `router_default_reader`, and `router_default_writer` are the
  pattern to follow.
- Runtime identity/routing state and lifecycle mechanics that are inherently data-dependent,
  such as `EntityName` from the node/router name, partition membership from operator
  team/route commands, and the temporary manually-enabled participant factory setting used for
  disabled-startup ordering, are explicit exceptions. Keep them narrowly scoped and do not use
  those exceptions to smuggle fixed QoS policy choices back into code.
- After XML QoS edits, validate with the installed Connext `QosProvider` or XML validator and
  rebuild `router/build` before relying on the change.

## Validate Connext specifics — don't guess

A `connext` MCP server is available. Use it instead of relying on memory for Connext APIs,
QoS, and behavior:
- `ask_connext_question` for API/feature/version behavior (cite the answer).
- `validate_xml_code` for QoS XML (schema-checks and fixes).
- `validate_modern_cpp_code` for C++11 Connext API code.

**The MCP is a strong hint, NOT ground truth — the build/a run/introspection is the arbiter.**
It has been wrong repeatedly on this install (Python-binding surface, QoS-field names, XML
schema, discovery/type behavior). Therefore:
- **Before** relying on an MCP answer for anything load-bearing, first sync the doc, then
  check [`docs/connext-ai-issues/connext-ai-issues.md`](../docs/connext-ai-issues/connext-ai-issues.md)
  for a matching known-wrong entry. That path is a git submodule
  (`rtidgreenberg/connext-ai-issues`) **shared across repos** — its content can move forward
  without this repo's pinned commit knowing, so pull before trusting it:
  `git submodule update --init` if it's empty, then `cd docs/connext-ai-issues && git pull`
  to fetch entries other repos may have added.
- **Verify** MCP claims against the build, a runnable spike, `dir()`/`grep` of the actual
  headers/`rti.connextdds` binding, or `$NDDSHOME/doc` — never present an unverified MCP claim
  as fact.
- **When the build/empirical evidence contradicts the MCP, append an entry** to
  `docs/connext-ai-issues/connext-ai-issues.md` (newest at bottom: date, tool, claimed, actual,
  verified). This needs a two-part sync so other repos and this repo's own history both pick
  it up:
  1. `cd docs/connext-ai-issues && git commit -am "..." && git push` (commit/push inside the
     submodule itself).
  2. Back in the parent repo, `git add docs/connext-ai-issues && git commit` (and push) to
     re-pin this repo's submodule pointer to that new commit — otherwise a fresh clone or
     `submodule update` here would reset back to the old entry.
- **After every response that used or asserted an MCP Connext claim, cross-check it against
  `docs/connext-ai-issues/connext-ai-issues.md`** before presenting it.

Instance-state consistency (ISC) findings validated against 7.7.0 (see
`docs/cpp_router/`): native `RECOVER_INSTANCE_STATE_CONSISTENCY` recovers only the
**same-physical-writer reconnect** case (Scenario A). Recovery across a **restarted writer
(new physical GUID, same virtual GUID)** is **not** shipped in 7.7 (Scenario B; CORE-13337
for infrastructure services) — it requires durable writer history + app-level state
republish, and is the F3 feature under design.

## Wire-level verification (tshark/dumpcap)

For discovery/protocol claims the MCP can't settle (message timing, message size,
periodic-vs-event-driven behavior, which builtin entity sent what) — capture the real
traffic instead of trusting a description. Verified working on this VM without sudo:
`dumpcap` already carries `cap_net_raw,cap_net_admin` (`getcap $(which dumpcap)` to
confirm) and this user is in the `wireshark` group, so `dumpcap -i lo -w out.pcap` and
`tshark -r out.pcap ...` both run unprivileged. **Never run `tshark`/`dumpcap` without
`-r <file>` or `-w <file>`/`-c <count>`/`-a duration:N`** — an unbounded live capture on a
real interface (not `lo`) will pick up unrelated host traffic and run forever.

- Filter to DDS-RTPS traffic with `-Y rtps` (tshark's RTPS dissector understands SPDP,
  SEDP, and RTI's SPDP2 builtin discovery messages).
- Useful fields via `-T fields -e <field>`: `frame.time_epoch` (wall-clock, for bucketing
  against Python-side `time.time()` timestamps — not `time.monotonic()`), `frame.len`
  (wire size), `rtps.sm.id` (submessage type), `rtps.sm.wrEntityId` (which builtin writer
  sent it), `rtps.guidPrefix.src`/`.dst` (sender/receiver GUID prefix).
- **`rtps.guidPrefix.src` is a message-header field — one value per packet, not per
  submessage** (unlike `rtps.sm.id`/`rtps.sm.wrEntityId`, which can repeat if a packet
  bundles multiple submessages). Attribute captured traffic to specific participants by
  filtering on GuidPrefix (obtain a running participant's own prefix from
  `str(participant.instance_handle)[:24]` in the Python binding) — this avoids
  double-counting a packet across its submessages, which per-submessage fields would risk.
- Worked example: `spikes/partition_retarget/bandwidth_compare.py` captures on `lo` with
  `dumpcap`, parses with `tshark -T fields`, and attributes bytes to two test participants
  by GuidPrefix to compare steady-state bandwidth and per-event wire cost across discovery
  configurations (see `spikes/partition_retarget/README.md`'s "Wire-level bandwidth"
  section for the pattern and results).

## Spikes

Experimental proofs live in their own folder (e.g. `spikes/isc_recovery/relay/`) with a
`PLAN.md`, sources, QoS, a runner, and a `README.md`. Runners must place working dirs on a
local fs per the rules above.

## Test harnesses

Two test harnesses are available for debugging and verification:

Before claiming the full test suite is green, verify the environment that is actually running
the commands. A valid full router gate needs `cmake`/`ctest`, `pytest`, and an importable
`rti.connextdds` binding in the same shell/container that runs the tests. Do not treat stale
prebuilt binaries as a full-suite substitute: binaries compiled with `/workspace/...` sample
paths will fail config tests when run from `/home/ssm-user/...` unless rebuilt or executed from
the matching mounted path. The Connext Docker image is optional, not assumed; use it only after
confirming the image exists or Docker daemon access is available, then bind the current checkout
as `/workspace`, rebuild inside that environment, and run the tests there.

- **C++ unit tests** (`router/test/`): in-process controller/state-machine tests with fakes
  for all DDS seams. Run via `router/run_tests.sh`. **Do not use
  `ctest --test-dir router/build`** — `--test-dir` needs CMake >= 3.20 and this VM has
  3.16.3, where the flag is ignored, ctest scans the cwd, finds nothing, prints "No tests
  were found!!!" and **exits 0**; `run_tests.sh` cds into the build tree and fails a
  zero-test run instead of passing it. Fast,
  no DDS entities created.

- **Python e2e suite** (`router/test_e2e/`): launches real `router_main` subprocess pairs
  (control-role + platform-role) and drives DDS traffic through them from Python using
  `router/test_e2e/util/dds_probe.py`. Uses `conftest.py` fixtures (`router_pair`,
  `unique_domains`, `admin_types_xml`, etc.) and per-test YAML configs under
  `router/config/e2e_*.yaml` with domain-placeholder isolation. Run via
  `pytest router/test_e2e/ -v` from the repo root. Covers: route forwarding (every route),
  admin command/ack/status loop, QoS alias resolution, auto-QoS, content-filter drop,
  same-node ignore, presence/health/mesh, team partitions, link stats, discovery startup.
  Key utility: `dds_probe.Probe` (UDPv4-only participant), `AdminChannel` (status reader +
  command writer + ack collector), `write_until_seen` (poll-with-timeout).

- **Type probe** (`debug/scripts/dds_type_probe.py`): standalone diagnostic —
  `python3 debug/scripts/dds_type_probe.py --domain 20 [--topic ActTeamAssignment]
  [--wait 5]` — lists every discovered publication/subscription on a domain (topic, type,
  owning participant, name) and whether its inline TypeObject resolved. Surfaces exactly
  the fact `DiscoveryDispatcher.maybe_learn_type()` gates on, so a topic stuck in
  `TOPIC_IDLE` because no publication propagates an inline TypeObject (e.g. WIS writers)
  is immediately visible instead of requiring log-grepping.

- **Live mesh** (`harness_v2/scripts/run_mesh.sh --emane`): default launcher for a full
  N-platform router mesh in containers with EMANE, platform sims, mesh-control helpers, and the
  dashboard (`--with-dashboard`). Use this path for normal live dashboard/mesh runs, e.g.
  `CONNEXT_SHARED_DIR=/home/dgreenberg/rti_connext_dds-7.7.0 harness_v2/scripts/run_mesh.sh up --emane --platforms 8 --with-dashboard`,
  and tear down with
  `CONNEXT_SHARED_DIR=/home/dgreenberg/rti_connext_dds-7.7.0 harness_v2/scripts/run_mesh.sh down --emane`.
  The host-process path without `--emane` is for explicit host-only diagnostics. Logs in
  `debug/logs/mesh/` and generated lifecycle state in the selected workdir.

## Data model

All DDS types (application payload + router admin/status/presence) are defined in a single
IDL source of truth: `harness_v2/datamodel/ActTypes.idl`. The generated XML
(`harness_v2/datamodel/gen/ActTypes.xml`) is committed alongside and must be regenerated
after any IDL change:
```
$NDDSHOME/bin/rtiddsgen -convertToXml -d harness_v2/datamodel/gen harness_v2/datamodel/ActTypes.idl
```
The router's C++ codegen (`router/CMakeLists.txt`) generates from the same IDL at build
time. All YAML configs, Python scripts, and WIS reference the generated XML.
