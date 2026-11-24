#!/usr/bin/env python3
"""Real WAN-only netem delay/outage with native Routing Service cache observations."""

import copy
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET

import yaml

from run import BUILD, EXPECTED, ROOT, SPIKE, digest, run

WORK = Path(tempfile.mkdtemp(prefix="act-contact-delayed-"))
RESULTS = Path("/results")
REPORT = {"status": "FAIL", "delay_seconds": 3, "outage_seconds": 2,
          "cleanup_seconds": [0, 10], "trials": [], "scratch": str(WORK)}


def wait(predicate, label, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Timed out: " + label)


def tc(*arguments):
    try:
        return run(["tc", *arguments], capture_output=True).stdout
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"tc {' '.join(arguments)}: {error.stderr}") from error


def rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.startswith("{")]


def configure_link():
    tc("qdisc", "add", "dev", "lo", "root", "handle", "1:", "prio", "bands", "3",
       "priomap", *(["0"] * 16))
    tc("qdisc", "add", "dev", "lo", "parent", "1:3", "handle", "30:",
       "netem", "delay", "3000ms", "limit", "10000")
    for network in ipaddress.summarize_address_range(
            ipaddress.IPv4Address(60400), ipaddress.IPv4Address(60649)):
        tc("filter", "add", "dev", "lo", "protocol", "ip", "parent", "1:", "prio", "1",
           "u32", "match", "ip", "protocol", "17", "0xff", "match", "ip", "dport",
           str(int(network.network_address)), hex(int(network.netmask) & 65535),
           "flowid", "1:3")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 60599))
        receiver.settimeout(5)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            started = time.monotonic()
            sender.sendto(b"delay-calibration", receiver.getsockname())
            assert receiver.recv(100) == b"delay-calibration"
            elapsed = time.monotonic() - started
    assert 2.9 <= elapsed <= 4.5, elapsed
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 60399))
        receiver.settimeout(1)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            started = time.monotonic()
            sender.sendto(b"undelayed-control", receiver.getsockname())
            assert receiver.recv(100) == b"undelayed-control"
            undelayed = time.monotonic() - started
    assert undelayed < 0.5, undelayed
    return {"wan_udp_delay_seconds": elapsed, "outside_wan_delay_seconds": undelayed,
            "qdisc": json.loads(tc("-s", "-j", "qdisc", "show", "dev", "lo")),
            "filter": tc("filter", "show", "dev", "lo", "parent", "1:")}


