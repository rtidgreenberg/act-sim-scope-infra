"""Pure accounting regressions; no DDS entities or live network required."""

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "debug/scripts"))
sys.path.insert(0, str(ROOT / "gui/mesh_dashboard/server"))

from traffic_accounting import FIELDS, TX_CLASSES, FrameAccounting, classify_packet
from domain_traffic_monitor import TrafficAccumulator, EmaneSampler, build_port_filter
from traffic_metrics import aggregate, counter_fields, validate_sample


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


class AggregateTests(unittest.TestCase):
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
