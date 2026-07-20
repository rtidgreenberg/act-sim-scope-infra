# Link-probe spike — app-ack RTT mechanism for Phase 9 (D81 gate)

**Status: PASSED 3/3 consecutive runs (2026-07-17, domains 3210/3212/3213), `/dev/shm`
clean.** The gate on Phase 9's `LinkStatsCollector` RTT probe is cleared; the pinned
ReadCondition echo fallback (D81 item 3) is **not needed**.

## What ran

One prober + three peer subprocesses (`link_probe_spike.py`, UDPv4-only on loopback),
probe writer under the full `RouterLinkProbe` QoS stack from
[link-health.md](../../docs/cpp_router/link-health.md): `RELIABLE` with
`acknowledgment_kind = APPLICATION_AUTO`, `VOLATILE`, `KEEP_LAST(1)`, fixed send window
`min == max == 1` with `heartbeats_per_max_samples == 1` (piggyback HB per sample), zero
`heartbeat_response_delay` on the probe readers, no liveliness. The app-ack listener is
installed on the probe writer alone with exactly the
`DATAWRITER_APPLICATION_ACKNOWLEDGMENT` mask (D81 item 3's containment, mirrored); the
callback only stamps a clock and appends under a mutex.

```
python3 spikes/link_probe/link_probe_spike.py [domain]   # default 3210, exit 0 = pass
```

## Results

- **A — cadence + attribution PASS:** both taking peers acked **10/10** probes at 1 Hz;
  every ack's `AcknowledgmentInfo.subscription_handle` resolved via
  `matched_subscription_participant_data()` → `participant_name.name` to the correct
  D74/D79 peer name (`PeerA_1/router`, `PeerB_2/router`, `PeerC_3/router`) — the
  discovery-DB attribution D81 item 1 builds on, proven against live handles.
- **B — RTT plausibility PASS:** per-sample app-level RTT on `lo` across the three runs:
  min ≈ 0.7–1.0 ms, mean ≈ 2.7–4.6 ms, max ≈ 11–22 ms (n=10 per peer per run). Positive,
  ms-scale, usable as a signal; the first ack of a run is the slow one (discovery-warm
  path), steady-state sits ~1–3 ms.
- **C — `KEEP_LAST(1)` × app-ack retention PASS** (the interaction that rejected
  `RouterHealth` reuse, validated for the probe): with a matched reader **never taking**
  through the whole 10-sample burst, `write()` at 1 Hz never blocked (worst observed
  write latency **0.26–2.09 ms** across runs) — `KEEP_LAST(1)` replacement proceeds
  despite "retain until fully ACKed". When the holding peer finally took, it received
  **exactly one** sample (the newest, `probe_seq 9`) and produced **exactly one** ack
  (RTPS seq 10 = newest).

## Findings (behavioral, feed the Phase 9 implementation)

1. **Replaced-never-taken samples produce NO app-ack.** The holding peer emitted zero
   acks for the nine samples that were replaced out of its `KEEP_LAST(1)` cache before
   being taken — no stale-ack backlog, no ack-with-no-response-data noise. Clean for RTT
   use: an ack always corresponds to a sample the peer actually consumed, so a missing
   ack per interval means "peer not consuming" (slow/held), not an error path the
   collector must filter.
2. **`AcknowledgmentInfo.sample_identity.sequence_number` is the RTPS sequence number,
   1-based** — probe `probe_seq i` acks as sequence `i + 1`. The collector's send-time
   join must key by RTPS seq (record it at `write()` time), not by payload `probe_seq`.
3. **AppAck latency is take-driven.** With `APPLICATION_AUTO`, the ack fires on
   take + return-loan; the measured RTT genuinely includes peer middleware + take, as
   link-health.md describes. A peer that takes lazily inflates RTT rather than dropping
   the ack — consistent with treating RTT as "app-level" health, but worth remembering
   when the collector's numbers look slow: check the peer's take cadence before blaming
   the link.
4. The full QoS stack (including `min == max == 1` send window with per-sample piggyback
   HB) is accepted by 7.7 with no QoS-consistency complaints, and the whole surface is
   drivable from `rti.connextdds` — the Phase 9 e2e (`test_link_stats.py`) can assert the
   probe from Python without C++ test scaffolding.

## Files

- `PLAN.md` — claims, method, fallback.
- `link_probe_spike.py` — prober + `peer` subcommand (subprocess peers). No runtime
  artifacts are written (stdout only; nothing touches the share).
