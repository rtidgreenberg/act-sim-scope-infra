#!/usr/bin/env python3
"""Matched union workloads through freshly built, fully monitored gateways."""

from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import re
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET

import rti.connextdds as dds
import yaml

ROOT = Path("/workspace")
HERE = ROOT / "spikes/full_union_compression"
WORK = Path("/work")
RESULTS = Path("/results")
sys.path.insert(0, str(ROOT / "router/routing_service"))
sys.path.insert(0, str(ROOT / "router/test_e2e/util"))
sys.path.insert(0, str(ROOT / "debug/scripts"))
from run import render, preflight
from dds_probe import Probe, reader_qos, writer_qos
from rtps_delivery import endpoint_metadata, submessages

FAMILIES = ("P1_Event", "P1_Periodic", "P2_Event", "P2_State", "P2_Periodic", "P3_Periodic")
NODES = (("Control_20", "control", 110), ("Platform_30", "platform", 112),
         ("Platform_31", "platform", 113))
CASES = (
    ("Mission", "control_command", "source", 0, 1),
    ("Team", "TeamAssignment", "platform_node", 0, 1),
    ("ActPlatformStatusMode", "PlatformStatusMode", "platform_node", 0, 1),
    ("PlatformCommandAck", "control_command_ack", "source", 1, 0),
    ("ContactReport", "contact_report", "contact_id", 1, 0),
    ("PlatformStatus", "platform_init_status", "source", 1, 0),
    ("PlatformData", "platform_data", "source", 1, 2),
    ("PlatformMissionStatus", "platform_mission_status", "source", 1, 0),
    ("PlatformWaypointStatus", "platform_waypoint_status", "source", 1, 0),
    ("PlatformDetailStatus", "platform_detail_status", "source", 1, 0),
    ("PlatformDebugStatus", "platform_debug_status", "source", 1, 0),
    ("PlatformThrusterStatus", "platform_thruster_status", "source", 1, 0),
    ("PlatformPowerStatus", "platform_power_status", "source", 1, 0),
)
COUNT = 64


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stop(process):
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            print(f"Graceful shutdown expired for owned PID {process.pid}", file=sys.stderr)
            process.kill()
            process.wait(timeout=5)


