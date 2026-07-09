# Connext Investigation Review

> **Historical record.** This note captures the Connext 7.7 API investigation as it stood; some
> of its decisions (notably serialized-CDR-first and ISC/keyed-lifecycle) were later superseded.
> For the current design read [Thesis & Tenets](thesis-and-tenets.md) (authoritative),
> [isc-findings.md](isc-findings.md) (why ISC is out of scope), and
> [presence-and-health.md](presence-and-health.md). Where this doc conflicts, those win.

## Purpose

This note records the Connext-specific design decisions, risks, and follow-up spikes for
the C++ Dynamic DDS Router plan. It is intended to raise confidence in the phased plan
without pretending that every runtime behavior is already proven in code.

Evidence sources:

- Connext AI answers for RTI Connext DDS Professional 7.7.0 Modern C++ API behavior.
- Local Connext 7.7.0 headers under `/home/rti/rti_connext_dds-7.7.0/include/ndds/hpp`.
- Existing ACT QoS and Routing Service configuration under `harness/act/config`.
- Existing keyed C++ relay proof in `relay/cpp/isc_relay.cxx`.

Overall confidence: high for the phased plan if serialized CDR forwarding and generic
DynamicData lifecycle mirroring remain isolated optimization/risk spikes, not blockers for
the first working milestone.

## Summary Decisions

| Area | Decision | Confidence | Remaining proof |
|---|---|---|---|
| Discovery metadata | Build `DiscoveryDispatcher` from built-in publication/subscription metadata; translate endpoint facts into controller events and keep only the participant tag table | High | Smoke test duplicate discovery callbacks and endpoint removal |
| Type resolution | Use local XML/generated type definitions as the deterministic POC path; use discovered DynamicType/TypeLookup only when type propagation is enabled | High | Verify ACT XML type load and mapping from topic/type names |
| AsyncWaitSet lifecycle | Attach/detach read conditions dynamically, but serialize entity lifecycle through controller/dispatcher ownership | High | Repeated attach/detach/close executable |
| QoS policy | Treat discovered QoS as diagnostic input, not something to clone blindly; use explicit topic-class QoS aliases for POC routes | High | Capture ACT endpoint QoS and confirm aliases match intended flows |
| Team partition changes | Prefer scoped publisher/subscriber or team participant partition updates; use make-before-break recreation if live mutation is disruptive | Medium-high | Team assignment test under active `PlatformData` traffic |
| Serialized CDR path | Support only as an optimization using DynamicData CDR mode; do not describe it as raw RTPS or true zero-copy forwarding | Medium-high | Standalone CDR-mode pass-through spike |
| Keyed lifecycle | Mirror lifecycle by recovering keys with `reader.key_value()` and resolving outbound writer handles from keys | Medium-high for generated/dynamic data, medium for CDR-only | Dispose/unregister test for generated and DynamicData keyed topics |
| Harness replacement | Run Routing Service baseline and C++ router candidate through equivalent ACT edge tests | Medium-high | Baseline/candidate matrix in compose/scripts |

## Phase 2: Discovery Dispatcher

Evidence:

- Connext 7.7 built-in publication/subscription data exposes topic name, type name,
  partitions, endpoint and participant identity, and endpoint QoS metadata.
- Connext AI confirmed full type structure is separate from basic endpoint metadata.
- Local headers confirm `PublicationBuiltinTopicData` and `SubscriptionBuiltinTopicData`
  accessors for `topic_name()`, `type_name()`, `partition()`, and DynamicType-related
  fields.

Decision:

- `DiscoveryDispatcher` should translate built-in participant/publication/subscription
  samples into route-relevant controller events. It keeps only the participant tag table;
  endpoint current state remains in the DDS built-in readers.
- Discovery can activate route resolution, but it must not create a route until type and
  QoS resolution have both succeeded.
- **Resolved ([design-decisions.md](design-decisions.md) D12):** the fallback below is
  promoted to the decision — builtin participant/publication/subscription readers with
  `ReadCondition`s on the router's `AsyncWaitSet` (created before participant enable) are
  the primary mechanism; discovery listeners are not used.

Concern:

- Discovery can produce repeated callbacks and transient endpoint loss. Type resolution in
  7.7 can also be asynchronous when TypeLookup is involved.

Required spike/test:

- Start router before and after a generated writer/reader pair. Verify route candidate count,
  duplicate suppression, endpoint removal, and status snapshots.

