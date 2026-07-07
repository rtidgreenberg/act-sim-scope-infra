# ISC recovery spike — Scenario A vs Scenario B

Empirical proof of which instance-state recoveries Connext 7.7 gives for free vs which the
application must implement. See [PLAN.md](PLAN.md) for the full rationale and test matrix.

## Build

```bash
export NDDSHOME=/home/rti/rti_connext_dds-7.7.0
cmake -B build -DCONNEXTDDS_ARCH=x64Linux4gcc7.3.0
cmake --build build
```

Produces `build/state_writer` and `build/state_reader` (type generated from `IscState.idl`).

## Run (Phase 1 — relay / native ISC, no durable writer history)

```bash
export NDDSHOME=/home/rti/rti_connext_dds-7.7.0
./run_tests.sh [base_domain]        # default base domain 50
```

The runner puts all working files (FIFOs, logs) under a local `/tmp/isc_recovery_run.*`
dir — **never** the VirtualBox shared folder, which cannot host FIFOs or SQLite safely
(see `.github/copilot-instructions.md`). QoS is UDPv4-only so nothing leaks `/dev/shm`.

## Manual / interactive use

```bash
# terminal 1
build/state_reader --domain 42 --qos-file qos_isc_recovery.xml --profile Recovery::Recover
# terminal 2 — commands on stdin: write K [payload] | dispose K | unregister K | pause | resume | quit
build/state_writer --domain 42 --qos-file qos_isc_recovery.xml --profile Recovery::Recover
```

The reader tags every transition with **how it arrived**:
- `via=DATA(seq=N)` — a real (re)written / replayed data sample.
- `via=STATE` — an invalid sample (state change only, no data): a dispose/no-writers change,
  or a **native ISC recovery**.

`pause`/`resume` drop and regain liveliness **without killing the writer** (manual liveliness,
short lease) — that is the Scenario A trigger.

## Phase 1 results (validated 2026-07-07, Connext 7.7.0)

| Test | Result | Observed |
|---|---|---|
| T0 sanity | PASS | late joiner sees `K1=ALIVE` (durable replay), `K2=NOT_ALIVE_DISPOSED` |
| T1 imperative-mirror thesis | PASS | a fresh reader converges to states the writer set via `write`/`dispose` calls (`A=ALIVE via=DATA`, `B=DISPOSED via=STATE`) |
| **T2 Scenario A** | **PASS** | `K1 ALIVE → (pause) NO_WRITERS via=STATE → (resume, no write) ALIVE via=STATE` — **native ISC recovers instance state with no data sample** |
| T5 NoRecover control | PASS | with ISC off, liveliness regain does **not** restore `K1` (stays `NO_WRITERS`) — proves T2's recovery is attributable to `RECOVER`, not durability |

### What this establishes

1. **Native `RECOVER_INSTANCE_STATE_CONSISTENCY` works for Scenario A** (same physical writer
   loses and regains liveliness): the reader is driven back to `ALIVE` via a state-only sample,
   no data replay needed. T5 confirms it's the feature doing it, not plain durability.
