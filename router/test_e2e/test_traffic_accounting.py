"""Pure accounting regressions; no DDS entities or live network required."""

import json
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "debug/scripts"))
sys.path.insert(0, str(ROOT / "gui/mesh_dashboard/server"))

from traffic_accounting import FIELDS, TX_CLASSES, FrameAccounting, classify_packet
from domain_traffic_monitor import (TrafficAccumulator, EmaneSampler, build_port_filter,
                                   _start_tshark_live, CaptureBatches)
from traffic_metrics import aggregate, coherent_samples, counter_fields, validate_sample


PREFIX = "0102030405060708090a0b0c"
WRITER = "00001002"


def frame(**changes):
    row = dict.fromkeys(FIELDS, "")
    row.update({
        "frame.time_epoch": "100", "frame.len": "120",
        "ip.src": "10.0.0.1", "ip.dst": "10.0.0.2", "ip.id": "0x1234",
        "ip.proto": "17",
        "ip.frag_offset": "0", "ip.flags.mf": "0", "udp.dstport": "57400",
        "rtps.guidPrefix.src": PREFIX, "rtps.sm.wrEntityId": WRITER,
        "rtps.sm.id": "0x15",
    })
    row.update(changes)
    return "|".join(row[field] for field in FIELDS)


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.accumulator = TrafficAccumulator()
        self.parser = FrameAccounting([200], {"10.0.0.1"}, self.accumulator)

    def test_application_ack_is_control_not_data(self):
        for control in ("0x1c", "0x1d", "0x06", "0x1a", "0x1e"):
            with self.subTest(control=control):
                self.assertEqual(classify_packet(WRITER, control), "reliability")
        self.assertEqual(classify_packet(WRITER, "0x15,0x07"), "mixed")

    def test_spdp2_discovery_writers_do_not_require_topic_metadata(self):
        for entity in ("0x00010082", "00:01:00:82", "0x00010182", "0xff010182"):
            with self.subTest(entity=entity):
                self.parser.process(frame(**{
                    "frame.len": "230", "rtps.sm.wrEntityId": entity,
                    "rtps.sm.id": "0x09,0x15",
                }))
                self.assertEqual(classify_packet(entity, "0x09,0x15"), "discovery")
                counters = self.accumulator.drain()[200]
                self.assertEqual(counters["discovery_bytes"], 230)
                self.assertEqual(counters["discovery_tx_bytes"], 230)
                self.assertEqual(counters["unknown_tx_bytes"], 0)
                self.assertEqual(counters["application_tx_bytes"], 0)
                self.assertEqual(counters["tx_bytes"], 230)
                self.assertEqual(counters["tx_packets"], 1)

    def test_spdp2_control_and_bundled_frames_keep_exclusive_accounting(self):
        for entities, submessages, frame_class, tx_class in (
            ("0x00010182", "0x07", "reliability", "discovery"),
            ("0x00010182", "0x15,0x07", "mixed", "discovery"),
            ("0x00010082," + WRITER, "0x15,0x15", "mixed", "mixed"),
        ):
            with self.subTest(entities=entities, submessages=submessages):
                self.parser.process(frame(**{
                    "rtps.sm.wrEntityId": entities, "rtps.sm.id": submessages,
                }))
                self.assertEqual(classify_packet(entities, submessages), frame_class)
                counters = self.accumulator.drain()[200]
                self.assertEqual(counters[f"{tx_class}_tx_bytes"], 120)
                self.assertEqual(counters["tx_bytes"],
                                 sum(counters[f"{kind}_tx_bytes"] for kind in TX_CLASSES))

    def test_other_vendor_specific_writers_remain_unknown(self):
        for entity in ("0x00019982", "0xff020082"):
            with self.subTest(entity=entity):
                self.parser.process(frame(**{"rtps.sm.wrEntityId": entity}))
                self.assertEqual(classify_packet(entity, "0x15"), "unknown")
                counters = self.accumulator.drain()[200]
                self.assertEqual(counters["unknown_tx_bytes"], 120)
                self.assertEqual(counters["discovery_tx_bytes"], 0)

    def test_icmp_quoted_udp_is_not_counted_as_transmitted_rtps(self):
        self.parser.process(frame(**{
            "ip.proto": "1,17", "ip.src": "10.0.0.1,10.0.0.2",
            "ip.frag_offset": "0,0", "ip.flags.mf": "0,0",
        }))
        self.assertEqual(self.accumulator.drain(), {})

    def test_guid_scoped_topics_and_exclusive_conservation(self):
        self.parser.process(frame(**{
            "rtps.sm.wrEntityId": "000003c2", "rtps.param.endpoint_guid": PREFIX + WRITER,
            "rtps.param.topicName": "RouterHealth",
        }))
        self.parser.process(frame())
        self.parser.process(frame(**{"rtps.guidPrefix.src": "111111111111111111111111"}))
        self.parser.process(frame(**{"rtps.sm.id": "0x1c"}))
        counters = self.accumulator.drain()[200]
        self.assertEqual(counters["infrastructure_tx_packets"], 1)
        self.assertEqual(counters["unknown_tx_packets"], 1)
        self.assertEqual(counters["reliability_tx_packets"], 1)
        self.assertEqual(counters["discovery_tx_packets"], 1)
        for unit in ("bytes", "packets"):
            self.assertEqual(counters[f"tx_{unit}"],
                             sum(counters[f"{kind}_tx_{unit}"] for kind in TX_CLASSES))

    def test_fragment_reassembly_counts_all_physical_frames(self):
        payload = (b"\x00\x00" + (57400).to_bytes(2, "big") + b"\x00" * 4 + b"RTPS").hex()
        for offset, size, final in [(0, 1514, False), (1480, 1514, False), (2960, 398, True)]:
            self.parser.process(frame(**{
                "frame.len": str(size), "ip.frag_offset": str(offset),
                "ip.flags.mf": "0" if final else "1",
                "rtps.guidPrefix.src": PREFIX if final else "",
                "udp.dstport": "57400" if final else "",
                "data.data": payload if offset == 0 else "",
            }))
        counters = self.accumulator.drain()[200]
        self.assertEqual(counters["tx_bytes"], 3426)
        self.assertEqual(counters["tx_packets"], 3)
        self.assertEqual(counters["unknown_tx_bytes"], 3426)
        self.assertFalse(self.parser.fragments)

    def test_missing_and_orphan_fragments_are_explicit(self):
        payload = (b"\x00\x00" + (57400).to_bytes(2, "big") + b"\x00" * 4 + b"RTPS").hex()
        self.parser.process(frame(**{
            "ip.flags.mf": "1", "rtps.guidPrefix.src": "", "udp.dstport": "", "data.data": payload,
        }))
        self.parser.process(frame(**{
            "ip.id": "0x5678", "ip.frag_offset": "1480",
            "rtps.guidPrefix.src": "", "udp.dstport": "",
        }))
        self.parser.expire(106)
        counters = self.accumulator.drain()[200]
        self.assertEqual(counters["incomplete_fragment_packets"], 1)
        self.assertEqual(counters["unknown_tx_packets"], 1)
        self.assertEqual(self.accumulator.orphan_fragment_packets, 1)

    def test_reordered_and_duplicate_fragments_keep_physical_counts(self):
        for fragments in (
            [(1480, 1514), (2960, 398), (0, 1514)],
            [(0, 1514), (0, 1514), (1480, 1514), (2960, 398)],
        ):
            with self.subTest(fragments=fragments):
                accumulator = TrafficAccumulator()
                parser = FrameAccounting([200], {"10.0.0.1"}, accumulator)
                for index, (offset, size) in enumerate(fragments):
                    reconstructed = index == len(fragments) - 1
                    parser.process(frame(**{
                        "frame.len": str(size), "ip.frag_offset": str(offset),
                        "ip.flags.mf": "0" if offset == 2960 else "1",
                        "rtps.guidPrefix.src": PREFIX if reconstructed else "",
                        "udp.dstport": "57400" if reconstructed else "",
                    }))
                counters = accumulator.drain()[200]
                self.assertEqual(counters["tx_packets"], len(fragments))
                self.assertEqual(counters["tx_bytes"], sum(size for _, size in fragments))

    def test_bad_or_missing_metadata_does_not_discard_frame(self):
        for metadata in (
            {"rtps.param.endpoint_guid": "bad", "rtps.param.topicName": "RouterHealth"},
            {"rtps.param.endpoint_guid": PREFIX + WRITER, "rtps.param.topicName": ""},
            {"rtps.param.endpoint_guid": PREFIX + WRITER, "rtps.param.topicName": "one,two"},
        ):
            self.parser.process(frame(**metadata))
        self.assertEqual(self.accumulator.drain()[200]["tx_packets"], 3)

    def test_topic_cache_supports_warm_monitor_restart(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "topics.json"
            self.parser.topic_cache = path
            self.parser.process(frame(**{
                "frame.time_epoch": str(__import__("time").time()),
                "rtps.param.endpoint_guid": PREFIX + WRITER,
                "rtps.param.topicName": "RouterLinkStats",
            }))
            self.parser.save_topics()
            restored = FrameAccounting([200], {"10.0.0.1"}, self.accumulator, path)
            restored.process(frame())
            self.assertEqual(self.accumulator.drain()[200]["application_tx_packets"], 2)
            self.assertEqual(len(json.loads(path.read_text())), 1)

    def test_domain_limits_and_fragment_filter(self):
        self.assertIn("ip[6:2]", build_port_filter([200]))
        self.assertIn("65535", build_port_filter([232]))
        with self.assertRaises(ValueError):
            build_port_filter([233])

    def test_invalid_topic_cache_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "topics.json"
            for value in ([], {"writer": {"topic": "RouterHealth", "seen_at": None}}):
                path.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError, "Invalid topic cache"):
                    FrameAccounting([200], {"10.0.0.1"}, self.accumulator, path)

    def test_capture_time_windows_do_not_move_a_delayed_burst(self):
        accumulator = TrafficAccumulator(start_ms=100000)
        parser = FrameAccounting([200], {"10.0.0.1"}, accumulator)
        with patch("traffic_accounting.time.time", return_value=102.8):
            for timestamp in ("100.010", "100.990", "101.010"):
                parser.process(frame(**{"frame.time_epoch": timestamp}))
        self.assertEqual(accumulator.drain_ready(102999), [])
        first = accumulator.drain_ready(103000)
        self.assertEqual(len(first), 1)
        counters, metadata = first[0]
        self.assertEqual(counters[200]["tx_bytes"], 240)
        self.assertEqual(metadata["capture_timestamp"], 101000)
        self.assertEqual(metadata["capture_window_start_ms"], 100000)
        self.assertEqual(metadata["interval_ms"], 1000)
        self.assertEqual(metadata["reported_at_ms"], 103000)
        second = accumulator.drain_ready(104000)
        self.assertEqual(second[0][0][200]["tx_bytes"], 120)

    def test_fragment_frames_keep_their_own_windows_and_delay_finalization(self):
        accumulator = TrafficAccumulator(start_ms=100000)
        parser = FrameAccounting([200], {"10.0.0.1"}, accumulator)
        payload = (b"\x00\x00" + (57400).to_bytes(2, "big") + b"\x00" * 4 + b"RTPS").hex()
        parser.process(frame(**{
            "frame.time_epoch": "100.9", "frame.len": "1514", "ip.flags.mf": "1",
            "rtps.guidPrefix.src": "", "udp.dstport": "", "data.data": payload,
        }))
        self.assertEqual(accumulator.drain_ready(103000), [])
        parser.process(frame(**{
            "frame.time_epoch": "101.1", "frame.len": "398", "ip.frag_offset": "1480",
        }))
        first = accumulator.drain_ready(103000)
        second = accumulator.drain_ready(104000)
        self.assertEqual(first[0][0][200]["tx_bytes"], 1514)
        self.assertEqual(second[0][0][200]["tx_bytes"], 398)
        self.assertFalse(accumulator.pending_fragments)

    def test_late_frames_invalidate_rates_instead_of_polluting_the_next_window(self):
        accumulator = TrafficAccumulator(start_ms=100000)
        parser = FrameAccounting([200], {"10.0.0.1"}, accumulator)
        accumulator.drain_ready(103000)
        parser.process(frame(**{"frame.time_epoch": "100.99"}))
        counters, metadata = accumulator.drain_ready(104000)[0]
        self.assertEqual(counters, {})
        self.assertFalse(metadata["capture_timing_valid"])
        self.assertEqual(metadata["late_capture_packets_interval"], 1)
        self.assertTrue(accumulator.drain_ready(105000)[0][1]["capture_timing_valid"])

    def test_restart_marks_every_intersected_window_and_then_recovers(self):
        accumulator = TrafficAccumulator(start_ms=100000)
        accumulator.set_capture_status("live", 100100)
        accumulator.set_capture_status("restarting", 101500)
        accumulator.set_capture_status("live", 102200)
        windows = accumulator.drain_ready(106000)
        self.assertEqual([metadata["capture_gap"] for _, metadata in windows],
                         [True, True, True, False])

    def test_invalid_decoded_row_marks_its_capture_window_not_an_earlier_report(self):
        accumulator = TrafficAccumulator(start_ms=100000)
        accumulator.note_invalid_row(101500)
        self.assertEqual(accumulator.drain_ready(103000)[0][1]["capture_invalid_rows_interval"], 0)
        self.assertEqual(accumulator.drain_ready(104000)[0][1]["capture_invalid_rows_interval"], 1)
        self.assertEqual(accumulator.drain_ready(105000)[0][1]["capture_invalid_rows_interval"], 0)

    def test_mac_sampler_uses_own_interval_and_rebaselines_reset(self):
        with patch("domain_traffic_monitor.read_emane_mac_counters",
                   side_effect=[{"count": 5}, {"count": 8}, {"count": 2}, {"count": 4}]), \
             patch("domain_traffic_monitor.time.monotonic",
                   side_effect=[0, .2, 1, 1.2, 2, 2.2, 3, 3.2]):
            sampler = EmaneSampler(1, "node")
            self.assertIsNone(sampler.poll())
            sample = sampler.poll()
            self.assertEqual(sample["count"], 3)
            self.assertAlmostEqual(sample["interval_ms"], 1000)
            self.assertIsNone(sampler.poll())
            self.assertEqual(sampler.poll()["count"], 2)


