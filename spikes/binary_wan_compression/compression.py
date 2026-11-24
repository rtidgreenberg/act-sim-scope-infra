#!/usr/bin/env python3
"""Measure native Routing Service compression of fixed-envelope binary CDR."""

from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zlib

import rti.connextdds as dds

ROOT = Path("/workspace")
HERE = ROOT / "spikes/binary_wan_compression"
sys.path.insert(0, str(ROOT / "spikes/routing_service_lbed"))
import runner as rig
from cft import Admin

ALGORITHMS = {"none": 0, "lz4": 3, "zlib": 1, "bzip2": 2, "lz4_8k": 3}
WORKLOADS = ("small", "medium", "structured", "entropy", "precompressed")
COUNT = 32
REPETITIONS = 2
FIELDS = ("source", "original_topic", "instance_id", "destination", "schema_name",
          "schema_identity", "encoding", "operation")


def representation(name, kind):
    profile = ET.parse(HERE / "qos.xml").getroot().find(
        f"./qos_library/qos_profile[@name='{name}']")
    return copy.deepcopy(profile.find(f"{kind}_qos/representation"))


def service(path, node, algorithm, incompatible=False, dynamic=False):
    rig.service(path, node, "sedp")
    root = ET.parse(path).getroot()
    for family in rig.FAMILIES:
        writer = root.find(f".//topic_route[@name='tx{family}']/output/datawriter_qos")
        writer.append(representation(algorithm, "datawriter"))
        reader = root.find(f".//topic_route[@name='rx{family}']/input")
        profile = "reader_none" if incompatible and node == "B" and family == "Event" else "reader"
        reader.find("datareader_qos").append(representation(profile, "datareader"))
        expression = f"destination = '{node}'"
        if family == "Event":
            expression += " AND original_topic = '" + (
                "MoveCommand" if node == "B" else "CommandAck") + "'"
        filter_element = rig.child(reader, "content_filter")
        if dynamic and node == "B" and family == "Event":
            expression = "destination = %0 AND original_topic = 'MoveCommand'"
        rig.child(filter_element, "expression", expression)
        if dynamic and node == "B" and family == "Event":
            rig.child(rig.child(filter_element, "expression_parameters"), "element", "'B'")
    if dynamic and node == "B":
        admin = ET.Element("administration")
        rig.child(admin, "domain_id", 113)
        rig.child(admin, "domain_participant_qos", base_name="LBEDSpike::participant")
        rig.child(admin, "datareader_qos", base_name="LBEDSpike::event")
        rig.child(admin, "datawriter_qos", base_name="LBEDSpike::event")
        root.find("routing_service").insert(0, admin)
    rig.save(root, path)


def provider(path):
    root = rig.document()
    for source in (HERE / "types.xml",
                   ROOT / "spikes/serialized_wan_envelope/application_types.xml"):
        root.find("types").extend(copy.deepcopy(ET.parse(source).getroot().find("types")))
    rig.save(root, path)
    return dds.QosProvider(str(path))


def cdr(value):
    return bytes(ord(octet) for octet in value.to_cdr_buffer())


def envelope(types, original, source, topic, identity, destination=None):
    payload = cdr(original)
    fields = {
        "source": source, "original_topic": topic, "instance_id": identity,
        "destination": destination or ("B" if source == "A" else "A"),
        "schema_name": original.type.name, "schema_identity": "compression-fixture-v1",
        "encoding": "connext-cdr", "operation": "WRITE", "payload": list(payload),
    }
    value = dds.DynamicData(types.type(rig.TYPE), fields)
    return value, fields, original, len(cdr(value))


def command(types, sequence):
    return dds.DynamicData(types.type("MoveCommand"), {
        "id": "command", "sequence": sequence, "timestamp": 1700000000000000000 + sequence,
        "waypoints": [{"north": 12.5, "east": -7.25}, {"north": 14.5, "east": 8.75}],
        "limits": [2.5, 100.0, -0.5],
    })


def telemetry(types, kind, sequence):
    common = {"id": "sensor", "sequence": sequence,
              "timestamp": 1700000000000000000 + sequence}
    if kind == "small":
        return dds.DynamicData(types.type("NestedStatus"), {
            **common, "position": {"north": 12.5, "east": -7.25},
            "notes": "Nominal sensor operation", "enabled": True,
            "readings": list(range(8)),
        })
    if kind in ("medium", "structured"):
        count = 512 if kind == "medium" else 2048
        readings, blob = [float(index % 32) / 8 for index in range(count)], b""
    else:
        readings = []
        blob = random.Random(sequence).randbytes(32768)
        if kind == "precompressed":
            blob = zlib.compress(blob, level=6)
    return dds.DynamicData(types.type("BinaryTelemetry"), {
        **common, "readings": readings, "blob": list(blob),
    })


