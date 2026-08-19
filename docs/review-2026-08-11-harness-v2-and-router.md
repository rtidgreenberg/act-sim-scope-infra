# Exhaustive review — `harness_v2/` and the C++ router — 2026-08-11

Scope: everything under `harness_v2/` (datamodel, QoS libs, sims, scripts) and `router/`
(src, config, test, test_e2e, CMake). GUI (`gui/mesh_dashboard/`) and the v1 `harness/` are
out of scope except where `run_mesh.sh` launches them. Reviewed at commit `777f8df`,
working tree clean.

Findings are ordered by severity. Each carries the evidence that supports it, so a reader
can re-check the claim rather than take it on trust.

**Resolution status.** H1–H4 are **fixed and committed** on branch
`review/2026-08-11-h1-h4-fixes` (see the per-finding *Status* blocks and the resolution log
at the end). M5–M15 and L1–L10 are **open**. One residual follow-up is carried under H4.
**M15 was added after the initial review**, from the spike run to settle M6.

---

## What was actually run (not just read)

| Check | Command | At review (`777f8df`) | After the H1–H4 fixes |
|---|---|---|---|
| Build | `cmake --build router/build -j4` | clean | clean |
| C++ unit tests | `router/run_tests.sh` | **4/4 pass** (0.09 s) | **4/4 pass** |
| C++ unit tests, *as documented* | `ctest --test-dir router/build --output-on-failure` | **"No tests were found!!!", exit 0** — H1 | command retired |
| Python e2e | `pytest router/test_e2e/ -q` | **2 failed, 25 passed** (179 s) — H2 | **27 passed** (190 s) |
| Post-run hygiene | `ls /dev/shm \| grep -E '^(RTI\|dds)'` | none | none |

