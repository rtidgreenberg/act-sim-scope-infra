#!/usr/bin/env python3
"""Isolated DDS comparison; each logical node owns a subprocess and one/two WAN DPs."""

import argparse
from collections import Counter
import csv
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import resource
import re
import signal
import shutil
import subprocess
import sys
import time
import traceback
import uuid


ROOT = Path(__file__).resolve().parents[2]
QOS = Path(__file__).with_name("qos.xml")
STAGE_SOURCE_FILES = ("spike.py", "run.py", "qos.xml",
                      "../../debug/scripts/traffic_accounting.py")
SCALE_SOURCE_FILES = ("run_scale.py", "scale.py", "schema_profile.py", "spike.py",
                      "goal_model.py", "liveliness.py", "qos.xml",
                      "../../debug/scripts/traffic_accounting.py")
NODES = ("Control", "P0", "P1", "P2")
TOPICS = {
    "health": "RouterHealth",
    "probe": "RouterLinkProbe",
    "navigation": "TradeoffNavigation",
    "team": "TradeoffTeam",
    "command": "TradeoffCommand",
    "pair": "TradeoffPair",
}


def snapshot_sources(output, names):
    snapshot = output / "source"
    snapshot.mkdir()
    for name in names:
        original = (QOS.parent / name).resolve()
        copied = snapshot / original.relative_to(ROOT)
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, copied)
        copied.chmod(0o444)
    return snapshot


def source_hashes(names, directory=None):
    directory = QOS.parent if directory is None else directory
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in names}


def execution_provenance(names, inputs=None):
    return dict(
        source_hashes=source_hashes(names),
        input_hashes={name: hashlib.sha256(path.read_bytes()).hexdigest()
                      for name, path in (inputs or {}).items()},
        image_id=os.environ.get("WAN_DP_IMAGE_ID"))


def validate_provenance(proof, manifest):
    if not isinstance(proof, dict):
        raise ValueError("missing execution provenance")
    if not isinstance(proof.get("source_hashes"), dict) or not proof["source_hashes"]:
        raise ValueError("missing execution source hashes")
    for field in ("source_hashes", "input_hashes", "image_id"):
        if proof.get(field) != manifest.get(field, {} if field == "input_hashes" else None):
            raise ValueError(f"execution provenance mismatch: {field}")
    if not isinstance(proof.get("image_id"), str) or not proof["image_id"].startswith("sha256:"):
        raise ValueError("execution provenance requires a pinned image ID")


def stop_capture(capture):
    requested = capture.poll() is None
    forced = False
    if requested:
        capture.send_signal(signal.SIGINT)
    try:
        capture.wait(timeout=10)
    except subprocess.TimeoutExpired:
        forced = True
        capture.kill()
        capture.wait(timeout=5)
    return dict(stop_requested=requested, forced_kill=forced, returncode=capture.returncode)


def validate_capture_integrity(proof, summary=None):
    if not isinstance(proof, dict) or proof.get("version") != 1:
        raise ValueError("missing capture integrity evidence")
    if (proof.get("stop_requested") is not True or proof.get("forced_kill") is not False
            or proof.get("returncode") != 0):
        raise ValueError("capture did not complete its owned graceful stop")
    if (proof.get("dropped_packets") != 0 or not isinstance(proof.get("drop_counters"), dict)
            or set(proof["drop_counters"]) != {"pcap", "dumpcap", "flushed", "ps_ifdrop"}
            or any(value != 0 for value in proof["drop_counters"].values())):
        raise ValueError("capture drops or missing drop counters")
    if (proof["captured_packets"] != proof["received_packets"]
            or proof["captured_packets"] < proof["owned_rtps_packets"]
            or proof["owned_rtps_packets"] < 1):
        raise ValueError("capture packet counts do not reconcile")
    if not all(math.isfinite(proof[field]) for field in
               ("first_owned_rtps_epoch", "last_owned_rtps_epoch", "required_end_epoch")):
        raise ValueError("invalid capture timestamps")
    if proof["last_owned_rtps_epoch"] < proof["required_end_epoch"]:
        raise ValueError("capture does not cover the final measurement boundary")
    if summary is not None:
        if (proof["required_end_epoch"] != max(w["end_epoch"] for w in summary["windows"].values())
                or any(proof[field] != summary[field] for field in
                       ("owned_rtps_packets", "first_owned_rtps_epoch", "last_owned_rtps_epoch"))):
            raise ValueError("capture integrity evidence does not match measured windows")
        if proof["first_owned_rtps_epoch"] >= min(w["end_epoch"] for w in summary["windows"].values()):
            raise ValueError("capture missed the initial measurement window")


