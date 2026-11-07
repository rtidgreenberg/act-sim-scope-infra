#!/usr/bin/env python3
"""C2 supplies a DDS directory; the client adds peers in its own OS process."""

import argparse
from collections import Counter
from importlib.metadata import version
import json
import multiprocessing
import os
from pathlib import Path
import time
import traceback


QOS = Path(__file__).with_name("qos.xml")
DIRECTORY = [
    {"name": name, "peer": f"[{index}]@udpv4://127.0.0.1"}
    for name, index in (("Open", 2), ("Late", 3), ("Strict", 4), ("Missing", 6))
]


def reconcile_peers(entries, configured, add_peer):
    """Only record successful additions; repeated directory samples are harmless."""
    for entry in entries:
        name, peer = entry["name"], entry["peer"]
        if configured.get(name) == peer:
            continue
        if name in configured:
            raise ValueError(f"locator changes are outside this spike: {name}")
        add_peer(peer)
        configured[name] = peer


def worker(connection, name, domain, protocol):
    participant = None
    try:
        import rti.connextdds as dds

        runtime_version = version("rti.connext")
        if runtime_version != "7.7.0":
            raise RuntimeError(f"expected Connext 7.7.0, found {runtime_version}")
        os.environ["BOOTSTRAP_PROTOCOL"] = protocol
        provider = dds.QosProvider(str(QOS))
        qos = provider.participant_qos_from_profile(f"Bootstrap::{name}")
        qos.participant_name.name = name
        participant = dds.DomainParticipant(domain, qos)
        dtype = provider.type("BootstrapMessage")
        publisher = dds.Publisher(participant)
        subscriber = dds.Subscriber(participant)
        topics = {
            topic: dds.DynamicData.Topic(participant, topic, dtype)
            for topic in ("PeerDirectory", "BootstrapProbe", "BootstrapAck")
        }
        writer_qos = provider.datawriter_qos_from_profile("Bootstrap::message")
        reader_qos = provider.datareader_qos_from_profile("Bootstrap::message")
        writers = {
            topic: dds.DynamicData.DataWriter(publisher, entity, writer_qos)
            for topic, entity in topics.items()
        }
        readers = {
            topic: dds.DynamicData.DataReader(subscriber, entity, reader_qos)
            for topic, entity in topics.items()
        }
        configured = {}
        add_calls = Counter()
        directory_samples = 0
        received = set()
        discovery_races = 0

        def add_peer(peer):
            participant.add_peer(peer)
            add_calls[peer] += 1

        def write(topic, target, sequence, payload=""):
            sample = dds.DynamicData(dtype)
            for field, value in (("source", name), ("target", target),
                                 ("sequence", sequence), ("payload", payload)):
                sample[field] = value
            writers[topic].write(sample)

        def snapshot():
            nonlocal discovery_races
            discovered = []
            for handle in participant.discovered_participants():
                try:
                    data = participant.discovered_participant_data(handle)
                except dds.InvalidArgumentError:
                    # A remote can expire between obtaining the handle and looking it up.
                    discovery_races += 1
                    continue
                discovered.append({
                    "name": data.participant_name.name,
                    "handle": str(handle),
                    "partial_configuration": bool(data.partial_configuration),
                })
            return {
                "name": name, "own_handle": str(participant.instance_handle),
                "connext_version": runtime_version,
                "discovered": discovered, "configured": dict(configured),
                "add_calls": dict(add_calls), "directory_samples": directory_samples,
                "initial_peers": list(participant.qos.discovery.initial_peers),
                "acks": sorted(received), "discovery_lookup_races": discovery_races,
            }

        connection.send({"ready": snapshot()})
        running = True
        while running:
            if name == "Client":
                for sample in readers["PeerDirectory"].take_data():
                    if sample["source"] == "C2" and sample["target"] == "Client":
                        entries = json.loads(sample["payload"])
                        reconcile_peers(entries, configured, add_peer)
                        directory_samples += 1
                for sample in readers["BootstrapAck"].take_data():
                    if sample["target"] == "Client":
                        received.add((sample["source"], sample["sequence"]))
            elif name != "C2":
                for sample in readers["BootstrapProbe"].take_data():
                    if sample["source"] == "Client" and sample["target"] == name:
                        write("BootstrapAck", "Client", sample["sequence"])

            if connection.poll(0.02):
                command = connection.recv()
                operation = command["operation"]
                if operation == "snapshot":
                    connection.send(snapshot())
                elif operation == "directory":
                    write("PeerDirectory", "Client", command["sequence"],
                          json.dumps(DIRECTORY))
                    connection.send({"sent": command["sequence"]})
                elif operation == "probe":
                    write("BootstrapProbe", command["target"], command["sequence"])
                    connection.send({"sent": command["sequence"]})
                elif operation == "add":
                    add_peer(command["peer"])
                    connection.send({"added": command["peer"]})
                elif operation == "stop":
                    running = False
                    connection.send({"stopping": name})
                else:
                    raise ValueError(f"unknown operation: {operation}")
    except Exception:
        connection.send({"error": traceback.format_exc()})
        raise
    finally:
        if participant is not None:
            participant.close()
        connection.close()