def prepare(directory, seconds, participant_assert_seconds=None):
    qos = ET.parse(ROOT / "harness_v2/qos/act_qos_profiles.xml")
    for mask in qos.findall(".//transport_builtin/mask"):
        mask.text = "UDPv4"
    if participant_assert_seconds is not None:
        participant = qos.find("./qos_library/qos_profile[@name='wan_three_topic_participant']"
                               "/domain_participant_qos")
        discovery = participant.find("discovery_config")
        if discovery is None:
            discovery = ET.SubElement(participant, "discovery_config")
        period = ET.SubElement(discovery, "participant_liveliness_assert_period")
        ET.SubElement(period, "sec").text = str(participant_assert_seconds)
        ET.SubElement(period, "nanosec").text = "0"
    for profile in qos.findall("./qos_library/qos_profile"):
        for endpoint in ("datawriter_qos", "datareader_qos"):
            if endpoint == "datawriter_qos" and profile.attrib["name"] not in (
                    "lan_event", "wan_event", "wan_three_topic_event", "wan_union_contacts"):
                continue
            element = profile.find(endpoint)
            if element is None:
                continue
            lifecycle_name = "writer_data_lifecycle" if endpoint == "datawriter_qos" else "reader_data_lifecycle"
            lifecycle = element.find(lifecycle_name)
            if lifecycle is None:
                lifecycle = ET.SubElement(element, lifecycle_name)
            names = ["autopurge_disposed_instances_delay"]
            if endpoint == "datareader_qos":
                names.append("autopurge_disposed_samples_delay")
            for name in names:
                old = lifecycle.find(name)
                if old is not None:
                    lifecycle.remove(old)
                duration = ET.SubElement(lifecycle, name)
                ET.SubElement(duration, "sec").text = (
                    str(seconds) if endpoint == "datawriter_qos" else "DURATION_INFINITE_SEC")
                ET.SubElement(duration, "nanosec").text = (
                    "0" if endpoint == "datawriter_qos" else "DURATION_INFINITE_NSEC")
    qos_path = directory / "qos.xml"
    qos.write(qos_path)
    merged = copy.deepcopy(qos.getroot())
    merged.append(copy.deepcopy(ET.parse(
        ROOT / "harness_v2/datamodel/gen/ActTypes.xml").getroot().find("types")))
    merged.append(copy.deepcopy(ET.parse(SPIKE / "qos.xml").getroot().find("qos_library")))
    merged_path = directory / "probe.xml"
    ET.ElementTree(merged).write(merged_path)
    configs = {}
    for node, role, lan in (("Control_20", "control", "control_lan"),
                            ("Platform_30", "platform", "platform_lan")):
        config = yaml.safe_load((ROOT / "router/config/control-platform.yaml").read_text())
        config["router"].update(wan_contract="three_topic_union", admin_participant=lan)
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
        path = directory / (node + ".yaml")
        path.write_text(yaml.safe_dump(config))
        xml = directory / (node + ".xml")
        run([sys.executable, str(ROOT / "router/routing_service/run.py"),
             "--config", str(path), "--role", role, "--node-name", node, "--name", "rs",
             "--build-dir", str(BUILD), "--render", str(xml)], capture_output=True)
        run([sys.executable, str(ROOT / "router/routing_service/run.py"),
             "--xml", str(xml), "--validate-only"], capture_output=True)
        configs[node] = xml
    return merged_path, configs


