#!/usr/bin/env python3
"""Link-probe spike — validate the Phase 9 RTT probe's app-ack mechanism (D81 item 4).

Claims under test (see PLAN.md):
  A. CADENCE + ATTRIBUTION: under the full RouterLinkProbe QoS (RELIABLE +
     APPLICATION_AUTO acks, VOLATILE, KEEP_LAST(1), fixed send window with a piggyback
     HB per sample, zero reader heartbeat-response delay, no liveliness) app-acks fire
     per-sample per-peer at ~1 Hz, and each ack's subscription_handle resolves through
     matched_subscription_participant_data() to the acknowledging peer's D74/D79
     participant name — the discovery-DB attribution Phase 9 is built on.
  B. RTT PLAUSIBILITY: ack_time - send_time per (seq, peer) is positive and small on lo
     (the app-level RTT: wire + peer middleware + peer take/return-loan).
  C. RETENTION (the unvalidated KEEP_LAST(1) x app-ack interaction that rejected
     RouterHealth reuse): a matched reader that never takes must not block write() at
     probe cadence — KEEP_LAST(1) replacement proceeds despite "retain until fully
     ACKed" — and when it finally takes it sees/acks only the NEWEST sample. Whatever
     the middleware does about replaced-never-taken samples (silent drop vs. an ack
     with no response data) is recorded as a finding either way.

Peers are subprocesses (`peer` subcommand) — real processes, real loopback UDP.
UDPv4-only everywhere (nothing can leak into /dev/shm). The probe type is a programmatic
dds.StructType mirror of link-health.md's RouterLinkProbe sketch.

Exit 0 = all parts pass. Run:  python3 spikes/link_probe/link_probe_spike.py [domain]
"""

import argparse
import subprocess
import sys
import threading
import time
import os

os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

import rti.connextdds as dds  # noqa: E402

PROBE_TOPIC = "RouterLinkProbeSpike"
DEFAULT_DOMAIN = 3210  # low-thousands rule (repo guardrails)

PROBE_PERIOD_S = 1.0
N_PROBES = 10
# Peer C holds (no takes) through the whole burst, then takes once.
HOLD_S = N_PROBES * PROBE_PERIOD_S + 2.0

PROBER = "ProberNode/router"
PEER_A = "PeerA_1/router"
PEER_B = "PeerB_2/router"
PEER_C = "PeerC_3/router"


class SpikeError(AssertionError):
    pass


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- type (sketch mirror)
def probe_type():
    t = dds.StructType("RouterLinkProbe")
    t.add_member(dds.Member("router", dds.StringType(128), is_key=True))
    t.add_member(dds.Member("probe_seq", dds.Uint64Type()))
    t.add_member(dds.Member("send_timestamp", dds.Int64Type()))
    return t


# ------------------------------------------------------------------- entity builders
def make_participant(domain, name):
    """UDPv4-only participant wearing the D74 '<node>/<router>' EntityName."""
    q = dds.DomainParticipantQos()
    q.transport_builtin = dds.TransportBuiltin.udpv4
    en = q.participant_name
    en.name = name
    en.role_name = "act.router"
    q.participant_name = en
    return dds.DomainParticipant(domain, q)


def probe_writer_qos():
    """The full RouterLinkProbe writer stack (link-health.md sketch / D81 item 4)."""
    q = dds.DataWriterQos()
    rel = dds.Reliability(kind=dds.ReliabilityKind.RELIABLE)
    rel.acknowledgment_kind = dds.AcknowledgmentKind.APPLICATION_AUTO
    q.reliability = rel
    q.durability = dds.Durability(kind=dds.DurabilityKind.VOLATILE)
    q.history = dds.History(dds.HistoryKind.KEEP_LAST, 1)
    # Fixed send window with heartbeats_per_max_samples == send_window_size: a
    # piggyback HB rides every sample (the RTI low-latency snippet pattern, sized for
    # the probe's KEEP_LAST(1) world).
    proto = q.protocol
    rw = proto.rtps_reliable_writer
    rw.min_send_window_size = 1
    rw.max_send_window_size = 1
    rw.heartbeats_per_max_samples = 1
    proto.rtps_reliable_writer = rw
    q.protocol = proto
    # No liveliness settings: presence stays RouterHealth's job (default AUTOMATIC +
    # infinite lease = no liveliness traffic).
    return q


def probe_reader_qos():
    q = dds.DataReaderQos()
    rel = dds.Reliability(kind=dds.ReliabilityKind.RELIABLE)
    rel.acknowledgment_kind = dds.AcknowledgmentKind.APPLICATION_AUTO
    q.reliability = rel
    q.durability = dds.Durability(kind=dds.DurabilityKind.VOLATILE)
    q.history = dds.History(dds.HistoryKind.KEEP_LAST, 1)
    # Zero ACK delay on the probe reader ONLY (the 0..0.5 s default jitter would swamp
    # the measurement; data-plane readers keep the jitter against ACK implosion).
    proto = q.protocol
    rr = proto.rtps_reliable_reader
    rr.min_heartbeat_response_delay = dds.Duration.zero
    rr.max_heartbeat_response_delay = dds.Duration.zero
    proto.rtps_reliable_reader = rr
    q.protocol = proto
    return q


