# Two-topic WAN native instance lifecycle plan

Date: 2026-11-10

Status: Native application lifecycle forwarding is implemented at both adapter
boundaries. The four previously failing acceptance cases and isolated regression
gates pass. See [migration checkpoint 3.20](two-topic-wan-migration-plan.md#320-native-lifecycle-implementation-and-isolated-regression-gate-2026-11-10)
for exact counts and browser/live skips. The integrated working tree was later
deployed separately; that deployment did not run live lifecycle or mission
acceptance. Finalization here uses an isolated lifecycle-only source snapshot
and does not rebuild or restart the running mesh.

Final lifecycle-only review/build gate: **9 C++ targets, 273 Python tests plus
171 subtests, 29 WAN qualification cases, and 21 purge cases passed**; 24 optional
browser/live tests skipped. Four per-leg lifecycle cases also passed a separate
evidence rerun. See [finalization evidence](two-topic-wan-migration-plan.md#3201-lifecycle-only-finalization-gate-2026-11-10).

Parent roadmap: [two-topic WAN migration plan](two-topic-wan-migration-plan.md),
especially the confirmed gap in section 3.19.

## 1. Agreed routing contract

**Regular samples and lifecycle metadata use the same routes.** There are no
separate disposal routes, alternate destination lists, or per-instance
destination-delivery ledgers.

An "empty sample" here means a key-only lifecycle notification: the application
payload is absent, but the instance identity and DDS instance state remain
available. It does not mean publishing a zero-filled valid application sample.

```text
Input data or lifecycle notification
  -> existing route binding and topic mapping
  -> existing codec payload/key translation
  -> existing route output writer
  -> write, dispose_instance, or unregister_instance
```

| Input | Application payload | Output operation |
|---|---|---|
| Valid data | Present | `write()` |
| NOT_ALIVE_DISPOSED notification | Absent | `dispose_instance()` |
| NOT_ALIVE_NO_WRITERS notification | Absent | `unregister_instance()` |

Both LAN-to-WAN and WAN-to-LAN must implement this contract. The WAN carries
native DDS lifecycle operations on the existing Event/Status instances, not
application messages containing a new lifecycle opcode.

Unchanged: schema version 2, the two WAN topic names, application payloads,
current reliability policies, and the canonical legacy default.

## 2. Current code and reuse

| Surface | Existing behavior | Planned change |
|---|---|---|
| [InputRuntime](../router/src/core/WanAdapterFactory.cxx) | Dispatches valid samples and provides a separate invalid-data callback hook | Recover key/state and dispatch lifecycle through the same bound route ownership; isolate per-notification errors |
| `bind_source()` in the same file | Merges active logical route metadata and writes one shared WAN frame | Add key-only lifecycle forwarding through that same shared output |
| `make_receiver()` in the same file | Decodes valid frames and iterates existing topic-matched targets | Reuse that target selection for application lifecycle operations |
| `Source::routes`, `Receiver::targets`, `Target::routes` | Own current logical contributions and physical output sharing | Remain the authoritative route/output selection structures |
| [WanCodec](../router/src/core/WanCodec.hpp) | Provides `pack`, `key_frame`, `unpack`, and `unpack_key` | Reuse existing key conversion; do not introduce a second mapping catalog |
| [WanInterests](../router/src/core/WanInterests.cxx) | Derives route metadata using current interests and full payload fields | Separate payload-derived routing context only where needed for key-only handling |
| [External QoS profiles](../harness_v2/qos/act_qos_profiles.xml) | Own fixed DDS policies | Set both reader-instance purge delays to infinite for the relevant two-topic adapter input readers |
| [WanAdapterFactory interface](../router/src/core/WanAdapterFactory.hpp) | Documents the application lifecycle gap | Update the contract after implementation; avoid public API expansion unless required |

Do not fork a second routing implementation inside the lifecycle callbacks.
Factor only the small shared target-selection or output-operation code needed
to prevent valid-data and lifecycle paths from diverging.

## 3. Key recovery and permitted adapter state

Input handles belong to the input reader; they cannot be passed directly to a
different output writer.

1. Recover the input key using the native reader registry where available.
2. Translate it through `key_frame()` or `unpack_key()`.
3. Use the selected output writer's `lookup_instance(translated_key)`.
4. Apply the native lifecycle operation to that output instance.

Key lookup after taking a final NO_WRITERS notification has already failed in
the Python acceptance observer because the reader registry was reclaimed.
The [native C++ purge spike](../spikes/reader_instance_purge/README.md) now
qualifies this distinction: keys remained available while the take loan was held,
including for 2100 ms with zero-delay purging, and disappeared immediately after
the loan was returned. Synchronous key translation and native relay passed
without a key cache. Prefer this existing loan-scoped path for keyed inputs;
retain canonical keys from valid samples only where necessary to survive
reclamation or deferred processing. The result is not a guarantee under arbitrary
resource replacement or concurrent reader consumers.
Some mappings derive WAN identity from a payload field rather than a LAN DDS
key; their identity must likewise be retained before the payload disappears.

Finite instance delays of 2 and 5 seconds were accepted by the XML provider but
rejected by enabled reader construction in installed 7.7.0. Only zero/infinite
instance retention is supported in this spike. Finite sample-delay acceptance
does not establish finite instance retention. Do not implement the proposed
short retention window as if it were supported. Infinite instance retention
is the selected implementation policy below.

### Selected reader retention policy

Decision, 2026-11-10: use **infinite reader-instance retention** for both
`autopurge_nowriter_instances_delay` and `autopurge_disposed_instances_delay`.
Apply this in external XML profiles to the relevant LAN source readers and
shared WAN input readers of the two-topic adapter. Scope profile inheritance
so unrelated readers and the legacy path are not changed unintentionally.

Automatic reader-instance purging is deferred to later work. Do not introduce
a timed purge, background eviction task, or TTL cache as part of this phase.
This decision does not change sample-purge delays, writer lifecycle policies,
presence timing, reliability, or the disposal/unregister operation itself.
Forward lifecycle immediately through the same routes; infinite retention
preserves reader state, not the instance's ALIVE state.

Continue using the native reader registry for recoverable keys and the native
output writer registry for translated handles. Do not add a duplicate key map
solely to work around zero-delay reader reclamation. Payload-derived identity
or routing context remains a separate requirement where it is not a DDS key.

Infinite retention is not infinite capacity: existing DDS resource limits
remain in force, and exhaustion must be surfaced explicitly. Reader close and
owned teardown still release resources. Pressure, replacement, and long-run
instance-churn limits must be documented; infinite retention is not a guarantee
against every form of resource replacement.

Implemented as the XML `router_lifecycle_reader` profile. `QosResolver` composes
only its two instance-delay values into the selected adapter reader policy,
preserving all other route QoS, including sample-purge settings. This avoids
changing shared legacy profiles or defining a duplicate profile for every alias.
Enabled adapters exercise this policy in native tests; no persistent router
binary has been overwritten or deployed.

Any fallback cache must be scoped to its physical input reader/epoch, bounded,
and cleared with that reader's owned teardown. Define the bound against actual
configured resource limits before implementation is accepted. Do not silently
evict a live entry and later claim its lifecycle was forwarded.

Permitted state is key translation and the minimum payload-derived routing
context that native keys cannot supply. It is not:

- A duplicate DDS writer-registration, ownership, or writer-count database.
- A record of which boats or applications consumed a sample.
- A parallel list of output writers that overrides current route bindings.
- A persistent database or a cached copy of every application payload.

Dispose does not remove registration. Keep the key/context needed for a later
unregister and for revival. Verify adapter-owned context cleanup separately
from the intentionally retained native reader state. Verify reader replacement,
route teardown, and shutdown cleanup.

## 4. Same routes does not mean blank-payload filtering

The same route enablement, topic mapping, loop prevention, and applicable
source/audience rules must govern data and lifecycle forwarding.

However, a native invalid-data notification does not provide non-key audience,
destination, or audit fields. The current metadata provider reads such fields
from valid payloads; do not invoke it on a default-filled key object.

Use current route policy plus retained payload-derived routing context where
required. Route/team updates must not replay a stale cached route snapshot.
If sufficient context cannot be recovered, report the limitation explicitly;
do not invent an audience or treat absent fields as permission to broadcast.

Native lifecycle delivery through a content-filtered reader is not proof that
the normal non-key content predicate matched. The implementation and tests must
establish which lifecycle operations are eligible for the existing route
outputs, without adding disposal-only routing.

The baseline is existing-instance forwarding: if the selected output writer has
no registered matching instance, do not register or publish one just to dispose
it. Report this outcome distinctly from successful forwarding and from a DDS
operation failure. A first-observed lifecycle notification does not create a
new audience or LAN instance.

## 5. Implementation sequence

### A. Reader retention, shared dispatch, and identity recovery

- Author both infinite reader-instance delays in narrowly scoped external XML
  profiles for the relevant two-topic inputs. Validate with the installed
  `QosProvider`, create enabled readers, and inspect effective QoS. Do not set
  fixed DDS policies programmatically or change unrelated legacy readers.

- Extend `InputRuntime` handling so valid and invalid samples reach the same
  binding-owned route processing, with explicit validity/state.
- Resolve keys while the input loan is alive and retain only what must survive
  that processing scope; do not read invalid payload fields.
- Handle per-sample failures through existing error/warning callbacks so one bad
  notification does not discard the remainder of a taken batch.
- Keep valid-data forwarding counts meaningful; do not count lifecycle as
  successful valid DATA or fabricate application delivery-audit sequences.

### B. LAN-to-WAN lifecycle

- Use `Source::routes` and the existing shared WAN output.
- Translate the original topic/key to the schema-2 WAN key:
  `(source_id, original_topic, instance_id)`.
- Preserve router-authoritative source identity.
- Resolve the output handle and issue dispose or unregister once per physical
  output, not once per overlapping logical contribution.
- Continue normal `pack()`/`write()` behavior for valid data and revival.

### C. WAN-to-LAN lifecycle

- Recover WAN keys and use `unpack_key()` to restore the LAN key.
- Preserve the self-origin loop guard.
- Select outputs through the same topic-matched `Receiver::targets` traversal
  used for valid data; share that traversal rather than copying its rules.
- Use each selected writer's native lookup and lifecycle API.
- Preserve existing RouterHealth observer behavior and presence timing; an
  application lifecycle notification must not become a missing-health event.

### D. Ownership and teardown

- Reuse existing mutexes, route generations, physical endpoint epochs, output
  leases, and dispatcher detach/close barriers.
- A disabled or detached route forwards neither data nor lifecycle.
- Removing one logical route must not close an output still owned by another.
- Do not add a teardown sweep that disposes every instance on a shared writer.
- Release key/context state with its owning input runtime; stale callbacks must
  not act on a newly enabled route generation.

### E. Identity-collision qualification

DDS already aggregates input instance state across its writers. Do not rebuild
that writer accounting in the router. A last-writer NO_WRITERS notification maps
to unregister; dispose remains distinct and must not wait for NO_WRITERS.

Key normalization can nevertheless merge distinct input instances. For example,
different producer `source` keys for the same ContactReport `contact_id` can map
to one router-authoritative WAN key. Native output lookup does not prove which
input alias owns that normalized instance.

Reproduce this case explicitly. If independent aliases require conflicting
lifecycle outcomes, record the result and obtain a policy decision before
claiming support. Do not silently add an ownership ledger or claim that a
many-to-one mapping preserves independent instance lifecycles.

Verified and approved, 2026-11-10: producer aliases with the same boat/contact ID
are one logical contact. Their regular writes and lifecycle operations affect
that same canonical output instance. Dispose or NO_WRITERS from either original
alias applies to the canonical instance; independent producer-alias lifecycles
are not promised. This does not change native DDS aggregation of multiple
writers on the same input key.

## 6. Acceptance and regression gates

The first gate makes all four existing cases in
[test_two_topic_controller.py](../router/test_e2e/test_two_topic_controller.py)
pass without xfails, skips, weaker assertions, or reliability changes:

- ContactReport/Event and PlatformInitStatus/Status.
- Full LAN-to-WAN-to-LAN and direct WAN-to-LAN injection.
- Initial ALIVE, dispose, ALIVE revival, unregister, ALIVE revival.
- Native observations on each applicable leg, preserving logical keys/handles.

Then add targeted coverage:

| Scenario | Required outcome |
|---|---|
| Two writers for one input instance | One unregister does not synthesize NO_WRITERS while another writer remains |
| Dispose followed by unregister without intervening valid write | Distinct operations forward when the configured native reader exposes the transitions; otherwise document the observed QoS boundary |
| Overlapping logical routes sharing one physical writer | One lifecycle operation per physical output; remaining route ownership survives unbind |
| Multiple instance IDs and origins | No cross-instance or cross-origin disposal |
| Non-key filter/audience exclusion; unknown output instance | No unintended output creation or unrelated disposal; explicit diagnostic |
| Team/filter update and route disable/re-enable | Current routes govern forwarding; no stale-generation callback effects |
| Key reclamation and repeated lifecycle | Key recovery succeeds or reports a precise unsupported/missing-key outcome |
| Infinite reader-instance retention | Effective QoS is infinite for both delays; key lookup survives loan return and lifecycle still reaches the destination immediately |
| Many-to-one key normalization | Tested, documented alias behavior; no unsupported independence claim |
| Malformed key or unsupported mapping | Explicit error; later samples in the batch still process |
| RouterHealth and application lifecycle mixed on Status | Existing health/presence behavior remains intact |
| Teardown and repeated runtime creation | No leaked callbacks, outputs, or unbounded key/context state |

Run focused codec/interest/adapter tests and rebuild the C++ router before native
tests. Use disposable isolated Docker networking, safe DDS domains <= 232, and
owned test cleanup; do not run the test rig against the live mesh.

After focused gates pass, run the router unit/e2e, WAN qualification, relevant
spike, and dashboard regression suites. A final live mission/deployment gate,
if requested, uses the owned EMANE lifecycle only. Preserve compact per-leg
evidence under `debug/test_reports`; update the
[e2e README](../router/test_e2e/README.md) and parent roadmap with actual results.

## 7. Completion and limits

Complete means both production boundaries forward native lifecycle through
existing routes, focused and regression gates pass, ownership/cleanup behavior
is tested, and documented limitations match evidence.

Best-effort Status remains best effort: successful adapter forwarding is not a
guarantee of arrival under packet loss. Restart recovery, first-observed
unknown-instance propagation, durable replay, congestion qualification, and
changing the default WAN mode remain separate work unless explicitly added.
Automatic reader-instance purging is also deferred; this phase uses infinite
reader-instance retention, with existing resource limits and owned teardown.
