# Phase 3–4 Code Review — Findings

High-effort recall review of the Phase 3 (generated-type forwarding) and Phase 4
(DynamicData + content filter + config parser) code, run 2026-07-09 over commits
`1f17baf` (Phase 3) and `afb8baa` (Phase 4). Eight independent finder angles →
dedup → per-candidate verification against the source. All 10 findings are
**CONFIRMED** or **PLAUSIBLE**.

The test suite (8/8) passes because none of these findings is on a path the current
tests exercise: the tests forward, tear down **once**, and exit — they never rebuild
a route, lose a participant, flap repeatedly, drive discovery-regression mid-create,
run config through the generated-type lane, or use a numeric filter / incompatible-QoS
writer.

Status legend: ☐ open · ☑ fixed. Update as fixes land.

**Resolution (2026-07-10):** all ten findings fixed — decisions pinned as **D41** in
`design-decisions.md` (executes D40 items 1–2). 8/8 test targets green, including two new
regression checks: `test_stale_error_after_abort_discarded` (F3) and the `dynamic_forward`
rebuild leg (F1/F4 — new source after teardown → filtered route rebuilds and forwards
again). Per-finding fix notes below.

## Correctness — fix now

### F1 ☑ CFT leak breaks filtered-route rebuild (CONFIRMED)
`router/src/core/DynamicRouteFactory.cxx:94`

A filtered route's `ContentFilteredTopic` is created with a fixed name
(`topic + "_cft"`) but is never deleted on teardown — `RouteTopicRuntime::close()`
closes only the ReadCondition/reader/writer. Confirmed against Connext 7.7: closing a
`DataReader` does **not** delete its CFT (the participant retains the named entity),
and creating a second CFT with the same name throws `PRECONDITION_NOT_MET`.

**Failure:** filtered route reaches FORWARDING (`X_cft` created) → source drops →
teardown closes reader/writer but not the CFT → source reappears → `create_topic_entities`
re-runs → `ContentFilteredTopic(in_topic, "X_cft", …)` throws → `RouteEntityError` →
topic sticky `TOPIC_ERROR`, route never re-enables. **This breaks the D32 rebuild goal
for every filtered route.**

**Fix direction:** have `RouteTopicRuntime` own and close the CFT (and delete it on
teardown), or find-or-reuse the CFT by name the way the base topic is.

**Fixed (D41):** `RouteTopicRuntime` owns the CFT and closes it after the reader
(validated 7.7: reader close alone does not delete it), freeing the `"<topic>_cft"` name
for the next build. Proven by the `dynamic_forward` rebuild leg.

### F2 ☑ `on_participant` reads `data()` on an invalid sample — the D33 bug, unfixed here (CONFIRMED)
`router/src/core/DiscoveryDispatcher.cxx:127`

`std::string guid = format_key(it->data().key());` runs unconditionally, *before* the
`!it->info().valid()` check at line 128, with no try/catch — the exact invalid-builtin-sample
pattern that D33 replaced in `on_publication`/`on_subscription`, left unfixed in
`on_participant`.

**Failure:** a remote participant departs → NOT_ALIVE participant sample → `data().key()`
on an invalid sample. If it throws (as D33 established for builtin samples), the exception
escapes the AsyncWaitSet handler (worker-thread abort/crash). If it returns a zeroed key,
`participant_table_.erase` / `pending_publications_.erase` target `00000000:…` and remove
nothing → the departed participant's tag leaks in `participant_table_` forever and its
pending publications are never cleared.

**Fix direction:** recover the participant GUID via a captured `instance_handle → GUID`
map (as pub/sub now do), or read the key only inside the `valid()` branch.

**Fixed (D41):** both — the key is read only on valid samples (where it also feeds a new
`part_handle_guid_` map), and the NOT_ALIVE branch recovers the GUID from that map, the
same D33 pattern as pub/sub.

### F3 ☑ `apply_entity_error` misapplies a stale error after abort (CONFIRMED)
`router/src/core/RouterController.cxx:374`

The stale-stamp guard is `if (topic.entity_generation != 0 && e.entity_generation != topic.entity_generation) return;`.
When an abort has zeroed `entity_generation`, the `!= 0` clause makes the guard false, so a
stale error is **not** discarded. Unlike `apply_entities_ready` / `apply_teardown_complete`,
this handler also never gates on `topic_state`.

**Failure:** `create_topic_entities(G)` fails and posts `RouteEntityError(…,G)`; before it
drains, discovery regresses → `reconcile_topic` (CREATING branch) calls
`abort_topic_creation`, setting `topic_state=IDLE, entity_generation=0`. The queued error(G)
then drains, is **not** discarded (gen is 0), and forces the topic to `TOPIC_ERROR` though it
had legitimately returned to IDLE → route stuck in ERROR until command re-arm.

**Fix direction:** drop the `!= 0` clause (`if (e.entity_generation != topic.entity_generation) return;`)
and/or gate on `topic_state == TOPIC_CREATING`, matching the sibling handlers.