# --------------------------------------------------------------------------- the peers
def run_peer(args):
    """A probe-reader peer. mode=take: take immediately on data for the whole run.
    mode=hold: match but take nothing for HOLD_S, then take ONCE, report, linger."""
    participant = make_participant(args.domain, args.name)
    topic = dds.DynamicData.Topic(participant, PROBE_TOPIC, probe_type())
    reader = dds.DynamicData.DataReader(
        dds.Subscriber(participant), topic, probe_reader_qos())

    waitset = dds.WaitSet()
    cond = dds.ReadCondition(reader, dds.DataState.any)
    waitset += cond

    print("READY", flush=True)
    end = time.monotonic() + args.run_s
    if args.mode == "take":
        while time.monotonic() < end:
            waitset.wait(dds.Duration.from_milliseconds(200))
            for _ in reader.take():   # take + implicit return-loan -> AppAck
                pass
    else:  # hold
        hold_end = time.monotonic() + HOLD_S
        while time.monotonic() < hold_end:
            time.sleep(0.1)           # matched, receiving, never taking
        got = [(int(s.data["probe_seq"]), s.info.valid) for s in reader.take()]
        print(f"HOLD_TOOK {got}", flush=True)
        while time.monotonic() < end:  # linger so the post-take AppAck flies
            time.sleep(0.1)
    return 0


# -------------------------------------------------------------------------- the prober
class AckRecorder(dds.DynamicData.NoOpDataWriterListener):
    """D81 item 3's containment, mirrored: installed on the probe writer alone with
    exactly the app-ack mask; the callback does the minimum (clock read + append under
    a mutex) — the real collector drains such an accumulator on its own tick."""

    def __init__(self):
        super().__init__()
        self.mutex = threading.Lock()
        self.acks = []  # (recv_monotonic, subscription_handle_str, seq)

    def on_application_acknowledgment(self, writer, info):
        now = time.monotonic()
        seq = int(info.sample_identity.sequence_number.value)
        with self.mutex:
            self.acks.append((now, str(info.subscription_handle), seq))