class Node:
    def __init__(self, context, name, domain, protocol):
        self.name = name
        self.connection, remote = context.Pipe()
        self.process = context.Process(target=worker,
                                       args=(remote, name, domain, protocol))
        self.process.start()
        remote.close()
        try:
            self.initial = self.response()
        except (RuntimeError, TimeoutError, EOFError):
            self.process.join(2)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(5)
            self.connection.close()
            raise

    def response(self):
        if not self.connection.poll(15):
            raise TimeoutError(f"{self.name}: worker response timeout")
        response = self.connection.recv()
        if "error" in response:
            raise RuntimeError(response["error"])
        return response

    def request(self, operation, **fields):
        self.connection.send(dict(operation=operation, **fields))
        return self.response()

    def snapshot(self):
        return self.request("snapshot")

    def close(self):
        if self.process.is_alive():
            self.request("stop")
            self.process.join(10)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(5)
            raise RuntimeError(f"{self.name}: forced shutdown")
        if self.process.exitcode != 0:
            raise RuntimeError(f"{self.name}: exit {self.process.exitcode}")
        self.connection.close()


def discovered(snapshot, name):
    return [item for item in snapshot["discovered"]
            if item["name"] == name and not item["partial_configuration"]]


def wait_for(label, predicate, timeout=12):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        value = predicate()
        if value:
            return {"elapsed_s": round(time.monotonic() - start, 3), "evidence": value}
        time.sleep(0.1)
    raise AssertionError(f"timeout waiting for {label}")


def assert_absent(node, names, seconds):
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        snapshot = node.snapshot()
        for name in names:
            if discovered(snapshot, name):
                raise AssertionError(f"{node.name} unexpectedly discovered {name}")
        time.sleep(0.1)
    return snapshot


def snapshot_when(node, predicate):
    snapshot = node.snapshot()
    return snapshot if predicate(snapshot) else None


def roundtrip(client, target, sequence):
    def attempt():
        client.request("probe", target=target, sequence=sequence)
        snapshot = client.snapshot()
        return snapshot if (target, sequence) in snapshot["acks"] else None

    return wait_for(f"roundtrip {target}/{sequence}", attempt)