def wait(predicate, label, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out: " + label)


def cpu(processes):
    ticks = os.sysconf("SC_CLK_TCK")
    total = 0
    for process in processes:
        assert process.poll() is None, process.returncode
        fields = Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()
        total += (int(fields[11]) + int(fields[12])) / ticks
    return total


def prepare(node, role, domain, directory, algorithm, bandwidth_bps=4000000):
    config = yaml.safe_load((ROOT / "router/config/control-platform.yaml").read_text())
    config["router"].update(wan_contract="six_topic_union",
                            admin_participant="control_lan" if role == "control" else "platform_lan")
    config["qos_profiles"].update(
        wan_participant="ACT_QOS_LIB::rs_six_topic_participant_v6",
        wan_event="ACT_QOS_LIB::wan_three_topic_event",
        wan_contacts="ACT_QOS_LIB::wan_union_contacts",
        wan_periodic="ACT_QOS_LIB::wan_union_periodic")
    config["routing_service"] = {
        "async_bandwidth_bps": bandwidth_bps, "receive_policy": "mission",
        "topic_priorities": {"PlatformDebugStatus": "low"}}
    for name, participant in config["participants"].items():
        participant["domain"] = 111 if name.endswith("_wan") else domain
    for route in config["routes"]:
        route["enabled"] = True
    source = directory / f"{node}.yaml"
    source.write_text(yaml.safe_dump(config))
    command = [str(WORK / "build/routing_service/act_rs_config"), str(source),
               role, node, "rs", *(["TEAM_A"] if role == "platform" else [])]
    model = yaml.safe_load(subprocess.check_output(command, text=True))
    root = ET.fromstring(render(model, str(source),
                               str(WORK / "build/routing_service/libact_gateway_processor.so"), False))
    settings = ET.parse(ROOT / "spikes/binary_wan_compression/qos.xml")
    writer_rep = settings.find(
        f"./qos_library/qos_profile[@name='{algorithm}']/datawriter_qos/representation")
    reader_rep = settings.find("./qos_library/qos_profile[@name='reader']/datareader_qos/representation")
    effective = {}
    for family in FAMILIES:
        writer = root.find(f".//output[@name='wan_out_{family}']/datawriter_qos")
        reader = root.find(f".//input[@name='wan_in_{family}']/datareader_qos")
        assert writer is not None and reader is not None, family
        writer.append(copy.deepcopy(writer_rep))
        reader.append(copy.deepcopy(reader_rep))
        # Read the exact composed writer policy, not just the chosen algorithm.
        library = root.find("./qos_library[@name='ACT_QOS_LIB']")
        profile = ET.SubElement(library, "qos_profile", name="measurement_" + family,
                                base_name=writer.attrib["base_name"])
        override = copy.deepcopy(writer)
        override.attrib.clear()
        profile.append(override)
    path = directory / f"{node}.xml"
    ET.ElementTree(root).write(path)
    preflight(str(path))
    qos_provider = dds.QosProvider(str(path))
    for family in FAMILIES:
        qos = qos_provider.datawriter_qos_from_profile("ACT_QOS_LIB::measurement_" + family)
        assert qos.publish_mode.kind == dds.PublishModeKind.ASYNCHRONOUS
        assert qos.publish_mode.flow_controller_name == "dds.flow_controller.token_bucket.act_rs"
        assert len(qos.multi_channel.channels) == 2
        effective[family] = {
            "asynchronous": True, "controller": qos.publish_mode.flow_controller_name,
            "reliability": str(qos.reliability.kind), "history": str(qos.history.kind),
            "priority_channels": [c.filter_expression for c in qos.multi_channel.channels],
            "transport_priority": qos.transport_priority.value,
            "compression": str(qos.data_representation.compression_settings.compression_ids)}
    return path, effective


def sample(probes, index, sequence, mode):
    topic, type_name, key, source, _ = CASES[index]
    node = NODES[source][0]
    value = probes[source].sample(type_name, **{key: node if key != "contact_id" else f"c-{sequence}"})
    if key in ("source", "contact_id"):
        value["run_id"] = "full-compression"
        value["source_node"] = node
        value["audit_sequence"] = sequence
        value["sent_at_ns"] = time.time_ns()
        value["timestamp"] = 1000000 + sequence
    if topic == "Mission":
        value["destination"] = "Platform_30"
        value["command_id"] = f"command-{sequence}"
        value["command_type"] = "COMPRESSION_TEST"
        value["payload"] = ([sequence % 256] * 8192 if mode != "entropy" else
                            list(random.Random(sequence).randbytes(8192))) if mode != "small" else [1, 2, 3]
    elif topic == "PlatformCommandAck":
        value["destination"] = "Control_20"
        value["command_id"] = f"command-{sequence}"
        value["accepted"] = True
        value["message"] = "accepted"
    elif topic == "Team":
        value["platform_node"] = "Platform_30"
        value["team_name"] = "TEAM_A"
    elif topic == "ActPlatformStatusMode":
        value["platform_node"] = "Platform_30"
        value["request_id"] = "mode-control"
    elif topic == "ContactReport":
        value["source"] = "Platform_30"
        value["classification"] = "SURFACE"
        value["range_m"] = 123.5
        value["confidence_pct"] = 95.0
    elif topic == "PlatformMissionStatus":
        value["mission_phase"] = "ORBIT"
        value["mission_id"] = "compression"
    return value


def drain(readers):
    for reader in readers.values():
        reader.take()


def stage(mode, sequence_start, probes, writers, readers, processes):
    expected = {}
    requests = []
    telemetry = []
    indices = (4, 5, 7, 8, 9, 10, 11, 12)
    for offset in range(COUNT):
        sequence = sequence_start + offset
        request = sample(probes, 0, sequence, mode)
        ack = sample(probes, 3, sequence, mode)
        requests.append(request)
        expected[(0, sequence)] = request
        expected[(3, sequence)] = ack
        row = []
        for index in indices:
            value = sample(probes, index, sequence, mode)
            expected[(index, sequence)] = value
            row.append((index, value))
        telemetry.append(row)
    drain(readers)
    seen, received, send_times, rtts = set(), [], {}, []

    def pump():
        for index, reader in readers.items():
            if index in (1, 2, 6):
                continue
            for data, info in reader.take():
                if not info.valid or data["run_id"] != "full-compression":
                    continue
                sequence = int(data["audit_sequence"])
                if sequence < sequence_start:
                    continue
                key = (index, sequence)
                assert key in expected and key not in seen, key
                seen.add(key)
                received.append((dds.DynamicData(data), expected[key]))
                if index == 0:
                    writers[3].write(expected[(3, sequence)])
                elif index == 3:
                    rtts.append((time.monotonic() - send_times[sequence]) * 1000)

    started, cpu_start, monotonic_start = time.time(), cpu(processes), time.monotonic()
    for offset, request in enumerate(requests):
        for index, value in telemetry[offset]:
            writers[index].write(value)
        sequence = sequence_start + offset
        send_times[sequence] = time.monotonic()
        writers[0].write(request)
        if mode != "burst":
            wait(lambda: (pump() or (3, sequence) in seen), f"command ACK {sequence}", 10)
        else:
            pump()
    # Production LAN time filters may drop the last update of a fast burst.
    # Publish a distinct final marker after their one-second filter window.
    settle_end = time.monotonic() + 1.2
    while time.monotonic() < settle_end:
        pump()
        time.sleep(0.005)
    final_sequence = sequence_start + COUNT
    for index in indices:
        if index != 4:
            value = sample(probes, index, final_sequence, mode)
            expected[(index, final_sequence)] = value
            writers[index].write(value)
    required = {key for key in expected if key[0] in (0, 3, 4)
                or key[1] == final_sequence}
    def complete():
        pump()
        return required <= seen
    try:
        wait(complete, mode + " Events, distinct contacts and final periodic values", 30)
    except AssertionError:
        raise AssertionError(f"{mode}: missing required deliveries {sorted(required - seen)}")
    elapsed = time.monotonic() - monotonic_start
    cpu_ms = (cpu(processes) - cpu_start) * 1000
    ended = time.time()
    # Equality validation is outside the timed interval.
    for data, original in received:
        assert data == original, (mode, str(original))
    ordered = sorted(rtts)
    return {
        "workload": mode, "start": started, "end": ended, "wall_seconds": elapsed,
        "gateway_cpu_ms": cpu_ms, "exact_deliveries": len(seen),
        "offered_deliveries": len(expected), "required_deliveries": len(required),
        "periodic_intermediates_not_observed": len(expected) - len(seen),
        "commands": len(rtts), "rtt_ms": {"median": statistics.median(rtts),
                                          "p95": ordered[int(.95 * (len(ordered) - 1))],
                                          "max": max(rtts)},
        "original_sample_bytes": sum(len(v.to_cdr_buffer()) for v in expected.values())}


def wire(capture, stages, endpoints=None):
    output = subprocess.check_output([
        "tshark", "-r", str(capture), "-Y", "rtps", "-T", "fields",
        "-e", "frame.time_epoch", "-e", "frame.len", "-e", "udp.payload"], text=True)
    decoded = []
    discovered = {}
    for line in output.splitlines():
        stamp, size, raw = line.split("\t")
        packet = bytes.fromhex(raw.replace(":", ""))
        # Connext's 16-byte NDDS PING is not a full RTPS header.
        messages = () if len(packet) == 16 and packet[8:] == b"NDDSPING" else submessages(packet)
        items = list(messages)
        for item in items:
            if "payload" in item and item["writer_guid"].endswith("000003c2"):
                metadata = endpoint_metadata(item["payload"])
                if metadata and metadata["topic"] in FAMILIES:
                    discovered[metadata["writer_guid"]] = metadata["topic"]
        decoded.append((float(stamp), int(size), items))
    if endpoints is None:
        endpoints = discovered
    assert len(endpoints) == 18, endpoints
    capture.with_suffix(".writers.json").write_text(json.dumps(endpoints, indent=2) + "\n")
    channel_endpoints = {}
    for guid, family in endpoints.items():
        for channel in (1, 2):
            channel_guid = guid[:24] + f"{int(guid[24:], 16) + (channel << 8):08x}"
            assert channel_guid not in endpoints and channel_guid not in channel_endpoints
            channel_endpoints[channel_guid] = family
    packets = []
    for stamp, size, items in decoded:
        records = []
        for item in items:
            guid = item["writer_guid"]
            family = endpoints.get(guid)
            if family is None and guid[24:26] == "80":
                # Capture-verified channel keys are the published writer key + 1/+2.
                family = channel_endpoints.get(guid)
                assert family is not None, ("Unmapped native channel writer", guid)
            if family and "payload" in item:
                first = item.get("first", 1) == 1
                payload = item["payload"]
                assert not first or len(payload) >= 4, "Missing CDR encapsulation"
                ident = ((int.from_bytes(payload[2:4], "big") >> 2) & 7) if first else None
                records.append((guid[:24], guid[24:], item["sequence"], family, ident,
                                item.get("first", 1)))
        packets.append((stamp, size, records))
    assert set(FAMILIES) <= {record[3] for _, _, records in packets for record in records}
    for stage_result in stages:
        chosen = [(size, records) for stamp, size, records in packets
                  if stage_result["start"] <= stamp <= stage_result["end"]]
        ids = {}
        wire_keys = {}
        for _, records in chosen:
            for prefix, entity, sequence, family, ident, first in records:
                wire_keys[(prefix, entity, sequence)] = family
                if ident is not None:
                    ids[(prefix, entity, sequence)] = (family, ident)
        stage_result["wire"] = {
            "all_wan_frame_bytes": sum(size for size, _ in chosen),
            "all_gateway_data_frame_bytes": sum(size for size, records in chosen if records),
            "application_data_frame_bytes": sum(size for size, records in chosen
                if any(record[3].startswith(("P2_", "P3_")) for record in records)),
            "unique_gateway_writes": len(ids),
            "command_and_ack_writes": sum(family == "P2_Event" for family, _ in ids.values()),
            "command_and_ack_writes_including_continuation_fragments": sum(
                family == "P2_Event" for family in wire_keys.values()),
            "compressed_writes": sum(ident != 0 for _, ident in ids.values()),
            "fragment_frames": sum(any(record[5] > 1 for record in records)
                                  for _, records in chosen),
            "compression_ids": sorted({ident for _, ident in ids.values()}),
            "data_frame_bytes_by_family": {family: sum(size for size, records in chosen
                if any(record[3] == family for record in records)) for family in FAMILIES}}
        assert ids, "No gateway user DATA captured"
        assert stage_result["wire"]["data_frame_bytes_by_family"]["P2_State"] > 0
        assert stage_result["wire"]["data_frame_bytes_by_family"]["P3_Periodic"] > 0


def capture_drops(path):
    text = path.read_text()
    counts = re.findall(r"Packets received/dropped on interface .*?: (\d+)/(\d+)", text)
    assert counts and "Packets captured:" in text, text
    assert all(int(dropped) == 0 for _, dropped in counts), counts
    return sum(int(dropped) for _, dropped in counts)


def comparison(cases):
    rows = []
    for workload in ("small", "structured", "entropy", "burst"):
        baseline = [s for c in cases if c["algorithm"] == "none"
                    for s in c["stages"] if s["workload"] == workload]
        for algorithm in ("none", "lz4", "zlib"):
            stages = [s for c in cases if c["algorithm"] == algorithm
                      for s in c["stages"] if s["workload"] == workload]
            mean = lambda values: statistics.mean(values)
            row = {"workload": workload, "algorithm": algorithm,
                   "runs": len(stages),
                   "wall_seconds": mean(s["wall_seconds"] for s in stages),
                   "gateway_cpu_ms": mean(s["gateway_cpu_ms"] for s in stages),
                   "mean_run_rtt_median_ms": mean(s["rtt_ms"]["median"] for s in stages),
                   "worst_rtt_ms": max(s["rtt_ms"]["max"] for s in stages)}
            for field in ("all_wan_frame_bytes", "all_gateway_data_frame_bytes"):
                row[field] = mean(s["wire"][field] for s in stages)
                row[field + "_saving_pct"] = 100 * (
                    1 - row[field] / mean(s["wire"][field] for s in baseline))
            row["command_ack_frame_bytes"] = mean(
                s["wire"]["data_frame_bytes_by_family"]["P2_Event"] for s in stages)
            row["command_ack_saving_pct"] = 100 * (1 - row["command_ack_frame_bytes"] / mean(
                s["wire"]["data_frame_bytes_by_family"]["P2_Event"] for s in baseline))
            rows.append(row)
    return rows


def reanalyze():
    path = RESULTS / "report.json"
    report = json.loads(path.read_text())
    report["status"] = "FAIL"
    path.write_text(json.dumps(report, indent=2) + "\n")
    assert {(c["repetition"], c["algorithm"]) for c in report["cases"]} == {
        (r, a) for r in (1, 2) for a in ("none", "lz4", "zlib")}
    assert len(report["cases"]) == 6 and not report["private_shm_remnants"]
    for case in report["cases"]:
        assert case["native_error_lines"] == case["baseline_config_publish_failures"] == 0
        assert len(case["stages"]) == 4 and all(
            s["commands"] == COUNT and s["exact_deliveries"] >= s["required_deliveries"]
            for s in case["stages"])
        label = f"{case['repetition']}-{case['algorithm']}"
        case["capture_drops"] = capture_drops(RESULTS / (label + "-capture.log"))
        case["capture_sha256"] = digest(RESULTS / (label + ".pcapng"))
        wire(RESULTS / (label + ".pcapng"), case["stages"])
        allowed = {"none": {0}, "lz4": {0, 3}, "zlib": {0, 1}}[case["algorithm"]]
        for stage_result in case["stages"]:
            ids = set(stage_result["wire"]["compression_ids"])
            assert ids <= allowed, (label, ids)
            if case["algorithm"] != "none" and stage_result["workload"] == "structured":
                assert allowed - {0} <= ids, (label, ids)
        (RESULTS / (label + ".json")).write_text(json.dumps(case, indent=2) + "\n")
    report["wire_reanalysis"] = {
        "decoder_sha256": digest(ROOT / "debug/scripts/rtps_delivery.py"),
        "runner_sha256": digest(HERE / "runner.py"),
        "reason": "Verify retained captures, native DATA_FRAG_SESSION and channel attribution"}
    report["comparison"] = comparison(report["cases"])
    report["compression_only_xml_changes"] = verify_xml_equivalence(report["cases"])
    report["status"] = "PASS"
    path.write_text(json.dumps(report, indent=2) + "\n")


def verify_xml_equivalence(cases):
    result = {}
    for node, _, _ in NODES:
        hashes = []
        for case in cases:
            label = f"{case['repetition']}-{case['algorithm']}"
            root = ET.parse(RESULTS / (label + "-" + node + ".xml"))
            for writer in (root.findall(".//output/datawriter_qos")
                           + root.findall("./qos_library/qos_profile/datawriter_qos")):
                for representation in writer.findall("representation"):
                    writer.remove(representation)
            hashes.append(hashlib.sha256(ET.tostring(root.getroot())).hexdigest())
        assert len(set(hashes)) == 1, (node, hashes)
        result[node] = hashes[0]
    return result


def run_case(algorithm, repetition):
    label = f"{repetition}-{algorithm}"
    directory = Path(tempfile.mkdtemp(prefix=label + "-", dir=WORK))
    directory.chmod(0o777)
    report = {"algorithm": algorithm, "repetition": repetition, "stages": []}
    home = Path(os.environ["NDDSHOME"])
    executable, = (home / "resource/app/bin").glob("*/rtiroutingserviceapp")
    environment = dict(os.environ, LD_LIBRARY_PATH=os.pathsep.join([
        str(home / "resource/app/lib" / executable.parent.name),
        str(home / "lib" / executable.parent.name), os.environ.get("LD_LIBRARY_PATH", "")]))
    processes = []
    logs = []
    with ExitStack() as cleanup:
        capture = RESULTS / f"{label}.pcapng"
        capture.touch()
        capture.chmod(0o666)
        log = cleanup.enter_context((RESULTS / f"{label}-capture.log").open("w"))
        recorder = subprocess.Popen(["dumpcap", "-i", "lo", "-f", "ip6 and udp dst portrange 35150-35399",
                                     "-a", "duration:180", "-w", str(capture)],
                                    stdout=log, stderr=subprocess.STDOUT)
        cleanup.callback(stop, recorder)
        wait(lambda: capture.stat().st_size > 0, "capture ready", 5)
        combined = ET.parse(ROOT / "harness_v2/qos/act_qos_profiles.xml")
        combined.getroot().extend(ET.parse(HERE / "qos.xml").getroot().findall("qos_library"))
        probe_qos = directory / "probe.xml"
        combined.write(probe_qos)
        observer = dds.DomainParticipant(111, dds.QosProvider(str(probe_qos)).participant_qos_from_profile(
            "FullCompressionProbe::wan"))
        cleanup.callback(observer.close)
        publications = observer.publication_reader
        subscriptions = observer.subscription_reader
        probes = []
        report["effective_writers"] = {}
        for node, role, domain in NODES:
            path, effective = prepare(node, role, domain, directory, algorithm)
            report["effective_writers"][node] = effective
            shutil_target = RESULTS / f"{label}-{node}.xml"
            shutil_target.write_bytes(path.read_bytes())
            native_log = RESULTS / f"{label}-{node}.log"
            logs.append(native_log)
            opened = cleanup.enter_context(native_log.open("w"))
            process = subprocess.Popen([str(executable), "-cfgFile", str(path),
                                        "-cfgName", "ACTGateway", "-verbosity", "WARN"],
                                       env=environment, stdout=opened, stderr=subprocess.STDOUT)
            processes.append(process)
            cleanup.callback(stop, process)
            probe = Probe(domain)
            probes.append(probe)
            cleanup.callback(probe.close)
        wait(lambda: all("rs.gateway.started" in path.read_text() for path in logs), "full startup")
        writers = {i: probes[src].writer(topic, dtype, writer_qos("lan_event"))
                   for i, (topic, dtype, _, src, _) in enumerate(CASES)}
        readers = {i: probes[dst].reader(topic, dtype, reader_qos("lan_event"))
                   for i, (topic, dtype, _, _, dst) in enumerate(CASES)}
        health = probes[0].reader("ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))
        health_observations = []
        def all_alive():
            for data, info in health.take():
                if info.valid:
                    peers = {str(peer["health.router"]): str(peer["presence"])
                             for peer in data["peers"]}
                    health_observations.append(peers)
                    (RESULTS / (label + "-presence.json")).write_text(json.dumps({
                        "matched_writers": health.subscription_matched_status.current_count,
                        "observations": health_observations}, indent=2))
                    if all(peers.get(node + "/rs") == "PRESENCE_ALIVE"
                           for node in ("Platform_30", "Platform_31")):
                        return True
            return False
        wait(all_alive, "both monitored peers ALIVE")
        for node_index in (2,):
            w = probes[node_index].writer("PlatformMissionStatus", "platform_mission_status", writer_qos("lan_event"))
            value = probes[node_index].sample("platform_mission_status", source=NODES[node_index][0],
                source_node=NODES[node_index][0], mission_phase="ORBIT", run_id="warmup",
                sent_at_ns=time.time_ns(), audit_sequence=1)
            wait(lambda: w.publication_matched_status.current_count > 0, "mission input")
            w.write(value)
        time.sleep(2)
        for index, (_, _, key, _, _) in enumerate(CASES):
            if index in (2, 6):
                continue
            value = sample(probes, index, 1, "small")
            value_key = value[key]
            writers[index].write(value)
            def deliver():
                return any(info.valid and data[key] == value_key and data == value
                           for data, info in readers[index].take())
            wait(deliver, f"whole original {CASES[index][0]}")
        for index in (2, 6):
            readers[index].take()
            writers[index].write(sample(probes, index, 2, "small"))
            time.sleep(0.5)
            assert not any(info.valid for data, info in readers[index].take()), CASES[index][0]
        report["mission_policy_excluded"] = [CASES[index][0] for index in (2, 6)]
        time.sleep(1)
        endpoints = {}
        for data, info in publications.read():
            if info.valid and data.topic_name in FAMILIES:
                handle = str(info.instance_handle).lower().replace(":", "").replace("-", "")
                assert len(handle) == 32, handle
                endpoints[handle] = data.topic_name
        assert len(endpoints) == 18, endpoints
        assert len([1 for data, info in subscriptions.read()
                    if info.valid and data.topic_name in FAMILIES]) == 18
        report["endpoint_counts"] = {"writers": 18, "readers": 18}
        report["full_monitoring_peers_alive"] = True
        for index, mode in enumerate(("small", "structured", "entropy", "burst")):
            report["stages"].append(stage(mode, 1000 + index * 1000, probes, writers, readers, processes))
            print(json.dumps({"case": label, **report["stages"][-1]}), flush=True)
        # Lifecycle uses the actual composite contact key, then a valid revival.
        value = sample(probes, 4, 9999, "small")
        writers[4].write(value)
        wait(lambda: any(info.valid and data == value for data, info in readers[4].take()), "contact materialization")
        handle = writers[4].register_instance(value)
        writers[4].dispose_instance(handle)
        wait(lambda: any(info.state.instance_state == dds.InstanceState.NOT_ALIVE_DISPOSED
                         for _, info in readers[4].take()), "contact disposal")
        writers[4].write(value)
        wait(lambda: any(info.valid and data == value for data, info in readers[4].take()), "contact revival")
        report["contact_dispose_revive"] = True
        stop(recorder)
        report["capture_drops"] = capture_drops(RESULTS / f"{label}-capture.log")
        report["capture_sha256"] = digest(capture)
        wire(capture, report["stages"], endpoints)
        allowed_ids = {"none": {0}, "lz4": {0, 3}, "zlib": {0, 1}}[algorithm]
        for result in report["stages"]:
            ids = set(result["wire"]["compression_ids"])
            assert ids <= allowed_ids, (algorithm, ids)
            if algorithm != "none" and result["workload"] == "structured":
                assert allowed_ids - {0} <= ids, (algorithm, ids)
    errors = [line for path in logs for line in path.read_text().splitlines()
              if re.search(r"level=ERROR|ERROR|FATAL", line)]
    assert not errors, errors[:20]
    report["native_error_lines"] = 0
    report["baseline_config_publish_failures"] = sum(
        path.read_text().count("event=router_config_publish_failed") for path in logs)
    (RESULTS / f"{label}.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    report = {"status": "FAIL", "scope": "Latest full monitored union processor, three gateways, "
              "six_topic_union IPv6, shared 4 Mbps async controller; isolated loopback, not EMANE",
              "source_revision": subprocess.check_output(
                  ["git", "-c", "safe.directory=/workspace", "rev-parse", "HEAD"], text=True).strip(),
              "cases": []}
    try:
        assert {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}
        for index in range(1, 12):
            os.environ["WAN_PEER" + (str(index) if index > 1 else "")] = "[0-15]@builtin.udpv4://127.0.0.1"
            os.environ["WAN_PEER_V6" + (f"_{index}" if index > 1 else "")] = "::1"
        report["binary_hashes"] = {name: digest(WORK / "build/routing_service" / name)
            for name in ("libact_gateway_processor.so", "act_rs_config")}
        report["source_hashes"] = {str(p.relative_to(ROOT)): digest(p)
            for p in [HERE / "runner.py", HERE / "qos.xml",
                      ROOT / "harness_v2/qos/act_qos_profiles.xml",
                      ROOT / "harness_v2/datamodel/gen/ActTypes.xml",
                      ROOT / "harness_v2/datamodel/ActTypes.idl",
                      ROOT / "router/routing_service/GatewayMonitor.cxx",
                      ROOT / "router/routing_service/run.py",
                      ROOT / "debug/scripts/rtps_delivery.py"]}
        for repetition, algorithms in ((1, ("none", "lz4", "zlib")), (2, ("zlib", "lz4", "none"))):
            for algorithm in algorithms:
                report["cases"].append(run_case(algorithm, repetition))
        report["comparison"] = comparison(report["cases"])
        report["compression_only_xml_changes"] = verify_xml_equivalence(report["cases"])
        report["status"] = ("COMPLETED_WITH_BASELINE_FAILURES" if any(
            case["baseline_config_publish_failures"] for case in report["cases"]) else "PASS")
    except Exception:
        report["error"] = traceback.format_exc()
        print(report["error"], file=sys.stderr)
    finally:
        report["private_shm_remnants"] = [p.name for p in Path("/dev/shm").iterdir()
                                        if p.name.startswith(("RTI", "dds"))]
        if report["private_shm_remnants"]:
            report["status"] = "FAIL"
        (RESULTS / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--reanalyze"]:
        reanalyze()
    else:
        sys.exit(main())
