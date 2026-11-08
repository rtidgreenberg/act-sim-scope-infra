"""DDS-free checks for argument and packet-window accounting."""

import unittest
import copy
import json
import signal
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spike import (capture_integrity, capture_summary, execution_provenance, main,
                   snapshot_sources, source_hashes, stop_capture,
                   validate_capture_integrity, validate_provenance)
from report import aggregate, report
from run_scale import case


class PacketAccountingTests(unittest.TestCase):
    def test_whole_frames_and_complete_peak_windows(self):
        prefix = "010100000000000000000001"
        rows = "\n".join([
            f"100.2|120|{prefix}|0x00010082|0x15|RouterHealth|0|0",
            f"100.8|100|{prefix}|0x00010082|0x15||0|0",
            f"101.2|90|{prefix}|0x00010082,0x00010182|0x15,0x15||0|0",
            f"101.4|80|{prefix}|0x00000102|0x15||0|0",
            "101.5|999|unowned|0x00010082|0x15||0|0",
            f"102.0|999|{prefix}|0x00010082|0x15||0|0",
        ])
        with patch("spike.subprocess.run") as command:
            command.return_value.stdout = rows
            result = capture_summary("unused", {prefix: "P0"},
                                     {"idle": (100.0, 102.0)})
        idle = result["windows"]["idle"]
        self.assertEqual(idle["total_bytes"], 390)
        self.assertEqual(idle["discovery_tx_Bps"], 155)
        self.assertEqual(idle["discovery_peak_1s_Bps"], 220)
        self.assertEqual(sum(idle["per_dp_bytes"].values()), 390)
        self.assertEqual(idle["class_bytes"]["data"], 80)
        self.assertEqual(result["observed_topics"], ["RouterHealth"])

    def test_partial_seconds_are_not_peaks(self):
        prefix = "010100000000000000000001"
        rows = f"100.2|999|{prefix}|0x00010082|0x15||0|0"
        with patch("spike.subprocess.run") as command:
            command.return_value.stdout = rows
            result = capture_summary("unused", {prefix: "P0"},
                                     {"idle": (100.1, 100.9)})
        self.assertEqual(result["windows"]["idle"]["discovery_peak_1s_Bps"], 0)

    def test_invalid_domain_is_rejected(self):
        with patch("sys.argv", ["spike.py", "--output", "/tmp/unused", "--domain", "233"]):
            with self.assertRaises(SystemExit) as failure:
                main()
        self.assertEqual(failure.exception.code, 2)

    def test_builtin_control_is_discovery_tx(self):
        prefix = "010100000000000000000001"
        rows = f"100.2|120|{prefix}|0x000003c2|0x06||0|0"
        with patch("spike.subprocess.run") as command:
            command.return_value.stdout = rows
            result = capture_summary("unused", {prefix: "P0"}, {"idle": (100.0, 101.0)})
        self.assertEqual(result["windows"]["idle"]["class_bytes"], {"discovery": 120})

    def test_builtin_data_and_heartbeat_bundle_is_discovery(self):
        prefix = "010100000000000000000001"
        rows = f"100.2|240|{prefix}|0x000003c2|0x15,0x07||0|0"
        with patch("spike.subprocess.run") as command:
            command.return_value.stdout = rows
            result = capture_summary("unused", {prefix: "P0"}, {"idle": (100.0, 101.0)})
        self.assertEqual(result["windows"]["idle"]["class_bytes"], {"discovery": 240})
        self.assertEqual(result["windows"]["idle"]["start_epoch"], 100.0)
        self.assertEqual(result["windows"]["idle"]["end_epoch"], 101.0)
        self.assertEqual(result["accounting_version"], 2)

    def test_discovery_and_application_bundle_remains_mixed(self):
        prefix = "010100000000000000000001"
        for submessages in ("0x15,0x07", "0x06,0x06"):
            with self.subTest(submessages=submessages):
                rows = f"100.2|240|{prefix}|0x000003c2,0x80000002|{submessages}||0|0"
                with patch("spike.subprocess.run") as command:
                    command.return_value.stdout = rows
                    result = capture_summary("unused", {prefix: "P0"}, {"idle": (100.0, 101.0)})
                self.assertEqual(result["windows"]["idle"]["class_bytes"], {"mixed": 240})