Fallback:

- If listener callbacks are awkward, use built-in publication/subscription readers with a
  polling or condition-based index update loop. The architecture does not depend on a
  specific discovery event API.

Confidence after investigation: high.

## Type Resolution And ACT WAN QoS

> **Decision reversed (2026-07-07): type discovery is now ENABLED on the WAN.** An earlier
> draft required propagation to stay off and matched types from a local XML catalog only. We
> now keep TypeObject v2 + on-demand TypeLookup enabled, because when both ends create the
> **same** topics/types there is effectively **no full-type traffic across the link** anyway
> (see [WAN Type-Exchange Behavior](#wan-type-exchange-behavior-typeobject-v2)). The local
> catalog is retained as a deterministic **fallback**, not the primary path.
>
> **Why the reversal — the Connext 7.7 delta.** The `type_object_max_serialized_length = 0`
> setting in ACT's WAN QoS was a rational choice under the **pre-7.7** Connext this system
> previously ran on: there, type propagation meant inlining the full **TypeObject v1 / TypeCode**
> into every endpoint discovery announcement — genuinely WAN-expensive, so it was disabled and
> types were matched by name from a local catalog. **7.7 introduces TypeObject v2**, which
> changes that calculus: discovery now carries only small fixed-size type-identifier **hashes**,
> and the full type is fetched **on demand once and cached** via TypeLookup. Enabling type
> discovery is therefore cheap now in a way it was not before — the old `=0` was correct for the
> old stack; the v2 delta is what makes turning it on the right call today.

Evidence:

- Connext AI confirmed topic name, type name, partition, and QoS metadata remain discoverable
  regardless of the TypeObject setting.
- **Critical QoS correction.** The `type_object_max_serialized_length` value has three regimes,
  not two:
  - `0` (explicit) → disables **all** TypeObject propagation, **including TypeObject v2**. This
    is what turns type discovery off entirely.
  - `LENGTH_AUTO` (the 7.7 default) → disables only legacy **TypeObject v1**; **TypeObject v2
    still works** as long as `TYPE_LOOKUP_SERVICE_CHANNEL` is enabled.
  - `> 0` → an explicit cap.
- `type_code_max_serialized_length` controls only the deprecated **TypeCode** wire format; it
  is legacy and stays `0`.
- ACT `wan_qos_lib.xml` currently sets `type_object_max_serialized_length = 0`, which
  **explicitly disables even TypeObject v2**. Enabling type discovery therefore requires a
  real config change, not just relying on defaults.

Decision:

- **Enable TypeObject v2 type discovery on the WAN participant.** Set
  `type_object_max_serialized_length` to `LENGTH_AUTO` (or a sized cap) and keep
  `TYPE_LOOKUP_SERVICE_CHANNEL` in `discovery_config.enabled_builtin_channels`. Leave
  `type_code_max_serialized_length = 0` (legacy).
- **The relay carries this in its own QoS files, not by editing ACT.** The ACT submodule
  (`harness/act`, `wan_qos_lib.xml` with `type_object_max_serialized_length = 0`) is left
  untouched for now. The relay ships its own WAN QoS library (same pattern as `relay/qos_isc.xml`)
  with v2 enabled, and reconciling the ACT repo is deferred to a single later pass.
- The router must not silently depend on the setting — validate at startup and fail the route
  loudly if a type cannot be resolved.
- Keep the local type catalog (ACT XML / generated type support), keyed by discovered
  `(topic_name, type_name)`, as the **deterministic fallback** and as the source both ends use
  to register the *same* type so the equivalence hashes match (and no full type crosses the
  WAN). Resolution order is unchanged: generated support → loaded XML → discovered
  TypeObject/TypeLookup.

Concern:

- A route that appears discoverable by topic/type name can still fail to create DynamicData
  entities if neither the local catalog nor TypeLookup yields the exact type before the
  deadline.
- If the two ends drift structurally (different IDL/extensibility/rtiddsgen version), the
  equivalence hashes diverge and Connext falls back to fetching the full TypeObject per
  affected topic — silently reintroducing WAN type traffic. Pin the IDL as a shared contract.

Required spike/test:

- With `type_object_max_serialized_length = LENGTH_AUTO` on both ends, create one DynamicData
  topic/reader/writer pair driven purely by discovered topic/type + TypeLookup (no preloaded
  catalog) and confirm the route activates.
- Repeat with the local catalog preloaded on both ends and confirm **zero** TypeLookup
  request/reply traffic crosses the link (both sides already hold the type → hash match).

Fallback:

- For lifecycle-sensitive or hard-to-load topics, use generated-type route runtimes for the
  first POC and keep DynamicData as the generic route path.

Confidence after investigation: high; the local catalog remains available as fallback.

## WAN Type-Exchange Behavior (TypeObject v2)

Validated against Connext 7.7.0 (`ask_connext_question`, 2026-07-07). This is the basis for
enabling type discovery over the WAN with negligible traffic cost.

Model: the router runs a fixed topic list and, on the LAN side, creates a reader from a
discovered writer's type; it then creates the WAN-side writer registered with that **same**
type (same equivalence hash). During WAN discovery:

1. The router never issues a TypeLookup **request** across the WAN for its own type — it
   already holds that type locally (learned on the LAN / from the catalog). It is the type
   **authority/source** on the WAN side, not a learner.
2. The WAN writer's `DATA(w)` still carries the small, fixed-size hash-based
   `TypeInformation`/`TypeIdentifier`. This per-endpoint overhead is unavoidable and
   independent of type complexity — orders of magnitude smaller than inlined TypeObject v1.
3. The **only** full-TypeObject transfer across the WAN is **relay → remote**, served on
   demand, once per remote participant that does not already hold the type, then cached
   (dependencies included). No re-send for later endpoints with the same hash.
4. If the remote peer already holds the same type (same equivalence hash), matching completes
   against the locally-held type with **no** full-TypeObject exchange at all. Precise wording:
   *if the participant already has the TypeObject for the received TypeIdentifier, it does not
   fetch it remotely.* It is not a "hash-only" match — assignability is still the underlying
   model — but no bytes cross the link.
5. `request_types_filter` is **not** needed on the WAN participant: that knob is for
   proactively *learning* unknown remote types before a local endpoint exists. The router is
   serving types outward, not learning them from the WAN. (It would only matter for a generic
   "learn arbitrary WAN types first" mode, which the POC does not do.) Do **not** set it to
   `*` on the WAN — that would proactively pull every remote type. The **LAN** side is the
   opposite case: there the router *is* the learner with no local endpoint yet, so LAN
   participants **do** set `request_types_filter` — pinned in
   [design-decisions.md](design-decisions.md) D13.

Two operational consequences to carry into config/deploy:

- **TypeLookup reachability is required inbound.** Even though the router never requests types
  over the WAN, remote readers request *from* it. The builtin TypeLookup replier endpoints
  must be reachable across the WAN transport/NAT/firewall, or a fresh peer that lacks the type
  cannot resolve it (requests retry with exponential backoff, ~2 s doubling up to 10 attempts,
  so a blocked path shows up as slow/failed matching, not a clean error).
- **MINIMAL vs COMPLETE.** For a structural relay, MINIMAL TypeObject suffices. Capture the
  **COMPLETE** TypeObject if anything on the far side introspects member names (tooling,
  transforms). The router already holds a usable `DynamicType` because it built the LAN reader
  from the discovered type.

Net: with matching type definitions on both ends, steady-state WAN discovery carries only the
small per-endpoint TypeIdentifier hashes; a full TypeObject crosses the link only when a peer
that has never seen the type joins — served once by the relay and cached. This is the
minimal-traffic outcome, which is why type discovery stays enabled.

## Phase 3: Dynamic Entity Lifecycle And AsyncWaitSet

Evidence:

- Connext AI confirmed that `rti::core::cond::AsyncWaitSet` supports dynamic
  attach/detach while running.
- Default blocking attach/detach operations are recommended unless the router needs
  explicit completion-token orchestration.
- `AsyncWaitSet` uses a thread pool; different conditions can dispatch concurrently. The
  same condition is not concurrently dispatched by default.
- `unlock_condition()` is an advanced same-condition reentrancy escape hatch and should not
  be part of the initial router design.

Decision:

- One `ReadCondition` per route reader.
- Attach/detach from `AsyncWaitSetDispatcher` or the controller-owned lifecycle path, not
  from arbitrary worker code.
- Detach before destroying the condition, reader, or handler state.
- Keep callbacks shallow: bounded take/read, enqueue forwarding work, update lightweight
  counters, and return.

Concern:

- Deleting DDS entities while callbacks are active is the highest concurrency risk in the
  early implementation.

Required spike/test:

- Repeatedly create a reader, attach a read condition, forward a sample, detach, and close
  the reader under active publishing. Run enough cycles to catch callback-after-close and
  resource leaks.

Fallback:

- Route all attach/detach/close through a single controller strand and avoid aggressive
  route rebuilds. Use blocking detach for teardown unless a later profile proves it blocks
  too long.

Confidence after investigation: high.

## Phase 5: LAN Auto QoS

Evidence:

- Connext AI warned against blindly cloning discovered endpoint QoS.
- Routing Service also relies on explicit configured QoS for its created readers/writers,
  with topic filters or route configuration selecting those policies.
- ACT LAN QoS already classifies status, 1 Hz status, events, platform status, and control
  status in named profiles.

Decision:

- Use explicit QoS aliases for first POC routes.
- Treat `auto` as deterministic policy selection from a small topic-class table, not as a
  full endpoint-QoS copy operation.
- Status should show both the configured alias and resolved key policies.

Concern:

- Some discovered endpoint policies are route-edge contracts, while others are local
  resource decisions. Copying all of them can create unexpected buffering, durability, or
  partition behavior.

Required spike/test:

- Capture actual ACT endpoint QoS summaries for `ControlCommand`, `PlatformStatus`,
  `ContactReport`, and `PlatformData`. Verify the minimal alias set matches the intended
  DDS compatibility at each edge.

Fallback:

- Require explicit `reader_qos` and `writer_qos` aliases in YAML for every POC route.

Confidence after investigation: medium-high.

## Phase 8: Team Partition Changes

Evidence:

- Connext AI confirmed Partition QoS exists at participant, publisher, and subscriber
  levels, not directly on DataReader/DataWriter.
- Partition QoS is mutable at runtime through entity QoS setters.
- Runtime partition changes cause unmatch/rematch behavior and discovery churn.
- ACT uses team partition changes to move platforms from node-specific isolation to shared
  team membership.

Decision:

- Keep team traffic isolated in the `platform-team` router instance.
- Prefer scoped publisher/subscriber partition changes or make-before-break entity
  recreation for the team route.
- Participant-level partition changes are acceptable when the entire team participant moves
  together, but they are broader and more disruptive.

Concern:

- Live partition mutation can temporarily break matches and reliable streams. Behavior may
  be acceptable for team assignment but should not be hidden from status.

Required spike/test:

- Run Platform_30 and Platform_31 with unique partitions, mutate both to `TEAM_A`, verify
  `PlatformData` crosses, then move one to `TEAM_B` and verify isolation returns.

Fallback:

- Recreate affected publisher/subscriber/readers/writers with the new partition. If that is
  still unpredictable in the ACT harness, restart only the `platform-team` router instance
  on team assignment for the first demo.

Confidence after investigation: medium-high.

## Phase 9: Serialized CDR Fast Path

Evidence:

- Connext AI confirmed `DynamicData` CDR mode supports efficient bridging with
  `skip_deserialization`, `get_cdr_buffer()`, and `set_cdr_buffer()`.
- It also confirmed this is application-level CDR-buffer forwarding, not raw RTPS packet
  forwarding and not a general true zero-copy router handoff.
- Local 7.7 headers confirm `DynamicData::is_cdr()`, `get_cdr_buffer()`,
  `set_cdr_buffer()`, and CDR helper APIs.

Decision:

- Keep `dynamic_data` forwarding as the first working path.
- Add `serialized_cdr` as an optional route forwarding mode only when type and data
  representation are compatible and the route does not need field inspection.
- Do not use `serialized_cdr` for lifecycle-sensitive routes until key handling is proven.

Concern:

- CDR-backed DynamicData cannot be reflectively manipulated like normal DynamicData.
- `set_cdr_buffer()` must be used on an outbound DynamicData object, not a reader-owned
  sample.
- XCDR/XCDR2 representation mismatches require deserialize/serialize conversion.

Required spike/test:

- Build one generated-type or XML-backed pass-through route using CDR mode. Confirm a valid
  sample arrives downstream and status reports the fast path. Then intentionally mismatch
  data representation and verify the route rejects or falls back.

Fallback:

- Ship all first POC routes in `dynamic_data` mode. Treat CDR forwarding as a performance
  optimization after the control plane and harness are stable.

Confidence after investigation: medium-high for API feasibility, medium for ACT route use.

## Phase 10: Keyed Lifecycle Mirroring

> **Superseded/expanded by [ISC Findings & Path Forward](isc-findings.md).** That doc records
> the validated Instance State Consistency behavior (native RECOVER covers only same-writer
> reconnect, not restarted-writer/virtual-GUID; CORE-13337; the intermediary gap) and the
> recommended read-retain + re-write path, all proven by the `spikes/isc_recovery/` spike.

Evidence:

- Existing `relay/cpp/isc_relay.cxx` already proves generated-type key recovery using
  `reader_.key_value(key, info.instance_handle())` and mirrors dispose/unregister with the
  output writer.
- Connext AI confirmed the generic DynamicData pattern: recover a key-only DynamicData from
  the input reader, use that key to look up or register an output writer instance, and then
  call `dispose_instance()` or `unregister_instance()` on the output writer.
- Connext AI confirmed inbound instance handles are not portable to the output writer.

Decision:

- Store route lifecycle state by canonical instance key, not by inbound instance handle.
- Preserve dispose and unregister as distinct lifecycle operations.
- Use generated-type or normal DynamicData forwarding mode for lifecycle-sensitive routes.

Concern:

- CDR-only forwarding does not by itself solve invalid-sample lifecycle handling. The router
  still needs key recovery and outbound writer-handle management.

Required spike/test:

- For one generated keyed type and one DynamicData keyed route, test write, dispose,
  unregister, dispose-then-rewrite, writer restart, and route delete while instances are
  alive.

Fallback:

- Require `forwarding_mode: generated_type` or `dynamic_data` for routes with
  `mirror_instance_state: true`. Disallow `serialized_cdr` for those routes until the key
  path is explicitly proven.

Confidence after investigation: medium-high for generated/DynamicData lifecycle,
medium for serialized CDR lifecycle.

## Phase 11: Harness Replacement

Evidence:

- Connext AI recommended a behavioral conformance suite comparing Routing Service baseline
  and custom C++ router candidate at the route edges.
- ACT already has Routing Service scenarios for command/status, detail status enablement,
  and team assignment.

Decision:

- Treat harness replacement as baseline/candidate validation, not only a startup script
  change.
- Record route formation, match/non-match behavior, command replies, status snapshots,
  sample counts, WAN impairment behavior, and lifecycle events.

Concern:

- A router can pass clean-LAN demos and still fail under discovery churn, lossy WAN, or
  repeated dynamic route changes.

Required spike/test:

- Run a 12-test minimum matrix: startup discovery, late discovery, partition isolation,
  QoS compatibility, late joiner durability, dynamic create, dynamic delete, admin reply,
  pause/resume, lossy WAN reconnect, dispose propagation, and unregister propagation.

Fallback:

- Start with observe-only/status-only sidecar mode beside Routing Service, then replace one
  non-critical route before removing Routing Service from the node stack.

Confidence after investigation: medium-high.

## Foundational Phases 0, 1, 4, 6, And 7

These phases remain high confidence because their risks are mostly application architecture
and ACT route mapping rather than uncertain Connext API behavior.

- Phase 0: build skeleton and admin IDL is straightforward if Connext 7.7 C++ builds in the
  harness.
- Phase 1: controller-owned state is an ordinary state-machine/testability concern.
- Phase 4: role-aware route selection follows directly from the ACT control/platform route
  symmetry.
- Phase 6: command/status loop can be built as generated admin topics with idempotent
  command handling.
- Phase 7: platform status/events replacement uses the same route mechanics proven by
  Phases 3 and 4, plus ACT topic mapping.

## Open Design Rules

These rules should be treated as binding until an executable spike proves a better option:

- Type fallback through XML/generated support is mandatory for ACT WAN-like profiles because
  type propagation is disabled.
- All route entity lifecycle operations must detach AsyncWaitSet conditions before closing
  readers or destroying handler state.
- AsyncWaitSet callbacks must not wait on completion tokens or perform route graph mutation.
- Discovered QoS should inform compatibility checks and diagnostics, not be cloned blindly.
- Serialized CDR forwarding is optional and must fall back to normal DynamicData forwarding.
- Lifecycle-sensitive routes must use generated-type or normal DynamicData mode until CDR
  key/lifecycle handling is separately proven.
- Team partition changes must publish status transitions that expose the temporary rematch
  window.
