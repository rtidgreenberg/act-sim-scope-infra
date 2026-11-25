# Graph topic subscriptions: isolated native spike

Implements a bounded proof of the
[subscription plan](../../docs/router-topic-subscriptions-plan.md), not production
integration. No production source/configuration, persistent router binary or live
mesh is changed.

```bash
bash spikes/topic_subscriptions/run.sh
bash spikes/topic_subscriptions/run.sh --extended
bash spikes/topic_subscriptions/run.sh --extended --transient-local
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s spikes/topic_subscriptions -p 'test_*.py' -v
```

Requires the existing `connext:7.7.0` image, the usual license-only directory and
the already installed host Playwright/Chromium. No dependencies are downloaded.
The owned runner serializes this spike with a nonblocking lock and uses a private
network/IPC container. It prints timestamped report/capture paths and its exact
disposable `/tmp/topic-subscriptions.*` workdir. After a run, retain required
evidence, repair ownership only inside that owned workdir if necessary, and remove
that exact directory after all owned processes stop. Do not unlink an active lock.

## What is real

Two native Routing Services each create three shared WAN pairs: Event, State and
Periodic. Only those services join the WAN. A small freshly compiled processor
operates on their actual content-filtered readers using the same native CFT API
as production. All fixed QoS comes from external XML; the spike's WAN participant
inherits the previous SEDP fixture and sets the existing production-equivalent
16,384-byte filter-property limit.

**Current path: receiver-side filter changes only.** A user request or local
mission transition changes the receiver's native CFT. DDS propagates that reader
interest to the source writers; no code configures a remote writer's selection.
The spike does not configure Routing Service administration, create an
administration participant/client, generate administration types or send RPCs.
It also verifies that the source service configuration and writer prefix remain
unchanged while the selected WAN sample identities change.

Three original application LAN topics use a complete typed Report structure.
A LAN-only fixture controller reads them, copies whole DynamicData into a typed
union and passes frames through Routing Service. The receiver verifies complete
original equality, not selected fields. Metadata/request/result branches are also
typed structures; there is no opaque WAN JSON/octet envelope.

Inventory is generated from prepared topic bindings and actual native LAN
subscription match counts. Producer availability changes increment its revision.
Inventory and applied-state snapshots travel on State, independently of optional
application interests. Requests and correlated results travel on reliable Event.
Mission phases arrive over a local DDS input. User selection persists across
mission changes until Auto is restored; active navigation remains protected.

The graph is a standalone spike page, not the production graph. Playwright clicks
its controls, which send actual DDS requests through the isolated fixture. Native
results and snapshots populate its available/subscribed lists. Local-file
synchronization replaces a future production bridge API.

## Results and evidence (2026-11-24)

### Initial mechanism gate

The initial receiver-only run, without Routing Service administration, passed:
fresh C++ build with `-Wall -Wextra -Werror`,
12 flow phases, 36 offered/12 selected original reports, four correlated browser
actions and additional fault/churn/restart checks. Native logs contain zero
ERROR/FATAL lines and capture drops are zero; ordinary native warnings remain.
Compact local evidence is
[the final report](../../debug/test_reports/topic_subscriptions_20261124T210806Z/report.json).
The wire capture is
[wan.pcapng](../../debug/pcap/topic_subscriptions_20261124T210806Z/wan.pcapng).
These ignored local files may later be cleared; the method/results are recorded
here. All owned containers and DDS/shared-memory resources were stopped/closed.

The report distinguishes 12 stable flow phases from additional negative,
availability, restart, rollback and browser cases. In each flow phase three
original reports are offered. Expected delivered counts are:

| Phase | Selected reports |
| --- | ---: |
| Auto Init | 0 |
| Auto active | 3 |
| User ContactReport, active navigation protected | 2 |
| User selection survives CANCELLED | 1 |
| Return to Auto at the current idle phase | 0 |
| Empty User selection, inventory still available | 0 |
| Empty User selection with active navigation protected | 1 |
| User selection cannot bypass a different team | 1 |
| Team restoration | 2 |
| Native multi-family rollback | 2 |
| Actual receiver router restart and fixture Auto reset | 0 |
| Browser returns to Auto | 0 |

This is 36 offered original reports and 12 selected reports, with full typed
payload equality and selected identities independently checked in the WAN
capture. It is not a throughput or recovery measurement. Stable measurements
wait one second for propagation before offering data; no instantaneous cutover
or one-second worst-case guarantee is claimed.

The runner additionally verifies stale/unknown requests, request-ID reuse,
native producer churn, stale inventory revisions, late receiving-LAN-reader
refresh, and explicit failure/degraded handling. Browser checks cover User/Auto,
empty selection, revision conflict, unsubmitted draft preservation during native
producer churn, and visible unknown state with editing disabled after divergence.
Three focused policy unit tests cover override precedence and invalid requests.