def trial(build, seconds, outage, repeat):
    directory = WORK / f"writer_{seconds}_outage_{outage}_repeat_{repeat}"
    directory.mkdir()
    result = {"writer_cleanup_seconds": seconds, "outage": outage, "repeat": repeat}
    REPORT["trials"].append(result)
    processes, logs = [], []
    monitor_stop = directory / "stop_monitor"
    cache_path = directory / "wan_writer_cache.jsonl"
    link_configured = False
    try:
        probe_qos, configs = prepare(directory, seconds)
        sdk = Path(os.environ["NDDSHOME"])
        executable = next((sdk / "resource/app/bin").glob("*/rtiroutingserviceapp"))
        environment = os.environ.copy()
        environment["LD_LIBRARY_PATH"] = os.pathsep.join([
            str(sdk / "resource/app/lib" / executable.parent.name),
            str(sdk / "lib" / executable.parent.name)])
        for node, xml in configs.items():
            env = environment.copy()
            if node == "Platform_30":
                env.update(LD_PRELOAD=str(build / "libcontact_cache_monitor.so"),
                           CONTACT_CACHE_LOG=str(cache_path), CONTACT_CACHE_STOP=str(monitor_stop),
                           CONTACT_CACHE_START=str(directory / "start_monitor"))
            log = (directory / (node + ".log")).open("w")
            logs.append(log)
            processes.append(subprocess.Popen(
                [str(executable), "-cfgFile", str(xml), "-cfgName", "ACTGateway",
                 "-verbosity", "WARN"], env=env, stdout=log, stderr=subprocess.STDOUT))
        probe_output = directory / "probe.jsonl"
        log = probe_output.open("w")
        logs.append(log)
        probe = subprocess.Popen(
            [str(build / "contact_disposal_timing"), str(probe_qos),
             "delay_ten" if seconds else "delay_zero", str(repeat), str(directory)],
            env=environment, stdout=log, stderr=subprocess.STDOUT)
        processes.append(probe)

        def ready():
            assert all(p.poll() is None for p in processes), "gateway/probe exited before readiness"
            return (directory / "ready").exists()

        wait(ready, "valid contact materialized at C2")
        (directory / "start_monitor").touch()
        wait(lambda: any(r.get("alive") == 1 for r in rows(cache_path)),
             "native platform WAN State writer observer")
        result["link"] = configure_link()
        link_configured = True
        if outage:
            tc("qdisc", "change", "dev", "lo", "parent", "1:3", "handle", "30:",
               "netem", "delay", "3000ms", "loss", "100%", "limit", "10000")
            onset = time.monotonic_ns()
            (directory / "go").touch()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
                receiver.bind(("127.0.0.1", 60599))
                receiver.settimeout(0.3)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    sender.sendto(b"blackout-control", receiver.getsockname())
                    try:
                        receiver.recv(100)
                    except socket.timeout:
                        result["blackout_control_dropped"] = True
                    else:
                        raise AssertionError("blackout control was delivered")
            time.sleep(max(0, 2 - (time.monotonic_ns() - onset) / 1e9))
            tc("qdisc", "change", "dev", "lo", "parent", "1:3", "handle", "30:",
               "netem", "delay", "3000ms", "loss", "0%", "limit", "10000")
            result["blackout"] = {"onset_ns": onset, "restored_ns": time.monotonic_ns()}
        else:
            (directory / "go").touch()
        probe.wait(timeout=70)
        assert probe.returncode == 0, probe_output.read_text()[-8000:]
        events = rows(probe_output)
        outcome = next(row for row in events if row["event"] == "result")
        disposed_ns = next(row["monotonic_ns"] for row in events if row["event"] == "dispose")
        assert outcome["status"] == "PASS", outcome
        assert outcome["dispose_to_c2_invalid_ms"] >= 2900, outcome
        if outage:
            assert result["blackout"]["onset_ns"] <= disposed_ns < result["blackout"]["restored_ns"]
        result["native_result"] = outcome
        result["source_dispose_monotonic_ns"] = disposed_ns
        wait(lambda: any(row.get("disposed") == 1 for row in rows(cache_path)), "WAN writer disposed")

        def cleaned():
            observations = [row for row in rows(cache_path) if row.get("monotonic_ns", 0) >= disposed_ns]
            disposed = [row for row in observations if row.get("disposed") == 1]
            return bool(disposed and any(row.get("disposed") == 0 and row.get("alive") == 0
                                        and row["monotonic_ns"] > disposed[0]["monotonic_ns"]
                                        for row in observations))

        wait(cleaned, "native WAN writer resource cleanup", 30)
        observations = [row for row in rows(cache_path) if row.get("monotonic_ns", 0) >= disposed_ns]
        disposed = [row for row in observations if row.get("disposed") == 1]
        first = disposed[0]
        purge = next(row for row in observations if row.get("disposed") == 0
                     and row.get("alive") == 0 and row["monotonic_ns"] > first["monotonic_ns"])
        assert any(row["unacknowledged"] > 0 for row in disposed), "no observed unacknowledged dispose"
        assert purge["unacknowledged"] == 0, purge
        purge_since_source_ms = (purge["monotonic_ns"] - disposed_ns) / 1e6
        assert purge_since_source_ms >= outcome["dispose_to_c2_invalid_ms"]
        if seconds:
            assert purge_since_source_ms >= seconds * 1000
        result["wan_writer"] = {
            "first_disposed_since_source_ms": (first["monotonic_ns"] - disposed_ns) / 1e6,
            "first_cleanup_since_source_ms": purge_since_source_ms,
            "last_retained_since_source_ms": (disposed[-1]["monotonic_ns"] - disposed_ns) / 1e6,
            "unacknowledged_seen": True, "unacknowledged_at_cleanup": purge["unacknowledged"]}
        cache_events = rows(cache_path)
        assert not any("error" in row for row in cache_events), cache_events[-5:]
        effective = next(row for row in cache_events if row.get("event") == "writer_qos")
        assert effective["cleanup_sec"] == seconds and effective["cleanup_nanosec"] == 0
        result["wan_writer"]["effective_qos"] = effective
        result["qdisc_after"] = json.loads(tc("-s", "-j", "qdisc", "show", "dev", "lo"))
        if outage:
            assert any(q.get("drops", 0) > 0 for q in result["qdisc_after"]), result["qdisc_after"]
        result["status"] = "PASS"
        print(json.dumps({key: result[key] for key in (
            "status", "writer_cleanup_seconds", "outage", "repeat", "native_result",
            "wan_writer")}), flush=True)
    finally:
        monitor_stop.touch()
        if cache_path.exists():
            wait(lambda: any(row.get("event") == "monitor_stopped" for row in rows(cache_path)),
                 "native cache observer shutdown", 5)
        for process in reversed(processes):
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()
        if link_configured:
            result["qdisc_final"] = tc("-s", "qdisc", "show", "dev", "lo")
            tc("qdisc", "del", "dev", "lo", "root")
        result["process_returncodes"] = [p.returncode for p in processes]


