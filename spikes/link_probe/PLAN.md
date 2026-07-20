# Spike: RouterLinkProbe app-ack RTT mechanism (gates Phase 9 — D81 item 4)

## Question

Phase 9's RTT probe (D14/D81, [link-health.md](../../docs/cpp_router/link-health.md))
rides `APPLICATION_AUTO` app-acks on a dedicated `RouterLinkProbe` topic under a specific
QoS stack. Three things are unvalidated on this install and must be shown before the
`LinkStatsCollector` is coded against them:

1. **App-acks fire per-peer at ~1 Hz under the full probe QoS** — `RELIABLE` with
   `acknowledgment_kind = APPLICATION_AUTO`, `VOLATILE`, `KEEP_LAST(1)`, fixed send
   window with `heartbeats_per_max_samples == send_window_size` (piggyback HB per
   sample), zero `heartbeat_response_delay` on the probe readers, no liveliness — and
   the `on_application_acknowledgment` listener attributes each ack to the acknowledging
   peer via `AcknowledgmentInfo.subscription_handle()` →
   `matched_subscription_participant_data()` → `participant_name` (the D74/D79 name).
2. **RTTs on `lo` are plausible** — positive, sub-second, stable enough to be a signal
   (the app-level RTT includes wire + peer middleware + peer take/return-loan).
3. **`KEEP_LAST(1)` × app-ack retention is sane** — the interaction link-health.md
   flagged as unvalidated when it rejected `RouterHealth` as the probe carrier: app-ack
   extends writer retention to "fully ACKed", so does `KEEP_LAST(1)` replacement still
   proceed when a matched reader sits on samples without taking them? `write()` at probe
   cadence must never block/throw behind a non-taking peer, and when that peer finally
   takes, it must see (and ack) only the newest sample — no stale-ack backlog.

**Fallback if disproven (pinned in D81):** a probe/echo topic pair measured entirely
with `ReadCondition`s (waitset-pure; costs a responder in every router and a second
topic).

## Method

One Python file (`link_probe_spike.py`), peers as **subprocesses** (real processes, real
loopback UDP; UDPv4-only transport so nothing can leak into `/dev/shm`). The Python
binding exposes the whole surface (verified by `dir()` on this install:
`Reliability.acknowledgment_kind`, `rtps_reliable_writer.heartbeats_per_max_samples` /
`min/max_send_window_size`, `rtps_reliable_reader.min/max_heartbeat_response_delay`,
`NoOpDataWriterListener.on_application_acknowledgment`, `AcknowledgmentInfo`
{`subscription_handle`, `sample_identity`}, `matched_subscription_participant_data`), so
no C++ pair is needed.

- **Prober** (main process): participant named `ProberNode/router` (D74 shape), probe
  writer with the full QoS stack above, app-ack listener installed with exactly the
  `DATAWRITER_APPLICATION_ACKNOWLEDGMENT` mask (D81 item 3's containment, mirrored). The
  callback does the minimum the real collector will do: clock read + append
  `(subscription_handle, sequence_number, recv_time)` to a mutex-guarded list.
- **Peers A and B** (`PeerA_1/router`, `PeerB_2/router`): probe readers (matching QoS,
  zero HB response delay) on a WaitSet; take immediately on data — the ~1 Hz ack sources.
- **Peer C** (`PeerC_3/router`): same reader QoS but **holds** (never takes) for the
  whole write burst, then takes once and lingers — the retention check.

Prober writes `probe_seq = 0..9` at 1 Hz (send times recorded per seq against the same
monotonic clock the listener stamps acks with), then joins the peers and evaluates:

- **A (cadence + attribution):** ≥ 8 of 10 seqs acked by BOTH A's and B's distinct
  subscription handles; each handle resolves via
  `matched_subscription_participant_data().participant_name.name` to the right peer name.
- **B (RTT):** per-ack RTT = ack_time − send_time[seq]; assert every RTT > 0 and
  mean < 250 ms (expect ms-scale on `lo`); report min/mean/max per peer.
- **C (retention):** no write in the 1 Hz burst ever blocks > 500 ms or throws while C
  withholds takes; C produces no acks during the hold **for samples it never took** (any
  middleware-generated "dropped sample" acks are recorded and reported — that behavior is
  itself a finding); after C's single take, an ack for the **newest** seq arrives and the
  take yields exactly the one newest sample (`KEEP_LAST(1)`).

Domain id from argv (default 3210 — low-thousands rule). Runtime artifacts: none written
(stdout only); nothing touches the share.

## Result

**PASSED 3/3 consecutive runs (2026-07-17; domains 3210/3212/3213; `/dev/shm` clean; one
harness fix between run 1 and 2: argparse dispatch, no behavior change).** All three
claims held: 10/10 acks per taking peer at 1 Hz with exact name attribution; RTTs
min ≈ 0.7–1.0 ms / mean ≈ 2.7–4.6 ms / max ≈ 11–22 ms on `lo`; `write()` never blocked
behind the non-taking peer (worst 2.09 ms) and the hold-take yielded exactly the newest
sample with exactly one ack. Key finding: replaced-never-taken samples produce **no**
app-ack (no stale backlog to filter). Numbers + implementation notes in
[README.md](README.md). The ReadCondition echo fallback is not needed.
