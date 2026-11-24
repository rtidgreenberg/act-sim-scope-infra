#!/usr/bin/env python3
"""Actual persistent Routing Service processor, in a network-none container."""

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import rti.connextdds as dds
import yaml

from wire import analyze

ROOT = Path("/workspace")
HERE = ROOT / "spikes/preemptive_acknack"
WORK = Path(os.environ["TMPDIR"])
REPORTS = Path("/reports")
CAPTURES = Path("/captures")
BUILD = ROOT / "router/build/routing_service"
TYPES = ROOT / "router/routing_service/fixtures/full_type_bindings.xml"
PARTICIPANT_VARIANTS = {
    "participant_baseline": ("disabled", "participant_baseline"),
    "participant_disabled": ("disabled", "participant_disabled"),
    "matrix_none": ("baseline", "participant_baseline"),
    "matrix_reader": ("disabled", "participant_baseline"),
    "matrix_participant": ("baseline", "participant_disabled"),
    "matrix_both": ("disabled", "participant_disabled"),
}
sys.path.insert(0, str(ROOT / "router/routing_service"))
from run import render, preflight


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stop(process):
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def wait(predicate, label, timeout=25):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out: " + label)


def guid(handle):
    value = str(handle).lower().replace(":", "").replace("-", "")
    assert len(value) == 32, repr(value)
    return value


def invalid_element_probe():
    root = ET.Element("dds")
    library = ET.SubElement(root, "qos_library", name="INVALID_MCP_ADVICE")
    profile = ET.SubElement(library, "qos_profile", name="reader")
    reader = ET.SubElement(profile, "datareader_qos")
    protocol = ET.SubElement(ET.SubElement(reader, "protocol"), "rtps_reliable_reader")
    ET.SubElement(protocol, "disable_preemptive_acknack").text = "true"
    path = WORK / "invalid_mcp_element.xml"
    ET.ElementTree(root).write(path)
    error = None
    try:
        dds.QosProvider(str(path)).datareader_qos_from_profile("INVALID_MCP_ADVICE::reader")
    except Exception as caught:
        error = str(caught)
    assert error, "Invented MCP XML element unexpectedly accepted"
    result = {"invented_element_rejected": True, "error": error,
              "binding_reliable_reader_fields": [
                  key for key in dir(dds.DataReaderQos().protocol.rtps_reliable_reader)
                  if not key.startswith("_")]}
    (REPORTS / "invalid_element_rejection.json").write_text(json.dumps(result, indent=2) + "\n")


def model(node, domain, variant):
    properties = {
        "act.version": "1", "act.types_uri": str(TYPES), "act.node": node,
        "act.role": "platform", "act.router": "rs", "act.wan_contract": "three_topic_union",
        "act.receive_policy": "static", "act.monitoring": "false",
        "act.admin_participant": "lan", "act.lan_participant": "lan",
        "act.lan_domain": str(domain), "act.wan_participant": "wan", "act.wan_domain": "102",
        "act.binding.command.topic_name": "DeviceCommands",
        "act.binding.command.type": "DeviceCommand", "act.binding.command.branch": "device_command",
        "act.binding.command.instance_id": "request_id", "act.binding.command.channel": "event_high",
        "act.binding.command.tx.port": "input_command",
        "act.binding.command.tx.recipients": "global",
        "act.binding.command.rx.port": "output_command",
        "act.binding.command.rx.recipients": "global",
    }
    path = WORK / (node + ".yaml")
    path.write_text(yaml.safe_dump(properties))
    result = subprocess.run([str(BUILD / "act_rs_config"), "--properties", str(path)],
                            check=True, capture_output=True, text=True)
    value = yaml.safe_load(result.stdout)
    value["qos_libraries"] = [str(ROOT / "harness_v2/qos/act_qos_profiles.xml"),
                              str(HERE / "qos.xml")]
    for participant in value["participants"]:
        participant["profile"] = "ACT_QOS_LIB::" + (
            "router_journal_participant" if participant["name"] == "lan"
            else "wan_three_topic_participant")
        if participant["name"] == "wan" and variant in PARTICIPANT_VARIANTS:
            participant["profile"] = "PREEMPTIVE_SPIKE::" + PARTICIPANT_VARIANTS[variant][1]
        participant["partitions"] = [] if participant["name"] == "lan" else ["SHARED"]
    for port in value["inputs"] + value["outputs"]:
        port["profile"] = "ACT_QOS_LIB::lan_event"
    for family in value["wan"]:
        selected = (PARTICIPANT_VARIANTS[variant][0] if variant in PARTICIPANT_VARIANTS else
                    variant if variant != "mcp_key" or family["name"] == "Event" else "baseline")
        family["profile"] = "PREEMPTIVE_SPIKE::" + selected + "_" + family["name"].lower()
        if family["name"] == "Event":
            family["writer_profile"] = "PREEMPTIVE_SPIKE::event_writer"
    return value