No live mesh was launched (per the repo's mesh-debugging guardrails); every router finding
below is from source, the unit suite, or the e2e suite — with one addition made after the
initial review:

| Check | Command | Result |
|---|---|---|
| Write-drop accounting spike (M6, M15) | `spikes/write_drop_accounting/build/write_drop_accounting` | 4 runs, stable; UDPv4-only, domains 181-184, `/dev/shm` clean after |

That spike exists because M6 and M15 are both claims about *what Connext does and does not
count*, which source reading cannot settle. See
[`spikes/write_drop_accounting/README.md`](../spikes/write_drop_accounting/README.md).

---

## Overall assessment

The router is genuinely well-built. The single-writer-strand model, the D5
fingerprint/publish-if-changed predicate, the D23 generation stamping with exact-match
staleness discard, the D32 detach-before-close barrier, and the create-and-observe
retirement of discovery-gated creation are all coherent and consistently applied. The
`try_apply_qos` skeleton, `WanStatsPoll.hpp`, and `RouteEntityFactory`'s lane split are
good de-duplications. The commentary explains *why*, not *what*, and repeatedly records
decisions that were tried and reverted — that is unusually valuable and worth preserving.

The problems cluster in three places, and they are all the same shape: **verification and
supporting tooling have drifted behind the code.** The documented test command runs
nothing; the e2e suite is red against the production config; two large test files are
never collected; several config keys and IDL fields are inert; and the simulator layer has
not received the same care as the router. None of this is architectural — it is all
recoverable in a focused pass.

---

# HIGH

## H1 — The documented unit-test command runs zero tests and exits 0

`.github/copilot-instructions.md:154`, `router/README.md:47`, and the header of
`router/CMakeLists.txt` all instruct:

```
ctest --test-dir router/build --output-on-failure
```

`--test-dir` was added in CMake 3.20. This VM has **CMake/ctest 3.16.3**, which ignores the
flag, scans the current directory, finds no `CTestTestfile.cmake`, prints
`No tests were found!!!` — and **exits 0**:

```
$ ctest --test-dir router/build > /dev/null 2>&1; echo $?
0
```

Run correctly, the tests are fine (`cd router/build && ctest` → 4/4). So this is not a test
failure; it is a **silent verification hole**. Any script, hook, or human following the
documented command gets a green result having executed nothing. Every "ctest 4/4" claim in
`docs/cpp_router/design-decisions.md` and `implementation-plan.md` must have come from a
different invocation than the one the docs prescribe.

**Fix.** Change the documented command to `cd router/build && ctest --output-on-failure`,
or add `router/run_tests.sh` that does the `cd` and additionally fails when the executed
test count is 0. A zero-test run must never be a pass.

> **Status: FIXED.**
> - Added `router/run_tests.sh` — cds into the build tree (works on every ctest version),
>   counts tests with `ctest -N` first, and **exits 1 if the count is 0** rather than
>   reporting success on an empty run. Extra args pass through (`run_tests.sh -R controller`).
> - Repointed all three documented invocations at it: `.github/copilot-instructions.md`,
>   `router/README.md`, `router/CMakeLists.txt` header. Each now states explicitly why
>   `ctest --test-dir` must not be used here.
> - Verified: `./router/run_tests.sh` → `100% tests passed, 0 tests failed out of 4`.
> - Fixed in passing (was **L10**): `router/README.md` no longer claims "9 C++ binaries"
>   (it is 4, and names the 5 retired targets whose stale binaries linger in the build
>   tree) and no longer says "Phase 6 … is next".

## H2 — The Python e2e suite is red; the production-config test is one of the two failures

```
FAILED router/test_e2e/test_control_command_route.py::test_command_reaches_only_addressed_platform
FAILED router/test_e2e/test_control_platform_full.py::test_full_control_platform_config
2 failed, 25 passed in 179.70s
```

Both fail with:

```
rti.connextdds.InvalidArgumentError: Failed to loan complex member
ERROR DDS_DynamicData2StructPlugin_getMemberInfo: Cannot find a member (name = "msg", id = 0) in type control_command
```

Commit `5100c81` ("Realistic IDL types") flattened `control_command`, removing the
`base_type msg` wrapper (`harness_v2/datamodel/ActTypes.idl:23-31`). The router configs were
updated with it — `router/config/e2e_control_command.yaml:72` and
`router/config/control-platform.yaml:127` both now filter on `destination = %0`. The tests
were not:

- `router/test_e2e/test_control_command_route.py:42-48` — `msg.destination`, `msg.source`
- `router/test_e2e/test_control_platform_full.py:141,143,163` — same

(Other tests still using `msg.*` — `test_create_and_observe.py`, `test_auto_qos.py`,
`test_wan_partition.py`, `test_platform_events.py` — pass legitimately: they use
`router/config/example_types.xml`, which still has the nested `MsgBase msg` member.)

Consequence beyond the red bar: `test_control_platform_full.py` is the **only** test that
loads the real production `control-platform.yaml` through a router pair. While it fails at
the probe, the full-config regression gate does not exist.

**Fix.** Map to the flattened members: `msg.destination` → `destination`, `msg.source` →
`source`. Note the flattened `control_command` has no `seq` member — use `command_id` (or
`timestamp`) wherever the old `msg.seq` was the correlation key.

> **Status: FIXED.**
> - `test_control_command_route.py`: writes and the `classify` lambda now use
>   `destination` / `source`; docstring corrected (it described the CFT as keying on
>   `msg.destination`, which no config does any more).
> - `test_control_platform_full.py`: same for `control_command`, **and** for
>   `control_command_ack` — line 163 still passed `"msg.source"` behind a comment claiming
>   "control_command_ack still wraps base_type msg". It does not; that was a second stale
>   accessor the earlier failure masked, since the test died at the `control_command` write
>   before reaching it.
> - Neither test needed a `seq` substitute — both correlate on `source`/`destination`.
> - Both files gained a short field-naming note so the next reader knows the types are flat.

## H3 — `test_control_platform_full.py` has no domain isolation and can collide with a live mesh

Every other e2e test renders its config through `render_config()`, which substitutes
`__DOMAIN_CONTROL_LAN__` / `__DOMAIN_WAN__` / `__DOMAIN_PLATFORM_LAN__` from the
`unique_domains` fixture (`router/test_e2e/conftest.py:94-104,74-82`).

`test_control_platform_full.py:43` sets `CONFIG = "control-platform.yaml"`, and that file
has **literal** domains — `20`, `200`, `30` (`router/config/control-platform.yaml:59,62,83,93`).
`render_config` finds no placeholders and substitutes nothing, so this test always runs on
20/200/30.

Those are exactly the domains `harness_v2/scripts/run_mesh.sh` uses (`--domain 20` for the
bridge, `CONTROL_WAN_PEER1` on 200, platform LANs from 30). On a VM that runs concurrent
sessions, this test can discover — and be discovered by — a mesh it does not own. It can
both produce false results and perturb someone else's run.

**Fix.** Add domain placeholders to `control-platform.yaml` with a `sed`-free default, or
have the test render a placeholder copy the way `test_team_partition.py:62` already does
with its own local `_render` helper.

> **Status: FIXED** — and the investigation found the problem was **wider than this one
> test**.
>
> **The whole allocator was inside the live band.** `unique_domains` allocated
> `40 + n*3`, and the live platform-LAN band is **30–99** (`start_platform_sim.sh`'s ID
> range; `run_mesh.sh` monitors `20,200,30..N`). So it was not only the production-config
> test that could meet a live mesh — every e2e test past the first few handed itself a
> live platform's domain id. Per the request, the suite now allocates from a band the
> live system never touches:
> ```python
> E2E_DOMAIN_BASE = 101    # live band is 20, 30..99, 200
> E2E_DOMAIN_MAX  = 199    # hard ceiling: see below
> base = E2E_DOMAIN_BASE + next(_domain_counter) * 3
> ```
>
> **The first attempt at this used base 1000, and the e2e suite caught it.** That is worth
> recording, because it also corrects a wrong guardrail in the repo. The run failed with:
> ```
> Domain=1023 ... RTIOsapiSocket_bindWithIP: OS bind() failure, error 0XD: Permission denied
> NDDS_Transport_UDPv4_Socket_bind_with_ip: FAILED TO BIND | Port 1006
> [wire-frugal] WAN domain 1046 ports [268900,269149]: topics=[]
> ```
> RTPS maps a domain to a port as `7400 + 250*D`. That exceeds 65535 at **D = 233** and
> then **wraps mod 65536**: `7400 + 250*1023 = 263150`, and `263150 mod 65536 = 1006` — a
> privileged port, exactly the number in the error. Domain 1046's range `[268900, 269149]`
> is likewise nonsense, so the wire-frugality test matched no traffic and failed too.
> Three tests failed this way; the H2 fixes and the production-config rendering were
> unaffected and passed.
>
> `copilot-instructions.md` said the ceiling was "~5900–6000". **That is wrong, and it is
> what I followed.** 5900 is merely one instance where the wrap happened to land low; the
> actual limit is 232, and above it the failure is not a threshold you can stay under but a
> wrap that can land anywhere — including on a port that binds fine but talks to nothing.
> The guardrail has been rewritten with the arithmetic, the measured values, and the live
> vs. free domain windows, so the next reader is not misled the same way.
>
> `E2E_DOMAIN_MAX = 199` is asserted per allocation, and the message explicitly says *do
> not just raise the ceiling* — it points at the 201–232 window or PARTITION-based
> isolation (D50) instead. The band holds 33 triples against 27 collected tests.
>
> **The production config is now rendered, not passed through.** `control-platform.yaml`
> stays runnable exactly as authored (no placeholders added — `router/README.md` makes a
> point of that), so `render_config()` rewrites its three literal domains onto the test's
> triple instead:
> ```python
> PRODUCTION_DOMAINS = {"control_lan": 20, "wan": 200, "platform_lan": 30}
> ```
> matched as `(domain:\s*)20\b` and applied longest-first, so the `20 → N` mapping cannot
> corrupt `200`. The config is therefore exercised verbatim in every respect except the
> domain ids.
>
> **A pass-through can no longer happen silently.** `render_config()` now *requires* one
> of the two shapes to apply: a config with neither `__DOMAIN_*__` placeholders nor the
> production literals raises, rather than being copied unchanged and quietly run on
> whatever it named. It also asserts no live domain survives rendering.
>
> **Second affected test found.** `test_detail_status_toggle.py` also loads
> `control-platform.yaml` and also probed the literal `20`/`30`. Same fix applied — both
> tests now read `unique_domains`.
>
> Verified: rendering `control-platform.yaml` with `{1000,1001,1002}` yields
> `domain: 1000 / 1001 / 1002 / 1001`, and the placeholder fixtures are unaffected.
> (The remaining `domain: 100` in the render is the dead `control:` block — **M11**.)

## H4 — The presence roster is never pruned

`PresenceMonitor` inserts into `roster_` and `handle_to_name_` and never erases from either
— confirmed by grep: there is no `roster_.erase` / `handle_to_name_.erase` anywhere in
`router/src/core/PresenceMonitor.cxx`. A peer that goes DEAD stays DEAD in the map for the
process lifetime.

Three consequences, in increasing order of importance:

1. **Memory** grows monotonically with every distinct `"<node>/<router>"` name ever seen.
   Bounded in practice, but unbounded in principle.
2. **The dashboard never forgets.** A decommissioned or renamed router remains a DEAD peer
   in every `ActRouterMeshStatus` sample and in every heartbeat's `peers_seen`, forever.
   There is no operator action that removes it short of restarting the router. This is the
   consequence that bites at demo scale.
3. **Cap crowd-out.** Both `peers_seen` and `mesh.peers` are unbounded IDL sequences (cap
   100) and the code truncates in roster name order, keeping name-order-lowest
   (`PresenceMonitor.cxx:168-176,410-418`). Accumulated DEAD entries with low-sorting names
   can therefore displace live peers once the roster exceeds 100 names. The truncation is
   handled gracefully and warned about — but *what* gets kept is the wrong set.

**Fix.** Age out DEAD entries after a grace period (a multiple of the liveliness lease).
Rejoin already works correctly — the roster is name-keyed, so a returning peer just
re-enters ALIVE.

> **Status: FIXED.**
> - New `kDeadPeerRetentionMs = 60000` beside the other pinned presence numbers in
>   `PresenceMonitor.hpp`, documented with the reasoning: long enough that an operator sees
>   the DEAD transition on the dashboard, short enough that a decommissioned router is gone
>   within a minute.
> - New `PresenceMonitor::prune_dead_locked()` erases DEAD peers whose `last_seen` is older
>   than that, **and** every `handle_to_name_` entry resolving to the pruned name (a peer
>   can accumulate several handles across restarts, so it is a sweep by value, not one
>   erase). It needs no new timestamp field — `last_seen` already marks when the peer went
>   quiet, so the clock starts when it actually died rather than when we noticed.
> - Called from `publish_mesh()` inside the existing `roster_mutex_` block, which bumps
>   `mesh_revision_` when anything was pruned — forgetting a peer *is* a roster change.
>   Deliberately **not** placed inside `build_mesh_locked()`: D99 requires that function to
>   stay a pure read, because MeshTick calls it every 0.5 s whether or not anything changed.
>   Putting the mutation there would have re-created exactly the bug D99 fixed.
> - The next heartbeat reads the same pruned roster, so `peers_seen` clears too — no second
>   prune site needed.
> - Logs `presence_peer_forgotten` with the dead-for duration, so the removal is
>   observable rather than silent.
> - Rejoin is unchanged and still correct: the roster is name-keyed, so a returning peer
>   re-enters ALIVE via the normal path.
>
> **Residual — this fix is not covered by a test.** Be clear about what the green suite
> does and does not prove here. `test_presence_roster.py::test_presence_roster_mesh_dead_and_rejoin`
> passes, so the prune did not break the DEAD→rejoin path — but that test's rejoin happens
> far inside the 60 s retention window, so `prune_dead_locked()` never actually removes
> anything during it. Nothing in the suite waits 60 s, so the erase itself is compiled and
> reached but never asserted.
>
> The cheap follow-up is to make the retention injectable — a `PresenceMonitor` constructor
> parameter defaulting to `kDeadPeerRetentionMs`, plumbed from an optional
> `router.dead_peer_retention_ms` config key — so an e2e test can set it to ~3 s, kill a
> peer, and assert the peer leaves `ActRouterMeshStatus.peers` and the next heartbeat's
> `peers_seen`. That is a small change but it is a behaviour change to the config surface,
> so it is left as a follow-up rather than smuggled into this fix.

---

# MEDIUM

## M5 — `RefreshCounters` republishes a stale `caused_by_command_id`

`RouterController::process_one` handles the four tick kinds by early-return
(`RouterController.cxx:148-170`) — *before* `current_cause_.clear()` at line 180.
`apply_refresh_counters()` then calls `republish_status()` (line 766), and
`build_snapshot()` copies `current_cause_` into the top-level status field
(`RouterController.cxx:1122`).

So after any accepted command, **every subsequent 1 Hz counter republish re-asserts that
command as the cause of a status sample whose `state_revision` did not change.** A consumer
correlating "which command produced this status" gets a stale attribution once per second,
indefinitely. The per-route `caused_by_command_id` is stamped only on real change and stays
correct; it is the top-level field that lies.

**Fix.** Clear `current_cause_` in the tick early-return path, or pass the cause explicitly
into `build_snapshot()` so a revision-less republish is structurally unable to carry one.

## M6 — `pump()` drops samples silently

`RouteTopicRuntime::pump()` (`router/src/core/RouteRuntime.hpp:217-230`) catches every
per-sample `write()` exception and continues with an empty handler. The intent is right —
one bad sample must not kill the loop — but there is no counter, no log, and no status
field. A route whose writer rejects *every* sample is indistinguishable from an idle one:
`topic_state` stays `TOPIC_FORWARDING`, `input_matched`/`output_matched` stay non-zero, and
`samples_forwarded` simply never advances.

**Fix.** Add a `dropped_` atomic beside `count_`, a rate-limited `Log::warn` on the first
drop and on each order-of-magnitude, and a `samples_dropped` field on
`RouterRouteTopicStatus`. Cheap, and it turns a silent failure into a visible one.

> **Evidence: `spikes/write_drop_accounting/` (2026-08-19, Connext 7.7.0).** The obvious
> objection to the fix is that it duplicates a counter the middleware already keeps, so
> this was measured before writing any code. It does not — **no native writer-side counter
> observes a thrown `write()` at all.** Forcing 25 `dds::core::TimeoutError`s out of 40
> writes (RELIABLE + KEEP_ALL, stalled reader) left `pushed_sample_count` at exactly 15 —
> the count of writes that *returned* — with writer-global `rejected_sample_count`,
> per-subscription `rejected_sample_count` and `replaced_unacknowledged_sample_count` all
> at **0**. The sample never enters the writer's cache, so there is nothing for Connext to
> account. A router-side counter of our own `write()` outcomes is the only possible source.
> Full results, including the rejected alternatives below, in
> [`spikes/write_drop_accounting/README.md`](../spikes/write_drop_accounting/README.md).
>
> **Two refinements to the fix**, both from the same spike:
> - Classify the error. All 25 failures were `dds::core::TimeoutError` with zero other
>   exception types, so `max_blocking_time` exhaustion is distinguishable at the catch site.
>   Carry a `last_write_error` (or error-class) field rather than a bare count. Give it its
>   own field — `last_error` belongs to the `apply_entity_error` lifecycle and is cleared on
>   re-arm (`RouterController.cxx:409`).
> - Pull it on the existing D63 counter tick. `apply_refresh_counters` already reads
>   `forwarded_count()` synchronously on the controller strand and republishes without
>   bumping `state_revision`; adding `dropped_count()` beside it needs no new
>   `ControllerEvent`, no cross-strand mutation, and no D23 stale-stamp gating. Do **not**
>   add a `topic_state` value for this — a topic hitting write exceptions is still armed and
>   pumping and must keep reading as `TOPIC_FORWARDING` everywhere, including the
>   `!= TOPIC_FORWARDING` guard at `RouterController.cxx:753`. If a visible degraded flag is
>   wanted, derive it at publish time from "the dropped counter moved since the previous
>   tick" — self-clearing, and it cannot get stuck the way a latch cleared only by a
>   subsequent successful write does when the input goes idle.
>
> **Three native counters were considered and rejected** — recorded so they are not
> re-proposed:
> - `replaced_unacknowledged_sample_count` **is not a loss counter.** Two scenarios with
>   identical writer QoS, differing only in whether the reader was drained, both read
>   **35** — one having genuinely lost 35 samples, the other having lost **none**. It counts
>   samples overwritten before the heartbeat-driven ACK returned, which is routine on a
>   healthy KEEP_LAST reliable writer. Wiring it to route health would light up permanently
>   on every healthy route.
> - `unacknowledged_sample_count` is a **saturating gauge**. It read exactly 10 at the first
>   throw — precisely the writer's `max_samples` — and peaked there, so it reaches its
>   ceiling simultaneously with the first failure rather than ahead of it, and cannot count
>   what was lost. It stays legitimate for what `ControllerJournalPublisher` uses it for.
> - `full_reliable_writer_cache` counts cache-full *events*, not samples (2 events for 25
>   lost samples), and reads 0 on the KEEP_LAST path where 35 were lost. Supporting signal
>   at best.
>
> **Scope note.** `samples_dropped` captures the forwarding side only. In the same scenario
> a further 10 samples reached the reader and were rejected *there* — so 35 of 40 were lost
> through two independent mechanisms, and the second is visible only on the receiving
> router's reader (`samples_rejected_local`). See **M15** for the writer-side field that was
> supposed to cover the remote half and does not.

### M6 — decided: dropping samples does NOT change route state or `state_revision`

Recorded because it is the one part of this fix that is a decision rather than an edit, and
because the obvious-looking alternative is wrong for a non-obvious reason.

A route dropping every sample keeps `topic_state == TOPIC_FORWARDING` and route state
`ROUTE_ENABLED` (`derive_operational` returns it as soon as `any_forwarding`, and never
consults counters — `RouterState.cxx:100-131`). The dropped counter rides the D63
refresh-counters republish: fresh numbers, **same** `state_revision`. `route_fingerprint`
already excludes sample counters and says so at `RouterState.cxx:158-160`.

The fork, if a `write_degraded` flag is added: `qos_warning` **is** in the fingerprint
(`RouterState.cxx:155`), so there is a precedent for a runtime-observed health signal being
D5-visible and revision-bumping. Resist it here. The flag would be derived from counters
sampled at 1 Hz, so including it lets a flapping writer bump `state_revision` once per
second — which drains the meaning of revision ("externally visible state changed") and makes
**M5** materially worse, since every revision-less republish already re-asserts a stale
`caused_by_command_id`. Turning counter churn into revision churn multiplies that bug. Keep
`write_degraded` out of the fingerprint; if it ever goes in, it needs hysteresis first, and
that is a larger change than M6 justifies.

Do **not** repurpose `ROUTE_DEGRADED`. It is reachable today from exactly one condition — a
topic in `TOPIC_TEARING_DOWN` with nothing forwarding (`RouterState.cxx:125`) — and both
`test_controller_phase1.cxx:831` and `test_qos_alias_route.py:90` pin that meaning, while
`gui/mesh_dashboard/static/mesh_graph.js:487` already renders it as unhealthy. Using it for
write drops would report a *forwarding* route as degraded and collide with the teardown
meaning.

### M6 — implementation order (each step mirrors something that already exists)

1. **Runtime** — `dropped_` atomic beside `count_` in `RouteRuntime.hpp:217-230`, incremented
   in `pump()`'s catch, plus a `last_write_error_` classification. Expose `dropped()` on
   `RouteTopicRuntimeBase` next to `forwarded()`.
2. **Factory seam** — `dropped_count(route, topic)` as a sibling of `forwarded_count` on
   `Interfaces.hpp:66` and `RouteEntityFactory.hpp:247`.
3. **Controller** — pull it in `apply_refresh_counters` (`RouterController.cxx:741`) beside
   `forwarded_count`, under the same `TOPIC_FORWARDING` guard, folding into `any_changed`.
4. **IDL** — append `samples_dropped` + `last_write_error` to `RouterRouteTopicStatus`, and
   roll `samples_dropped` up on `RouterRouteStatus` like `samples_forwarded` (D11 aggregate).
   Regenerate `harness_v2/datamodel/gen/ActTypes.xml` per the data-model rule. The IDL
   carries no extensibility annotations, so every type is default-appendable and appending at
   the end is assignability-safe; in practice all routers build from this one IDL anyway.
5. **Log** — rate-limited `Log::warn` on the first drop and each order of magnitude.

**Test.** Extend `test_refresh_counters_republish_no_bump`
(`router/test/test_controller_phase1.cxx:1068`): add a `dropped_counts` map to
`FakeEntityFactory` beside `forwarded_counts` and assert the three properties it already
asserts for forwarded — republish without a revision bump, silence when unchanged, cleared on
teardown. No DDS entities.

**Known gap.** That covers the controller half only. `pump()` actually incrementing on a
throw is not reachable from the current fakes, since `RouteTopicRuntime` is templated on a
real DDS writer. Either accept `spikes/write_drop_accounting/` as the evidence for that half,
or add an e2e whose route output writer is RELIABLE + KEEP_ALL with tight resource limits
against an undrained reader — scenario A of the spike, expressed as a `router/config/e2e_*.yaml`.
Recommendation: ship without it, treat the e2e as optional follow-up.

**Do this in the same pass as M15** — same subsystem, same evidence, and one IDL
regeneration instead of two.

## M7 — An error on a FORWARDING topic leaves live entities running, and a later re-arm corrupts the dispatcher map

`apply_entity_error` (`RouterController.cxx:891-923`) sets `TOPIC_ERROR`, zeroes the
generation, and clears the entity facts — but never calls
`factory_->teardown_topic_entities`. Its own comment (line 911) explicitly contemplates
"a runtime fault while FORWARDING". If that happens:

1. The runtime **keeps forwarding** while status reports `ROUTE_ERROR` / `TOPIC_ERROR`.
2. A subsequent `ENABLE_ROUTE` re-arm moves the topic to IDLE and rebuilds
   (`handle_enable`, lines 404-416), calling `create_topic_entities` for a `(route, topic)`
   key **still present** in `AsyncWaitSetDispatcher::runtimes_`.
3. `attach()` does `runtimes_[key(route, topic)] = std::move(runtime)`
   (`AsyncWaitSetDispatcher.cxx:32`), which destroys the old runtime **without** detaching
   its AsyncWaitSet conditions — violating the D32 barrier the whole teardown discipline is
   built on — and **without** `unregister_source`, leaving a dangling `IWanStatsSource*` in
   `LinkStatsCollector::sources_` that the next `LinkStatsTick` will poll.

Today this is **latent, not live**: the only producer of `RouteEntityError` is
`RouteEntityFactory::create_topic_entities`'s catch block, which cannot fire after the
`dispatcher_.attach()` on line 191. But the controller is written as if the FORWARDING case
is reachable, and adding any runtime-fault reporting would make it so.

**Fix.** Tear down entities on a per-topic error before making the topic sticky-ERROR; and
make `attach()` defensive — detach-and-close (or hard-fail) on a duplicate key rather than
silently overwriting.

## M8 — `RouterHealth` now carries every route's full spec, at 1 Hz, on the WAN

`RouterHealth` gained `sequence<RouterRouteStatus> routes` (`ActTypes.idl:366`, commit
`60295ae`), which `apply_presence_tick` fills from `build_route_statuses()`
(`RouterController.cxx:803-806`). Each `RouterRouteStatus` embeds the complete `desired`
`RouterRouteSpec` — both endpoint specs with participant, two partitions, two QoS aliases,
the filter expression and its parameter sequence, plus the topic list and every
`RouterRouteTopicStatus` with its four QoS-summary strings. On top of that the same sample
carries `peers_seen`.

It is written RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1) at `kHeartbeatPeriodMs = 1000`
(`PresenceMonitor.cxx:19-28`, `PresenceMonitor.hpp:108`) on the **WAN** participant. On the
production 9-route `control-platform.yaml` this is, by inspection, the largest recurring WAN
payload in the system — in an architecture whose stated tenet is WAN frugality.