2. **The imperative-mirror substitution is valid**: a writer whose instances were set purely by
   `write`/`dispose`/`unregister` (exactly what the router's mirror would call) serves those
   states correctly to a newly-joining reader. This is what lets a custom router stand in for
   the missing Scenario B in application code.

Note on T1 key `C` (unregister): a `NOT_ALIVE_NO_WRITERS`-only instance the reader never saw
alive is not delivered to a late joiner (no key to recover) — consistent with the
`relay/cpp/isc_relay.cxx` key-recovery caveat. Not a failure.

## Relay-chain results (the router leg: reader → mirror → writer → downstream reader)

`state_relay` puts the mirror between an origin writer and a downstream reader:
`origin(dom A) → [relay reader → mirror → relay writer](A→B) → downstream reader(dom B)`.

| Test | Result | Observed downstream K1 |
|---|---|---|
| R1 relay basic | PASS | `K1=ALIVE`, `K2=ALIVE→DISPOSED` — instance state relays across both legs |
| **R3 gap** (relay `--no-reassert`) | **PASS (gap reproduced)** | `ALIVE → NOT_ALIVE_NO_WRITERS` and **stuck** after origin liveliness blip |
| **R2 fix** (relay reasserts) | **PASS** | `ALIVE → NO_WRITERS → ALIVE via=DATA` — recovered |

### The key finding

The naive mirror in `relay/cpp/isc_relay.cxx` forwards valid data and mirrors
dispose/unregister, but has **no case for an invalid sample that reports the instance back
to `ALIVE`** — which is exactly how leg-1 native ISC recovery is delivered. So when the
origin loses and regains liveliness, the relay drives downstream to `NOT_ALIVE_NO_WRITERS`
and **never brings it back** (R3). This is the CORE-13337 intermediary limitation,
reproduced in our own relay.

The fix (R2): the relay caches the last value per key and, when leg-1 reports the instance
`ALIVE` again, **re-writes** that value on the output leg. `register_instance()` alone does
**not** work (Connext-confirmed; a reader only returns to `ALIVE` on a written sample), so
the mirror must `write()`. With that, leg-1 ISC recovery is routed end-to-end and the
downstream reader returns to `ALIVE` (delivered as `via=DATA`, since the relay re-wrote).

**Consequence for the router plan:** an ISC-transparent relay is not just "run a reader and a
writer with RECOVER QoS." The mirror must actively translate leg-1 instance-state
observations (including ISC recoveries to `ALIVE`) into imperative writer calls on leg 2.
This is the app-level work the F3 IST feature does natively in Routing Service.

### Recommended path forward (supersedes the map cache in `state_relay.cxx`)

Keep the relay's input reader at `KEEP_LAST(1)` and use its own retained last sample as the
value source — **no separate application cache**. On a leg-1 `ALIVE`-recovery, read the
reader's retained sample for that instance and `write()` it on the output leg to re-assert a
live writer:

```
forward new data:   read NOT_READ samples -> write() on output   (last sample stays in reader KEEP_LAST(1))
dispose/unregister:  mirror by recovered key/handle
leg-1 ALIVE again:   reader.select().instance(handle).read() -> write() that value on output
```

`state_relay.cxx` currently keeps a parallel `std::map<key,last-value>` — both pass R2, but the
read-retain form is preferred (single source of truth, no duplicated state). Full rationale and
all findings: [`docs/cpp_router/isc-findings.md`](../../docs/cpp_router/isc-findings.md).

## Multi-hop chain (`run_2hop.sh`) — the ACT topology

`origin(D0) → relay(D0→D1) → relay(D1→D2) → reader(D2)` — two intermediaries, the real ACT
shape (platform-node router + control-node router). Chain length is just the `DOMS` array, so
the same runner extends to N hops. No new binary — `state_relay` is reused per hop.

```bash
NDDSHOME=/home/rti/rti_connext_dds-7.7.0 ./run_2hop.sh [base_domain]
```

Results (3/3 PASS):

| Check | Result |
|---|---|
| H1 — K1 `ALIVE` and K2 `DISPOSED` relayed **end-to-end across 2 hops** | PASS |
| H2 — origin pause → downstream `NO_WRITERS`; origin resume → **downstream `ALIVE` again** | PASS |

**Key composition insight:** on H2 the downstream reader recovered as `ALIVE via=DATA`. The
hop-1 relay turns the leg-1 data-less ISC recovery into an ordinary **written** sample; hop 2
then sees normal data and forwards it without needing any special ISC-recovery handling. So
the `ALIVE`-reassert logic is only required at the hop **adjacent to the recovering writer** —
downstream hops just relay data. Instance-state transparency composes across the chain.

## Phase 2 (deferred — NOT in this runner)

Scenario B — writer **process restart** with **durable writer history** (new physical GUID,
same virtual GUID) — requires SQLite DWH files and `SIGKILL`, so it must be set up on a local
fs with a dedicated runner. Prediction to test: native ISC does **not** recover on discovery of
the restarted writer; state returns only via durability replay, or stays stuck
`NOT_ALIVE_NO_WRITERS` if the replayed sample is suppressed as a virtual-GUID duplicate
(the CORE-3018 / CORE-13337 shape). Do not enable until Phase 1 stays green.

## Files

- `PLAN.md` — rationale, full A/B matrix, pass/fail criteria.
- `IscState.idl` — keyed test type.
- `state_writer.cxx` / `state_reader.cxx` — the rig.
- `qos_isc_recovery.xml` — `Recover` / `NoRecover` profiles (no DWH in Phase 1).
- `run_tests.sh` — Phase-1 orchestration (local-fs, UDPv4-only).