**Fixed (D41):** dropped the `!= 0` clause (exact-stamp match). Deliberately NOT gated on
`TOPIC_CREATING`: a runtime error on a FORWARDING build carries the live stamp and must
still land (per-topic containment, `test_per_topic_activation_two_topics`). New
regression: `test_stale_error_after_abort_discarded`.

### F4 ☑ `close()` leaks Publisher/Subscriber/CFT on every rebuild (CONFIRMED)
`router/src/core/RouteRuntime.hpp:52`

Both factories construct a fresh `dds::pub::Publisher(out_dp)` and `dds::sub::Subscriber(in_dp)`
per `create_topic_entities`. `close()` tears down reader/writer/condition but not those parents
(nor the CFT, see F1). A route that flaps accumulates orphaned Publisher/Subscriber/CFT entities
on its participants — unbounded DDS-entity and memory growth for the process lifetime.

**Fix direction:** have the runtime hold and close the Publisher/Subscriber it created (or reuse
one long-lived Publisher/Subscriber per participant across routes).

**Fixed (D41):** `RouteTopicRuntime` owns the per-build Publisher and Subscriber; close
order is condition, reader, writer, CFT, subscriber, publisher (child-first, validated
7.7).

## Correctness — latent / semantics

### F5 ☑ Forced RELIABLE + TRANSIENT_LOCAL → silent no-match (CONFIRMED)
`router/src/core/QosResolver.hpp:24`

`reader_qos`/`writer_qos` ignore the alias and force RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(16).
A route reader then cannot match a BEST_EFFORT or VOLATILE application writer (offered < requested
on those RxO policies), yet the route still reaches `ROUTE_ENABLED` (readiness is driven by builtin
publication discovery, which ignores QoS compatibility) → status shows ENABLED with **zero samples
forwarded and no error**. Common for telemetry (BEST_EFFORT) or non-durable command streams.

**Fix direction:** ~~an unresolvable/incompatible alias should fail loudly; Phase 5's auto-QoS should
derive compatible QoS from the discovered writer rather than forcing one profile.~~ **Superseded by
D39 (closes as part of Phase 5):** input readers become fixed weakest-request (`BEST_EFFORT` +
`VOLATILE` + defaults) and match every writer by RxO construction — no derivation; the silent
no-match is caught by `REQUESTED_INCOMPATIBLE_QOS`/`OFFERED_INCOMPATIBLE_QOS` StatusConditions on
the AsyncWaitSet, warning with `last_policy_id`.

**Fixed (D41, interim until D39 lands):** `QosResolver` now throws (→ `RouteEntityError`,
visible sticky topic error) on any alias other than `""`/`"default"` instead of silently
forcing the built-in profile.

### F6 ☑ QoS alias read from different spec levels across the two lanes (CONFIRMED)
`router/src/core/EntityFactory.hpp:77` (and `:93`)

`EntityFactory<T>` reads `spec->writer_qos` / `spec->reader_qos` (per-topic
`RouterRouteTopicSpec`); `DynamicRouteFactory` reads `view.spec.output.writer_qos` /
`view.spec.input.reader_qos` (endpoint `RouterRouteEndpointSpec`). `RouteConfigParser::fill_endpoint`
populates only the **endpoint** fields (matching the YAML). So a config-driven route run through the
generated-type lane reads an **empty** alias and silently drops the configured QoS. Masked today
because `QosResolver` ignores the alias; it surfaces the moment Phase 5 implements alias lookup.

**Fix direction:** pick one canonical location for the per-topic QoS alias, have a single factory
read it, and drop the unused schema slot. Ties to F10.

**Fixed (D41):** canonical location is the endpoint spec (`input.reader_qos` /
`output.writer_qos`), matching the YAML; `RouterRouteTopicSpec` lost its alias slots and
the one shared factory (F10) reads the endpoint fields. Auto-QoS detection moved to
`route_uses_auto_qos` accordingly.

### F7 ☑ Publication lost while pending is dropped, then replayed as phantom (PLAUSIBLE)
`router/src/core/DiscoveryDispatcher.cxx:163`

A publication discovered before its participant is parked in `pending_publications_` and is **not**
recorded in `pub_handle_guid_` (that write happens later, in `handle_publication_sample`). If the
publisher dies while still pending, `take_lost_guid` misses → no `endpoint_lost` posted and the
pending entry is not removed. When the participant is finally discovered, the dead pending record is
replayed as `publication_discovered` → the controller builds a route toward a source that no longer
exists, and since the loss was already consumed it is never torn down (route stuck FORWARDING, no data).

**Fix direction:** on a NOT_ALIVE for an untracked handle, also drop any matching `pending_publications_`
entry; or record the handle when parking the pending pub.

**Fixed (D41):** a NOT_ALIVE with an untracked handle now sweeps `pending_publications_`
and drops the matching parked record, so it is never replayed as a phantom discovery.

### F8 ☑ Every filter parameter is single-quoted → numeric filters break (PLAUSIBLE)
`router/src/config/RouteConfigParser.cxx:26`

