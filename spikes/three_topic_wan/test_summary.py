import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from summarize import convergence_phase, health_gaps, percentile, summary, validate_layout, write_report
from topics import TRADEOFF_CASES
from repair_trace import analyze, event_submessages, main as repair_main
from wire import packet_category


def inventory(case):
    topics = ["Event", "Status"] if case.startswith("mixed") else ["Event", "Status", "Contacts"]
    readers, writers = {}, {}
    for node in ("Source", "Receiver"):
        for topic in topics:
            name = node + "/" + topic
            history = ("HistoryKind.KEEP_LAST"
                       if topic == "Status" or topic == "Contacts" and case != "split_keep_all"
                       else "HistoryKind.KEEP_ALL")
            zero = case == "mixed_no_suppression" or case == "bands_no_suppression" and topic == "Contacts"
            writers[name] = {
                "topic": topic, "history": history, "matched_readers": 1,
                "channel_count": 0 if topic == "Status" else 2,
                "flow_controller": "default" if topic == "Status" else "shared",
                "reliability": "ReliabilityKind.BEST_EFFORT" if topic == "Status" else "ReliabilityKind.RELIABLE",
            }
            readers[name] = {"topic": topic, "history": "HistoryKind.KEEP_LAST" if topic == "Status" else "HistoryKind.KEEP_ALL",
                             "reliability": writers[name]["reliability"], "matched_writers": 1,
                             "heartbeat_suppression_s": 0 if zero else 0.0625}
    return {"writers": writers, "readers": readers}


def result(outage=False):
    metric = {
        "unacked": 0, "rss_kib": 100, "health_deadline_misses": 0,
        "writer_metrics": {
            "Source/Event": {"cache_samples": 3, "pushed_bytes": 80, "pulled_bytes": 30,
                             "nacks": 2, "inactive_readers": 0},
            "Receiver/Event": {"cache_samples": 1, "pushed_bytes": 10, "pulled_bytes": 0,
                               "nacks": 0, "inactive_readers": 0},
        },
    }
    return {
        "outage": outage, "started": 0, "stopped_producing": 10,
        "outage_started": 2 if outage else None, "outage_finished": 6 if outage else None,
        "effective_qos": {
            "comparison_case": "mixed_all", "flow_controller": "shared",
            "endpoint_inventory": inventory("mixed_all"),
            "rss_before_endpoints_kib": 60, "rss_after_endpoints_kib": 80,
        },
        "metrics": [{"at": 0, **metric}, {"at": 10, **metric}],
        "commands": [{"sent": 1, "received": 1.01, "rtt_s": 0.01},
                     {"sent": 7, "received": 7.03, "rtt_s": 0.03}],
        "health": [{"at": 1.1, "age_s": 0.1}, {"at": 9, "age_s": 0.03}],
        "health_sends": [{"sequence": 1, "at": 1}, {"sequence": 2, "at": 8.97}],
        "event_delivery": {
            "commands_sent": 2, "missing_commands": [], "duplicate_commands": [],
            "duplicate_acks": [], "unexpected_commands": [], "unexpected_acks": [],
        },
        "missing_command_acks": [],
        "final_discrepancies": {"ghosts": [], "missing": [], "stale": [], "unexpected": []},
        "write_history": {"required": True, "expected": 20, "observed": 20},
        "first_recovery_convergence_s": 0.8 if outage else None,
        "contract": "two_topic", "passed": True, "errors": [],
        "scheduler_lag_max_s": 0.001, "cpu_seconds": 0.5,
        "observations": [{"at": 1, "key": "a", "state": "alive"},
                         {"at": 3, "key": "a", "state": "disposed"}],
        "operation_events": [{"at": 2, "key": "a", "operation": "dispose"}],
    }


