"""Wire-only delivery regressions; no DDS entities or live network required."""

import ast
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "debug/scripts"), str(ROOT / "gui/mesh_dashboard/server")]

from rtps_delivery import SequenceRanges, WireCapture, endpoint_metadata, submessages
from wire_delivery import WireDelivery
from delivery_config import load_delivery_config


PREFIX = bytes.fromhex("0102030405060708090a0b0c")
WRITER = bytes.fromhex("00001002")
GUID = PREFIX.hex() + WRITER.hex()
NOW = 100_000_000_000


READER_PREFIX = bytes.fromhex("1112131415161718191a1b1c")
READER = bytes.fromhex("00001007")


def message(sequence=1, payload=b"hello", prefix=PREFIX, writer=WRITER, fragment=None,
            little=True, reader=b"\x00" * 4, destination=None):
    endian = "<" if little else ">"
    flags = 1 if little else 0
    header = b"RTPS\x02\x03\x01\x01" + prefix
    if destination:
        header += bytes([0x0e, flags]) + struct.pack(endian + "H", 12) + destination
    fixed = b"\x00\x00" + struct.pack(endian + "H", 28 if fragment else 16)
    fixed += reader + writer + struct.pack(endian + "iI", sequence >> 32, sequence & 0xffffffff)
    if fragment:
        fixed += struct.pack(endian + "IHHI", *fragment)
        kind = 0x16
    else:
        flags |= 4
        kind = 0x15
    return header + bytes([kind, flags]) + struct.pack(endian + "H", len(fixed) + len(payload)) + fixed + payload