def run_prober(args):
    here = os.path.abspath(__file__)

    def spawn(name, mode, run_s):
        return subprocess.Popen(
            [sys.executable, here, "peer", "--domain", str(args.domain),
             "--name", name, "--mode", mode, "--run-s", str(run_s)],
            stdout=subprocess.PIPE, text=True)

    run_s = HOLD_S + 8.0
    peers = {
        PEER_A: spawn(PEER_A, "take", run_s),
        PEER_B: spawn(PEER_B, "take", run_s),
        PEER_C: spawn(PEER_C, "hold", run_s),
    }
    participant = None
    failures = []
    try:
        for name, proc in peers.items():
            line = proc.stdout.readline().strip()
            if line != "READY":
                raise SpikeError(f"peer {name} failed to start: {line!r}")
        log("peers ready")

        participant = make_participant(args.domain, PROBER)
        topic = dds.DynamicData.Topic(participant, PROBE_TOPIC, probe_type())
        recorder = AckRecorder()
        writer = dds.DynamicData.DataWriter(
            dds.Publisher(participant), topic, probe_writer_qos(), recorder,
            dds.StatusMask.DATAWRITER_APPLICATION_ACKNOWLEDGMENT)

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if writer.publication_matched_status.current_count == 3:
                break
            time.sleep(0.1)
        else:
            raise SpikeError("probe writer never matched all 3 peer readers")

        # Resolve subscription handle -> peer participant name NOW (D81 item 1's
        # attribution path; also proves the call against live handles).
        handle_to_name = {}
        for handle in writer.matched_subscriptions:
            pdata = writer.matched_subscription_participant_data(handle)
            handle_to_name[str(handle)] = pdata.participant_name.name
        log(f"matched peers: {sorted(handle_to_name.values())}")
        if sorted(handle_to_name.values()) != sorted([PEER_A, PEER_B, PEER_C]):
            raise SpikeError(f"attribution mismatch: {handle_to_name}")

        # ---- the 1 Hz probe burst -------------------------------------------------
        beat = dds.DynamicData(probe_type())
        beat["router"] = PROBER
        send_time = {}     # RTPS sample sequence number -> send monotonic
        write_stall = 0.0  # worst write() latency (part C: must never block)
        for i in range(N_PROBES):
            beat["probe_seq"] = i
            beat["send_timestamp"] = time.time_ns()
            t0 = time.monotonic()
            writer.write(beat)
            dt = time.monotonic() - t0
            write_stall = max(write_stall, dt)
            # The RTPS seq an ack's sample_identity carries is the middleware-assigned
            # one, not derived from probe_seq — read it back off the writer instead of
            # assuming a fixed i->seq offset (last_available_sample_sequence_number
            # reflects the sample this write() call just produced).
            rtps_seq = writer.datawriter_protocol_status \
                .last_available_sample_sequence_number.value
            send_time[rtps_seq] = t0
            time.sleep(PROBE_PERIOD_S)

        log(f"burst done; worst write() latency {write_stall*1000:.2f} ms")
        # Let peer C's hold expire + its single take's ack arrive.
        time.sleep(HOLD_S + 4.0 - N_PROBES * PROBE_PERIOD_S)

        with recorder.mutex:
            acks = list(recorder.acks)

        by_peer = {}  # peer name -> {rtps_seq: rtt}
        unknown = []
        for recv, handle, seq in acks:
            name = handle_to_name.get(handle)
            if name is None or seq not in send_time:
                unknown.append((handle, seq))
                continue
            by_peer.setdefault(name, {})[seq] = recv - send_time[seq]
        log(f"acks: total={len(acks)} per-peer counts="
            f"{ {n: len(v) for n, v in by_peer.items()} } unknown={unknown}")

        # ---- Part A: cadence + attribution ---------------------------------------
        for name in (PEER_A, PEER_B):
            n = len(by_peer.get(name, {}))
            if n < N_PROBES - 2:
                failures.append(f"A: {name} acked only {n}/{N_PROBES} probes")
        log(f"[A] per-sample per-peer acks + name attribution "
            f"{'PASS' if not failures else 'FAIL'}")

        # ---- Part B: RTT plausibility on lo --------------------------------------
        for name in (PEER_A, PEER_B):
            rtts = sorted(by_peer.get(name, {}).values())
            if not rtts:
                failures.append(f"B: no RTTs for {name}")
                continue
            mean = sum(rtts) / len(rtts)
            log(f"[B] {name}: rtt min/mean/max = "
                f"{rtts[0]*1000:.2f}/{mean*1000:.2f}/{rtts[-1]*1000:.2f} ms "
                f"(n={len(rtts)})")
            if rtts[0] <= 0:
                failures.append(f"B: non-positive RTT for {name}: {rtts[0]}")
            if mean > 0.25:
                failures.append(f"B: mean RTT implausible on lo: {mean:.3f}s ({name})")

        # ---- Part C: KEEP_LAST(1) x app-ack retention -----------------------------
        if write_stall > 0.5:
            failures.append(
                f"C: write() blocked {write_stall:.2f}s behind the non-taking peer")
        c_acks = by_peer.get(PEER_C, {})
        last_seq = max(send_time)  # RTPS seq of the newest probe (measured, not assumed)
        stale = [s for s in c_acks if s != last_seq]
        # The claim: after the single take, the NEWEST sample gets acked; replaced
        # never-taken samples must not produce a stale-ack backlog. (An ack for an
        # early seq that was in C's KEEP_LAST(1) cache exactly when replaced-out is
        # middleware-internal drop handling — record it; a BACKLOG is the failure.)
        if last_seq not in c_acks:
            failures.append(f"C: newest probe (rtps seq {last_seq}) never acked by "
                            f"the holding peer after its take: {sorted(c_acks)}")
        if len(stale) > 2:
            failures.append(f"C: stale-ack backlog from the holding peer: {sorted(stale)}")
        log(f"[C] holding-peer acks by rtps seq: {sorted(c_acks)} "
            f"(newest={last_seq}, worst write stall {write_stall*1000:.2f} ms) "
            f"{'PASS' if not any(f.startswith('C:') for f in failures) else 'FAIL'}")
        c_out = peers[PEER_C].stdout.readline().strip()
        log(f"[C] holding peer take result: {c_out}")
        if "HOLD_TOOK" in c_out:
            took = eval(c_out.split(" ", 1)[1])  # [(seq, valid)] from our own peer
            valid_seqs = [s for s, v in took if v]
            if len(valid_seqs) != 1 or valid_seqs[0] != N_PROBES - 1:
                failures.append(
                    f"C: KEEP_LAST(1) hold-take expected exactly newest probe_seq "
                    f"{N_PROBES - 1}, got {took}")
        else:
            failures.append(f"C: holding peer never reported its take: {c_out!r}")
    finally:
        for proc in peers.values():
            if proc.poll() is None:
                proc.terminate()
        for proc in peers.values():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if participant is not None:
            participant.close()

    if failures:
        for f in failures:
            log(f"FAIL {f}")
        return 1
    log("ALL PARTS PASS (A cadence+attribution, B rtt, C retention)")
    return 0


def main():
    # Manual dispatch: `peer ...` is the subprocess entry; anything else is the prober
    # with an optional positional domain (a bare subparser would swallow the domain).
    if len(sys.argv) > 1 and sys.argv[1] == "peer":
        peer = argparse.ArgumentParser()
        peer.add_argument("--domain", type=int, required=True)
        peer.add_argument("--name", required=True)
        peer.add_argument("--mode", choices=["take", "hold"], required=True)
        peer.add_argument("--run-s", type=float, required=True)
        return run_peer(peer.parse_args(sys.argv[2:]))
    parser = argparse.ArgumentParser()
    parser.add_argument("domain", nargs="?", type=int, default=DEFAULT_DOMAIN)
    return run_prober(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
