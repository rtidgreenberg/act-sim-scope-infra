#!/usr/bin/env python3
"""Isolated native Routing Service forwarding with static WAN discovery."""

from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

import rti.connextdds as dds


ROOT = Path("/workspace")
HERE = ROOT / "spikes/routing_service_lbed"
REPORT = Path("/reports")
CAPTURE = Path("/captures")
SDK = Path(os.environ["NDDSHOME"])
TYPE_FILE = ROOT / "spikes/serialized_wan_envelope/envelope.xml"
TYPE = "SerializedWanEnvelope"
FAMILIES = ("Event", "State", "Periodic")
DOMAINS = {"A": 110, "B": 112}
WAN_DOMAIN = 111
PROFILES = {"sedp": "participant", "classic_lbed": "classic_lbed",
            "spdp2_lbed": "lbed", "missing_writer": "lbed"}
sys.path.insert(0, str(ROOT / "spikes/preemptive_acknack"))
from wire import capture_rows


def child(parent, tag, text=None, **attributes):
    result = ET.SubElement(parent, tag, attributes)
    if text is not None:
        result.text = str(text)
    return result


def save(root, path):
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def stop(process):
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if process.returncode not in (0, -signal.SIGINT, -signal.SIGTERM):
        raise RuntimeError(f"Child {process.args} exited {process.returncode}")


