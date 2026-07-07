# Spike: Instance-State Recovery — Scenario A vs Scenario B

## Purpose

Empirically settle the single load-bearing question under the C++ router / RS-replacement
plan: **which instance-state recoveries does shipping Connext 7.7 give us for free, and
which must the application implement itself?**

Connext AI (validated against the 7.7.0 docs and release notes) says:

- **Scenario A** — the *same physical* DataWriter loses liveliness and later regains it
  (transient disconnect, process never died): native `RECOVER_INSTANCE_STATE_CONSISTENCY`
  recovers reader instance state over the ServiceRequest channel. **Supported in 7.7.**
- **Scenario B** — a *new physical* DataWriter appears with a **different physical GUID but
  the same virtual GUID** (a process restarted from durable writer history): the reader
  requesting a full instance-state snapshot from it is **NOT a documented/supported feature
  in 7.7** (CORE-13337 for infrastructure services; no QoS knob for the general case). This
  is the feature the F3 SDD proposes to build.

If that boundary holds, then for a state-critical route through the router:

- transient link flaps recover for free (Scenario A);
- **any process restart** (origin app, router itself) does **not** recover via native ISC —
  it must ride on **durability replay** (durable writer history + `TRANSIENT_LOCAL`) and/or
  the router's **imperative mirror** (explicit `write`/`dispose`/`unregister`).

This spike proves or refutes that with running code before it shapes the roadmap.

## Secondary thesis

Confirm that a DataWriter whose instance states were set **imperatively** (via
`write()` / `dispose_instance()` / `unregister_instance()` — i.e. exactly the calls the
router's mirror makes) serves those states correctly to a reconnecting / late-joining
reader, the same as a writer driven by ordinary application traffic. This is what lets the
router substitute an imperative mirror for the missing Scenario B.

## Non-goals

- Not building the router. This is a 2–3 process DDS test rig only.
- Not testing WAN transports, partitions, or the ACT types. Uses one keyed test type.
- Not proving performance. Correctness of instance-state transitions only.

## Test type

`IscState` — minimal keyed type (`@key string key_id; int64 seq; string payload;`),
mirroring the `relay/cpp/ActState.idl` shape so the rig reuses the proven Modern C++ paths.

## Components

| File | Role |
|---|---|
| `IscState.idl` | keyed test type |
| `state_writer.cxx` | stdin-driven writer: `write K`, `dispose K`, `unregister K`, `pause`, `resume`, `quit`. Manual liveliness so `pause`/`resume` drop/regain liveliness **without** killing the process (drives Scenario A). Durable writer history enabled so a process kill + restart resumes the **same virtual GUID** (drives Scenario B). |
| `state_reader.cxx` | prints every instance-state transition as `key -> ALIVE/NOT_ALIVE_DISPOSED/NOT_ALIVE_NO_WRITERS` with a monotonic tick, and prints a full per-key snapshot on `SIGUSR1`. Runs until signalled. |
| `qos_isc_recovery.xml` | profiles: `Recovery::Recover` (RELIABLE + RECOVER + TRANSIENT_LOCAL + KEEP_LAST(1) + durable writer history + manual liveliness + reader recovery knobs) and `Recovery::NoRecover` (identical but `instance_state_consistency_kind = NONE`) as a control. |
| `CMakeLists.txt` | builds both binaries; codegen from IDL at build time (no committed generated code). |
| `run_tests.sh` | orchestrates the scenarios, captures reader output, asserts expected states, prints PASS/FAIL. |
| `README.md` | build + run instructions and interpretation. |

All three processes run on one host, isolated by **domain id** (default writer↔reader on the
same domain; the runner sets `--domain`). Liveliness lease is short (≈2 s) so pause/kill is
observable within the test's time budget.

## How each scenario is induced on a single host

- **Scenario A (same physical writer, liveliness regain):** MANUAL_BY_TOPIC liveliness. The
  writer's `pause` stops asserting liveliness; after the lease expires the reader sees the
  instance go `NOT_ALIVE_NO_WRITERS`. `resume` re-asserts (and re-writes) — **same process,
  same physical GUID**. Native ISC should restore state.
- **Scenario B (new physical writer, same virtual GUID):** kill the writer process (SIGKILL),
  then restart the *same binary/QoS*. Durable writer history restores the virtual GUID from
  its SQLite file → **new physical GUID, same virtual GUID**. The reader (kept alive) newly
  discovers this writer.

## Phasing

- **Phase 1 (this run):** relay / native-ISC behavior only — **no durable writer history**
  and **no process kills**. Tests T0, T1, T2, T5. Safe to run; all runtime files on local
  `/tmp` (the vboxsf share cannot host FIFOs or SQLite safely — see the repo copilot
  instructions).
- **Phase 2 (deferred, separate runner):** Scenario B (T3, T4) — writer process restart with
  durable writer history. Only set up after Phase 1 is clean, and only on a local fs.

## Test matrix and pass/fail

| # | Scenario | Steps | Expected (thesis holds) | What a failure would mean |
|---|---|---|---|---|
| **T0** | Sanity | writer `write K1`, `write K2`, `dispose K2`; fresh reader joins | reader shows `K1=ALIVE`, `K2=NOT_ALIVE_DISPOSED` (durable replay to late joiner) | basic keyed ISC/durability broken — stop and fix env |
| **T1** | Imperative-mirror thesis | writer sets a scripted mix via the mirror-style calls (`write`, `dispose`, `unregister`); then a **new reader** joins | new reader converges to the exact per-key states the writer set imperatively | writer's instance-state table not served correctly ⇒ mirror substitution invalid |
| **T2** | **Scenario A** | reader up + synced; writer `pause` (lose liveliness) → reader sees `NO_WRITERS`; writer `resume` | reader returns to correct state (`ALIVE`/`DISPOSED`) via **native ISC**, ideally before a fresh data sample | if it only recovers on the re-written sample, ISC-A isn't doing the work |
| **T3** | **Scenario B** | reader up + synced; **SIGKILL** writer; restart writer (DWH restore, same virtual GUID) | Prediction: native ISC does **NOT** synthesize recovery on discovery of the new physical writer; state returns only via **durability replay** of the restored history / a re-write | if the reader recovers with **no** replayed sample, Scenario B *is* supported after all — a finding that changes the plan |
| **T4** | Scenario B, no-DWH control | as T3 but writer QoS has DWH disabled | restarted writer has a **new** virtual GUID; reader treats it as a brand-new source | isolates DWH's role in virtual-GUID continuity |
| **T5** | NoRecover control | rerun T2 with `Recovery::NoRecover` | reader does **not** recover on liveliness regain until a new sample arrives | confirms T2's recovery is attributable to RECOVER, not durability alone |

"Recovered via ISC" vs "recovered via replayed sample" is distinguished by watching whether
the reader's transition arrives as an **invalid sample** (`valid_data=false`, instance-state
change only) versus a **valid data sample** with a new `seq`. The reader logs both, so the
runner can tell them apart.