class SummaryTests(unittest.TestCase):
    def test_report_describes_executed_native_policies_and_negative_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            comparison = Path(directory)
            rows = []
            for case in ("baseline", "none_all", "mixed_last"):
                data = result(True)
                data["effective_qos"]["comparison_case"] = case
                data["effective_qos"]["endpoint_inventory"] = inventory(case)
                for writer in data["effective_qos"]["endpoint_inventory"]["writers"].values():
                    writer["channel_count"] = 0 if case != "mixed_last" else writer["channel_count"]
                data["passed"] = False
                data["errors"] = ["negative control rejected"]
                folder = comparison / case
                folder.mkdir()
                (folder / "results.json").write_text(json.dumps([data]))
                rows.append({**summary(data), "repeat": 1, "report": str(folder)})
            (comparison / "comparison.json").write_text(json.dumps(rows))
            (comparison / "manifest.json").write_text(json.dumps({
                "source_commit": "test", "image": "test", "started_at_utc": "test",
                "completed_at_utc": "test", "host_cpu_count": 1, "host_scope": "test",
                "execution": "sequential", "arguments": {
                    "repeats": 1, "outage_seconds": 120, "baseline": 20, "recovery": 40},
            }))
            output, _ = write_report(comparison)
            text = output.read_text()
            self.assertNotIn("all comparison writers retain the same two priority bands", text)
            self.assertNotIn("`mixed_all` versus `split_keep_all`", text)
            self.assertIn("| `baseline` | Source/Event | HistoryKind.KEEP_ALL | 0 |", text)
            self.assertIn("lossy shared Event", text)
            self.assertIn("negative control rejected", text)

    def test_explicit_healthy_repair_report_has_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "manifest.json").write_text("{}")
            (folder / "results.json").write_text(json.dumps([{"outage": False}]))
            with self.assertRaisesRegex(ValueError, "no outage trial"):
                analyze(folder)

    def test_repair_comparison_skips_healthy_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comparison = root / "comparison"
            comparison.mkdir()
            (comparison / "comparison.json").write_text(json.dumps([
                {"report": "/old/path/healthy", "scenario": "healthy"},
                {"report": "/old/path/outage", "scenario": "outage"},
            ]))
            output = root / "repair.json"
            value = {"report": "outage", "recovery_s": 1, "writers": {}}
            with patch("repair_trace.ROOT", root), patch("repair_trace.analyze", return_value=value) as native, \
                    patch("sys.argv", ["repair_trace", "--comparison", str(comparison),
                                       "--output", str(output)]), patch("builtins.print"):
                repair_main()
            native.assert_called_once_with(root / "debug/test_reports/outage")
            self.assertEqual(json.loads(output.read_text()), [value])

    def test_healthy_only_repair_selection_is_rejected_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "comparison.json").write_text(json.dumps([
                {"report": "healthy", "scenario": "healthy"},
            ]))
            output = root / "repair.json"
            with patch("sys.argv", ["repair_trace", "--comparison", str(root),
                                   "--output", str(output)]), patch("sys.stderr"), \
                    patch("repair_trace.analyze") as native:
                with self.assertRaises(SystemExit) as error:
                    repair_main()
            self.assertEqual(error.exception.code, 2)
            native.assert_not_called()
            self.assertFalse(output.exists())

    def test_quantiles_and_health_censoring_do_not_drop_absence(self):
        self.assertEqual(percentile([1, 2, 3], 0.95), 2.9)
        self.assertIsNone(percentile([], 0.95))
        self.assertEqual(health_gaps([], 0, 4), [4])
        self.assertEqual(health_gaps([{"at": 1}, {"at": 3}], 0, 4), [1, 2, 1])

    def test_all_lossless_layouts_require_symmetric_remote_only_endpoints(self):
        for case in TRADEOFF_CASES:
            data = {"effective_qos": {"comparison_case": case, "flow_controller": "shared",
                                     "endpoint_inventory": inventory(case)}}
            validate_layout(data)
            contaminated = copy.deepcopy(data)
            contaminated["effective_qos"]["endpoint_inventory"]["writers"]["Source/Event"]["matched_readers"] = 2
            with self.assertRaisesRegex(AssertionError, "Local or missing"):
                validate_layout(contaminated)
        data["effective_qos"]["endpoint_inventory"]["writers"]["Source/Event"]["history"] = "HistoryKind.KEEP_LAST"
        with self.assertRaisesRegex(AssertionError, "retention or scheduling"):
            validate_layout(data)

    def test_healthy_and_outage_summary_keeps_correct_units_and_gates(self):
        healthy = summary(result())
        outage = summary(result(True))
        self.assertEqual(healthy["scenario"], "healthy")
        self.assertIsNone(healthy["recovery_s"])
        self.assertAlmostEqual(healthy["fresh_rtt_p95_ms"], 29)
        self.assertAlmostEqual(outage["fresh_rtt_p95_ms"], 30)
        self.assertAlmostEqual(outage["health_age_max_ms"], 30)
        self.assertEqual(outage["health_first_after_restore_s"], 3)
        self.assertEqual(outage["convergence_phase"], "producing")
        data = result(True)
        data["first_recovery_convergence_s"] = 5
        self.assertEqual(convergence_phase(data), "drain")
        data["first_recovery_convergence_s"] = None
        self.assertEqual(convergence_phase(data), "not_observed")
        self.assertEqual(outage["health_gaps_over_2s_outside_blackout"], 1)
        self.assertTrue(healthy["correctness_passed"])
        self.assertEqual(healthy["endpoint_rss_delta_kib"], 20)
        self.assertEqual(healthy["source_repair_bytes"], 30)
        self.assertEqual(healthy["visible_deletion_delay_max_s"], 1)
        self.assertIsNone(healthy["startup_udp_bytes"])
        data = result()
        data["event_delivery"]["duplicate_acks"] = [2]
        self.assertFalse(summary(data)["correctness_passed"])

    def test_packet_classes_are_exclusive_and_mixed_builtin_frames_explicit(self):
        def data(writer):
            body = b"\0" * 8 + bytes.fromhex(writer) + b"\0" * 8
            return b"\x15\x01" + len(body).to_bytes(2, "little") + body
        header = b"RTPS" + b"\0" * 16
        self.assertEqual(packet_category(header + data("000003c2")), "discovery")
        self.assertEqual(packet_category(header + data("00010082")), "discovery")
        self.assertEqual(packet_category(b"RTPS" + b"\0" * 4), "prefixless_rtps")
        self.assertEqual(packet_category(header + data("80000102")), "user_data")
        self.assertEqual(packet_category(header + data("000003c2") + data("80000102")),
                         "mixed_builtin_user")
        self.assertEqual(len(event_submessages(header + data("80000102"), "80000202")), 1)
        body = b"\0" * 4 + bytes.fromhex("80000102") + b"\0" * 20
        heartbeat = b"\x07\x01" + len(body).to_bytes(2, "little") + body
        self.assertEqual(packet_category(header + heartbeat), "user_control")
        vendor_heartbeat = b"\x1b\x01" + len(body).to_bytes(2, "little") + body
        self.assertEqual(packet_category(header + vendor_heartbeat), "user_control")
        with self.assertRaisesRegex(ValueError, "Truncated"):
            packet_category(header + b"\x15\x01\xff\xff")


if __name__ == "__main__":
    unittest.main()