Timestamped evidence under `debug/test_reports/topic_subscriptions_*` contains
the native/browser reports, generated service XML, logs and three graph
screenshots. Bounded WAN captures are under `debug/pcap/topic_subscriptions_*`.
Failed construction/reproduction runs are not passing qualification records.

### Expanded qualification and spike repairs

The complete `--extended` run at
[230005Z](../../debug/test_reports/topic_subscriptions_20261124T230005Z/report.json)
passed all seven native stress groups, 18 stable three-report flow phases,
four correlated browser actions and the final timestamped WAN oracles.
Six focused policy/browser regressions also passed. Native router logs have
zero ERROR/FATAL lines; capture drops and browser page errors are zero.
Expected SDK bound-rejection diagnostics in the console are not router errors.

Five review defects were repaired in the spike: replayable local request files,
older equal-policy-revision snapshots hiding degraded state, protected navigation
becoming a permanent user choice, enabled controls after polling failure, and
false confirmation of an unconfirmed snapshot. Request files are consumed and
discarded on owned restart. Applied snapshots have their own monotonic sequence,
retain degraded errors and separate stored user choices from protected interests.
Restart reads actual native defaults before publishing confirmed Auto state.

| Expanded group | Observed result |
| --- | --- |
| Continuous traffic | 1,236 originals offered; six narrowing/widening/team/mission operations; independent producer and source relay |
| WAN during operations | Fresh report identities crossed during every operation: 55/25/19/31/12/36 |
| Settled selection | After the tested 350 ms settling point, every selected reliable ContactReport arrived; no excluded stable-window identity crossed WAN or arrived on LAN |
| Transition drain | Ten reports excluded by the new team policy were observed after local commit; no atomic cutover is claimed |
| Two-second WAN-only bidirectional outage | Queued reliable request repaired and rejected as stale about 363 ms after restore; latest local selection survived |
| Concurrent requests | Four competing same-revision pairs each accepted exactly one request; pending requests lost to newer mission/team revisions |
| Every-family failures | Apply and rollback-refusal cases for Event/State/Periodic; truthful persistent degraded state and explicit test-only reconciliation |
| Capacity | Maximum/overflow checks for three selected topics, 64-byte request ID, 16 Report values and eight inventory entries |
| Typed WAN lifecycle | Real materialize/dispose/revive/unregister/re-register; not original unkeyed Report producer lifecycle |
| Source restart | Early report after LAN matching was not received; fresh inventory crossed after about 6.10 s, then strict selected-delivery checks passed |
| New receiver WAN participant | No volatile inventory replay with refresh disabled; explicit metadata refresh recovered in about 52 ms; old-session request rejected |

Observed continuous application receive latency was approximately 10.84 ms
median and 352.68 ms maximum. These are fixture observations, not radio
performance or production latency/recovery guarantees. The seven groups do not
constitute production integration. The fixture authority remains alive across
Routing Service restarts and explicitly resets the receiver session.

Earlier expanded attempts exposed fixture synchronization, exception handling,
lifecycle-oracle and readiness issues; they remain failed runs, not passing gates.
The final run uses correlated local mission sequences, native lifecycle enums,
an independent relay and packet timestamps to prove traffic did not pause during
filter operations.

### Transient-local WAN State experiment (2026-11-25)

The opt-in `--transient-local` mode changes only the two services' **WAN State
writers and readers** to the external `TopicSubscriptions::state` profile.
Reliability, writer KEEP_LAST(1), reader KEEP_ALL, Event and Periodic remain
unchanged. All LAN endpoints remain volatile, deliberately separating WAN
retention from LAN replay. The default runner and production profiles are not
changed.

[The full transient-local run](../../debug/test_reports/topic_subscriptions_20261125T151448Z/report.json)
passed the new retention group plus all seven existing native stress groups,
18 stable flow phases (54 offered/20 selected), four browser actions and the
timestamped WAN oracles. It offered 1,221 reports during continuous updates.
Native router errors, capture drops and browser page errors were zero.
[The volatile-mode rerun](../../debug/test_reports/topic_subscriptions_20261125T151706Z/report.json)
also passed, as did all six focused host regressions.

| Retention test | Native observation |
| --- | --- |
| New receiver WAN participant, live source writer, refresh disabled | Inventory replayed without an application refresh; about 2.03 s including owned receiver restart/readiness |
| Two cached updates for each of two selected keys | A new WAN reader created with ContactReport selected received exactly the latest complete value for each key |
| Third cached key on another team | Not delivered to that new reader |
| Widen existing reader from empty to ContactReport, without new data | No cached ContactReports replayed during the 1.5 s observation |
| Narrow then widen again; widen after an Auto/Init receiver restart | Also no cached ContactReport replay during the respective 1.5 s observations |
| Restart source writer, then create another selected receiver | Old contacts and inventory did not replay during 1.5 s; explicit source republishing restored metadata |
| Late LAN reader | Still needs a refresh because its LAN leg remains volatile |