The existing guard cannot catch this. `test_link_stats_wire_frugal`
(`router/test_e2e/test_link_stats.py:265`) asserts `bytes_per_s < 50_000` against
`e2e_link_stats.yaml`, which declares **2** routes with no filters. Nine production routes
with filter expressions would have to grow the heartbeat ~25× before that ceiling trips.

**This is flagged, not measured.** I did not launch a mesh to size it. The repo already has
the recipe: `dumpcap -i lo -w out.pcap` during a `control-platform.yaml` router pair, then
`tshark -r out.pcap -Y rtps -T fields -e frame.len` filtered to the WAN port range, exactly
as `test_link_stats_wire_frugal` does.

**Fix options**, in increasing order of effort: strip `desired` from the heartbeat copy
(the LAN `ActRouterStatus` already carries the full spec, so nothing is lost); or publish
route statuses on their own slower/on-change cadence separate from the 1 Hz liveliness
beat; or bound `routes` in the IDL and truncate the way `peers_seen` already does. Whichever
is chosen, re-point the frugality test at a route-count representative of production.

## M9 — 1,136 lines of test code in the pytest directory that pytest never collects

```
$ pytest router/test_e2e/test_status_resolution_e2e.py router/test_e2e/test_team_assignment_e2e.py --collect-only -q
no tests collected
```