class LiveCaptureTests(unittest.TestCase):
    header = struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 262144, 1)

    @staticmethod
    def record(line):
        payload = line.encode()
        return struct.pack("<IIII", 100, 0, len(payload), len(payload)) + payload

    @staticmethod
    def decode(command, **kwargs):
        data = kwargs["input"][24:]
        rows = []
        while data:
            length = struct.unpack_from("<I", data, 8)[0]
            rows.append(data[16:16 + length] + f'|"{len(rows) + 1}"\n'.encode())
            data = data[16 + length:]
        return Mock(returncode=0, stdout=b"".join(rows), stderr=b"")

    def run_capture(self, chunks, stop_on_eof=True, decoder_error=None, exit_code=0):
        self.stop = threading.Event()
        self.accumulator = TrafficAccumulator(start_ms=100000)
        self.capture = Mock(stdout=Mock(), poll=Mock(return_value=None),
                            wait=Mock(return_value=0))
        self.commands = []
        self.error_files = []
        chunks = iter(chunks)

        def spawn(command, **kwargs):
            self.commands.append(command)
            self.error_files.append(kwargs["stderr"])
            if len(self.commands) > 1:
                raise AssertionError("Capture restarted")
            kwargs["stderr"].write("Capturing on 'test'\n")
            kwargs["stderr"].flush()
            return self.capture

        def decode(command, **kwargs):
            if decoder_error:
                raise decoder_error
            result = self.decode(command, **kwargs)
            result.returncode = exit_code
            return result

        def read(fd, size):
            chunk = next(chunks)
            if not chunk and stop_on_eof:
                self.stop.set()
            return chunk

        with tempfile.TemporaryDirectory() as work, \
             patch("domain_traffic_monitor.time.time", return_value=100.1), \
             patch("domain_traffic_monitor.subprocess.Popen", side_effect=spawn), \
             patch("domain_traffic_monitor.subprocess.run", side_effect=decode) as runner, \
             patch("domain_traffic_monitor.CAPTURE_BATCH_PACKETS", 1), \
             patch("domain_traffic_monitor.select.select",
                   return_value=([self.capture.stdout], [], [])), \
             patch("domain_traffic_monitor.os.read", side_effect=read):
            _start_tshark_live("test", "udp portrange 57400-57649",
                               self.accumulator, self.stop, {"10.0.0.1"}, [200],
                               Path(work) / "topics.json", Path(work) / "ready")
            self.decoder_calls = runner.call_args_list

    def test_continuous_pipe_preserves_rows_past_multiple_former_rotation_periods(self):
        rows = [self.record(frame(**{"frame.time_epoch": timestamp}))
                for timestamp in ("100.1", "401.1", "702.1")]
        self.run_capture([self.header[:10], self.header[10:] + rows[0][:20],
                          rows[0][20:] + rows[1], rows[2], b""])
        self.assertEqual([command[0] for command in self.commands], ["dumpcap"])
        self.assertEqual(self.commands[0][-2:], ["-w", "-"])
        self.assertNotIn("-a", self.commands[0])
        self.assertNotIn("-b", self.commands[0])
        self.assertIn("-P", self.commands[0])
        self.assertEqual(len(self.decoder_calls), 3)
        for call in self.decoder_calls:
            self.assertEqual(call.args[0][:3], ["tshark", "-r", "-"])
            self.assertEqual(call.kwargs["timeout"], 5)
        self.assertEqual(self.accumulator.capture_restarts, 0)
        self.assertEqual(self.accumulator.capture_status, "live")
        self.assertEqual(self.accumulator.drain()[200]["tx_bytes"], 360)
        self.capture.terminate.assert_called_once()
        self.capture.wait.assert_called_once()
        self.capture.stdout.close.assert_called_once()
        self.assertTrue(all(file.closed for file in self.error_files))

    def test_unexpected_clean_eof_is_an_explicit_failure_not_a_silent_restart(self):
        with self.assertRaisesRegex(RuntimeError, "dumpcap exited unexpectedly"):
            self.run_capture([b""], stop_on_eof=False)
        self.capture.terminate.assert_called_once()
        self.assertTrue(all(file.closed for file in self.error_files))

    def test_decoder_failure_surfaces_status_and_cleans_up_capture(self):
        with self.assertRaisesRegex(RuntimeError, "tshark batch failed.*2"):
            self.run_capture([self.header + self.record(frame())], exit_code=2)
        self.capture.terminate.assert_called_once()
        self.assertTrue(all(file.closed for file in self.error_files))

    def test_decoder_start_failure_still_reaps_capture(self):
        with self.assertRaisesRegex(OSError, "missing decoder"):
            self.run_capture([self.header + self.record(frame())],
                             decoder_error=OSError("missing decoder"))
        self.capture.terminate.assert_called_once()
        self.capture.wait.assert_called_once()
        self.capture.stdout.close.assert_called()
        self.assertTrue(all(file.closed for file in self.error_files))

    def test_invalid_capture_header_and_oversized_records_fail_explicitly(self):
        for data in (b"x" * 24, self.header + struct.pack("<IIII", 100, 0, 262145, 262145)):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    self.run_capture([data])
                self.capture.terminate.assert_called_once()