This supports transient-local WAN State for new-reader last-value recovery.
It does **not** make an in-place manual subscription change a request for
previously published state, preserve history across source writer restart, or
repair the earlier LAN-match/WAN-readiness gap. The early source-restart report
was again missed; fresh inventory readiness took about 5.09 s in this run.
Periodic refresh still has a role in writer restart and volatile LAN recovery.
Late-join lifecycle/tombstone retention is not qualified by the existing live
dispose/unregister tests.

The first transient-local attempt failed a volatile-only wire oracle that searched
the entire capture: previously excluded ContactReports legitimately appeared
later when a new selected reader joined. The second caught a 2.15 ms receive/
relay drain beyond the offered-sample window. The final oracle checks exclusion
within each policy phase, observes the reliable drain before ending its wire
window, and records subsequent historical traffic separately. Neither failed
attempt is a passing qualification record.

Before production adoption, define acceptable stale-state ages, stable per-state
keys, cache/resource bounds and disposal retention. A new matching reader can
receive old retained state without a new producer write; the UI and application
must not label that replay as freshly generated data. Six-topic integration,
production graph changes and deployment remain separate.

## Observed fragility

### 1. A transaction can leave native filters and model state inconsistent

Fault injection after an actual State-filter mutation allows ordinary rollback
to restore every family. Deliberately refusing State rollback leaves that family
changed while the committed model remains Auto. This is an injected failure,
not a spontaneous Connext failure.

The spike detects the divergence, marks the snapshot unconfirmed, blocks further
changes and displays **native filters diverged** instead of falsely listing the
old selection as applied. Production needs a defined reconciliation/recovery path
and the same honest UI state.

### 2. Metadata freshness needs explicit handling

A newly created receiving LAN reader obtains no previous volatile inventory
sample; publishing a refresh recovers it. The expanded test also recreates the
receiver WAN participant and verifies no replay until refresh. The fixture refreshes every second
for bounded testing, not the proposed production configuration cadence.
The transient-local experiment above changes this result for the WAN leg only.

A matched producer can also be idle; match count is not evidence of recent data.
Stale catalog revisions are rejected, but full inventory session/identity
replacement and fleet cache limits remain unqualified.

### 3. Filter size has a sharp edge

The initial combined expression exceeded the default 256-byte filter-property
limit and prevented WAN reader creation, even though the LAN endpoints matched.
The final spike uses the production-equivalent XML limit and explicit reader
readback. Large inventories/topic sets still need real capacity qualification.

### 4. Local commit is not fleet-wide atomic selection

Family updates, metadata publication and remote writer-filter propagation are
separate steps. Already accepted samples can drain. The spike validates settled sender suppression during continuous traffic and
records ten old-policy deliveries after a team change. A short outage preserves
the latest local selection and repairs a stale request, but does not establish
bounded propagation latency, long-outage recovery or zero transition leakage.

### 5. LAN matching is not end-to-end restart readiness

After a real source restart, an early ContactReport offered after LAN matching
was not delivered. A fresh inventory revision subsequently crossed WAN, and
the strict fresh selected-report check passed. This demonstrates a readiness
gap in the fixture, not a diagnosed production/DDS root cause. Production must
not treat LAN matches alone as proof that remote interests have converged or
that early volatile traffic is safe.

### Historical administration exploration, excluded from the current test

The earlier report at `topic_subscriptions_20261124T205552Z` also reproduced an
incomplete six-element partial administration UPDATE despite a successful return,
an over-broad configuration GET and an XML parsing failure with `<>`. Those are
limitations of that abandoned test interface, not part of receiver-to-writer DDS
filter propagation. The current spike removes the entire administration path and
reads back the actual receiver CFT directly.

## Remaining integration risks

- Adding metadata/control branches requires coordinated IDL/union/bundle changes.
  Inventory publication cannot make an unsupported discovered type routable.
- Production request authorization, current-session authority, replay limits and
  multi-user conflict handling need explicit contracts. Interest is not permission.
- Fixture overrides reset after restart; the Python controller is reset explicitly
  alongside the real Routing Service restart. Persistence is not demonstrated.
- Production delivery accounting still assumes Init/ActiveMission and must be
  updated before introducing User selection, or it can report deliberate exclusion
  as lost traffic.
- Protection of active navigation and restart reset are prototype choices from the
  draft, not newly approved production behavior.
- Manual selection is qualified only in a three-topic IPv4 fixture with three
  prepared report topics. A separate actual ACT six-topic IPv6
  [production-default baseline](../full_union_compression/README.md#production-default-six-topic-baseline-2026-11-24)
  passed, but does not qualify six-topic manual selection. Current ACT subscription
  integration, real missions, original-producer lifecycle, large inventories,
  sustained fleet load, long outages/radio and production graph/bridge remain separate.

**Recommendation:** the selection mechanism is viable. Integrate it through the
existing processor-owned native filter transaction, not the partial XML merge
interface. Treat authoritative readback, session/freshness handling, rollback
reconciliation and delivery accounting as requirements, not optional polish.