`test_status_resolution_e2e.py` (590 lines) and `test_team_assignment_e2e.py` (546 lines)
are named `test_*.py` and sit in the pytest suite directory, but define no test functions —
they are standalone Playwright scripts with `main()` entry points. `pytest router/test_e2e/`
— the command in `copilot-instructions.md` — silently skips both. A reader counting 27
collected tests has no signal that two of the most end-to-end checks in the repo did not
run.

**Fix.** Move them to `router/test_e2e/manual/` (or `harness_v2/scripts/`) without the
`test_` prefix, or wrap each in a real pytest test behind a marker
(`@pytest.mark.playwright`) that is deselected by default and explicitly runnable.

## M10 — `run_mesh.sh up` deletes `--workdir` *before* validating it

```bash
rm -rf "$WORKDIR"                                   # line 67
mkdir -p "$WORKDIR"                                 # line 68
if ! (mkfifo "$WORKDIR/.probe" ...); then           # lines 70-73
    echo "Error: ... refusing to use a vboxsf/non-local path"; exit 1
fi
```

The mkfifo probe is the repo's headline filesystem-safety guard — and it runs on a
directory that has already been recursively deleted and recreated. A mistyped `--workdir`
pointing at a real directory destroys it, and only then reports that the path is unusable.
The only check before the `rm -rf` is `[[ -z "$WORKDIR" ]]`.