class CaptureBatchTests(unittest.TestCase):
    def setUp(self):
        self.accumulator = TrafficAccumulator()
        self.accounting = FrameAccounting([200], {"10.0.0.1"}, self.accumulator)
        self.batches = CaptureBatches(LiveCaptureTests.header, self.accounting)

    def test_ip_fragments_replay_without_double_counting_across_decoder_batches(self):
        first = frame(**{
            "frame.time_epoch": "100.1", "frame.len": "1514", "ip.flags.mf": "1",
            "rtps.guidPrefix.src": "", "udp.dstport": "",
            "data.data": (b"\0\0" + (57400).to_bytes(2, "big") + b"\0" * 4 + b"RTPS").hex(),
        })
        last = frame(**{
            "frame.time_epoch": "101.1", "frame.len": "398", "ip.frag_offset": "1480",
        })
        with patch("domain_traffic_monitor.time.time", return_value=101.2), \
             patch("domain_traffic_monitor.subprocess.run",
                   side_effect=LiveCaptureTests.decode) as runner:
            self.batches.decode([(0, LiveCaptureTests.record(first))])
            self.assertEqual(len(self.batches.replay()), 1)
            self.batches.decode([(1, LiveCaptureTests.record(last))])
        self.assertIn(LiveCaptureTests.record(first), runner.call_args.kwargs["input"])
        self.assertFalse(self.batches.replay())
        counts = self.accumulator.drain()[200]
        self.assertEqual(counts["tx_packets"], 2)
        self.assertEqual(counts["tx_bytes"], 1912)
        self.assertEqual(counts["incomplete_fragment_packets"], 0)

    def test_fragmented_discovery_replays_until_complete(self):
        from test_wire_delivery import message, metadata
        payload = metadata(topic="RouterHealth")
        size = len(payload)
        with patch("domain_traffic_monitor.time.time", return_value=100.2), \
             patch("domain_traffic_monitor.subprocess.run", side_effect=LiveCaptureTests.decode):
            for number in (1, 2):
                fragment_size = (size + 1) // 2
                part = payload[(number - 1) * fragment_size:number * fragment_size]
                line = frame(**{
                    "rtps.sm.wrEntityId": "000003c2", "rtps.sm.id": "0x16",
                    "udp.payload": message(payload=part, writer=bytes.fromhex("000003c2"),
                                           fragment=(number, 1, fragment_size, size)).hex(),
                })
                self.batches.decode([(number, LiveCaptureTests.record(line))])
                self.assertEqual(bool(self.batches.replay()), number == 1)
        self.assertEqual(self.accumulator.drain()[200]["tx_packets"], 2)

    def test_replay_budget_and_expiry_are_explicit(self):
        self.batches.discovery_fragments["sample"] = {
            "at": 100, "records": {0: b"xx"}, "parts": {1}}
        with patch("domain_traffic_monitor.CAPTURE_REPLAY_BYTES", 1):
            with self.assertRaisesRegex(RuntimeError, "replay memory limit"):
                self.batches.replay()
        with patch("domain_traffic_monitor.time.time", return_value=161):
            self.batches.prune()
        self.assertFalse(self.batches.replay())

    def test_missing_decoded_frames_fail_instead_of_reporting_success(self):
        with patch("domain_traffic_monitor.subprocess.run",
                   return_value=Mock(returncode=0, stdout=b"", stderr=b"")):
            with self.assertRaisesRegex(RuntimeError, "every captured frame"):
                self.batches.decode([(0, LiveCaptureTests.record(frame()))])