def capture_integrity(log_path, stopped, summary, windows):
    text = log_path.read_text()
    captured = re.findall(r"^Packets captured: (\d+)\s*$", text, re.MULTILINE)
    stats = re.findall(
        r"^Packets received/dropped on interface .+: (\d+)/(\d+) \(([^)]*)\)",
        text, re.MULTILINE)
    if len(captured) != 1 or len(stats) != 1:
        raise ValueError(f"missing or ambiguous dumpcap statistics: {log_path}")
    received, dropped, counters = stats[0]
    counters = dict(re.findall(r"([a-z_]+):(\d+)", counters))
    if set(counters) != {"pcap", "dumpcap", "flushed", "ps_ifdrop"}:
        raise ValueError(f"unrecognized dumpcap drop counters: {log_path}")
    proof = dict(
        version=1, **stopped, captured_packets=int(captured[0]),
        received_packets=int(received), dropped_packets=int(dropped),
        drop_counters={name: int(value) for name, value in counters.items()},
        owned_rtps_packets=summary["owned_rtps_packets"],
        first_owned_rtps_epoch=summary["first_owned_rtps_epoch"],
        last_owned_rtps_epoch=summary["last_owned_rtps_epoch"],
        required_end_epoch=max(end for _, end in windows.values()))
    validate_capture_integrity(proof, summary)
    return proof


