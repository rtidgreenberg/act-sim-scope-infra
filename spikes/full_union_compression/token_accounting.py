#!/usr/bin/env python3
"""Measure native union throughput against compressed/uncompressed budget models."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET

import runner as base

COUNT = 128
BUDGETS = (2000000, 4000000)
HERE = base.HERE
RESULTS = base.RESULTS
dds = base.dds


def measure(mode, probes, writers, readers, processes):
    requests = [base.sample(probes, 0, 1000 + i, mode) for i in range(COUNT)]
    replies = [base.sample(probes, 3, 1000 + i, mode) for i in range(COUNT)]
    expected = {int(value["audit_sequence"]): value for value in requests}
    acknowledgements = {int(value["audit_sequence"]): value for value in replies}
    for reader in readers.values():
        reader.take()
    seen = {0: set(), 3: set()}
    received = []
    start = time.time()
    started = time.monotonic()
    cpu_start = base.cpu(processes)
    for request in requests:
        writers[0].write(request)
    enqueue_seconds = time.monotonic() - started

    def complete():
        for index, reader in readers.items():
            for data, info in reader.take():
                if not info.valid or data["run_id"] != "full-compression":
                    continue
                sequence = int(data["audit_sequence"])
                assert sequence in expected and sequence not in seen[index], (index, sequence)
                seen[index].add(sequence)
                received.append((dds.DynamicData(data),
                                 expected[sequence] if index == 0 else acknowledgements[sequence]))
                if index == 0:
                    writers[3].write(acknowledgements[sequence])
        return len(seen[3]) == COUNT

    base.wait(complete, "all exact command/ACK exchanges", 60)
    end = time.time()
    seconds = time.monotonic() - started
    cpu_ms = (base.cpu(processes) - cpu_start) * 1000
    assert len(seen[0]) == len(seen[3]) == COUNT
    for data, original in received:
        assert data == original, str(original)
    return {"start": start, "end": end, "seconds": seconds,
            "enqueue_seconds": enqueue_seconds, "gateway_cpu_ms": cpu_ms,
            "commands": COUNT, "exact_payload_comparisons": len(received),
            "original_command_cdr_bytes": sum(len(v.to_cdr_buffer()) for v in requests)}


def analyze_wire(capture, measurement, control_writers, all_writers):
    channels = {guid[:24] + f"{int(guid[24:], 16) + (channel << 8):08x}": family
                for guid, family in all_writers.items() for channel in (1, 2)}
    control_prefixes = {guid[:24] for guid in control_writers}
    output = subprocess.check_output([
        "tshark", "-r", str(capture), "-Y", "rtps", "-T", "fields",
        "-e", "frame.time_epoch", "-e", "frame.len", "-e", "udp.dstport",
        "-e", "udp.payload"], text=True)
    frame_bytes = all_bytes = fragment_frames = 0
    first_samples = []
    stamps = []
    destinations = set()
    destination_ports = set()
    compressed_ids = set()
    sizes = []
    for line in output.splitlines():
        stamp, length, port, raw = line.split("\t")
        stamp, length = float(stamp), int(length)
        if not measurement["start"] <= stamp <= measurement["capture_end"]:
            continue
        all_bytes += length
        packet = bytes.fromhex(raw.replace(":", ""))
        if len(packet) == 16 and packet[8:] == b"NDDSPING":
            continue
        selected = []
        for item in base.submessages(packet):
            guid = item["writer_guid"]
            family = all_writers.get(guid, channels.get(guid))
            if guid[:24] in control_prefixes and family == "P2_Event" and "payload" in item:
                selected.append(item)
                destinations.add(item["destination_prefix"])
                if item.get("first", 1) == 1:
                    payload = item["payload"]
                    assert len(payload) >= 4
                    compressed_ids.add((int.from_bytes(payload[2:4], "big") >> 2) & 7)
                    sizes.append(item.get("sample_size", len(payload)))
                    first_samples.append((guid, item["sequence"]))
        if selected:
            destination_ports.add(int(port))
            frame_bytes += length
            fragment_frames += int(any("first" in item for item in selected))
            stamps.append(stamp)
    assert len(stamps) >= 2 and sizes, "No measured command DATA"
    assert len(destination_ports) == 2, ("Unexpected native WAN fan-out", destination_ports)
    assert max(stamps) < measurement["capture_end"] - .25, "Command DATA did not settle"
    assert len({prefix for prefix in destinations if prefix is not None}) <= 1, destinations
    return {"command_data_frame_bytes": frame_bytes, "all_wan_frame_bytes": all_bytes,
            "command_wire_span_seconds": max(stamps) - min(stamps),
            "command_frame_count": len(stamps), "fragment_frame_count": fragment_frames,
            "first_sample_submessages": len(first_samples),
            "median_serialized_wire_sample_bytes": statistics.median(sizes),
            "compression_ids": sorted(compressed_ids),
            "physical_destination_ports": sorted(destination_ports),
            "destination_prefixes": sorted(destinations, key=lambda v: v or "")}


def run_case(algorithm, budget, mode, repetition):
    label = f"{repetition}-{budget}-{mode}-{algorithm}"
    directory = Path(tempfile.mkdtemp(prefix=label + "-", dir=base.WORK))
    directory.chmod(0o777)
    report = {"label": label, "algorithm": algorithm, "budget_bps": budget,
              "workload": mode, "repetition": repetition, "effective_writers": {}}
    home = Path(os.environ["NDDSHOME"])
    executable, = (home / "resource/app/bin").glob("*/rtiroutingserviceapp")
    env = dict(os.environ, LD_LIBRARY_PATH=os.pathsep.join([
        str(home / "resource/app/lib" / executable.parent.name),
        str(home / "lib" / executable.parent.name), os.environ.get("LD_LIBRARY_PATH", "")]))
    logs, processes, probes = [], [], []
    with ExitStack() as cleanup:
        capture = RESULTS / (label + ".pcapng")
        capture.touch()
        capture.chmod(0o666)
        capture_log = RESULTS / (label + "-capture.log")
        opened = cleanup.enter_context(capture_log.open("w"))
        recorder = subprocess.Popen([
            "dumpcap", "-i", "lo", "-f", "ip6 and udp dst portrange 35150-35399",
            "-a", "duration:180", "-w", str(capture)],
            stdout=opened, stderr=subprocess.STDOUT)
        cleanup.callback(base.stop, recorder)
        base.wait(lambda: capture.stat().st_size > 0, "capture ready", 5)
        combined = ET.parse(base.ROOT / "harness_v2/qos/act_qos_profiles.xml")
        combined.getroot().extend(ET.parse(HERE / "qos.xml").getroot().findall("qos_library"))
        probe_qos = directory / "probe.xml"
        combined.write(probe_qos)
        observer = dds.DomainParticipant(111, dds.QosProvider(str(probe_qos)).participant_qos_from_profile(
            "FullCompressionProbe::wan"))
        cleanup.callback(observer.close)
        publications = observer.publication_reader
        subscriptions = observer.subscription_reader

        def endpoints():
            return {str(info.instance_handle).lower().replace(":", "").replace("-", ""): data.topic_name
                    for data, info in publications.read()
                    if info.valid and data.topic_name in base.FAMILIES}

        control_writers = {}
        for node, role, domain in base.NODES:
            path, effective = base.prepare(node, role, domain, directory, algorithm, budget)
            report["effective_writers"][node] = effective
            root = ET.parse(path)
            native_qos = root.find(
                f".//domain_route/participant[@name='{role}_wan']/domain_participant_qos")
            assert native_qos is not None
            profile = ET.SubElement(root.find("./qos_library[@name='ACT_QOS_LIB']"),
                                    "qos_profile", name="measurement_native_wan",
                                    base_name=native_qos.get("base_name"))
            override = base.copy.deepcopy(native_qos)
            override.attrib.clear()
            profile.append(override)
            root.write(path)
            base.preflight(str(path))
            (RESULTS / (label + "-" + node + ".xml")).write_bytes(path.read_bytes())
            provider = dds.QosProvider(str(path))
            qos = provider.participant_qos_from_profile("ACT_QOS_LIB::measurement_native_wan")
            prefix = "dds.flow_controller.token_bucket.act_rs.token_bucket."
            actual = {name: qos.property.get(prefix + name) for name in (
                "bytes_per_token", "max_tokens", "tokens_added_per_period", "tokens_leaked_per_period",
                "period.sec", "period.nanosec")}
            assert actual == {"bytes_per_token": str(budget // 800), "max_tokens": "1",
                              "tokens_added_per_period": "1", "tokens_leaked_per_period": "0",
                              "period.sec": "0", "period.nanosec": "10000000"}, actual
            report["controller"] = actual
            log = RESULTS / (label + "-" + node + ".log")
            logs.append(log)
            opened = cleanup.enter_context(log.open("w"))
            process = subprocess.Popen([str(executable), "-cfgFile", str(path),
                                        "-cfgName", "ACTGateway", "-verbosity", "WARN"],
                                       env=env, stdout=opened, stderr=subprocess.STDOUT)
            processes.append(process)
            cleanup.callback(base.stop, process)
            probe = base.Probe(domain)
            probes.append(probe)
            cleanup.callback(probe.close)
            if role == "control":
                base.wait(lambda: len(endpoints()) == 6, "six control WAN writers")
                control_writers = endpoints()
        base.wait(lambda: all("rs.gateway.started" in p.read_text() for p in logs), "full startup")
        base.wait(lambda: len(endpoints()) == 18, "all 18 native WAN writers")
        base.wait(lambda: len([1 for data, info in subscriptions.read()
                              if info.valid and data.topic_name in base.FAMILIES]) == 18,
                  "all 18 native WAN readers")
        writers = {i: probes[src].writer(topic, dtype, base.writer_qos("lan_event"))
                   for i in (0, 3) for topic, dtype, _, src, _ in (base.CASES[i],)}
        readers = {i: probes[dst].reader(topic, dtype, base.reader_qos("lan_event"))
                   for i in (0, 3) for topic, dtype, _, _, dst in (base.CASES[i],)}
        health = probes[0].reader("ActRouterMeshStatus", "RouterMeshStatus",
                                base.reader_qos("router_mesh_status"))

        def alive():
            for data, info in health.take():
                if info.valid:
                    peers = {str(p["health.router"]): str(p["presence"]) for p in data["peers"]}
                    if all(peers.get(n + "/rs") == "PRESENCE_ALIVE"
                           for n in ("Platform_30", "Platform_31")):
                        return True
            return False

        base.wait(alive, "both monitored peers ALIVE")
        base.wait(lambda: all(w.publication_matched_status.current_count > 0 for w in writers.values())
                  and all(r.subscription_matched_status.current_count > 0 for r in readers.values()),
                  "all original LAN endpoints matched before the volatile warmup")
        report["warmup_attempts"] = {}
        for index in (0, 3):
            value = base.sample(probes, index, 1, "small")
            value["run_id"] = "token-warmup"
            attempts, next_write = 0, 0

            def warmed():
                nonlocal attempts, next_write
                if time.monotonic() >= next_write:
                    writers[index].write(value)
                    attempts += 1
                    next_write = time.monotonic() + .1
                return any(info.valid and data == value for data, info in readers[index].take())

            base.wait(warmed, "exact pre-measurement warmup " + base.CASES[index][0])
            report["warmup_attempts"][base.CASES[index][0]] = attempts
        time.sleep(1)
        report["measurement"] = measure(mode, probes, writers, readers, processes)
        time.sleep(.75)
        report["measurement"]["capture_end"] = time.time()
        base.stop(recorder)
        report["capture_drops"] = base.capture_drops(capture_log)
        report["capture_sha256"] = base.digest(capture)
        report["wire"] = analyze_wire(capture, report["measurement"], control_writers, endpoints())
        (RESULTS / (label + "-writers.json")).write_text(json.dumps({
            "control": control_writers, "all": endpoints()}, indent=2) + "\n")
        assert set(report["wire"]["compression_ids"]) <= ({0} if algorithm == "none" else {0, 1})
        if algorithm == "zlib":
            assert 1 in report["wire"]["compression_ids"]
        report["native_writer_count"] = 18
        report["full_monitoring_peers_alive"] = True
    errors = [line for path in logs for line in path.read_text().splitlines()
              if re.search(r"ERROR|FATAL", line)]
    assert not errors, errors[:20]
    report["native_error_lines"] = 0
    (RESULTS / (label + ".json")).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"case": label, "measurement": report["measurement"], "wire": report["wire"]}),
          flush=True)
    return report


def conclusions(cases):
    rows = []
    for budget in BUDGETS:
        for mode in ("structured", "entropy"):
            off = [c for c in cases if c["budget_bps"] == budget and c["workload"] == mode
                   and c["algorithm"] == "none"]
            compressed = [c for c in cases if c["budget_bps"] == budget and c["workload"] == mode
                          and c["algorithm"] == "zlib"]
            assert len(off) == len(compressed) == 2
            mean = lambda group, section, field: statistics.mean(c[section][field] for c in group)
            off_time = mean(off, "measurement", "seconds")
            zlib_time = mean(compressed, "measurement", "seconds")
            time_ratio = zlib_time / off_time
            wire_ratio = mean(compressed, "wire", "command_data_frame_bytes") / mean(
                off, "wire", "command_data_frame_bytes")
            for c in off + compressed:
                assert c["measurement"]["enqueue_seconds"] < off_time * .25, "Producer-limited trial"
            if mode == "structured":
                assert wire_ratio < .25, ("Insufficient compression contrast", wire_ratio)
                if time_ratio < .5:
                    model = "post-compression"
                elif .75 < time_ratio < 1.3:
                    model = "original-size"
                else:
                    raise AssertionError(("Accounting models not distinguished", time_ratio))
            else:
                assert .75 < time_ratio < 1.3 and wire_ratio > .8, ("Random control", time_ratio, wire_ratio)
                model = "random-payload control"
            rows.append({"budget_bps": budget, "workload": mode,
                         "supported_accounting_model": model,
                         "off_seconds": off_time, "zlib_seconds": zlib_time,
                         "zlib_to_off_time_ratio": time_ratio, "zlib_to_off_wire_ratio": wire_ratio,
                         "off_command_frame_bytes": mean(off, "wire", "command_data_frame_bytes"),
                         "zlib_command_frame_bytes": mean(compressed, "wire", "command_data_frame_bytes")})
    for mode in ("structured", "entropy"):
        slow, fast = [next(r for r in rows if r["budget_bps"] == b and r["workload"] == mode)
                      for b in BUDGETS]
        assert 1.4 < slow["off_seconds"] / fast["off_seconds"] < 2.8, "Budget scaling control"
    return rows


def main():
    report = {"status": "FAIL", "scope": "Native monitored six-topic typed-union processor; "
              "one application target, two captured native WAN destinations; "
              "not EMANE or a sustained-load gate",
              "cases": [], "count_per_case": COUNT}
    try:
        assert {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}
        for index in range(1, 12):
            os.environ["WAN_PEER" + (str(index) if index > 1 else "")] = "[0-15]@builtin.udpv4://127.0.0.1"
            os.environ["WAN_PEER_V6" + (f"_{index}" if index > 1 else "")] = "::1"
        report["binary_hashes"] = {name: base.digest(base.WORK / "build/routing_service" / name)
                                 for name in ("libact_gateway_processor.so", "act_rs_config")}
        report["source_hashes"] = {str(p.relative_to(base.ROOT)): base.digest(p)
                                 for p in (HERE / "token_accounting.py", HERE / "runner.py",
                                           base.ROOT / "router/routing_service/run.py",
                                           base.ROOT / "harness_v2/qos/act_qos_profiles.xml",
                                           base.ROOT / "debug/scripts/rtps_delivery.py")}
        for repetition, algorithms in ((1, ("none", "zlib")), (2, ("zlib", "none"))):
            for budget in BUDGETS:
                for mode in ("structured", "entropy"):
                    for algorithm in algorithms:
                        report["cases"].append(run_case(algorithm, budget, mode, repetition))
                        (RESULTS / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        report["comparison"] = conclusions(report["cases"])
        report["compression_only_xml_changes"] = {}
        for budget in BUDGETS:
            for node, _, _ in base.NODES:
                hashes = []
                for case in report["cases"]:
                    if case["budget_bps"] != budget:
                        continue
                    root = ET.parse(RESULTS / (case["label"] + "-" + node + ".xml"))
                    for writer in (root.findall(".//output/datawriter_qos")
                                   + root.findall("./qos_library/qos_profile/datawriter_qos")):
                        for representation in writer.findall("representation"):
                            writer.remove(representation)
                    hashes.append(base.hashlib.sha256(ET.tostring(root.getroot())).hexdigest())
                assert len(set(hashes)) == 1, (budget, node, hashes)
                report["compression_only_xml_changes"][f"{budget}-{node}"] = hashes[0]
        models = {r["supported_accounting_model"] for r in report["comparison"]
                  if r["workload"] == "structured"}
        assert len(models) == 1, models
        report["finding"] = "Measured throughput supports " + models.pop() + " accounting"
        report["limits"] = "No exact per-token debit instrumentation; fragmentation, overhead, rounding, "
        report["limits"] += "multiple destinations, packet repair and other budgets remain distinct questions"
        report["status"] = "PASS"
    except Exception:
        report["error"] = traceback.format_exc()
        print(report["error"], flush=True)
    finally:
        report["private_shm_remnants"] = [p.name for p in Path("/dev/shm").iterdir()
                                        if p.name.startswith(("RTI", "dds"))]
        if report["private_shm_remnants"]:
            report["status"] = "FAIL"
        (RESULTS / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