def case(variant):
    with ExitStack() as cleanup:
        capture = CAPTURES / (variant + ".pcapng")
        capture.write_bytes(b"")
        capture.chmod(0o666)
        capture_log = cleanup.enter_context((REPORTS / (variant + "_capture.log")).open("w"))
        recorder = subprocess.Popen(
            ["dumpcap", "-i", "lo", "-f", "udp portrange 32900-33149", "-a", "duration:55",
             "-w", str(capture)], stdout=capture_log, stderr=subprocess.STDOUT)
        cleanup.callback(stop, recorder)
        wait(lambda: capture.exists() and capture.stat().st_size > 0, "capture ready", 5)
        manifest = {"variant": variant, "endpoints": {}, "received": [], "timeline": []}
        environment = dict(os.environ)
        for index in range(1, 12):
            environment["WAN_PEER" + (str(index) if index > 1 else "")] = (
                "[0-15]@builtin.udpv4://127.0.0.1")
        os.environ.update(environment)
        provider = dds.QosProvider(str(ROOT / "harness_v2/qos/act_qos_profiles.xml"))
        types = dds.QosProvider(str(TYPES))
        observer_qos = provider.participant_qos_from_profile(
            "ACT_QOS_LIB::router_journal_participant")
        assert observer_qos.transport_builtin.mask == dds.TransportBuiltinMask.UDPv4
        combined = ET.parse(ROOT / "harness_v2/qos/act_qos_profiles.xml")
        combined.getroot().extend(ET.parse(HERE / "qos.xml").getroot().findall("qos_library"))
        combined_path = WORK / "combined.xml"
        combined.write(combined_path)
        spike_provider = dds.QosProvider(str(combined_path))
        if variant in PARTICIPANT_VARIANTS:
            participant_profile = PARTICIPANT_VARIANTS[variant][1]
            participant_qos = spike_provider.participant_qos_from_profile(
                "PREEMPTIVE_SPIKE::" + participant_profile)
            manifest["participant_properties"] = dict(participant_qos.property.get_all())
            expected = "true" if participant_profile == "participant_disabled" else "false"
            assert manifest["participant_properties"][
                "dds.participant.inter_participant_data_reader.protocol.disable_preemptive_nack"
            ] == expected
        assert spike_provider.participant_qos_from_profile(
            "PREEMPTIVE_SPIKE::observer").transport_builtin.mask == dds.TransportBuiltinMask.UDPv4
        assert spike_provider.participant_qos_from_profile(
            "ACT_QOS_LIB::wan_three_topic_participant").transport_builtin.mask == (
                dds.TransportBuiltinMask.UDPv4)
        effective = {}
        for profile in ("baseline_event", "disabled_event", "mcp_key_event"):
            qos = spike_provider.datareader_qos_from_profile("PREEMPTIVE_SPIKE::" + profile)
            effective[profile] = {
                "reliability": str(qos.reliability.kind),
                "properties": dict(qos.property.get_all()),
                "disable_positive_acks": qos.protocol.disable_positive_acks,
            }
        manifest["qos_provider_readback"] = effective
        assert effective["baseline_event"]["properties"] == {
            "dds.data_reader.protocol.disable_preemptive_nack": "false"}
        assert effective["disabled_event"]["properties"] == {
            "dds.data_reader.protocol.disable_preemptive_nack": "true"}
        assert not effective["disabled_event"]["disable_positive_acks"]
        assert effective["disabled_event"]["reliability"] == "ReliabilityKind.RELIABLE"
        observer = dds.DomainParticipant(102, spike_provider.participant_qos_from_profile(
            "PREEMPTIVE_SPIKE::observer"))
        cleanup.callback(observer.close)
        publications, subscriptions = observer.publication_reader, observer.subscription_reader
        discovered = {"writers": {}, "readers": {}}

        def discover():
            for kind, reader in (("writers", publications), ("readers", subscriptions)):
                for data, info in reader.read():
                    if info.valid and data.topic_name in ("Event", "State", "Periodic"):
                        discovered[kind][guid(info.instance_handle)] = data.topic_name

        def start(node, domain):
            config = ET.fromstring(render(model(node, domain, variant), "",
                                          str(BUILD / "libact_gateway_processor.so"), True))
            if variant in PARTICIPANT_VARIANTS:
                wan_qos = config.find(
                    "./routing_service/domain_route/participant[@name='wan']/domain_participant_qos")
                values = wan_qos.find("./property/value")
                entry = ET.parse(HERE / "qos.xml").find(
                    f"./qos_library/qos_profile[@name='{participant_profile}']"
                    "/domain_participant_qos/property/value/element")
                assert values is not None and entry is not None
                values.append(entry)
                effective_xml = ET.parse(combined_path)
                library = effective_xml.find("./qos_library[@name='PREEMPTIVE_SPIKE']")
                profile = ET.SubElement(library, "qos_profile", name="effective_" + node)
                profile.append(ET.fromstring(ET.tostring(wan_qos)))
                effective_path = WORK / ("effective_" + node + ".xml")
                effective_xml.write(effective_path)
                effective_qos = dds.QosProvider(str(effective_path)).participant_qos_from_profile(
                    "PREEMPTIVE_SPIKE::effective_" + node)
                actual_properties = dict(effective_qos.property.get_all())
                property_name = (
                    "dds.participant.inter_participant_data_reader.protocol.disable_preemptive_nack")
                assert actual_properties[property_name] == manifest["participant_properties"][property_name]
                manifest.setdefault("effective_participant_properties", {})[node] = actual_properties
            path = REPORTS / (variant + "_" + node + ".xml")
            ET.ElementTree(config).write(path)
            manifest.setdefault("configuration_sha256", {})[node] = sha(path)
            preflight(path)
            log_path = REPORTS / (variant + "_" + node + ".log")
            log = cleanup.enter_context(log_path.open("w"))
            env = dict(environment)
            if node == "A":
                env["LD_PRELOAD"] = str(WORK / "drop.so")
                env["SPIKE_DROP_CONTROL"] = str(WORK / (variant + "_drop"))
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "router/routing_service/run.py"),
                 "--xml", str(path)], env=env, stdout=log, stderr=subprocess.STDOUT)
            cleanup.callback(stop, process)
            wait(lambda: "rs.gateway.started" in log_path.read_text() or
                 (variant == "mcp_key" and "Unexpected property:" in log_path.read_text()),
                 variant + " " + node + " startup")
            return process

        start("A", 103)
        if variant == "mcp_key":
            log = (REPORTS / "mcp_key_A.log").read_text()
            assert "Unexpected property: dds.participant.inter_participant_data_reader." in log
            assert "rs.gateway.started" not in log
            stop(recorder)
            capture.chmod(0o644)
            result = {"variant": variant, "expected_native_rejection": True,
                      "reason": "Unexpected participant-prefixed property on DataReader",
                      "capture_sha256": sha(capture)}
            (REPORTS / "mcp_key_rejection.json").write_text(json.dumps(result, indent=2) + "\n")
            return result
        wait(lambda: (discover() or (len(discovered["writers"]) == 3 and
                                    len(discovered["readers"]) == 3)), "A WAN discovery")
        for kind in discovered:
            manifest["endpoints"].setdefault("A", {})[kind] = {
                name: key for key, name in discovered[kind].items()}
        time.sleep(1)
        start("B", 105)
        wait(lambda: (discover() or (len(discovered["writers"]) == 6 and
                                    len(discovered["readers"]) == 6)), "B WAN discovery")
        for kind in discovered:
            known = set(manifest["endpoints"]["A"][kind].values())
            manifest["endpoints"].setdefault("B", {})[kind] = {
                name: key for key, name in discovered[kind].items() if key not in known}
        for endpoint in manifest["endpoints"].values():
            for kind in ("writers", "readers"):
                assert set(endpoint[kind]) == {"Event", "State", "Periodic"}, endpoint
        time.sleep(3)

        participants = []
        for domain in (103, 105):
            participant = dds.DomainParticipant(domain, observer_qos)
            participants.append(participant)
            cleanup.callback(participant.close)
        topics = [dds.DynamicData.Topic(p, "DeviceCommands", types.type("DeviceCommand"))
                  for p in participants]
        writer = dds.DynamicData.DataWriter(
            dds.Publisher(participants[0]), topics[0],
            provider.datawriter_qos_from_profile("ACT_QOS_LIB::lan_event"))
        reader = dds.DynamicData.DataReader(
            dds.Subscriber(participants[1]), topics[1],
            provider.datareader_qos_from_profile("ACT_QOS_LIB::lan_event"))
        cleanup.callback(reader.close)
        cleanup.callback(writer.close)
        wait(lambda: writer.publication_matched_status.current_count > 0 and
             reader.subscription_matched_status.current_count > 0, "LAN endpoints")
        time.sleep(0.5)
        (WORK / (variant + "_drop")).write_text(
            manifest["endpoints"]["A"]["writers"]["Event"][-8:] + " 2\n")
        manifest["timeline"].append({"stage": "writes_start", "time": time.time()})
        for number in range(1, 9):
            data = dds.DynamicData(types.type("DeviceCommand"))
            data["request_id"] = f"command-{number}"
            data["opcode"] = number
            data["bytes"] = [number, 0, 127, 255]
            writer.write(data)
            time.sleep(0.03)
        received = {}

        def complete():
            for data, info in reader.take():
                if info.valid:
                    received[int(data["opcode"])] = {
                        "time": time.time(), "request_id": str(data["request_id"]),
                        "bytes": list(data["bytes"])}
            return len(received) == 8

        wait(complete, "all eight commands including repaired sequence 2", 15)
        time.sleep(2)
        manifest["received"] = received
        assert set(received) == set(range(1, 9)), received
        for number, row in received.items():
            assert row["request_id"] == f"command-{number}"
            assert row["bytes"] == [number, 0, 127, 255]
        stop(recorder)
        assert recorder.returncode == 0, "Capture failed"
        capture.chmod(0o644)
        capture_log.flush()
        assert "pcap:0/dumpcap:0" in (REPORTS / (variant + "_capture.log")).read_text()
        manifest["capture_sha256"] = sha(capture)
        manifest["drop_count"] = (REPORTS / (variant + "_A.log")).read_text().count("SPIKE_DROP")
        (REPORTS / (variant + "_manifest.json")).write_text(json.dumps(manifest, indent=2) + "\n")
        result = analyze(capture, manifest, REPORTS / (variant + "_decoded.json"))
        result["delivered"] = len(received)
        result["drop_count"] = manifest["drop_count"]
        return result