def worker(connection, node, variant, domain):
    import rti.connextdds as dds

    provider = dds.QosProvider(str(QOS))
    dtype = provider.type("TradeoffSample")
    two = variant in ("A", "D")
    lazy = variant in ("C", "D")
    team = "BLUE" if node == "P2" else "RED"
    participants, topics, endpoints, discovery_cache = {}, {}, {}, {}
    sent, received, duplicates = {}, {}, 0
    sequence = 0
    session = uuid.uuid4().hex
    phase = "startup"
    pair_open = False
    next_heartbeat = time.monotonic()

    def participant(label, partitions):
        qos = provider.participant_qos_from_profile("Tradeoffs::participant")
        qos.partition = dds.Partition(partitions)
        name = qos.participant_name
        name.name = f"{node}/{label}"
        qos.participant_name = name
        participants[label] = dds.DomainParticipant(domain, qos)

    def main_partitions():
        if node == "Control":
            return ["*"]
        if not two:
            return ["SHARED"]
        result = [node, f"TEAM_{team}"]
        if pair_open and node in ("P0", "P2"):
            result.append("PAIR_P0_P2")
        return result

    participant("main", main_partitions())
    if two and node != "Control":
        participant("navigation", ["NAVIGATION"])

    def update_partitions():
        qos = participants["main"].qos
        qos.partition = dds.Partition(main_partitions())
        participants["main"].qos = qos

    def add_endpoint(key, flow, writer, partition, label="main"):
        p = participants[label]
        topic_key = (label, flow)
        if topic_key not in topics:
            topics[topic_key] = dds.DynamicData.Topic(p, TOPICS[flow], dtype)
        if writer:
            qos = p.default_publisher_qos
            qos.partition = dds.Partition([partition])
            parent = dds.Publisher(p, qos)
            endpoint = dds.DynamicData.DataWriter(
                parent, topics[topic_key],
                provider.datawriter_qos_from_profile(
                    "Tradeoffs::probe" if flow == "probe" else "Tradeoffs::sample"))
        else:
            qos = p.default_subscriber_qos
            qos.partition = dds.Partition([partition])
            parent = dds.Subscriber(p, qos)
            endpoint = dds.DynamicData.DataReader(
                parent, topics[topic_key],
                provider.datareader_qos_from_profile(
                    "Tradeoffs::probe" if flow == "probe" else "Tradeoffs::sample"))
        endpoints[key] = (endpoint, parent, flow, writer, label, partition)

    def remove_endpoint(key):
        endpoint, parent, *_ = endpoints.pop(key)
        endpoint.close()
        parent.close()

    def add_pairs():
        if node == "Control":
            return
        for other in NODES[1:]:
            if other == node:
                continue
            add_endpoint(f"pair_w_{other}", "pair", True, f"PAIR_{node}_{other}")
            add_endpoint(f"pair_r_{other}", "pair", False, f"PAIR_{other}_{node}")

    def set_pair(opened):
        nonlocal pair_open
        pair_open = opened
        update_partitions()
        if lazy and node in ("P0", "P2"):
            other = "P2" if node == "P0" else "P0"
            for writer in (True, False):
                key = f"pair_{'w' if writer else 'r'}_{other}"
                if opened:
                    add_endpoint(key, "pair", writer,
                                 f"PAIR_{node}_{other}" if writer else f"PAIR_{other}_{node}")
                elif key in endpoints:
                    remove_endpoint(key)

    for flow in ("health", "probe"):
        add_endpoint(f"{flow}_w", flow, True,
                     "TO_PLATFORMS" if node == "Control" else "TO_CONTROL")
        add_endpoint(f"{flow}_r", flow, False,
                     "TO_CONTROL" if node == "Control" else "TO_PLATFORMS")
    if node == "Control":
        add_endpoint("command_w", "command", True, "COMMAND_P0")
    else:
        add_endpoint("command_r", "command", False, f"COMMAND_{node}")
        label = "navigation" if two else "main"
        add_endpoint("navigation_w", "navigation", True, "NAVIGATION", label)
        add_endpoint("navigation_r", "navigation", False, "NAVIGATION", label)
        add_endpoint("team_w", "team", True, f"TEAM_{team}")
        add_endpoint("team_r", "team", False, f"TEAM_{team}")
        if not lazy:
            add_pairs()

    def send(key, sample_phase=None):
        nonlocal sequence
        endpoint, _, flow, writer, _, partition = endpoints[key]
        if not writer:
            raise ValueError(f"{key} is not a writer")
        if flow == "pair" and not pair_open:
            raise ValueError("closed route cannot forward new writes")
        sequence += 1
        sample = dds.DynamicData(dtype)
        fields = dict(source=node, flow=flow, phase=sample_phase or phase, session=session,
                      sequence=sequence, payload="x" * 64)
        for name, value in fields.items():
            sample[name] = value
        endpoint.write(sample)
        sample_key = f"{node}:{session}:{sequence}"
        sent[sample_key] = dict(fields, partition=partition, sent_at=time.monotonic())
        return sample_key

    def drain():
        nonlocal duplicates
        for endpoint, _, _, writer, _, _ in list(endpoints.values()):
            if writer:
                continue
            for data, info in endpoint.take():
                if not info.valid or data["source"] == node:
                    continue
                key = f"{data['source']}:{data['session']}:{data['sequence']}"
                if key in received:
                    duplicates += 1
                received[key] = {
                    "flow": data["flow"], "phase": data["phase"],
                    "source": data["source"], "sequence": int(data["sequence"]),
                    "received_at": time.monotonic(),
                }

    def snapshot():
        drain()
        inventory = {}
        for label, p in participants.items():
            cached = discovery_cache.setdefault(label, {"publications": {}, "subscriptions": {}})
            for reader, category in ((p.publication_reader, "publications"),
                                     (p.subscription_reader, "subscriptions")):
                for data, info in reader.select().state(dds.DataState.any).read():
                    handle = str(info.instance_handle)
                    if info.valid:
                        prefix = "".join(f"{int(word):08x}" for word in data.participant_key.value)[:24]
                        cached[category][handle] = dict(
                            topic=data.topic_name, type=data.type_name,
                            participant_prefix=prefix)
                    else:
                        cached[category].pop(handle, None)
            inventory[label] = dict(
                guid=str(p.instance_handle)[:24].lower(),
                peers=[str(h) for h in p.discovered_participants()],
                publications=list(cached["publications"].values()),
                subscriptions=list(cached["subscriptions"].values()))
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return dict(node=node, sent=sent, received=received, duplicates=duplicates,
                    cpu_s=usage.ru_utime + usage.ru_stime, max_rss_kib=usage.ru_maxrss,
                    participants=inventory,
                    endpoints={key: dict(flow=value[2], writer=value[3],
                                         dp=value[4], partition=value[5],
                                         matches=len(value[0].matched_subscriptions
                                                     if value[3]
                                                     else value[0].matched_publications))
                               for key, value in endpoints.items()})

    connection.send({"ready": node})
    try:
        running = True
        while running:
            if connection.poll(0.01):
                request = connection.recv()
                operation = request["op"]
                if operation == "phase":
                    phase = request["phase"]
                    result = phase
                elif operation == "send":
                    result = send(request["key"])
                elif operation == "snapshot":
                    result = snapshot()
                elif operation == "pair":
                    set_pair(request["opened"])
                    result = True
                elif operation == "team":
                    team = request["team"]
                    update_partitions()
                    for key in ("team_w", "team_r"):
                        remove_endpoint(key)
                    add_endpoint("team_w", "team", True, f"TEAM_{team}")
                    add_endpoint("team_r", "team", False, f"TEAM_{team}")
                    result = team
                elif operation == "stop":
                    result = True
                    running = False
                else:
                    raise ValueError(f"unknown operation {operation}")
                connection.send({"result": result})
            now = time.monotonic()
            if running and now >= next_heartbeat:
                send("health_w")
                send("probe_w")
                next_heartbeat = now + 1
            drain()
    finally:
        for p in reversed(list(participants.values())):
            p.close()
        connection.close()