def verify(data, expected):
    _, fields, original, _ = expected
    assert {key: data[key] for key in FIELDS} == {key: fields[key] for key in FIELDS}
    assert list(data["payload"]) == fields["payload"], fields["instance_id"]
    restored = dds.DynamicData(original.type)
    restored.from_cdr_buffer(dds.Uint8Seq(data["payload"]))
    assert restored == original, fields["instance_id"]


def cpu_seconds(processes):
    ticks = os.sysconf("SC_CLK_TCK")
    total = 0
    for process in processes:
        assert process.poll() is None, process.returncode
        fields = Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()
        total += (int(fields[11]) + int(fields[12])) / ticks
    return total


def stage(types, probes, processes, kind, repetition):
    prefix = f"r{repetition}-{kind}"
    expected, commands, bulk = {}, [], []
    for index in range(COUNT):
        serial = repetition * 1000 + index
        original = command(types, serial)
        request = envelope(types, original, "A", "MoveCommand", f"{prefix}-c{index:02}")
        reply = envelope(types, original, "B", "CommandAck", f"{prefix}-c{index:02}")
        commands.append(request)
        expected[("B", "Event", request[1]["instance_id"])] = request
        expected[("A", "Event", reply[1]["instance_id"])] = reply
        values = {}
        for family in ("State", "Periodic"):
            item = envelope(types, telemetry(types, kind, serial), "A", kind,
                            f"{prefix}-{family}-{index:02}")
            values[family] = item
            expected[("B", family, item[1]["instance_id"])] = item
        bulk.append(values)
    rejected = envelope(types, command(types, 99999), "A", "MoveCommand",
                        prefix + "-blocked", destination="NotB")
    seen, sent_at, rtts, received = set(), {}, [], []

    def pump():
        for node, probe in probes.items():
            for family, reader in probe.readers.items():
                for data, info in reader.take():
                    if not info.valid:
                        continue
                    key = (node, family, data["instance_id"])
                    assert key in expected and key not in seen, key
                    seen.add(key)
                    received.append((dds.DynamicData(data), expected[key]))
                    if family == "Event":
                        index = int(data["instance_id"].rsplit("c", 1)[1])
                        if node == "B":
                            reply = dds.DynamicData(data)
                            reply["source"] = "B"
                            reply["destination"] = "A"
                            reply["original_topic"] = "CommandAck"
                            probes["B"].writers["Event"].write(reply)
                        else:
                            rtts.append((time.monotonic() - sent_at[index]) * 1000)

    started, cpu_start = time.time(), cpu_seconds(processes)
    wall = time.monotonic()
    for index in range(COUNT):
        for family, item in bulk[index].items():
            probes["A"].writers[family].write(item[0])
        sent_at[index] = time.monotonic()
        probes["A"].writers["Event"].write(commands[index][0])
        deadline = time.monotonic() + 10
        while len(rtts) != index + 1:
            pump()
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{prefix} command {index} did not return")
            if any(process.poll() is not None for process in processes):
                raise RuntimeError(f"{prefix}: Routing Service exited")
            if len(rtts) != index + 1:
                time.sleep(0.0001)
        if index % 8 == 0:
            probes["A"].writers["Event"].write(rejected[0])
        until = time.monotonic() + 0.005
        while time.monotonic() < until:
            pump()
            time.sleep(0.0005)
    rig.wait(lambda: (pump() or len(seen) == len(expected)),
             f"{prefix} complete payloads", processes, timeout=10)
    # Include any accidentally forwarded exclusion controls or trailing duplicates.
    until = time.monotonic() + 0.1
    while time.monotonic() < until:
        pump()
        time.sleep(0.001)
    elapsed = time.monotonic() - wall
    cpu = cpu_seconds(processes) - cpu_start
    ended = time.time()
    for data, item in received:
        verify(data, item)
    ordered = sorted(rtts)
    return {
        "workload": kind, "repetition": repetition, "start": started, "end": ended,
        "exact_deliveries": len(seen), "expected_deliveries": len(expected),
        "blocked_offers": 4, "command_count": len(rtts),
        "command_rtt_ms": {"median": statistics.median(rtts),
                           "p95": ordered[int(0.95 * (len(ordered) - 1))],
                           "max": max(rtts)},
        "command_rtt_samples_ms": rtts,
        "gateway_cpu_ms": cpu * 1000, "wall_seconds": elapsed,
        "inner_cdr_bytes": sum(len(item[1]["payload"]) for item in expected.values()),
        "outer_serialized_bytes": sum(item[3] for item in expected.values()),
        "command_serialized_bytes": commands[0][3],
        "telemetry_serialized_bytes": bulk[0]["State"][3],
    }