def run_protocol(protocol, domain):
    context = multiprocessing.get_context("spawn")
    nodes = {}
    result = {"protocol": protocol, "domain": domain, "checks": {}}
    checks = result["checks"]

    def start(name):
        node = Node(context, name, domain, protocol)
        nodes[name] = node
        return node

    def mutual(client, remote):
        def both():
            left, right = client.snapshot(), remote.snapshot()
            if discovered(left, remote.name) and discovered(right, client.name):
                return {"client": left, "platform": right}
            return None

        return wait_for(f"mutual discovery {remote.name}", both)

    try:
        c2 = start("C2")
        client = start("Client")
        result["connext_version"] = client.initial["ready"]["connext_version"]
        open_node = start("Open")
        strict = start("Strict")
        for name in ("Client", "Open", "Strict"):
            wait_for(f"{name} discovers C2",
                     lambda name=name: discovered(nodes[name].snapshot(), "C2"))
        checks["no_transitive_discovery_before_directory"] = assert_absent(
            client, ("Open", "Strict"), 3)

        def deliver_directory():
            c2.request("directory", sequence=1)
            snapshot = client.snapshot()
            return snapshot if snapshot["directory_samples"] >= 1 else None

        checks["directory_received_and_peers_added"] = wait_for(
            "DDS directory delivery", deliver_directory)
        checks["open_platform_discovered"] = mutual(client, open_node)
        checks["open_platform_roundtrip"] = roundtrip(client, "Open", 10)
        checks["unavailable_and_strict_not_discovered"] = assert_absent(
            client, ("Late", "Strict", "Missing"), 8)

        for sequence in (2, 3, 4):
            c2.request("directory", sequence=sequence)
        checks["duplicate_directory_deduplicated"] = wait_for(
            "repeated directory received",
            lambda: snapshot_when(client, lambda s: s["directory_samples"] >= 4))
        late = start("Late")
        checks["late_platform_added_once_discovered"] = mutual(client, late)
        checks["late_platform_roundtrip"] = roundtrip(client, "Late", 20)

        strict.request("add", peer="[1]@udpv4://127.0.0.1")
        checks["strict_reciprocal_add_recovers"] = mutual(client, strict)
        checks["strict_platform_roundtrip"] = roundtrip(client, "Strict", 30)

        c2.close()
        del nodes["C2"]
        checks["c2_expires"] = wait_for(
            "C2 lease expires",
            lambda: snapshot_when(client, lambda s: not discovered(s, "C2")))
        checks["direct_roundtrip_without_c2"] = roundtrip(client, "Open", 40)

        old_handle = late.initial["ready"]["own_handle"]
        late.close()
        del nodes["Late"]
        checks["late_old_incarnation_expires"] = wait_for(
            "old Late incarnation expires",
            lambda: snapshot_when(client, lambda s: not discovered(s, "Late")))
        late = start("Late")
        new_handle = late.initial["ready"]["own_handle"]
        if old_handle == new_handle:
            raise AssertionError("restart did not produce a new physical GUID")
        checks["restart_new_guid_discovered_without_readd_or_c2"] = mutual(client, late)
        checks["restart_roundtrip_without_c2"] = roundtrip(client, "Late", 50)
        final = client.snapshot()
        if final["configured"] != {entry["name"]: entry["peer"] for entry in DIRECTORY}:
            raise AssertionError("configured peers differ from directory")
        if final["add_calls"] != {entry["peer"]: 1 for entry in DIRECTORY}:
            raise AssertionError(f"expected exactly one add per locator: {final['add_calls']}")
        if final["initial_peers"] != ["[0]@udpv4://127.0.0.1"]:
            raise AssertionError("runtime adds unexpectedly changed QoS initial_peers")
        if discovered(final, "Missing"):
            raise AssertionError("absent locator reported as discovered")
        restarted = discovered(final, "Late")
        if len(restarted) != 1 or restarted[0]["handle"] != new_handle:
            raise AssertionError("discovered Late identity is not the restarted GUID")
        checks["final_identity_and_add_once_assertions"] = {
            "old_handle": old_handle, "new_handle": new_handle, "client": final}
        result["passed"] = True
    finally:
        for node in reversed(list(nodes.values())):
            node.close()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-domain", type=int, default=210)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if not 0 <= args.base_domain <= 231:
        parser.error("base domain must be between 0 and 231")
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for repetition in range(1, args.repetitions + 1):
        for offset, protocol in enumerate(("SPDP", "SPDP2")):
            print(f"Repetition {repetition}: {protocol}", flush=True)
            result = run_protocol(protocol, args.base_domain + offset)
            result["repetition"] = repetition
            results.append(result)
            (args.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            print(f"PASS {protocol}: {len(result['checks'])} checks", flush=True)
    print(f"PASS: {len(results)} protocol runs", flush=True)


if __name__ == "__main__":
    main()