def worker_entry(connection, node, variant, domain):
    try:
        worker(connection, node, variant, domain)
    except Exception:
        error = traceback.format_exc()
        print(error, file=sys.stderr, flush=True)
        connection.send({"error": error})
        raise


class Rig:
    def __init__(self, variant, domain, workdir):
        self.variant, self.domain, self.workdir = variant, domain, workdir
        self.workers = {}
        self.context = multiprocessing.get_context("spawn")

    def start_node(self, node):
        parent, child = self.context.Pipe()
        process = self.context.Process(target=worker_entry,
                                       args=(child, node, self.variant, self.domain))
        process.start()
        child.close()
        self.workers[node] = (parent, process)
        if not parent.poll(30):
            raise TimeoutError(f"{node} did not start")
        result = parent.recv()
        if "error" in result:
            raise RuntimeError(result["error"])
        if result != {"ready": node}:
            raise RuntimeError(f"unexpected startup response {result}")

    def rpc(self, node, operation, **fields):
        connection, process = self.workers[node]
        if not process.is_alive():
            raise RuntimeError(f"{node} exited: {process.exitcode}")
        connection.send(dict(op=operation, **fields))
        if not connection.poll(10):
            raise TimeoutError(f"{node}: {operation} timed out")
        response = connection.recv()
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"]

    def snapshots(self):
        return {node: self.rpc(node, "snapshot") for node in self.workers}

    def phase(self, name):
        for node in self.workers:
            self.rpc(node, "phase", phase=name)

    def wait_matches(self, requirements, timeout=30):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            snapshots = self.snapshots()
            if all(snapshots[node]["endpoints"][key]["matches"] == count
                   for node, key, count in requirements):
                return time.monotonic() - start
            time.sleep(0.05)
        raise TimeoutError(f"matches not reached: {requirements}; "
                           f"{json.dumps({n:s['endpoints'] for n,s in snapshots.items()})}")

    def wait_stable(self, timeout=30):
        start, previous, unchanged = time.monotonic(), None, 0
        while time.monotonic() - start < timeout:
            snapshots = self.snapshots()
            state = [
                (node, label, sorted(p["peers"]), len(p["publications"]), len(p["subscriptions"]))
                for node, snapshot in sorted(snapshots.items())
                for label, p in sorted(snapshot["participants"].items())
            ]
            unchanged = unchanged + 1 if state == previous else 0
            if unchanged >= 4:
                return time.monotonic() - start
            previous = state
            time.sleep(0.5)
        raise TimeoutError("discovery inventory did not stabilize")

    def exercise(self, phase, flows, expected, count=5):
        self.phase(phase)
        sent = []
        for _ in range(count):
            for node, key in flows:
                sent.append((self.rpc(node, "send", key=key), expected[(node, key)]))
            time.sleep(0.1)
        start = time.monotonic()
        while time.monotonic() - start < 10:
            snapshots = self.snapshots()
            missing = [(key, receiver) for key, receivers in sent
                       for receiver in receivers
                       if key not in snapshots[receiver]["received"]]
            if not missing:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"{phase}: missing deliveries {missing}")
        time.sleep(0.5)
        snapshots = self.snapshots()
        for key, receivers in sent:
            source = key.split(":")[0]
            actual = {node for node in NODES if node != source
                      and key in snapshots[node]["received"]}
            if actual != set(receivers):
                raise AssertionError(f"{phase} {key}: {actual} != {set(receivers)}")
        return dict(sent=len(sent), destination_receives=sum(len(r) for _, r in sent),
                    unintended=0, delivery_check_s=time.monotonic() - start)

    def stop_node(self, node):
        connection, process = self.workers.pop(node)
        try:
            if process.is_alive():
                connection.send({"op": "stop"})
                if connection.poll(5):
                    connection.recv()
                process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
            if process.exitcode not in (0, -signal.SIGTERM, -signal.SIGKILL):
                raise RuntimeError(f"{node} failed with exit code {process.exitcode}")
        finally:
            connection.close()

    def close(self):
        errors = []
        for node in list(self.workers):
            try:
                self.stop_node(node)
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError("; ".join(errors))