def repair_request(sequence=1, offsets=(0,), little=True, fragment=False, destination=PREFIX):
    endian = "<" if little else ">"
    flags = 1 if little else 0
    header = b"RTPS\x02\x03\x01\x01" + READER_PREFIX
    if destination:
        header += bytes([0x0e, flags]) + struct.pack(endian + "H", 12) + destination
    bits = max(offsets) + 1 if offsets else 0
    words = [0] * ((bits + 31) // 32)
    for offset in offsets:
        words[offset // 32] |= 1 << (31 - offset % 32)
    body = READER + WRITER + struct.pack(endian + "iI", sequence >> 32, sequence & 0xffffffff)
    if fragment:
        body += struct.pack(endian + "I", 1)
    body += struct.pack(endian + "I", bits)
    body += b"".join(struct.pack(endian + "I", word) for word in words)
    body += struct.pack(endian + "I", 1)
    return header + bytes([0x12 if fragment else 0x06, flags]) + struct.pack(endian + "H", len(body)) + body


def metadata(reliable=True, little=True, writer_guid=GUID, topic="PlatformMissionStatus"):
    endian = "<" if little else ">"
    def parameter(pid, value):
        return struct.pack(endian + "HH", pid, len(value)) + value + b"\x00" * ((-len(value)) % 4)
    name = topic.encode("utf-8") + b"\x00"
    reliability = (parameter(0x001a, struct.pack(endian + "III", 2 if reliable else 1, 0, 0))
                   if reliable is not None else b"")
    return (bytes([0, 3 if little else 2, 0, 0]) + parameter(0x005a, bytes.fromhex(writer_guid))
            + parameter(0x0005, struct.pack(endian + "I", len(name)) + name)
            + reliability + struct.pack(endian + "HH", 1, 0))


def row(payload, timestamp=90):
    return {"ip.src": "10.0.0.1", "ip.dst": "10.0.0.2",
            "frame.time_epoch": str(timestamp), "udp.payload": payload.hex(), "udp.dstport": "57411"}


def observation(kind="sent", sequence=1, timestamp=NOW - 20_000_000_000, **changes):
    value = {"scope": "wan_wire", "schema_version": 1, "event": kind, "run_id": "test",
             "domain_id": 200, "writer_guid": GUID, "sequence": sequence,
             "timestamp_ns": timestamp, "source": "Control_20", "recipient": "Platform_30",
             "source_ip": "10.0.0.1", "destination_ip": "10.0.0.2",
             "node": "Platform_30" if kind == "received" else "Control_20",
             "topic": "ControlCommand", "reliability": "reliable"}
    if kind == "retransmitted":
        value.update(requested_at_ns=timestamp - 1, reader_guid=READER_PREFIX.hex() + READER.hex())
    return dict(value, **changes)


class WireParserTests(unittest.TestCase):
    def test_repair_request_bitmaps_and_identity(self):
        for little in (True, False):
            items = list(submessages(repair_request(2**32 + 7, (0, 2, 33), little=little)))
            self.assertEqual([item["sequence"] for item in items], [2**32 + 7, 2**32 + 9, 2**32 + 40])
            self.assertTrue(all(item["writer_guid"] == GUID for item in items))
            self.assertTrue(all(item["reader_guid"] == READER_PREFIX.hex() + READER.hex() for item in items))
            fragments = list(submessages(repair_request(9, (0, 32), little=little, fragment=True)))
            self.assertEqual([item["sequence"] for item in fragments], [9])
        self.assertEqual(list(submessages(repair_request(offsets=()))), [])
        self.assertEqual(list(submessages(repair_request(destination=None))), [])
        for payload in (repair_request()[:-1], repair_request(offsets=(256,)), repair_request(0)):
            with self.assertRaises(ValueError):
                list(submessages(payload))

    def test_retry_requires_request_and_matching_reader_not_original_fanout(self):
        with tempfile.TemporaryDirectory() as temporary:
            nodes = {"10.0.0.1": "Control_20", "10.0.0.2": "Platform_30", "10.0.0.3": "Platform_31"}
            capture = WireCapture(Path(temporary) / "wire.jsonl", "Control_20", "test",
                                  nodes, {"10.0.0.1"})
            capture.process(row(message(), 90), 200)
            capture.process(row(message(), 90.5), 200)
            request = dict(row(repair_request(), 91), **{"ip.src": "10.0.0.2", "ip.dst": "10.0.0.1"})
            capture.process(request, 200)
            capture.process(row(message(reader=bytes.fromhex("00002007")), 91.1), 200)
            capture.process(row(message(destination=PREFIX), 91.2), 200)
            capture.process(dict(row(message(), 91.3), **{"ip.dst": "10.0.0.3"}), 200)
            capture.process(dict(row(message(reader=READER, destination=READER_PREFIX), 91.4),
                                 **{"udp.dstport": "57413"}), 200)
            capture.process(row(message(reader=READER, destination=READER_PREFIX), 92), 200)
            capture.process(row(message(reader=READER, destination=READER_PREFIX), 93), 200)
            capture.close()
            events = [json.loads(line) for line in capture.path.read_text().splitlines()]
            self.assertEqual([event["event"] for event in events], ["sent", "sent", "retransmitted"])
            self.assertEqual(events[-1]["timestamp_ns"], 92_000_000_000)
            self.assertEqual(events[-1]["requested_at_ns"], 91_000_000_000)
            self.assertEqual(events[-1]["recipient"], "Platform_30")

    def test_fragment_recovery_uses_full_destination_completion(self):
        with tempfile.TemporaryDirectory() as temporary:
            nodes = {"10.0.0.1": "Control_20", "10.0.0.2": "Platform_30"}
            sender = WireCapture(Path(temporary) / "tx.jsonl", "Control_20", "test", nodes, {"10.0.0.1"})
            receiver = WireCapture(Path(temporary) / "rx.jsonl", "Platform_30", "test", nodes, {"10.0.0.2"})
            first = message(payload=b"abc", fragment=(1, 1, 3, 6))
            second = message(payload=b"def", fragment=(2, 1, 3, 6))
            sender.process(row(first, 90), 200)
            sender.process(row(second, 91), 200)
            receiver.process(row(first, 90.1), 200)
            request = dict(row(repair_request(fragment=True, offsets=(1,)), 91.5),
                           **{"ip.src": "10.0.0.2", "ip.dst": "10.0.0.1"})
            sender.process(request, 200)
            sender.process(row(second, 92), 200)
            receiver.process(row(second, 92.1), 200)
            metrics = WireDelivery()
            for capture in (receiver, sender):
                capture.close()
                for line in capture.path.read_text().splitlines():
                    metrics.ingest(json.loads(line))
            metrics.ingest(observation("endpoint", timestamp=93_000_000_000))
            flow = metrics.snapshot(now_ns=NOW)["rows"][0]
            self.assertEqual((flow["sent"], flow["received"], flow["recovered_samples"]), (1, 1, 1))
            self.assertAlmostEqual(flow["avg_recovery_ms"], 2100)

    def test_endianness_and_message_identity(self):
        for little in (True, False):
            item = list(submessages(message(2**32 + 7, little=little)))[0]
            self.assertEqual((item["writer_guid"], item["sequence"]), (GUID, 2**32 + 7))

    def test_multiple_submessages_and_destination_context(self):
        first = message(1)
        dst = bytes.fromhex("1112131415161718191a1b1c")
        packet = first + b"\x0e\x01\x0c\x00" + dst + message(2)[20:]
        items = list(submessages(packet))
        self.assertEqual([item["sequence"] for item in items], [1, 2])
        self.assertIsNone(items[0]["destination_prefix"])
        self.assertEqual(items[1]["destination_prefix"], dst.hex())

    def test_zero_length_pad_and_invalid_timestamp_preserve_following_submessages(self):
        for little in (True, False):
            for kind in (0x01, 0x09):
                flags = int(little) | (2 if kind == 0x09 else 0)
                empty = bytes([kind, flags, 0, 0])
                for packet in (message(little=little),
                               message(payload=b"abc", fragment=(1, 1, 3, 3), little=little),
                               repair_request(little=little)):
                    with self.subTest(little=little, kind=kind, packet=packet):
                        combined = packet[:20] + empty + packet[20:]
                        self.assertEqual(list(submessages(combined)), list(submessages(packet)))
                with tempfile.TemporaryDirectory() as temporary:
                    capture = WireCapture(
                        Path(temporary) / "wire.jsonl", "Control_20", "test",
                        {"10.0.0.1": "Control_20", "10.0.0.2": "Platform_30"}, {"10.0.0.1"})
                    packet = message(little=little)
                    capture.process(row(packet[:20] + empty + packet[20:]), 200)
                    capture.close()
                    events = [json.loads(line) for line in capture.path.read_text().splitlines()]
                    self.assertEqual(len(events), 1)
                    self.assertEqual((events[0]["event"], events[0]["sequence"]), ("sent", 1))

    def test_zero_length_data_still_consumes_the_packet_remainder(self):
        packet = message()
        self.assertEqual(list(submessages(packet[:22] + b"\x00\x00" + packet[24:])),
                         list(submessages(packet)))

    def test_discovered_reliability_not_topic_guess(self):
        self.assertEqual(endpoint_metadata(metadata())["reliability"], "reliable")
        self.assertEqual(endpoint_metadata(metadata(False))["reliability"], "best_effort")

    def test_omitted_writer_reliability_uses_standard_default_in_both_byte_orders(self):
        for little in (True, False):
            for reliable, expected in ((None, "reliable"), (True, "reliable"), (False, "best_effort")):
                with self.subTest(little=little, reliable=reliable):
                    result = endpoint_metadata(metadata(reliable, little, topic="ArbitraryTopic"))
                    self.assertEqual(result["reliability"], expected)
                    self.assertEqual(result["topic"], "ArbitraryTopic")
            payload = metadata(True, little)
            endian = "<" if little else ">"
            invalid_kind = payload.replace(struct.pack(endian + "HHI", 0x001a, 12, 2),
                                           struct.pack(endian + "HHI", 0x001a, 12, 99))
            self.assertEqual(endpoint_metadata(invalid_kind)["reliability"], "unknown")

    def test_default_requires_complete_writer_discovery(self):
        payload = metadata(None)
        self.assertIsNone(endpoint_metadata(b"application data"))
        self.assertIsNone(endpoint_metadata(payload[:4] + payload[24:]))
        self.assertIsNone(endpoint_metadata(payload[:24] + struct.pack("<HH", 1, 0)))
        self.assertIsNone(endpoint_metadata(metadata(None, writer_guid=READER_PREFIX.hex() + READER.hex())))
        invalid = [
            payload[:-4],
            payload[:-1],
            payload[:-4] + struct.pack("<HHI", 0x001a, 4, 1) + struct.pack("<HH", 1, 0),
        ]
        for packet in invalid:
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                endpoint_metadata(packet)

    def test_sentinel_length_is_ignored_in_both_byte_orders(self):
        for little in (True, False):
            endian = "<" if little else ">"
            payload = metadata(None, little)
            expected = endpoint_metadata(payload)
            for length in (0, 4, 65535):
                for trailing in (b"", b"ignored trailing bytes"):
                    with self.subTest(little=little, length=length, trailing=trailing):
                        packet = payload[:-4] + struct.pack(endian + "HH", 1, length) + trailing
                        self.assertEqual(endpoint_metadata(packet), expected)

    def test_defaulted_probe_discovery_backfills_qos_without_inventing_delivery(self):
        metrics = WireDelivery()
        with tempfile.TemporaryDirectory() as temporary:
            capture = WireCapture(
                Path(temporary) / "wire.jsonl", "Control_20", "test",
                {"10.0.0.1": "Control_20", "10.0.0.2": "Platform_30"}, {"10.0.0.1"})
            capture.process(row(message()), 200)
            events = [json.loads(line) for line in capture.path.read_text().splitlines()]
            for event in events:
                metrics.ingest(event)
            before = metrics.snapshot(now_ns=NOW)
            self.assertEqual(before["unknown_qos_flows"], 1)
            capture.process(row(message(payload=metadata(None, topic="RouterLinkProbe"),
                                        writer=bytes.fromhex("000003c2")), 91), 200)
            capture.close()
            events = [json.loads(line) for line in capture.path.read_text().splitlines()]
            metrics.ingest(events[-1])
        after = metrics.snapshot(now_ns=NOW)
        flow = after["rows"][0]
        self.assertEqual(after["unknown_qos_flows"], 0)
        self.assertEqual((flow["topic"], flow["reliability"], flow["settle_ms"]),
                         ("RouterLinkProbe", "reliable", 15_000))
        self.assertEqual((flow["sent"], flow["received"]),
                         (before["rows"][0]["sent"], before["rows"][0]["received"]))

    def test_dedup_ranges_keep_out_of_order_samples(self):
        seen = SequenceRanges()
        for value in (3, 1, 2, 6, 4, 5):
            self.assertTrue(seen.add(value))
        self.assertEqual(seen.ranges, [(1, 6)])
        self.assertFalse(seen.add(3))

    def test_truncated_packets_are_explicit_errors(self):
        for payload in (b"RTPS", message()[:-1], message(fragment=(1, 1, 10, 10))):
            with self.assertRaises(ValueError):
                list(submessages(payload))

    def test_fragment_repair_is_one_complete_sample_at_each_interface(self):
        with tempfile.TemporaryDirectory() as temporary:
            nodes = {"10.0.0.1": "Control_20", "10.0.0.2": "Platform_30"}
            sender = WireCapture(Path(temporary) / "tx.jsonl", "Control_20", "test", nodes, {"10.0.0.1"})
            receiver = WireCapture(Path(temporary) / "rx.jsonl", "Platform_30", "test", nodes, {"10.0.0.2"})
            first = message(payload=b"abc", fragment=(1, 1, 3, 6))
            second = message(payload=b"def", fragment=(2, 1, 3, 6))
            for capture in (sender, receiver):
                capture.process(row(first), 200)
                self.assertEqual(capture.path.read_text(), "")
                capture.process(row(first), 200)
                capture.process(row(second, 91), 200)
                capture.process(row(second, 92), 200)
                capture.close()
                events = [json.loads(line) for line in capture.path.read_text().splitlines()]
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["sequence"], 1)
                self.assertEqual(events[0]["event"], "sent" if capture is sender else "received")

    def test_unknown_destination_is_not_invented(self):
        with tempfile.TemporaryDirectory() as temporary:
            capture = WireCapture(Path(temporary) / "wire.jsonl", "Control_20", "test",
                                  {"10.0.0.1": "Control_20"}, {"10.0.0.1"})
            with self.assertLogs(level="WARNING"):
                capture.process(row(message()), 200)
            self.assertEqual(capture.unknown_destinations, 1)
            self.assertEqual(capture.path.read_text(), "")
            capture.close()

    def test_fragmented_discovery_backfills_the_actual_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            capture = WireCapture(Path(temporary) / "wire.jsonl", "Control_20", "test",
                                  {"10.0.0.1": "Control_20", "10.0.0.2": "Platform_30"},
                                  {"10.0.0.1"})
            payload = metadata(False)
            for index in reversed(range((len(payload) + 15) // 16)):
                capture.process(row(message(payload=payload[index * 16:(index + 1) * 16],
                    writer=bytes.fromhex("000003c2"),
                    fragment=(index + 1, 1, 16, len(payload)))), 200)
            capture.process(row(message()), 200)
            capture.close()
            events = [json.loads(line) for line in capture.path.read_text().splitlines()]
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["event"], "endpoint")
            self.assertEqual(events[1]["topic"], "PlatformMissionStatus")
            self.assertEqual(events[1]["reliability"], "best_effort")

    def test_reassembled_ip_datagram_reaches_wire_parser_once(self):
        from domain_traffic_monitor import TrafficAccumulator
        from traffic_accounting import FrameAccounting
        from test_traffic_accounting import frame
        with tempfile.TemporaryDirectory() as temporary:
            capture = WireCapture(Path(temporary) / "wire.jsonl", "Control_20", "test",
                                  {"10.0.0.1": "Control_20", "10.0.0.2": "Platform_30"},
                                  {"10.0.0.1"})
            accumulator = TrafficAccumulator()
            accounting = FrameAccounting([200], {"10.0.0.1"}, accumulator, wire_capture=capture)
            accounting.process(frame(**{"ip.flags.mf": "1", "rtps.guidPrefix.src": "",
                                        "udp.dstport": ""}))
            accounting.process(frame(**{"ip.frag_offset": "1480", "udp.payload": message().hex()}))
            capture.close()
            self.assertEqual(len(capture.path.read_text().splitlines()), 1)
            self.assertEqual(accumulator.drain()[200]["tx_packets"], 2)


class WireMetricTests(unittest.TestCase):
    def test_recovery_average_excludes_first_attempts_and_missing_samples(self):
        metrics = WireDelivery()
        start = NOW - 20_000_000_000
        for sequence in (1, 2, 3, 4):
            metrics.ingest(observation(sequence=sequence, timestamp=start))
            metrics.ingest(observation("retransmitted", sequence=sequence, timestamp=start + 50_000_000))
            metrics.ingest(observation("retransmitted", sequence=sequence, timestamp=start + 70_000_000))
        metrics.ingest(observation("received", sequence=1, timestamp=start + 100_000_000))
        metrics.ingest(observation("received", sequence=1, timestamp=start + 150_000_000))
        metrics.ingest(observation("received", sequence=2, timestamp=start + 300_000_000))
        metrics.ingest(observation("received", sequence=3, timestamp=start + 10_000_000))
        flow = metrics.snapshot(now_ns=NOW)["rows"][0]
        self.assertEqual((flow["sent"], flow["received"], flow["lost"]), (4, 3, 1))
        self.assertEqual(flow["recovered_samples"], 2)
        self.assertEqual(flow["avg_recovery_ms"], 200)
        self.assertEqual(metrics.snapshot(now_ns=NOW + 31_000_000_000)["rows"], [])

    def test_no_confirmed_recovery_is_null_not_zero_and_not_best_effort(self):
        for reliability in ("reliable", "best_effort", "unknown"):
            metrics = WireDelivery()
            metrics.ingest(observation(reliability=reliability))
            metrics.ingest(observation("received", reliability=reliability, timestamp=NOW))
            if reliability != "reliable":
                metrics.ingest(observation("retransmitted", timestamp=NOW - 19_000_000_000))
            flow = metrics.snapshot(now_ns=NOW)["rows"][0]
            self.assertIsNone(flow["avg_recovery_ms"])
            self.assertEqual(flow["recovered_samples"], 0)
        for changes in ({"requested_at_ns": None}, {"requested_at_ns": NOW + 1},
                        {"reader_guid": "not-a-guid"}, {"node": "Platform_30"},
                        {"completed_at_ns": 1}):
            with self.assertRaises(ValueError):
                WireDelivery().ingest(observation("retransmitted", **changes))

    def test_malformed_records_are_reported_without_stopping_ingestion(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wire.jsonl"
            path.write_text("[]\n" + json.dumps(observation(domain_id=233)) + "\n"
                            + json.dumps(observation(timestamp=time.time_ns())) + "\n")
            metrics = WireDelivery()
            with self.assertLogs("wire_delivery", level="WARNING"):
                self.assertTrue(metrics.poll([path], "test"))
            self.assertEqual(metrics.invalid_records, 2)
            self.assertEqual(len(metrics.sent), 1)

    def test_no_lan_or_application_events(self):
        metrics = WireDelivery()
        with self.assertRaises(ValueError):
            metrics.ingest({"event": "sent", "topic": "ControlCommand", "scope": "lan"})
        self.assertEqual(metrics.snapshot(now_ns=NOW)["rows"], [])

    def test_dedup_late_receive_corrects_loss_and_survives_mode_changes(self):
        metrics = WireDelivery()
        metrics.ingest(observation())
        metrics.ingest(observation())
        self.assertEqual(metrics.snapshot(now_ns=NOW)["rows"][0]["lost"], 1)
        metrics.ingest(observation("received", timestamp=NOW))
        metrics.ingest(observation("received", timestamp=NOW))
        item = metrics.snapshot(now_ns=NOW)["rows"][0]
        self.assertEqual((item["sent"], item["received"], item["lost"]), (1, 1, 0))

    def test_fanout_is_counted_per_actual_unicast_destination(self):
        metrics = WireDelivery()
        metrics.ingest(observation())
        metrics.ingest(observation(destination_ip="10.0.0.3", recipient="Platform_31"))
        metrics.ingest(observation("received"))
        rows = metrics.snapshot(now_ns=NOW)["rows"]
        self.assertEqual([(r["recipient"], r["received"], r["lost"]) for r in rows],
                         [("Platform_30", 1, 0), ("Platform_31", 0, 1)])

    def test_discovery_backfills_unknown_qos_without_inventing_delivery(self):
        metrics = WireDelivery()
        metrics.ingest(observation(reliability="unknown", topic=None))
        self.assertEqual(metrics.snapshot(now_ns=NOW)["unknown_qos_flows"], 1)
        event = observation()
        event.update(event="endpoint", topic="PlatformMissionStatus", reliability="best_effort")
        metrics.ingest(event)
        item = metrics.snapshot(now_ns=NOW)["rows"][0]
        self.assertEqual((item["topic"], item["reliability"], item["settle_ms"]),
                         ("PlatformMissionStatus", "best_effort", 2000))

    def test_grace_and_window_are_based_on_source_send_not_receive_time(self):
        metrics = WireDelivery()
        metrics.ingest(observation(timestamp=NOW - 3_000_000_000))
        self.assertEqual(metrics.snapshot(now_ns=NOW)["rows"][0]["pending"], 1)
        self.assertEqual(metrics.snapshot(now_ns=NOW)["rows"][0]["lost"], 0)
        self.assertEqual(metrics.snapshot(now_ns=NOW + 13_000_000_000)["rows"][0]["lost"], 1)
        self.assertEqual(metrics.snapshot(now_ns=NOW + 31_000_000_000)["rows"], [])

    def test_receiver_capture_can_arrive_before_sender_record(self):
        metrics = WireDelivery()
        metrics.ingest(observation("received"))
        metrics.ingest(observation())
        self.assertEqual(metrics.snapshot(now_ns=NOW)["rows"][0]["received"], 1)

    def test_partial_jsonl_is_retained_and_run_restart_isolated(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wire.jsonl"
            event = observation(timestamp=time.time_ns())
            text = json.dumps(event)
            path.write_text(text[:-3])
            metrics = WireDelivery()
            metrics.poll([path], "test")
            self.assertFalse(metrics.sent)
            with path.open("a") as out:
                out.write(text[-3:] + "\n")
            metrics.poll([path], "test")
            self.assertEqual(len(metrics.sent), 1)
            metrics.poll([path], "new-run")
            self.assertFalse(metrics.sent)


class ConfiguredWanQosTests(unittest.TestCase):
    def test_configuration_is_optional_and_missing_files_are_explicit_errors(self):
        self.assertFalse(load_delivery_config(None, None, None)["available"])
        with self.assertRaises(FileNotFoundError):
            load_delivery_config(ROOT / "nonexistent-router-config.yaml", None, None)

    def test_wan_aliases_reader_writer_settings_and_auto_qos_are_distinct(self):
        config = {
            "qos_libraries": [str(ROOT / "harness_v2/qos/act_qos_profiles.xml")],
            "qos_profiles": {"events": "Test::Events"}, "router": {},
            "participants": {
                "wan": {"on_wan": True, "role": "platform", "domain": 200},
                "lan": {"role": "platform", "domain": 30},
            },
            "routes": [
                {"name": name, "topics": [{"name": "Example"}], "source_side": {
                    "input": {"participant": "lan"},
                    "output": {"participant": "wan", "writer_qos": "events"}},
                 "destination_side": {"enabled": True,
                                      "input": {"participant": "wan", "reader_qos": "events"}}}
                for name in ("first", "second")
            ] + [{"name": "auto", "enabled": False, "topics": [{"name": "Auto"}],
                  "source_side": {"output": {"participant": "wan"}}},
                 {"name": "override", "enabled": True, "topics": [{"name": "Overrides"}],
                  "source_side": {"enabled": False, "output": {"participant": "wan"}},
                  "destination_side": {"input": {"participant": "wan"}}}],
        }
        def qos(depth):
            return SimpleNamespace(
                reliability=SimpleNamespace(kind="ReliabilityKind.RELIABLE"),
                history=SimpleNamespace(kind="HistoryKind.KEEP_LAST", depth=depth),
                durability=SimpleNamespace(kind="DurabilityKind.VOLATILE"),
                deadline=SimpleNamespace(period=float("inf")),
                liveliness=SimpleNamespace(kind="LivelinessKind.AUTOMATIC", lease_duration=2.5))
        writer = unittest.mock.Mock(return_value=qos(1))
        reader = unittest.mock.Mock(return_value=qos(50))
        provider = SimpleNamespace(datawriter_qos_from_profile=writer, datareader_qos_from_profile=reader)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.yaml"
            path.write_text(json.dumps(config))
            result = load_delivery_config(path, lambda libraries: provider, float("inf"))
        self.assertEqual(len(result["topics"]["Example"]), 2)
        entries = {entry["endpoint"]: entry for entry in result["topics"]["Example"]}
        self.assertEqual(entries["writer"]["settings"]["history_depth"], 1)
        self.assertEqual(entries["reader"]["settings"]["history_depth"], 50)
        self.assertEqual(entries["writer"]["routes"], ["first", "second"])
        self.assertIsNone(entries["writer"]["settings"]["deadline_seconds"])
        self.assertEqual(entries["reader"]["settings"]["lease_seconds"], 2.5)
        self.assertFalse(entries["writer"]["enabled"])
        self.assertTrue(entries["reader"]["enabled"])
        overrides = {entry["endpoint"]: entry["enabled"] for entry in result["topics"]["Overrides"]}
        self.assertEqual(overrides, {"writer": False, "reader": True})
        self.assertEqual(result["topics"]["Auto"][0]["settings"], None)
        self.assertFalse(result["topics"]["Auto"][0]["enabled"])
        writer.assert_called_with("Test::Events")
        reader.assert_called_with("Test::Events")

    def test_presence_supports_scalar_and_role_mapping_and_rejects_invalid_shapes(self):
        qos = SimpleNamespace(
            reliability=SimpleNamespace(kind="ReliabilityKind.RELIABLE"),
            history=SimpleNamespace(kind="HistoryKind.KEEP_LAST", depth=1),
            durability=SimpleNamespace(kind="DurabilityKind.VOLATILE"),
            deadline=SimpleNamespace(period=float("inf")),
            liveliness=SimpleNamespace(kind="LivelinessKind.AUTOMATIC", lease_duration=3))
        provider = SimpleNamespace(datawriter_qos_from_profile=lambda _: qos,
                                   datareader_qos_from_profile=lambda _: qos)
        config = {"qos_libraries": [str(ROOT / "harness_v2/qos/act_qos_profiles.xml")],
                  "router": {}, "routes": [],
                  "participants": {"wan": {"on_wan": True, "role": "control", "domain": 200}}}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.yaml"
            for presence in ("wan", {"control": "wan"}, {"control": "wan", "platform": "wan"}):
                with self.subTest(presence=presence):
                    config["router"]["presence_participant"] = presence
                    path.write_text(json.dumps(config))
                    result = load_delivery_config(path, lambda _: provider, float("inf"))
                    self.assertEqual(set(result["topics"]), {"RouterHealth", "RouterLinkProbe"})
                    for entries in result["topics"].values():
                        self.assertEqual(len(entries), 2)
                        self.assertTrue(all(entry["enabled"] for entry in entries))
            config["router"]["presence_participant"] = ["wan"]
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "presence_participant"):
                load_delivery_config(path, lambda _: provider, float("inf"))

    def test_installed_provider_resolves_current_wan_profiles(self):
        try:
            import rti.connextdds as dds
        except ModuleNotFoundError as error:
            if error.name not in ("rti", "rti.connextdds"):
                raise
            self.skipTest("Installed Connext binding required; no DDS entities created")
        result = load_delivery_config(
            ROOT / "router/config/control-platform.yaml", dds.QosProvider, dds.Duration.infinite)
        expected = {"RouterHealth": "BEST_EFFORT", "RouterLinkProbe": "RELIABLE",
                    "ControlCommand": "RELIABLE", "PlatformInitStatus": "BEST_EFFORT"}
        for topic, reliability in expected.items():
            with self.subTest(topic=topic):
                entries = result["topics"][topic]
                self.assertEqual({entry["endpoint"] for entry in entries}, {"reader", "writer"})
                self.assertTrue(all(entry["settings"]["reliability"] == reliability for entry in entries))
        for topic in ("PlatformMissionStatus", "ActRouterDiagnostics"):
            self.assertEqual({entry["endpoint"]: entry["enabled"] for entry in result["topics"][topic]},
                             {"writer": False, "reader": True})

    def test_mesh_participant_profiles_disable_default_domain_announcements(self):
        try:
            import rti.connextdds as dds
        except ModuleNotFoundError as error:
            if error.name not in ("rti", "rti.connextdds"):
                raise
            self.skipTest("Installed Connext binding required; no DDS entities created")
        provider = dds.QosProvider(str(ROOT / "harness_v2/qos/act_qos_profiles.xml"))
        profiles = (
            "participant_discovery_base", "lan_participant_base", "router_journal_participant",
            "lan_transport_loopback", "lan_router_participant",
            "lan_control_router_participant", "lan_platform_participant",
            "lan_control_participant", "wan_router_participant_base",
            "wan_router_participant", "wan_router_participant_emane",
        )
        for profile in profiles:
            with self.subTest(profile=profile):
                qos = provider.participant_qos_from_profile("ACT_QOS_LIB::" + profile)
                self.assertEqual(qos.discovery_config.default_domain_announcement_period,
                                 dds.Duration.infinite)
        deny_key = "dds.transport.UDPv4.builtin.parent.deny_interfaces_list"
        allow_key = "dds.transport.UDPv4.builtin.parent.allow_interfaces_list"
        for profile in profiles:
            with self.subTest(interface_scope=profile):
                qos = provider.participant_qos_from_profile("ACT_QOS_LIB::" + profile)
                if profile.startswith("lan_") or profile == "router_journal_participant":
                    self.assertEqual(qos.property.get(deny_key), "emane0")
                else:
                    self.assertFalse(qos.property.exists(deny_key))
        loopback = provider.participant_qos_from_profile("ACT_QOS_LIB::lan_transport_loopback")
        self.assertEqual(loopback.property.get(allow_key), "127.0.0.1")
        wan = provider.participant_qos_from_profile("ACT_QOS_LIB::wan_router_participant_emane")
        self.assertEqual(wan.property.get(allow_key), "emane0")
        from router_journal_subscriber import journal_participant_qos
        qos = journal_participant_qos()
        self.assertEqual(qos.discovery_config.default_domain_announcement_period, dds.Duration.infinite)
        self.assertEqual(qos.transport_builtin, dds.TransportBuiltin.udpv4)
        self.assertEqual(qos.property.get(deny_key), "emane0")


class ProductionTestQosTests(unittest.TestCase):
    def test_fixture_overrides_only_runtime_peer_locators(self):
        from conftest import WAN_PEER_ENV_DEFAULTS
        self.assertTrue(all("PEER" in name for name in WAN_PEER_ENV_DEFAULTS))

    def test_normal_calls_select_profiles_without_policy_keywords(self):
        for path in (ROOT / "router/test_e2e").glob("test_*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for call in ast.walk(tree):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id in {"reader_qos", "writer_qos"}):
                    with self.subTest(file=path.name, line=call.lineno):
                        self.assertEqual(len(call.args), 1)
                        self.assertEqual(call.keywords, [])

    def test_helper_policy_mutation_is_confined_to_incompatibility_helpers(self):
        path = ROOT / "router/test_e2e/util/dds_probe.py"
        tree = ast.parse(path.read_text(), filename=str(path))
        policies = {"reliability", "durability", "history", "deadline", "liveliness",
                    "ownership", "transport_builtin", "discovery_config"}
        exceptions = {"_apply_common", "incompatible_reader_qos", "incompatible_writer_qos"}
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Attribute) and target.attr in policies:
                            self.assertIn(function.name, exceptions, (function.name, node.lineno))
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "_apply_common"):
                    self.assertIn(function.name, exceptions - {"_apply_common"})

    def test_helpers_preserve_complete_production_qos(self):
        try:
            import rti.connextdds as dds
        except ImportError:
            self.skipTest("Connext SDK not installed")
        from util import dds_probe

        provider = dds.QosProvider(str(ROOT / "harness_v2/qos/act_qos_profiles.xml"))
        profiles = ("lan_event", "lan_status", "router_admin_status",
                    "router_admin_command", "router_admin_command_ack",
                    "router_controller_journal", "router_link_stats", "router_mesh_status",
                    "router_default_reader", "router_default_writer", "wan_event", "wan_status")
        for profile in profiles:
            with self.subTest(profile=profile):
                qualified = "ACT_QOS_LIB::" + profile
                self.assertEqual(dds_probe.reader_qos(profile),
                                 provider.datareader_qos_from_profile(qualified))
                self.assertEqual(dds_probe.writer_qos(profile),
                                 provider.datawriter_qos_from_profile(qualified))

        for profile in ("lan_router_participant", "wan_router_participant"):
            with self.subTest(participant_profile=profile), \
                    patch.object(dds_probe.dds, "DomainParticipant") as create, \
                    patch.object(dds_probe.dds, "Publisher"), \
                    patch.object(dds_probe.dds, "Subscriber"):
                probe = dds_probe.Probe(101, participant_profile=profile)
                create.assert_called_once_with(
                    101, provider.participant_qos_from_profile("ACT_QOS_LIB::" + profile))
                probe.close()

        with self.assertRaises(TypeError):
            dds_probe.reader_qos("lan_event", deadline_ms=500)
        with self.assertRaises(TypeError):
            dds_probe.writer_qos("lan_event", reliability="best_effort")

        base = dds_probe.reader_qos("lan_event")
        incompatible = dds_probe.incompatible_reader_qos("lan_event", deadline_ms=500)
        self.assertEqual(incompatible.deadline.period, dds.Duration.from_milliseconds(500))
        incompatible.deadline = base.deadline
        self.assertEqual(incompatible, base)

        for normal, adverse in ((dds_probe.reader_qos, dds_probe.incompatible_reader_qos),
                                (dds_probe.writer_qos, dds_probe.incompatible_writer_qos)):
            base = normal("wan_event")
            incompatible = adverse("wan_event", reliability="best_effort")
            policy = incompatible.reliability
            self.assertEqual(policy.kind, dds.ReliabilityKind.BEST_EFFORT)
            policy.kind = base.reliability.kind
            incompatible.reliability = policy
            self.assertEqual(incompatible, base)


class ContainerDiscoveryTests(unittest.TestCase):
    def test_container_peers_probe_ids_zero_and_one(self):
        with tempfile.TemporaryDirectory(prefix="container-discovery-") as work:
            root = Path(work)
            script = root / "harness_v2/scripts/run_container_baseline.sh"
            config = root / "router/config/control-platform.yaml"
            script.parent.mkdir(parents=True)
            config.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / "harness_v2/scripts/run_container_baseline.sh", script)
            shutil.copyfile(ROOT / "router/config/control-platform.yaml", config)
            for emane in (False, True):
                with self.subTest(emane=emane):
                    output = root / "debug/mesh_runs" / ("emane" if emane else "container")
                    with patch.dict("os.environ", {"CONNEXT_SHARED_DIR": str(root)}):
                        subprocess.run(
                            ["bash", str(script), "render", "--platforms", "2",
                             "--workdir", str(output), *(["--emane"] if emane else [])],
                            check=True, capture_output=True, text=True, timeout=15)
                    peers = re.findall(r"^\s+WAN_PEER(?:\d+)?: (.+)$",
                                       (output / "compose.yaml").read_text(), re.MULTILINE)
                    expected = ({"1@builtin.udpv4://10.88.0.1",
                                 "1@builtin.udpv4://10.88.0.2",
                                 "1@builtin.udpv4://10.88.0.3"} if emane else
                                {"1@control-20", "1@platform-30", "1@platform-31"})
                    self.assertEqual(len(peers), 33)
                    self.assertEqual(set(peers), expected)


class WireBridgeTests(unittest.TestCase):
    def test_package_import_aggregates_capture_windows(self):
        self.make_bridge()
        result = subprocess.run(
            [sys.executable, "-I", "-c", """
import sys
import time
sys.path.insert(0, sys.argv[1])
from gui.mesh_dashboard.server.mesh_bridge import DdsBridge
from gui.mesh_dashboard.server.traffic_metrics import counter_fields

now = int(time.time() * 1000)
sample = dict(dict.fromkeys(counter_fields(), 0), observer="Control_20", domain_id=200,
              schema_version=3, capture_timestamp=now - 2000, interval_ms=1000,
              capture_window_start_ms=now - 3000, reported_at_ms=now,
              timestamp_basis="capture", capture_timing_valid=True,
              capture_status="live", application_tx_bytes=100)
bridge = DdsBridge.__new__(DdsBridge)
bridge.traffic_cache = {("Control_20", 200): sample}
bridge.traffic_history = {("Control_20", 200): [sample]}
bridge.traffic_observers = {"Control_20"}
aggregate = bridge._aggregate_traffic_locked()[0]
assert aggregate["complete"] and aggregate["windows_aligned"], aggregate
assert aggregate["application_tx_bytes_per_second"] == 100, aggregate
""", str(ROOT)],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_asynchronous_capture_windows_are_aggregated_without_mixing_intervals(self):
        bridge = self.make_bridge()
        from traffic_metrics import counter_fields
        now = int(time.time() * 1000)

        def sample(node, end, count):
            return dict(dict.fromkeys(counter_fields(), 0), observer=node, domain_id=200,
                        schema_version=3, capture_timestamp=end, interval_ms=1000,
                        capture_window_start_ms=end - 1000, reported_at_ms=now,
                        timestamp_basis="capture", capture_timing_valid=True,
                        capture_status="live", capture_invalid_rows_interval=0,
                        late_capture_packets_interval=0, orphan_fragment_packets_interval=0,
                        application_tx_bytes=count)

        bridge.update_traffic(sample("Control_20", now - 3000, 100))
        bridge.update_traffic(sample("Platform_30", now - 3000, 200))
        bridge.update_traffic(sample("Control_20", now - 2000, 9000))
        pending = bridge._aggregate_traffic_locked()[0]
        self.assertTrue(pending["complete"])
        self.assertEqual(pending["application_tx_bytes_per_second"], 300)
        bridge.update_traffic(sample("Platform_30", now - 2000, 400))
        complete = bridge._aggregate_traffic_locked()[0]
        self.assertTrue(complete["windows_aligned"])
        self.assertEqual(complete["application_tx_bytes_per_second"], 9400)

    def make_bridge(self):
        try:
            import rti.connextdds
        except ModuleNotFoundError as error:
            if error.name not in ("rti", "rti.connextdds"):
                raise
            self.skipTest("Connext binding required to import bridge; no DDS entities created")
        from mesh_bridge import DdsBridge
        bridge = DdsBridge.__new__(DdsBridge)
        bridge.cache_lock = threading.RLock()
        bridge.cache = {}
        bridge.traffic_cache = {}
        bridge.traffic_history = {}
        bridge.traffic_observers = {"Control_20", "Platform_30"}
        bridge.traffic_published_at = {}
        bridge.latest_traffic_aggregate = []
        bridge.wire_delivery = WireDelivery()
        return bridge

    def test_incomplete_capture_masks_recovery_average_and_sample_count(self):
        bridge = self.make_bridge()
        metrics = WireDelivery()
        metrics.ingest(observation())
        metrics.ingest(observation("retransmitted", timestamp=NOW - 19_500_000_000))
        metrics.ingest(observation("received", timestamp=NOW - 19_000_000_000))
        nodes = ["Control_20", "Platform_30"]
        bridge.wire_delivery = metrics
        bridge.traffic_cache = {
            (node, 200): {"wire_delivery_enabled": True, "capture_status": "live",
                          "wire_coverage_since_ms": NOW / 1_000_000 - 60_000}
            for node in nodes}
        bridge._aggregate_traffic_locked = lambda: [{"domain_id": 200, "contributors": nodes}]
        with patch("wire_delivery.time.time_ns", return_value=NOW):
            flow = bridge.delivery_snapshot()["rows"][0]
            self.assertTrue(flow["coverage_complete"])
            self.assertEqual((flow["recovered_samples"], flow["avg_recovery_ms"]), (1, 1000))
            bridge.traffic_cache["Platform_30", 200]["capture_gap"] = True
            flow = bridge.delivery_snapshot()["rows"][0]
            self.assertFalse(flow["coverage_complete"])
            self.assertIsNone(flow["recovered_samples"])
            self.assertIsNone(flow["avg_recovery_ms"])
            self.assertEqual(flow["observed_recovered_samples"], 1)

    def update_capture(self, bridge, node, timestamp_ms, **changes):
        sample = dict(observer=node, domain_id=200, capture_timestamp=timestamp_ms,
                      interval_ms=1000, wire_delivery_enabled=True, capture_status="live",
                      capture_restarts=0)
        with patch("traffic_metrics.time.time", return_value=timestamp_ms / 1000):
            bridge.update_traffic(dict(sample, **changes))

    def test_gaps_remain_unknown_until_a_full_clean_selected_window(self):
        now_ms = NOW // 1_000_000
        for fault in ({"capture_gap": True}, {"capture_status": "restarting"},
                      {"wire_delivery_enabled": False}):
            with self.subTest(fault=fault):
                bridge = self.make_bridge()
                bridge.wire_delivery.ingest(observation(timestamp=NOW - 20_500_000_000))
                bridge.wire_delivery.ingest(observation(sequence=2, timestamp=NOW - 9_000_000_000))
                for timestamp in range(now_ms - 60_000, now_ms + 1, 1000):
                    self.update_capture(bridge, "Control_20", timestamp)
                    self.update_capture(bridge, "Platform_30", timestamp,
                                        **(fault if timestamp == now_ms - 20_000 else {}))
                with patch("time.time", return_value=now_ms / 1000), \
                        patch("time.time_ns", return_value=NOW):
                    row = bridge.delivery_snapshot()["rows"][0]
                    self.assertFalse(row["coverage_complete"])
                    self.assertEqual(row["observed_missing"], 1)
                    self.assertIsNone(row["lost"])
                    self.assertIsNone(row["avg_recovery_ms"])
                    self.assertTrue(bridge.delivery_snapshot(window_ms=10_000)["rows"][0]["coverage_complete"])
                for timestamp in range(now_ms + 1000, now_ms + 12_001, 1000):
                    for node in ("Control_20", "Platform_30"):
                        self.update_capture(bridge, node, timestamp)
                with patch("time.time", return_value=(now_ms + 12_000) / 1000), \
                        patch("time.time_ns", return_value=NOW + 12_000_000_000):
                    row = bridge.delivery_snapshot()["rows"][0]
                    self.assertTrue(row["coverage_complete"])
                    self.assertEqual(row["lost"], 1)

    def test_cumulative_parser_errors_allow_recovery_after_a_full_clean_window(self):
        bridge = self.make_bridge()
        now_ms = NOW // 1_000_000
        error_at = now_ms - 45_000
        for sequence, timestamp in enumerate(range(now_ms - 90_000, now_ms + 1, 1000), 1):
            bridge.wire_delivery.ingest(observation(sequence=sequence, timestamp=timestamp * 1_000_000))
            self.update_capture(bridge, "Control_20", timestamp)
            self.update_capture(bridge, "Platform_30", timestamp,
                                capture_invalid_rows=int(timestamp >= error_at))
            if timestamp in (error_at, error_at + 30_000, error_at + 31_000):
                with self.subTest(timestamp=timestamp), \
                        patch("time.time", return_value=timestamp / 1000), \
                        patch("time.time_ns", return_value=timestamp * 1_000_000):
                    snapshot = bridge.delivery_snapshot()
                    covered = timestamp == error_at + 31_000
                    self.assertEqual(snapshot["rows"][0]["coverage_complete"], covered)
                    self.assertEqual(snapshot["rows"][0]["lost"] is not None, covered)
                    self.assertEqual(snapshot["capture"]["capture_complete"], timestamp != error_at)
        sample = bridge.traffic_cache["Platform_30", 200]
        self.assertEqual(sample["capture_invalid_rows"], 1)
        self.assertEqual(sample["capture_invalid_rows_interval"], 0)
        self.assertEqual(sample["wire_coverage_since_ms"], error_at + 1000)
        bridge.wire_delivery.ingest(observation(sequence=1000))
        bridge.wire_delivery.ingest(observation("retransmitted", sequence=1000,
                                               timestamp=NOW - 19_500_000_000))
        bridge.wire_delivery.ingest(observation("received", sequence=1000,
                                               timestamp=NOW - 19_000_000_000))
        with patch("time.time", return_value=now_ms / 1000), patch("time.time_ns", return_value=NOW):
            flow = bridge.delivery_snapshot()["rows"][0]
            self.assertTrue(flow["coverage_complete"])
            self.assertGreater(flow["lost"], 0)
            self.assertEqual((flow["recovered_samples"], flow["avg_recovery_ms"]), (1, 1000))

    def test_parser_error_counter_reset_restarts_coverage(self):
        bridge = self.make_bridge()
        now_ms = NOW // 1_000_000
        self.update_capture(bridge, "Platform_30", now_ms, capture_invalid_rows=1)
        self.assertIsNone(bridge.traffic_cache["Platform_30", 200]["wire_coverage_since_ms"])
        self.update_capture(bridge, "Platform_30", now_ms + 1000, capture_invalid_rows=1)
        self.assertEqual(bridge.traffic_cache["Platform_30", 200]["wire_coverage_since_ms"], now_ms + 1000)
        self.update_capture(bridge, "Platform_30", now_ms + 2000, capture_invalid_rows=0)
        self.assertEqual(bridge.traffic_cache["Platform_30", 200]["wire_coverage_since_ms"], now_ms + 2000)
        self.update_capture(bridge, "Platform_30", now_ms + 3000, capture_invalid_rows=0)
        self.assertEqual(bridge.traffic_cache["Platform_30", 200]["wire_coverage_since_ms"], now_ms + 2000)

    def test_startup_restarts_and_missing_reports_reset_coverage(self):
        now_ms = NOW // 1_000_000
        cases = [
            ({}, {}, now_ms + 1000, 1000, now_ms),
            ({}, {"capture_restarts": 1}, now_ms + 1000, 1000, now_ms + 1000),
            ({"capture_restarts": 1}, {}, now_ms + 1000, 1000, now_ms + 1000),
            ({}, {}, now_ms + 5000, 1000, now_ms + 5000),
            ({}, {}, now_ms + 5000, 5000, now_ms),
        ]
        for initial, changes, timestamp, interval, expected in cases:
            with self.subTest(initial=initial, changes=changes, interval=interval, timestamp=timestamp):
                bridge = self.make_bridge()
                bridge.wire_delivery.ingest(observation())
                for node in ("Control_20", "Platform_30"):
                    self.update_capture(bridge, node, now_ms, **initial)
                with patch("time.time", return_value=now_ms / 1000), \
                        patch("time.time_ns", return_value=NOW):
                    self.assertFalse(bridge.delivery_snapshot()["rows"][0]["coverage_complete"])
                    self.assertEqual(bridge.traffic_cache["Platform_30", 200]["wire_coverage_since_ms"], now_ms)
                self.update_capture(bridge, "Platform_30", timestamp, interval_ms=interval, **changes)
                self.assertEqual(bridge.traffic_cache["Platform_30", 200]["wire_coverage_since_ms"], expected)
                self.update_capture(bridge, "Platform_30", now_ms, capture_gap=True)
                self.assertEqual(bridge.traffic_cache["Platform_30", 200]["wire_coverage_since_ms"], expected)


class WireDashboardTests(unittest.TestCase):
    def test_link_activity_side_drawer_layout_and_toggle(self):
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError:
            self.skipTest("Playwright is required")
        static = ROOT / "gui/mesh_dashboard/static"
        html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", (static / "index.html").read_text())
        with sync_playwright() as driver:
            browser = driver.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/**", lambda route: route.fulfill(json={}))
            page.route("http://activity.test/", lambda route: route.fulfill(body=html, content_type="text/html"))
            page.goto("http://activity.test/")
            page.evaluate("() => { window.WebSocket = class {}; }")
            page.add_script_tag(path=str(static / "vendor/vis-network.min.js"))
            page.add_script_tag(path=str(static / "traffic_rates.js"))
            page.add_script_tag(path=str(static / "mesh_graph.js"))
            page.add_script_tag(path=str(static / "operator_tabs.js"))
            page.evaluate("""() => {
                document.querySelector('#traffic-panel').innerHTML = '<div style="height:200px"></div>';
                document.documentElement.style.setProperty('--mesh-bottom-inset', '227px');
            }""")
            drawer = page.locator("#activity-panel")
            toggle = page.locator("#activity-toggle")
            self.assertEqual(toggle.get_attribute("aria-expanded"), "false")
            self.assertFalse(page.locator("#activity-body").is_visible())
            self.assertLess(drawer.bounding_box()["width"], 50)
            toggle.click()
            for width, height in ((1440, 1000), (1000, 800), (600, 900)):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_function("""() => {
                    const drawer = document.querySelector('#activity-panel').getBoundingClientRect();
                    const graph = document.querySelector('#graph').getBoundingClientRect();
                    return graph.right <= drawer.left;
                }""")
                expanded = drawer.bounding_box()
                chart = page.locator("#traffic-panel").bounding_box()
                self.assertLessEqual(expanded["y"] + expanded["height"], chart["y"])
                filters = page.locator(".activity-controls select").all()
                for before, after in zip(filters, filters[1:]):
                    first, second = before.bounding_box(), after.bounding_box()
                    self.assertLessEqual(first["y"] + first["height"], second["y"])
                toggle.click()
                self.assertEqual(toggle.get_attribute("aria-expanded"), "false")
                self.assertFalse(page.locator("#activity-body").is_visible())
                collapsed = drawer.bounding_box()
                self.assertLess(collapsed["width"], 50)
                self.assertGreater(collapsed["height"], collapsed["width"])
                self.assertEqual(toggle.evaluate("el => getComputedStyle(el).writingMode"), "vertical-rl")
                toggle.click()
                self.assertEqual(toggle.get_attribute("aria-expanded"), "true")
                self.assertTrue(page.locator("#activity-body").is_visible())
            page.locator('[data-tab="delivery"]').click()
            self.assertFalse(drawer.is_visible())
            page.locator('[data-tab="mesh"]').click()
            self.assertTrue(drawer.is_visible())
            self.assertFalse(errors)
            browser.close()

    def test_source_destination_multitopic_filters_and_configuration_sidebar(self):
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError:
            self.skipTest("Playwright is required")
        static = ROOT / "gui/mesh_dashboard/static"
        html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", (static / "index.html").read_text())
        common = dict(percentage_ready=True, sent_event_version=1, received_event_version=1,
                      recovered_samples=0, avg_recovery_ms=None)
        snapshot = {"scope": "wan_wire", "window_ms": 30_000, "rows": [
            dict(common, topic="ControlCommand", source="Control_20", recipient="Platform_30",
                 reliability="reliable", sent=2, received=1, settled=2, settled_received=1, lost=1),
            dict(common, topic="ContactReport", source="Platform_30", recipient="Control_20",
                 reliability="reliable", sent=3, received=3, settled=3, settled_received=3, lost=0),
            dict(common, topic="ContactReport", source="Platform_31", recipient="Control_20",
                 reliability="reliable", sent=5, received=4, settled=5, settled_received=4, lost=1),
            dict(common, topic="RouterHealth", source="Control_20", recipient="Platform_31",
                 reliability="best_effort", sent=4, received=3, settled=4, settled_received=3, lost=1),
        ]}
        settings = dict(reliability="RELIABLE", history="KEEP_ALL", history_depth=None,
                        durability="VOLATILE", deadline_seconds=None, liveliness="AUTOMATIC", lease_seconds=None)
        config = {"available": True, "config_file": "/run/node-config.yaml", "qos_libraries": ["qos.xml"],
                  "topics": {topic: [dict(endpoint="writer", participant="platform_wan", domain_id=200,
                      profile="ACT_QOS_LIB::wan_event", routes=["events"], enabled=True, settings=settings)]
                      for topic in ("ContactReport", "PlatformMissionStatus")}}
        config["topics"]["ContactReport"].append(dict(
            config["topics"]["ContactReport"][0], endpoint="reader", participant="control_wan"))
        with sync_playwright() as driver:
            browser = driver.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            def serve(route):
                if "/api/delivery_config" in route.request.url:
                    route.fulfill(json=config)
                elif "/api/delivery_stats" in route.request.url:
                    route.fulfill(json=snapshot)
                elif "/api/" in route.request.url:
                    route.fulfill(json={})
                else:
                    route.fulfill(body=html, content_type="text/html")
            page.route("**/*", serve)
            page.goto("http://wire.test/")
            page.evaluate("() => { window.WebSocket = class { constructor() { window.deliverySocket = this; } }; }")
            page.add_script_tag(path=str(static / "operator_tabs.js"))
            page.locator('[data-tab="delivery"]').click()
            page.wait_for_selector(".delivery-config details")
            page.wait_for_function(
                "() => document.querySelector('#delivery-config-status').textContent === 'Config: node-config.yaml'")
            page.locator(".delivery-config details").first.evaluate("element => { element.open = true; }")
            page.locator('[data-tab="mesh"]').click()
            with page.expect_response("**/api/delivery_config"):
                page.locator('[data-tab="delivery"]').click()
            page.wait_for_function(
                "() => document.querySelector('#delivery-config-status').textContent === 'Config: node-config.yaml'")
            self.assertTrue(page.locator(".delivery-config details").first.evaluate("element => element.open"))
            page.route("**/api/delivery_config", lambda route: route.fulfill(status=503))
            page.locator('[data-tab="mesh"]').click()
            page.locator('[data-tab="delivery"]').click()
            page.wait_for_function(
                "() => document.querySelector('#delivery-config-status').textContent.includes('unavailable: HTTP 503')")
            self.assertEqual(page.locator(".delivery-config details").count(), 0)
            page.unroute("**/api/delivery_config")
            page.locator('[data-tab="mesh"]').click()
            page.locator('[data-tab="delivery"]').click()
            page.wait_for_function(
                "() => document.querySelector('#delivery-config-status').textContent === 'Config: node-config.yaml'")
            rows = page.locator(".delivery-topic-row")
            self.assertEqual(rows.count(), 4)
            page.locator("#delivery-filter-source").select_option("Control_20")
            self.assertEqual(rows.count(), 2)
            page.locator("#delivery-filter-destination").select_option("Platform_30")
            self.assertEqual(rows.count(), 1)
            self.assertIn("ControlCommand", rows.inner_text())
            self.assertIn("1/2", page.locator("#delivery-reliable-summary").inner_text())
            page.locator("#delivery-filter-source").select_option("all")
            page.locator("#delivery-filter-destination").select_option("Control_20")
            self.assertEqual(rows.count(), 2)
            self.assertIn("7/8", page.locator("#delivery-reliable-summary").inner_text())
            page.locator("#delivery-topic-filter summary").click()
            page.locator("#delivery-topics-clear").click()
            self.assertEqual(rows.count(), 0)
            page.locator('#delivery-topic-options input[value="ContactReport"]').check()
            self.assertEqual(rows.count(), 2)
            self.assertIn("RELIABLE", page.locator("#delivery-config-topics").inner_text())
            self.assertIn("KEEP_ALL", page.locator("#delivery-config-topics").inner_text())
            self.assertEqual(page.locator(".delivery-config-endpoint").count(), 1)
            self.assertIn("Writer / Reader", page.locator("#delivery-config-topics").inner_text())
            page.locator('#delivery-topic-options input[value="RouterHealth"]').check()
            page.locator("#delivery-filter-destination").select_option("all")
            self.assertEqual(rows.count(), 3)
            page.locator("#delivery-filter-source").select_option("Platform_30")
            snapshot["rows"] = []
            page.evaluate("data => window.deliverySocket.onmessage({data:JSON.stringify({type:'delivery_stats',data})})", snapshot)
            self.assertEqual(page.locator("#delivery-filter-source").input_value(), "Platform_30")
            self.assertTrue(page.locator('#delivery-topic-options input[value="ContactReport"]').is_checked())
            self.assertEqual(rows.count(), 0)
            page.locator("#delivery-topics-clear").click()
            page.locator('#delivery-topic-options input[value="PlatformMissionStatus"]').check()
            self.assertIn("PlatformMissionStatus", page.locator("#delivery-config-topics").inner_text())
            self.assertEqual(rows.count(), 0)
            for width in (800, 375):
                page.set_viewport_size({"width": width, "height": 1000})
                self.assertTrue(page.locator("#delivery-panel").evaluate("p => p.scrollWidth <= p.clientWidth"))
            self.assertFalse(errors)
            browser.close()

    def test_grouping_uses_discovery_and_summaries_do_not_overlap(self):
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError:
            self.skipTest("Playwright is required")
        static = ROOT / "gui/mesh_dashboard/static"
        document = (static / "index.html").read_text()
        html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", document)
        common = dict(source="Control_20", recipient="Platform_30", percentage_ready=True,
                      sent_event_version=1, received_event_version=1)
        snapshot = {"scope": "wan_wire", "window_ms": 30_000, "unknown_qos_flows": 1,
                    "node_modes": {"Platform_30": "init"}, "rows": [
            dict(common, topic="ControlCommand", reliability="reliable", sent=2,
                 received=2, settled=2, settled_received=2, lost=0,
                 avg_recovery_ms=125.5, recovered_samples=2),
            dict(common, topic="RouterHealth", reliability="best_effort", sent=3,
                 received=1, settled=3, settled_received=1, lost=2),
            dict(common, topic="PlatformMissionStatus", reliability="unknown", sent=1,
                 received=0, settled=1, settled_received=0, lost=1),
        ]}
        with sync_playwright() as driver:
            browser = driver.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def serve(route):
                if "/api/delivery_stats" in route.request.url:
                    route.fulfill(json=snapshot)
                elif "/api/" in route.request.url:
                    route.fulfill(json={})
                else:
                    route.fulfill(body=html, content_type="text/html")

            page.route("**/*", serve)
            page.goto("http://wire.test/")
            page.evaluate("() => { window.WebSocket = class { constructor() { window.deliverySocket = this; } }; }")
            page.add_script_tag(path=str(static / "operator_tabs.js"))
            page.locator('[data-tab="delivery"]').click()
            page.wait_for_selector(".delivery-reliability-group")
            self.assertEqual(page.locator(".delivery-reliability-group").all_text_contents(),
                             ["Reliable", "Best Effort", "Unknown QoS"])
            self.assertIn("2/2", page.locator("#delivery-reliable-summary").inner_text())
            self.assertIn("1/3", page.locator("#delivery-periodic-summary").inner_text())
            self.assertEqual(page.locator(".delivery-recovery").all_text_contents(), ["125.5 ms (2)", "—", "—"])
            self.assertEqual(page.locator(".delivery-reliability-group th").first.get_attribute("colspan"), "5")
            self.assertIn("RouterHealth", page.locator(
                '.delivery-topic-row[data-reliability="best_effort"]').inner_text())
            colors = page.locator(".delivery-topic-row").evaluate_all(
                "rows => rows.map(row => getComputedStyle(row).backgroundColor)")
            self.assertEqual(len(set(colors)), 3)
            page.locator("#delivery-filter-node").select_option("Platform_30")
            self.assertEqual(page.locator("#delivery-filter-resolution").input_value(), "all")
            self.assertEqual(page.locator(".delivery-topic-row").count(), 3)
            self.assertIn("not DDS reader acceptance", page.locator(".delivery-definitions").inner_text())
            self.assertIn("Unknown QoS", page.locator("#delivery-capture-status").inner_text())
            guide = page.locator(".delivery-guide")
            self.assertIn("rolling 30-second counts", guide.inner_text())
            self.assertIn("15-second grace", guide.inner_text())
            self.assertIn("2-second grace", guide.inner_text())
            self.assertIn("settled received / settled sent", guide.inner_text())
            self.assertIn("does not reset every 30 seconds", guide.inner_text())
            self.assertIn("original WAN send to complete WAN receipt", guide.inner_text())
            desktop_guide = guide.bounding_box()
            desktop_content = page.locator(".delivery-content").bounding_box()
            self.assertLessEqual(desktop_guide["x"] + desktop_guide["width"], desktop_content["x"])
            page.set_viewport_size({"width": 600, "height": 900})
            mobile_guide = guide.bounding_box()
            mobile_content = page.locator(".delivery-content").bounding_box()
            self.assertLessEqual(mobile_guide["y"] + mobile_guide["height"], mobile_content["y"])
            self.assertTrue(page.evaluate(
                "document.querySelector('#delivery-panel').scrollWidth <= "
                "document.querySelector('#delivery-panel').clientWidth"))
            self.assertFalse(errors)
            page.set_viewport_size({"width": 375, "height": 900})
            self.assertTrue(page.evaluate(
                "document.querySelector('#delivery-panel').scrollWidth <= "
                "document.querySelector('#delivery-panel').clientWidth"))
            for changes, text in [
                ({"avg_recovery_ms": 1500}, "1.50 s (2)"),
                ({"avg_recovery_ms": None, "recovered_samples": 0}, "—"),
                ({"coverage_complete": False}, "Unknown"),
            ]:
                snapshot["rows"][0].update(changes)
                page.evaluate(
                    "data => window.deliverySocket.onmessage({data: JSON.stringify({type: 'delivery_stats', data})})",
                    snapshot)
                self.assertEqual(page.locator(".delivery-recovery").first.inner_text(), text)
            browser.close()


if __name__ == "__main__":
    unittest.main()
