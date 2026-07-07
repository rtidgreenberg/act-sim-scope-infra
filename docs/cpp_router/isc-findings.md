# Instance State Consistency (ISC) — Investigation & Why We Don't Use It

> **Decision (see [Thesis & Tenets](thesis-and-tenets.md) Tenet 2): ISC is OUT OF SCOPE.**
> This document is the *evidence* for that decision, not a plan to adopt ISC. We investigated
> ISC thoroughly, proved exactly what it can and cannot do across an intermediary, and — given
> the system uses **system-level presence with no WAN-topic liveliness** (Tenet 4) — concluded
> the router should **not** rely on ISC. The relay instead does: **forward data + mirror meta
> samples (dispose/unregister) + presence-driven reset**. Because there is no data-topic
> liveliness, no data-less `ALIVE` recovery ever reaches the relay, so the CORE-13337 gap and
> the read-retain / re-assert machinery below are **moot for the ACT relay** — they are retained
> here only as the record of what ISC-transparency *would* have required, and as reference if a
> future keyed ISC channel is ever wanted.

Consolidated findings from the ISC investigation for the C++ Dynamic DDS Router / Routing
Service replacement. Every behavioral claim here was validated one of two ways:

- **[C]** Connext AI against the RTI Connext DDS Professional **7.7.0** docs/release notes, or
- **[S]** the running spike in [`spikes/isc_recovery/`](../../spikes/isc_recovery/)
  (Connext 7.7.0, Modern C++, single host, UDPv4).

Where a claim earlier in the design was wrong, it is corrected here.

---

## TL;DR

1. Native `RECOVER_INSTANCE_STATE_CONSISTENCY` in 7.7 recovers instance state **only** for the
   *same physical writer* losing and regaining liveliness (Scenario A). **[C][S]**
2. Recovery across a **restarted writer** (new physical GUID, same virtual GUID) is **not**
   shipped in 7.7 (Scenario B); it's the F3 feature. Infrastructure services carry the
   documented limitation **CORE-13337**. **[C]**
3. An intermediary (Routing Service, or our router) that terminates DDS on both legs **cannot
   be ISC-transparent for free**: the leg-1 recovery-to-`ALIVE` arrives as a data-less sample
   the intermediary cannot re-emit downstream. Reproduced in our own relay. **[S]**
4. No application `DataWriter` operation produces a data-less `ALIVE`. `register_instance()`
   does not move a reader to `ALIVE`; only `write()` of a real sample does. **[C][S]**
5. **If ISC-transparency were wanted** (it is not — see banner), the path would be: keep the
   reader at `KEEP_LAST(1)` and, on a leg-1 `ALIVE`-recovery, read the retained last sample and
   `write()` it on the output leg. **[S]** In the actual design this is unnecessary because
   there is no WAN-topic liveliness, so no `ALIVE`-recovery occurs to propagate.
6. Surviving a **router restart** with instance-state intact (Scenario B for our own writer)
   would need durable writer history + stable virtual GUID — also **not** pursued; router
   restart is handled by presence + normal re-forwarding, not ISC.

---

## 1. What native ISC does — and does not — do in 7.7

`RELIABILITY.instance_state_consistency_kind = RECOVER_INSTANCE_STATE_CONSISTENCY` restores a
reader's per-instance state (`ALIVE` / `NOT_ALIVE_DISPOSED` / `NOT_ALIVE_NO_WRITERS`) after a
disconnect, delivered to the app as `valid_data = false` samples, via the built-in
**ServiceRequest** channel. Keyed + `RELIABLE` only.

| Scenario | What happens | 7.7 support | Evidence |
|---|---|---|---|
| **A** — same physical writer loses liveliness, then regains it (link flap, process alive) | reader is driven back to correct state with no new data sample | **Supported** | **[C]**; **[S]** T2: `K1 ALIVE→NO_WRITERS→ALIVE via=STATE`; T5 control (ISC off) leaves it stuck |
| **B** — a *new physical* writer appears with the *same virtual GUID* (restarted from durable writer history) | reader would request a full snapshot from the new writer | **NOT supported** (planned F3 feature) | **[C]**: docs frame recovery only as rediscovery of a formerly-matched writer; no QoS knob for the vGUID case |

Corrections to earlier design notes:
- The SDD phrasing "the reader will put SN 0 in the request" is **not** the literal wire
  mechanism. Recovery is a ServiceRequest-channel query/response, not an SN-0 user-data
  repair. **[C]**