**Fix.** Probe before deleting: `mkdir -p`, mkfifo-probe, and only then `rm -rf` the
contents (not the directory). Consider also refusing a `$WORKDIR` that exists, is
non-empty, and contains no `pids.txt` from a prior `up`.

## M11 — Unknown config keys are silently ignored, and the production config ships a dead block

`RouteConfigParser` reads a fixed key set and ignores everything else. `control-platform.yaml`
carries an entire block the parser never reads:

```yaml
control:                                    # lines 34-39 — read by nothing
  domain: 100
  command_topic: ActRouterCommand
  status_topic: ActRouterStatus
  target_node: Platform_30
  target_router: platform-30-control-platform
```

plus `router.config_set` and `router.default_forwarding_mode` (lines 23-24), which only the
separate Phase-0 `RouterIdentity.cxx` reader consumes — not `RouteConfigParser`. These read
as live configuration. `domain: 100` in particular looks authoritative and is inert.

This is precisely the failure mode D79/D80 hard-error on for *retired* keys (`router.id`,
`inherit_participant` — `RouteConfigParser.cxx:160-165,245-251`), but there is no
unknown-key check, so the same class of bug survives under a different name.

**Fix.** Reject unknown keys under `node:`, `router:`, `participants.<name>:`, and each
route, with the same fail-fast posture as the retired-key checks. Delete the `control:`
block.

## M12 — `domain_traffic_monitor.py` under-counts silently, over-logs, and contradicts the repo's own tshark rule

Three separate issues in `harness_v2/scripts/domain_traffic_monitor.py`:

1. **Silent under-count.** `build_port_filter(domain_ids, max_participants=8)` (line 134)
   enumerates only 8 participant slots per domain. `run_mesh.sh` puts the control router,
   the bridge, and the monitor's own targets on domain 20 and every platform's `platform_wan`
   on domain 200 — as `--platforms` grows past a handful, traffic on participant indices ≥ 8
   is dropped from the BPF filter with no warning. The dashboard shows a number that is
   confidently wrong. `run_mesh.sh:197-200` never passes `--max-participants`.