def main():
    try:
        assert {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}
        REPORT["binary_hashes"] = {name: digest(BUILD / name) for name in EXPECTED}
        assert REPORT["binary_hashes"] == EXPECTED
        REPORT["source_hashes"] = {str(path.relative_to(ROOT)): digest(path) for path in (
            SPIKE / "delayed.py", SPIKE / "cache_monitor.c", SPIKE / "timing.cxx",
            SPIKE / "qos.xml", ROOT / "harness_v2/qos/act_qos_profiles.xml")}
        for key in ("CONTROL_LAN_PEER1", "CONTROL_LAN_PEER2", "CONTROL_LAN_PEER3",
                    "PLATFORM_LAN_PEER1", "PLATFORM_LAN_PEER2", "PLATFORM_LAN_PEER3"):
            os.environ[key] = "127.0.0.1"
        for i in range(1, 12):
            os.environ["WAN_PEER" + (str(i) if i > 1 else "")] = "[0-15]@builtin.udpv4://127.0.0.1"
        build = WORK / "build"
        with (WORK / "build.log").open("w") as log:
            run(["cmake", "-S", str(SPIKE), "-B", str(build),
                 "-DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0", "-DBUILD_SHARED_LIBS=ON"],
                stdout=log, stderr=subprocess.STDOUT)
            run(["cmake", "--build", str(build), "-j2"], stdout=log, stderr=subprocess.STDOUT)
        for seconds in (0, 10):
            for outage in (False, True):
                for repeat in range(2):
                    trial(build, seconds, outage, repeat)
        REPORT["status"] = "PASS"
    except Exception:
        REPORT["error"] = traceback.format_exc()
        print(REPORT["error"], file=sys.stderr)
    finally:
        REPORT["binaries_unchanged"] = {name: digest(BUILD / name) for name in EXPECTED} == EXPECTED
        REPORT["private_shm_artifacts"] = [p.name for p in Path("/dev/shm").iterdir()
                                          if p.name.startswith(("RTI", "dds"))]
        if not REPORT["binaries_unchanged"] or REPORT["private_shm_artifacts"]:
            REPORT["status"] = "FAIL"
        (RESULTS / "report.json").write_text(json.dumps(REPORT, indent=2) + "\n")
        with tarfile.open(RESULTS / "inputs-and-logs.tar.gz", "w:gz") as archive:
            for path in WORK.rglob("*"):
                if path.is_file() and "build" not in path.relative_to(WORK).parts:
                    archive.add(path, arcname=str(path.relative_to(WORK)))
            archive.add(WORK / "build.log", arcname="build.log")
    return 0 if REPORT["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