def wire(path, stages, algorithm):
    raw = subprocess.check_output([
        "tshark", "-r", str(path), "-Y", "rtps", "-T", "fields",
        "-e", "frame.time_epoch", "-e", "frame.len", "-e", "udp.payload"],
        text=True, stderr=subprocess.PIPE)
    packets = []
    for line in raw.splitlines():
        timestamp, length, payload = line.split("\t")
        packet = bytes.fromhex(payload.replace(":", ""))
        assert packet[:4] == b"RTPS"
        offset, samples = 20, []
        while offset + 4 <= len(packet):
            kind, flags = packet[offset:offset + 2]
            endian = "little" if flags & 1 else "big"
            size = int.from_bytes(packet[offset + 2:offset + 4], endian)
            body = packet[offset + 4:offset + 4 + size] if size else packet[offset + 4:]
            if kind in (0x15, 0x16) and len(body) >= 20:
                writer = body[8:12].hex()
                if writer in {"0003e902", "0003ea02", "0003eb02"}:
                    sequence = (int.from_bytes(body[12:16], endian, signed=True) << 32
                                | int.from_bytes(body[16:20], endian))
                    start = 4 + int.from_bytes(body[2:4], endian)
                    if flags & 2:
                        while start + 4 <= len(body):
                            pid = int.from_bytes(body[start:start + 2], endian)
                            width = int.from_bytes(body[start + 2:start + 4], endian)
                            start += 4
                            if pid == 1:
                                break
                            start += (width + 3) & ~3
                    first = kind == 0x15 or int.from_bytes(body[20:24], endian) == 1
                    compression_id = ((int.from_bytes(body[start + 2:start + 4], "big") >> 2)
                                      & 7) if first else None
                    samples.append((packet[8:20].hex(), writer, sequence, compression_id))
            offset += 4 + (size or len(body))
        packets.append((float(timestamp), int(length), samples))
    for result in stages:
        selected = [(size, samples) for stamp, size, samples in packets
                    if result["start"] <= stamp <= result["end"]]
        unique = {}
        for _, samples in selected:
            for prefix, writer, sequence, compression_id in samples:
                key = (prefix, writer, sequence)
                if key not in unique or compression_id is not None:
                    unique[key] = compression_id
        assert len(unique) == result["expected_deliveries"], (algorithm, result, len(unique))
        assert all(value is not None for value in unique.values()), "Missing first fragment"
        ids = sorted(set(unique.values()))
        assert set(ids) <= {0, ALGORITHMS[algorithm]}, (algorithm, ids)
        if algorithm == "none":
            assert ids == [0], ids
        if algorithm in ("lz4", "zlib", "bzip2") and result["workload"] in (
                "medium", "structured"):
            assert ALGORITHMS[algorithm] in ids, (algorithm, ids)
        if algorithm == "lz4_8k":
            event_ids = {value for (_, writer, _), value in unique.items()
                         if writer == "0003e902"}
            assert event_ids == {0}, event_ids
            if result["workload"] in ("small", "medium"):
                assert ids == [0], ids
            if result["workload"] == "structured":
                assert 3 in ids, ids
        result["wire"] = {
            "unique_writes": len(unique),
            "user_data_frame_bytes": sum(size for size, samples in selected if samples),
            "all_wan_frame_bytes": sum(size for size, _ in selected),
            "compression_ids": ids,
            "compressed_writes": sum(value != 0 for value in unique.values()),
        }


def dynamic_filters(types, probes, processes, admin):
    phases = []
    admin.command(1)
    for index, selected in enumerate(("B", "C", "B")):
        if index:
            admin.update((selected,))
            time.sleep(1)
        offered = [
            envelope(types, command(types, index), "A", "MoveCommand",
                     f"dynamic-{index}-{destination}", destination=destination)
            for destination in ("B", "C")]
        received = []
        started = time.time()
        for value in offered:
            probes["A"].writers["Event"].write(value[0])
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            for data, info in probes["B"].readers["Event"].take():
                if info.valid:
                    expected = next(value for value in offered
                                    if value[1]["destination"] == selected)
                    verify(data, expected)
                    received.append(data["destination"])
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("Routing Service exited during dynamic-filter control")
            time.sleep(0.001)
        assert received == [selected], (selected, received)
        phases.append({"workload": "dynamic_interest", "selected": selected,
                       "start": started, "end": time.time(),
                       "exact_deliveries": 1, "expected_deliveries": 1,
                       "offered": 2, "blocked_offers": 1})
    return phases


