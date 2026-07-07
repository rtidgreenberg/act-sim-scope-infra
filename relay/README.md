# relay/ — Python ISC relay · Phase 1 PoC (M0 go/no-go gate)

> Part of the ACT EMANE plan — see [roadmap.md](../docs/roadmap.md) **Phase 1** and
> [product-gaps.md](../docs/product-gaps.md) **LP-1**.

De-risks the linchpin of the transparent-relay strategy **before** building the
environment around it:

> **Does a pure-Python, ISC-enabled DP-to-DP relay preserve true DDS `instance_state`
> per key across a disconnection — reproducing what a *direct* pair of ISC endpoints
> does, where Routing Service does not?**

Instance State Consistency (ISC, `instance_state_consistency_kind=RECOVER_STATE`,
production since Connext 7.3) recovers a reader's instance-state transitions
(DISPOSED / NO_WRITERS) after a disconnection — but **it is not carried across Routing
Service** (LP-1). The workaround is a standalone, ISC-enabled **DataParticipant-to-
DataParticipant relay** that mirrors its reader's instance lifecycle onto its writer, so
each leg is a genuine pair of matched ISC endpoints and true `instance_state` converges
end-to-end.

Needs only `rti.connextdds` on a plain network — **no containers / EMANE / ACT stack.**

## Files