- Scenario B was earlier assumed to work off virtual-GUID continuity. It does not in shipping
  7.7. Virtual GUID + durable writer history give *sample-identity/duplicate-suppression and
  history restoration*, **not** ISC recovery across a new physical identity. **[C]**

### CORE-13337 (infrastructure-services limitation)

Per the 7.7 release notes: `RECOVER_INSTANCE_STATE_CONSISTENCY` is **not fully supported by RTI
Infrastructure Services**. Routing Service **inputs cannot route the `NOT_ALIVE_NO_WRITERS →
ALIVE` transition** after liveliness regain. A Routing Service **output** writer can be
configured to respond to downstream readers, but the input side cannot carry that transition.
Persistence/Queuing/Recording/Replay do not support being configured with it at all. **[C]**

This is not specific to RTI's implementation — it is intrinsic to *any* intermediary that
terminates DDS on both legs (see §3).

## 2. QoS required for native ISC (Scenario A)

Both DataReader **and** DataWriter:
- `reliability.kind = RELIABLE`
- `reliability.instance_state_consistency_kind = RECOVER_INSTANCE_STATE_CONSISTENCY`
  (a RECOVER reader only does ISC with RECOVER writers)

DataReader additionally:
- `reader_resource_limits.keep_minimum_state_for_instances = true` (default)
- `protocol.propagate_dispose_of_unregistered_instances = true` — **required** to recover
  `NOT_ALIVE_DISPOSED` from `NOT_ALIVE_NO_WRITERS`; **not** a default

Also: keyed types only; ServiceRequest builtin channel enabled (default); if
`DESTINATION_ORDER = BY_SOURCE_TIMESTAMP`, RECOVER is limited to INSTANCE scope. **[C]**

Reference profile: [`spikes/isc_recovery/qos_isc_recovery.xml`](../../spikes/isc_recovery/qos_isc_recovery.xml),
and the proven relay profile [`relay/cpp/qos_isc.xml`](../../relay/qos_isc.xml).

## 3. The intermediary gap (why a relay/router can't be transparent for free)

Topology: `origin(dom A) → [ relay: reader → mirror → writer ](A→B) → downstream reader(dom B)`.
Native ISC runs *per leg*. The relay must make the two legs behave as one hop.

When the origin loses liveliness and later regains it:
1. The relay's **leg-1 reader** goes `NOT_ALIVE_NO_WRITERS`, then — because it's a RECOVER
   reader — is recovered to `ALIVE` by native ISC, delivered as an **invalid sample**
   (`valid_data = false`, no payload). **[S]**
2. To be transparent, the mirror had already **`unregister_instance()`** on the relay writer
   when leg 1 went `NO_WRITERS`, so downstream also went `NO_WRITERS`.
3. The leg-1 recovery carries **no data** to forward, and the naive mirror has **no case** for
   "invalid sample reporting `ALIVE`" — so it does nothing, and **downstream stays stuck at
   `NOT_ALIVE_NO_WRITERS`**. Reproduced: **[S]** R3.

The downstream reader **cannot** pull `ALIVE` from the relay writer via native leg-2 ISC:
- The relay writer **unregistered** the instance, so it holds no `ALIVE` state to serve — ISC
  only replays the writer's *current* held state. **[C]**
- The relay process stayed up (asserting liveliness), so the downstream reader had **no
  liveliness-regain event** to trigger an ISC request on leg 2. **[S] reasoning**

The one theoretical alternative — don't unregister, and instead gate the relay writer's
liveliness per-instance so leg-2 native ISC does a data-less recovery — **does not
generalize**: liveliness is a per-writer property, not per-instance, so one writer carrying
many keys from independent origins cannot drop liveliness for one key without flapping all of
them.

## 4. Why the fix must be `write()` of a real value

- **No data-less `ALIVE` from an application writer.** Confirmed: the only `valid_data = false`
  transitions an application can emit are `dispose_instance` (→ DISPOSED) and
  `unregister_instance` (→ NO_WRITERS). `register_instance()` does **not** move a reader to
  `ALIVE`. The only data-less `ALIVE` is the middleware's internal ISC recovery, which an
  application cannot synthesize. **[C]**
- **An empty/key-only sample is mechanically enough but semantically wrong.** Any sample with
  the correct key flips the reader to `ALIVE`, but it is delivered as `valid_data = true` and,
  on a `KEEP_LAST(1)` state topic, becomes the instance's *current value* — overwriting real
  state with garbage. So the sample must carry the **correct current value**. **[C] reasoning**