2. **Log volume.** `run_mesh.sh:199` launches it with `--interval 0.1`, and the loop prints
   one line per monitored domain per tick (lines 291-293). With 5 platforms that is 7
   domains × 10 Hz ≈ 70 lines/s into `traffic_monitor.log`.
3. **Guardrail contradiction.** `copilot-instructions.md` states: "**Never** run
   `tshark`/`dumpcap` without `-r <file>` or `-w <file>`/`-c <count>`/`-a duration:N`". This
   script runs an unbounded live `tshark` (line 154). It is defensible here — `lo` only, and
   behind a narrow BPF filter — but the rule and the code disagree, and one of them should
   move. Prefer amending the rule with an explicit exception for filtered `lo` capture.

**Fix.** Derive `max_participants` from the mesh size in `run_mesh.sh` (or default it much
higher — the filter is just a port list), gate the per-tick print behind a `--verbose` flag
or aggregate to ~1 Hz, and reconcile the guardrail text.

## M13 — `platform_mesh_control.py` never applies the initial team or status mode

`harness_v2/scripts/platform_mesh_control.py:130-131` initialises
`tracked_team = ""` and `tracked_mode = STATUS_INIT`, and both handlers skip when the
incoming value equals the tracked one (lines 231-234, 258-259).

So an explicit `STATUS_INIT` — or an empty team assignment — arriving as the *first* sample
is a no-op, and the routes are left at whatever the config's `enabled:` said. If a platform
router restarts while C2's retained mode is INIT, nothing re-asserts INIT; the platform
silently sits at its config default. The same holds for a platform whose first
`TeamAssignment` is the empty team.

**Fix.** Initialise both trackers to `None` so the first received sample always applies.

## M14 — `platform_sim.py` publishes power status at half rate

```python
        self.platform_power_status_writer.write(sample)
        print("Writing to PlatformPowerStatus topic")
        await asyncio.sleep(1)
        await asyncio.sleep(1)      # <- harness_v2/sims/platform_sim.py:345
```

Every sibling writer coroutine sleeps once. `write_power_status` sleeps twice, so
`PlatformPowerStatus` publishes at 0.5 Hz while the rest of the sim runs at 1 Hz. Nothing
in the file suggests this is deliberate. Any rate-based analysis of that topic — including
the traffic monitor's data/discovery split — is skewed by it. This is the kind of defect
the duplication in L1 makes easy to introduce and hard to see.

## M15 — `samples_rejected_remote` is always zero and does not measure remote rejection

Found while measuring M6 (`spikes/write_drop_accounting/`, 2026-08-19, Connext 7.7.0).

`poll_writer_wan_stats` reads `st.rejected_sample_count().total()` off the
**per-matched-subscription** `DataWriterProtocolStatus`
(`router/src/core/WanStatsPoll.hpp:73-85`) into `WriterLinkDeltas::samples_rejected_remote`,
published as `samples_rejected_remote` (`harness_v2/datamodel/ActTypes.idl:460`). The name,
the field, and the surrounding comment all promise "samples the peer reader rejected".

It does not measure that. In a scenario where the remote reader **demonstrably rejected 35
samples** — confirmed on that reader's own
`DataReaderProtocolStatus::rejected_sample_count` — both the per-subscription **and** the
writer-global `rejected_sample_count` read **0**. Across all four scenarios in the spike,
including two with heavy confirmed loss, every writer-side rejection counter stayed at 0.

Two things follow:

1. This is **not** merely the `/* Only available for local DW status */` annotation on
   `$NDDSHOME/include/ndds/dds_c/dds_c_publication.h:460` — which would have predicted the
   per-subscription variant reading 0 while the writer-global one moved. The writer-global
   variant does not move either. **Switching getters will not fix it**; nothing on the
   writer side observes peer-side rejection.
2. The working counterpart already exists and is already collected: the reader-side
   `samples_rejected_local` cleanly separated the lossy scenario (35) from the lossless ones
   (0). But it lives on the *receiving* router's reader, so remote rejection is observable
   in the mesh — just never from the sending side.

**Fix.** Delete `samples_rejected_remote` from `WriterTotals`, `WriterLinkDeltas`, the poll,
and the IDL, and note in `WanStatsPoll.hpp` that writer-side rejection accounting is not
available per peer so readers of link stats look to the peer's `samples_rejected_local`
instead. Keeping a permanently-zero field named after a real failure mode is worse than not
having it: it reads as evidence of no rejection.

Worth checking the same way before trusting them: the other writer-side fields
(`nack_frags_received`, `heartbeats_sent`) came from the same per-matched-endpoint getter,
and only `pushed_sample_count` was confirmed live here.

---

# LOW — clarity, simplification, hygiene

## L1 — `platform_sim.py` / `control_sim.py`: nine copies of one loop

`harness_v2/sims/platform_sim.py` is 461 lines, of which roughly 200 are nine near-identical
writer coroutines (`write_cmd_ack`, `write_primary_status`, `write_detail_status`,
`write_mission_status`, `write_waypoint_status`, `write_debug_status`,
`write_thruster_status`, `write_power_status`, `write_data`, `write_contact_report`). They
differ only in the type, the writer, and the per-iteration field fill. `control_sim.py`
repeats the pattern at smaller scale. A table of `(type, topic, writer, period, fill_fn)`
driven by one generic `async def publish_loop(...)` would cut the file by well over half and
structurally prevent M14.

Alongside that, in both sims:

- `args` is a module-level global read from inside instance methods
  (`platform_sim.py:187` and throughout) rather than stored as `self.args`.
