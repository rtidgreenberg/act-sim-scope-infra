#!/usr/bin/env python3
"""Compare cold-start SPDP2/SEDP with three or six topic pairs."""

import argparse
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import time

import rti.connextdds as dds
import rti.types.builtin as builtin

DOMAIN = 102
WINDOW = 3
ROOT = Path(__file__).resolve().parents[2]
FAMILIES = {"Event": "WanEvent", "State": "WanEvent", "Periodic": "WanStatus"}
SEDP_WRITERS = {0x3C2, 0x4C2}


def wait(predicate, label, deadline):
    while time.monotonic() < deadline:
        if predicate():
            return time.monotonic()
        time.sleep(0.002)
    raise RuntimeError(f"Timed out: {label}")


@contextmanager
def capture(path):
    with subprocess.Popen(
        ["dumpcap", "-q", "-i", "lo", "-f", "udp portrange 32900-33149",
         "-a", "duration:10", "-w", str(path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    ) as recorder:
        try:
            wait(lambda: path.exists() and path.stat().st_size > 0, "capture startup",
                 time.monotonic() + 2)
            yield
        finally:
            if recorder.poll() is None:
                recorder.terminate()
            _, log = recorder.communicate(timeout=5)
        if recorder.returncode or not re.search(r"Dropped:\s*0\b|\b0 dropped\b|pcap:0/dumpcap:0", log):
            raise RuntimeError(f"Capture failed or drops unverified: {log}")


def packets(path, start, end, family, display_filter="rtps"):
    fields = ("frame.time_epoch", "udp.length", "ip.len", "ipv6.plen",
              "rtps.sm.wrEntityId", "ipv6.tclass", "udp.dstport")
    command = ["tshark", "-r", str(path), "-Y", display_filter, "-T", "fields"]
    for field in fields:
        command.extend(("-e", field))
    for row in subprocess.check_output(command, text=True).splitlines():
        stamp, udp, v4_len, v6_len, writers, tclass, dst = row.split("\t")
        if start <= float(stamp) < end:
            if bool(v4_len) != (family == "v4") or bool(v6_len) != (family == "v6"):
                raise RuntimeError(f"Unexpected IP family: {row}")
            yield (int(v4_len) if v4_len else int(v6_len) + 40, int(udp) - 8,
                   {int(w, 16) for w in writers.split(",") if w},
                   int(tclass, 16) if tclass else None, int(dst))


def profile(name):
    return f"SPIKE::{name}"


def trial(qos, types, family, count, peers, path):
    endpoints = [(label + suffix, label, kind) for label, kind in FAMILIES.items()
                 for suffix in (("",) if count == 3 else ("High", "Normal"))]
    with ExitStack() as cleanup:
        with capture(path):
            started = time.monotonic()
            wall = time.time()
            participants, writers, readers = [], [], []
            for _ in range(peers):
                p = dds.DomainParticipant(DOMAIN, qos.participant_qos_from_profile(profile(family)))
                cleanup.callback(p.close)
                participants.append(p)
                for label, base, kind in endpoints:
                    topic = dds.DynamicData.Topic(p, label, types.type(kind))
                    reader = dds.DynamicData.DataReader(
                        p.implicit_subscriber, topic, qos.datareader_qos_from_profile(profile(base.lower())))
                    writer = dds.DynamicData.DataWriter(
                        p.implicit_publisher, topic, qos.datawriter_qos_from_profile(profile(base.lower())))
                    p.ignore_datawriter(writer.instance_handle)
                    p.ignore_datareader(reader.instance_handle)
                    writers.append(writer)
                    readers.append(reader)
            deadline = started + WINDOW
            matched = wait(
                lambda: all(len(p.discovered_participants()) == peers - 1 for p in participants)
                and all(len(w.matched_subscriptions) == peers - 1 for w in writers)
                and all(len(r.matched_publications) == peers - 1 for r in readers),
                "all remote endpoint pairs", deadline,
            )
            time.sleep(max(0, deadline - time.monotonic()) + 0.05)
        rows = list(packets(path, wall, wall + WINDOW, family))
    # Odd well-known destination ports (7411 + domain*250 + 2*id) carry
    # user endpoint traffic. No application samples are written in this phase.
    discovery = [row for row in rows if row[4] % 2 == 0]
    sedp = [row for row in discovery if row[2] & SEDP_WRITERS]
    if not sedp or not any(0x10082 in row[2] for row in discovery):
        raise RuntimeError("Missing packet evidence for SPDP2 or SEDP")
    return {
        "family": family, "topics": count, "participants": peers,
        "endpoint_pairs": len(writers), "match_ms": round((matched - started) * 1000, 2),
        "discovery_packets": len(discovery), "discovery_ip_bytes": sum(r[0] for r in discovery),
        "discovery_udp_payload_bytes": sum(r[1] for r in discovery),
        "sedp_packets": len(sedp), "sedp_ip_bytes": sum(r[0] for r in sedp),
        "user_protocol_packets": len(rows) - len(discovery),
    }


def marking_probe(qos, path):
    checks = {}
    with ExitStack() as cleanup:
        participants = []
        for _ in range(2):
            p = dds.DomainParticipant(DOMAIN, qos.participant_qos_from_profile(profile("v6")))
            cleanup.callback(p.close)
            participants.append(p)
        source, sink = participants
        for band, expected in (("normal", 0), ("high", 184)):
            tx = dds.Topic(source, f"Mark{band}", builtin.String)
            rx = dds.Topic(sink, f"Mark{band}", builtin.String)
            writer = dds.DataWriter(
                source.implicit_publisher, tx, qos.datawriter_qos_from_profile(profile(band)))
            reader = dds.DataReader(sink.implicit_subscriber, rx)
            wait(lambda: len(writer.matched_subscriptions) == 1, band, time.monotonic() + 2)
            marker = f"wan-priority-probe-{band}"
            with capture(path):
                writer.write(builtin.String(marker))
                received = []

                def delivered():
                    received.extend(s.data.value for s in reader.take() if s.info.valid)
                    return received == [marker]

                wait(delivered, f"{band} sample delivery", time.monotonic() + 2)
                time.sleep(0.5)
            classes = {r[3] for r in packets(path, 0, float("inf"), "v6",
                                            f'udp contains "{marker}"') if r[4] % 2 == 1}
            path.unlink()
            if classes != {expected}:
                raise RuntimeError(f"{band}: expected traffic class {expected}, observed {classes}")
            checks[band] = {"traffic_class": expected, "dscp": expected >> 2}
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=5, choices=range(1, 21), metavar="N")
    parser.add_argument("--participants", type=int, default=7, choices=(2, 7))
    args = parser.parse_args()
    if {p.name for p in Path("/sys/class/net").iterdir()} != {"lo"}:
        raise RuntimeError("Run only inside the private loopback container")
    qos = dds.QosProvider(str(Path(__file__).with_name("qos.xml")))
    types = dds.QosProvider(str(ROOT / "harness_v2/datamodel/gen/ActTypes.xml"))
    trials = []
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "trial.pcapng"
        cases = [("v4", 3), ("v6", 6), ("v4", 6), ("v6", 3)]
        for rep in range(args.repetitions):
            for family, count in (cases if rep % 2 == 0 else reversed(cases)):
                trials.append(trial(qos, types, family, count, args.participants, path))
                path.unlink()
        marking = marking_probe(qos, path)
    if any(p.name.startswith(("RTI", "dds")) for p in Path("/dev/shm").iterdir()):
        raise RuntimeError("Unexpected DDS shared-memory files")
    print(json.dumps({"image": os.environ["SPIKE_IMAGE_ID"], "window_s": WINDOW,
                      "trials": trials, "ipv6_marking": marking}, indent=2))


if __name__ == "__main__":
    main()
