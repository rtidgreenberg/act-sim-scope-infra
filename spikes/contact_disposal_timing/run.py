#!/usr/bin/env python3
"""Prepare two native gateways and collect native probe timing without pytest."""

import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET

import yaml

ROOT = Path("/workspace")
SPIKE = ROOT / "spikes/contact_disposal_timing"
WORK = Path(tempfile.mkdtemp(prefix="act-contact-disposal-"))
RESULTS = Path("/results")
BUILD = ROOT / "router/build/routing_service"
EXPECTED = {
    "libact_gateway_processor.so": "022941caaef2604dd41657da494b9144f17e5bbb7fd1a6c7b816e4dfd007f94b",
    "act_rs_config": "406e734e0f713443fd4dd2a16bcbd6e4665d9eae583660211abb9fd1820ab6bf",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, **kwargs):
    return subprocess.run(command, check=True, text=True, **kwargs)


def main():
    os.chdir(ROOT)
    report = {
        "status": "FAIL", "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "isolation": {"network": "none", "ipc": "private", "transport": "UDPv4",
                      "domains": {"platform": 211, "wan": 212, "control": 213}},
        "clock": "C++ std::chrono::steady_clock in one producer/consumer process",
        "bounds": {"dispose_s": 5, "materialize_s": 20, "zero_purge_s": 2,
                   "poll_sleep_ms": 1, "default_post_return_observation_ms": 5500},
        "scratch_location": str(WORK) + "; removed with private container",
        "scope": "Current deployed plugin; isolated loopback, two gateways, full monitoring, "
                 "static receive policy; ContactReport only application routes. Not EMANE/load qualification.",
        "trials": [], "inputs": {},
    }
    processes = []
    logs = []
    failure = None
    try:
        report["binaries"] = {name: digest(BUILD / name) for name in EXPECTED}
        assert report["binaries"] == EXPECTED, report["binaries"]
        report["source_revision"] = run(["git", "rev-parse", "HEAD"], capture_output=True).stdout.strip()
        report["qos_source_sha256"] = digest(ROOT / "harness_v2/qos/act_qos_profiles.xml")
        report["idl_xml_sha256"] = digest(ROOT / "harness_v2/datamodel/gen/ActTypes.xml")
        report["launcher_sha256"] = digest(ROOT / "router/routing_service/run.py")
        report["spike_source_hashes"] = {p.name: digest(p) for p in SPIKE.iterdir()
                                          if p.is_file()}
        WORK.mkdir(exist_ok=True)
        for key in ("CONTROL_LAN_PEER1", "CONTROL_LAN_PEER2", "CONTROL_LAN_PEER3",
                    "PLATFORM_LAN_PEER1", "PLATFORM_LAN_PEER2", "PLATFORM_LAN_PEER3"):
            os.environ[key] = "127.0.0.1"
        for i in range(1, 12):
            os.environ["WAN_PEER" + (str(i) if i > 1 else "")] = "[0-15]@builtin.udpv4://127.0.0.1"
        report["peer_environment"] = {k: v for k, v in os.environ.items()
                                      if "PEER" in k and ("LAN" in k or k.startswith("WAN"))}

        qos_path = WORK / "isolated-production-qos.xml"
        qos = ET.parse(ROOT / "harness_v2/qos/act_qos_profiles.xml")
        for mask in qos.findall(".//transport_builtin/mask"):
            mask.text = "UDPv4"
        qos.write(qos_path, encoding="unicode", xml_declaration=True)
        merged = copy.deepcopy(qos.getroot())
        merged.append(copy.deepcopy(ET.parse(
            ROOT / "harness_v2/datamodel/gen/ActTypes.xml").getroot().find("types")))
        merged.append(copy.deepcopy(ET.parse(SPIKE / "qos.xml").getroot().find("qos_library")))
        merged_path = WORK / "probe-qos.xml"
        ET.ElementTree(merged).write(merged_path, encoding="unicode", xml_declaration=True)
        native_build = WORK / "build"
        with (WORK / "build.log").open("w") as log:
            run(["cmake", "-S", str(SPIKE), "-B", str(native_build),
                 "-DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0"], stdout=log, stderr=subprocess.STDOUT)
            run(["cmake", "--build", str(native_build), "-j2"], stdout=log, stderr=subprocess.STDOUT)
        report["probe_sha256"] = digest(native_build / "contact_disposal_timing")

        commands = []
        for node, role, lan in (("Control_20", "control", "control_lan"),
                                ("Platform_30", "platform", "platform_lan")):
            config = yaml.safe_load((ROOT / "router/config/control-platform.yaml").read_text())
            config["router"]["wan_contract"] = "three_topic_union"
            config["router"]["admin_participant"] = lan
            config["qos_profiles"].update(
                wan_participant="ACT_QOS_LIB::wan_three_topic_participant",
                wan_event="ACT_QOS_LIB::wan_three_topic_event",
                wan_contacts="ACT_QOS_LIB::wan_union_contacts",
                wan_status="ACT_QOS_LIB::wan_union_status")
            config["routing_service"] = {"receive_policy": "static"}
            config["qos_libraries"] = [str(qos_path)]
            for name, participant in config["participants"].items():
                participant["domain"] = {"control_lan": 213, "platform_lan": 211,
                                         "control_wan": 212, "platform_wan": 212}[name]
            for route in config["routes"]:
                route["enabled"] = any(t["name"] == "ContactReport" for t in route.get("topics", []))
                if route["enabled"]:
                    route["topics"] = [t for t in route["topics"] if t["name"] == "ContactReport"]
            path = WORK / f"{node}.yaml"
            path.write_text(yaml.safe_dump(config))
            xml = WORK / f"{node}.xml"
            prepare = [sys.executable, str(ROOT / "router/routing_service/run.py"),
                       "--config", str(path), "--role", role, "--node-name", node,
                       "--name", "rs", "--build-dir", str(BUILD), "--render", str(xml)]
            commands.append(prepare)
            run(prepare, capture_output=True)
            tree = ET.parse(xml)
            masks = [m.text for m in tree.findall(".//transport_builtin/mask")]
            assert masks and set(masks) == {"UDPv4"}, masks
            launch = [sys.executable, str(ROOT / "router/routing_service/run.py"), "--xml", str(xml)]
            commands.append(launch)
            log = (WORK / f"{node}.log").open("w")
            logs.append(log)
            processes.append(subprocess.Popen(launch, stdout=log, stderr=subprocess.STDOUT))
        report["gateway_commands"] = commands
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert all(p.poll() is None for p in processes), "gateway exited at startup"
            if all("rs.gateway.started" in (WORK / f"{node}.log").read_text()
                   for node in ("Platform_30", "Control_20")):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("gateway startup markers missing after 20s")

        for profile in ("probe", "zero", "zero_forget", "finite_2"):
            for trial in range(1 if profile == "finite_2" else 3):
                command = [str(native_build / "contact_disposal_timing"), str(merged_path),
                           profile, str(trial)]
                native = subprocess.run(command, text=True, capture_output=True, timeout=70)
                events = [json.loads(line) for line in native.stdout.splitlines() if line.startswith("{")]
                result = {"profile": profile, "trial": trial, "returncode": native.returncode,
                          "command": command, "events": events, "stderr": native.stderr}
                report["trials"].append(result)
                (WORK / f"{profile}_{trial}.stdout").write_text(native.stdout)
                (WORK / f"{profile}_{trial}.stderr").write_text(native.stderr)
                print(json.dumps({"profile": profile, "trial": trial,
                                  "result": next((e for e in events if e["event"] == "result"), None)}),
                      flush=True)
                assert native.returncode == 0, f"{profile}/{trial} failed: {native.stdout}\n{native.stderr}"
                assert events[-1]["status"] == ("EXPECTED_REJECTION" if profile == "finite_2" else "PASS")
                if profile == "finite_2":
                    assert "autopurge_disposed_instances_delay" in native.stderr
                    assert "DDS_DURATION_ZERO or DDS_DURATION_INFINITE" in native.stderr
                assert all(p.poll() is None for p in processes), "gateway exited during trials"
        timings = [e["dispose_to_c2_invalid_ms"] for t in report["trials"]
                   for e in t["events"] if e["event"] == "result" and e["status"] == "PASS"]
        report["timings_ms"] = {"count": len(timings), "min": min(timings),
                                "median": statistics.median(timings), "max": max(timings)}
        report["default_retention"] = {
            "disposed_instance_delay": "INFINITE",
            "purge_observed": False,
            "interpretation": "right-censored observations, not a measured finite purge time",
        }
        report["status"] = "PASS"
    except Exception:
        failure = traceback.format_exc()
        report["error"] = failure
        print(failure, file=sys.stderr)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()
        report["gateways_stopped"] = all(p.poll() is not None for p in processes)
        report["gateway_returncodes"] = [p.returncode for p in processes]
        for node in ("Control_20", "Platform_30"):
            path = WORK / f"{node}.log"
            if path.exists():
                lines = path.read_text().splitlines()
                report.setdefault("gateway_logs", {})[node] = {
                    "line_count": len(lines), "tail": lines[-20:],
                    "error_lines": [line for line in lines if "ERROR" in line or "level=error" in line],
                }
        inputs = [p for p in WORK.iterdir() if p.suffix in (".yaml", ".xml")]
        for path in inputs:
            report["inputs"][path.name] = digest(path)
        with tarfile.open(RESULTS / "inputs-and-logs.tar.gz", "w:gz") as archive:
            for path in sorted(WORK.iterdir()):
                if path.is_file():
                    archive.add(path, arcname=path.name)
        report["binaries_unchanged"] = {name: digest(BUILD / name) for name in EXPECTED} == EXPECTED
        report["private_shm_artifacts"] = [p.name for p in Path("/dev/shm").iterdir()
                                          if p.name.startswith(("RTI", "dds"))]
        if not report["gateways_stopped"] or not report["binaries_unchanged"] or report["private_shm_artifacts"]:
            report["status"] = "FAIL"
        (RESULTS / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"status": report["status"], "timings_ms": report.get("timings_ms"),
                          "report": "debug/test_reports/contact_disposal_20261119/report.json"}), flush=True)
    return 0 if report["status"] == "PASS" and failure is None else 1


if __name__ == "__main__":
    sys.exit(main())