Therefore, to bring a downstream reader instance back to `ALIVE`, the relay must `write()` the
instance's real current value. This is the irreducible application-level work — exactly what
the F3 IST feature does natively inside Routing Service.

## 5. Recommended path forward

**On a leg-1 `ALIVE`-recovery, read the reader's retained `KEEP_LAST(1)` last sample for that
instance and `write()` it on the output leg to re-assert a live writer downstream.**

- Keep the relay's input reader at `RELIABLE + RECOVER + TRANSIENT_LOCAL + KEEP_LAST(1)`. The
  reader's own history is then the source of truth for "last value per instance" — **no
  separate application cache needed.**
- Forward new data by reading `NOT_READ` samples (so each new sample forwards once) while the
  last sample per instance remains resident in the reader's `KEEP_LAST(1)` cache, readable
  later. Mirror `dispose`/`unregister` as today (by recovered key/handle).
- When an invalid sample reports the instance back to `ALIVE`, fetch the retained value with
  `reader.select().instance(handle).read()` and `write()` it on the output writer. Downstream
  returns to `ALIVE` (delivered as `via=DATA` — the real value re-asserted). Proven: **[S]** R2.

This supersedes the interim approach in the spike's `state_relay.cxx`, which keeps a parallel
`std::map<key, last-value>`. Both work (R2 passes with the map); the read-retain form is
preferred because it avoids duplicating state the middleware already holds and keeps a single
source of truth.

Residual limit (both forms): an instance recovered to `ALIVE` that the relay never held a
value for cannot be re-asserted — logged and skipped, same class as the `key_value()`
recovery caveat in [`relay/cpp/isc_relay.cxx`](../../relay/cpp/isc_relay.cxx). With
`TRANSIENT_LOCAL` the value normally arrives alongside, so this is an edge, not the common
case.

## 6. Router restart (Scenario B for our own writer) — deferred

Everything above keeps state correct **while the relay stays up**. Surviving a **router
process restart** is a separate problem and needs, on the output writer:
- **durable writer history** (7.7: SQLite via `DURABILITY.storage_settings`, file-based — not
  the deprecated ODBC path), plus
- a **stable virtual GUID**, so downstream readers reconcile after the writer resumes.

Even then, native Scenario-B recovery is not shipped (§1), so downstream recovery after a
router restart rides on **durability replay** plus the same read-retain/re-write reconciliation
on the restarted relay's input side. This is deferred to spike Phase 2 and must run entirely on
a local filesystem (SQLite is unsafe on the vboxsf share — see
[`.github/copilot-instructions.md`](../../.github/copilot-instructions.md)).

Layering of the "last value per key":

| Layer | Where | Survives router restart | Covers |
|---|---|---|---|
| reader `KEEP_LAST(1)` (read-retain) | middleware, RAM | No | live re-assert while relay is up |
| durable writer history | middleware, SQLite on disk | Yes | router restart |

## 7. Evidence (spike `spikes/isc_recovery/`, 7/7 PASS)

| Test | Proves |
|---|---|
| T0 | durable last-value + dispose delivered to a late joiner |
| T1 | a writer whose instances were set via `write`/`dispose`/`unregister` serves a fresh reader correctly (mirror substitution valid) |
| **T2** | **native ISC Scenario A**: liveliness regain restores `ALIVE via=STATE` (no data) |
| T5 | control: with ISC off, liveliness regain does **not** restore state — attributes T2 to RECOVER |
| R1 | relay propagates instance state end-to-end across both legs |
| **R3** | **the gap**: naive mirror leaves downstream stuck `NO_WRITERS` after an origin blip (CORE-13337 reproduced) |
| **R2** | **the fix**: re-write on leg-1 `ALIVE`-recovery routes recovery end-to-end |

## 8. Consequences for the router design

- For **state-critical keyed routes**, the router is **not** a stateless forwarder. Its mirror
  must translate leg-1 instance-state observations — including ISC recoveries to `ALIVE` —
  into imperative output-writer calls (`write` re-assert / `dispose` / `unregister`), sourcing
  the re-assert value from the reader's retained `KEEP_LAST(1)` sample.
- Route config must force keyed state routes onto `dynamic_data` / `generated_type` forwarding
  (not `serialized_cdr`) so keys and values are accessible for this.
- Bulk unkeyed routes are unaffected — no instances, nothing to recover.
- ISC transparency across a **router restart** is a distinct, larger commitment (durable
  writer history) and should be scoped explicitly, not assumed.