def run_case(work, algorithm, types, incompatible=False, admin_types=None):
    dynamic = admin_types is not None
    label = algorithm + ("_incompatible" if incompatible else "_dynamic" if dynamic else "")
    directory = work / label
    directory.mkdir()
    result = {"algorithm": label, "stages": []}
    with ExitStack() as cleanup:
        capture = rig.CAPTURE / f"{label}.pcapng"
        capture.touch()
        capture.chmod(0o666)
        log = cleanup.enter_context((rig.REPORT / f"{label}-capture.log").open("w"))
        recorder = subprocess.Popen(
            ["dumpcap", "-i", "lo", "-f", "udp dst portrange 35150-35399",
             "-a", "duration:180", "-w", str(capture)],
            stdout=log, stderr=subprocess.STDOUT)
        cleanup.callback(rig.stop, recorder)
        rig.wait(lambda: capture.stat().st_size > 0, "capture ready", timeout=5)
        processes = []
        for node in rig.DOMAINS:
            config = directory / f"{node}.xml"
            service(config, node, algorithm, incompatible, dynamic)
            log = cleanup.enter_context((rig.REPORT / f"{label}-{node}.log").open("w"))
            process = subprocess.Popen(
                [str(rig.SDK / "resource/app/bin/x64Linux4gcc8.5.0/rtiroutingserviceapp"),
                 "-cfgFile", str(config), "-cfgName", "Gateway" + node, "-verbosity", "2"],
                stdout=log, stderr=subprocess.STDOUT)
            cleanup.callback(rig.stop, process)
            processes.append(process)
        probes = {node: rig.Probe(node, types) for node in rig.DOMAINS}
        for probe in probes.values():
            cleanup.callback(probe.close)
        rig.wait(lambda: all(probe.matched() for probe in probes.values()),
                 "LAN matches", processes)
        time.sleep(3)
        if dynamic:
            admin = Admin(types, admin_types)
            cleanup.callback(admin.close)
            result["dynamic_filter_phases"] = dynamic_filters(types, probes, processes, admin)
            result["administration"] = admin.history
        elif incompatible:
            value = envelope(types, command(types, 1), "A", "MoveCommand", "incompatible")
            probes["A"].writers["Event"].write(value[0])
            until = time.monotonic() + 2
            while time.monotonic() < until:
                assert not any(info.valid for _, info in probes["B"].readers["Event"].take())
                time.sleep(0.02)
            result["expected_non_delivery"] = True
        else:
            for repetition in range(REPETITIONS):
                order = WORKLOADS if repetition == 0 else tuple(reversed(WORKLOADS))
                for kind in order:
                    result["stages"].append(stage(types, probes, processes, kind, repetition))
        for process in processes:
            rig.stop(process)
        rig.stop(recorder)
        if dynamic:
            wire(capture, result["dynamic_filter_phases"], algorithm)
            assert all(phase["wire"]["compressed_writes"] == 1
                       for phase in result["dynamic_filter_phases"])
        elif not incompatible:
            wire(capture, result["stages"], algorithm)
        capture_log = (rig.REPORT / f"{label}-capture.log").read_text()
        drops = re.search(r"Packets received/dropped on interface .*?: (\d+)/(\d+)", capture_log)
        assert drops and int(drops[2]) == 0, capture_log
        result["capture_dropped"] = int(drops[2])
        errors = []
        for node in rig.DOMAINS:
            log = (rig.REPORT / f"{label}-{node}.log").read_text()
            errors.extend(line for line in log.splitlines() if re.match(r"^(ERROR|FATAL)\b", line))
        result["native_error_lines"] = len(errors)
        result["qualified"] = not errors
        if errors:
            result["rejection"] = "Native compression errors; do not adopt this variant"
            result["first_native_errors"] = errors[:4]
            assert algorithm == "bzip2" and all(
                "RTIOsapi_Bzip2_compress:The output buffer length is not enough" in line
                for line in errors), (label, errors[:4])
        for config in directory.glob("*.xml"):
            (rig.REPORT / f"{label}-{config.name}").write_bytes(config.read_bytes())
    (rig.REPORT / f"{label}.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"algorithm": label, "stages": result["stages"],
                      "native_error_lines": result["native_error_lines"]}), flush=True)
    return result