- `threading`, `uuid`, and `rti.types.builtin.String` are imported and unused in both files.
- `import math` appears inside five separate methods instead of at module scope.
- `print()` is called without `flush=True` on the hot path. Under `run_mesh.sh`'s `nohup ...
  > platform30_sim.log`, stdout is block-buffered, so the sim logs lag reality by up to a
  4 KB block. `platform_mesh_control.py` gets this right (`flush=True` everywhere); the sims
  should match.
- Neither sim closes its participant on exit — `KeyboardInterrupt` is caught and swallowed
  with `pass`, and there is no SIGTERM handler, so `run_mesh.sh down`'s SIGTERM gets no
  graceful DDS teardown.
- `--source` and `--qos_profile` are declared `type=str` with `default=0` (an int).

## L2 — Duplicate and blank route/topic names are silently accepted

`RouteConfigParser` validates that a route's participants exist and that QoS aliases are
declared, but never that names are unique or non-empty.

- Two routes with the same `name:` collapse: `state_.routes[route.desired.route_name] = route`
  (`RouterController.cxx:96`) is a `std::map` keyed by name, so the second silently replaces
  the first — while `router_main`'s preflight loops (lines 316-330, 342-353) still iterate
  `cfg.routes` and validate both, hiding the loss.
- Two topics with the same name inside one route diverge: `route.topics` (a map) holds one,
  `route.desired.topics` (a vector) holds two, so `build_route_statuses`
  (`RouterController.cxx:1081-1109`) emits a duplicate `topic_status` entry for the same
  topic.
- A route or topic with no `name:` becomes the `""` key; two of them collide.

**Fix.** Validate non-empty and unique route names, and unique topic names within a route,
at parse time.

## L3 — Dead and unwired declarations

| Item | Status |
|---|---|
| `RouterRouteSpec.forwarding_mode` | Parsed (`RouteConfigParser.cxx:367`), published in status, read by nothing — every route is the DynamicData lane |
| `RouterRouteSpec.mirror_instance_state`, `.key_fields` | Declared in `ActTypes.idl:257-258`, never set or read |
| `RouterCommand.payload_json`, `ControllerJournalRecord.payload_json` | Never set or read |
| `router/src/core/EntityFactory.hpp` | Not in any CMake target; grep finds zero references outside the file itself |
| `TypeResolver::register_type` / `is_constructible` | Only callers are inside the dead `EntityFactory.hpp` |
| `TypeResolver::has_dynamic_type` / `get_dynamic_type` | No callers at all |
| `TypeResolver::load_types` | Called once (`router_main.cxx:252`) purely as a legacy/debug path, explicitly off the route-build path |
| `derive_topic_discovery(topic, route_spec)` | `route_spec` is `(void)`-discarded (`RouterState.cxx:51`) — drop the parameter and both call sites |
| `router/build/` | Still holds binaries for retired targets: `test_route_forward`, `test_dynamic_forward`, `test_runtime_spine`, `test_auto_qos`, `test_discovery_smoke` |

The IDL fields are the ones worth deciding about deliberately: they are on the wire, so
consumers can see them and reasonably assume they mean something.

## L4 — `relay/qos_isc.xml` is loaded but unused, and claims default-QoS status

`control-platform.yaml:47` lists `relay/qos_isc.xml` in `qos_libraries:`, but no
`qos_profiles:` alias resolves into it — every alias points at `WAN_QOS_LIB::` or
`LAN_QOS_LIB::` (lines 50-54). The file declares:

```xml
<qos_library name="ActIscLibrary">
    <qos_profile name="ActIscProfile" is_default_qos="true">
```

Loading an unrelated library that asserts `is_default_qos="true"` into the router's
`QosProvider` for no benefit is a latent surprise. Remove it from the list.

## L5 — Unbounded diagnostic maps in `DiscoveryDispatcher`

- `pending_publications_` parks publications whose participant is not yet known
  (`DiscoveryDispatcher.cxx:216-225`). Entries are removed when the participant is
  discovered or the endpoint is lost, but nothing ages out an entry whose participant never
  appears at all.
- `type_not_inline_warned_` (line 302) holds one entry per `(topic, endpoint)` forever.

Both are small in practice. A cap plus a "dropped N pending" counter would make them
provably bounded rather than incidentally bounded.

## L6 — `run_mesh.sh` and the launcher scripts

- **`--with-dashboard` is a no-op.** `WITH_DASHBOARD=true` is the default (line 39) and the
  flag sets it to `true` again (line 47). There is no `--no-dashboard`. The usage text
  advertises a switch that cannot change behaviour. Add the negative flag or drop the
  positive one.
- **`down` never escalates.** It sends SIGTERM, polls for 10 s (lines 229-236), then prints
  "done" unconditionally. A wedged process is reported as torn down. Escalate to `kill -9`
  after the poll, and report anything still alive as a failure.
- **`--help` uses hardcoded line ranges.** `sed -n '2,26p' "$0"` in `run_mesh.sh:49`, and
  `sed -n '2,14p'` in both `start_platform_sim.sh:35` and `start_control_sim.sh:35`. These
  drift the moment the header comment changes.
- **The templating guard is vacuous for platform 30.** For `ID=30` every `sed` is a no-op
  and all three verification greps (lines 140-142) pass trivially, so the guard only
  actually guards IDs ≥ 31. Worth a comment, since `--platforms 1` is the common case.
- `start_platform_sim.sh` / `start_control_sim.sh` lack `set -euo pipefail` (their sibling
  `run_mesh.sh` has it).

## L7 — `port_to_domain()` has a duplicated condition

```python
    if remainder >= D1:
        # Could be meta-unicast (even >= 10) or user-unicast (odd >= 11)
        if remainder >= D1:
            return domain