def wait(predicate, label, processes=(), timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for process in processes:
            if process.poll() is not None:
                raise RuntimeError(f"{label}: child exited {process.returncode}")
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out: {label}")


def document():
    root = ET.Element("dds")
    root.extend(copy.deepcopy(ET.parse(HERE / "qos.xml").getroot()).findall("qos_library"))
    root.append(copy.deepcopy(ET.parse(TYPE_FILE).getroot().find("types")))
    return root


def partition(qos, name):
    child(child(child(qos, "partition"), "name"), "element", name)


def endpoint_qos(parent, kind, family, object_id=None):
    qos = child(parent, kind + "_qos", base_name="LBEDSpike::" + family.lower())
    if object_id is not None:
        child(child(qos, "protocol"), "rtps_object_id", object_id)
    return qos


def catalog(path, missing_writer):
    root = document()
    domain = child(child(root, "domain_library", name="WAN"), "domain",
                   name="radio", domain_id=str(WAN_DOMAIN))
    child(domain, "register_type", name=TYPE, type_ref=TYPE)
    for family in FAMILIES:
        child(domain, "topic", name=family, register_type_ref=TYPE)
    library = child(root, "domain_participant_library", name="Gateways")
    for node in DOMAINS:
        participant = child(library, "domain_participant", name="Gateway" + node,
                            domain_ref="WAN::radio")
        for kind, group in (("datawriter", "publisher"), ("datareader", "subscriber")):
            container = child(participant, group, name=group)
            partition(child(container, group + "_qos"),
                      node if kind == "datawriter" else ("B" if node == "A" else "A"))
            for index, family in enumerate(FAMILIES, 1):
                if missing_writer and node == "A" and family == "Event" and kind == "datawriter":
                    continue
                tag = "data_writer" if kind == "datawriter" else "data_reader"
                port = child(container, tag, name=family, topic_ref=family)
                object_id = (1000 if kind == "datawriter" else 2000) + index
                endpoint_qos(port, kind, family, object_id)
    save(root, path)


def service(path, node, variant):
    root = document()
    routing = child(root, "routing_service", name="Gateway" + node)
    domain = child(routing, "domain_route", name="bridge")
    for label, domain_id in (("lan", DOMAINS[node]), ("wan", WAN_DOMAIN)):
        participant = child(domain, "participant", name=label)
        child(participant, "domain_id", domain_id)
        profile = PROFILES[variant] if label == "wan" else "participant"
        qos = child(participant, "domain_participant_qos",
                    base_name="LBEDSpike::" + profile)
        child(child(qos, "participant_name"), "name", "Gateway" + node if label == "wan"
              else "Gateway" + node + "LAN")
        child(participant, "register_type", name=TYPE, type_ref=TYPE)
    session = child(domain, "session", name="routes")
    child(session, "publisher_qos", base_name="LBEDSpike::partition_" + node)
    child(session, "subscriber_qos",
          base_name="LBEDSpike::partition_" + ("B" if node == "A" else "A"))
    for index, family in enumerate(FAMILIES, 1):
        for direction in ("tx", "rx"):
            route = child(session, "topic_route", name=direction + family)
            child(route, "route_types", "false")
            for kind in ("input", "output"):
                is_wan = (direction == "tx") == (kind == "output")
                port = child(route, kind, name=kind, participant="wan" if is_wan else "lan")
                child(port, "topic_name", family if is_wan else
                      ("Send" if direction == "tx" else "Receive") + family)
                child(port, "registered_type_name", TYPE)
                child(port, "creation_mode", "IMMEDIATE")
                child(port, "auto_configure_qos", "false")
                qkind = "datareader" if kind == "input" else "datawriter"
                object_id = (2000 if kind == "input" else 1000) + index if is_wan else None
                endpoint_qos(port, qkind, family, object_id)
    save(root, path)


class Probe:
    def __init__(self, node, provider):
        self.participant = dds.DomainParticipant(
            DOMAINS[node], provider.participant_qos_from_profile("LBEDSpike::participant"))
        self.publisher = dds.Publisher(self.participant, provider.publisher_qos_from_profile(
            "LBEDSpike::partition_" + ("B" if node == "A" else "A")))
        self.subscriber = dds.Subscriber(self.participant, provider.subscriber_qos_from_profile(
            "LBEDSpike::partition_" + node))
        self.writers, self.readers = {}, {}
        dtype = provider.type(TYPE)
        for family in FAMILIES:
            send = dds.DynamicData.Topic(self.participant, "Send" + family, dtype)
            receive = dds.DynamicData.Topic(self.participant, "Receive" + family, dtype)
            profile = "LBEDSpike::" + family.lower()
            self.writers[family] = dds.DynamicData.DataWriter(
                self.publisher, send, provider.datawriter_qos_from_profile(profile))
            self.readers[family] = dds.DynamicData.DataReader(
                self.subscriber, receive, provider.datareader_qos_from_profile(profile))

    def matched(self):
        return all(writer.publication_matched_status.current_count == 1
                   for writer in self.writers.values()) and all(
                       reader.subscription_matched_status.current_count == 1
                       for reader in self.readers.values())

    def close(self):
        self.participant.close()


def sample(dtype, node, family, phase, ordinal):
    value = dds.DynamicData(dtype)
    fields = {
        "source": node, "original_topic": family + "Original",
        "instance_id": f"{phase}-{ordinal}", "destination": "B" if node == "A" else "A",
        "schema_name": "opaque-test", "schema_identity": "fixture-v1",
        "encoding": "test-octets", "operation": "WRITE",
        "payload": [] if ordinal == 0 else list(range(256)) * ordinal,
    }
    for key, field in fields.items():
        value[key] = field
    return value, fields


def exchange(probes, dtype, processes, phase, missing_writer=False):
    results = []
    for node, source in probes.items():
        target = probes["B" if node == "A" else "A"]
        for family in FAMILIES:
            for ordinal in range(3):
                value, expected = sample(dtype, node, family, phase, ordinal)
                received = []
                source.writers[family].write(value)
                blocked = missing_writer and node == "A" and family == "Event"

                def delivered():
                    for data, info in target.readers[family].take():
                        if info.valid:
                            actual = {key: list(data[key]) if key == "payload" else data[key]
                                      for key in expected}
                            assert actual == expected, (actual, expected)
                            received.append(actual)
                    return bool(received)

                if blocked:
                    until = time.monotonic() + 1
                    while time.monotonic() < until:
                        assert not delivered(), "Uncatalogued writer unexpectedly delivered Event"
                        time.sleep(0.05)
                else:
                    wait(delivered, f"{phase} {node} {family} {ordinal}", processes)
                    assert len(received) == 1, (phase, node, family, received)
                results.append({"source": node, "family": family, "ordinal": ordinal,
                                "received": len(received), "expected_block": blocked})
    time.sleep(0.3)
    for node, probe in probes.items():
        for family, reader in probe.readers.items():
            assert not any(info.valid for _, info in reader.take()), (
                "Unexpected trailing sample", node, family, phase)
    return results


def wire_result(path, variant):
    rows = capture_rows(path)
    sedp_ids = {"000003c2", "000004c2"}
    sedp = [row for row in rows if row["writer"] in sedp_ids]
    data = [row for row in rows if row["kind"] == "DATA"]
    participant_ids = {"000100c2", "00010082", "00010182"}
    user = [row for row in data if not (int(row["writer"][-2:], 16) & 0x80)]
    result = {
        "sedp_submessages": len(sedp),
        "sedp_data": sum(row["kind"] == "DATA" for row in sedp),
        "participant_data": sum(row["writer"] in participant_ids for row in data),
        "user_data": len(user),
        "user_writer_ids": sorted({row["writer"] for row in user}),
        "gateway_prefixes": sorted({row["prefix"] for row in user}),
        "builtin_data_ids": sorted({row["writer"] for row in data
                                   if int(row["writer"][-2:], 16) & 0x80}),
    }
    assert result["participant_data"] > 0 and result["user_data"] > 0, result
    assert len(result["user_writer_ids"]) == 3, result
    # One gateway is restarted in positive trials, creating a third physical GUID prefix.
    assert len(result["gateway_prefixes"]) == (2 if variant == "missing_writer" else 3), result
    if variant == "sedp":
        assert result["sedp_data"] > 0, result
    else:
        assert not sedp, result
    return result


def case(work, variant):
    directory = work / variant
    directory.mkdir()
    catalog_path = directory / "catalog.xml"
    catalog(catalog_path, variant == "missing_writer")
    os.environ["LBED_CATALOG"] = str(catalog_path)
    config = directory / "probe.xml"
    save(document(), config)
    provider = dds.QosProvider(str(config))
    with ExitStack() as cleanup:
        pcap = CAPTURE / f"{variant}.pcapng"
        pcap.touch()
        pcap.chmod(0o666)
        capture_log = cleanup.enter_context((REPORT / f"{variant}-capture.log").open("w"))
        recorder = subprocess.Popen(
            ["dumpcap", "-i", "lo", "-f", f"udp dst portrange {7400 + WAN_DOMAIN * 250}-"
             f"{7400 + WAN_DOMAIN * 250 + 249}", "-a", "duration:100", "-w", str(pcap)],
            stdout=capture_log, stderr=subprocess.STDOUT)
        cleanup.callback(stop, recorder)
        wait(lambda: pcap.exists() and pcap.stat().st_size > 0, "capture ready", timeout=5)
        processes = {}

        def launch(node, suffix=""):
            path = directory / f"{node}.xml"
            service(path, node, variant)
            log = cleanup.enter_context((REPORT / f"{variant}-{node}{suffix}.log").open("w"))
            process = subprocess.Popen(
                [str(SDK / "resource/app/bin/x64Linux4gcc8.5.0/rtiroutingserviceapp"),
                 "-cfgFile", str(path),
                 "-cfgName", "Gateway" + node, "-verbosity", "3"],
                stdout=log, stderr=subprocess.STDOUT)
            cleanup.callback(stop, process)
            return process

        for node in DOMAINS:
            processes[node] = launch(node)
        probes = {node: Probe(node, provider) for node in DOMAINS}
        for probe in probes.values():
            cleanup.callback(probe.close)
        wait(lambda: all(probe.matched() for probe in probes.values()), "LAN route matches",
             processes.values())
        # Reliable writer matching is verified by actual traffic, not by a SEDP observer.
        time.sleep(3)
        results = exchange(probes, provider.type(TYPE), list(processes.values()), "initial",
                           variant == "missing_writer")
        if variant != "missing_writer":
            stop(processes["B"])
            processes["B"] = launch("B", "-restart")
            wait(probes["B"].matched, "restarted LAN matches", processes.values())
            time.sleep(3)
            results += exchange(probes, provider.type(TYPE), list(processes.values()), "restart")
        for process in processes.values():
            stop(process)
        stop(recorder)
        capture_text = (REPORT / f"{variant}-capture.log").read_text()
        drops = re.search(r"Packets received/dropped on interface .*?: (\d+)/(\d+)",
                          capture_text)
        assert drops and int(drops[2]) == 0, capture_text
        native_logs = {}
        for log in REPORT.glob(f"{variant}-*.log"):
            if log.name.endswith("-capture.log"):
                continue
            errors = [line for line in log.read_text().splitlines()
                      if re.search(r"\b(ERROR|FATAL)\b", line)]
            assert not errors, (log.name, errors)
            native_logs[log.name] = len(errors)
        result = {"variant": variant, "checks": results, "wire": wire_result(pcap, variant),
                  "capture_received": int(drops[1]), "capture_dropped": int(drops[2]),
                  "native_error_counts": native_logs}
        for path in directory.glob("*.xml"):
            # Retain exact generated inputs; rewrite nothing in production.
            (REPORT / f"{variant}-{path.name}").write_bytes(path.read_bytes())
        (REPORT / f"{variant}.json").write_text(json.dumps(result, indent=2) + "\n")
        return result


def main():
    results = []
    library_paths = [SDK / "resource/app/lib/x64Linux4gcc8.5.0",
                     SDK / "lib/x64Linux4gcc8.5.0"]
    os.environ["LD_LIBRARY_PATH"] = ":".join(
        [str(path) for path in library_paths] + [os.environ.get("LD_LIBRARY_PATH", "")])
    try:
        with tempfile.TemporaryDirectory(prefix="routing-service-lbed-") as directory:
            for variant in PROFILES:
                result = case(Path(directory), variant)
                results.append(result)
                print(json.dumps({"variant": variant, "checks": len(result["checks"]),
                                  "wire": result["wire"]}), flush=True)
        remnants = [str(path) for path in Path("/dev/shm").iterdir()
                    if path.name.startswith(("RTI", "dds"))]
        assert not remnants, remnants
        sources = [HERE / "runner.py", HERE / "qos.xml", HERE / "run.sh", TYPE_FILE]
        report = {"result": "PASS", "variants": results, "shared_memory_remnants": remnants,
                  "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(
                      path.read_bytes()).hexdigest() for path in sources}}
        (REPORT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        (REPORT / "PASS").write_text("PASS\n")
    except Exception as error:
        (REPORT / "failure.json").write_text(json.dumps(
            {"result": "FAIL", "error": str(error), "completed_variants": results}, indent=2) + "\n")
        raise


if __name__ == "__main__":
    main()