class ReportTests(unittest.TestCase):
    def test_median_ranges_keep_metadata_boolean_separate(self):
        result = aggregate([
            {"variant": "A", "repetition": 1, "idle_discovery_kbps": 20,
             "out_of_team_metadata_visible": False},
            {"variant": "A", "repetition": 2, "idle_discovery_kbps": 10,
             "out_of_team_metadata_visible": False},
            {"variant": "A", "repetition": 3, "idle_discovery_kbps": 30,
             "out_of_team_metadata_visible": False},
        ])
        self.assertEqual(result["A"]["metrics"]["idle_discovery_kbps"],
                         {"median": 20, "min": 10, "max": 30})
        self.assertFalse(result["A"]["out_of_team_metadata_visible"])
        self.assertNotIn("repetition", result["A"]["metrics"])

    def test_reports_do_not_attach_current_sources_to_old_measurements(self):
        names = (
            "wan_dp_count", "local_endpoint_count", "observed_remote_metadata_entries",
            "idle_discovery_kbps", "idle_discovery_peak_kbps",
            "idle_discovery_bytes_per_nominal_30s_cycle", "idle_total_kbps", "loaded_total_kbps",
            "startup_bytes", "pair_open_bytes", "pair_close_bytes", "team_change_bytes",
            "restart_bytes", "post_pair_commands_match_poll_ms", "post_team_commands_match_poll_ms",
            "initial_match_poll_ms", "restart_match_poll_ms", "idle_worker_cpu_seconds",
            "worker_peak_rss_kib_sum", "audited_test_sends", "audited_test_destination_receives")
        rows = [{**dict.fromkeys(names, 1), "variant": v, "repetition": 1,
                 "unintended_test_deliveries": 0, "out_of_team_metadata_visible": v in "BC"}
                for v in "ABCD"]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            manifest = dict(settings=dict(repetitions=1, variants="ABCD", idle_seconds=150,
                                          concurrent_repetitions=1))
            results = [dict(variant=v, idle_seconds=150) for v in "ABCD"]
            (source / "results.json").write_text(json.dumps(results))
            (source / "manifest.json").write_text(json.dumps(manifest))
            with patch("report.metrics", side_effect=rows), patch("report.hashlib.sha256") as digest:
                digest.return_value.hexdigest.return_value = "report-tool-hash"
                retained = report(source, source / "historical")
            self.assertEqual(digest.call_count, 1)
            self.assertIsNone(retained["source_sha256"])
            self.assertEqual(retained["report_generator_sha256"], "report-tool-hash")
            self.assertFalse(retained["execution_provenance_verified"])

            proof = dict(source_hashes={"qos.xml": "execution-hash"}, input_hashes={},
                         image_id="sha256:" + "a" * 64)
            manifest.update(execution_provenance_version=1, **proof)
            for result in results:
                result["execution_provenance"] = proof
            (source / "manifest.json").write_text(json.dumps(manifest))
            (source / "results.json").write_text(json.dumps(results))
            with patch("report.metrics", side_effect=rows):
                retained = report(source, source / "verified")
            self.assertEqual(retained["source_sha256"], proof["source_hashes"])
            self.assertTrue(retained["execution_provenance_verified"])
            results[0]["execution_provenance"] = None
            (source / "results.json").write_text(json.dumps(results))
            with self.assertRaisesRegex(ValueError, "provenance"):
                report(source, source / "invalid")


class CaptureIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.summary = dict(owned_rtps_packets=2, first_owned_rtps_epoch=100.1,
                            last_owned_rtps_epoch=103.1,
                            windows=dict(startup=dict(end_epoch=101), operational=dict(end_epoch=103)))
        self.proof = dict(
            version=1, stop_requested=True, forced_kill=False, returncode=0,
            captured_packets=3, received_packets=3, dropped_packets=0,
            drop_counters=dict(pcap=0, dumpcap=0, flushed=0, ps_ifdrop=0),
            owned_rtps_packets=2, first_owned_rtps_epoch=100.1,
            last_owned_rtps_epoch=103.1, required_end_epoch=103)

    def test_actual_dumpcap_format_and_final_boundary(self):
        log = ("Packets captured: 3\n"
               "Packets received/dropped on interface 'Loopback: lo': "
               "3/0 (pcap:0/dumpcap:0/flushed:0/ps_ifdrop:0) (100.0%)\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.log"
            path.write_text(log)
            proof = capture_integrity(
                path, dict(stop_requested=True, forced_kill=False, returncode=0),
                self.summary, {"startup": (100, 101), "operational": (101, 103)})
            self.assertEqual(proof, self.proof)
            for bad in ("", log.replace("pcap:0", "pcap:1"), log + log):
                path.write_text(bad)
                with self.assertRaises(ValueError):
                    capture_integrity(path, dict(stop_requested=True, forced_kill=False, returncode=0),
                                      self.summary, {"operational": (101, 103)})

    def test_truncation_early_exit_and_drops_are_rejected(self):
        for field, value in (
                ("stop_requested", False), ("forced_kill", True), ("returncode", -signal.SIGKILL),
                ("dropped_packets", 1), ("drop_counters", {"pcap": 0}),
                ("captured_packets", 1), ("received_packets", 4), ("last_owned_rtps_epoch", 102.99),
                ("last_owned_rtps_epoch", float("nan")), ("required_end_epoch", 102),
                ("first_owned_rtps_epoch", 102)):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.proof)
                changed[field] = value
                with self.assertRaises(ValueError):
                    validate_capture_integrity(changed, self.summary)
        exact = copy.deepcopy(self.proof)
        exact["last_owned_rtps_epoch"] = 103
        validate_capture_integrity(exact)
        late = {**self.proof, "first_owned_rtps_epoch": 102}
        with self.assertRaisesRegex(ValueError, "initial"):
            validate_capture_integrity(late, {**self.summary, "first_owned_rtps_epoch": 102})

    def test_capture_stop_reaps_forced_exit_without_accepting_it(self):
        with patch("spike.subprocess.Popen") as factory:
            capture = factory.return_value
            capture.poll.return_value = None
            capture.returncode = 0
            stopped = stop_capture(capture)
            self.assertEqual(stopped, dict(stop_requested=True, forced_kill=False, returncode=0))
            capture.send_signal.assert_called_once_with(signal.SIGINT)
            capture.wait.side_effect = [subprocess.TimeoutExpired("dumpcap", 10), -signal.SIGKILL]
            capture.returncode = -signal.SIGKILL
            stopped = stop_capture(capture)
            self.assertTrue(stopped["forced_kill"])
            capture.kill.assert_called_once()
            with self.assertRaises(ValueError):
                validate_capture_integrity({**self.proof, **stopped})
            capture.wait.side_effect = None
            capture.poll.return_value = 0
            capture.returncode = 0
            self.assertFalse(stop_capture(capture)["stop_requested"])


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        CaptureIntegrityTests.setUp(self)

    def test_snapshot_is_independent_of_checkout_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            original = root / "spikes/wan_dp_route_tradeoffs/qos.xml"
            original.parent.mkdir(parents=True)
            original.write_text("original")
            output = Path(directory) / "run"
            output.mkdir()
            with patch("spike.ROOT", root), patch("spike.QOS", original):
                snapshot = snapshot_sources(output, ("qos.xml",))
                archived = snapshot / "spikes/wan_dp_route_tradeoffs"
                before = source_hashes(("qos.xml",), archived)
                original.write_text("edited")
                self.assertEqual(source_hashes(("qos.xml",), archived), before)
                self.assertNotEqual(execution_provenance(("qos.xml",))["source_hashes"], before)
                self.assertEqual((archived / "qos.xml").stat().st_mode & 0o222, 0)

    def test_scale_pins_image_and_rejects_mismatched_case_provenance(self):
        proof = dict(source_hashes={"qos.xml": "a" * 64},
                     input_hashes={"schemas.xml": "b" * 64}, image_id="sha256:" + "c" * 64)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "manifest.json").write_text(json.dumps(proof))
            target = output / "n2-t2-k1-SPDP2-A-r1"
            target.mkdir()
            result = dict(execution_provenance=proof, capture={**self.summary, "integrity": self.proof})
            path = target / "result.json"
            path.write_text(json.dumps(result))
            args = SimpleNamespace(license_dir=output, seconds=3, image="mutable:tag",
                                   health_profile="scale_periodic", change_hold_seconds=10)
            with patch("run_scale.subprocess.run") as run:
                run.side_effect = lambda command, **kwargs: subprocess.CompletedProcess(
                    command, 0 if command[1] == "run" else 1, stdout="", stderr="")
                with patch("builtins.print"):
                    self.assertEqual(case(args, output, 2, 2, 1, "SPDP2", "A", 1), result)
                command = run.call_args_list[0].args[0]
                self.assertIn(proof["image_id"], command)
                self.assertNotIn("mutable:tag", command)
                self.assertIn(f"WAN_DP_IMAGE_ID={proof['image_id']}", command)
                self.assertIn(f"{output / 'source'}:/workspace:ro", command)
                for field in ("source_hashes", "input_hashes", "image_id"):
                    changed = copy.deepcopy(proof)
                    changed[field] = None
                    with self.assertRaises(ValueError):
                        validate_provenance(changed, proof)
                result["execution_provenance"] = {**proof, "source_hashes": {"qos.xml": "changed"}}
                path.write_text(json.dumps(result))
                with patch("builtins.print"), self.assertRaisesRegex(ValueError, "source_hashes"):
                    case(args, output, 2, 2, 1, "SPDP2", "A", 1)


if __name__ == "__main__":
    unittest.main()