```
`harness_v2/scripts/domain_traffic_monitor.py:73-76`. The inner test is the outer test. It
also accepts any remainder ≥ 10 as a valid slot, so ports well past the real participant
range still map to a domain — harmless behind the BPF filter, confusing to read.

## L8 — `EventQueue::drain()` copies instead of moving

`router/src/core/EventQueue.hpp:30-44` builds `std::vector<ControllerEvent> out(queue_.begin(),
queue_.end())` then clears. Each `ControllerEvent` holds a full `RouterCommand` with an
embedded `RouterRouteSpec` (two endpoint specs, a topic sequence, a filter-parameter
sequence), so this is a deep copy per drained event on the hot path.
`std::make_move_iterator` fixes it in one line. The queue is also unbounded — a high-water
warn would make discovery-churn backpressure visible.

## L9 — Loop prevention is load-bearing and unasserted

`control-platform.yaml`'s team routes are bidirectional across the same two participants:
`platform_team_to_wan` (platform_lan → platform_wan) and `wan_team_to_platform`
(platform_wan → platform_lan), both on topic `PlatformData` (lines 288-315). A sample
therefore has a complete cycle available to it: A.lan → A.wan → B.wan → B.lan → B.wan → …

Two independent mechanisms break it, and **either alone is sufficient**: the factory's
`dds::pub::ignore(out_dp, writer.instance_handle())` on every route's own output writer
(`RouteEntityFactory.hpp:105`, D31.4), and `DiscoveryDispatcher::is_same_node`'s ignore of
same-node router publications (`DiscoveryDispatcher.cxx:241-252`, D15). That redundancy is
deliberate and documented.

What is missing is the assertion. `test_team_partition.py` exercises exactly this config
shape (`e2e_team_partition.yaml:41-70`) and asserts *delivery*, but never asserts the
absence of an echo. A regression that removed both ignores — or a future config where only
one applies — would produce unbounded WAN amplification, and the suite would still be green.

**Fix.** Add to `test_team_partition.py`: the originating node must not receive its own
`PlatformData` back, and the receiving node's `samples_forwarded` on
`platform_team_to_wan` must not grow when it is only *receiving*.

## L10 — Stale claims in `router/README.md`

- Line 34: "Tests: 9 C++ binaries under `test/` (`ctest`)" — there are 4 test sources and 4
  registered tests. The 9 figure is plausible only because the build tree still holds
  binaries for the 5 retired targets (L3).
- Line 36: "Phase 6 (command/status DDS control loop) is next" — the tree is at Phase 17 /
  D120.
- Line 47: the broken `ctest --test-dir` invocation (H1).

---

# Remaining order of work

~~1. **H1–H4**~~ — done, see the resolution log below.

0. **H4 follow-up** — make the dead-peer retention injectable so the prune is actually
   asserted (see the H4 status block's residual note). Small, and it closes the one place
   where an applied fix currently rests on inspection rather than a test.
1. **M9** — close the remaining coverage hole that makes the suite look bigger than it is.
2. **M5, M6, M13, M14** — small correctness fixes with visible consequences. M6's shape is
   now settled by measurement rather than argument, so it is ready to write.
3. **M15** — best done with M6: same subsystem, same spike, and the two findings are the
   forwarding-side and remote-side halves of the same question. Deleting a
   permanently-zero field is smaller than adding the counter that replaces its intent.
4. **M7, M11** — the remaining state-hygiene items; each needs a decision, not just an edit.
5. **M8** — measure the heartbeat first, then decide. Do not change the IDL on a hunch.
6. **M10, M12, L6** — harness robustness.
7. **L1** — the sim refactor. Highest line-count payoff, lowest risk, and it removes the
   soil M14 grew in.
8. **L2–L5, L7–L9** — hygiene, best done as one sweep. (L10 was folded into the H1 fix.)

---

# Resolution log — H1 through H4

Applied 2026-08-11 on branch `review/2026-08-11-h1-h4-fixes`, branched from `777f8df`.

| Commit | Subject | Findings |
|---|---|---|
| `e9a0732` | `test(router): add run_tests.sh so a zero-test run fails instead of passing` | H1, L10 |
| `472e86d` | `fix(e2e): repair flattened payload accessors and isolate domains off the live band` | H2, H3 |
| `1478edc` | `fix(presence): forget peers that have been DEAD past the retention window` | H4 |
| `80c508b` | `docs: correct two guardrails the 2026-08-11 review disproved` | H1, H3 |
| *this doc* | `docs: add the 2026-08-11 harness_v2 + router review` | — |

By file:

| File | Change | Finding |
|---|---|---|
| `router/run_tests.sh` *(new)* | Build-dir-relative ctest wrapper; fails a zero-test run | H1 |
| `router/CMakeLists.txt` | Header comment repointed at `run_tests.sh` | H1 |
| `router/README.md` | Same; plus corrected test count and the stale "Phase 6 is next" | H1, L10 |
| `.github/copilot-instructions.md` | Point at `run_tests.sh`; and correct the domain-id ceiling from "~5900–6000" to 232, with the port arithmetic and the live/free windows | H1, H3 |
| `router/test_e2e/test_control_command_route.py` | `msg.destination`/`msg.source` → flat members | H2 |
| `router/test_e2e/test_control_platform_full.py` | Flat members (incl. the second stale `control_command_ack` accessor); probes read `unique_domains` | H2, H3 |
| `router/test_e2e/test_detail_status_toggle.py` | Probes read `unique_domains` | H3 |
| `router/test_e2e/conftest.py` | Allocator moved to the non-live 101–199 band with a ceiling assert; `render_config()` rewrites the production config's literal domains and now refuses to pass a config through un-isolated | H3 |
| `router/src/core/PresenceMonitor.{hpp,cxx}` | `kDeadPeerRetentionMs` + `prune_dead_locked()`, called under `roster_mutex_` from `publish_mesh()` | H4 |

Not committed here: `spikes/write_drop_accounting/` was already present and untracked at the
start of this work (another session on this shared VM) and was deliberately left alone.

Three things the fixes turned up that the original review had not seen:

1. **H3 was not confined to one test.** The `unique_domains` allocator itself started at 40,
   inside the live 30–99 platform-LAN band — so *every* test past the first few was
   allocating a live platform's domain, not just the one that loaded the production config.
   And a second test (`test_detail_status_toggle.py`) had the same literal-domain probes.
2. **H2 had a second stale accessor.** `test_control_platform_full.py:163` passed
   `"msg.source"` for `control_command_ack` behind a comment asserting that type "still
   wraps base_type msg". It does not — the earlier `control_command` failure was masking it.
3. **A repo guardrail was wrong, and the suite proved it.** The documented domain-id
   ceiling of "~5900–6000" is off by more than an order of magnitude; it is 232. The first
   H3 attempt followed the documented figure, and three tests failed with a privileged-port
   bind error that traces exactly to `(7400 + 250*1023) mod 65536 = 1006`. Corrected in
   `copilot-instructions.md`. This is a good argument for keeping the e2e suite green and
   fast enough to run after every change — it was the only thing that would have caught it.

---

# Things worth explicitly *not* changing

Called out because a future reader might mistake them for defects:

- **`DrainThread`'s four independent tick knobs.** They look redundant. They are not — the
  comments record a real regression (D97/D98) where coupling the LAN mesh refresh to the WAN
  heartbeat silently changed both. Keep them separate.
- **The two-part protection check in `is_protected_partition_name`**
  (`RouterState.hpp:170-177`). Folding `team_scoped`'s identity entry into
  `protected_partition_entries` at parse time is the obvious simplification and is
  explicitly documented as having been tried and reverted, because it broke protection for
  every `ParticipantState` not built by the parser. Leave it.
- **The `pre`/`post` fingerprint vector indexing in `publish_if_changed`.** It relies on
  `state_.routes` and `state_.participants` having identical size and order across the
  before/after calls. That holds because routes and participants are fixed at construction
  (D24: no implicit route creation). It is safe today; it is worth one assertion if route
  creation ever becomes dynamic.
- **`ParticipantRegistry`'s process-global `DomainParticipantFactoryQos` swap**
  (`ParticipantRegistry.cxx:77-106`). It looks alarming but is correctly save/restored on
  both the normal and exception paths, and there is exactly one registry per process.