## Relay-chain tests (added: the actual router leg under test)

`state_relay.cxx` is the mirror between two legs:
`origin(dom A) → [relay reader → mirror → relay writer](A→B) → downstream reader(dom B)`.
These prove instance state relayed from the relay's *reader* reaches its *writer* and the
downstream reader — the question T1 did not cover.

| # | Test | Steps | Expected |
|---|---|---|---|
| **R1** | relay basic | origin writes K1, writes+disposes K2 | downstream sees K1=ALIVE, K2=DISPOSED |
| **R3** | gap (relay `--no-reassert`) | origin K1 ALIVE → pause → resume | downstream stuck NOT_ALIVE_NO_WRITERS (naive mirror can't route leg-1 ISC ALIVE-recovery — CORE-13337) |
| **R2** | fix (relay reasserts) | same as R3 with reassert on | downstream returns to ALIVE (relay re-writes cached value on leg-1 ISC recovery) |

Finding: an ISC-transparent relay must translate leg-1 instance-state observations — including
ISC recoveries back to `ALIVE` — into imperative `write`/`dispose`/`unregister` calls on
leg 2, with a per-key last-value cache. `register_instance()` alone will not drive `ALIVE`;
only `write()` does.

## Deliverable

A PASS/FAIL table over T0–T5 and a one-paragraph verdict:

1. Scenario A confirmed supported (or not).
2. Scenario B confirmed unsupported in 7.7 (or a surprise if supported).
3. Imperative-mirror thesis confirmed (or not).
4. Consequence for the router plan: which recoveries need durable writer history + the
   imperative mirror, and which come free from the middleware.

That verdict feeds directly into the `docs/cpp_router` instance-state section and settles
whether the roadmap needs durable writer history for router restarts.

## Risk / honesty notes

- Single-host liveliness timing can be flaky; the runner uses generous sleeps relative to the
  liveliness lease and re-checks state rather than asserting on a single instant.
- If durable-writer-history XML (`durability.storage_settings`) does not validate/behave as
  expected in 7.7, that itself is a finding (it would mean DWH is harder than assumed) and is
  reported rather than worked around.
- `key_value()` recovery for dispose-only samples depends on `serialize_key_with_dispose` and
  `keep_minimum_state_for_instances`, both already proven in `relay/cpp/isc_relay.cxx`.
