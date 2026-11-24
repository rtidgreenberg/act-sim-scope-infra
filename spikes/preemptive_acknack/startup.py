#!/usr/bin/env python3
"""Two Python participants: SPDP versus SPDP2, both with SEDP."""

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

import rti.connextdds as dds

from wire import capture_rows, is_builtin_writer

HERE = Path(__file__).resolve().parent
REPORTS, CAPTURES = Path("/reports"), Path("/captures")
PROPERTY = "dds.participant.inter_participant_data_reader.protocol.disable_preemptive_nack"


def wait(predicate, label):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out: " + label)


def trial(profile, repetition):
    stem = f"r{repetition}_{profile}"
    capture = CAPTURES / (stem + ".pcapng")
    capture.write_bytes(b"")
    capture.chmod(0o666)
    provider = dds.QosProvider(str(HERE / "startup_qos.xml"))
    qos = provider.participant_qos_from_profile("STARTUP::" + profile)
    plugin = (dds.DiscoveryConfigBuiltinPluginKindMask.SPDP2 if profile.startswith("spdp2")
              else dds.DiscoveryConfigBuiltinPluginKindMask.SPDP)
    expected_plugins = plugin | dds.DiscoveryConfigBuiltinPluginKindMask.SEDP
    expected_property = "true" if profile.endswith("_on") else "false"
    properties, prefixes = [], set()
    log_path = REPORTS / (stem + "_capture.log")
    with log_path.open("w") as log:
        with subprocess.Popen(
            ["dumpcap", "-i", "lo", "-f", "udp portrange 32900-33149",
             "-a", "duration:8", "-w", str(capture)],
            stdout=log, stderr=subprocess.STDOUT) as recorder:
            wait(lambda: capture.stat().st_size > 0, "capture ready")
            with ExitStack() as cleanup:
                participants = []
                for index in range(2):
                    participant = dds.DomainParticipant(102, qos)
                    cleanup.callback(participant.close)
                    participants.append(participant)
                    actual = participant.qos
                    assert actual.discovery_config.builtin_discovery_plugins == expected_plugins
                    assert actual.transport_builtin.mask == dds.TransportBuiltinMask.UDPv4
                    values = dict(actual.property.get_all())
                    assert values[PROPERTY] == expected_property
                    properties.append(values)
                    prefixes.add(str(participant.instance_handle).replace(":", "").replace("-", "").lower()[:24])
                    if index == 0:
                        time.sleep(1)
                wait(lambda: all(len(p.discovered_participants()) == 1 for p in participants),
                     "mutual participant discovery")
                time.sleep(3)
        assert recorder.returncode == 0, "Capture failed"
    assert "pcap:0/dumpcap:0" in log_path.read_text()
    capture.chmod(0o644)
    rows = capture_rows(capture)
    acks = [r for r in rows if r["kind"] == "ACKNACK" and is_builtin_writer(r["writer"])
            and r["prefix"] in prefixes and r["destination"] in prefixes
            and r["prefix"] != r["destination"]]
    counts = {}
    for writer in sorted({r["writer"] for r in acks} | {"000003c2", "000004c2"}):
        matching = [r for r in acks if r["writer"] == writer]
        counts[writer] = {"all": len(matching), "startup": sum(r["base"] == 0 for r in matching),
                          "positive": sum(r["base"] > 0 and not r["missing"] for r in matching)}
    data_writers = sorted({r["writer"] for r in rows
                           if r["kind"] == "DATA" and r["prefix"] in prefixes})
    assert ("00010082" if profile.startswith("spdp2") else "000100c2") in data_writers
    result = {"profile": profile, "repetition": repetition, "mutual_discovery": True,
              "participant_property": expected_property, "native_properties": properties,
              "discovery_plugins": str(expected_plugins), "prefixes": sorted(prefixes),
              "builtin_acknacks": counts, "data_writer_ids": data_writers,
              "capture_sha256": hashlib.sha256(capture.read_bytes()).hexdigest()}
    (REPORTS / (stem + "_decoded.json")).write_text(json.dumps(acks, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    return result


def main():
    report = {"status": "FAIL", "trials": []}
    try:
        assert {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}
        paths = [HERE / name for name in ("startup.py", "startup_qos.xml", "wire.py")]
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        report["source_hashes"] = hashes
        for repetition in (1, 2):
            for profile in ("spdp_off", "spdp_on", "spdp2_off", "spdp2_on"):
                report["trials"].append(trial(profile, repetition))
        assert all(hashlib.sha256((HERE / p).read_bytes()).hexdigest() == h for p, h in hashes.items())
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
