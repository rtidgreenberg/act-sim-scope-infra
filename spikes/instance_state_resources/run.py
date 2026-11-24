#!/usr/bin/env python3
"""Bounded native instance replacement and actual ten-second unread sample expiry."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import traceback
import xml.etree.ElementTree as ET

ROOT = Path("/workspace")
HERE = ROOT / "spikes/instance_state_resources"
WORK = Path(tempfile.mkdtemp(prefix="act-instance-resources-"))
RESULTS = Path("/results")
report = {"status": "FAIL", "scope": "Native DDS controls, not Routing Service forwarding",
          "cases": [], "scratch": str(WORK)}


def main():
    try:
        assert {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}
        merged = ET.parse(ROOT / "harness_v2/qos/act_qos_profiles.xml")
        merged.getroot().append(ET.parse(
            ROOT / "harness_v2/datamodel/gen/ActTypes.xml").getroot().find("types"))
        merged.getroot().append(ET.parse(HERE / "qos.xml").getroot().find("qos_library"))
        qos = WORK / "qos.xml"
        merged.write(qos)
        report["source_hashes"] = {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in list(HERE.iterdir()) + [ROOT / "spikes/contact_disposal_timing/probe_support.hpp"]
            if p.is_file()}
        build = WORK / "build"
        with (WORK / "build.log").open("w") as log:
            subprocess.run(["cmake", "-S", str(HERE), "-B", str(build),
                            "-DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0"], check=True,
                           stdout=log, stderr=subprocess.STDOUT)
            subprocess.run(["cmake", "--build", str(build), "-j2"], check=True,
                           stdout=log, stderr=subprocess.STDOUT)
        for profile, mode in (
                ("bounded", "churn"), ("bounded_forget", "churn"),
                ("slow_infinite", "unread"), ("slow_ten", "before_expiry"),
                ("slow_ten", "unread"), ("slow_infinite", "invalid_only"),
                ("slow_ten", "invalid_only"), ("slow_infinite", "read_returned"),
                ("slow_infinite", "read_held")):
            log_path = WORK / (profile + "_" + mode + ".log")
            with log_path.open("w") as log:
                executed = subprocess.run(
                    [str(build / "instance_state_resources"), str(qos), profile, mode],
                    stdout=log, stderr=subprocess.STDOUT, timeout=180)
            events = [json.loads(line) for line in log_path.read_text().splitlines() if line.startswith("{")]
            record = {"profile": profile, "mode": mode, "returncode": executed.returncode,
                      "events": events, "log": log_path.name}
            report["cases"].append(record)
            assert executed.returncode == 0, record
            assert events[-1]["status"] == "PASS", record
            print(json.dumps(events[-1]), flush=True)
        report["status"] = "PASS"
    except Exception:
        report["error"] = traceback.format_exc()
        print(report["error"], file=sys.stderr)
    finally:
        report["private_shm_artifacts"] = [p.name for p in Path("/dev/shm").iterdir()
                                          if p.name.startswith(("RTI", "dds"))]
        if report["private_shm_artifacts"]:
            report["status"] = "FAIL"
        (RESULTS / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        with tarfile.open(RESULTS / "inputs-and-logs.tar.gz", "w:gz") as archive:
            for p in WORK.iterdir():
                if p.is_file():
                    archive.add(p, arcname=p.name)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