def capture_summary(path, prefixes, windows):
    sys.path.insert(0, str(ROOT / "debug/scripts"))
    from traffic_accounting import classify_packet, entity_ids, is_discovery_entity

    fields = ("frame.time_epoch", "frame.len", "rtps.guidPrefix.src",
              "rtps.sm.wrEntityId", "rtps.sm.id", "rtps.param.topicName",
              "ip.flags.mf", "ip.frag_offset")
    command = ["tshark", "-n", "-r", str(path), "-Y", "rtps", "-T", "fields",
               "-E", "separator=|", "-E", "quote=d", "-E", "occurrence=a"]
    for field in fields:
        command.extend(["-e", field])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    packets = []
    topic_names = set()
    for row in csv.reader(result.stdout.splitlines(), delimiter="|"):
        if len(row) != len(fields):
            raise ValueError(f"malformed tshark row: {row}")
        timestamp, length, prefix, writers, submessages, topics, more, offset = row
        prefix = prefix.replace(":", "").lower()
        if prefix not in prefixes:
            continue
        if more == "1" or offset not in ("", "0"):
            raise AssertionError("fragmented traffic requires fragment-aware accounting")
        packet_class = classify_packet(writers, submessages)
        entities = entity_ids(writers)
        if entities:
            discovery = [is_discovery_entity(e) for e in entities]
            if all(discovery) and packet_class in ("discovery", "reliability", "mixed"):
                packet_class = "discovery"
            elif any(discovery) and packet_class == "reliability":
                packet_class = "mixed"
        packets.append((float(timestamp), int(length), prefix, packet_class, writers))
        topic_names.update(t for t in topics.split(",") if t)
    if not packets:
        raise AssertionError("capture has no owned RTPS traffic")
    summaries = {}
    for name, (start, end) in windows.items():
        selected = [p for p in packets if start <= p[0] < end]
        totals, per_dp, buckets, entity_frames = {}, {}, {}, {}
        for timestamp, length, prefix, kind, writers in selected:
            totals[kind] = totals.get(kind, 0) + length
            per_dp[prefix] = per_dp.get(prefix, 0) + length
            bucket = math.floor(timestamp)
            if math.ceil(start) <= bucket < math.floor(end):
                buckets[bucket] = buckets.get(bucket, 0) + (length if kind == "discovery" else 0)
            entity_set = tuple(sorted(set(entity_ids(writers))))
            label = ",".join(f"{entity:08x}" for entity in entity_set) or "no_writer_field"
            entity_frames[label] = entity_frames.get(label, 0) + length
        duration = end - start
        summaries[name] = dict(
            start_epoch=start, end_epoch=end, duration_s=duration,
            total_bytes=sum(totals.values()), class_bytes=totals,
            total_tx_Bps=sum(totals.values()) / duration,
            discovery_tx_Bps=totals.get("discovery", 0) / duration,
            discovery_peak_1s_Bps=max(buckets.values(), default=0),
            per_dp_bytes=per_dp, writer_entity_set_frame_bytes=entity_frames)
    return dict(windows=summaries, observed_topics=sorted(topic_names),
                owned_rtps_packets=len(packets),
                first_owned_rtps_epoch=min(p[0] for p in packets),
                last_owned_rtps_epoch=max(p[0] for p in packets),
                accounting_version=2,
                accounting="whole Ethernet frames once; pure builtin data/control is discovery; true mixed remains separate",
                assertion_attribution="writer-entity sets only; no assertion/response claim")


