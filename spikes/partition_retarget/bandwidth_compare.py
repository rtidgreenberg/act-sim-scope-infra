#!/usr/bin/env python3
"""Wire-level bandwidth comparison: plain SPDP (default assert period), SPDP fast-assert
(200ms, the Part E knob), and SPDP2 (Part F) -- steady-state bytes/sec while idle-matched,
and total bytes during a participant_partition retarget event.

Follow-up to the timing-only Parts C/D/E/F: those measured how FAST each mechanism
converges; this measures the actual wire cost, since "fast" via a short
participant_liveliness_assert_period buys speed at the cost of continuously resending the
full periodic SPDP announcement, while SPDP2 keeps steady-state traffic small and pays for
speed with an event-driven (not periodic) reliable configuration message instead.

Captures on loopback with `dumpcap` (works without sudo here: dumpcap has
cap_net_raw/cap_net_admin and this user is in the `wireshark` group -- verify with
`getcap $(which dumpcap)` if that's not true on another machine). Traffic is attributed to
our two test participants via the RTPS GuidPrefix -- a message-header field (one value per
packet, not per-submessage), so summing by sender GuidPrefix cannot double-count a packet.

Exit 0 = ran to completion; this is a measurement script, not a pass/fail spike. Run:
  python3 spikes/partition_retarget/bandwidth_compare.py [rep]
"""

import os
import subprocess
import sys
import time

os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

import rti.connextdds as dds  # noqa: E402
import partition_retarget_spike as prs  # noqa: E402

IDLE_WINDOW_S = 8.0   # >= the 2.0s SPDP2 settle (Part F), so its handshake completes inside
PCAP_DIR = "/tmp"


def guid_prefix_hex(participant):
    """First 12 bytes (24 hex chars) of the GUID -- the GuidPrefix, one value per RTPS
    message header (not per-submessage), so it's safe for whole-packet attribution."""
    return str(participant.instance_handle)[:24].lower()


def start_capture(path):
    proc = subprocess.Popen(["dumpcap", "-i", "lo", "-w", path, "-q"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.4)  # let dumpcap actually attach before traffic starts
    return proc


def stop_capture(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    time.sleep(0.2)  # let the pcap file flush


def parse_pcap(path, prefixes):
    """Return [(time_epoch, frame_len), ...] for packets whose RTPS sender GuidPrefix
    matches one of ours -- i.e. traffic OUR two participants transmitted (no double count:
    each packet has exactly one sender)."""
    cmd = ["tshark", "-r", path, "-Y", "rtps", "-T", "fields",
           "-e", "frame.time_epoch", "-e", "frame.len", "-e", "rtps.guidPrefix.src",
           "-E", "separator=|", "-E", "occurrence=f"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        t, length, src = parts[0], parts[1], parts[2]
        src_hex = src.replace(":", "").lower()
        if not t or not length or not any(src_hex.startswith(p) for p in prefixes):
            continue
        rows.append((float(t), int(length)))
    return rows


def bytes_in_window(rows, t0, t1):
    return sum(n for t, n in rows if t0 <= t <= t1)


def run_scenario(name, base, spdp2=False, fast_assert=False, settle_s=0.0):
    pcap_path = f"{PCAP_DIR}/bw_{name}.pcap"
    cap = start_capture(pcap_path)
    try:
        pa = prs.make_participant(base, participant_partition="SHARED",
                                   fast_assert=fast_assert, spdp2=spdp2)
        pb = prs.make_participant(base, participant_partition="SHARED",
                                   fast_assert=fast_assert, spdp2=spdp2)
        prefixes = [guid_prefix_hex(pa), guid_prefix_hex(pb)]
        try:
            ta = dds.DynamicData.Topic(pa, prs.TOPIC, prs.data_type())
            tb = dds.DynamicData.Topic(pb, prs.TOPIC, prs.data_type())
            pub, writer = prs.make_writer(pa, ta, publisher_partition="SHARED")
            sub, reader = prs.make_reader(pb, tb, subscriber_partition="SHARED")

            elapsed = prs.wait_matched(lambda: len(reader.matched_publications))
            if elapsed is None:
                raise prs.SpikeError(f"[{name}] baseline never matched")
            print(f"  baseline matched in {prs.fmt(elapsed)}")

            # Idle window: nothing changes. >= settle_s so SPDP2's one-time post-match
            # handshake (Part F) has time to complete inside it, not during the retarget.
            t_idle_start = time.time()
            time.sleep(IDLE_WINDOW_S)
            t_idle_end = time.time()

            t_retarget_start = time.time()
            prs.set_participant_partition(pa, "MISMATCH")
            prs.track_transitions({
                "unmatch": lambda: len(writer.matched_subscriptions) == 0,
            }, timeout_s=prs.UNMATCH_TIMEOUT_S)
            prs.set_participant_partition(pa, "SHARED")
            back = prs.track_transitions({
                "local_rematch": lambda: len(writer.matched_subscriptions) >= 1,
                "remote_rematch": lambda: len(reader.matched_publications) >= 1,
            }, timeout_s=prs.REMATCH_TIMEOUT_S)
            t_retarget_end = time.time()
            print(f"  retarget event took {prs.fmt(back['remote_rematch'])} "
                  f"(remote rematch)")
        finally:
            pa.close()
            pb.close()
    finally:
        stop_capture(cap)

    rows = parse_pcap(pcap_path, prefixes)
    idle_bytes = bytes_in_window(rows, t_idle_start, t_idle_end)
    retarget_bytes = bytes_in_window(rows, t_retarget_start, t_retarget_end)
    idle_bps = idle_bytes / (t_idle_end - t_idle_start)
    return {
        "idle_bytes_per_sec": idle_bps,
        "idle_window_bytes": idle_bytes,
        "idle_window_s": t_idle_end - t_idle_start,
        "retarget_bytes": retarget_bytes,
        "retarget_window_s": t_retarget_end - t_retarget_start,
    }


def main():
    rep = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    off = rep * 100
    scenarios = [
        ("spdp_default", dict(base=5000 + off)),
        ("spdp_fast200", dict(base=5010 + off, fast_assert=True)),
        ("spdp2", dict(base=5020 + off, spdp2=True, settle_s=2.0)),
    ]
    results = {}
    for name, kwargs in scenarios:
        print(f"=== {name} (rep {rep}) ===")
        results[name] = run_scenario(name, **kwargs)
        r = results[name]
        print(f"  idle: {r['idle_bytes_per_sec']:.0f} B/s over {r['idle_window_s']:.1f}s "
              f"({r['idle_window_bytes']} bytes total)")
        print(f"  retarget event: {r['retarget_bytes']} bytes over "
              f"{r['retarget_window_s']*1000:.0f}ms")
        print()

    print("=== Summary ===")
    print(f"{'scenario':16}{'idle B/s':>12}{'retarget bytes':>18}")
    for name, _ in scenarios:
        r = results[name]
        print(f"{name:16}{r['idle_bytes_per_sec']:>12.0f}{r['retarget_bytes']:>18}")


if __name__ == "__main__":
    sys.exit(main())