@unittest.skipUnless(shutil.which("tshark"), "tshark is required for offline decoder verification")
class NativeCaptureBatchTests(unittest.TestCase):
    @staticmethod
    def packet(payload, timestamp, *, offset=0, more=False, ident=1):
        ip = struct.pack(
            "!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), ident,
            (0x2000 if more else 0) | (offset // 8), 64, 17, 0,
            b"\x0a\0\0\1", b"\x0a\0\0\2")
        packet = b"\0" * 12 + b"\x08\0" + ip + payload
        return struct.pack("<IIII", timestamp, 0, len(packet), len(packet)) + packet

    @staticmethod
    def udp(payload):
        return struct.pack("!HHHH", 57411, 57411, len(payload) + 8, 0) + payload

    def test_reordered_duplicate_ip_fragments_span_real_decoder_sessions(self):
        from test_wire_delivery import message
        for order in ((0, 1), (1, 0), (0, 0, 1)):
            with self.subTest(order=order):
                accumulator = TrafficAccumulator()
                accounting = FrameAccounting([200], {"10.0.0.1"}, accumulator)
                batches = CaptureBatches(LiveCaptureTests.header, accounting)
                raw = self.udp(message(payload=b"\0\1\0\0" + b"x" * 600))
                now = int(time.time())
                fragments = (
                    self.packet(raw[:400], now, more=True),
                    self.packet(raw[400:], now, offset=400))
                for number, index in enumerate(order):
                    batches.decode([(number, fragments[index])])
                counts = accumulator.drain()[200]
                self.assertEqual(counts["tx_packets"], len(order))
                self.assertEqual(counts["tx_bytes"], sum(len(fragments[i]) - 16 for i in order))
                self.assertEqual(counts["incomplete_fragment_packets"], 0)
                self.assertFalse(batches.replay())

    def test_fragmented_sedp_keeps_topic_learning_across_real_decoder_sessions(self):
        from test_wire_delivery import message, metadata, GUID
        accumulator = TrafficAccumulator()
        accounting = FrameAccounting([200], {"10.0.0.1"}, accumulator)
        batches = CaptureBatches(LiveCaptureTests.header, accounting)
        payload = metadata(topic="RouterHealth")
        fragment_size = (len(payload) + 1) // 2
        for index in (0, 1):
            sample = message(
                writer=bytes.fromhex("000003c2"),
                payload=payload[index * fragment_size:(index + 1) * fragment_size],
                fragment=(index + 1, 1, fragment_size, len(payload)))
            batches.decode([(index, self.packet(self.udp(sample), int(time.time()), ident=index))])
        self.assertEqual(accounting.topics["200:" + GUID]["topic"], "RouterHealth")
        self.assertEqual(accumulator.drain()[200]["tx_packets"], 2)
        self.assertFalse(batches.replay())


class AggregateTests(unittest.TestCase):
    def capture_sample(self, observer, end=10000):
        sample = self.sample(observer, 1000)
        sample.update(schema_version=3, capture_timestamp=end,
                      capture_window_start_ms=end - 1000, reported_at_ms=end + 2000,
                      timestamp_basis="capture", capture_timing_valid=True,
                      capture_invalid_rows_interval=0, late_capture_packets_interval=0,
                      orphan_fragment_packets_interval=0)
        return sample

    def test_capture_time_metadata_is_validated_and_report_time_controls_freshness(self):
        sample = self.capture_sample("a")
        validate_sample(sample)
        self.assertTrue(aggregate({"a": sample}, ["a"], now_ms=16000)["complete"])
        for field, value in (("capture_window_start_ms", 8000), ("timestamp_basis", "processing"),
                             ("reported_at_ms", 9999), ("capture_timing_valid", "true"),
                             ("late_capture_packets_interval", -1)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_sample(dict(sample, **{field: value}))

    def test_network_rates_use_the_latest_shared_capture_window(self):
        a = self.capture_sample("a")
        b = self.capture_sample("b")
        newer = self.capture_sample("a", end=11000)
        newer["application_tx_bytes"] = 9000
        samples = {"a": newer, "b": b}
        unaligned = aggregate(samples, ["a", "b"], now_ms=13000)
        self.assertFalse(unaligned["complete"])
        aligned = coherent_samples(samples, ["a", "b"], {"a": [a, newer], "b": [b]}, now_ms=13000)
        result = aggregate(aligned, ["a", "b"], now_ms=13000)
        self.assertTrue(result["complete"])
        self.assertEqual(result["capture_timestamp"], 10000)
        self.assertEqual(result["application_tx_bytes_per_second"], 2000)

    def test_late_capture_rows_are_not_healthy_and_orphan_errors_can_recover(self):
        sample = self.capture_sample("a")
        sample["capture_timing_valid"] = False
        self.assertFalse(aggregate({"a": sample}, ["a"], now_ms=13000)["capture_complete"])
        sample.update(capture_timing_valid=True, orphan_fragment_packets=5,
                      orphan_fragment_packets_interval=0)
        self.assertTrue(aggregate({"a": sample}, ["a"], now_ms=13000)["capture_complete"])

    def sample(self, observer, interval, count=1000, mac=False):
        sample = dict.fromkeys(counter_fields(mac), 0)
        sample.update(observer=observer, interval_ms=interval, capture_timestamp=10000,
                      domain_id=200, schema_version=2, capture_status="live")
        sample["mac_tx_unicast_bytes" if mac else "application_tx_bytes"] = count
        return sample

    def test_sum_of_individually_normalized_rates(self):
        for mac in (False, True):
            samples = {node: self.sample(node, interval, mac=mac)
                       for node, interval in (("a", 1000), ("b", 2000))}
            result = aggregate(samples, ["a", "b"], mac, now_ms=10000)
            field = "mac_tx_bytes" if mac else "application_tx_bytes"
            self.assertEqual(result[field], 2000)
            self.assertEqual(result[f"{field}_per_second"], 1500)
            self.assertTrue(result["complete"])

    def test_missing_and_stale_sources_are_not_healthy_zero(self):
        sample = self.sample("a", 1000)
        result = aggregate({"a": sample}, ["a", "b"], now_ms=10000)
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing_contributors"], ["b"])
        result = aggregate({"a": sample}, ["a", "b"], now_ms=16000)
        self.assertTrue(result["stale"])
        self.assertEqual(result["contributors"], [])

    def test_delayed_contributor_advances_aggregate_order_not_source_timestamp(self):
        samples = {"a": self.sample("a", 1000), "b": self.sample("b", 1000)}
        samples["a"]["capture_timestamp"] = 9000
        before = aggregate(samples, ["a", "b"], now_ms=10000)
        samples["a"].update(capture_timestamp=9900, application_tx_bytes=9900)
        after = aggregate(samples, ["a", "b"], now_ms=10001)
        self.assertEqual(before["capture_timestamp"], after["capture_timestamp"])
        self.assertLess(before["aggregated_at_ms"], after["aggregated_at_ms"])
        self.assertEqual(after["application_tx_bytes_per_second"], 10900)
        expired = aggregate(samples, ["a", "b"], now_ms=15001)
        self.assertGreater(expired["aggregated_at_ms"], after["aggregated_at_ms"])
        self.assertTrue(expired["stale"])

    def test_invalid_inputs_are_rejected(self):
        for field, value in (("interval_ms", 0), ("interval_ms", float("nan")),
                             ("application_tx_bytes", -1), ("domain_id", 233),
                             ("total_bytes", True), ("capture_invalid_rows", -1),
                             ("capture_invalid_rows", "1"), ("capture_invalid_rows", True),
                             ("capture_restarts", -1)):
            sample = self.sample("a", 1000)
            sample[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_sample(sample)

    def test_capture_gaps_invalid_rows_and_unknown_roles_are_not_complete(self):
        for field in ("capture_gap", "capture_invalid_rows", "orphan_fragment_packets",
                      "incomplete_fragment_packets"):
            sample = self.sample("a", 1000)
            sample[field] = 1
            with self.subTest(field=field):
                self.assertFalse(aggregate({"a": sample}, ["a"], now_ms=10000)["capture_complete"])
        sample = self.sample("a", 1000)
        sample["unknown_tx_bytes"] = 100
        self.assertFalse(aggregate({"a": sample}, ["a"], now_ms=10000)["classification_complete"])

    def test_capture_health_uses_interval_errors_not_the_lifetime_total(self):
        sample = self.sample("a", 1000)
        sample.update(capture_invalid_rows=7, capture_invalid_rows_interval=0)
        self.assertTrue(aggregate({"a": sample}, ["a"], now_ms=10000)["capture_complete"])
        sample["capture_invalid_rows_interval"] = 1
        self.assertFalse(aggregate({"a": sample}, ["a"], now_ms=10000)["capture_complete"])
        self.assertEqual(sample["capture_invalid_rows"], 7)


class DiagnosticsCacheTests(unittest.TestCase):
    def setUp(self):
        try:
            from mesh_bridge import DdsBridge
        except ModuleNotFoundError as error:
            if error.name in ("rti", "rti.connextdds"):
                self.skipTest("Connext binding required for bridge module (no DDS entities created)")
            raise
        self.bridge = DdsBridge.__new__(DdsBridge)
        self.bridge.cache_lock = threading.Lock()
        self.router = "Platform_30/router"
        self.summary = {"router": self.router, "config_hash": "current", "state_revision": 5,
                        "routes": [{"route_name": "platform_debug_status", "state": "ROUTE_ENABLED"}]}
        self.bridge.cache = {"Control_20": {"peers": [{"health": self.summary}]}}
        self.bridge.router_diagnostics_cache = {}
        self.bridge.platform_status_modes = {}
        self.bridge.platform_status_mode_changed_ns = {}
        self.bridge._broadcast = lambda payload: None
        self.sample = {"router": self.router, "config_hash": "current", "state_revision": 5,
                       "snapshot_seq": 10, "send_timestamp": time.time_ns(), "routes": []}
        self.bridge.record_router_diagnostics(self.sample)

    def state(self):
        return self.bridge.router_diagnostics_snapshot()["rows"][0]["state"]

    def test_debug_exit_reentry_cannot_reuse_old_snapshot(self):
        self.assertEqual(self.state(), "live")
        self.bridge.platform_status_modes["Platform_30"] = "mission"
        self.summary["routes"][0]["state"] = "ROUTE_DISABLED"
        self.assertEqual(self.state(), "debug off / historical")
        self.bridge.platform_status_modes["Platform_30"] = "debug"
        self.summary["routes"][0]["state"] = "ROUTE_ENABLED"
        cutoff = time.time_ns()
        self.bridge.platform_status_mode_changed_ns["Platform_30"] = cutoff
        self.assertEqual(self.state(), "awaiting fresh debug snapshot")
        self.bridge.record_router_diagnostics(dict(self.sample, send_timestamp=cutoff + 1, snapshot_seq=11))
        self.assertEqual(self.state(), "live")

    def test_reported_debug_route_overrides_previous_ui_request(self):
        self.bridge.platform_status_modes["Platform_30"] = "mission"
        self.assertEqual(self.state(), "live")
        self.bridge.platform_status_modes["Platform_30"] = "debug"
        self.summary["routes"][0]["state"] = "ROUTE_DISABLED"
        self.assertEqual(self.state(), "debug off / historical")
        self.bridge.cache.clear()
        self.assertEqual(self.state(), "awaiting health")

    def test_wrong_config_revision_stale_and_out_of_order(self):
        self.bridge.record_router_diagnostics(dict(self.sample, send_timestamp=1, config_hash="old"))
        self.assertEqual(self.state(), "live")
        self.summary["config_hash"] = "new"
        self.assertEqual(self.state(), "configuration mismatch")
        self.summary["config_hash"] = "current"
        self.summary["state_revision"] = 6
        self.assertEqual(self.state(), "awaiting matching health revision")
        self.summary["state_revision"] = 5
        self.bridge.router_diagnostics_cache[self.router]["received_at"] = int(time.time() * 1000) - 6000
        self.assertEqual(self.state(), "stale")


if __name__ == "__main__":
    unittest.main()