| File | What |
|---|---|
| [act_state.py](act_state.py) | Keyed `@idl.struct` `ActState` + the **ISC QoS builders** (single source of truth) + `resolve_key_id` helper |
| [isc_relay.py](isc_relay.py) | The relay: ISC `DataReader` (leg 1) + ISC `DataWriter` (leg 2); mirrors write / dispose / unregister |
| [poc_test.py](poc_test.py) | The go/no-go test: paths A (direct) · C (relay) · B (Routing Service) × fault matrix × convergence assertion |
| [poc_netem.py](poc_netem.py) | **netem transport-partition variant** — relay transparency across a real (packet-loss) outage, root-free via `unshare -rn` |
| [isc_isolation.py](isc_isolation.py) | **ISC isolation control** — proves `RECOVER_STATE` recovers instance state that plain RELIABLE+durability cannot (network dropout + liveliness expiration + unchanged instance) |
| [qos_isc.xml](qos_isc.xml) | Equivalent ISC QoS as an **XML profile** (the roadmap's "QoS API or XML fallback"); also loaded by RS |
| [rs_config.xml](rs_config.xml) | Routing Service config for **path B** (bridges domains 13→14) |

## The relay's mirroring rule

```
reader sample valid           →  writer.write(sample)              # forward, key preserved 1:1
reader NOT_ALIVE_DISPOSED      →  writer.dispose_instance(handle)
reader NOT_ALIVE_NO_WRITERS    →  writer.unregister_instance(handle)
```

Native ISC runs **per leg** (source-writer↔relay-reader, relay-writer↔dest-reader); the
relay stitches the two legs into one transparent hop.

## ISC QoS (verified against RTI Connext AI + the 7.6 runtime)

Both endpoints: `RELIABLE` + `instance_state_consistency_kind = RECOVER_STATE` +
`TRANSIENT_LOCAL` + `KEEP_LAST(1)`. Plus:

- **writer** `writer_data_lifecycle.autodispose_unregistered_instances = False` — so
  `unregister` yields **NO_WRITERS**, not DISPOSED (the two transitions must stay distinct).
- **writer** `data_writer_protocol.serialize_key_with_dispose = True` — so a reader can
  recover an instance's **key from a dispose-only sample** it never saw alive.
- **reader** `data_reader_resource_limits.keep_minimum_state_for_instances = True` and
  `data_reader_protocol.propagate_dispose_of_unregistered_instances = True` — highest
  instance-state recovery fidelity.

> Note: the Python 7.6 binding spells the ISC enum `RECOVER_STATE`; the XML/C++ token is
> `RECOVER_INSTANCE_STATE_CONSISTENCY`. Both map to the same feature.

## Running it

```bash
export RTI_LICENSE_FILE=/home/rti/rti_connext_dds-7.6.0/rti_license.dat   # your license
cd relay
python3 poc_test.py
```

Paths A and C run in-process. **Path B** additionally needs a live Routing Service
(otherwise it is reported *skipped* and the go/no-go still stands on A vs C):

```bash
export NDDSHOME=/home/rti/rti_connext_dds-7.6.0
export RTI_LICENSE_FILE=$NDDSHOME/rti_license.dat
export NDDS_QOS_PROFILES=$PWD/qos_isc.xml
$NDDSHOME/bin/rtiroutingservice -cfgFile rs_config.xml -cfgName ActStateBridge &
python3 poc_test.py
```

The relay is also runnable standalone:

```bash
python3 isc_relay.py --upstream-domain 11 --downstream-domain 12 --topic ActState -v
```

**netem transport-partition variant** (recovers NO_WRITERS across a real outage; root-free):

```bash
export RTI_LICENSE_FILE=/home/rti/rti_connext_dds-7.6.0/rti_license.dat
unshare -rn python3 poc_netem.py
```

`unshare -rn` puts the whole test in an unprivileged user+network namespace where it is
mapped to root, so it can drive `tc`/netem on that namespace's **own** loopback — a clean
transport partition of the test's DDS traffic, touching nothing on the host.

## Test design — two scenarios, one assertion

Three paths, and the assertion is always **C must match A key-for-key** (A = ground
truth); B (RS) is expected to diverge (the LP-1 gap):

- **A (direct)** source writer → dest reader (no gateway) — ISC baseline == ground truth
- **C (relay)** source → **Python ISC relay** → dest — the proof
- **B (RS)** source → **Routing Service** → dest — expected to diverge

Per-key fault matrix: **K1** dispose → DISPOSED · **K2** unregister → NO_WRITERS · **K3**
dispose/re-write/dispose → DISPOSED (multi-transition replay) · **K4** untouched → ALIVE.

**Scenario 1 — connected transparency.** Dest reader stays matched throughout. Proves the
relay mirrors every transition live, **including NO_WRITERS**.

**Scenario 2 — disconnect / reconnect recovery.** The *same* dest-reader entity is
unmatched (its Subscriber is moved to an isolated PARTITION), the transitions happen while
it is gone, then it rematches and ISC must recover them. Proves **DISPOSED + replay +
ALIVE** recovery across a disconnection.

## Result (localhost, Connext 7.6)

```
Scenario 1 (connected transparency, incl NO_WRITERS): PASS   C == A on K1,K2,K3,K4
Scenario 2 (disconnect/reconnect recovery) .........: PASS   C == A on K1,K3,K4
VERDICT: ✅ GO
```

**netem transport-partition variant** (`poc_netem.py`) — closes the Scenario-2 caveat:

```
transport blackout (netem loss 100%) during transitions:
  C matches A on K1,K2,K3,K4 ..................... YES ✅
  NO_WRITERS (K2) recovered across the outage ... YES ✅
VERDICT: ✅ GO — relay reproduces direct-ISC across a real transport partition,
                 INCLUDING NOT_ALIVE_NO_WRITERS recovery.
```

Because the outage is a *packet drop* (not an endpoint unmatch) shorter than the discovery
lease, the reader stays matched and ISC reconciles the missed NO_WRITERS on restoration —
the case bare-partition-toggle can't reach.

### Isolating ISC (`isc_isolation.py`) — proving RECOVER_STATE ≠ NONE

The transparency tests above (`poc_test.py`, `poc_netem.py`) show the relay reproduces the
direct baseline (C == A) — but on localhost with RELIABLE, ordinary reliable retransmission
and TRANSIENT_LOCAL durable replay *already* recover instance state on reconnect, so those
tests do **not** isolate ISC's own contribution (ISC on == off in all of them). To prove
ISC does something nothing else can, `isc_isolation.py` removes every other recovery path:

- a real **netem network dropout** (packets stop) — *not* a discovery unmatch;
- **liveliness expiration** (MANUAL_BY_TOPIC, short lease) so the reader marks the instance
  NO_WRITERS while staying matched — the precondition ISC reconciles from;
- an **unchanged** instance (no dispose/re-write) → nothing for reliable to retransmit;
- **VOLATILE** durability → nothing to replay;
- liveliness regained via `assert_liveliness()` (no data sample).

```
ISC ON :  ALIVE -> (dropout) NO_WRITERS -> (restore) ALIVE        <- recovered via ISC ServiceRequest
ISC OFF:  ALIVE -> (dropout) NO_WRITERS -> (restore) NO_WRITERS   <- stuck; no sample ever comes
RESULT: ✅ ISC ISOLATED
```

This is the localhost analogue of the RF-link-drop convergence the relay must provide at
scale, and the one test that attributes recovery **specifically** to Instance State
Consistency rather than to reliability or durability.

**Bonus artifact (B vs C).** With RS live, path B **drops K2 (NO_WRITERS → absent)** in
Scenario 1 where the relay preserves it — "RS defeats ISC; the relay restores it," made
concrete and reproducible.

## Exit criterion (go/no-go) — MET ✅

Path C reproduces the direct-ISC baseline (Path A) for lifecycle mirroring and
disconnect recovery → **the transparent-relay strategy is validated; proceed.** The
relay is pure Python ⇒ user-inspectable/modifiable (answers the "RS is a black box"
feedback), ready to harden and integrate in **Phase 8**.

## Known limitations / deferred to later phases

- **ISC vs plain reliability.** The transparency tests (`poc_test.py`, `poc_netem.py`)
  prove C == A, but do **not** isolate ISC — on localhost with RELIABLE, reliable
  retransmission + TRANSIENT_LOCAL replay already recover instance state, so ISC on == off
  there. `isc_isolation.py` isolates it (see above): ISC's distinct contract is recovering
  an instance's current state on **liveliness loss → regain** with **no sample to deliver**
  — the one thing reliability/durability cannot do. The trigger is a *liveliness* event,
  not a discovery unmatch (a partition-toggle is the wrong model and discards the state ISC
  needs — confirmed with RTI Connext AI).
- **Relay behavior under liveliness recovery — validate in Phase 8.** `isc_isolation.py`
  exercises a *direct* pair. The relay's pump is `take()`-driven, so an ISC current-state
  reconcile that arrives **without a data sample** (e.g. NO_WRITERS → ALIVE on the upstream
  leg) may not generate a takeable event for the relay to mirror onto leg 2. Whether the
  relay faithfully propagates sample-less ISC recoveries end-to-end is an open item for the
  Phase 8 hardening (matched-QoS + DynamicData) and scenario S8 at RF scale.
- **Metadata is not preserved end-to-end.** The relay writer re-stamps `source_timestamp`
  and issues new sample identity (it is the publisher of record downstream). Fine for
  instance-state fidelity in this topology; revisit for Phase 8 if source provenance,
  `BY_SOURCE_TIMESTAMP` ordering, or exclusive ownership matter.
- **DynamicData.** The PoC uses a concrete keyed type; the real keyed state topics use
  DynamicData in Phase 8.
- **`_key_cache` is unbounded** — fine for the PoC's few keys; add terminal-instance
  cleanup for a long-running deployment.
```