def main():
    assert set(Path("/sys/class/net").iterdir()) == {Path("/sys/class/net/lo")}
    invalid_element_probe()
    hashes = {str(path.relative_to(ROOT)): sha(path) for path in (
        BUILD / "libact_gateway_processor.so", BUILD / "act_rs_config", HERE / "qos.xml",
        ROOT / "harness_v2/qos/act_qos_profiles.xml", TYPES,
        ROOT / "router/routing_service/run.py", HERE / "drop.c", HERE / "runner.py",
        HERE / "wire.py")}
    sdk = Path(os.environ["NDDSHOME"])
    executable = next((sdk / "resource/app/bin").glob("*/rtiroutingserviceapp"))
    hashes[str(executable)] = sha(executable)
    header = sdk / "include/ndds/property/property_propertyNames.h"
    (REPORTS / "installed_preemptive_properties.txt").write_text("\n".join(
        line for line in header.read_text().splitlines() if "PREEMPTIVE" in line) + "\n")
    assert hashes["router/build/routing_service/libact_gateway_processor.so"] == (
        "022941caaef2604dd41657da494b9144f17e5bbb7fd1a6c7b816e4dfd007f94b")
    assert hashes["router/build/routing_service/act_rs_config"] == (
        "406e734e0f713443fd4dd2a16bcbd6e4665d9eae583660211abb9fd1820ab6bf")
    (REPORTS / "hashes.json").write_text(json.dumps(hashes, indent=2) + "\n")
    results = [case(variant) for variant in ("baseline", "mcp_key", "disabled")]
    for node in ("A", "B"):
        before = (REPORTS / ("baseline_" + node + ".xml")).read_text()
        after = (REPORTS / ("disabled_" + node + ".xml")).read_text()
        for family in ("event", "state", "periodic"):
            after = after.replace("PREEMPTIVE_SPIKE::disabled_" + family,
                                  "PREEMPTIVE_SPIKE::baseline_" + family)
        assert before == after, "Unexpected configuration difference"
    for relative, expected in hashes.items():
        path = Path(relative) if relative.startswith("/") else ROOT / relative
        assert sha(path) == expected, "Source or persistent artifact changed during qualification"
    compact = [{k: v for k, v in row.items()
                if k not in ("acknacks", "heartbeats", "data", "all_wan_acknacks")}
               for row in results]
    (REPORTS / "comparison.json").write_text(json.dumps(compact, indent=2) + "\n")
    print(json.dumps(compact, indent=2), flush=True)
    baseline, mcp_key, disabled = results
    assert baseline["base_zero_before_any_heartbeat"] > 0, "No proven baseline stimulus"
    assert disabled["base_zero_count"] == 0, "Preemptive base-zero ACKNACK remains"
    assert disabled["all_wan_user_base_zero_count"] == 0
    assert mcp_key["expected_native_rejection"]
    for result in (baseline, disabled):
        assert result["drop_count"] == 1, "Original DATA was not deterministically dropped"
        assert result["missing_sequence_2_requests"] > 0, "No decoded request for lost DATA"
        assert result["repair_request_precedes_sequence_2"], "No ordered repair evidence"
        assert next(row for row in result["acknacks"] if 2 in row["missing"])["missing"] == [2]
        assert sorted(row["sequence"] for row in result["data"]) == list(range(1, 9))
        assert result["delivered"] == 8
    print("PASS: base-zero startup ACKNACK suppressed; normal missing-sequence repair retained")
    (REPORTS / "PASS").write_text(
        "Baseline stimulus, MCP-key rejection, disabled startup and normal repair passed.\n"
        "XML differs only in three WAN reader profile selections. Binary hashes unchanged.\n")


if __name__ == "__main__":
    main()
