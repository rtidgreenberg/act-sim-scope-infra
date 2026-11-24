#!/usr/bin/env python3
"""Compare seven native WAN participants before/after one-second startup spreading."""

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET

import rti.connextdds as dds

from startup import HERE, REPORTS, CAPTURES, wait
from wire import capture_rows

ROOT = HERE.parents[1]
WRITERS = ("000003c2", "000004c2", "00010182")


def trial(provider, name, spread):
    profile = ("ACT_QOS_LIB::wan_three_topic_participant" if spread else
               "PREEMPTIVE_SPIKE::immediate_discovery")
    qos = provider.participant_qos_from_profile(profile)
    capture = CAPTURES / (name + ".pcapng")
    capture.write_bytes(b"")
    capture.chmod(0o666)
    created = {}
    log_path = REPORTS / (name + "_capture.log")
    with log_path.open("w") as log:
        with subprocess.Popen(
            ["dumpcap", "-i", "lo", "-f", "udp portrange 32900-33149",
             "-a", "duration:9", "-w", str(capture)],
            stdout=log, stderr=subprocess.STDOUT) as recorder:
            wait(lambda: capture.stat().st_size > 0, "capture ready")
            with ExitStack() as cleanup:
                participants = []
                origin = time.monotonic()
                for slot in range(7):
                    target = origin + slot / 6 if spread else origin
                    time.sleep(max(0, target - time.monotonic()))
                    participant = dds.DomainParticipant(102, qos)
                    cleanup.callback(participant.close)
                    participants.append(participant)
                    prefix = str(participant.instance_handle).replace(":", "").replace("-", "").lower()[:24]
                    created[prefix] = time.monotonic() - origin
                    effective = ET.fromstring(participant.qos.to_string(print_all=True))
                    for reader in ("publication_reader", "subscription_reader", "participant_configuration_reader"):
                        actual = effective.find("./discovery_config/" + reader)
                        assert actual.findtext("min_heartbeat_response_delay/sec") == "0"
                        assert actual.findtext("max_heartbeat_response_delay/sec") == ("1" if spread else "0")
                    (REPORTS / (name + "_effective_qos.xml")).write_text(
                        participant.qos.to_string(print_all=True))
                wait(lambda: all(len(p.discovered_participants()) == 6 for p in participants),
                     "all seven participants mutually discovered")
                time.sleep(2)
                end = time.time()
        assert recorder.returncode == 0
    assert "pcap:0/dumpcap:0" in log_path.read_text()
    capture.chmod(0o644)
    rows = [r for r in capture_rows(capture) if r["prefix"] in created and r["time"] <= end]
    samples = []
    for ack in rows:
        if (ack["kind"] != "ACKNACK" or ack["writer"] not in WRITERS
                or ack["destination"] not in created or ack["base"] == 0):
            continue
        heartbeats = [h for h in rows if h["kind"] == "HEARTBEAT"
                      and h["prefix"] == ack["destination"] and h["writer"] == ack["writer"]
                      and h["destination"] in (None, "0" * 24, ack["prefix"])
                      and h["time"] <= ack["time"]]
        if heartbeats:
            samples.append({"writer": ack["writer"], "gap_sec": ack["time"] - heartbeats[-1]["time"]})
    assert samples, "No heartbeat-to-ACKNACK observations"
    gaps = [s["gap_sec"] for s in samples]
    span = max(created.values()) - min(created.values())
    if spread:
        assert 0.9 <= span <= 1.3, span
        assert max(gaps) > 0.5, max(gaps)
    result = {"variant": name, "created_sec": created, "creation_span_sec": span,
              "mutual_discovery": True, "heartbeat_to_ack_samples": samples,
              "gap_min_sec": min(gaps), "gap_max_sec": max(gaps),
              "gap_bins_100ms": [sum(i / 10 <= x < (i + 1) / 10 for x in gaps) for i in range(10)],
              "capture_sha256": hashlib.sha256(capture.read_bytes()).hexdigest()}
    print(json.dumps({k: v for k, v in result.items() if k != "heartbeat_to_ack_samples"}), flush=True)
    return result


def main():
    report = {"status": "FAIL", "trials": []}
    try:
        assert {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}
        for index in range(1, 12):
            os.environ["WAN_PEER" + (str(index) if index > 1 else "")] = "[0-15]@builtin.udpv4://127.0.0.1"
        paths = [HERE / p for p in ("optimized.py", "startup.py", "wire.py", "qos.xml")]
        paths.append(ROOT / "harness_v2/qos/act_qos_profiles.xml")
        report["source_hashes"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        with tempfile.TemporaryDirectory(prefix="startup-spread-") as directory:
            combined = ET.parse(ROOT / "harness_v2/qos/act_qos_profiles.xml")
            combined.getroot().extend(ET.parse(HERE / "qos.xml").getroot().findall("qos_library"))
            path = Path(directory) / "combined.xml"
            combined.write(path)
            provider = dds.QosProvider(str(path))
            report["trials"] = [trial(provider, "immediate", False), trial(provider, "spread", True)]
        assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
                   for p, h in report["source_hashes"].items())
        assert not [p for p in Path("/dev/shm").iterdir() if p.name.startswith(("RTI", "dds"))]
        report["status"] = "COMPLETE"
    except Exception:
        report["error"] = traceback.format_exc()
        print(report["error"], file=sys.stderr)
    finally:
        (REPORTS / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())
