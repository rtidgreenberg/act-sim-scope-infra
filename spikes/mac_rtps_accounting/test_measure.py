import json
import struct
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from measure import cleanup_captures, compare_monitor_intervals, count_frames, decode_frame, frames, layer_delta, model_headers, monitor_records, parse_drop_tables, parse_stats, pcap_records


LOCAL = bytes.fromhex("020200000002")


def packet(fragment=0, identifier=7, payload=b"RTPSabcd", port=57410):
    udp = struct.pack("!HHHH", 12000, port, len(payload) + 8, 0) + payload
    if fragment & 0x1fff:
        udp = payload
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), identifier,
                     fragment, 64, 17, 0, b"\x0a\x58\x00\x02", b"\x0a\x58\x00\x01")
    return bytes.fromhex("020200000001") + LOCAL + b"\x08\x00" + ip + udp


class DecoderTests(unittest.TestCase):
    def write_pcap(self, path, records, endian="<", nanoseconds=False):
        magic = 0xa1b23c4d if nanoseconds else 0xa1b2c3d4
        raw = struct.pack(endian + "IHHIIII", magic, 2, 4, 0, 0, 65535, 1)
        for timestamp, body in records:
            raw += struct.pack(endian + "IIII", 1, timestamp, len(body), len(body)) + body
        path.write_bytes(raw)

    def test_pcap_byte_orders_and_timestamp_resolutions(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.pcap"
            for endian in ("<", ">"):
                for nano in (False, True):
                    with self.subTest(endian=endian, nano=nano):
                        self.write_pcap(path, [(12, packet())], endian, nano)
                        self.assertEqual(list(pcap_records(path)),
                                         [(1_000_000_000 + 12 * (1 if nano else 1000), packet())])

    def test_independent_rtps_and_non_rtps_identification(self):
        self.assertEqual(decode_frame(packet())["kind"], "rtps")
        self.assertEqual(decode_frame(packet())["domain"], 200)
        self.assertEqual(decode_frame(packet(payload=b"other"))["kind"], "non_rtps")
        self.assertEqual(decode_frame(packet(port=14900))["domain"], 30)
        self.assertEqual(decode_frame(packet(payload=b"RTPXabcd", port=7400))["kind"], "rtpx")

    def test_other_domains_and_non_dds_traffic_are_separate(self):
        decoded = [(10, decode_frame(packet(port=14900))),
                   (11, decode_frame(packet(payload=b"RTPXabcd", port=7400)))]
        totals, _ = count_frames(decoded, "different", 0, 20)
        self.assertEqual(totals["other_rtps_rx_packets"], 1)
        self.assertEqual(totals["rtps_domain_30_rx_packets"], 1)
        self.assertEqual(totals["rtpx_rx_packets"], 1)
        self.assertNotIn("wan_rtps_rx_packets", totals)

    def test_fragments_are_counted_as_physical_frames_in_both_directions(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.pcap"
            first = packet(fragment=0x2000)
            second = packet(fragment=2, payload=b"fragment")
            self.write_pcap(path, [(100, second), (200, first)])
            totals, _ = count_frames(frames(path), LOCAL.hex(), 0, 2_000_000_000)
            self.assertEqual(totals["wan_rtps_tx_packets"], 2)
            self.assertEqual(totals["wan_rtps_tx_bytes"], len(first) + len(second))
            rx, _ = count_frames(frames(path), "different", 0, 2_000_000_000)
            self.assertEqual(rx["wan_rtps_rx_packets"], 2)

    def test_boundary_frames_are_excluded_and_truncation_is_an_error(self):
        decoded = [(10, decode_frame(packet())), (20, decode_frame(packet()))]
        totals, _ = count_frames(decoded, LOCAL.hex(), 10, 20)
        self.assertEqual(totals["tx_packets"], 1)
        with self.assertRaises(ValueError):
            decode_frame(packet()[:-1])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.pcap"
            self.write_pcap(path, [(1, packet())])
            path.write_bytes(path.read_bytes()[:-1])
            with self.assertRaises(ValueError):
                list(pcap_records(path))

    def test_counter_delta_rejects_resets_and_ignores_averages(self):
        text = "nem 2 mac numUpstreamBytesUnicastRx0 = 123\nnem 2 mac avgDelay = 0\n"
        self.assertEqual(parse_stats(text), {"mac": {"numUpstreamBytesUnicastRx0": 123}})
        self.assertEqual(layer_delta({"mac": {"num": 1}}, {"mac": {"num": 3}}), {"mac": {"num": 2}})
        with self.assertRaises(ValueError):
            layer_delta({"mac": {"num": 3}}, {"mac": {"num": 1}})

    def test_actual_layer_headers_are_derived_without_a_fixed_63_byte_assumption(self):
        delta = {}
        for layer, size in (("mac", 6), ("shim0", 59)):
            delta[layer] = {}
            for kind in ("Unicast", "Broadcast"):
                for way in ("Rx", "Tx"):
                    delta[layer][f"numDownstreamPackets{kind}{way}0"] = 10
                    delta[layer][f"numDownstreamBytes{kind}{way}0"] = 1000 + (size * 10 if way == "Tx" else 0)
        self.assertEqual(model_headers(delta), {"mac": 6, "shim0": 59, "total": 65})
        delta["mac"]["numDownstreamPacketsUnicastTx0"] -= 1
        with self.assertRaises(ValueError):
            model_headers(delta)

    def test_destination_and_effect_drop_columns_are_not_conflated(self):
        text = """nem 2 mac UnicastPacketDropTable0
| NEM | SINR | Dst MAC |
| 1   | 0    | 42      |

nem 2 shim0 UnicastPacketDropTable0
| NEM | Effect | No Profile |
| 1   | 3      | 0          |
"""
        parsed = parse_drop_tables(text)
        self.assertEqual(parsed["mac UnicastPacketDropTable0"]["1"]["Dst MAC"], 42)
        self.assertEqual(parsed["shim0 UnicastPacketDropTable0"]["1"]["Effect"], 3)


class MeasurementTests(unittest.TestCase):
    def test_partial_start_record_is_excluded_but_complete_records_are_parsed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "monitor.jsonl"
            first = json.dumps({"samples": [1]}).encode() + b"\n"
            second = json.dumps({"samples": [2]}).encode() + b"\n"
            for offset in (0, len(first), len(first) // 2):
                with self.subTest(offset=offset):
                    path.write_bytes(first[:offset])
                    with path.open("ab") as output:
                        output.write(first[offset:] + second + b'{"unfinished":')
                    expected = [{"samples": [1]}, {"samples": [2]}] if offset == 0 else [{"samples": [2]}]
                    self.assertEqual(monitor_records(path, offset), expected)
            path.write_bytes(b"invalid complete record\n")
            with self.assertRaises(json.JSONDecodeError):
                monitor_records(path, 0)

    def test_shifted_burst_passes_aggregate_but_fails_individual_intervals(self):
        frame = decode_frame(packet())
        decoded = [(500_000_000, frame)] * 10
        intervals = [
            dict(capture_timestamp=1000, interval_ms=1000,
                 tx_packets=0, tx_bytes=0, rx_packets=0, rx_bytes=0),
            dict(capture_timestamp=2000, interval_ms=1000,
                 tx_packets=10, tx_bytes=frame["bytes"] * 10, rx_packets=0, rx_bytes=0),
        ]
        aggregate, individual = compare_monitor_intervals(decoded, LOCAL.hex(), intervals)
        self.assertTrue(all(check["passed"] for check in aggregate))
        failed = [check for check in individual if not check["passed"]]
        self.assertEqual([check["name"] for check in failed],
                         ["Production WAN tx_packets", "Production WAN tx_packets"])
        self.assertEqual([(check["start_ns"], check["end_ns"]) for check in failed],
                         [(0, 1_000_000_000), (1_000_000_000, 2_000_000_000)])

    def test_matching_intervals_and_exact_packet_tolerance_pass(self):
        frame = decode_frame(packet())
        sample = dict(capture_timestamp=1000, interval_ms=1000,
                      tx_packets=12, tx_bytes=frame["bytes"] * 10, rx_packets=0, rx_bytes=0)
        aggregate, individual = compare_monitor_intervals(
            [(500_000_000, frame)] * 10, LOCAL.hex(), [sample])
        self.assertTrue(all(check["passed"] for check in aggregate + individual))
        sample["tx_packets"] = 13
        aggregate, individual = compare_monitor_intervals(
            [(500_000_000, frame)] * 10, LOCAL.hex(), [sample])
        self.assertFalse(aggregate[0]["passed"])
        self.assertFalse(individual[0]["passed"])

    def test_interval_byte_tolerance_is_enforced(self):
        sample = dict(capture_timestamp=1000, interval_ms=1000,
                      tx_packets=0, tx_bytes=3000, rx_packets=0, rx_bytes=0)
        _, individual = compare_monitor_intervals([], LOCAL.hex(), [sample])
        self.assertTrue(individual[1]["passed"])
        sample["tx_bytes"] = 3001
        _, individual = compare_monitor_intervals([], LOCAL.hex(), [sample])
        self.assertFalse(individual[1]["passed"])

    def captures(self):
        nodes = [dict(name=name, container=name, work="/tmp/owned-" + name, capture_pid=index)
                 for index, name in enumerate(("first", "second"), 1)]
        processes = {}
        for node in nodes:
            process = Mock()
            process.poll.side_effect = [None, 0]
            processes[node["name"]] = process
        return nodes, processes

    def test_cleanup_attempts_every_node_and_preserves_original_failure(self):
        nodes, processes = self.captures()
        calls = []
        def command(args):
            calls.append(args)
            if args[2] == "first" and "kill" in args:
                raise subprocess.CalledProcessError(1, args)
        original = ValueError("snapshot failed")
        with patch("measure.run", side_effect=command), self.assertLogs(level="ERROR"):
            with self.assertRaises(ValueError) as raised:
                try:
                    raise original
                finally:
                    cleanup_captures(nodes, processes)
        self.assertIs(raised.exception, original)
        self.assertEqual([args[2] for args in calls if "kill" in args], ["first", "second"])
        for process in processes.values():
            process.communicate.assert_called_once_with(timeout=30)

    def test_cleanup_failure_after_success_is_not_silenced(self):
        nodes, processes = self.captures()
        with patch("measure.run", side_effect=OSError("container unavailable")), self.assertLogs(level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "Capture cleanup failed"):
                cleanup_captures(nodes, processes)
        for process in processes.values():
            process.communicate.assert_called_once_with(timeout=30)

    def test_wait_timeout_retains_open_capture_files_and_cleans_other_nodes(self):
        nodes, processes = self.captures()
        processes["first"].poll.side_effect = [None, None]
        processes["first"].communicate.side_effect = subprocess.TimeoutExpired("capture", 30)
        with patch("measure.run") as command, self.assertLogs(level="ERROR"):
            with self.assertRaises(RuntimeError):
                cleanup_captures(nodes, processes)
        removals = [call.args[0][2] for call in command.call_args_list if "python3" in call.args[0]]
        self.assertEqual(removals, ["second"])


if __name__ == "__main__":
    unittest.main()
