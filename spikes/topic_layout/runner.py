#!/usr/bin/env python3
"""Native DDS topic-layout experiment; run only in the owned private container."""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import rti.connextdds as dds

from analyze import packets, summarize


HERE = Path(__file__).resolve().parent
REPORT = Path("/reports")
CAPTURE = Path("/captures")
LAYOUTS = ("six_topics", "three_topics_six_readers", "three_topics_three_readers")
FAMILIES = ("Event", "State", "Periodic")
DSFIELDS = (0, 32, 64, 96, 128, 160)
STARTUP_SECONDS = 4
UPDATE_SECONDS = 1.5


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def guid(entity):
    result = str(entity.instance_handle).lower().replace(":", "").replace("-", "")
    assert len(result) == 32, result
    return result


def wait(predicate, label, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out: " + label)


def stop(process):
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=10)
    assert process.returncode == 0, (process.args, process.returncode)


def profile(name):
    return "TopicLayout::" + name


def trial(layout, count, label, suppress_preemptive=False):
    config = REPORT / f"{label}.xml"
    root = ET.parse(HERE / "qos.xml").getroot()
    root.append(ET.parse(HERE / "types.xml").getroot().find("types"))
    ET.ElementTree(root).write(config, encoding="utf-8", xml_declaration=True)
    qos = dds.QosProvider(str(config))
    dtype = qos.type("Frame")
    path = CAPTURE / f"{label}.pcapng"
    path.touch()
    path.chmod(0o666)
    phases, participants, writers, readers, filters = {}, [], [], [], []
    manifest = dict(layout=layout, participants=count, topics=6 if layout == LAYOUTS[0] else 3,
                    writers_per_participant=6,
                    readers_per_participant=3 if layout == LAYOUTS[2] else 6,
                    preemptive_nack_disabled=suppress_preemptive,
                    phases=phases, endpoints=[], deliveries=[])
    save(REPORT / f"{label}.json", dict(status="RUNNING", **manifest))
    with ExitStack() as cleanup:
        log = cleanup.enter_context((CAPTURE / f"{label}.log").open("w"))
        recorder = subprocess.Popen(
            ["dumpcap", "-q", "-i", "lo", "-f", "udp portrange 32900-33149",
             "-a", "duration:60", "-w", str(path)], stdout=log, stderr=subprocess.STDOUT)
        cleanup.callback(stop, recorder)
        wait(lambda: path.exists() and path.stat().st_size > 0, "capture startup", timeout=5)
        startup_start = time.time()
        started = time.monotonic()
        for node in range(count):
            participant = dds.DomainParticipant(
                102, qos.participant_qos_from_profile(profile("participant")))
            cleanup.callback(participant.close)
            participants.append(participant)
            pubq = qos.publisher_qos_from_profile(profile("endpoint"))
            pubq.partition = dds.Partition([f"node{node}"])
            subq = qos.subscriber_qos_from_profile(profile("endpoint"))
            subq.partition = dds.Partition([f"node{peer}" for peer in range(count) if peer != node])
            publisher = dds.Publisher(participant, pubq)
            subscriber = dds.Subscriber(participant, subq)
            topics = {}
            for sample_class in range(6):
                name = FAMILIES[sample_class // 2]
                if layout == LAYOUTS[0]:
                    name += "_" + str(sample_class % 2)
                if name not in topics:
                    topics[name] = dds.DynamicData.Topic(participant, name, dtype)
                writer = dds.DynamicData.DataWriter(publisher, topics[name],
                    qos.datawriter_qos_from_profile(profile(f"class_{sample_class}")))
                assert writer.qos.transport_priority.value == DSFIELDS[sample_class]
                writers.append((node, sample_class, writer))
            for index in range(manifest["readers_per_participant"]):
                low = index * 2 if layout == LAYOUTS[2] else index
                high = low + 1 if layout == LAYOUTS[2] else low
                name = FAMILIES[low // 2]
                if layout == LAYOUTS[0]:
                    name += "_" + str(low % 2)
                cft = dds.DynamicData.ContentFilteredTopic(
                    topics[name], f"Selection_{node}_{index}",
                    dds.Filter("sample_class >= %0 AND sample_class <= %1 AND sequence >= %2",
                               [str(low), str(high), "0"]))
                reader = dds.DynamicData.DataReader(subscriber, cft,
                    qos.datareader_qos_from_profile(profile(
                        "reader_suppressed" if suppress_preemptive else "endpoint")))
                filters.append((node, index, cft, low, high))
                readers.append((node, index, reader, low, high))
        writer_matches = (count - 1) * (2 if layout == LAYOUTS[1] else 1)
        reader_matches = (count - 1) * (1 if layout == LAYOUTS[0] else 2)

        def all_matched():
            return all(len(w.matched_subscriptions) == writer_matches for _, _, w in writers
                       ) and all(len(r.matched_publications) == reader_matches
                                 for _, _, r, _, _ in readers)
        try:
            wait(all_matched, "exact remote writer-reader matches")
        except AssertionError as error:
            raise AssertionError(dict(
                expected_writer_matches=writer_matches, expected_reader_matches=reader_matches,
                writers=[(node, kind, len(writer.matched_subscriptions),
                          writer.offered_incompatible_qos_status.total_count)
                         for node, kind, writer in writers],
                readers=[(node, index, len(reader.matched_publications),
                          reader.requested_incompatible_qos_status.total_count)
                         for node, index, reader, _, _ in readers])) from error
        manifest["match_ms"] = (time.monotonic() - started) * 1000
        assert manifest["match_ms"] < STARTUP_SECONDS * 1000, manifest["match_ms"]
        time.sleep(max(0, startup_start + STARTUP_SECONDS - time.time()))
        phases["startup"] = dict(start=startup_start, end=startup_start + STARTUP_SECONDS)
        manifest["matched_associations"] = sum(len(w.matched_subscriptions) for _, _, w in writers)
        for node, sample_class, writer in writers:
            manifest["endpoints"].append(dict(
                kind="writer", node=node, sample_class=sample_class, guid=guid(writer),
                matched_readers=len(writer.matched_subscriptions)))
        for node, index, reader, low, high in readers:
            manifest["endpoints"].append(dict(
                kind="reader", node=node, index=index, classes=[low, high], guid=guid(reader),
                matched_writers=len(reader.matched_publications)))
        prefixes = {guid(p)[:24] for p in participants}
        assert len(prefixes) == count
        manifest["prefixes"] = sorted(prefixes)

        def observe(name, changes=(), minimum=0):
            phase = dict(start=time.time())
            for _, _, cft, low, high in changes:
                cft.filter_parameters = [str(low), str(high), str(minimum)]
                assert list(cft.filter_parameters) == [str(low), str(high), str(minimum)]
            time.sleep(UPDATE_SECONDS)
            phase.update(end=time.time(), changed_readers=len(changes),
                         changed_participant_prefix=guid(participants[0])[:24])
            assert all_matched(), name
            phases[name] = phase

        def delivery(name, minimum, sequence):
            expected, actual, samples = {}, {}, {}
            for _, _, reader, _, _ in readers:
                assert not [value for value, info in reader.take() if info.valid], name
            for sample_class in range(6):
                value = dds.DynamicData(dtype)
                marker = f"{label}:{name}:{sample_class}"
                value["source"] = "node1"
                value["instance_id"] = marker
                value["sample_class"] = sample_class
                value["sequence"] = sequence
                if sample_class // 2 == 0:
                    payload = dds.DynamicData(qos.type("Command"))
                    payload["operation"] = "exact-command"
                    payload["arguments"] = [sample_class, 7, 11]
                    branch = "command"
                elif sample_class // 2 == 1:
                    payload = dds.DynamicData(qos.type("Report"))
                    payload["values"] = [sample_class + 0.25, sequence + 0.5]
                    branch = "report"
                else:
                    payload = dds.DynamicData(qos.type("Inventory"))
                    payload["topics"] = ["Mission", "ContactReport", "PlatformStatus"]
                    branch = "inventory"
                value.set_complex("payload." + branch, payload)
                samples[marker] = value
                expected[marker] = {
                    (node, index) for node, index, _, low, high in readers
                    if node != 1 and low <= sample_class <= high and
                    not (node == 0 and index in minimum and sequence < minimum[index])}
                writer = next(w for node, kind, w in writers if node == 1 and kind == sample_class)
                writer.write(value)
                time.sleep(0.01)

            def receive():
                for node, index, reader, _, _ in readers:
                    for value, info in reader.take():
                        if info.valid:
                            marker = value["instance_id"]
                            assert marker in samples, (name, marker)
                            assert value == samples[marker], (name, marker, "payload mismatch")
                            key = (node, index)
                            assert key in expected[marker], (name, marker, "excluded delivery", key)
                            actual.setdefault(marker, []).append(key)
                            assert len(actual[marker]) == len(set(actual[marker])), (name, marker)
                return all(set(actual.get(marker, [])) == targets for marker, targets in expected.items())
            wait(receive, name + " exact selected originals")
            for _, _, writer in writers:
                writer.wait_for_acknowledgments(dds.Duration(3))
            time.sleep(0.15)
            assert receive()
            manifest["deliveries"].append(dict(
                name=name, originals=6, delivered=sum(len(v) for v in actual.values()),
                expected=sum(len(v) for v in expected.values()),
                markers=list(samples), sequence=sequence))

        local = [row for row in filters if row[0] == 0]
        observe("idle")
        observe("one_filter_update", local[:1], 100)
        delivery("one_blocked", {0: 100}, 1)
        observe("one_filter_restore", local[:1], 0)
        delivery("one_restored", {}, 2)
        observe("all_filter_update", local, 100)
        delivery("all_blocked", {index: 100 for _, index, _, _, _ in local}, 3)
        observe("all_filter_restore", local, 0)
        delivery("all_restored", {}, 4)
        stop(recorder)
        capture_log = (CAPTURE / f"{label}.log").read_text()
        assert re.search(r"pcap:0/dumpcap:0", capture_log), capture_log
        captured = packets(path)
        assert captured and min(p["time"] for p in captured) < phases["startup"]["end"]
        assert max(p["time"] for p in captured) >= phases["all_filter_restore"]["end"]
        manifest["wire"] = {name: summarize(captured, phase) for name, phase in phases.items()}
        for name, phase in phases.items():
            manifest["wire"][name]["changed_participant"] = summarize(
                captured, phase, guid(participants[0])[:24])
        startup = manifest["wire"]["startup"]
        manifest["startup_user_base_zero_acknacks"] = sum(
            row["kind"] == "ACKNACK" and row["base"] == 0 and
            not (int(row["writer"][-2:], 16) & 0x80)
            for packet in captured
            if phases["startup"]["start"] <= packet["time"] < phases["startup"]["end"]
            for row in packet["rows"])
        if suppress_preemptive:
            assert manifest["startup_user_base_zero_acknacks"] == 0, manifest
        assert startup["unique_publication_records"] == count * 6, startup
        assert startup["unique_subscription_records"] == count * manifest["readers_per_participant"], startup
        for name in ("one_filter_update", "one_filter_restore", "all_filter_update", "all_filter_restore"):
            observed = manifest["wire"][name]["changed_participant"]["unique_subscription_records"]
            assert observed == phases[name]["changed_readers"], (name, observed, phases[name])
        marking = {}
        for sample_class, expected_dsfield in enumerate(DSFIELDS):
            marker = manifest["deliveries"][-1]["markers"][sample_class].encode() + b"\0"
            found = {p["dsfield"] for p in captured if marker in p["payload"]}
            assert found == {expected_dsfield}, (sample_class, expected_dsfield, found)
            marking[str(sample_class)] = dict(dsfield=expected_dsfield, dscp=expected_dsfield >> 2)
        manifest.update(status="PASS", capture_drops=0, marking=marking,
                        capture_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert not [p.name for p in Path("/dev/shm").iterdir() if p.name.startswith(("RTI", "dds"))]
    save(REPORT / f"{label}.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", choices=LAYOUTS)
    parser.add_argument("--participants", type=int, choices=(2, 7))
    parser.add_argument("--label")
    parser.add_argument("--repetitions", type=int, default=4, choices=range(1, 7))
    parser.add_argument("--suppress-preemptive", action="store_true")
    options = parser.parse_args()
    assert {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}, "private network required"
    if options.trial:
        trial(options.trial, options.participants, options.label, options.suppress_preemptive)
        return
    results = []
    report = dict(status="RUNNING", startup_seconds=STARTUP_SECONDS,
                  update_seconds=UPDATE_SECONDS, repetitions=options.repetitions,
                  preemptive_nack_disabled=options.suppress_preemptive,
                  scenarios=results, source_hashes={
                      path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in HERE.iterdir() if path.is_file()})
    for repetition in range(options.repetitions):
        cases = [(count, layout) for count in (2, 7) for layout in LAYOUTS]
        if repetition % 2:
            cases.reverse()
        for count, layout in cases:
            label = f"r{repetition + 1}-n{count}-{layout}"
            with (REPORT / f"{label}.console.log").open("w") as log:
                process = subprocess.run(
                    [sys.executable, str(HERE / "runner.py"), "--trial", layout,
                     "--participants", str(count), "--label", label] +
                    (["--suppress-preemptive"] if options.suppress_preemptive else []),
                    stdout=log, stderr=subprocess.STDOUT, timeout=60)
            if process.returncode:
                report.update(status="FAIL", failed_trial=label)
                save(REPORT / "report.json", report)
                raise RuntimeError(f"{label} failed; see retained trial console")
            text = (REPORT / f"{label}.console.log").read_text()
            assert not re.search(r"^(ERROR|FATAL)\b", text, re.MULTILINE), text
            result = json.loads((REPORT / f"{label}.json").read_text())
            assert result["status"] == "PASS"
            results.append(result)
            save(REPORT / "report.json", report)
            print("PASS:", label, flush=True)
    summary = {}
    for count in (2, 7):
        summary[str(count)] = {}
        for layout in LAYOUTS:
            trials = [r for r in results if r["participants"] == count and r["layout"] == layout]
            summary[str(count)][layout] = dict(
                mean_startup_discovery_ip_bytes=statistics.mean(
                    r["wire"]["startup"]["discovery_ip_bytes"] for r in trials),
                mean_startup_sedp_ip_bytes=statistics.mean(
                    r["wire"]["startup"]["sedp_ip_bytes"] for r in trials),
                mean_match_ms=statistics.mean(r["match_ms"] for r in trials),
                mean_one_update_discovery_ip_bytes=statistics.mean(
                    r["wire"]["one_filter_update"]["discovery_ip_bytes"] for r in trials),
                mean_all_update_discovery_ip_bytes=statistics.mean(
                    r["wire"]["all_filter_update"]["discovery_ip_bytes"] for r in trials),
                matched_associations=trials[0]["matched_associations"])
    report.update(status="PASS", summary=summary)
    save(REPORT / "report.json", report)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        if os.getuid() == 0:
            for root in (REPORT, CAPTURE):
                for path in root.rglob("*"):
                    os.chown(path, int(os.environ["TARGET_UID"]), int(os.environ["TARGET_GID"]))