`resolve_filter_param` unconditionally returns `"'" + v + "'"`, assuming every parameter is a string.
A numeric filter (e.g. `msg.seq > %0` with `5`) is emitted as `'5'`; Connext SQL rejects the
numeric-vs-string comparison at CFT creation → `RouteEntityError` → sticky ERROR. Only string-valued
filters work today.

**Fix direction:** SQL quoting belongs where the member type is known (the DDS/factory layer), not the
YAML parser; carry parameters as typed values or defer quoting.

**Fixed (D41, heuristic; superseded by D43):** D41's value-shape heuristic (numeric
literals pass through verbatim, everything else quoted) still misquoted a numeric-looking
*string* (e.g. a node named `"101"`). D43 replaced it with an author-intent signal: an
explicitly quoted YAML scalar (`Tag() == "!"`, confirmed against yaml-cpp 0.8.0) is always
treated as a string regardless of shape; only a plain/bare scalar (an actual YAML number)
falls back to the shape check. New regression in `test_route_config`: node name `"101"`
substituted into the quoted `"${node.name}"` parameter comes out `'101'`, not `101`.

### F9 ☑ handle→GUID maps not purged on participant loss (PLAUSIBLE)
`router/src/core/DiscoveryDispatcher.cxx:131`

`pub_handle_guid_` / `sub_handle_guid_` entries are erased only on a delivered per-endpoint NOT_ALIVE.
`on_participant`'s loss branch purges `participant_table_` and `pending_publications_` but not the
handle maps. D28 notes the "one NOT_ALIVE per endpoint" cardinality is not normatively guaranteed; if a
participant is purged without per-endpoint dispose, those handle entries leak and the endpoints'
`endpoint_lost` is never posted (routes live against a dead source). Related to F2/F7.

**Fix direction:** index handle→GUID by participant (or track ownership) so a participant loss purges its
endpoints' entries and synthesizes the missing losses.

**Fixed (D41):** handle-map entries now carry the owning participant GUID
(`EndpointIdentity`); the participant NOT_ALIVE branch purges both endpoint maps for that
participant and posts a synthesized `endpoint_lost` per purged endpoint.

## Altitude

### F10 ☑ Two near-duplicate factories, already drifting (CONFIRMED)
`router/src/core/DynamicRouteFactory.cxx:52` and `router/src/core/EntityFactory.hpp`

`DynamicRouteFactory` and `EntityFactory<T>` are near-verbatim `IEntityFactory` bodies (create /
ignore / attach / report, teardown, abort, `find_topic_spec`, `find_or_create_topic`, the D31.4
ordering); only the payload-type binding and the CFT branch differ. They have **already diverged** on
which QoS-alias level is authoritative (F6). Every cross-cutting change (Phase 10 dispose/unregister
mirroring, Phase 5 auto-QoS, teardown-barrier or generation-stamp fixes) must land in both files
identically, and a third lane (multi-type dispatch, D34) would triple the surface.

**Fix direction:** one factory skeleton parameterized by a type-lane strategy (payload-type binding +
topic/CFT construction hook), with the create/teardown/report logic shared once.

**Fixed (D41):** `RouteEntityFactory<T>` holds the shared body (create-order, CFT branch,
teardown/abort, reporting); `EntityFactory<T>` and `DynamicRouteFactory` are thin lane
bindings (`ensure_type_available()` + `make_topic()`). The generated lane gained the CFT
branch it silently lacked; the F6 alias divergence is structurally impossible now.

## Non-findings (checked, clean)

- **Conventions (CLAUDE.md):** UDPv4-only transport, generated-type direct member access, and
  participant cleanup all pass. Test `WORKING_DIRECTORY` resolves to local ext4 in this checkout
  (satisfies the hard "never the share" rule; only deviates from the softer "use `/tmp`" guidance).
- **`RouteTopicRuntime::pump()`** DynamicData copy/write and meta-sample skip are correct for the Phase 4
  scope (dispose/unregister mirroring is deferred to Phase 10).
- **`TypeResolver::load_types`** passing a bare path to `QosProvider` is valid.

## Also noted (lower priority — cleanup/efficiency, out of the top-10 cap)

- ☐ Test scaffolding (`DrainThread`, `make_app_participant`, `reliable_tl_reader/writer`,
  `route_enabled_now`, `CHECK`) is triplicated across the three DDS test files → extract a shared
  `router/test/test_support.hpp`. *(Still open.)*
- ☐ The RELIABLE+TL+KEEP_LAST(16) triple is written four times. *(QosResolver's two copies
  collapsed into one `apply_default_profile` with D41; the test-helper copies remain.)*
- ☑ `pump()` rebuilds a `reader.select().condition(cond_)` query per AWS wakeup on the hot path —
  cache the Selector. *(Cached as a member; safe because the D32 detach barrier stops dispatch
  before close — validated 7.7.)*
- ☑ `TypeResolver::has_dynamic_type` does a try/catch lookup, then `get_dynamic_type` looks up again
  (double lookup + exception-as-control-flow); `get_dynamic_type` also derefs `provider_` with no null check.
  *(`get_dynamic_type` null-checks and throws; `has_dynamic_type` delegates to it.)*