def main():
    results = []
    os.environ["LD_LIBRARY_PATH"] = ":".join([
        str(rig.SDK / "resource/app/lib/x64Linux4gcc8.5.0"),
        str(rig.SDK / "lib/x64Linux4gcc8.5.0"), os.environ.get("LD_LIBRARY_PATH", "")])
    os.environ["LBED_CATALOG"] = "/tmp/unused-compression-catalog.xml"
    try:
        with tempfile.TemporaryDirectory(prefix="binary-wan-compression-") as directory:
            work = Path(directory)
            types = provider(work / "probe.xml")
            for algorithm in ALGORITHMS:
                results.append(run_case(work, algorithm, types))
            results.append(run_case(work, "lz4", types, incompatible=True))
            for name in ("ServiceCommon", "ServiceAdmin"):
                subprocess.run([
                    str(rig.SDK / "bin/rtiddsgen"), "-convertToXml", "-unboundedSupport",
                    "-I", str(rig.SDK / "resource/idl"), "-d", str(work),
                    str(rig.SDK / f"resource/idl/{name}.idl")], check=True)
            original_directory = Path.cwd()
            try:
                os.chdir(work)
                admin_types = dds.QosProvider(str(work / "ServiceAdmin.xml"))
                results.append(run_case(work, "zlib", types, admin_types=admin_types))
            finally:
                os.chdir(original_directory)
        remnants = [str(path) for path in Path("/dev/shm").iterdir()
                    if path.name.startswith(("RTI", "dds"))]
        assert not remnants, remnants
        baseline = {(stage["workload"], stage["repetition"]): stage
                    for stage in results[0]["stages"]}
        for result in results[1:]:
            for stage in result["stages"]:
                reference = baseline[(stage["workload"], stage["repetition"])]
                assert stage["outer_serialized_bytes"] == reference["outer_serialized_bytes"]
                stage["wire"]["user_data_saving_percent"] = 100 * (
                    1 - stage["wire"]["user_data_frame_bytes"]
                    / reference["wire"]["user_data_frame_bytes"])
        summary = {}
        for result in results:
            if not result["stages"]:
                continue
            summary[result["algorithm"]] = {}
            for kind in WORKLOADS:
                stages = [stage for stage in result["stages"] if stage["workload"] == kind]
                timings = sorted(value for stage in stages
                                 for value in stage["command_rtt_samples_ms"])
                baseline_bytes = sum(
                    baseline[(kind, stage["repetition"])]["wire"]["user_data_frame_bytes"]
                    for stage in stages)
                frame_bytes = sum(stage["wire"]["user_data_frame_bytes"] for stage in stages)
                summary[result["algorithm"]][kind] = {
                    "user_data_frame_bytes": frame_bytes,
                    "saving_percent": 100 * (1 - frame_bytes / baseline_bytes),
                    "gateway_cpu_ms": sum(stage["gateway_cpu_ms"] for stage in stages),
                    "command_rtt_p95_ms": timings[int(0.95 * (len(timings) - 1))],
                    "command_rtt_max_ms": max(timings),
                    "exact_deliveries": sum(stage["exact_deliveries"] for stage in stages),
                }
        sources = [HERE / name for name in ("compression.py", "qos.xml", "types.xml", "run.sh")]
        sources += [ROOT / "spikes/routing_service_lbed/runner.py",
                    ROOT / "spikes/routing_service_lbed/cft.py",
                    ROOT / "spikes/routing_service_lbed/qos.xml",
                    ROOT / "spikes/serialized_wan_envelope/envelope.xml",
                    ROOT / "spikes/serialized_wan_envelope/application_types.xml"]
        rejected = [result["algorithm"] for result in results if not result["qualified"]]
        report = {"result": "COMPLETED_WITH_REJECTED_VARIANTS" if rejected else "PASS",
                  "experiment_checks_passed": True, "rejected_variants": rejected,
                  "variants": results, "summary": summary, "shared_memory_remnants": remnants,
                  "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(
                      path.read_bytes()).hexdigest() for path in sources}}
        (rig.REPORT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        if not rejected:
            (rig.REPORT / "PASS").write_text("PASS\n")
        print(json.dumps({"result": report["result"], "rejected_variants": rejected}), flush=True)
        return 1 if rejected else 0
    except Exception as error:
        (rig.REPORT / "failure.json").write_text(json.dumps({
            "result": "FAIL", "error": str(error), "completed_variants": results}, indent=2) + "\n")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