def run_variant(args, variant):
    provenance = execution_provenance(STAGE_SOURCE_FILES)
    if args.manifest is not None:
        validate_provenance(provenance, json.loads(args.manifest.read_text()))
    workdir = args.output / variant
    workdir.mkdir(parents=True, exist_ok=False)
    pcap = workdir / "traffic.pcapng"
    lo, hi = 7400 + 250 * args.domain, 7400 + 250 * args.domain + 249
    cap_log = (workdir / "capture.log").open("w")
    capture = subprocess.Popen(
        ["dumpcap", "-i", "lo", "-f", f"udp portrange {lo}-{hi}",
         "-a", f"duration:{args.idle_seconds + 180}", "-w", str(pcap), "-q"],
        stdout=cap_log, stderr=cap_log)
    rig = Rig(variant, args.domain, workdir)
    windows, events, checkpoints, prefixes = {}, {}, {}, {}
    start = time.time()
    try:
        time.sleep(0.5)
        if capture.poll() is not None:
            raise RuntimeError(f"capture failed; see {workdir / 'capture.log'}")
        for node in NODES:
            rig.start_node(node)
        requirements = [("Control", "health_w", 3), ("Control", "health_r", 3),
                        ("Control", "command_w", 1), ("P0", "navigation_w", 3),
                        ("P0", "team_w", 2), ("P2", "team_w", 1)]
        events["initial_matches_s"] = rig.wait_matches(requirements)
        flows = [("Control", "command_w"), ("P0", "navigation_w"), ("P0", "team_w")]
        expected = {flows[0]: ["P0"], flows[1]: ["P1", "P2"], flows[2]: ["P1"]}
        events["startup_delivery"] = rig.exercise("startup_check", flows, expected)
        events["discovery_stabilization_s"] = rig.wait_stable()
        checkpoints["startup"] = rig.snapshots()
        for node, snapshot in checkpoints["startup"].items():
            for label, participant in snapshot["participants"].items():
                prefixes[participant["guid"]] = f"{node}/{label}"
        windows["startup"] = (start, time.time())

        rig.phase("idle")
        idle_start = math.ceil(time.time())
        time.sleep(max(0, idle_start - time.time()))
        idle_cpu = {node: s["cpu_s"] for node, s in rig.snapshots().items()}
        while time.time() < idle_start + args.idle_seconds:
            if capture.poll() is not None:
                raise RuntimeError("capture ended before measurement completed")
            time.sleep(max(0, min(1, idle_start + args.idle_seconds - time.time())))
        idle_end = idle_start + args.idle_seconds
        windows["idle"] = (idle_start, idle_end)
        checkpoints["idle"] = rig.snapshots()
        peer_prefix = checkpoints["idle"]["P2"]["participants"]["main"]["guid"]
        out_of_team_visible = any(
            p["topic"] == TOPICS["team"] and p["participant_prefix"] == peer_prefix
            for p in checkpoints["idle"]["P0"]["participants"]["main"]["publications"])
        if out_of_team_visible != (variant in ("B", "C")):
            raise AssertionError(f"{variant}: unexpected out-of-team metadata exposure")
        events["out_of_team_metadata_visible"] = out_of_team_visible
        if variant in ("C", "D"):
            for node in NODES[1:]:
                if any(k.startswith("pair_") for k in checkpoints["idle"][node]["endpoints"]):
                    raise AssertionError(f"{node}: inactive pair endpoints were created")
        events["idle_cpu_s"] = {node: s["cpu_s"] - idle_cpu[node]
                               for node, s in checkpoints["idle"].items()}
        for node, snapshot in checkpoints["idle"].items():
            if not any(v["flow"] == "health" and v["phase"] == "idle"
                       for v in snapshot["received"].values()):
                raise AssertionError(f"{node}: no health receipt during idle")
            if not any(v["flow"] == "probe" and v["phase"] == "idle"
                       for v in snapshot["received"].values()):
                raise AssertionError(f"{node}: no application-auto-ack probe during idle")
            for key, sample in snapshot["sent"].items():
                if sample["phase"] != "idle" or sample["sent_at"] > time.monotonic() - 2:
                    continue
                expected_receivers = set(NODES[1:]) if node == "Control" else {"Control"}
                actual = {receiver for receiver in NODES if receiver != node
                          and key in checkpoints["idle"][receiver]["received"]}
                if actual != expected_receivers:
                    raise AssertionError(f"idle {key}: {actual} != {expected_receivers}")
        loaded_start = time.time()
        events["loaded"] = rig.exercise("loaded", flows, expected, count=30)
        windows["loaded"] = (loaded_start, time.time())

        pair_start = time.time()
        rig.phase("pair_open")
        for node in NODES:
            rig.rpc(node, "pair", opened=True)
        events["pair_matches_s"] = rig.wait_matches([("P0", "pair_w_P2", 1)])
        events["pair_delivery"] = rig.exercise(
            "pair_delivery", [("P0", "pair_w_P2")], {("P0", "pair_w_P2"): ["P2"]}, count=10)
        checkpoints["pair_open"] = rig.snapshots()
        windows["pair_open"] = (pair_start, time.time())
        close_start = time.time()
        for node in NODES:
            rig.rpc(node, "pair", opened=False)
        rig.phase("pair_closed")
        time.sleep(1)
        checkpoints["pair_closed"] = rig.snapshots()
        for node in ("P0", "P2"):
            actual = checkpoints["pair_closed"][node]["endpoints"]
            if variant in ("C", "D") and any(k.startswith("pair_") for k in actual):
                raise AssertionError(f"{node}: on-demand endpoints survived teardown")
        events["closed_delivery"] = rig.exercise("closed_check", flows, expected)
        windows["pair_close"] = (close_start, time.time())

        team_start = time.time()
        rig.phase("team_change")
        rig.rpc("P1", "team", team="BLUE")
        events["team_matches_s"] = rig.wait_matches(
            [("P0", "team_w", 1), ("P2", "team_w", 2)])
        changed_flows = [("Control", "command_w"), ("P0", "navigation_w"), ("P2", "team_w")]
        changed_expected = {changed_flows[0]: ["P0"], changed_flows[1]: ["P1", "P2"],
                            changed_flows[2]: ["P1"]}
        events["team_delivery"] = rig.exercise("team_check", changed_flows, changed_expected)
        checkpoints["team_change"] = rig.snapshots()
        windows["team_change"] = (team_start, time.time())

        restart_start = time.time()
        rig.phase("restart")
        rig.stop_node("P2")
        rig.start_node("P2")
        for label, participant in rig.rpc("P2", "snapshot")["participants"].items():
            prefixes[participant["guid"]] = f"P2/{label}/restarted"
        events["restart_matches_s"] = rig.wait_matches(
            [("Control", "health_w", 3), ("P0", "navigation_w", 3), ("P2", "team_w", 2)])
        events["restart_delivery"] = rig.exercise("restart_check", changed_flows, changed_expected)
        checkpoints["restart"] = rig.snapshots()
        windows["restart"] = (restart_start, time.time())
        time.sleep(1.2)  # Keep health traffic flowing past the final capture boundary.
    finally:
        try:
            rig.close()
        finally:
            try:
                stopped = stop_capture(capture)
            finally:
                cap_log.close()
    summary = capture_summary(pcap, prefixes, windows)
    summary["integrity"] = capture_integrity(workdir / "capture.log", stopped, summary, windows)
    if execution_provenance(STAGE_SOURCE_FILES) != provenance:
        raise ValueError("execution sources changed during measurement")
    if not {"RouterHealth", "RouterLinkProbe", "TradeoffNavigation"} <= set(summary["observed_topics"]):
        raise AssertionError(f"positive capture control missing: {summary['observed_topics']}")
    for checkpoint in checkpoints.values():
        for snapshot in checkpoint.values():
            for direction in ("sent", "received"):
                ledger = snapshot.pop(direction)
                snapshot[f"{direction}_count"] = len(ledger)
                snapshot[f"{direction}_phase_counts"] = dict(
                    Counter(sample["phase"] for sample in ledger.values()))
    result = dict(variant=variant, repetition=args.repetition, domain=args.domain,
                  platform_count=3, idle_seconds=args.idle_seconds, guid_map=prefixes,
                  windows=windows, events=events, capture=summary, checkpoints=checkpoints,
                  execution_provenance=provenance,
                  qos_sha256=hashlib.sha256(QOS.read_bytes()).hexdigest(),
                  stage="isolated DDS mechanism, synthetic common payload, not real router")
    (workdir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    idle = summary["windows"]["idle"]
    print(json.dumps(dict(variant=variant, repetition=args.repetition,
                          discovery_idle_Bps=idle["discovery_tx_Bps"],
                          discovery_peak_Bps=idle["discovery_peak_1s_Bps"],
                          total_idle_Bps=idle["total_tx_Bps"], unintended=0)), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--idle-seconds", type=int, default=150)
    parser.add_argument("--domain", type=int, default=201)
    parser.add_argument("--variants", default="ABCD")
    parser.add_argument("--repetition", type=int, default=1)
    args = parser.parse_args()
    if not 201 <= args.domain <= 232:
        parser.error("isolated spike domain must be within 201-232")
    if args.idle_seconds < 1:
        parser.error("idle duration must be positive")
    if not args.variants or any(v not in "ABCD" for v in args.variants):
        parser.error("variants must contain A/B/C/D")
    if len(set(args.variants)) != len(args.variants):
        parser.error("each variant may appear only once")
    args.output.mkdir(parents=True, exist_ok=True)
    results = [run_variant(args, variant) for variant in args.variants]
    (args.output / "summary.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
